"""
Integration tests for the PDF fallback path in _process_single_paper.

These tests use real PDFs already on disk — no network required.
They exercise the full chain:
    PDFProcessor.extract_text → clean_text → _detect_in_text → _build_detections

CI NOTE: everything below TestCleanTextHyphenation needs a real PDF, sourced by
scanning data/raw/pdfs/ (gitignored — populated by running the pipeline locally,
e.g. `make process`). There is no PDF fixture committed to the repo, so on a
fresh CI checkout data/raw/pdfs/ does not exist and TestPDFProcessorExtraction /
TestPDFFallbackChain are skipped (not run, not failed) via the pytestmark below.
This is a deliberate choice, not an oversight: PDF text extraction runs
through the real docling model, and a real downloaded arXiv paper is a much
more representative regression fixture than a synthetic minimal PDF would be.
Practical effect: pdf_processor.py's extract_text() and the PDF branch of
_process_single_paper/_reprocess_single_paper are only verified locally, not
in the Tests CI workflow or its coverage numbers — run this file locally
after `make process` (or similar) before trusting a PDF-path change.

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

_requires_sample_pdf = pytest.mark.skipif(
    _SAMPLE_PDF is None,
    reason="No valid PDFs found in data/raw/pdfs — download at least one first (not available in CI, see module docstring)",
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
# clean_text unit tests — no PDF required
# ---------------------------------------------------------------------------

class TestCleanTextHyphenation:
    """clean_text must rejoin PDF line-break hyphenation artifacts."""

    @pytest.fixture(autouse=True)
    def processor(self):
        from langtrend.pdf_processor import PDFProcessor
        self.p = PDFProcessor(input_dir=".", output_dir=".")

    def test_bare_hyphen_newline(self):
        # Classic pdfplumber: "dura-\ntion" → "duration"
        assert self.p.clean_text("dura-\ntion") == "duration"

    def test_space_hyphen_newline(self):
        # pdfplumber variant with trailing space: "anecdo -\ntal" → "anecdotal"
        assert self.p.clean_text("anecdo -\ntal") == "anecdotal"

    def test_space_hyphen_no_newline(self):
        # docling artifact (lines joined as space): "anecdo -tal" → "anecdotal"
        # This is the real case from 2605.22447v1.json.
        assert self.p.clean_text("anecdo -tal") == "anecdotal"

    def test_legitimate_hyphen_unchanged(self):
        # No preceding space, no newline — genuine compound word, must not change.
        assert self.p.clean_text("self-aware") == "self-aware"

    def test_multipart_compound_unchanged(self):
        assert self.p.clean_text("state-of-the-art") == "state-of-the-art"

    def test_in_sentence_context(self):
        text = "Without such resources it is difficult to move beyond anecdo -tal observations."
        cleaned = self.p.clean_text(text)
        assert "anecdotal" in cleaned
        assert "anecdo -tal" not in cleaned


# ---------------------------------------------------------------------------
# trim_markdown_end_matter — no PDF/docling required (operates on markdown str)
# ---------------------------------------------------------------------------

class TestTrimMarkdownEndMatter:
    """extract_text's end-matter boundary logic on docling markdown.

    Mirrors the two-tier + appendix-aware logic in
    text_cleaning.trim_pdf_text_to_body; kept in sync with it.
    """

    def _trim(self, md):
        from langtrend.pdf_processor import trim_markdown_end_matter
        return trim_markdown_end_matter(md)

    def test_cuts_at_references(self):
        md = "## 1 Introduction\n\nWe study Swahili.\n\n## References\n\n- Smith 2020.\n"
        result = self._trim(md)
        assert "Swahili" in result
        assert "Smith 2020" not in result

    def test_references_before_midpoint_still_cut(self):
        # Long bibliography puts References before the midpoint — must still cut.
        md = "## 1 Introduction\n\nWe study Igbo.\n\n## References\n\n" + "".join(
            f"- Author {i}. 2020. Cited title.\n" for i in range(1, 120)
        )
        result = self._trim(md)
        assert "Igbo" in result
        assert "Cited title" not in result

    def test_early_related_work_not_used_when_references_present(self):
        md = (
            "## 1 Introduction\n\nWe study Swahili.\n\n"
            "## 2 Related Work\n\nPrior work on Hausa.\n\n"
            "## 3 Methods\n\nWe evaluate Zulu.\n\n"
            "## References\n\n- Jones 2020.\n"
        )
        result = self._trim(md)
        assert "Zulu" in result  # body after early Related Work kept
        assert "Jones 2020" not in result

    def test_appendix_after_references_is_kept(self):
        md = (
            "## 1 Introduction\n\nWe study Swahili.\n\n"
            "## References\n\n- Smith 2020. Amharic study.\n- Jones 2021.\n\n"
            "## Appendix\n\n### A Extra results on Tamil and Telugu.\n"
        )
        result = self._trim(md)
        assert "Tamil" in result and "Telugu" in result  # appendix kept
        assert "Smith 2020" not in result  # citation list excised

    def test_no_end_matter_returns_unchanged(self):
        md = "## 1 Introduction\n\nWe study Arabic.\n\n## Conclusion\n\nDone.\n"
        assert self._trim(md) == md

    def test_empty_returns_empty(self):
        assert self._trim("") == ""


# ---------------------------------------------------------------------------
# PDFProcessor smoke tests
# ---------------------------------------------------------------------------

@_requires_sample_pdf
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
        # docling returns {} for page_texts (text is extracted as a whole document)

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

@_requires_sample_pdf
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
