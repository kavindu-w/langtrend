"""
Unit tests for scripts/judge_languages.py's pure logic (no network, no model calls).

Run with: pytest tests/test_judge_languages.py -v
"""

import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT))

import judge_languages as jl


# ---------------------------------------------------------------------------
# _parse_classes
# ---------------------------------------------------------------------------

class TestParseClasses:
    def test_none_means_all_classes(self):
        assert jl._parse_classes(None) is None
        assert jl._parse_classes("") is None

    def test_range(self):
        assert jl._parse_classes("0-4") == {0, 1, 2, 3, 4}

    def test_comma_list(self):
        assert jl._parse_classes("0,2,5") == {0, 2, 5}

    def test_mixed_range_and_list(self):
        assert jl._parse_classes("0-2,5") == {0, 1, 2, 5}


# ---------------------------------------------------------------------------
# _week_dir / _resolve_input
# ---------------------------------------------------------------------------

class TestWeekDir:
    def test_week_dir_extracts_slug(self):
        result = jl._week_dir(Path("arxiv_papers_20260518_to_20260525.jsonl"))
        assert result == jl._PROCESSED_DIR / "weeks" / "20260518_to_20260525"

    def test_week_dir_falls_back_when_no_slug(self):
        assert jl._week_dir(Path("something_else.jsonl")) == jl._PROCESSED_DIR

    def test_resolve_input_uses_explicit_path(self):
        args = type("Args", (), {"input": Path("/tmp/custom.jsonl")})()
        assert jl._resolve_input(args) == Path("/tmp/custom.jsonl")

    def test_resolve_input_derives_from_end_date(self):
        args = type("Args", (), {"input": None, "end_date": "2026-05-25", "window_days": 7})()
        result = jl._resolve_input(args)
        assert result.name == "arxiv_papers_20260518_to_20260525.jsonl"


# ---------------------------------------------------------------------------
# _find_all_week_slugs / _detected_path_for_week (sweep mode discovery)
# ---------------------------------------------------------------------------

class TestSweepDiscovery:
    def _make_week(self, weeks_dir: Path, slug: str, with_detected: bool = True) -> Path:
        week_dir = weeks_dir / slug
        week_dir.mkdir(parents=True)
        if with_detected:
            (week_dir / f"arxiv_papers_{slug}_detected.jsonl").write_text("", encoding="utf-8")
        return week_dir

    def test_finds_weeks_with_detected_jsonl_sorted_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        weeks_dir = tmp_path / "weeks"
        self._make_week(weeks_dir, "20260525_to_20260601")
        self._make_week(weeks_dir, "20260427_to_20260504")
        self._make_week(weeks_dir, "20260511_to_20260518")

        assert jl._find_all_week_slugs() == [
            "20260525_to_20260601", "20260511_to_20260518", "20260427_to_20260504",
        ]

    def test_skips_weeks_without_detected_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        weeks_dir = tmp_path / "weeks"
        self._make_week(weeks_dir, "20260427_to_20260504", with_detected=False)
        self._make_week(weeks_dir, "20260511_to_20260518", with_detected=True)

        assert jl._find_all_week_slugs() == ["20260511_to_20260518"]

    def test_ignores_non_week_shaped_directories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        weeks_dir = tmp_path / "weeks"
        weeks_dir.mkdir(parents=True)
        (weeks_dir / "not_a_week_dir").mkdir()

        assert jl._find_all_week_slugs() == []

    def test_returns_empty_when_weeks_dir_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        assert jl._find_all_week_slugs() == []

    def test_detected_path_for_week_finds_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        week_dir = self._make_week(tmp_path / "weeks", "20260427_to_20260504")
        result = jl._detected_path_for_week("20260427_to_20260504")
        assert result == week_dir / "arxiv_papers_20260427_to_20260504_detected.jsonl"


# ---------------------------------------------------------------------------
# _partition_pending
# ---------------------------------------------------------------------------

class TestPartitionPending:
    def _record(self, paper_id, language="Swahili", class_id=0):
        return {
            "paper_id": paper_id,
            "paper": {"id": paper_id, "title": "t", "abstract": "a"},
            "sections": {"abstract": {"source": "abstract", "detected_languages": [
                {"language": language, "class": class_id},
            ]}},
        }

    def test_splits_pending_from_cached(self, tmp_path):
        cached_record = self._record("http://arxiv.org/abs/1")
        pending_record = self._record("http://arxiv.org/abs/2")
        judge_cache_dir = tmp_path / "judge_cache"
        judge_cache_dir.mkdir()
        (judge_cache_dir / "1.json").write_text(json.dumps({
            "judge_model": "m", "judged_at": "t", "verdicts": {"Swahili": {"verdict": "studied", "reason": "r"}},
        }), encoding="utf-8")

        pending, cached_count, no_target_count = jl._partition_pending(
            [cached_record, pending_record], tmp_path, classes=None, force=False,
        )
        assert cached_count == 1
        assert no_target_count == 0
        assert [r["paper_id"] for r in pending] == ["http://arxiv.org/abs/2"]

    def test_force_re_includes_cached_papers(self, tmp_path):
        record = self._record("http://arxiv.org/abs/1")
        judge_cache_dir = tmp_path / "judge_cache"
        judge_cache_dir.mkdir()
        (judge_cache_dir / "1.json").write_text(json.dumps({
            "judge_model": "m", "judged_at": "t", "verdicts": {"Swahili": {"verdict": "studied", "reason": "r"}},
        }), encoding="utf-8")

        pending, cached_count, _ = jl._partition_pending([record], tmp_path, classes=None, force=True)
        assert cached_count == 0
        assert len(pending) == 1

    def test_no_target_papers_are_not_pending(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/1", "paper": {}, "sections": {}}
        pending, cached_count, no_target_count = jl._partition_pending([record], tmp_path, classes=None, force=False)
        assert pending == []
        assert no_target_count == 1


# ---------------------------------------------------------------------------
# --check-only (end-to-end via main(), no network)
# ---------------------------------------------------------------------------

class TestCheckOnly:
    def _week(self, tmp_path, slug, languages):
        week_dir = tmp_path / "weeks" / slug
        week_dir.mkdir(parents=True)
        record = {
            "paper_id": f"http://arxiv.org/abs/{slug}",
            "paper": {"id": f"http://arxiv.org/abs/{slug}", "title": "t", "abstract": "a"},
            "sections": {"abstract": {"source": "abstract", "detected_languages": [
                {"language": lang, "class": 0} for lang in languages
            ]}},
        }
        detected_path = week_dir / f"arxiv_papers_{slug}_detected.jsonl"
        detected_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return week_dir

    def test_check_only_exits_3_with_pending_papers(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        monkeypatch.setattr(jl, "_METADATA_DIR", tmp_path / "raw")
        self._week(tmp_path, "20260427_to_20260504", ["Swahili"])
        monkeypatch.setattr(sys, "argv", ["judge_languages.py", "--sweep-all-weeks", "--check-only"])

        with pytest.raises(SystemExit) as exc_info:
            jl.main()
        assert exc_info.value.code == jl.EXIT_INCOMPLETE
        assert "still pending" in capsys.readouterr().out

    def test_check_only_exits_0_when_all_judged(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        monkeypatch.setattr(jl, "_METADATA_DIR", tmp_path / "raw")
        week_dir = self._week(tmp_path, "20260427_to_20260504", ["Swahili"])
        judge_cache_dir = week_dir / "judge_cache"
        judge_cache_dir.mkdir()
        (judge_cache_dir / "20260427_to_20260504.json").write_text(json.dumps({
            "judge_model": "m", "judged_at": "t",
            "verdicts": {"Swahili": {"verdict": "studied", "reason": "r"}},
        }), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["judge_languages.py", "--sweep-all-weeks", "--check-only"])

        with pytest.raises(SystemExit) as exc_info:
            jl.main()
        assert exc_info.value.code == jl.EXIT_OK
        assert "Fully judged" in capsys.readouterr().out

    def test_check_only_does_not_require_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        monkeypatch.setattr(jl, "_METADATA_DIR", tmp_path / "raw")
        monkeypatch.delenv("LLM_JUDGE_API_KEY", raising=False)
        self._week(tmp_path, "20260427_to_20260504", ["Swahili"])
        monkeypatch.setattr(sys, "argv", [
            "judge_languages.py", "--sweep-all-weeks", "--check-only",
            "--base-url", "https://not-local-and-unreachable.example",
        ])

        with pytest.raises(SystemExit) as exc_info:
            jl.main()
        assert exc_info.value.code == jl.EXIT_INCOMPLETE  # not EXIT_ERROR from the missing-key check


# ---------------------------------------------------------------------------
# Stop-early exit codes (EXIT_QUOTA vs EXIT_INCOMPLETE)
#
# judge-catchup.yml's retry loop branches on these two codes to decide whether
# to retry within the same run (3) or defer to the next scheduled run (4), so
# they must stay distinct and must not be collapsed back into one.
# ---------------------------------------------------------------------------

class TestStopEarlyExitCodes:
    def test_quota_and_incomplete_are_distinct(self):
        assert jl.EXIT_QUOTA != jl.EXIT_INCOMPLETE
        assert jl.EXIT_QUOTA not in (jl.EXIT_OK, jl.EXIT_ERROR)

    def _week(self, root, slug, papers=1):
        week_dir = root / "weeks" / slug
        week_dir.mkdir(parents=True)
        records = [
            {
                "paper_id": f"http://arxiv.org/abs/{slug}.{n}",
                "paper": {"id": f"http://arxiv.org/abs/{slug}.{n}", "title": "t", "abstract": "a"},
                "sections": {"abstract": {"source": "abstract", "detected_languages": [
                    {"language": "Swahili", "class": 0},
                ]}},
            }
            for n in range(papers)
        ]
        (week_dir / f"arxiv_papers_{slug}_detected.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return week_dir

    def _run_with_judge(self, tmp_path, monkeypatch, judge_side_effect, papers=1):
        """Drive main()'s judging path with everything network-bound stubbed out."""
        monkeypatch.setattr(jl, "_PROCESSED_DIR", tmp_path)
        monkeypatch.setattr(jl, "_METADATA_DIR", tmp_path / "raw")
        self._week(tmp_path, "20260427_to_20260504", papers=papers)
        monkeypatch.setenv("LLM_JUDGE_API_KEY", "test-key")
        monkeypatch.setattr(jl, "_load_language_data", lambda path: ({1: {"Swahili"}}, set()))
        monkeypatch.setattr(jl, "OpenAICompatClient", lambda config: type(
            "FakeClient", (), {"ping": lambda self: None})())
        monkeypatch.setattr(jl, "ensure_context_cache",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(jl, "save_judge_record", lambda *args, **kwargs: None)
        monkeypatch.setattr(jl, "judge_paper", judge_side_effect)
        monkeypatch.setattr(sys, "argv", ["judge_languages.py", "--sweep-all-weeks"])

        with pytest.raises(SystemExit) as exc_info:
            jl.main()
        return exc_info.value.code

    def test_quota_exhausted_exits_4(self, tmp_path, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise jl.QuotaExhaustedError("daily limit reached")

        code = self._run_with_judge(tmp_path, monkeypatch, boom)
        assert code == jl.EXIT_QUOTA
        assert "daily quota likely exhausted" in capsys.readouterr().out

    def test_clean_full_run_exits_0(self, tmp_path, monkeypatch):
        verdict = {"judge_model": "m", "judged_at": "t", "verdicts": {}}
        code = self._run_with_judge(tmp_path, monkeypatch, lambda *a, **k: verdict)
        assert code == jl.EXIT_OK


# ---------------------------------------------------------------------------
# Per-paper cancellation wiring
#
# The worker-side half of this lives in langtrend/{judge,llm_client}.py and is
# covered by their own tests; what's tested here is the main-thread half —
# that judge_languages.py actually *sets* the events on every path where it
# stops collecting a worker's result. Neither path is observable from the exit
# code alone, so both assert on the event the worker was handed.
# ---------------------------------------------------------------------------

class TestCancellationWiring(TestStopEarlyExitCodes):
    def test_watchdog_cancels_the_papers_it_times_out(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(jl, "_PER_PAPER_TIMEOUT", 0.2)
        handed = {}

        def blocks_until_cancelled(record, week_dir, client, config, classes=None, cancel_event=None):
            handed["event"] = cancel_event
            # Bounded so a regression fails the test instead of hanging it.
            if not cancel_event.wait(10):
                raise AssertionError("watchdog never set the cancel event")
            raise jl.ChatCancelled("cancelled")

        code = self._run_with_judge(tmp_path, monkeypatch, blocks_until_cancelled)

        assert code == jl.EXIT_INCOMPLETE
        assert "TIMEOUT" in capsys.readouterr().out
        assert handed["event"].is_set()

    def test_quota_stop_cancels_the_workers_still_in_flight(self, tmp_path, monkeypatch):
        """Regression: the quota break used to leave in-flight workers running.

        QuotaExhaustedError is an LLMUnavailableError, so chat() walks each
        abandoned worker's whole remaining fallback chain — and because
        shutdown(wait=False) doesn't detach those threads, the interpreter
        joins them at exit and sys.exit() blocks behind them.
        """
        handed = []
        first = threading.Event()

        def quota_on_first_other_blocks(record, week_dir, client, config, classes=None, cancel_event=None):
            handed.append(cancel_event)
            if not first.is_set():
                first.set()
                raise jl.QuotaExhaustedError("daily limit reached")
            if not cancel_event.wait(10):
                raise AssertionError("quota stop never cancelled this worker")
            raise jl.ChatCancelled("cancelled")

        code = self._run_with_judge(
            tmp_path, monkeypatch, quota_on_first_other_blocks, papers=2)

        assert code == jl.EXIT_QUOTA
        assert len(handed) == 2
        assert all(event.is_set() for event in handed)

    def test_each_paper_gets_its_own_event(self, tmp_path, monkeypatch):
        handed = []

        def record_event(record, week_dir, client, config, classes=None, cancel_event=None):
            handed.append(cancel_event)
            return {"judge_model": "m", "judged_at": "t", "verdicts": {}}

        code = self._run_with_judge(tmp_path, monkeypatch, record_event, papers=3)

        assert code == jl.EXIT_OK
        assert len(handed) == 3
        assert len({id(event) for event in handed}) == 3, "events must not be shared between papers"

