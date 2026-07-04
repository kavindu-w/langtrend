"""
Unit tests for langtrend/html_processor.py.

Run with:  pytest tests/test_html_processor.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
from bs4 import BeautifulSoup
import requests

from langtrend.html_processor import (
    clean_html_soup,
    extract_sections_from_soup,
    extract_sections_from_html,
    is_removable_heading,
    recheck_languages_from_html,
    fetch_arxiv_html,
    _HTML_MAX_BYTES,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# is_removable_heading — shared section-exclusion rule (fresh + reprocess)
# ---------------------------------------------------------------------------

class TestIsRemovableHeading:
    """The single rule used both at fetch time and by the cache reprocess, so a
    reprocess drops exactly the sections a fresh fetch does. Substring match
    catches arXiv's glued numbering and non-leading terms."""

    @pytest.mark.parametrize("title", [
        "References",
        "Bibliography",
        "Related Work",
        "Related Works",
        "2Related work",                       # glued numbering
        "IIRelated Work",                      # Roman numeral, glued
        "Appendix ARelated Work",              # appendix variant
        "Appendix AExtended Related Work",
        "2Background and Related Work",         # combined — also excluded (substring)
        "IIBackground and Related Work",
        "Appendix EMethod Comparison and Related Work",
        "Acknowledgements",
        "Acknowledgments",
        "Ethics Statement",
        "Abstract",
    ])
    def test_excluded_headings(self, title):
        assert is_removable_heading(title) is True

    @pytest.mark.parametrize("title", [
        "Introduction",
        "2Methods",
        "Experiments",
        "Results",
        "Appendix AImplementation Details",
        "Dataset",
        "Conclusion",
    ])
    def test_kept_headings(self, title):
        assert is_removable_heading(title) is False

    def test_empty_is_not_removable(self):
        assert is_removable_heading("") is False

    def test_custom_heading_list(self):
        assert is_removable_heading("Limitations", ["Limitations"]) is True
        assert is_removable_heading("Methods", ["Limitations"]) is False


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

    def test_ltx_para_div_wrapping_p_not_duplicated(self):
        # arXiv's LaTeXML HTML5 export wraps every <p class="ltx_p"> in a
        # <div class="ltx_para">. find_all(["p", "div", ...]) matches both
        # the wrapper and the inner p, so without de-duplication the same
        # sentence gets appended twice.
        soup = _soup(
            '<section><h2>Methods</h2>'
            '<div class="ltx_para"><p class="ltx_p">'
            "We use jina-embeddings-v3 as the retrieval model."
            "</p></div>"
            "</section>"
        )
        text = extract_sections_from_soup(soup)["Methods"]
        assert text.count("jina-embeddings-v3") == 1

    def test_div_wrapping_two_paragraphs_keeps_both_without_duplication(self):
        soup = _soup(
            "<section><h2>S</h2>"
            '<div class="ltx_para"><p>First sentence about Swahili.</p>'
            "<p>Second sentence about Arabic.</p></div>"
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert text.count("Swahili") == 1
        assert text.count("Arabic") == 1

    def test_stray_text_beside_nested_p_is_kept_not_dropped(self):
        # A wrapper div that mixes loose text directly inside it alongside a
        # nested <p> — the wrapper must not be skipped wholesale (that would
        # silently drop the stray text), and the nested <p> must still be
        # captured exactly once (not duplicated via the wrapper's get_text).
        soup = _soup(
            "<section><h2>S</h2>"
            '<div class="ltx_para">As shown below in Swahili: '
            "<p>The Arabic paragraph text.</p></div>"
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert "Swahili" in text
        assert text.count("Arabic paragraph text") == 1

    def test_figcaption_buried_inside_non_text_tag_not_duplicated(self):
        # arXiv's multi-panel figures wrap each subfigure as
        # <div class="ltx_flex_cell"><figure>...<figcaption>...</figcaption>
        # </figure></div> — the figcaption is nested two levels deep through
        # a <figure> tag that isn't itself in _TEXT_TAGS, so a shallow
        # "is this direct child a match" check misses it and pulls the
        # caption text into the div's "own text" a second time.
        soup = _soup(
            '<section><h2>S</h2>'
            '<div class="ltx_flex_cell">'
            '<figure><img src="x.png">'
            "<figcaption>(a) Relearning leakage rate.</figcaption>"
            "</figure></div>"
            "</section>"
        )
        text = extract_sections_from_soup(soup)["S"]
        assert text.count("Relearning leakage rate") == 1


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

    # --- ltx_bibliography split structure ---

    def test_removes_ltx_bibliography_section_without_heading(self):
        # arXiv renders some papers with the References heading in one <section>
        # and the actual bibliography entries in a separate sibling
        # <section class="ltx_bibliography"> with no heading.  Both must be removed.
        html = (
            '<section><h2>Introduction</h2><p>Intro text.</p></section>'
            '<section><h2>References</h2><p>See bibliography below.</p></section>'
            '<section class="ltx_bibliography">'
            '<p>Smith et al. 2023. Some paper. arXiv:2301.00001.</p>'
            '<p>Jones et al. 2024. Another paper. EMNLP 2024.</p>'
            '</section>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        assert "Intro text" in text
        assert "Smith et al" not in text
        assert "Jones et al" not in text

    def test_ltx_bibliography_with_heading_inside_also_removed(self):
        # When ltx_bibliography contains the heading itself (the other arXiv variant),
        # the existing heading-based removal handles it — verify it still works.
        html = (
            '<section><h2>Introduction</h2><p>Intro text.</p></section>'
            '<section class="ltx_bibliography">'
            '<h2>References</h2>'
            '<p>Smith et al. 2023. Some paper.</p>'
            '</section>'
        )
        soup = clean_html_soup(html)
        text = soup.get_text()
        assert "Intro text" in text
        assert "Smith et al" not in text


# ---------------------------------------------------------------------------
# recheck_languages_from_html — raw HTML caching
# ---------------------------------------------------------------------------

_MINIMAL_HTML = (
    '<section><h2>Introduction</h2><p>We use Python.</p></section>'
)


class TestRawHtmlCaching:
    def test_saves_raw_html_on_first_fetch(self, tmp_path):
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(_MINIMAL_HTML, "url", True, False)) as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00001"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        assert (tmp_path / "2000.00001.html").exists()
        assert (tmp_path / "2000.00001.html").read_text() == _MINIMAL_HTML
        mock_fetch.assert_called_once()

    def test_uses_cached_html_without_fetching(self, tmp_path):
        (tmp_path / "2000.00002.html").write_text(_MINIMAL_HTML, encoding="utf-8")
        with patch("langtrend.html_processor.fetch_arxiv_html") as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00002"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        mock_fetch.assert_not_called()

    def test_json_cache_still_short_circuits_before_html(self, tmp_path):
        # If the JSON result cache exists, neither the HTML file nor fetch is touched.
        import json
        json_path = tmp_path / "2000.00003.json"
        json_path.write_text(json.dumps({"_complete": True}), encoding="utf-8")
        with patch("langtrend.html_processor.fetch_arxiv_html") as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00003"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        mock_fetch.assert_not_called()
        assert not (tmp_path / "2000.00003.html").exists()

    def test_incomplete_json_cache_retries_fetch(self, tmp_path):
        # _complete=False must NOT short-circuit — re-serving the same partial
        # content forever would defeat the point of ever retrying it.
        import json
        json_path = tmp_path / "2000.00004.json"
        json_path.write_text(json.dumps({"_complete": False, "Introduction": {"text": "old partial"}}), encoding="utf-8")
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(_MINIMAL_HTML, "url", True, False)) as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00004"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        mock_fetch.assert_called_once()

    def test_incomplete_json_cache_ignores_stale_html_file_too(self, tmp_path):
        # Regression guard: a stalled download writes its partial bytes to the
        # .html file too (see fetch_arxiv_html), so an incomplete json cache
        # must ALSO bypass the raw .html shortcut, not just re-load the same
        # stale partial content from disk instead of retrying.
        import json
        (tmp_path / "2000.00005.json").write_text(
            json.dumps({"_complete": False, "Introduction": {"text": "old partial"}}), encoding="utf-8"
        )
        (tmp_path / "2000.00005.html").write_text("<html>stale partial content</html>", encoding="utf-8")
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(_MINIMAL_HTML, "url", True, False)) as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00005"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        mock_fetch.assert_called_once()
        # The stale .html is overwritten with the newly-fetched (complete) content.
        assert (tmp_path / "2000.00005.html").read_text() == _MINIMAL_HTML

    def test_unavailable_json_cache_still_short_circuits(self, tmp_path):
        # _unavailable is a permanent "no HTML exists for this paper" marker —
        # unlike _complete=False, it must NOT trigger a retry every time.
        import json
        (tmp_path / "2000.00006.json").write_text(
            json.dumps({"_complete": False, "_unavailable": True}), encoding="utf-8"
        )
        with patch("langtrend.html_processor.fetch_arxiv_html") as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00006"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        mock_fetch.assert_not_called()

    def test_confirmed_404_writes_unavailable_sentinel(self, tmp_path):
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(None, "url", False, True)):
            recheck_languages_from_html(
                {"id": "2000.00007"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        import json
        cache = json.loads((tmp_path / "2000.00007.json").read_text(encoding="utf-8"))
        assert cache.get("_unavailable") is True

    def test_transient_failure_does_not_write_any_cache_file(self, tmp_path):
        # A rate limit / 5xx / timeout must NOT be treated the same as a
        # confirmed 404 — no sentinel should be written at all, so the paper
        # stays eligible for a real retry later (e.g. a CI retry job) instead
        # of being permanently written off based on a one-off hiccup.
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(None, "url", False, False)):
            detections, is_complete, conflicts = recheck_languages_from_html(
                {"id": "2000.00008"},
                lang_classes={},
                languages_to_ignore=set(),
                out_dir=tmp_path,
            )
        assert detections == {}
        assert is_complete is False
        assert not (tmp_path / "2000.00008.json").exists()
        assert not (tmp_path / "2000.00008.html").exists()

    def test_transient_failure_is_retried_on_next_call(self, tmp_path):
        # Since no cache file was written, a subsequent call must attempt the
        # fetch again rather than being short-circuited.
        with patch("langtrend.html_processor.fetch_arxiv_html", return_value=(None, "url", False, False)) as mock_fetch:
            recheck_languages_from_html(
                {"id": "2000.00009"}, lang_classes={}, languages_to_ignore=set(), out_dir=tmp_path,
            )
            recheck_languages_from_html(
                {"id": "2000.00009"}, lang_classes={}, languages_to_ignore=set(), out_dir=tmp_path,
            )
        assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# fetch_arxiv_html — real network fetch, mocked at the session boundary
# ---------------------------------------------------------------------------

def _mock_response(status_ok=True, error_status=404):
    resp = MagicMock()
    resp.status_code = 200 if status_ok else error_status
    if status_ok:
        resp.raise_for_status = MagicMock()
    else:
        http_err = requests.HTTPError(f"{error_status} error")
        http_err.response = resp
        resp.raise_for_status = MagicMock(side_effect=http_err)
    return resp


class TestFetchArxivHtml:
    def test_returns_none_immediately_for_empty_url(self):
        assert fetch_arxiv_html("") == (None, None, False, False)

    @patch("time.sleep")
    def test_fetches_and_decodes_successfully(self, mock_sleep):
        session = MagicMock()
        resp = _mock_response()
        resp.iter_content.return_value = iter([b"<html>", b"hello</html>"])
        session.get.return_value = resp

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/2501.00001")

        assert html_text == "<html>hello</html>"
        assert html_url == "https://arxiv.org/html/2501.00001"
        assert is_complete is True
        assert confirmed_missing is False

    @patch("time.sleep")
    def test_confirmed_missing_on_404(self, mock_sleep):
        # A definitive 404 means arXiv has no HTML for this paper at all.
        session = MagicMock()
        session.get.return_value = _mock_response(status_ok=False, error_status=404)

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert html_text is None
        assert is_complete is False
        assert confirmed_missing is True
        assert html_url == "https://arxiv.org/html/1"

    @patch("time.sleep")
    def test_rate_limit_is_not_confirmed_missing(self, mock_sleep):
        # A 429 (or any non-404 HTTP error) is transient — must NOT be treated as
        # a permanent "no HTML exists", so a retry job gets a real chance later.
        session = MagicMock()
        session.get.return_value = _mock_response(status_ok=False, error_status=429)

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert html_text is None
        assert is_complete is False
        assert confirmed_missing is False

    @patch("time.sleep")
    def test_server_error_is_not_confirmed_missing(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _mock_response(status_ok=False, error_status=503)

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert html_text is None
        assert confirmed_missing is False

    @patch("time.sleep")
    def test_returns_incomplete_when_the_get_call_itself_raises(self, mock_sleep):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("network down")

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert html_text is None
        assert is_complete is False
        assert confirmed_missing is False
        # html_url is computed before the network call, so it's still returned on failure.
        assert html_url == "https://arxiv.org/html/1"

    @patch("time.sleep")
    def test_marks_incomplete_but_keeps_partial_content_when_stream_breaks_mid_download(self, mock_sleep):
        session = MagicMock()
        resp = _mock_response()

        def _iter_then_break(*a, **kw):
            yield b"<html>partial"
            raise ConnectionError("stream broke")

        resp.iter_content.side_effect = _iter_then_break
        session.get.return_value = resp

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert html_text == "<html>partial"
        assert is_complete is False
        assert confirmed_missing is False

    @patch("time.sleep")
    def test_truncates_at_max_bytes_without_flagging_incomplete(self, mock_sleep):
        # Current behavior: hitting the size cap breaks the read loop but does NOT
        # set is_complete=False (only a genuine stall/timeout/error does).
        session = MagicMock()
        resp = _mock_response()
        big_chunk = b"x" * (_HTML_MAX_BYTES + 1)
        resp.iter_content.return_value = iter([big_chunk])
        session.get.return_value = resp

        with patch("langtrend.html_processor._get_session", return_value=session):
            html_text, html_url, is_complete, confirmed_missing = fetch_arxiv_html("https://arxiv.org/abs/1")

        assert len(html_text) == _HTML_MAX_BYTES + 1
        assert is_complete is True
