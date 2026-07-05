"""
Unit tests for langtrend/judge.py and langtrend/llm_client.py (no network).

Run with: pytest tests/test_judge.py -v
"""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.judge import (
    apply_judge_to_flagged,
    assemble_context,
    build_messages,
    collect_target_languages,
    ensure_context_cache,
    judge_paper,
    load_judge_cache,
    needs_judging,
    safe_paper_id,
    save_judge_record,
    validate_verdicts,
    _relocate_to_raw_text,
)
from langtrend.llm_client import (
    JSONParseError,
    LLMClientConfig,
    LLMUnavailableError,
    OpenAICompatClient,
    QuotaExhaustedError,
    _looks_like_daily_quota_exhausted,
    _RpmThrottle,
    extract_json,
)


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"verdicts": []}') == {"verdicts": []}

    def test_json_in_fences(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_after_think_block(self):
        text = "<think>hmm the languages are...\n{not json}</think>{\"a\": 1}"
        assert extract_json(text) == {"a": 1}

    def test_json_with_surrounding_prose(self):
        assert extract_json('Here is my answer: {"a": 1} hope it helps') == {"a": 1}

    def test_trailing_comma_repaired(self):
        assert extract_json('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}

    def test_braces_inside_strings(self):
        assert extract_json('{"a": "curly } brace"}') == {"a": "curly } brace"}

    def test_garbage_raises(self):
        with pytest.raises(JSONParseError):
            extract_json("no json here at all")

    def test_unclosed_object_raises(self):
        with pytest.raises(JSONParseError):
            extract_json('{"a": 1')


# ---------------------------------------------------------------------------
# Daily quota-exhaustion detection (Groq/OpenAI-style 429 responses)
# ---------------------------------------------------------------------------

class TestLooksLikeDailyQuotaExhausted:
    def test_message_mentions_per_day(self):
        assert _looks_like_daily_quota_exhausted(
            "Rate limit reached for model on requests per day (RPD): Limit 1000, Used 1000.", None
        )

    def test_message_mentions_rpd(self):
        assert _looks_like_daily_quota_exhausted("Error: RPD limit exceeded", None)

    def test_large_retry_after_implies_daily(self):
        assert _looks_like_daily_quota_exhausted("rate limited", "3600")

    def test_small_retry_after_is_per_minute_not_daily(self):
        assert not _looks_like_daily_quota_exhausted("rate limited, back off", "2")

    def test_no_signal_is_not_daily(self):
        assert not _looks_like_daily_quota_exhausted("temporary server error", None)

    def test_non_numeric_retry_after_ignored(self):
        assert not _looks_like_daily_quota_exhausted("rate limited", "Tue, 01 Jul 2026 00:00:00 GMT")


class _FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


class TestClientChatQuotaHandling:
    def _client(self):
        return OpenAICompatClient(LLMClientConfig(base_url="http://fake", timeout=5))

    def test_daily_quota_message_raises_quota_exhausted_immediately(self):
        client = self._client()
        responses = [_FakeResponse(429, text="Rate limit reached on requests per day (RPD): Limit 1000, Used 1000.")]
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.side_effect = responses
            with pytest.raises(QuotaExhaustedError):
                client.chat([{"role": "user", "content": "hi"}])
        # no retries attempted — a daily cap won't clear by waiting a few seconds
        assert mock_session.return_value.post.call_count == 1

    def test_large_retry_after_raises_quota_exhausted(self):
        client = self._client()
        responses = [_FakeResponse(429, text="rate limited", headers={"Retry-After": "3600"})]
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.side_effect = responses
            with pytest.raises(QuotaExhaustedError):
                client.chat([{"role": "user", "content": "hi"}])

    def test_ordinary_429_retries_then_raises_llm_unavailable_not_quota(self):
        client = self._client()
        responses = [_FakeResponse(429, text="rate limited", headers={"Retry-After": "1"})] * 5
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = responses
            with pytest.raises(LLMUnavailableError) as exc_info:
                client.chat([{"role": "user", "content": "hi"}])
        assert not isinstance(exc_info.value, QuotaExhaustedError)


# ---------------------------------------------------------------------------
# _RpmThrottle
# ---------------------------------------------------------------------------

class TestRpmThrottle:
    def test_spaces_requests_evenly_not_in_a_burst(self):
        # Some providers (Cerebras included) reject bursts even when the
        # total over any 60s window is under the limit — e.g. "60 RPM
        # enforced as 1 request per second." A counter that lets `rpm`
        # requests fire back-to-back then goes quiet is compliant with the
        # aggregate but not with that per-second enforcement; only strict
        # even spacing (60/rpm seconds between starts) is safe against both.
        throttle = _RpmThrottle(rpm=60)  # 60/60 = 1s between requests
        start = time.monotonic()
        throttle.acquire()  # first call never waits
        first = time.monotonic() - start
        throttle.acquire()
        second = time.monotonic() - start
        assert first < 0.2
        assert second >= 0.95  # ~1s later, not immediate

    def test_concurrent_workers_share_the_same_pacing(self):
        # Several worker threads calling acquire() "simultaneously" must
        # still be spaced out collectively — the throttle is shared, not
        # per-thread, so N workers don't each get their own rpm budget.
        throttle = _RpmThrottle(rpm=60)
        timestamps = []
        lock = threading.Lock()

        def worker():
            throttle.acquire()
            with lock:
                timestamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        offsets = sorted(ts - start for ts in timestamps)
        # 4 requests at 1/s minimum spacing must span close to 3 seconds
        # (not fire in a single burst near t=0).
        assert offsets[-1] - offsets[0] >= 2.5


# ---------------------------------------------------------------------------
# Fixtures: a detected.jsonl-style record plus caches in a tmp week dir
# ---------------------------------------------------------------------------

@pytest.fixture
def record():
    return {
        "paper_id": "http://arxiv.org/abs/2606.00001v1",
        "paper": {
            "id": "http://arxiv.org/abs/2606.00001v1",
            "title": "A Study of Swahili ASR",
            "abstract": "We build ASR for Swahili and compare against French baselines.",
        },
        "sources_checked": ["abstract", "html"],
        "sections": {
            "abstract": {
                "source": "abstract",
                "detected_languages": [
                    {"language": "Swahili", "class": 0},
                    {"language": "French", "class": 5},
                ],
            },
            "3Method": {
                "source": "html",
                "detected_languages": [
                    {"language": "Swahili", "class": 0},
                    {"language": "Swahili", "class": 0},  # duplicates are common
                    {"language": "Ari", "class": 0, "needs_review": True, "flag_reason": "ARI = Adjusted Rand Index"},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def week_dir(tmp_path, record):
    html_cache = tmp_path / "html_cache"
    html_cache.mkdir()
    section_text = (
        "We train on Swahili speech data collected from radio. "
        "The Ari score of the clustering is 0.8. "
        + "filler words " * 40
        + "Swahili morphology is agglutinative."
    )
    (html_cache / "2606.00001v1.json").write_text(
        json.dumps({
            "_complete": True,
            "3Method": {"text": section_text, "cleaned_text": section_text, "detected": ["Swahili", "Ari"]},
        }),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# collect_target_languages
# ---------------------------------------------------------------------------

class TestCollectTargets:
    def test_dedupes_and_sorts_by_class(self, record):
        targets = collect_target_languages(record)
        assert [t["language"] for t in targets] == ["Ari", "Swahili", "French"]
        swahili = next(t for t in targets if t["language"] == "Swahili")
        assert swahili["sections"] == ["abstract", "3Method"]

    def test_class_filter(self, record):
        targets = collect_target_languages(record, classes={0, 1, 2, 3, 4})
        assert [t["language"] for t in targets] == ["Ari", "Swahili"]

    def test_empty_record(self):
        assert collect_target_languages({"sections": {}}) == []


# ---------------------------------------------------------------------------
# assemble_context
# ---------------------------------------------------------------------------

class TestAssembleContext:
    def test_head_and_snippets(self, record, week_dir):
        targets = collect_target_languages(record)
        context = assemble_context(record, week_dir, targets, max_chars=12000)
        assert "A Study of Swahili ASR" in context.head
        assert context.coverage == "abstract+html"
        assert context.snippets, "expected at least one snippet"
        # every non-abstract-only language gets evidence
        covered = {lang for s in context.snippets for lang in s.languages}
        assert {"Swahili", "Ari"} <= covered
        # each snippet window actually contains its matches
        for snippet in context.snippets:
            for lang in snippet.languages:
                assert lang.lower() in snippet.text.lower()

    def test_budget_respected(self, record, week_dir):
        targets = collect_target_languages(record)
        context = assemble_context(record, week_dir, targets, max_chars=600)
        assert context.total_chars <= 600 or not context.snippets

    def test_abstract_only_fallback(self, record, tmp_path):
        targets = collect_target_languages(record)
        context = assemble_context(record, tmp_path, targets, max_chars=12000)
        assert context.coverage == "abstract_only"
        assert context.snippets == []

    def test_messages_list_all_candidates(self, record, week_dir):
        targets = collect_target_languages(record)
        context = assemble_context(record, week_dir, targets, max_chars=12000)
        messages = build_messages(context, targets)
        assert messages[0]["role"] == "system"
        user = messages[1]["content"]
        for target in targets:
            assert target["language"] in user

    def test_pdf_snippet_prefers_raw_text_over_screened(self, tmp_path):
        # screened_text has the numbered-list marker's digit blanked out by
        # character-level screening ("(2)" -> "( )"); body_text still has it.
        # The snippet shown to the judge should come from body_text.
        record = {
            "paper_id": "http://arxiv.org/abs/2606.00002v1",
            "paper": {"id": "http://arxiv.org/abs/2606.00002v1", "title": "T", "abstract": "A"},
            "sources_checked": ["abstract", "pdf"],
            "sections": {
                "abstract": {"source": "abstract", "detected_languages": []},
                "pdf_full_text": {
                    "source": "pdf",
                    "detected_languages": [{"language": "Swahili", "class": 0}],
                },
            },
        }
        body_text = (
            "some filler text before the marker. "
            "(2) we evaluate on Swahili speech data collected from radio broadcasts. "
            "more filler text follows after this point to pad the window out nicely."
        )
        screened_text = body_text.replace("(2)", "( )")
        pdf_cache = tmp_path / "pdf_cache"
        pdf_cache.mkdir()
        (pdf_cache / "2606.00002v1.json").write_text(
            json.dumps({"body_text": body_text, "screened_text": screened_text}),
            encoding="utf-8",
        )

        targets = collect_target_languages(record)
        context = assemble_context(record, tmp_path, targets, max_chars=12000)
        assert context.snippets, "expected at least one snippet"
        snippet = context.snippets[0]
        assert "(2)" in snippet.text
        assert "( )" not in snippet.text
        assert "Swahili" in snippet.languages


# ---------------------------------------------------------------------------
# _relocate_to_raw_text
# ---------------------------------------------------------------------------

class TestRelocateToRawText:
    def test_relocates_bridging_a_stripped_digit(self):
        screened = (
            "results were strong overall ( ) analyzing the details of this "
            "experiment further reveals interesting patterns worth noting"
        )
        raw = (
            "results were strong overall (4) analyzing the details of this "
            "experiment further reveals interesting patterns worth noting"
        )
        relocated = _relocate_to_raw_text(screened, raw, radius=200)
        assert relocated is not None
        assert "(4)" in relocated

    def test_returns_none_when_anchor_too_short(self):
        assert _relocate_to_raw_text("too short", "anything at all here", radius=50) is None

    def test_returns_none_when_ambiguous(self):
        # Same anchor phrase appears twice in raw_text — don't guess which.
        screened = "alpha bravo charlie delta echo foxtrot golf hotel"
        raw = (
            "alpha bravo charlie delta echo foxtrot golf hotel india "
            "alpha bravo charlie delta echo foxtrot golf hotel juliet"
        )
        assert _relocate_to_raw_text(screened, raw, radius=50) is None

    def test_returns_none_when_anchor_words_absent_from_raw(self):
        screened = "alpha bravo charlie delta echo foxtrot golf hotel"
        raw = "nothing in common with the anchor words at all here today"
        assert _relocate_to_raw_text(screened, raw, radius=50) is None


# ---------------------------------------------------------------------------
# validate_verdicts
# ---------------------------------------------------------------------------

class TestValidateVerdicts:
    TARGETS = [{"language": "Swahili", "class": 0, "sections": []},
               {"language": "Ari", "class": 0, "sections": []}]

    def test_happy_path_and_canonicalization(self):
        parsed = {"verdicts": [
            {"language": "swahili", "verdict": "STUDIED", "reason": "r"},
            {"language": "Ari", "verdict": "false_positive", "reason": "r2"},
        ]}
        out = validate_verdicts(parsed, self.TARGETS)
        assert out["Swahili"]["verdict"] == "studied"
        assert out["Ari"]["verdict"] == "false_positive"

    def test_unknown_language_dropped(self):
        parsed = {"verdicts": [{"language": "Klingon", "verdict": "studied"}]}
        assert validate_verdicts(parsed, self.TARGETS) == {}

    def test_invalid_verdict_dropped(self):
        parsed = {"verdicts": [{"language": "Swahili", "verdict": "maybe"}]}
        assert validate_verdicts(parsed, self.TARGETS) == {}

    def test_reason_truncated(self):
        parsed = {"verdicts": [{"language": "Swahili", "verdict": "studied", "reason": "y" * 999}]}
        out = validate_verdicts(parsed, self.TARGETS)
        assert len(out["Swahili"]["reason"]) == 200

    def test_evidence_ignored_even_if_model_still_sends_it(self):
        # No "evidence" field is requested (privacy: no paper text in judge_cache),
        # but a model that doesn't perfectly follow the schema might send one anyway —
        # it must never be propagated into the stored verdict.
        parsed = {"verdicts": [{"language": "Swahili", "verdict": "studied",
                                "evidence": "a quoted line from the paper", "reason": "r"}]}
        out = validate_verdicts(parsed, self.TARGETS)
        assert "evidence" not in out["Swahili"]

    def test_dict_map_form_tolerated(self):
        parsed = {"verdicts": {"Swahili": {"verdict": "studied", "reason": "r"}}}
        assert validate_verdicts(parsed, self.TARGETS)["Swahili"]["verdict"] == "studied"


# ---------------------------------------------------------------------------
# judge_paper with a fake client
# ---------------------------------------------------------------------------

class FakeClient:
    """Returns queued canned replies; records the prompts it saw."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, response_format_json=True):
        self.calls.append(messages)
        return self.replies.pop(0)


def _reply_for(targets_verdicts: dict) -> str:
    return json.dumps({"verdicts": [
        {"language": lang, "verdict": verdict, "reason": "why"}
        for lang, verdict in targets_verdicts.items()
    ]})


class TestJudgePaper:
    def test_happy_path(self, record, week_dir):
        client = FakeClient([_reply_for({"Swahili": "studied", "Ari": "false_positive", "French": "mentioned_only"})])
        config = LLMClientConfig(model="fake-model")
        judge_record = judge_paper(record, week_dir, client, config)
        assert judge_record["judge_model"] == "fake-model"
        assert judge_record["verdicts"]["Swahili"]["verdict"] == "studied"
        assert judge_record["verdicts"]["Ari"]["verdict"] == "false_positive"
        assert judge_record["context_coverage"] == "abstract+html"
        assert len(client.calls) == 1

    def test_repair_round_trip(self, record, week_dir):
        client = FakeClient(["sorry, here you go:", _reply_for({"Swahili": "studied"})])
        judge_record = judge_paper(record, week_dir, client, LLMClientConfig())
        assert judge_record["verdicts"]["Swahili"]["verdict"] == "studied"
        assert len(client.calls) == 2
        assert "not valid JSON" in client.calls[1][-1]["content"]

    def test_double_failure_raises(self, record, week_dir):
        client = FakeClient(["garbage", "more garbage"])
        with pytest.raises(JSONParseError):
            judge_paper(record, week_dir, client, LLMClientConfig())

    def test_no_targets_returns_none(self, week_dir):
        empty = {"paper_id": "http://arxiv.org/abs/x1", "paper": {}, "sections": {}}
        assert judge_paper(empty, week_dir, FakeClient([]), LLMClientConfig()) is None

    def test_batches_over_12_languages(self, week_dir):
        record = {
            "paper_id": "http://arxiv.org/abs/2606.00002v1",
            "paper": {"title": "t", "abstract": "a"},
            "sections": {"abstract": {"source": "abstract", "detected_languages": [
                {"language": f"Lang{i}", "class": 0} for i in range(15)
            ]}},
        }
        client = FakeClient([
            _reply_for({f"Lang{i}": "mentioned_only" for i in range(15)}),
            _reply_for({f"Lang{i}": "mentioned_only" for i in range(15)}),
        ])
        judge_record = judge_paper(record, week_dir, client, LLMClientConfig())
        assert len(client.calls) == 2
        assert len(judge_record["verdicts"]) == 15


# ---------------------------------------------------------------------------
# Cache round-trip + pending detection
# ---------------------------------------------------------------------------

class TestCacheAndPending:
    def test_save_and_load_round_trip(self, week_dir, record):
        judge_record = {"paper_id": record["paper_id"], "judge_model": "m",
                        "judged_at": "t", "context_coverage": "abstract_only",
                        "context_chars": 1, "verdicts": {"Swahili": {"verdict": "studied", "reason": ""}}}
        path = save_judge_record(week_dir, judge_record)
        assert path.name == "2606.00001v1.json"
        cache = load_judge_cache(week_dir / "judge_cache")
        assert cache["2606.00001v1"]["verdicts"]["Swahili"]["verdict"] == "studied"

    def test_needs_judging(self, record):
        assert needs_judging(record, None) is True
        partial = {"verdicts": {"Swahili": {}, "Ari": {}}}  # French missing
        assert needs_judging(record, partial) is True
        full = {"verdicts": {"Swahili": {}, "Ari": {}, "French": {}}}
        assert needs_judging(record, full) is False

    def test_needs_judging_no_targets(self):
        assert needs_judging({"sections": {}}, None) is False

    def test_safe_paper_id(self):
        assert safe_paper_id("http://arxiv.org/abs/2605.25263v1") == "2605.25263v1"
        assert safe_paper_id("2605.25263v1") == "2605.25263v1"


# ---------------------------------------------------------------------------
# Manifest merge
# ---------------------------------------------------------------------------

class TestApplyJudge:
    def test_fields_land_on_languages_and_sections(self):
        flagged = [{
            "paper": {"id": "http://arxiv.org/abs/2606.00001v1"},
            "languages": [
                {"language": "Swahili", "class": 0, "sources": ["abstract"]},
                {"language": "Ari", "class": 0, "sources": ["html"], "needs_review": True},
            ],
            "sections": [
                {"name": "3Method", "source": "html", "detected_languages": [
                    {"language": "Swahili", "class": 0},
                    {"language": "Swahili", "class": 0},
                ]},
            ],
        }]
        cache = {"2606.00001v1": {
            "judge_model": "m", "judged_at": "t",
            "verdicts": {
                "Swahili": {"verdict": "studied", "reason": "r"},
                "Ari": {"verdict": "false_positive", "reason": "r2"},
            },
        }}
        updated = apply_judge_to_flagged(flagged, cache)
        assert updated == 2
        langs = {l["language"]: l for l in flagged[0]["languages"]}
        assert langs["Swahili"]["judge_verdict"] == "studied"
        assert langs["Ari"]["judge_verdict"] == "false_positive"
        assert langs["Ari"]["judge_model"] == "m"
        assert "judge_evidence" not in langs["Ari"]
        for entry in flagged[0]["sections"][0]["detected_languages"]:
            assert entry["judge_verdict"] == "studied"  # duplicates both updated

    def test_unjudged_paper_untouched(self):
        flagged = [{"paper": {"id": "http://arxiv.org/abs/9999.9v1"},
                    "languages": [{"language": "Swahili", "class": 0}], "sections": []}]
        assert apply_judge_to_flagged(flagged, {}) == 0
        assert "judge_verdict" not in flagged[0]["languages"][0]


# ---------------------------------------------------------------------------
# ensure_context_cache — JIT re-fetch for judge runs with no local caches
# (e.g. a fresh CI checkout on a different runner/day than process_papers.py)
# ---------------------------------------------------------------------------

class TestEnsureContextCache:
    def test_fetches_html_when_sources_checked_says_html(self, tmp_path):
        record = {
            "paper_id": "http://arxiv.org/abs/2606.00003v1",
            "paper": {"id": "http://arxiv.org/abs/2606.00003v1"},
            "sources_checked": ["abstract", "html"],
        }
        with patch("langtrend.html_processor.recheck_languages_from_html") as mock_html:
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())
        mock_html.assert_called_once()
        assert mock_html.call_args.kwargs["out_dir"] == tmp_path / "html_cache"

    def test_fetches_html_for_html_partial_source_too(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/x1", "paper": {}, "sources_checked": ["html_partial"]}
        with patch("langtrend.html_processor.recheck_languages_from_html") as mock_html:
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())
        mock_html.assert_called_once()

    def test_does_not_fetch_html_when_not_in_sources_checked(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/x1", "paper": {}, "sources_checked": ["abstract", "pdf"]}
        with patch("langtrend.html_processor.recheck_languages_from_html") as mock_html, \
             patch("langtrend.pdf_processor.download_pdf", return_value=None):
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())
        mock_html.assert_not_called()

    def test_html_fetch_failure_does_not_raise(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/x1", "paper": {}, "sources_checked": ["html"]}
        with patch("langtrend.html_processor.recheck_languages_from_html", side_effect=RuntimeError("boom")):
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())  # must not raise

    def test_pdf_refetch_skipped_if_cache_already_exists(self, tmp_path):
        safe_id = "2606.00004v1"
        pdf_cache_dir = tmp_path / "pdf_cache"
        pdf_cache_dir.mkdir()
        (pdf_cache_dir / f"{safe_id}.json").write_text("{}", encoding="utf-8")
        record = {"paper_id": f"http://arxiv.org/abs/{safe_id}", "paper": {"pdf_url": "http://x/y.pdf"},
                  "sources_checked": ["pdf"]}
        with patch("langtrend.pdf_processor.download_pdf") as mock_download:
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())
        mock_download.assert_not_called()

    def test_pdf_refetch_no_op_when_no_pdf_url(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/x1", "paper": {}, "sources_checked": ["pdf"]}
        with patch("langtrend.pdf_processor.download_pdf") as mock_download:
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())
        mock_download.assert_not_called()

    def test_pdf_refetch_failure_does_not_raise(self, tmp_path):
        record = {"paper_id": "http://arxiv.org/abs/x1", "paper": {"pdf_url": "http://x/y.pdf"},
                  "sources_checked": ["pdf"]}
        with patch("langtrend.pdf_processor.download_pdf", side_effect=RuntimeError("network down")):
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())  # must not raise

    def test_pdf_refetch_writes_cache_with_extracted_text_only(self, tmp_path):
        safe_id = "2606.00005v1"
        record = {"paper_id": f"http://arxiv.org/abs/{safe_id}",
                  "paper": {"pdf_url": "http://arxiv.org/pdf/2606.00005v1"},
                  "sources_checked": ["pdf"]}
        fake_pdf_path = tmp_path / "pdfs" / safe_id / f"{safe_id}.pdf"
        fake_pdf_path.parent.mkdir(parents=True)
        fake_pdf_path.write_bytes(b"%PDF-fake")

        with patch("langtrend.pdf_processor.download_pdf", return_value=fake_pdf_path), \
             patch("langtrend.pdf_processor.PDFProcessor") as mock_processor_cls:
            mock_processor = mock_processor_cls.return_value
            mock_processor.extract_text.return_value = ("raw body text about Hausa and Igbo", {})
            mock_processor.clean_text.return_value = "raw body text about Hausa and Igbo"
            ensure_context_cache(record, tmp_path, tmp_path / "pdfs", {}, set())

        cache_path = tmp_path / "pdf_cache" / f"{safe_id}.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert "Hausa" in cached["screened_text"]
        assert cached["detected_languages"] == []  # not recomputed by the JIT fetch
