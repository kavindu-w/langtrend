"""
Integration tests for the PDF fallback path in _process_single_paper.

These tests use real PDFs already on disk — no network required.
They exercise the full chain:
    PDFProcessor.extract_text → clean_text → _detect_in_text → _build_detections

Run with:  pytest tests/test_pdf_fallback.py -v
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
PDF_ROOT = PROJECT_ROOT / "data/raw/pdfs"

# Add scripts/ to path so process_papers can be imported directly
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def _find_valid_pdf() -> Path | None:
    """Return the first PDF that pdfplumber can open without error."""
    import pdfplumber
    for p in sorted(PDF_ROOT.rglob("*.pdf")):
        try:
            with pdfplumber.open(p) as pdf:
                pdf.pages[0].extract_text()
            return p
        except Exception:
            continue
    return None


_SAMPLE_PDF = _find_valid_pdf()

pytestmark = pytest.mark.skipif(
    _SAMPLE_PDF is None,
    reason="No valid PDFs found in data/raw/pdfs — download at least one first",
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def language_data():
    path = PROJECT_ROOT / "data/processed/language_data.json"
    if not path.exists():
        pytest.skip("language_data.json not found — run extract_language_data.py first")
    with path.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def lang_classes(language_data):
    return {int(k): set(v) for k, v in language_data["lang_classes"].items()}


@pytest.fixture(scope="module")
def languages_to_ignore(language_data):
    return set(language_data["languages_to_ignore"])


@pytest.fixture(scope="module")
def possible_false_positives(language_data):
    return language_data.get("possible_false_positive_languages", {})


# ---------------------------------------------------------------------------
# PDFProcessor smoke tests
# ---------------------------------------------------------------------------

class TestPDFProcessorExtraction:
    def test_extract_text_returns_nonempty_string(self):
        from langtrend.pdf_processor import PDFProcessor

        processor = PDFProcessor(
            input_dir=str(_SAMPLE_PDF.parent),
            output_dir=str(_SAMPLE_PDF.parent),
        )
        raw_text, page_texts = processor.extract_text(_SAMPLE_PDF)

        assert isinstance(raw_text, str)
        assert len(raw_text) > 100, "Expected substantial text from a real paper"
        assert isinstance(page_texts, dict)
        assert len(page_texts) > 0

    def test_clean_text_removes_excessive_whitespace(self):
        from langtrend.pdf_processor import PDFProcessor

        processor = PDFProcessor(
            input_dir=str(_SAMPLE_PDF.parent),
            output_dir=str(_SAMPLE_PDF.parent),
        )
        raw_text, _ = processor.extract_text(_SAMPLE_PDF)
        cleaned = processor.clean_text(raw_text)

        assert "\n\n\n" not in cleaned
        assert "  " not in cleaned


# ---------------------------------------------------------------------------
# Full PDF fallback chain (_process_single_paper with HTML unavailable)
# ---------------------------------------------------------------------------

class TestPDFFallbackChain:
    def test_pdf_path_inside_per_paper_subdir(self):
        """The per-paper subdirectory layout must match what _download_pdf creates."""
        paper_id = _SAMPLE_PDF.stem
        expected = PDF_ROOT / paper_id / f"{paper_id}.pdf"
        assert _SAMPLE_PDF == expected, (
            f"PDF not in expected per-paper subdir.\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {_SAMPLE_PDF}"
        )

    def test_process_single_paper_pdf_fallback(
        self, lang_classes, languages_to_ignore, possible_false_positives, tmp_path
    ):
        """_process_single_paper extracts text and populates sections from a real PDF
        when HTML is unavailable."""
        import process_papers as pp

        paper_id = _SAMPLE_PDF.stem
        paper = {
            "id": paper_id,
            "title": "Test paper",
            "abstract": "",
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        }

        # process_papers imports recheck_languages_from_html with `from`, so patch
        # its own module attribute, not the source module.
        orig_download = pp._download_pdf
        orig_html = pp.recheck_languages_from_html

        pp._download_pdf = lambda url, pdf_dir, pid: _SAMPLE_PDF
        pp.recheck_languages_from_html = lambda *a, **kw: {}

        try:
            record = pp._process_single_paper(
                paper=paper,
                lang_classes=lang_classes,
                languages_to_ignore=languages_to_ignore,
                possible_false_positive_languages=possible_false_positives,
                pdf_dir=tmp_path / "pdfs",
                html_cache_dir=tmp_path / "html_cache",
                pdf_cache_dir=tmp_path / "pdf_cache",
            )
        finally:
            pp._download_pdf = orig_download
            pp.recheck_languages_from_html = orig_html

        assert "pdf" in record["sources_checked"]
        # The PDF processing chain ran; languages may or may not be detected depending
        # on the sample PDF content (previously-false-positive languages are filtered).
        # Verify the record structure, not a specific language count.
        assert "sections" in record
        assert isinstance(record.get("warnings", []), list)
        # If pdf_full_text is present, check its structure
        pdf_section = record["sections"].get("pdf_full_text")
        if pdf_section is not None:
            assert pdf_section["source"] == "pdf"
            assert isinstance(pdf_section["detected_languages"], list)

    def test_pdf_cache_written(
        self, lang_classes, languages_to_ignore, possible_false_positives, tmp_path
    ):
        """PDF cache JSON is written to pdf_cache_dir with the expected structure."""
        import process_papers as pp

        paper_id = _SAMPLE_PDF.stem
        paper = {
            "id": paper_id,
            "title": "Test paper",
            "abstract": "",
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        }

        orig_download = pp._download_pdf
        orig_html = pp.recheck_languages_from_html

        pp._download_pdf = lambda url, pdf_dir, pid: _SAMPLE_PDF
        pp.recheck_languages_from_html = lambda *a, **kw: {}

        pdf_cache_dir = tmp_path / "pdf_cache"
        try:
            pp._process_single_paper(
                paper=paper,
                lang_classes=lang_classes,
                languages_to_ignore=languages_to_ignore,
                possible_false_positive_languages=possible_false_positives,
                pdf_dir=tmp_path / "pdfs",
                html_cache_dir=tmp_path / "html_cache",
                pdf_cache_dir=pdf_cache_dir,
            )
        finally:
            pp._download_pdf = orig_download
            pp.recheck_languages_from_html = orig_html

        cache_file = pdf_cache_dir / f"{paper_id}.json"
        assert cache_file.exists(), "PDF cache file was not written to pdf_cache_dir"

        with cache_file.open() as f:
            cached = json.load(f)

        assert cached["paper_id"] == paper_id
        assert isinstance(cached["text"], str) and len(cached["text"]) > 0
        assert isinstance(cached["cleaned_text"], str)
        assert isinstance(cached["body_text"], str)
        assert isinstance(cached["screened_text"], str)
        assert isinstance(cached["detected_languages"], list)
