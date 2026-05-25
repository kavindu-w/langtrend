"""
Unit tests for langtrend/text_cleaning.py.

Run with:  pytest tests/test_text_cleaning.py -v
"""

import pytest

from langtrend.text_cleaning import (
    clean_paper_text_for_language_screening,
    detect_languages_in_text,
    should_ignore_acronym_language_match,
    trim_pdf_text_to_body,
    _looks_like_capitalized_acronym_context,
    _initials_match_acronym,
)


# ---------------------------------------------------------------------------
# Minimal language data fixtures (no file I/O needed)
# ---------------------------------------------------------------------------

@pytest.fixture
def lang_classes():
    return {
        0: {"Swahili", "Arabic", "Hindi", "Gan", "Mape", "Dass", "Elu"},
        1: {"French", "Spanish", "German"},
        2: {"Chinese", "Japanese"},
    }


@pytest.fixture
def languages_to_ignore():
    return {"The", "To", "As", "Are"}


# ---------------------------------------------------------------------------
# clean_paper_text_for_language_screening
# ---------------------------------------------------------------------------

class TestCleanPaperText:
    def test_removes_inline_math(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "The formula $x_i \\in \\mathbb{R}^{256}$ is used."
        )
        assert "inline_math" in steps
        assert all("$" not in b for b in blocks)

    def test_removes_citation_author_year(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "As shown by Smith et al. (2022), the results hold."
        )
        assert "citation_author_year" in steps
        assert all("Smith" not in b for b in blocks)

    def test_removes_citation_brackets(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "Results improve [Jones et al. 2021] significantly."
        )
        assert "citation_brackets" in steps

    def test_removes_alnum_tokens(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "We use a 3B parameter model with GPT2 architecture."
        )
        assert "alnum_tokens" in steps
        assert all("3B" not in b and "GPT2" not in b for b in blocks)

    def test_empty_input_returns_empty(self):
        blocks, steps = clean_paper_text_for_language_screening("")
        assert blocks == []

    def test_splits_on_double_newline(self):
        blocks, _ = clean_paper_text_for_language_screening("First paragraph.\n\nSecond paragraph.")
        assert len(blocks) == 2

    def test_acronym_catch_recorded_in_step_matches(self):
        _, steps = clean_paper_text_for_language_screening(
            "We use Mean Absolute Percentage Error (MAPE) as our metric."
        )
        assert "acronym_catch" in steps
        assert any("MAPE" in m for m in steps["acronym_catch"])

    def test_acronym_catch_removes_parenthetical_from_block(self):
        blocks, _ = clean_paper_text_for_language_screening(
            "Mean Absolute Percentage Error (MAPE) is our metric."
        )
        assert all("(MAPE)" not in b and "MAPE" not in b for b in blocks)
        assert any("Mean Absolute Percentage Error" in b for b in blocks)

    def test_acronym_catch_with_connector_word(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "Depression Anxiety and Stress Scale (DASS) scores were recorded."
        )
        assert "acronym_catch" in steps
        assert any("DASS" in m for m in steps["acronym_catch"])
        assert all("(DASS)" not in b and "DASS" not in b for b in blocks)

    def test_acronym_catch_full_phrase_captured(self):
        _, steps = clean_paper_text_for_language_screening(
            "Generative Adversarial Network (GAN) models."
        )
        assert "GAN" in steps["acronym_catch"]

    def test_acronym_catch_multiple_consecutive_connectors(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "Depression Anxiety of and Stress Scale (DASS)"
        )
        assert "acronym_catch" in steps
        assert "DASS" in steps["acronym_catch"]
        assert all("(DASS)" not in b and "DASS" not in b for b in blocks)

    def test_acronym_catch_two_definitions_same_sentence(self):
        blocks, steps = clean_paper_text_for_language_screening(
            "Mean Absolute Percentage Error (MAPE) and Depression Anxiety of and Stress Scale (DASS)"
        )
        caught = steps.get("acronym_catch", [])
        assert any("MAPE" in m for m in caught)
        assert any("DASS" in m for m in caught)
        assert all("(MAPE)" not in b and "(DASS)" not in b for b in blocks)


# ---------------------------------------------------------------------------
# _looks_like_capitalized_acronym_context
# ---------------------------------------------------------------------------

class TestLooksLikeCapitalizedAcronymContext:
    def test_titlecase_phrase_is_context(self):
        text = "Mean Absolute Percentage Error (MAPE)"
        paren_idx = text.index("(")
        assert _looks_like_capitalized_acronym_context(text, paren_idx)

    def test_lowercase_sentence_is_not_context(self):
        text = "the value was computed using some method (MAPE)"
        paren_idx = text.index("(")
        assert not _looks_like_capitalized_acronym_context(text, paren_idx)

    def test_connector_word_allowed(self):
        text = "Depression Anxiety and Stress Scale (DASS)"
        paren_idx = text.index("(")
        assert _looks_like_capitalized_acronym_context(text, paren_idx)

    def test_single_word_without_hyphen_is_not_context(self):
        text = "Scale (DASS)"
        paren_idx = text.index("(")
        assert not _looks_like_capitalized_acronym_context(text, paren_idx)

    def test_hyphenated_single_token_counts(self):
        text = "Feature-Based (FB)"
        paren_idx = text.index("(")
        assert _looks_like_capitalized_acronym_context(text, paren_idx)


# ---------------------------------------------------------------------------
# _initials_match_acronym
# ---------------------------------------------------------------------------

class TestInitialsMatchAcronym:
    def test_simple_match(self):
        text = "Mean Absolute Percentage Error (MAPE)"
        assert _initials_match_acronym(text, text.index("("), "MAPE")

    def test_connector_contributing_letter(self):
        text = "Point of Interest (POI)"
        assert _initials_match_acronym(text, text.index("("), "POI")

    def test_connector_not_contributing(self):
        text = "Depression Anxiety and Stress Scale (DASS)"
        assert _initials_match_acronym(text, text.index("("), "DASS")

    def test_wrong_acronym_does_not_match(self):
        text = "Mean Absolute Percentage Error (MAPE)"
        assert not _initials_match_acronym(text, text.index("("), "GAN")

    def test_two_acronyms_no_bleed(self):
        # When RL precedes MAPE, the initials check should still confirm MAPE
        text = "Reinforcement Learning (RL) uses Mean Absolute Percentage Error (MAPE)"
        mape_idx = text.rindex("(")
        assert _initials_match_acronym(text, mape_idx, "MAPE")


# ---------------------------------------------------------------------------
# should_ignore_acronym_language_match
# ---------------------------------------------------------------------------

class TestShouldIgnoreAcronymLanguageMatch:
    def test_suppresses_acronym_definition(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "Mean Absolute Percentage Error (MAPE) is reported.",
            "Mape",
            paper_id="test",
            warning_path=warn,
        )
        assert result is True

    def test_does_not_suppress_standalone_mention(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "We evaluated on Mape and Swahili corpora.",
            "Mape",
            paper_id="test",
            warning_path=warn,
        )
        assert result is False

    def test_suppresses_gan(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "Generative Adversarial Network (GAN) was applied.",
            "Gan",
            paper_id="test",
            warning_path=warn,
        )
        assert result is True

    def test_suppresses_dass_with_connector(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "Depression Anxiety and Stress Scale (DASS) was used.",
            "Dass",
            paper_id="test",
            warning_path=warn,
        )
        assert result is True

    def test_does_not_suppress_real_language_alongside_acronym(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "Generative Adversarial Network (GAN) tested on Swahili data.",
            "Swahili",
            paper_id="test",
            warning_path=warn,
        )
        assert result is False

    def test_warning_written_on_suppression(self, tmp_path):
        import json
        warn = tmp_path / "warnings.json"
        should_ignore_acronym_language_match(
            "Mean Absolute Percentage Error (MAPE) metric.",
            "Mape",
            paper_id="paper_001",
            warning_path=warn,
        )
        assert warn.exists()
        entries = json.loads(warn.read_text())
        assert entries[0]["language"] == "Mape"
        assert entries[0]["acronym"] == "MAPE"
        assert entries[0]["paper_id"] == "paper_001"
        assert entries[0]["rule"] == "parenthetical_acronym_context"

    def test_no_suppression_for_lowercase_context(self, tmp_path):
        warn = tmp_path / "warnings.json"
        result = should_ignore_acronym_language_match(
            "the error metric called (MAPE) is used here.",
            "Mape",
            paper_id="test",
            warning_path=warn,
        )
        assert result is False


# ---------------------------------------------------------------------------
# detect_languages_in_text (end-to-end)
# ---------------------------------------------------------------------------

class TestDetectLanguagesInText:
    def test_detects_real_languages(self, lang_classes, languages_to_ignore):
        blocks, _ = clean_paper_text_for_language_screening(
            "We evaluate on Arabic and Swahili benchmarks."
        )
        detected = detect_languages_in_text(blocks, lang_classes, languages_to_ignore)
        assert "Arabic" in detected
        assert "Swahili" in detected

    def test_suppresses_acronym_definition(self, lang_classes, languages_to_ignore, tmp_path):
        blocks, _ = clean_paper_text_for_language_screening(
            "Mean Absolute Percentage Error (MAPE) is our metric."
        )
        detected = detect_languages_in_text(blocks, lang_classes, languages_to_ignore, paper_id="test")
        assert "Mape" not in detected

    def test_suppresses_gan_keeps_arabic(self, lang_classes, languages_to_ignore):
        blocks, _ = clean_paper_text_for_language_screening(
            "Generative Adversarial Network (GAN) was used on Arabic data."
        )
        detected = detect_languages_in_text(blocks, lang_classes, languages_to_ignore, paper_id="test")
        assert "Gan" not in detected
        assert "Arabic" in detected

    def test_preserves_language_when_no_acronym_definition(self, lang_classes, languages_to_ignore):
        blocks, _ = clean_paper_text_for_language_screening(
            "We include Gan language data from West Africa."
        )
        detected = detect_languages_in_text(blocks, lang_classes, languages_to_ignore, paper_id="test")
        assert "Gan" in detected

    def test_two_acronyms_in_sentence(self, lang_classes, languages_to_ignore):
        blocks, _ = clean_paper_text_for_language_screening(
            "Reinforcement Learning (RL) uses Mean Absolute Percentage Error (MAPE)."
        )
        detected = detect_languages_in_text(blocks, lang_classes, languages_to_ignore, paper_id="test")
        assert "Mape" not in detected

    def test_ignores_listed_languages(self, lang_classes):
        ignore = {"Arabic", "Swahili"}
        blocks, _ = clean_paper_text_for_language_screening(
            "Results on Arabic and Swahili are shown."
        )
        detected = detect_languages_in_text(blocks, lang_classes, ignore)
        assert "Arabic" not in detected
        assert "Swahili" not in detected

    def test_empty_text_returns_empty(self, lang_classes, languages_to_ignore):
        detected = detect_languages_in_text([], lang_classes, languages_to_ignore)
        assert detected == []


# ---------------------------------------------------------------------------
# trim_pdf_text_to_body
# ---------------------------------------------------------------------------

class TestTrimPdfTextToBody:
    _FULL = (
        "Title of the Paper\n\nAuthor One, Author Two\n\n"
        "Abstract\nThis paper presents a study of Arabic NLP.\n\n"
        "{intro_heading}\n"
        "We evaluate on Swahili and Hindi corpora.\n\n"
        "2. Methods\nWe use a transformer model.\n\n"
        "{end_heading}\n"
        "[1] Smith 2022. Journal of NLP."
    )

    def _make(self, intro="1. Introduction", end="References"):
        return self._FULL.format(intro_heading=intro, end_heading=end)

    def test_skips_before_introduction(self):
        text = self._make()
        result = trim_pdf_text_to_body(text)
        assert "Abstract" not in result
        assert "Author One" not in result

    def test_includes_introduction_and_body(self):
        text = self._make()
        result = trim_pdf_text_to_body(text)
        assert "Introduction" in result
        assert "Swahili" in result
        assert "Methods" in result

    def test_stops_before_references(self):
        text = self._make()
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result
        assert "References" not in result

    def test_all_caps_introduction(self):
        text = self._make(intro="INTRODUCTION")
        result = trim_pdf_text_to_body(text)
        assert "Abstract" not in result
        assert "Swahili" in result

    def test_introduction_merged_with_text(self):
        # pdfplumber sometimes merges heading + first sentence on one line
        text = (
            "Abstract\nThis is the abstract.\n\n"
            "1. Introduction We begin by evaluating on Hindi data.\n\n"
            "References\n[1] Jones 2020."
        )
        result = trim_pdf_text_to_body(text)
        assert "Abstract" not in result
        assert "Hindi" in result
        assert "Jones 2020" not in result

    def test_stops_at_bibliography(self):
        text = self._make(end="Bibliography")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_stops_at_acknowledgements(self):
        text = self._make(end="Acknowledgements")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_stops_at_related_work(self):
        text = self._make(end="Related Work")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_stops_at_related_works(self):
        text = self._make(end="Related Works")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_stops_at_funding(self):
        text = self._make(end="Funding")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_stops_at_ethics_statement(self):
        # "Ethics Statement" must match before plain "Ethics"
        text = self._make(end="Ethics Statement")
        result = trim_pdf_text_to_body(text)
        assert "Smith 2022" not in result

    def test_appendix_is_kept(self):
        # Appendix sections are kept (consistent with the HTML processor)
        text = (
            "1. Introduction\nWe study Swahili.\n\n"
            "Appendix\nAdditional tables in Hindi.\n\n"
            "References\n[1] Jones 2020."
        )
        result = trim_pdf_text_to_body(text)
        assert "Hindi" in result       # appendix content kept
        assert "Jones 2020" not in result  # references still cut

    def test_no_introduction_returns_original(self):
        text = "Some text without section markers.\nSwahili data used."
        result = trim_pdf_text_to_body(text)
        assert result == text

    def test_no_references_includes_to_end(self):
        text = "1. Introduction\nWe study Arabic.\n\nConclusion\nThis works."
        result = trim_pdf_text_to_body(text)
        assert "Arabic" in result
        assert "Conclusion" in result

    def test_empty_string_returns_empty(self):
        assert trim_pdf_text_to_body("") == ""
