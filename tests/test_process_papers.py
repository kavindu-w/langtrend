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

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [], False)):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=True
        )

    assert record["sources_checked"] == ["abstract"]
    assert {d["language"] for d in record["sections"]["abstract"]["detected_languages"]} == {"Arabic", "Swahili"}


def test_process_single_paper_records_html_confirmed_missing_marker(lang_classes, languages_to_ignore, cache_dirs):
    # A definitive 404 (arXiv has no HTML for this paper) must be recorded
    # permanently in sources_checked — not just the local html_cache/
    # sentinel — so _needs_retry doesn't re-flag this paper as pending
    # forever on a fresh checkout that lacks that gitignored cache file.
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1", "abstract": "We study Arabic data."}

    with patch("process_papers.recheck_languages_from_html", return_value=({}, False, [], True)):
        record = pp._process_single_paper(
            paper, lang_classes, languages_to_ignore, {}, pdf_dir, html_cache_dir, pdf_cache_dir, no_pdf=True
        )

    assert "html_confirmed_missing" in record["sources_checked"]
    assert "html" not in record["sources_checked"]


def test_process_single_paper_uses_html_when_complete_and_skips_pdf(lang_classes, languages_to_ignore, cache_dirs):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    paper = {"id": "http://arxiv.org/abs/1"}
    html_cache = {"Introduction": ["Arabic"], "Experiments": ["Swahili"]}

    with patch("process_papers.recheck_languages_from_html", return_value=(html_cache, True, [], False)), \
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

    with patch("process_papers.recheck_languages_from_html", return_value=({"Intro": ["Arabic"]}, True, conflicts, False)):
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

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [], False)), \
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

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [], False)), \
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

    with patch("process_papers.recheck_languages_from_html", return_value=(None, False, [], False)), \
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

    with patch("process_papers.recheck_languages_from_html", return_value=(partial_html, False, [], False)), \
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

    record = pp._reprocess_single_paper(paper, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir)

    updated = json.loads((html_cache_dir / "2501.00010.json").read_text(encoding="utf-8"))
    assert updated.get("_unavailable") is True
    # The confirmed-404 fact must also land in the committed sources_checked
    # field, not just the local (gitignored) cache sentinel — otherwise
    # _needs_retry re-flags this paper as pending on every fresh CI checkout.
    assert "html_confirmed_missing" in record["sources_checked"]


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


def test_reprocess_from_cache_output_is_sorted_by_paper_id_regardless_of_completion_order(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    # ThreadPoolExecutor completion order is nondeterministic run-to-run, so streaming
    # records out in completion order (the old behavior) would jumble detected.jsonl
    # even when the underlying detections never changed. Sorting by paper_id at write
    # time keeps output stable/diff-free across reruns of identical input.
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    ids = ["3", "1", "4", "1a", "2"]
    papers = [{"id": pid} for pid in ids]
    records_by_id = {
        pid: _record(pid, sections={"abstract": {"source": "abstract", "detected_languages": [{"language": "Swahili", "class": 0}]}}, sources=["abstract"])
        for pid in ids
    }

    def fake_reprocess(paper, *a, **kw):
        return records_by_id[paper["id"]]

    with patch("process_papers._reprocess_single_paper", side_effect=fake_reprocess):
        pp.reprocess_from_cache(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=tmp_path / "detected.jsonl",
            warnings_file=tmp_path / "warnings.json",
            html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            no_detections_file=tmp_path / "no_detections.json",
            max_workers=1,  # single worker keeps future submission order == completion order
        )

    lines = (tmp_path / "detected.jsonl").read_text(encoding="utf-8").splitlines()
    written_ids = [json.loads(l)["paper_id"] for l in lines]
    assert written_ids == sorted(ids)


# Regression guards for a real bug (fixed twice already): docling, when invoked
# from one of these executors' worker threads (process_papers()'s ordinary PDF
# fallback, or reprocess_from_cache()'s force_pdf_reextract), can SIGSEGV during
# native cleanup shortly after ThreadPoolExecutor.shutdown() tears the pool down.
# That crash bypasses Python's exception handling entirely, so the only real
# protection is ensuring output files are written to disk *before* shutdown() is
# called, not after. These assert exactly that ordering by inspecting the
# filesystem from inside a mocked shutdown() — if a future edit hoists shutdown()
# back above the writes, these must fail.

def test_process_papers_writes_output_before_shutting_down_executor(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    pdf_dir, html_cache_dir, pdf_cache_dir = cache_dirs
    no_det_path = tmp_path / "no_detections.json"
    papers = [{"id": "1"}]
    record = _record("1", sources=["abstract"])  # no detections -> exercises the no_detections_file write

    shutdown_saw_file_written = []
    original_shutdown = pp.ThreadPoolExecutor.shutdown

    def fake_shutdown(self, *a, **kw):
        shutdown_saw_file_written.append(no_det_path.exists())
        return original_shutdown(self, *a, **kw)

    with patch("process_papers._process_single_paper", return_value=record), \
         patch.object(pp.ThreadPoolExecutor, "shutdown", fake_shutdown):
        pp.process_papers(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=tmp_path / "detected.jsonl",
            warnings_file=tmp_path / "warnings.json",
            pdf_dir=pdf_dir, html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            no_detections_file=no_det_path,
            max_workers=1, no_pdf=True,
        )

    assert shutdown_saw_file_written == [True]


def test_reprocess_from_cache_writes_output_before_shutting_down_executor(lang_classes, languages_to_ignore, cache_dirs, tmp_path):
    _, html_cache_dir, pdf_cache_dir = cache_dirs
    detected_path = tmp_path / "detected.jsonl"
    papers = [{"id": "1"}]
    record = _record("1", sections={"abstract": {"source": "abstract", "detected_languages": [{"language": "Swahili", "class": 0}]}}, sources=["abstract"])

    shutdown_saw_file_written = []
    original_shutdown = pp.ThreadPoolExecutor.shutdown

    def fake_shutdown(self, *a, **kw):
        shutdown_saw_file_written.append(detected_path.exists() and detected_path.stat().st_size > 0)
        return original_shutdown(self, *a, **kw)

    with patch("process_papers._reprocess_single_paper", return_value=record), \
         patch.object(pp.ThreadPoolExecutor, "shutdown", fake_shutdown):
        pp.reprocess_from_cache(
            papers, lang_classes, languages_to_ignore, {},
            output_jsonl=detected_path,
            warnings_file=tmp_path / "warnings.json",
            html_cache_dir=html_cache_dir, pdf_cache_dir=pdf_cache_dir,
            no_detections_file=tmp_path / "no_detections.json",
            max_workers=1,
        )

    assert shutdown_saw_file_written == [True]


def test_atomic_write_text_replaces_content(tmp_path):
    path = tmp_path / "out.json"
    pp._atomic_write_text(path, "old")
    pp._atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    # no leftover temp file
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_text_leaves_destination_untouched_on_mid_write_failure(tmp_path):
    path = tmp_path / "out.json"
    path.write_text("original", encoding="utf-8")

    with patch("process_papers.os.fsync", side_effect=OSError("simulated crash mid-write")):
        with pytest.raises(OSError):
            pp._atomic_write_text(path, "corrupted-partial-content")

    # The destination must still hold its old, complete content — never a
    # truncated mix of old and new (this is the exact failure mode a SIGSEGV
    # mid `path.open("w")` used to produce: real data silently dropped).
    assert path.read_text(encoding="utf-8") == "original"
    # temp file cleaned up, not left orphaned next to the real file
    assert list(tmp_path.iterdir()) == [path]


def test_plain_run_would_clobber_existing_output_true_when_populated(tmp_path):
    path = tmp_path / "detected.jsonl"
    path.write_text('{"paper_id": "1"}\n', encoding="utf-8")
    assert pp._plain_run_would_clobber_existing_output(path) is True


def test_plain_run_would_clobber_existing_output_false_when_missing(tmp_path):
    assert pp._plain_run_would_clobber_existing_output(tmp_path / "detected.jsonl") is False


def test_plain_run_would_clobber_existing_output_false_when_empty(tmp_path):
    path = tmp_path / "detected.jsonl"
    path.write_text("", encoding="utf-8")
    assert pp._plain_run_would_clobber_existing_output(path) is False


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


# Regression tests for the "html_confirmed_missing" fix: on a fresh CI
# checkout, cached_html_ids is always empty (the sentinel lives in a
# gitignored html_cache/ dir) — before this fix, a paper whose HTML was
# permanently 404'd and which resolved via PDF was flagged as "needs retry"
# forever, since sources_checked never distinguished "confirmed absent" from
# "never attempted." These pin down that it no longer does, for both the
# no-detections and has-detections branches.

def test_needs_retry_false_when_no_detections_html_confirmed_missing_pdf_succeeded_fresh_checkout():
    paper = {"id": "1"}
    no_det_sources = {"1": ["abstract", "html_confirmed_missing", "pdf"]}
    # cached_html_ids=set() simulates a fresh checkout with no local html_cache/ —
    # the exact scenario that caused the CI infinite-retry bug.
    assert pp._needs_retry(paper, {}, no_det_sources, set(), set(), Path("/nonexistent")) is False


def test_needs_retry_false_when_detected_html_confirmed_missing_pdf_succeeded_fresh_checkout():
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "html_confirmed_missing", "pdf"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), set(), Path("/nonexistent")) is False


def test_needs_retry_true_when_html_confirmed_missing_but_pdf_never_attempted():
    # html_confirmed_missing only settles the HTML question — PDF still
    # needs its own real attempt if it hasn't had one yet.
    paper = {"id": "1"}
    detected_sources = {"1": ["abstract", "html_confirmed_missing"]}
    assert pp._needs_retry(paper, detected_sources, {}, set(), set(), Path("/nonexistent")) is True


# ---------------------------------------------------------------------------
# _merge_retry_no_detection_record / _merge_retry_detected_record
#
# Regression coverage for a second bug found alongside the html_confirmed_missing
# one: --retry-missing rebuilds a flagged paper's record entirely from scratch,
# so a retry pass that runs with fewer capabilities than the original run (e.g.
# --no-pdf, which the CI fetch-and-html-2 job always uses) produces a record
# that's missing sources the paper already had. Before this fix, "last write
# wins" merging let that weaker record silently overwrite the already-confirmed
# one — observed for real on paper 2607.16021v1: sources_checked regressed from
# ['abstract', 'pdf'] to ['abstract'] mid-run before a later job restored it.
# ---------------------------------------------------------------------------

def test_merge_no_detection_record_keeps_pdf_source_dropped_by_a_no_pdf_retry():
    # This is the exact real-world shape: last week's committed record vs. what
    # a --no-pdf retry pass rebuilds from scratch for the same paper.
    old = {"paper_id": "2607.16021v1", "title": "T", "sources_checked": ["abstract", "pdf"], "warnings": []}
    new = {"paper_id": "2607.16021v1", "title": "T", "sources_checked": ["abstract"], "warnings": []}
    merged = pp._merge_retry_no_detection_record(old, new)
    assert merged["sources_checked"] == ["abstract", "pdf"]


def test_merge_no_detection_record_adopts_genuine_upgrades_from_new():
    # The merge must still work the other direction — a retry that finds MORE
    # than before (e.g. html newly resolved) should keep that upgrade.
    old = {"paper_id": "1", "sources_checked": ["abstract"], "warnings": []}
    new = {"paper_id": "1", "sources_checked": ["abstract", "html"], "warnings": []}
    merged = pp._merge_retry_no_detection_record(old, new)
    assert merged["sources_checked"] == ["abstract", "html"]


def test_merge_no_detection_record_unions_warnings_without_duplicating():
    old = {"paper_id": "1", "sources_checked": ["abstract"], "warnings": [{"step": "html", "error": "e1"}]}
    new = {"paper_id": "1", "sources_checked": ["abstract"], "warnings": [{"step": "html", "error": "e1"}, {"step": "pdf", "error": "e2"}]}
    merged = pp._merge_retry_no_detection_record(old, new)
    assert merged["warnings"] == [{"step": "html", "error": "e1"}, {"step": "pdf", "error": "e2"}]


def test_merge_detected_record_keeps_pdf_section_dropped_by_a_no_pdf_retry():
    old = {
        "paper_id": "1",
        "sources_checked": ["abstract", "pdf"],
        "sections": {"pdf_full_text": {"source": "pdf", "detected_languages": [{"language": "Chinese", "class": 5}]}},
        "warnings": [],
    }
    new = {
        "paper_id": "1",
        "sources_checked": ["abstract"],
        "sections": {},
        "warnings": [],
    }
    merged = pp._merge_retry_detected_record(old, new)
    assert merged["sources_checked"] == ["abstract", "pdf"]
    assert merged["sections"]["pdf_full_text"]["detected_languages"] == [{"language": "Chinese", "class": 5}]


def test_merge_detected_record_prefers_new_section_when_source_was_rechecked():
    # If the new pass DID re-check a source, its (fresher) result wins over the
    # old one for that source — the merge only backfills sources the new pass
    # never touched, it doesn't prefer stale data.
    old = {
        "paper_id": "1",
        "sources_checked": ["abstract", "html"],
        "sections": {"Intro": {"source": "html", "detected_languages": [{"language": "Old", "class": 0}]}},
        "warnings": [],
    }
    new = {
        "paper_id": "1",
        "sources_checked": ["abstract", "html"],
        "sections": {"Intro": {"source": "html", "detected_languages": [{"language": "New", "class": 0}]}},
        "warnings": [],
    }
    merged = pp._merge_retry_detected_record(old, new)
    assert merged["sections"]["Intro"]["detected_languages"] == [{"language": "New", "class": 0}]


def test_merge_detected_record_keeps_multiple_dropped_sections():
    old = {
        "paper_id": "1",
        "sources_checked": ["abstract", "html", "pdf"],
        "sections": {
            "Intro": {"source": "html", "detected_languages": [{"language": "Swahili", "class": 0}]},
            "pdf_full_text": {"source": "pdf", "detected_languages": [{"language": "Arabic", "class": 3}]},
        },
        "warnings": [],
    }
    new = {"paper_id": "1", "sources_checked": ["abstract"], "sections": {}, "warnings": []}
    merged = pp._merge_retry_detected_record(old, new)
    assert set(merged["sources_checked"]) == {"abstract", "html", "pdf"}
    assert "Intro" in merged["sections"]
    assert "pdf_full_text" in merged["sections"]


# ---------------------------------------------------------------------------
# main() --retry-missing CLI integration test
#
# The merge helpers above are covered in isolation, but the actual file-
# rewriting wiring in main() (loading existing records, running
# process_papers(), deduping/merging into detected.jsonl and
# no_detections.json) was previously only exercised manually. This drives it
# end-to-end through the real CLI entry point, reproducing the exact bug
# scenario found on paper 2607.16021v1: a --no-pdf --retry-missing pass must
# not erase an already-committed 'pdf' result.
# ---------------------------------------------------------------------------

def test_retry_missing_cli_preserves_pdf_source_through_no_pdf_pass(tmp_path, monkeypatch):
    input_path = tmp_path / "week_input.jsonl"
    paper = {
        "id": "http://arxiv.org/abs/2607.16021v1",
        "title": "Candidate Attended Dialogue State Tracking Using BERT",
        "abstract": "We study BERT for dialogue state tracking.",
    }
    input_path.write_text(json.dumps(paper) + "\n", encoding="utf-8")

    lang_data_path = tmp_path / "language_data.json"
    lang_data_path.write_text(json.dumps({
        "lang_classes": {"0": ["Swahili"]},
        "languages_to_ignore": [],
        "possible_false_positive_languages": {},
    }), encoding="utf-8")

    output_dir = tmp_path / "week_output"
    output_dir.mkdir()
    # Pre-seed the committed state exactly as it was on main before this run:
    # already resolved, HTML confirmed missing (but not yet recorded as such —
    # this is data from before the html_confirmed_missing fix), PDF checked,
    # nothing detected.
    no_det_path = output_dir / "week_input_no_detections.json"
    no_det_path.write_text(json.dumps([{
        "paper_id": "http://arxiv.org/abs/2607.16021v1",
        "title": paper["title"],
        "sources_checked": ["abstract", "pdf"],
        "warnings": [],
    }]), encoding="utf-8")

    # Simulate exactly what a --no-pdf CI job step (fetch-and-html-2) sees:
    # HTML confirmed missing, no local cache to prove it (fresh checkout).
    monkeypatch.setattr(pp, "recheck_languages_from_html", lambda *a, **kw: ({}, False, [], True))

    monkeypatch.setattr(sys, "argv", [
        "process_papers.py",
        "--input", str(input_path),
        "--language-data", str(lang_data_path),
        "--output-dir", str(output_dir),
        "--workers", "1",
        "--retry-missing",
        "--no-pdf",
    ])

    # main() only calls sys.exit() on error or on the "nothing to do" early
    # exit (empty subset) — neither applies here: the pre-seeded record lacks
    # html_confirmed_missing, so _needs_retry correctly still flags this paper
    # (matching the real bug — old data predates the fix), giving a non-empty
    # subset that runs to completion and returns normally.
    pp.main()

    result = json.loads(no_det_path.read_text(encoding="utf-8"))
    assert len(result) == 1
    sources_checked = set(result[0]["sources_checked"])
    # The regression this guards against: a naive "last write wins" merge
    # would leave this as just {"abstract", "html_confirmed_missing"} — the
    # already-committed 'pdf' result silently erased because the --no-pdf
    # pass never re-checked it.
    assert sources_checked == {"abstract", "pdf", "html_confirmed_missing"}
