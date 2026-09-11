#!/usr/bin/env python3
"""
LLM-as-judge verification of regex language detections.

Reads a week's <stem>_detected.jsonl, assembles per-paper context from the
cached HTML/PDF text (re-fetched on demand if missing — e.g. a fresh CI
checkout on a different runner/day than the one that ran process_papers.py),
and asks an OpenAI-compatible model whether each detected language is
studied, only mentioned, or a false positive. Verdicts are written one file
per paper to <week_dir>/judge_cache/<safe_id>.json and folded into the
manifest on the next build_manifest run.

Configuration comes from LLM_JUDGE_* environment variables (see
langtrend/llm_client.py or .env.example) — provider and model are whatever
LLM_JUDGE_BASE_URL/LLM_JUDGE_MODEL point at (Cerebras, Groq, and OpenRouter
free tiers, or a local Ollama server, all work as drop-in options; see
.env.example for current tradeoffs).

Exit codes: 0 = nothing left pending (either there was nothing to do, or
every targeted paper got judged this run); 1 = hard error (bad config,
missing detected.jsonl); 3 = stopped early with papers still pending for a
reason worth retrying soon (per-paper timeout or --limit reached); 4 =
stopped early because the provider's daily quota is spent, which retrying
before it resets at midnight UTC cannot fix. 3 and 4 are both safe to re-run
later — already-judged papers are skipped — but CI tells them apart to decide
whether to retry within the same run (3) or defer to the next one (4).

Usage:
    python scripts/judge_languages.py --end-date 2026-05-25
    python scripts/judge_languages.py --input data/raw/extracted_papers_metadata/arxiv_papers_20260518_to_20260525.jsonl
    python scripts/judge_languages.py --end-date 2026-05-25 --limit 5
    python scripts/judge_languages.py --end-date 2026-05-25 --paper-id 1111.11111v1 --dry-run
    python scripts/judge_languages.py --sweep-all-weeks   # daily catch-up: any week with gaps, newest first
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.judge import (
    build_messages,
    assemble_context,
    batches_needed,
    collect_target_languages,
    ensure_context_cache,
    judge_cache_path,
    judge_paper,
    malformed_detections,
    needs_judging,
    safe_paper_id,
    save_judge_record,
)
from langtrend.llm_client import (
    ChatCancelled,
    LLMClientConfig,
    LLMUnavailableError,
    OpenAICompatClient,
    QuotaExhaustedError,
)

_PROJECT_ROOT = Path(__file__).parent.parent
_METADATA_DIR = _PROJECT_ROOT / "data/raw/extracted_papers_metadata"
_PROCESSED_DIR = _PROJECT_ROOT / "data/processed"
_DEFAULT_LANG_DATA = _PROCESSED_DIR / "language_data.json"
_DEFAULT_PDF_DIR = _PROJECT_ROOT / "data/raw/pdfs"
_WEEK_RE = re.compile(r"^\d{8}_to_\d{8}$")

# Seconds to wait for *any* in-flight paper before giving up on the whole
# remaining batch (see the `wait(..., timeout=...)` watchdog below). Worst
# case for one paper is several full chat() attempts across a multi-model
# fallback chain (default LLM_JUDGE_TIMEOUT=180s per attempt, _CHAT_RETRIES=3
# per model) — a degraded-but-not-dead free-tier endpoint can genuinely take
# a few minutes per paper without ever erroring out, and 300s left little
# room for that (observed mean latency on a slow endpoint: 438s). Set
# generously since a timeout here is now cheap: the abandoned worker receives
# a cancel_event (see ChatCancelled) and bails at its next attempt/model
# boundary instead of continuing to retry for however long the process
# happens to stay alive. This is a floor, not the whole story — see
# _PER_BATCH_BUDGET below for papers that need much longer than this alone
# would allow.
_PER_PAPER_TIMEOUT = 600

# judge_paper() sends its chat() calls one batch at a time, sequentially (see
# judge.batches_needed). Most papers need one batch, but some — a survey
# mentioning a hundred-plus languages — need a dozen or more, each its own
# throttled round-trip, so each is another chance to snag on a slow or
# retried call. _PER_PAPER_TIMEOUT alone is the budget for a single-batch
# paper; every *additional* batch buys _PER_BATCH_BUDGET more on top, so the
# allowance grows with the work the paper actually represents rather than
# being a flat number that fits the median and starves the tail.
#
# Deliberately additive rather than `max(floor, batches * budget)`: with a
# 600s floor and a 60s budget, that form only exceeds the floor past 10
# batches, which measured over ~6.3k real papers is 3 of them (0.05%) — it
# would leave the entire p99 (3 batches) on exactly the flat timeout this is
# meant to replace. Additive, the same constants give 720s at 3 batches,
# 1080s at 9, and 1620s at the largest paper on record (207 languages, 18
# batches). The cap then bites past ~21 batches, so one outlier still can't
# stall a whole week indefinitely.
_PER_BATCH_BUDGET = 60
_PER_PAPER_TIMEOUT_CAP = 1800

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 3  # stopped early, retryable now — papers still pending, safe to re-run
EXIT_QUOTA = 4  # stopped early on daily quota — retrying before midnight UTC won't help


def _last_monday_midnight() -> datetime:
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _resolve_input(args: argparse.Namespace) -> Path:
    if args.input:
        return args.input
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d") if args.end_date else _last_monday_midnight()
    start_date = end_date - timedelta(days=args.window_days)
    return _METADATA_DIR / (
        f"arxiv_papers_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.jsonl"
    )


def _week_dir_for_slug(slug: str) -> Path:
    return _PROCESSED_DIR / "weeks" / slug


def _week_dir(input_path: Path) -> Path:
    m = re.search(r"(\d{8}_to_\d{8})", input_path.stem)
    return _week_dir_for_slug(m.group(1)) if m else _PROCESSED_DIR


def _load_detected(detected_path: Path) -> list[dict]:
    records = []
    with detected_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _load_language_data(path: Path) -> tuple[dict[int, set[str]], set[str]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    lang_classes = {int(k): set(v) for k, v in data["lang_classes"].items()}
    languages_to_ignore = set(data.get("languages_to_ignore", []))
    return lang_classes, languages_to_ignore


def _parse_classes(raw: str | None) -> set[int] | None:
    """'0-4' or '0,1,2' -> {0,1,2,3,4} / {0,1,2}; None means all classes."""
    if not raw:
        return None
    classes: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            classes.update(range(int(lo), int(hi) + 1))
        elif part:
            classes.add(int(part))
    return classes or None


def _find_all_week_slugs() -> list[str]:
    """Every week subdirectory with a detected.jsonl, sorted newest-first.

    Newest-first so a quota-limited sweep (--sweep-all-weeks) prioritizes the
    current/most-recent week — the one the site's "latest" pointer and the
    deploy gate care about — before spending budget backfilling older weeks.
    """
    weeks_dir = _PROCESSED_DIR / "weeks"
    if not weeks_dir.is_dir():
        return []
    return sorted(
        (p.name for p in weeks_dir.iterdir()
         if p.is_dir() and _WEEK_RE.match(p.name) and any(p.glob("arxiv_papers_*_detected.jsonl"))),
        reverse=True,
    )


def _detected_path_for_week(slug: str) -> Path:
    week_dir = _week_dir_for_slug(slug)
    matches = sorted(week_dir.glob("arxiv_papers_*_detected.jsonl"))
    return matches[0] if matches else week_dir / f"arxiv_papers_{slug}_detected.jsonl"


def _print_verdicts(judge_record: dict) -> None:
    verdicts = judge_record.get("verdicts", {})
    if not verdicts:
        print("  (no verdicts returned)")
        return
    width = max(len(name) for name in verdicts)
    for name, v in sorted(verdicts.items(), key=lambda kv: kv[1]["verdict"]):
        print(f"  {name:<{width}}  {v['verdict']:<15} {v['reason']}")


def _single_paper_mode(args, records, week_dir, config, classes) -> int:
    wanted = args.paper_id
    record = next((r for r in records if safe_paper_id(r.get("paper_id", "")) == wanted), None)
    if record is None:
        print(f"Error: paper '{wanted}' not found in detected records.", file=sys.stderr)
        return EXIT_ERROR

    if not args.dry_run:
        lang_classes, languages_to_ignore = _load_language_data(args.language_data)
        ensure_context_cache(record, week_dir, args.pdf_dir, lang_classes, languages_to_ignore)

    targets = collect_target_languages(record, classes=classes)
    context = assemble_context(record, week_dir, targets, max_chars=config.max_context_chars)
    messages = build_messages(context, targets)

    print(f"Paper: {record['paper'].get('title', '')}")
    print(f"Targets ({len(targets)}): {', '.join(t['language'] for t in targets)}")
    print(f"Context: {context.total_chars} chars, coverage={context.coverage}, "
          f"{len(context.snippets)} snippet(s)\n")
    print("--- system message " + "-" * 50)
    print(messages[0]["content"])
    print("--- user message " + "-" * 52)
    print(messages[1]["content"])
    print("-" * 70)

    if args.dry_run:
        print("\n--dry-run: no model call made.")
        return EXIT_OK

    client = OpenAICompatClient(config)
    client.ping()
    judge_record = judge_paper(record, week_dir, client, config, classes=classes)
    print(f"\nVerdicts from {judge_record['judge_model'] if judge_record else config.model}:")
    _print_verdicts(judge_record)
    if args.save:
        path = save_judge_record(week_dir, judge_record)
        print(f"\nSaved: {path}")
    else:
        print("\n(not saved — pass --save to write the judge cache)")
    return EXIT_OK


def _partition_pending(
    records: list[dict], week_dir: Path, classes: set[int] | None, force: bool
) -> tuple[list[dict], int, int]:
    """Split records into (pending, cached_count, no_target_count)."""
    pending: list[dict] = []
    cached_count = no_target_count = 0
    for record in records:
        cache_file = judge_cache_path(week_dir, record.get("paper_id", ""))
        cached = None
        if cache_file.exists() and not force:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cached = None
        if not needs_judging(record, None if force else cached, classes=classes):
            if cached is not None:
                cached_count += 1
            else:
                no_target_count += 1
            continue
        pending.append(record)
    return pending, cached_count, no_target_count


_MALFORMED_REPORT_LIMIT = 10


def _abort_on_malformed(stem: str, detected_path: Path, records: list[dict]) -> None:
    """Stop the run if any record's detections can't be read, saying exactly where.

    Deliberately fatal rather than skip-and-continue. The realistic way a bad
    shape reaches detected.jsonl is a schema change in process_papers.py, which
    affects every record rather than one — and silently dropping every detection
    would rebuild the manifest with collapsed counts, pass the deploy gate, and
    publish wrong numbers. A stopped run is the cheaper failure.

    What this adds over just letting collect_target_languages raise is the
    message: the bare "ValueError: invalid literal for int() with base 10" it
    throws from inside _partition_pending names no file, no line, and no paper,
    leaving thousands of records to bisect by hand.

    Exits EXIT_ERROR, which judge-catchup.yml's retry loop already classifies as
    non-retryable — correct, since re-running cannot change what is on disk.
    """
    offenders = [(record, malformed_detections(record)) for record in records]
    offenders = [(record, problems) for record, problems in offenders if problems]
    if not offenders:
        return

    # Only now pay for a re-read, to point at exact line numbers. Nothing on the
    # happy path touches the file twice.
    line_of: dict[str, int] = {}
    try:
        for number, line in enumerate(detected_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                line_of.setdefault(json.loads(line).get("paper_id"), number)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass

    print(f"Error: {detected_path} has {len(offenders)} record(s) whose detections "
          f"can't be read:", file=sys.stderr)
    for record, problems in offenders[:_MALFORMED_REPORT_LIMIT]:
        paper_id = record.get("paper_id", "unknown")
        where = f"line {line_of[paper_id]}" if paper_id in line_of else "line ?"
        for problem in problems:
            print(f"  {where} [{paper_id}] {problem}", file=sys.stderr)
    if len(offenders) > _MALFORMED_REPORT_LIMIT:
        print(f"  ...and {len(offenders) - _MALFORMED_REPORT_LIMIT} more record(s) "
              f"in {stem}", file=sys.stderr)
    print("\nThis is a data problem, not a transient one — re-running won't fix it.\n"
          "Regenerate the affected paper's detections (make retry-missing, or make\n"
          "reprocess) and commit the result.", file=sys.stderr)
    sys.exit(EXIT_ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Judge regex language detections with an LLM (studied / mentioned_only / false_positive)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", type=Path, default=None,
                        help="Raw metadata JSONL (default: derived from --end-date/--window-days)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Window end date YYYY-MM-DD (default: last Monday midnight)")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--sweep-all-weeks", action="store_true",
                        help="Daily catch-up mode: judge pending papers across every week with a "
                             "detected.jsonl (newest first, so the current week finishes before older "
                             "backlog), not just the week derived from --end-date/--input. Stops "
                             "cleanly on quota exhaustion; re-run to continue.")
    parser.add_argument("--check-only", action="store_true",
                        help="Don't judge anything — just report whether any targeted week still has "
                             "pending papers (exit 0 if fully judged, exit 3 if gaps remain). Without "
                             "--sweep-all-weeks, checks only the single week derived from "
                             "--end-date/--input (the current week by default) — this is what gates the "
                             "CI deploy. Combine with --sweep-all-weeks for a repo-wide report.")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel worker threads (default: LLM_JUDGE_WORKERS env)")
    parser.add_argument("--force", action="store_true", help="Re-judge papers with existing cache files")
    parser.add_argument("--limit", type=int, default=None, help="Judge at most N pending papers total")
    parser.add_argument("--classes", type=str, default=None,
                        help="Only judge these resource classes, e.g. '0-4' (default: all; quota lever)")
    parser.add_argument("--model", type=str, default=None, help="Override LLM_JUDGE_MODEL")
    parser.add_argument("--base-url", type=str, default=None, help="Override LLM_JUDGE_BASE_URL")
    parser.add_argument("--max-context-chars", type=int, default=None,
                        help="Override LLM_JUDGE_MAX_CONTEXT_CHARS")
    parser.add_argument("--language-data", type=Path, default=_DEFAULT_LANG_DATA,
                        help=f"language_data.json, needed for on-demand HTML/PDF re-fetch (default: {_DEFAULT_LANG_DATA})")
    parser.add_argument("--pdf-dir", type=Path, default=_DEFAULT_PDF_DIR,
                        help=f"Directory for on-demand PDF downloads (default: {_DEFAULT_PDF_DIR})")
    parser.add_argument("--paper-id", type=str, default=None,
                        help="Single-paper mode: judge one paper (bare id like 1111.11111v1) and print the prompt")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --paper-id: print the assembled prompt without calling the model")
    parser.add_argument("--save", action="store_true",
                        help="With --paper-id: also write the judge cache file")
    parser.add_argument("--skip-if-unconfigured", action="store_true",
                        help="Exit 0 with a notice when no API key is configured (for CI/forks)")
    args = parser.parse_args()

    config = LLMClientConfig.from_env()
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url.rstrip("/")
    if args.max_context_chars:
        config.max_context_chars = args.max_context_chars
    if args.workers:
        config.workers = args.workers

    dry_run = bool(args.paper_id and args.dry_run)
    if not config.api_key and not config.is_local() and not dry_run and not args.check_only:
        if args.skip_if_unconfigured:
            print("LLM_JUDGE_API_KEY not set — skipping judge stage.")
            sys.exit(EXIT_OK)
        print("Error: LLM_JUDGE_API_KEY is not set (required for hosted endpoints).\n"
              "Set it in .env or the environment, or point LLM_JUDGE_BASE_URL at a local server.",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)

    classes = _parse_classes(args.classes)

    if args.sweep_all_weeks:
        week_slugs = _find_all_week_slugs()
        if not week_slugs:
            print("No weeks with detected.jsonl found — nothing to sweep.")
            sys.exit(EXIT_OK)
        print(f"Sweeping {len(week_slugs)} week(s), newest first: {', '.join(week_slugs)}")
        weeks = [(slug, _detected_path_for_week(slug), _week_dir_for_slug(slug)) for slug in week_slugs]
    else:
        input_path = _resolve_input(args)
        week_dir = _week_dir(input_path)
        detected_path = week_dir / f"{input_path.stem}_detected.jsonl"
        if not detected_path.exists():
            print(f"Error: detections not found: {detected_path}\n"
                  "Run the process step first (make process).", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        weeks = [(input_path.stem, detected_path, week_dir)]

    if args.paper_id:
        if len(weeks) != 1:
            print("Error: --paper-id requires a single week (don't combine with --sweep-all-weeks).", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        _, detected_path, week_dir = weeks[0]
        records = _load_detected(detected_path)
        print(f"Loaded {len(records)} detected paper(s) from {detected_path.name}")
        sys.exit(_single_paper_mode(args, records, week_dir, config, classes))

    if args.check_only:
        total_pending = 0
        gapped_weeks: list[str] = []
        for stem, detected_path, week_dir in weeks:
            if not detected_path.exists():
                continue
            records = _load_detected(detected_path)
            pending, _, _ = _partition_pending(records, week_dir, classes, force=False)
            if pending:
                total_pending += len(pending)
                gapped_weeks.append(stem)
        if total_pending == 0:
            print("Fully judged — no pending papers in any targeted week.")
            sys.exit(EXIT_OK)
        print(f"{total_pending} paper(s) still pending across {len(gapped_weeks)} week(s): "
              f"{', '.join(gapped_weeks)}")
        sys.exit(EXIT_INCOMPLETE)

    lang_classes, languages_to_ignore = _load_language_data(args.language_data)

    client = OpenAICompatClient(config)
    try:
        client.ping()
    except LLMUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    fallback_note = ""
    if config.fallback_models:
        chain = " -> ".join(config.fallback_models)
        fallback_note = f", fallback={chain}"
    print(f"Judge: {config.model} @ {config.base_url}{fallback_note} "
          f"(workers={config.workers}, rpm={config.rpm}, rph={config.rph}, "
          f"context≤{config.max_context_chars} chars)")

    total_judged = total_failed = total_cached = total_no_target = 0
    stopped_early = False
    quota_exhausted = False
    latencies: list[float] = []
    remaining_limit = args.limit

    for stem, detected_path, week_dir in weeks:
        if not detected_path.exists():
            continue
        records = _load_detected(detected_path)
        # Before partitioning, which is where an unreadable record would
        # otherwise surface as a bare traceback from collect_target_languages.
        _abort_on_malformed(stem, detected_path, records)
        pending, cached_count, no_target_count = _partition_pending(records, week_dir, classes, args.force)
        total_cached += cached_count
        total_no_target += no_target_count
        if remaining_limit is not None:
            if remaining_limit <= 0:
                stopped_early = bool(pending)
                break
            if len(pending) > remaining_limit:
                pending = pending[:remaining_limit]
                stopped_early = True  # this week alone has more than the remaining budget

        print(f"\n=== {stem}: {cached_count} cached, {no_target_count} without targets, "
              f"{len(pending)} pending ===")
        if not pending:
            continue

        warnings: list[dict] = []

        def _judge_one(record: dict, cancel_event: threading.Event) -> dict | None:
            t0 = time.monotonic()
            ensure_context_cache(record, week_dir, args.pdf_dir, lang_classes, languages_to_ignore)
            judge_record = judge_paper(record, week_dir, client, config, classes=classes, cancel_event=cancel_event)
            if judge_record is not None:
                save_judge_record(week_dir, judge_record)
                latencies.append(time.monotonic() - t0)
            return judge_record

        executor = ThreadPoolExecutor(max_workers=config.workers)
        week_quota_hit = False
        # One cancel_event per paper: a request already in flight can't be
        # aborted from another thread, but setting this lets an abandoned
        # worker notice at its next attempt/model boundary (see ChatCancelled)
        # and stop, instead of continuing to retry — and potentially
        # succeeding, uncounted — long after its result stopped being
        # collected. Keyed by future (not by the record, or by id() of it) so
        # the lookups below can't desync from `futures`, and declared out here
        # so the finally block can still reach it if submission itself raises.
        cancel_events: dict = {}
        # How many sequential chat() round-trips each paper will cost, so the
        # watchdog below can scale its patience to the work actually in
        # flight. Precomputed here rather than in the worker because the
        # watchdog runs on this thread. Cheap: judge.batches_needed is
        # in-memory over the record's own detections, the same walk
        # _partition_pending already did above.
        batches_by_future: dict = {}
        try:
            futures = {}
            for record in pending:
                cancel_event = threading.Event()
                future = executor.submit(_judge_one, record, cancel_event)
                futures[future] = record
                cancel_events[future] = cancel_event
                batches_by_future[future] = batches_needed(record, classes=classes)
            remaining = set(futures.keys())
            with tqdm(total=len(futures), desc=f"Judging {stem}") as pbar:
                while remaining:
                    # Scaled to the largest paper still in flight: wait() is a
                    # whole-batch stall detector (it only fires when *nothing*
                    # completes), so the run can afford to be as patient as its
                    # slowest legitimate member. Recomputed each pass, so the
                    # allowance drops back as the big papers finish.
                    max_batches = max(batches_by_future[f] for f in remaining)
                    watchdog_timeout = min(
                        _PER_PAPER_TIMEOUT_CAP,
                        _PER_PAPER_TIMEOUT + (max_batches - 1) * _PER_BATCH_BUDGET,
                    )
                    done, remaining = wait(remaining, timeout=watchdog_timeout, return_when=FIRST_COMPLETED)
                    if not done:
                        # These papers are only *marked* failed here; the
                        # cancel_events that actually stop their workers are
                        # set in the finally below, which this break falls
                        # straight into and which covers the quota stop too.
                        for future in list(remaining):
                            pid = futures[future].get("paper_id", "unknown")
                            tqdm.write(f"  TIMEOUT: [{pid}] no response after {watchdog_timeout}s "
                                       f"({batches_by_future[future]} batch(es) needed) — skipping")
                            warnings.append({
                                "paper_id": pid,
                                "step": "judge_timeout",
                                "error": f"no response after {watchdog_timeout}s "
                                         f"({batches_by_future[future]} batch(es) needed)",
                                "timestamp": datetime.now().isoformat(),
                            })
                            total_failed += 1
                            pbar.update(1)
                        remaining.clear()
                        stopped_early = True
                        break
                    for future in done:
                        pid = futures[future].get("paper_id", "unknown")
                        try:
                            if future.result() is not None:
                                total_judged += 1
                                if remaining_limit is not None:
                                    remaining_limit -= 1
                        except QuotaExhaustedError as exc:
                            tqdm.write(f"  QUOTA: [{pid}] {exc} — stopping cleanly, will resume next run")
                            week_quota_hit = True
                            quota_exhausted = True
                            stopped_early = True
                        except Exception as exc:
                            tqdm.write(f"  ERROR: [{pid}] {type(exc).__name__}: {exc}")
                            warnings.append({
                                "paper_id": pid,
                                "step": "judge",
                                "error": f"{type(exc).__name__}: {exc}",
                                "timestamp": datetime.now().isoformat(),
                            })
                            total_failed += 1
                        pbar.update(1)
                    if week_quota_hit:
                        break
        finally:
            # Cancel everything before shutting down, not just the papers the
            # watchdog timed out: a quota stop (`week_quota_hit` above) leaves
            # its in-flight workers running too, and QuotaExhaustedError is an
            # LLMUnavailableError, so chat() walks each one's whole remaining
            # fallback chain rather than stopping. Setting an already-finished
            # paper's event is a no-op, so this is safe on the happy path.
            #
            # shutdown(wait=False) does NOT detach those threads — the
            # interpreter joins them at exit — so without this, sys.exit()
            # blocks until every abandoned worker finishes its chain.
            for cancel_event in cancel_events.values():
                cancel_event.set()
            executor.shutdown(wait=False, cancel_futures=True)
            if warnings:
                warnings_path = week_dir / f"{stem}_judge_warnings.json"
                existing = []
                if warnings_path.exists():
                    try:
                        existing = json.loads(warnings_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        existing = []
                with warnings_path.open("w", encoding="utf-8") as fh:
                    json.dump(existing + warnings, fh, ensure_ascii=False, indent=2)
                print(f"Warnings saved to {warnings_path}")

        if quota_exhausted:
            break

    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"\nJudged: {total_judged}  Failed: {total_failed}  Cached: {total_cached}  "
          f"Mean latency: {mean_latency:.1f}s")

    if quota_exhausted:
        print("Stopped early: daily quota likely exhausted. Remaining papers stay pending "
              "— re-run later (already-judged papers are skipped).")
    elif stopped_early:
        print("Stopped early (timeout or --limit). Remaining papers stay pending — re-run to continue.")
    else:
        print("Run 'make manifest' (or python scripts/build_manifest.py) to fold verdicts into the manifest.")

    # Quota gets its own exit code rather than sharing 3: judge-catchup.yml's
    # retry loop must tell "retry this within the run" from "the provider is
    # done with us until midnight UTC", and keying that off a machine-readable
    # status beats grepping the human-readable message printed just above.
    if quota_exhausted:
        sys.exit(EXIT_QUOTA)
    sys.exit(EXIT_INCOMPLETE if stopped_early else EXIT_OK)


if __name__ == "__main__":
    main()
