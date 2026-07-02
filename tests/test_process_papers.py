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
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import process_papers as pp


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
