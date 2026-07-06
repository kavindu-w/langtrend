"""
Unit tests for the per-paper worker functions in scripts/process_papers.py.

These cover the abstract -> HTML -> PDF fallback chain (`_process_single_paper`)
and the cache-only reprocess path (`_reprocess_single_paper`). Network and PDF
extraction are mocked; PDFProcessor.clean_text is exercised for real since it's
pure string cleaning (no docling model load — only .extract_text() touches docling).

Run with: pytest tests/test_process_papers.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT))

import process_papers as pp
from langtrend import pdf_processor


@pytest.fixture
def lang_classes():
    return {0: {"Swahili", "Arabic"}, 1: {"Sinhala"}, 2: {"Gan"}}


@pytest.fixture
def languages_to_ignore():
    return {"The", "To"}


@pytest.fixture
def cache_dirs(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    html_cache_dir = tmp_path / "html_cache"
    pdf_cache_dir = tmp_path / "pdf_cache"
    pdf_dir.mkdir()
    html_cache_dir.mkdir()
    pdf_cache_dir.mkdir()
    return pdf_dir, html_cache_dir, pdf_cache_dir


# ---------------------------------------------------------------------------
# _detect_in_text
# ---------------------------------------------------------------------------

def test_detect_in_text_finds_tracked_languages(lang_classes, languages_to_ignore):
    dets = pp._detect_in_text("We evaluate on Arabic and Swahili benchmarks.", lang_classes, languages_to_ignore, "1")
    assert {d["language"] for d in dets} == {"Arabic", "Swahili"}


def test_detect_in_text_returns_empty_for_blank_text(lang_classes, languages_to_ignore):
    assert pp._detect_in_text("", lang_classes, languages_to_ignore, "1") == []


# ---------------------------------------------------------------------------
# _download_pdf
# ---------------------------------------------------------------------------

def _mock_pdf_response(chunks):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_content.return_value = iter(chunks)
    return resp


@patch("time.sleep")
def test_download_pdf_returns_existing_file_without_hitting_network(mock_sleep, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    paper_pdf_dir = pdf_dir / "2501.00001"
    paper_pdf_dir.mkdir(parents=True)
    existing = paper_pdf_dir / "2501.00001.pdf"
    existing.write_bytes(b"%PDF-existing")

    with patch("langtrend.pdf_processor.requests.get") as mock_get:
        result = pp._download_pdf("http://arxiv.org/pdf/2501.00001", pdf_dir, "2501.00001")

    assert result == existing
    mock_get.assert_not_called()
    mock_sleep.assert_not_called()


@patch("time.sleep")
def test_download_pdf_writes_streamed_chunks_to_disk(mock_sleep, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    resp = _mock_pdf_response([b"chunk1", b"chunk2"])

    with patch("langtrend.pdf_processor.requests.get", return_value=resp):
        result = pp._download_pdf("http://arxiv.org/pdf/1", pdf_dir, "1")

    assert result == pdf_dir / "1" / "1.pdf"
    assert result.read_bytes() == b"chunk1chunk2"


@patch("time.sleep")
def test_download_pdf_retries_after_a_failed_attempt_then_succeeds(mock_sleep, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ok_resp = _mock_pdf_response([b"data"])

    with patch("langtrend.pdf_processor.requests.get", side_effect=[requests.ConnectionError("network down"), ok_resp]):
        result = pp._download_pdf("http://arxiv.org/pdf/1", pdf_dir, "1")

    assert result is not None
    assert result.read_bytes() == b"data"


@patch("time.sleep")
def test_download_pdf_returns_none_after_all_retries_fail(mock_sleep, tmp_path):
    pdf_dir = tmp_path / "pdfs"

    with patch("langtrend.pdf_processor.requests.get", side_effect=requests.ConnectionError("network down")):
        result = pp._download_pdf("http://arxiv.org/pdf/1", pdf_dir, "1")

    assert result is None
    assert not (pdf_dir / "1" / "1.pdf").exists()


@patch("time.sleep")
def test_download_pdf_raises_http_error_treated_as_failed_attempt(mock_sleep, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = requests.HTTPError("404")

    with patch("langtrend.pdf_processor.requests.get", return_value=bad_resp):
        result = pp._download_pdf("http://arxiv.org/pdf/1", pdf_dir, "1")

    assert result is None


# ---------------------------------------------------------------------------
# _process_single_paper — abstract + HTML
# ---------------------------------------------------------------------------

def test_process_single_paper_abstract_only_when_html_unavailable_and_no_pdf(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1", "abstract": "We study Arabic and Swahili data."}

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [])):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=True
        )

    assert record["sources_checked"] == ["abstract"]
    assert {d["language"] for d in record["sections"]["abstract"]["detected_languages"]} == {"Arabic", "Swahili"}


def test_process_single_paper_uses_html_when_complete_and_skips_pdf(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1"}
    html_cache = {"Introduction": ["Arabic"], "Experiments": ["Swahili"]}

    with patch("process_papers.recheck_languages_from_html", return_value=(html_cache, True, [])), \
         patch("process_papers._download_pdf") as mock_download:
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    mock_download.assert_not_called()
    assert record["sources_checked"] == ["html"]
    assert set(record["sections"].keys()) == {"Introduction", "Experiments"}


def test_process_single_paper_records_acronym_conflict_warnings(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1"}
    conflicts = [{"acronym": "GAN", "language": "Gan", "class": 2}]

    with patch("process_papers.recheck_languages_from_html", return_value=({"Intro": ["Arabic"]}, True, conflicts)):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=True
        )

    assert record["warnings"] == [{
        "step": "acronym_language_conflict",
        "acronym": "GAN",
        "language": "Gan",
        "language_class": 2,
        "message": (
            "Paper defines 'GAN' as an acronym. "
            "The language 'Gan' (class 2) "
            "shares this name — mentions may have been suppressed. Manual review recommended."
        ),
    }]


def test_process_single_paper_falls_back_to_pdf_when_html_raises(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1"}  # no pdf_url either

    with patch("process_papers.recheck_languages_from_html", side_effect=RuntimeError("boom")):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    assert {"step": "html", "error": "boom"} in record["warnings"]
    assert "pdf_unavailable" in record["sources_checked"]
    assert {"step": "pdf", "error": "No PDF URL available"} in record["warnings"]


# ---------------------------------------------------------------------------
# _process_single_paper — PDF fallback
# ---------------------------------------------------------------------------

def test_process_single_paper_uses_pdf_cache_hit_without_downloading(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00001", "pdf_url": "http://arxiv.org/pdf/2501.00001"}
    (pdf_cache_dir / "2501.00001.json").write_text(
        json.dumps({"detected_languages": [{"language": "Sinhala", "class": 1}]}), encoding="utf-8"
    )

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [])), \
         patch("process_papers._download_pdf") as mock_download:
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    mock_download.assert_not_called()
    assert "pdf" in record["sources_checked"]
    assert record["sections"]["pdf_full_text"]["detected_languages"] == [{"language": "Sinhala", "class": 1}]


def test_process_single_paper_downloads_and_extracts_pdf_on_success(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00002", "pdf_url": "http://arxiv.org/pdf/2501.00002"}
    fake_pdf_path = tmp_path / "fake.pdf"
    fake_pdf_path.write_bytes(b"%PDF-fake")

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [])), \
         patch("process_papers._download_pdf", return_value=fake_pdf_path), \
         patch("process_papers.PDFProcessor") as MockProcessor:
        instance = MockProcessor.return_value
        instance.extract_text.return_value = ("Body text mentioning Arabic and Swahili research.", {})
        instance.clean_text.return_value = "Body text mentioning Arabic and Swahili research."

        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    assert "pdf" in record["sources_checked"]
    detected_langs = {d["language"] for d in record["sections"]["pdf_full_text"]["detected_languages"]}
    assert detected_langs == {"Arabic", "Swahili"}

    cache_file = pdf_cache_dir / "2501.00002.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached["text"] == "Body text mentioning Arabic and Swahili research."


def test_process_single_paper_records_warning_when_pdf_download_fails(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00003", "pdf_url": "http://arxiv.org/pdf/2501.00003"}

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [])), \
         patch("process_papers._download_pdf", return_value=None):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    assert "pdf_unavailable" in record["sources_checked"]
    assert {"step": "pdf_download", "error": "Failed to download PDF from http://arxiv.org/pdf/2501.00003"} in record["warnings"]


def test_process_single_paper_uses_partial_html_when_pdf_also_fails(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00004", "pdf_url": "http://arxiv.org/pdf/2501.00004"}
    partial_html = {"Introduction": ["Arabic"]}

    with patch("process_papers.recheck_languages_from_html", return_value=(partial_html, False, [])), \
         patch("process_papers._download_pdf", return_value=None):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=False
        )

    assert "html_partial" in record["sources_checked"]
    assert record["sections"]["Introduction"]["source"] == "html_partial"
    assert any(w["step"] == "html_partial" for w in record["warnings"])


# ---------------------------------------------------------------------------
# _reprocess_single_paper
# ---------------------------------------------------------------------------

def test_reprocess_single_paper_rescans_abstract(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1", "abstract": "We study Arabic data."}

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert record["sources_checked"] == ["abstract"]
    assert {d["language"] for d in record["sections"]["abstract"]["detected_languages"]} == {"Arabic"}


def test_reprocess_single_paper_rebuilds_detections_from_complete_html_cache(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00005"}
    (html_cache_dir / "2501.00005.json").write_text(json.dumps({
        "_complete": True,
        "Introduction": {"text": "We evaluate on Arabic and Swahili benchmarks."},
    }), encoding="utf-8")

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert "html" in record["sources_checked"]
    detected = {d["language"] for d in record["sections"]["Introduction"]["detected_languages"]}
    assert detected == {"Arabic", "Swahili"}

    # The html cache file itself is rewritten with fresh "detected"/"cleaned_text" fields.
    updated = json.loads((html_cache_dir / "2501.00005.json").read_text(encoding="utf-8"))
    assert "Arabic" in updated["Introduction"]["detected"]


def test_reprocess_single_paper_does_not_count_incomplete_html_as_a_source(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00006"}
    (html_cache_dir / "2501.00006.json").write_text(json.dumps({
        "_complete": False,
        "Introduction": {"text": "We evaluate on Arabic benchmarks."},
    }), encoding="utf-8")

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert "html" not in record["sources_checked"]
    assert {"step": "reprocess", "error": "HTML cache incomplete, no PDF cache available — skipped"} in record["warnings"]


def test_reprocess_single_paper_preserves_unavailable_marker(lang_classes, languages_to_ignore, cache_dirs):
    # A paper confirmed to have no HTML version at all (_unavailable, written by
    # recheck_languages_from_html after a 404) must keep that marker through a
    # reprocess-cache run — otherwise a later --retry-missing would pointlessly
    # re-attempt a fetch already confirmed to fail.
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00010"}
    (html_cache_dir / "2501.00010.json").write_text(
        json.dumps({"_complete": False, "_unavailable": True}), encoding="utf-8"
    )

    pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    updated = json.loads((html_cache_dir / "2501.00010.json").read_text(encoding="utf-8"))
    assert updated.get("_unavailable") is True


def test_reprocess_single_paper_recomputes_detections_from_pdf_cache_text(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00007"}
    (pdf_cache_dir / "2501.00007.json").write_text(json.dumps({
        "text": "Full body text mentioning Swahili research methods.",
    }), encoding="utf-8")

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert "pdf" in record["sources_checked"]
    detected = {d["language"] for d in record["sections"]["pdf_full_text"]["detected_languages"]}
    assert detected == {"Swahili"}

    updated = json.loads((pdf_cache_dir / "2501.00007.json").read_text(encoding="utf-8"))
    assert "detected_languages" in updated
    assert "body_text" in updated


def test_reprocess_single_paper_uses_precomputed_pdf_detections_when_no_raw_text(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00008"}
    (pdf_cache_dir / "2501.00008.json").write_text(json.dumps({
        "detected_languages": [{"language": "Sinhala", "class": 1}],
    }), encoding="utf-8")

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert record["sections"]["pdf_full_text"]["detected_languages"] == [{"language": "Sinhala", "class": 1}]


def test_reprocess_single_paper_warns_when_no_cache_exists_at_all(lang_classes, languages_to_ignore, cache_dirs):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/2501.00009"}

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    assert record["sources_checked"] == []
    assert {"step": "reprocess", "error": "No HTML or PDF cache found — skipped"} in record["warnings"]


# ---------------------------------------------------------------------------
# process_papers / reprocess_from_cache — ThreadPoolExecutor orchestration
#
# These mock the per-paper worker function itself (already unit-tested above)
# so what's actually under test is the executor loop: result aggregation,
# stats counting, and the three output files (detected.jsonl, warnings.json,
# no_detections.json) — not the abstract/HTML/PDF decision tree again.
# ---------------------------------------------------------------------------

def _record(paper_id, sections=None, warnings=None, sources=None):
    return {
        "paper_id": paper_id,
        "paper": {"id": paper_id, "title": f"Title {paper_id}"},
        "sources_checked": sources or [],
        "sections": sections or {},
        "warnings": warnings or [],
    }


def test_process_papers_writes_detected_and_no_detection_outputs(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    papers = [{"id": "1"}, {"id": "2"}]
    records_by_id = {
        "1": _record("1", sections={"abstract": {"source": "abstract", "detected_languages": [{"language": "Arabic", "class": 0}]}}, sources=["abstract"]),
        "2": _record("2", sources=["abstract"]),  # no detections
    }

    with patch("process_papers._process_single_paper", side_effect=lambda paper, *a, **kw: records_by_id[paper["id"]]):
        stats = pp.process_papers(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=tmp_path / "detected.jsonl",
            warnings_file=tmp_path / "warnings.json",
            pdf_dir=pdf_dir, html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            no_detections_file=tmp_path / "no_detections.json",
            max_workers=2, no_pdf=True,
        )

    assert stats["total_papers"] == 2
    assert stats["papers_with_detections"] == 1
    assert stats["total_detections"] == 1

    detected_lines = (tmp_path / "detected.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(detected_lines) == 1
    assert json.loads(detected_lines[0])["paper_id"] == "1"

    no_detections = json.loads((tmp_path / "no_detections.json").read_text(encoding="utf-8"))
    assert [r["paper_id"] for r in no_detections] == ["2"]


def test_process_papers_records_worker_exceptions_as_warnings(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    papers = [{"id": "1"}]

    with patch("process_papers._process_single_paper", side_effect=RuntimeError("worker crashed")):
        stats = pp.process_papers(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=tmp_path / "detected.jsonl",
            warnings_file=tmp_path / "warnings.json",
            pdf_dir=pdf_dir, html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            max_workers=1, no_pdf=True,
        )

    assert stats["failed_papers"] == 1
    warnings = json.loads((tmp_path / "warnings.json").read_text(encoding="utf-8"))
    assert warnings[0]["paper_id"] == "1"
    assert warnings[0]["error"] == "worker crashed"


def test_reprocess_from_cache_writes_detected_and_no_detection_outputs(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    papers = [{"id": "1"}, {"id": "2"}]
    records_by_id = {
        "1": _record("1", sections={"abstract": {"source": "abstract", "detected_languages": [{"language": "Swahili", "class": 0}]}}, sources=["abstract"]),
        "2": _record("2", sources=["abstract"]),
    }

    with patch("process_papers._reprocess_single_paper", side_effect=lambda paper, *a, **kw: records_by_id[paper["id"]]):
        stats = pp.reprocess_from_cache(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=tmp_path / "detected.jsonl",
            warnings_file=tmp_path / "warnings.json",
            html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            no_detections_file=tmp_path / "no_detections.json",
            max_workers=2,
        )

    assert stats["papers_with_detections"] == 1
    assert stats["total_detections"] == 1
    no_detections = json.loads((tmp_path / "no_detections.json").read_text(encoding="utf-8"))
    assert [r["paper_id"] for r in no_detections] == ["2"]


# ---------------------------------------------------------------------------
# _needs_retry (--retry-missing decision logic)
# ---------------------------------------------------------------------------

def test_needs_retry_true_for_a_paper_never_processed():
    paper = {"id": "1"}
    assert pp._needs_retry(paper, {}, {}, set(), set(), Path("/nonexistent")) is True


def test_needs_retry_false_when_no_detections_already_tried_html_or_pdf():
    paper = {"id": "1"}
    no_det_sources = {"1": ["abstract", "html"]}
    assert pp._needs_retry(paper, {}, no_det_sources, set(), set(), Path("/nonexistent")) is False


def test_needs_retry_true_when_no_detections_was_abstract_only():
    paper = {"id": "1"}
    no_det_sources = {"1": ["abstract"]}
    assert pp._needs_retry(paper, {}, no_det_sources, set(), set(), Path("/nonexistent")) is True


def test_needs_retry_true_when_no_detections_pdf_only_and_no_html_cache():
    # Same gap as the detected-paper branch: a "no detections" verdict from
    # pdf alone doesn't mean html ever got a real attempt — a transient
    # failure leaves no cache file at all.
    paper = {"id": "1"}
    no_det_sources = {"1": ["abstract", "pdf"]}
    assert pp._needs_retry(paper, {}, no_det_sources, set(), set(), Path("/nonexistent")) is True


def test_needs_retry_false_when_no_detections_pdf_only_but_html_cache_exists():
    paper = {"id": "1"}
    no_det_sources = {"1": ["abstract", "pdf"]}
    assert pp._needs_retry(paper, {}, no_det_sources, {"1"}, set(), Path("/nonexistent")) is False


def test_needs_retry_true_when_pdf_cache_exists_but_detection_never_recorded_it():
    # Looks like a crash mid-run: pdf_cache/<id>.json exists, but the detected
    # record's sources_checked never got "pdf" (or "html") added.
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), {"1"}, Path("/nonexistent")) is True


def test_needs_retry_true_when_no_cache_at_all_and_abstract_only_detection():
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), set(), Path("/nonexistent")) is True


def test_needs_retry_false_when_no_cache_but_html_or_pdf_already_recorded():
    # Cache may simply be gitignored/absent on a fresh checkout even though the
    # paper was already fully processed — must not be flagged for retry.
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "html"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), set(), Path("/nonexistent")) is False


def test_needs_retry_true_when_html_cache_incomplete_and_detection_abstract_only(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps({"_complete": False}), encoding="utf-8")
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract"]}
    assert pp._needs_retry(paper, detected_sources, {}, {"1"}, set(), tmp_path) is True


def test_needs_retry_false_when_html_cache_complete_and_detection_abstract_only(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps({"_complete": True}), encoding="utf-8")
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract"]}
    assert pp._needs_retry(paper, detected_sources, {}, {"1"}, set(), tmp_path) is False


def test_needs_retry_false_when_html_already_recorded():
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "html"]}
    assert pp._needs_retry(paper, detected_sources, {}, {"1"}, {"1"}, Path("/nonexistent")) is False


def test_needs_retry_false_for_confirmed_404_unavailable_sentinel(tmp_path):
    # _unavailable is a permanent "arXiv has no HTML for this paper" marker —
    # retrying would just waste a request re-confirming the same 404.
    (tmp_path / "1.json").write_text(json.dumps({"_complete": False, "_unavailable": True}), encoding="utf-8")
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "pdf"]}
    assert pp._needs_retry(paper, detected_sources, {}, {"1"}, {"1"}, tmp_path) is False


def test_needs_retry_true_when_pdf_succeeded_but_html_never_cached():
    # A transient failure (429/5xx/timeout) deliberately leaves no HTML cache
    # file at all (see fetch_arxiv_html) — PDF succeeding as a fallback
    # shouldn't permanently block HTML from ever getting a real retry.
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "pdf"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), {"1"}, Path("/nonexistent")) is True
