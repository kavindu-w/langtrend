"""LLM-as-judge verification of regex language detections.

Takes the per-paper records produced by scripts/process_papers.py
(<week>_detected.jsonl) plus the cached section text (html_cache/,
pdf_cache/) — re-fetched on demand via ensure_context_cache() if missing,
e.g. on a fresh CI checkout — assembles a bounded-size context, and asks an
OpenAI-compatible model for a per-language verdict:

    studied         the paper uses/evaluates/builds resources for the language
    mentioned_only  a real language reference, but not part of the paper's work
    false_positive  the match is not this human language at all

Verdicts are cached one file per paper in <week_dir>/judge_cache/<safe_id>.json
and merged into the manifest by scripts/build_manifest.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langtrend.llm_client import JSONParseError, OpenAICompatClient, LLMClientConfig, extract_json
from langtrend.text_cleaning import _compiled_pattern

VERDICTS = {"studied", "mentioned_only", "false_positive"}

_SNIPPET_RADIUS = 250  # chars either side of a match
_MAX_MATCHES_PER_SECTION = 3
_MAX_LANGUAGES_PER_CALL = 12
_REASON_MAX_CHARS = 200

JUDGE_SYSTEM_PROMPT = """\
You verify *human language* detections in research papers.
You will get a paper's title, abstract, a list of candidate languages that a
regex scanner flagged, and text snippets from the sections where each
candidate matched.

For EACH candidate language, decide exactly one verdict:
- "studied": the paper uses, evaluates, collects data for, or builds
  resources/models for this language (it is part of the paper's experiments
  or artifacts).
- "mentioned_only": the name genuinely refers to this human language, but the
  paper only mentions it (related work, motivation, an example) without
  working on it.
- "false_positive": the matched text does not refer to this human language at
  all (e.g. an acronym, part of an author or person's name or place name, model/dataset name, script
  name, or a common word coincidence. Also, the instance "Latin" meaning the Latin
  alphabet/script or "Latin America" rather than the Latin language can be considered a false positive).
Reply with ONLY a JSON object, no markdown, matching this schema:
{"verdicts": [{"language": "<exact candidate name>",
               "verdict": "studied" | "mentioned_only" | "false_positive",
               "reason": "<one line>"}]}
Include every candidate exactly once. If the snippets are insufficient to be
sure, prefer "mentioned_only" over "false_positive".
"""


def safe_paper_id(paper_id: str) -> str:
    """'http://arxiv.org/abs/2605.25263v1' -> '2605.25263v1' (repo-wide convention)."""
    return str(paper_id).split("/")[-1]


# ---------------------------------------------------------------------------
# Target collection
# ---------------------------------------------------------------------------

def collect_target_languages(record: dict, classes: set[int] | None = None) -> list[dict]:
    """Dedupe a detected.jsonl record's languages into judge targets.

    Returns [{language, class, sections: [section names]}] sorted by ascending
    class (rarest first). `classes` optionally restricts to a class subset.
    """
    by_key: dict[tuple[str, int], dict] = {}
    for section_name, section in (record.get("sections") or {}).items():
        for det in section.get("detected_languages", []):
            language = det.get("language")
            class_id = det.get("class")
            if not language or class_id is None:
                continue
            class_id = int(class_id)
            if classes is not None and class_id not in classes:
                continue
            key = (language, class_id)
            target = by_key.setdefault(key, {"language": language, "class": class_id, "sections": []})
            if section_name not in target["sections"]:
                target["sections"].append(section_name)
    return sorted(by_key.values(), key=lambda t: (t["class"], t["language"]))


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

@dataclass
class Snippet:
    section: str
    start: int
    end: int
    text: str
    languages: list[str] = field(default_factory=list)
    # All section names whose window text was identical to this one (arXiv
    # HTML export sometimes attributes the same paragraph to several sibling
    # headings). Populated by the dedup pass in assemble_context; always
    # includes at least `section` itself.
    sections: list[str] = field(default_factory=list)


@dataclass
class JudgeContext:
    head: str
    snippets: list[Snippet]
    coverage: str  # e.g. "abstract_only", "abstract+html", "abstract+html+pdf"

    @property
    def total_chars(self) -> int:
        return len(self.head) + sum(len(s.text) for s in self.snippets)


def _load_section_texts(record: dict, week_dir: Path) -> tuple[dict[str, str], str]:
    """Map section name -> raw-ish text for every detected section, from caches.

    Returns (texts, coverage). The abstract section is excluded — the full
    abstract is always in the context head.
    """
    safe_id = safe_paper_id(record.get("paper_id", ""))
    html_data: dict = {}
    pdf_data: dict = {}
    html_path = week_dir / "html_cache" / f"{safe_id}.json"
    pdf_path = week_dir / "pdf_cache" / f"{safe_id}.json"
    if html_path.exists():
        try:
            html_data = json.loads(html_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            html_data = {}
    if pdf_path.exists():
        try:
            pdf_data = json.loads(pdf_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pdf_data = {}

    texts: dict[str, str] = {}
    used_html = used_pdf = False
    for section_name, section in (record.get("sections") or {}).items():
        source = section.get("source", "")
        if source == "abstract":
            continue
        if source in ("html", "html_partial"):
            cached = html_data.get(section_name)
            if isinstance(cached, dict):
                text = cached.get("cleaned_text") or cached.get("text") or ""
                if text:
                    texts[section_name] = text
                    used_html = True
        elif source == "pdf":
            text = pdf_data.get("screened_text") or pdf_data.get("body_text") or ""
            if text:
                texts[section_name] = text
                used_pdf = True

    parts = ["abstract"] + (["html"] if used_html else []) + (["pdf"] if used_pdf else [])
    coverage = "abstract_only" if len(parts) == 1 else "+".join(parts)
    return texts, coverage


# ---------------------------------------------------------------------------
# On-demand context fetch (for judge runs where html_cache/pdf_cache are
# missing — e.g. a fresh CI checkout on a different runner/day than the one
# that originally ran process_papers.py). Re-fetches only the source(s) this
# specific paper's `sources_checked` says were actually scanned, matching the
# original detection's coverage. Download → use → discard: the resulting
# cache files are gitignored, same as when process_papers.py builds them.
# ---------------------------------------------------------------------------

def _ensure_pdf_cache(
    record: dict,
    week_dir: Path,
    pdf_dir: Path,
) -> None:
    """Re-download + re-extract PDF text if pdf_cache/<safe_id>.json is missing.

    Only the extracted text is needed for judging — detection is not re-run,
    so `detected_languages` is left empty in the freshly written cache file.
    """
    from langtrend.pdf_processor import PDFProcessor, download_pdf
    from langtrend.text_cleaning import clean_paper_text_for_language_screening, trim_pdf_text_to_body

    paper_id = record.get("paper_id", "")
    safe_id = safe_paper_id(paper_id)
    pdf_cache_dir = week_dir / "pdf_cache"
    pdf_cache_path = pdf_cache_dir / f"{safe_id}.json"
    if pdf_cache_path.exists():
        return

    pdf_url = record.get("paper", {}).get("pdf_url")
    if not pdf_url:
        return

    try:
        pdf_path = download_pdf(pdf_url, pdf_dir, paper_id)
        if not pdf_path:
            return
        processor = PDFProcessor(input_dir=str(pdf_path.parent), output_dir=str(pdf_path.parent))
        raw_text, _ = processor.extract_text(pdf_path)
        if not raw_text:
            return
        cleaned_text = processor.clean_text(raw_text)
        body_text = trim_pdf_text_to_body(cleaned_text)
        screened_blocks, _ = clean_paper_text_for_language_screening(body_text, _label=paper_id)
        pdf_cache_dir.mkdir(parents=True, exist_ok=True)
        with pdf_cache_path.open("w", encoding="utf-8") as fh:
            json.dump({
                "paper_id": paper_id,
                "text": raw_text,
                "cleaned_text": cleaned_text,
                "body_text": body_text,
                "screened_text": "\n\n".join(screened_blocks),
                "detected_languages": [],
            }, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[judge] PDF re-fetch failed for {paper_id}: {type(exc).__name__}: {exc}")


def ensure_context_cache(
    record: dict,
    week_dir: Path,
    pdf_dir: Path,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
) -> None:
    """JIT re-fetch whichever source(s) this paper's `sources_checked` used.

    Safe to call unconditionally — each fetch function already skips its own
    work when the corresponding cache file exists, so this is a no-op on a
    machine that already has the caches from the original process_papers.py
    run (e.g. local development).
    """
    sources = record.get("sources_checked", [])
    paper = record.get("paper", {})
    if "html" in sources or "html_partial" in sources:
        from langtrend.html_processor import recheck_languages_from_html
        try:
            recheck_languages_from_html(paper, lang_classes, languages_to_ignore, out_dir=week_dir / "html_cache")
        except Exception as exc:
            print(f"[judge] HTML re-fetch failed for {record.get('paper_id')}: {type(exc).__name__}: {exc}")
    if "pdf" in sources:
        _ensure_pdf_cache(record, week_dir, pdf_dir)


def _snap_window(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a window to the nearest whitespace so words are not cut."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def assemble_context(
    record: dict,
    week_dir: Path,
    targets: list[dict],
    max_chars: int = 12000,
) -> JudgeContext:
    """Title + abstract head, then match-window snippets round-robin per language.

    Languages are filled rarest-class-first, one snippet per language per
    round, so every candidate gets evidence before any gets a second snippet.
    """
    paper = record.get("paper", {})
    head = f"TITLE: {paper.get('title', '')}\n\nABSTRACT: {paper.get('abstract', '')}"
    texts, coverage = _load_section_texts(record, week_dir)

    # Candidate windows per (section, language), merged into per-section snippets.
    windows_by_section: dict[str, list[tuple[int, int]]] = {}
    for target in targets:
        pattern = _compiled_pattern(target["language"])
        for section_name in target["sections"]:
            text = texts.get(section_name)
            if not text:
                continue
            for i, match in enumerate(pattern.finditer(text)):
                if i >= _MAX_MATCHES_PER_SECTION:
                    break
                start, end = _snap_window(
                    text,
                    max(0, match.start() - _SNIPPET_RADIUS),
                    min(len(text), match.end() + _SNIPPET_RADIUS),
                )
                windows_by_section.setdefault(section_name, []).append((start, end))

    target_names = [t["language"] for t in targets]

    # Flatten to one Snippet per (section, window), deduping snippets whose
    # text is identical after whitespace normalization. arXiv HTML export
    # sometimes attributes the same paragraph to several sibling section
    # headings, which would otherwise burn context budget on verbatim repeats
    # with zero new evidence. Language coverage is unioned across duplicates
    # so no candidate loses evidence it would've gotten from either copy —
    # only exact-text repeats collapse, never distinct mentions.
    unique_by_text: dict[str, Snippet] = {}
    for section_name, windows in windows_by_section.items():
        text = texts[section_name]
        for start, end in _merge_windows(windows):
            snippet_text = text[start:end].strip()
            if not snippet_text:
                continue
            covered = [name for name in target_names if _compiled_pattern(name).search(snippet_text)]
            key = " ".join(snippet_text.split())
            existing = unique_by_text.get(key)
            if existing is None:
                unique_by_text[key] = Snippet(
                    section=section_name,
                    start=start,
                    end=end,
                    text=snippet_text,
                    languages=covered,
                    sections=[section_name],
                )
            else:
                existing.sections.append(section_name)
                for name in covered:
                    if name not in existing.languages:
                        existing.languages.append(name)

    # Per-language queues of unselected snippets (targets are already
    # class-ascending, so rare languages claim budget first).
    queues: dict[str, list[Snippet]] = {name: [] for name in target_names}
    for snippet in unique_by_text.values():
        for name in snippet.languages:
            if name in queues:
                queues[name].append(snippet)

    budget = max_chars - len(head)
    selected: list[Snippet] = []
    seen: set[int] = set()
    progressed = True
    while progressed and budget > 0:
        progressed = False
        for name in target_names:
            queue = queues.get(name, [])
            while queue:
                snippet = queue.pop(0)
                if id(snippet) in seen:
                    continue
                if len(snippet.text) > budget:
                    continue
                seen.add(id(snippet))
                selected.append(snippet)
                budget -= len(snippet.text)
                progressed = True
                break

    return JudgeContext(head=head, snippets=selected, coverage=coverage)


# ---------------------------------------------------------------------------
# Prompt + verdict parsing
# ---------------------------------------------------------------------------

def build_messages(context: JudgeContext, targets: list[dict]) -> list[dict]:
    lines = [context.head, "", "CANDIDATE LANGUAGES:"]
    for target in targets:
        sections = ", ".join(target["sections"]) or "abstract"
        lines.append(f"- {target['language']} (resource class {target['class']}) — detected in: {sections}")
    if context.snippets:
        lines += ["", "SNIPPETS:"]
        for snippet in context.snippets:
            candidates = ", ".join(snippet.languages)
            section_names = snippet.sections or [snippet.section]
            shown = ", ".join(section_names[:3])
            if len(section_names) > 3:
                shown += f" (+{len(section_names) - 3} more)"
            lines.append(f"[Section: {shown}] (candidates: {candidates})")
            lines.append(f'"{snippet.text}"')
            lines.append("")
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines).strip()},
    ]


def validate_verdicts(parsed: dict, targets: list[dict]) -> dict[str, dict]:
    """Keep only requested languages with valid verdicts; canonicalize names."""
    canonical = {t["language"].lower(): t["language"] for t in targets}
    raw = parsed.get("verdicts", [])
    if isinstance(raw, dict):  # tolerate {"Lang": {...}} map form
        raw = [{"language": k, **(v if isinstance(v, dict) else {})} for k, v in raw.items()]
    result: dict[str, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = canonical.get(str(entry.get("language", "")).strip().lower())
        verdict = str(entry.get("verdict", "")).strip().lower()
        if not name or verdict not in VERDICTS:
            continue
        result[name] = {
            "verdict": verdict,
            "reason": str(entry.get("reason", ""))[:_REASON_MAX_CHARS],
        }
    return result


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

def _chat_for_verdicts(client: OpenAICompatClient, messages: list[dict], targets: list[dict]) -> dict[str, dict]:
    reply = client.chat(messages)
    try:
        parsed = extract_json(reply)
    except JSONParseError:
        # One repair round-trip; small models often wrap or truncate JSON.
        repair = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "Your previous reply was not valid JSON. Reply with ONLY the JSON object."},
        ]
        parsed = extract_json(client.chat(repair))
    return validate_verdicts(parsed, targets)


def judge_paper(
    record: dict,
    week_dir: Path,
    client: OpenAICompatClient,
    config: LLMClientConfig,
    classes: set[int] | None = None,
) -> dict | None:
    """Judge one paper. Returns the judge-cache record, or None if no targets.

    Raises on unrecoverable model/parse failure — callers record a warning and
    leave the paper unjudged.
    """
    targets = collect_target_languages(record, classes=classes)
    if not targets:
        return None

    context = assemble_context(record, week_dir, targets, max_chars=config.max_context_chars)
    verdicts: dict[str, dict] = {}
    for start in range(0, len(targets), _MAX_LANGUAGES_PER_CALL):
        batch = targets[start : start + _MAX_LANGUAGES_PER_CALL]
        messages = build_messages(context, batch)
        verdicts.update(_chat_for_verdicts(client, messages, batch))

    return {
        "paper_id": record.get("paper_id", ""),
        "judge_model": config.model,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "context_coverage": context.coverage,
        "context_chars": context.total_chars,
        "verdicts": verdicts,
    }


def needs_judging(record: dict, cached: dict | None, classes: set[int] | None = None) -> bool:
    """True when the paper has judge targets not covered by its cached verdicts.

    Handles re-processed papers: newly detected languages make the paper
    pending again even though a cache file exists.
    """
    targets = collect_target_languages(record, classes=classes)
    if not targets:
        return False
    if not cached:
        return True
    verdicts = cached.get("verdicts", {})
    return any(t["language"] not in verdicts for t in targets)


# ---------------------------------------------------------------------------
# Cache + manifest merge
# ---------------------------------------------------------------------------

def judge_cache_path(week_dir: Path, paper_id: str) -> Path:
    return week_dir / "judge_cache" / f"{safe_paper_id(paper_id)}.json"


def save_judge_record(week_dir: Path, judge_record: dict) -> Path:
    path = judge_cache_path(week_dir, judge_record["paper_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(judge_record, fh, ensure_ascii=False, indent=2)
    return path


def load_judge_cache(judge_cache_dir: Path) -> dict[str, dict]:
    """Map safe_id -> judge record for every cached verdict file."""
    cache: dict[str, dict] = {}
    if not judge_cache_dir.is_dir():
        return cache
    for path in sorted(judge_cache_dir.glob("*.json")):
        try:
            cache[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return cache


def _apply_to_entry(entry: dict, verdicts: dict[str, dict], judge_record: dict) -> bool:
    verdict = verdicts.get(entry.get("language", ""))
    if not verdict:
        return False
    entry["judge_verdict"] = verdict["verdict"]
    entry["judge_reason"] = verdict["reason"]
    entry["judge_model"] = judge_record.get("judge_model", "")
    entry["judged_at"] = judge_record.get("judged_at", "")
    return True


def apply_judge_to_flagged(flagged_papers: list[dict], judge_cache: dict[str, dict]) -> int:
    """Attach judge fields to manifest flagged-paper entries in place.

    Covers both the merged `languages` list and every per-section
    `detected_languages` entry. Returns the number of language entries updated.
    """
    updated = 0
    for flagged in flagged_papers:
        paper_id = flagged.get("paper", {}).get("id", "")
        judge_record = judge_cache.get(safe_paper_id(paper_id))
        if not judge_record:
            continue
        verdicts = judge_record.get("verdicts", {})
        for entry in flagged.get("languages", []):
            updated += _apply_to_entry(entry, verdicts, judge_record)
        for section in flagged.get("sections", []):
            for entry in section.get("detected_languages", []):
                _apply_to_entry(entry, verdicts, judge_record)
    return updated
