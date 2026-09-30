"""
Unit tests for langtrend/digest.py.

Run with: pytest tests/test_digest.py -v
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.digest import (
    BUTTONDOWN,
    collect_language_sections,
    digest_subject,
    extract_run_summary,
    escape_text,
    format_week_range,
    render_digest,
    week_page_url,
)

SITE = "https://kavindu-w.github.io/langtrend"


def _paper(pid, title, *languages):
    return {
        "paper": {"id": f"http://arxiv.org/abs/{pid}", "title": title},
        "languages": [
            {"language": name, **({"judge_verdict": verdict} if verdict else {})}
            for name, verdict in languages
        ],
    }


@pytest.fixture
def manifest():
    return {
        "week_start": "2026-09-07",
        "week_end": "2026-09-14",
        "counts": {"papers": 50, "flagged_papers": 3, "unique_languages": 2, "unique_languages_mentioned_only": 1},
        "language_counts": [
            {"language": "English", "count": 3, "studied": 1, "mentioned_only": 2},
            {"language": "Sinhala", "count": 2, "studied": 2, "mentioned_only": 0},
        ],
        "flagged_papers": [
            _paper("2609.00001v1", "Sinhala NER", ("Sinhala", "studied"), ("English", "mentioned_only")),
            _paper("2609.00002v1", "Sinhala ASR", ("Sinhala", None), ("GAN", "false_positive")),
            _paper("2609.00003v1", "An English benchmark", ("English", "studied"), ("English", "studied")),
            _paper("2609.00004v1", "Another one", ("English", "mentioned_only")),
        ],
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-09-07", "2026-09-14", "7–14 Sep 2026"),
        ("2026-09-28", "2026-10-05", "28 Sep – 5 Oct 2026"),
        ("2026-12-28", "2027-01-04", "28 Dec 2026 – 4 Jan 2027"),
    ],
)
def test_format_week_range(start, end, expected):
    assert format_week_range(start, end) == expected


def test_subject_is_deterministic_per_week(manifest):
    assert digest_subject(manifest) == "LangTrend weekly digest: 7–14 Sep 2026"
    assert digest_subject(dict(manifest)) == digest_subject(manifest)


def test_escape_text_neutralises_template_tags_and_markdown():
    out = escape_text("Break {{ subscriber.email }} {% if x %} [link](evil) *bold*")
    assert "{" not in out and "}" not in out
    assert "&#123;&#123; subscriber.email &#125;&#125;" in out
    assert r"\[link\]" in out and r"\*bold\*" in out


def test_escape_text_collapses_whitespace():
    assert escape_text("Multi\n  line\ttitle") == "Multi line title"


def test_week_page_url_encodes_language():
    assert week_page_url(SITE, "2026-09-07") == f"{SITE}/weeks/2026-09-07/"
    assert week_page_url(SITE + "/", "2026-09-07", "N'Ko") == f"{SITE}/weeks/2026-09-07/?lang=N%27Ko"


# ---------------------------------------------------------------------------
# collect_language_sections
# ---------------------------------------------------------------------------

def test_sections_bucket_verdicts_and_drop_false_positives(manifest):
    sections = {s.language: s for s in collect_language_sections(manifest)}
    assert set(sections) == {"English", "Sinhala"}  # GAN false positive dropped
    assert [p.title for p in sections["Sinhala"].studied] == ["Sinhala NER", "Sinhala ASR"]  # unjudged counts as studied
    assert [p.title for p in sections["English"].mentioned_only] == ["Sinhala NER", "Another one"]
    assert [p.title for p in sections["English"].studied] == ["An English benchmark"]  # duplicate entry counted once


def test_sections_sorted_by_total_then_name(manifest):
    assert [s.language for s in collect_language_sections(manifest)] == ["English", "Sinhala"]


def test_paper_urls_use_https(manifest):
    section = collect_language_sections(manifest)[1]
    assert section.studied[0].url == "https://arxiv.org/abs/2609.00001v1"


# ---------------------------------------------------------------------------
# render_digest
# ---------------------------------------------------------------------------

def test_render_includes_summary_and_conditional_blocks(manifest):
    subject, body = render_digest(manifest, SITE)
    assert subject == digest_subject(manifest)
    assert "**50** papers scanned" in body
    assert "plus 1 only mentioned" in body
    assert "**Most-studied languages:** Sinhala (2), English (1)" in body  # ordered by studied, not total
    assert '{% if ",Sinhala," in subscriber.metadata.languages %}' in body
    assert f"({SITE}/weeks/2026-09-07/?lang=Sinhala)" in body
    assert body.count("{% if") == body.count("{% endif %}")


def test_render_links_to_site_signup_form_for_everyone(manifest):
    _, body = render_digest(manifest, SITE + "/")
    link = f"({SITE}/about/#subscribe)"
    assert link in body
    assert link in _as_subscriber(body, []) and link in _as_subscriber(body, ["Tamil"])


README = """intro
<!-- LANGTREND_STATS_START -->
## Latest Run Summary

| Metric | This week | All-time |
|--------|----------:|---------:|
| Papers scanned | 508 | 10,335 |
<!-- LANGTREND_STATS_END -->
outro"""


def test_extract_run_summary_takes_the_block_between_markers():
    block = extract_run_summary(README)
    assert block.startswith("## Latest Run Summary")
    assert block.endswith("| Papers scanned | 508 | 10,335 |")
    assert "intro" not in block and "outro" not in block


def test_extract_run_summary_missing_or_empty_markers():
    assert extract_run_summary("no markers here") is None
    assert extract_run_summary("<!-- LANGTREND_STATS_START -->\n\n<!-- LANGTREND_STATS_END -->") is None


def test_extract_run_summary_neutralises_template_braces():
    block = extract_run_summary("<!-- LANGTREND_STATS_START -->{{ x }}<!-- LANGTREND_STATS_END -->")
    assert block == "&#123;&#123; x &#125;&#125;"


def test_render_appends_run_summary_for_everyone(manifest):
    _, body = render_digest(manifest, SITE, run_summary=extract_run_summary(README))
    assert "| Papers scanned | 508 | 10,335 |" in body
    assert body.index("Latest Run Summary") > body.index("Your languages")  # at the end
    for tags in ([], ["Sinhala"], ["Tamil"]):
        assert "10,335" in _as_subscriber(body, tags)


def test_render_without_run_summary_omits_it(manifest):
    assert "Latest Run Summary" not in render_digest(manifest, SITE)[1]


def test_render_caps_papers_and_links_to_the_rest(manifest):
    manifest["flagged_papers"] += [_paper(f"2609.1{i:04d}v1", f"Extra {i}", ("Sinhala", "studied")) for i in range(5)]
    _, body = render_digest(manifest, SITE, max_papers_per_language=3)
    block = body.split("### Sinhala")[1].split("{% endif %}")[0]
    assert block.count("\n- [") == 3
    assert "See all 7 papers for Sinhala" in block


def test_render_with_no_detections_has_no_none_of_block():
    empty = {"week_start": "2026-09-07", "week_end": "2026-09-14", "counts": {}, "flagged_papers": []}
    _, body = render_digest(empty, SITE)
    assert "None of your languages" not in body
    assert "{% if not subscriber.metadata.languages %}" in body


def test_dialect_rejects_unquotable_tag():
    with pytest.raises(ValueError):
        BUTTONDOWN.if_language('Bad "name"')
    with pytest.raises(ValueError):
        BUTTONDOWN.if_language("Comma, name")


# ---------------------------------------------------------------------------
# What each subscriber actually sees. Buttondown's templates are Django; the
# if/in/not in/and constructs used here are spelled and behave identically in
# Jinja2, which is already a dependency, so it stands in as the renderer.
# ---------------------------------------------------------------------------

jinja2 = pytest.importorskip("jinja2")


def _as_subscriber(body, languages):
    # Mirrors what the signup form stores (see web/src/lib/subscribe-form.js).
    value = f",{','.join(languages)}," if languages else ""
    return jinja2.Environment().from_string(body).render(subscriber={"metadata": {"languages": value}})


def test_subscriber_without_metadata_field_gets_summary_only(manifest):
    # Subscribers who signed up before the metadata field existed.
    seen = jinja2.Environment().from_string(render_digest(manifest, SITE)[1]).render(subscriber={"metadata": {}})
    assert "summary only" in seen and "### " not in seen


def test_prefix_named_language_does_not_match(manifest):
    manifest["flagged_papers"].append(_paper("2609.7v1", "Malay NER", ("Malay", "studied")))
    seen = _as_subscriber(render_digest(manifest, SITE)[1], ["Malayalam"])
    assert "### Malay" not in seen and "None of your languages appeared" in seen


def test_subscriber_sees_only_their_languages(manifest):
    _, body = render_digest(manifest, SITE)
    seen = _as_subscriber(body, ["Sinhala"])
    assert "### Sinhala" in seen and "### English" not in seen
    assert "summary only" not in seen and "None of your languages" not in seen


def test_subscriber_with_multiple_languages_sees_each(manifest):
    seen = _as_subscriber(render_digest(manifest, SITE)[1], ["Sinhala", "English"])
    assert "### Sinhala" in seen and "### English" in seen


def test_summary_only_subscriber_gets_one_note(manifest):
    seen = _as_subscriber(render_digest(manifest, SITE)[1], [])
    assert "### " not in seen
    assert "summary only" in seen
    assert "None of your languages" not in seen
    assert "papers scanned" in seen


def test_subscriber_whose_languages_did_not_appear(manifest):
    seen = _as_subscriber(render_digest(manifest, SITE)[1], ["Tamil"])
    assert "None of your languages appeared" in seen
    assert "### " not in seen


def test_malicious_title_is_not_evaluated(manifest):
    manifest["flagged_papers"].append(_paper("2609.9v1", "{{ subscriber.tags }} leak", ("Sinhala", "studied")))
    seen = _as_subscriber(render_digest(manifest, SITE)[1], ["Sinhala"])
    assert "['Sinhala'] leak" not in seen
    assert "&#123;&#123; subscriber.tags &#125;&#125; leak" in seen
