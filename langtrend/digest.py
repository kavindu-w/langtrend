"""Render the weekly email digest from a snapshot manifest.

One email goes to every subscriber. It opens with the same summary for
everyone, followed by one block per language that had papers that week. Each
block is wrapped in a newsletter-provider conditional, so a subscriber only
sees the languages they picked. Addresses stay with the provider: this module
never sees who is subscribed, only which languages appeared this week.

Counting follows ``build_snapshot_manifest``: a detection with no verdict yet
counts as studied, ``mentioned_only`` is kept but listed separately, and
``false_positive`` is left out of the digest entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import quote

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")
# Cap per-language lists so a language like English (200+ papers a week) can't
# turn the email into a wall of links — the "see all" link covers the rest.
DEFAULT_MAX_PAPERS_PER_LANGUAGE = 10
# Mentioned-only papers get their own, smaller cap rather than whatever is
# left of the studied cap — otherwise a busy language lists none of them.
DEFAULT_MAX_MENTIONED_PER_LANGUAGE = 5
DEFAULT_TOP_LANGUAGES = 10


@dataclass
class DigestPaper:
    title: str
    url: str
    verdict: str  # "studied" (includes not-yet-judged) or "mentioned_only"


@dataclass
class LanguageSection:
    language: str
    studied: list[DigestPaper] = field(default_factory=list)
    mentioned_only: list[DigestPaper] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.studied) + len(self.mentioned_only)


def escape_text(text: str) -> str:
    """Escape paper-supplied text for Markdown *and* the provider's template engine.

    Titles come from arXiv authors, so they're untrusted: braces are turned
    into HTML entities so a title containing ``{{ ... }}`` or ``{% ... %}``
    can't be evaluated as a template tag, and Markdown metacharacters are
    backslash-escaped so they render literally.
    """
    text = " ".join(text.split())
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return text.replace("{", "&#123;").replace("}", "&#125;")


def format_week_range(week_start: str, week_end: str) -> str:
    start, end = date.fromisoformat(week_start), date.fromisoformat(week_end)
    if start.year != end.year:
        return f"{start.day} {_MONTHS[start.month - 1]} {start.year} – {end.day} {_MONTHS[end.month - 1]} {end.year}"
    if start.month != end.month:
        return f"{start.day} {_MONTHS[start.month - 1]} – {end.day} {_MONTHS[end.month - 1]} {end.year}"
    return f"{start.day}–{end.day} {_MONTHS[end.month - 1]} {end.year}"


def digest_subject(manifest: dict) -> str:
    # Also the dedup key the sender looks up before creating a new email, so
    # keep it deterministic for a given week.
    return f"LangTrend weekly digest: {format_week_range(manifest['week_start'], manifest['week_end'])}"


def _paper_url(paper: dict) -> str:
    return (paper.get("id") or "").replace("http://", "https://", 1)


def collect_language_sections(manifest: dict) -> list[LanguageSection]:
    """Group this week's non-false-positive detections by language.

    Sorted by total paper count (desc) then name, so the email mirrors the
    site's ordering; within a language, papers keep manifest order.
    """
    sections: dict[str, LanguageSection] = {}
    for item in manifest.get("flagged_papers", []):
        paper = item.get("paper", {})
        seen: set[str] = set()
        for entry in item.get("languages", []):
            name = entry.get("language") if isinstance(entry, dict) else entry
            verdict = entry.get("judge_verdict") if isinstance(entry, dict) else None
            if not name or name in seen or verdict == "false_positive":
                continue
            seen.add(name)
            section = sections.setdefault(name, LanguageSection(name))
            bucket = "mentioned_only" if verdict == "mentioned_only" else "studied"
            getattr(section, bucket).append(DigestPaper(paper.get("title", "Untitled"), _paper_url(paper), bucket))
    return sorted(sections.values(), key=lambda s: (-s.total, s.language))


def week_page_url(site_url: str, week_start: str, language: str | None = None) -> str:
    url = f"{site_url.rstrip('/')}/weeks/{week_start}/"
    return f"{url}?lang={quote(language, safe='')}" if language else url


def subscribe_page_url(site_url: str) -> str:
    # The site's own signup form (About page), which — unlike Buttondown's
    # hosted page linked from its template footer — has the language picker.
    return f"{site_url.rstrip('/')}/about/#subscribe"


# ── Provider template conditionals ────────────────────────────────────────────
# Buttondown templates are Django-based. Languages are read from subscriber
# *metadata*, not tags: on Buttondown's free plan `subscriber.tags` renders as
# an empty list (tagging is a paid add-on), while metadata is available. The
# signup form stores `metadata.languages` as ",Sinhala,Tamil," — delimited on
# both ends so `",Malay," in ...` can't match ",Malayalam,". Kept as small
# functions so switching providers only means supplying a different set.

@dataclass(frozen=True)
class TemplateDialect:
    if_language: Callable[[str], str]  # subscriber follows this language
    if_no_languages: str  # subscriber follows none (summary only)
    if_none_of: Callable[[list[str]], str]  # follows some, but none of these
    end_if: str


def usable_language_name(name: str) -> bool:
    # '"' would end the template string literal; ',' is the metadata delimiter.
    return bool(name) and not any(ch in name for ch in '",\n')


def _django_literal(language: str) -> str:
    if not usable_language_name(language):
        raise ValueError(f"language name can't be used in a template literal: {language!r}")
    return f'",{language},"'


_LANGS = "subscriber.metadata.languages"

BUTTONDOWN = TemplateDialect(
    if_language=lambda lang: f"{{% if {_django_literal(lang)} in {_LANGS} %}}",
    if_no_languages=f"{{% if not {_LANGS} %}}",
    # Guarded on the field being set so a summary-only subscriber gets the
    # if_no_languages note alone, not this one as well.
    if_none_of=lambda langs: f"{{% if {_LANGS} and " + " and ".join(f"{_django_literal(l)} not in {_LANGS}" for l in langs) + " %}",
    end_if="{% endif %}",
)


def _paper_lines(papers: list[DigestPaper], limit: int) -> list[str]:
    return [f"- [{escape_text(p.title)}]({p.url})" for p in papers[:limit]]


def render_language_block(
    section: LanguageSection,
    site_url: str,
    week_start: str,
    max_papers: int,
    max_mentioned: int = DEFAULT_MAX_MENTIONED_PER_LANGUAGE,
) -> list[str]:
    name = escape_text(section.language)
    lines = [f"### {name}", ""]
    for label, papers, limit in (("Studied", section.studied, max_papers), ("Mentioned only", section.mentioned_only, max_mentioned)):
        if papers:
            lines += [f"**{label}** ({len(papers)})", ""] + _paper_lines(papers, limit) + [""]
    link = week_page_url(site_url, week_start, section.language)
    shown = min(len(section.studied), max_papers) + min(len(section.mentioned_only), max_mentioned)
    more = f"all {section.total} papers" if section.total > shown else "these papers"
    lines += [f"[See {more} for {name} on LangTrend →]({link})", ""]
    return lines


_STATS_START = "<!-- LANGTREND_STATS_START -->"
_STATS_END = "<!-- LANGTREND_STATS_END -->"


def extract_run_summary(readme_text: str) -> str | None:
    """The README's "Latest Run Summary" block (this week + all-time table).

    Regenerated by scripts/update_readme_stats.py before every deploy — the
    same block the "site is live" push notification carries — so the email
    reuses it verbatim instead of recomputing the numbers. Braces are
    neutralised like any other text, since the block ends up inside a template.
    """
    match = re.search(re.escape(_STATS_START) + r"(.*?)" + re.escape(_STATS_END), readme_text, re.DOTALL)
    if not match or not match.group(1).strip():
        return None
    return match.group(1).strip().replace("{", "&#123;").replace("}", "&#125;")


def render_digest(
    manifest: dict,
    site_url: str,
    dialect: TemplateDialect = BUTTONDOWN,
    max_papers_per_language: int = DEFAULT_MAX_PAPERS_PER_LANGUAGE,
    top_languages: int = DEFAULT_TOP_LANGUAGES,
    run_summary: str | None = None,
) -> tuple[str, str]:
    """Return ``(subject, markdown_body)`` for the week in ``manifest``."""
    week_start = manifest["week_start"]
    counts = manifest.get("counts", {})
    # A name the template syntax can't quote would raise mid-render and sink
    # the whole week's email; drop just that language (none exist today).
    sections = [s for s in collect_language_sections(manifest) if usable_language_name(s.language)]
    week_url = week_page_url(site_url, week_start)

    lines = [
        f"## This week in cs.CL ({format_week_range(week_start, manifest['week_end'])})",
        "",
        f"- **{counts.get('papers', 0)}** papers scanned",
        f"- **{counts.get('flagged_papers', 0)}** papers with at least one detected language",
        f"- **{counts.get('unique_languages', 0)}** distinct languages studied"
        + (f", plus {counts['unique_languages_mentioned_only']} only mentioned" if counts.get("unique_languages_mentioned_only") else ""),
        "",
    ]

    def studied_count(row: dict) -> int:
        return row.get("studied", row.get("count", 0))

    # language_counts is ordered by total (studied + mentioned-only); re-sort
    # so "most-studied" really is ordered by the studied count it shows.
    top = sorted((r for r in manifest.get("language_counts", []) if studied_count(r)), key=lambda r: (-studied_count(r), r["language"]))[:top_languages]
    if top:
        lines += ["**Most-studied languages:** " + ", ".join(
            f"{escape_text(row['language'])} ({studied_count(row)})" for row in top
        ), ""]
    lines += [f"[Open this week's dashboard →]({week_url})", "", "---", "", "## Your languages", ""]

    for section in sections:
        lines.append(dialect.if_language(section.language))
        lines += render_language_block(section, site_url, week_start, max_papers_per_language)
        lines.append(dialect.end_if)

    lines.append(dialect.if_no_languages)
    lines += [
        "You're receiving the summary only. To also get papers for specific languages, "
        f"[pick them here]({subscribe_page_url(site_url)}) with this same email address.",
        "",
    ]
    lines.append(dialect.end_if)
    if sections:
        lines.append(dialect.if_none_of([s.language for s in sections]))
        lines += ["None of your languages appeared in cs.CL papers this week.", ""]
        lines.append(dialect.end_if)

    lines += [
        "",
        "---",
        "",
        "_Languages are detected automatically and checked by an LLM judge, so "
        "a few may be misclassified. Every detection links back to the paper._",
        "",
    ]
    if run_summary:
        # Outside every conditional: same numbers for every reader.
        lines += [run_summary, "", "---", ""]
    lines += [
        # Outside every conditional, so a forwarded copy shows it whatever the
        # original recipient's languages were.
        f"[Change your languages]({subscribe_page_url(site_url)}) by re-submitting the form with this address. "
        f"**Forwarded this email?** [Subscribe and pick the languages you follow →]({subscribe_page_url(site_url)})",
    ]
    return digest_subject(manifest), "\n".join(lines).strip() + "\n"
