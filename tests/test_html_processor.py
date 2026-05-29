"""
Unit tests for langtrend/html_processor.py.

Run with:  pytest tests/test_html_processor.py -v
"""

import pytest
from bs4 import BeautifulSoup

from langtrend.html_processor import (
    clean_html_soup,
    extract_sections_from_soup,
    extract_sections_from_html,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# extract_sections_from_soup — inline tag handling
# ---------------------------------------------------------------------------

class TestInlineTagTextExtraction:
    def test_inline_span_does_not_split_word(self):
        soup = _soup(
            '<section><h2>Intro</h2>'
            '<p>human <span class="ltx_font_bold">Mo</span>tion understanding</p>'
            "</section>"
        )
        sections = extract_sections_from_soup(soup)
        assert "Motion" in sections["Intro"]
        assert "Mo tion" not in sections["Intro"]

    def test_words_around_inline_tag_keep_space(self):
        soup = _soup(
            '<section><h2>S</h2>'
            '<p>We evaluate on <em>Arabic</em> and Swahili corpora.</p>'
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert "Arabic" in text
        assert " Arabic " in text  # space on both sides

    def test_bold_word_keeps_surrounding_spaces(self):
        soup = _soup(
            '<section><h2>S</h2>'
            '<p>Hello <strong>World</strong> stays together.</p>'
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert "Hello World stays together" in text

    def test_multiple_inline_splits_in_one_paragraph(self):
        soup = _soup(
            '<section><h2>S</h2>'
            '<p><em>Swahi</em>li and <span>Ara</span>bic datasets.</p>'
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert "Swahili" in text
        assert "Arabic" in text


# ---------------------------------------------------------------------------
# extract_sections_from_soup — section structure
# ---------------------------------------------------------------------------

class TestSectionExtraction:
    def test_single_section_with_heading(self):
        soup = _soup(
            '<section><h2>Methods</h2><p>We use Arabic data.</p></section>'
        )
        sections = extract_sections_from_soup(soup)
        assert "Methods" in sections
        assert "Arabic" in sections["Methods"]

    def test_multiple_sections(self):
        soup = _soup(
            '<section><h2>Intro</h2><p>First.</p></section>'
            '<section><h2>Results</h2><p>Second.</p></section>'
        )
        sections = extract_sections_from_soup(soup)
        assert "Intro" in sections
        assert "Results" in sections

    def test_fallback_when_no_section_tags(self):
        soup = _soup(
            '<html><body>'
            '<h2>Methods</h2><p>We use Hindi data.</p>'
            '<h2>Results</h2><p>We report on Swahili.</p>'
            '</body></html>'
        )
        sections = extract_sections_from_soup(soup)
        assert len(sections) >= 2

    def test_body_fallback_when_no_structure(self):
        soup = _soup('<p>Some plain text.</p>')
        sections = extract_sections_from_soup(soup)
        assert len(sections) > 0
        assert any("plain text" in v for v in sections.values())


# ---------------------------------------------------------------------------
# clean_html_soup — removes unwanted sections
# ---------------------------------------------------------------------------

class TestCleanHtmlSoup:
    def test_removes_abstract_div(self):
        soup = clean_html_soup(
            '<div class="abstract">This is the abstract.</div>'
            '<p>Main body text.</p>'
        )
        assert "abstract" not in soup.get_text().lower()
        assert "Main body" in soup.get_text()

    def test_removes_references_heading_and_content(self):
        soup = clean_html_soup(
            '<section><h2>Introduction</h2><p>Intro text.</p></section>'
            '<section><h2>References</h2><p>[1] Smith 2022.</p></section>',
            remove_headings=["References"],
        )
        text = soup.get_text()
        assert "Intro text" in text
        assert "Smith 2022" not in text

    def test_removes_nav_and_footer(self):
        soup = clean_html_soup(
            '<nav>Navigation</nav>'
            '<p>Content</p>'
            '<footer>Footer</footer>'
        )
        text = soup.get_text()
        assert "Navigation" not in text
        assert "Footer" not in text
        assert "Content" in text

    # --- math element handling ---

    def test_subscript_math_block_removed(self):
        # i_k rendered as MathML msub would produce "ik" which falsely matches Inupiaq.
        # The whole <math> block should be replaced with a space.
        html = (
            '<p>For each pair '
            '<math><semantics>'
            '<msub><mi>i</mi><mi>k</mi></msub>'
            '<annotation encoding="application/x-tex">i_k</annotation>'
            '</semantics></math>'
            ' we compute a score.</p>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        assert "ik" not in text

    def test_superscript_math_block_removed(self):
        html = (
            '<p>The value '
            '<math><msup><mi>x</mi><mn>2</mn></msup></math>'
            ' is computed.</p>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        # "x2" or "2x" concatenation should not appear
        assert "x2" not in text
        assert "2x" not in text

    def test_plain_math_block_annotation_stripped_not_whole_block(self):
        # A math block with no subscript/superscript should NOT be removed wholesale;
        # only its <annotation> child should be stripped.
        html = (
            '<p>Let '
            '<math><semantics>'
            '<mi>x</mi>'
            '<annotation encoding="application/x-tex">x</annotation>'
            '</semantics></math>'
            ' be a variable.</p>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        # The annotation LaTeX source "x" is gone but the display "x" remains
        assert "variable" in text
        # The word "x" should appear once (display), not twice (display + annotation)
        assert text.count("x") == 1

    def test_language_in_prose_adjacent_to_math_preserved(self):
        # A real language name in prose text must survive even when nearby math is stripped.
        html = (
            '<section><h2>Method</h2>'
            '<p>We train on Inupiaq data with input '
            '<math><msub><mi>i</mi><mi>k</mi></msub></math>'
            ' at each step.</p>'
            '</section>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        assert "Inupiaq" in text
        assert "ik" not in text

    # --- numbered heading removal (arXiv section numbers) ---

    def test_removes_numbered_related_work_section(self):
        # arXiv headings render as "6 Related Work" — must still be removed.
        html = (
            '<section><h2>Introduction</h2><p>Intro text.</p></section>'
            '<section><h2>6 Related Work</h2><p>GAN-based methods.</p></section>'
        )
        soup = clean_html_soup(html, remove_headings=["Related Work"])
        text = soup.get_text()
        assert "Intro text" in text
        assert "GAN-based methods" not in text

    def test_removes_dotted_numbered_related_work_section(self):
        # Handles "6. Related Work" (dot after number) as well.
        html = (
            '<section><h2>Methods</h2><p>Method text.</p></section>'
            '<section><h2>6. Related Work</h2><p>Prior work text.</p></section>'
        )
        soup = clean_html_soup(html, remove_headings=["Related Work"])
        text = soup.get_text()
        assert "Method text" in text
        assert "Prior work text" not in text

    def test_removes_nested_subsections_within_numbered_related_work(self):
        # When subsections are nested inside the Related Work <section>, they must
        # also be removed — the whole tree goes with sec.decompose().
        html = (
            '<section><h2>Introduction</h2><p>Intro.</p></section>'
            '<section>'
            '  <h2>6 Related Work</h2>'
            '  <section>'
            '    <h3>6.1 Sign-to-text and text-to-sign translation</h3>'
            '    <p>GAN-based pose synthesis.</p>'
            '  </section>'
            '  <section>'
            '    <h3>6.2 Multilingual sign processing</h3>'
            '    <p>Multilingual methods.</p>'
            '  </section>'
            '</section>'
        )
        soup = clean_html_soup(html, remove_headings=["Related Work"])
        text = soup.get_text()
        assert "Intro" in text
        assert "GAN-based pose synthesis" not in text
        assert "Multilingual methods" not in text
        assert "Sign-to-text" not in text

    def test_flat_numbered_related_work_stops_at_next_top_heading(self):
        # Flat HTML (no <section> tags): heading-based removal walks siblings until
        # it hits any h-tag, so content between "Related Work" and the next heading
        # is removed, and content after the next heading is preserved.
        html = (
            '<html><body>'
            '<h2>Introduction</h2><p>Intro text.</p>'
            '<h2>6 Related Work</h2><p>Related work prose.</p>'
            '<h2>Conclusion</h2><p>Conclusion text.</p>'
            '</body></html>'
        )
        soup = clean_html_soup(html, remove_headings=["Related Work"])
        text = soup.get_text()
        assert "Intro text" in text
        assert "Related work prose" not in text
        assert "Conclusion text" in text
