from __future__ import annotations

import json
import random
import re
import threading
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from .text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text, extract_paper_acronyms, find_language_acronym_conflicts

# One Session per thread — reuses TCP connections within each worker
_thread_local = threading.local()

# Cap concurrent arXiv HTML requests regardless of worker count
_ARXIV_SEMAPHORE = threading.Semaphore(1)
_HTML_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — enough for any paper's HTML
_HTML_DOWNLOAD_TIMEOUT = 120  # wall-clock cap; arXiv keepalives defeat per-chunk read timeouts

# Honest bot UA — arXiv's access policy asks automated tools to self-identify with contact info.
# Update the email if you deploy this under a different project.
ARXIV_USER_AGENT = (
    "LangTrendBot (Automated Research Tool); "
    "https://github.com/kavindu-w/langtrend; "
    "akwarnakulasuriya@gmail.com)"
)


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update({
            "User-Agent": ARXIV_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
        })
        _thread_local.session = s
    return _thread_local.session

_REMOVE_HEADINGS_DEFAULT = [
    "Abstract",
    "References",
    "Bibliography",
    "Related work",
    "Related Work",
    "Related Works",
    "Bibliographical References",
    "Literature Review",
    "Acknowledgements",
    "Acknowledgement",
    "Acknowledgments",
    "Acknowledgment",
    "Funding",
    "Ethics",
    "Ethics Statement",
    # Non-Latin transliterations of "References" that appear in arXiv HTML
    # for papers with non-ASCII section headings (e.g. Greek LLM papers).
    "Ρεφερενςες",  # Greek transliteration of "References"
]


def is_removable_heading(title: str, remove_headings: list[str] | None = None) -> bool:
    """True if a section heading names an excluded section (substring, case-insensitive).

    Single source of truth for the section-exclusion rule. Applied at fetch time
    in recheck_languages_from_html, and replicated by the cache-reprocess path so
    a reprocess drops exactly the sections a fresh fetch does — References,
    Related Work (including combined "Background and Related Work" and appendix
    variants), Acknowledgements, Abstract, etc. Substring (not startswith) is
    intentional: it catches arXiv's glued numbering ("IIRelated Work") and
    headings where the excluded term is not first ("Background and Related Work").
    """
    if not title:
        return False
    headings = _REMOVE_HEADINGS_DEFAULT if remove_headings is None else remove_headings
    return re.search("|".join(re.escape(h) for h in headings), title, re.IGNORECASE) is not None


def fetch_arxiv_html(abs_url: str, timeout: int = 30) -> tuple[str | None, str | None, bool]:
    """Fetch arXiv HTML for a paper.

    Returns (html_text, html_url, is_complete).
    is_complete=False means the download stalled mid-transfer (partial content) or failed entirely.
    """
    import time as _time
    if not abs_url:
        return None, None, False
    html_url = abs_url.replace("/abs/", "/html/")
    t0 = _time.monotonic()
    try:
        with _ARXIV_SEMAPHORE:
            _time.sleep(3)
        t0 = _time.monotonic()
        print(f"    [{abs_url}] GET started…", flush=True)
        response = _get_session().get(html_url, timeout=(5, 15), stream=True)
        response.raise_for_status()
        chunks = []
        total = 0
        is_complete = True
        _LOG_EVERY = 256 * 1024
        last_logged = 0
        try:
            for chunk in response.iter_content(chunk_size=65536):
                chunks.append(chunk)
                total += len(chunk)
                if total - last_logged >= _LOG_EVERY:
                    elapsed = _time.monotonic() - t0
                    print(f"    [{abs_url}] downloading… {total // 1024}KB in {elapsed:.1f}s", flush=True)
                    last_logged = total
                if total >= _HTML_MAX_BYTES:
                    print(f"    [{abs_url}] HTML truncated at {_HTML_MAX_BYTES // 1024}KB", flush=True)
                    break
                elapsed = _time.monotonic() - t0
                if elapsed > _HTML_DOWNLOAD_TIMEOUT:
                    is_complete = False
                    print(f"    [{abs_url}] HTML wall-clock timeout after {elapsed:.1f}s at {total // 1024}KB — aborting", flush=True)
                    break
        except Exception as chunk_err:
            elapsed = _time.monotonic() - t0
            is_complete = False
            print(f"    [{abs_url}] download stalled at {total // 1024}KB after {elapsed:.1f}s ({type(chunk_err).__name__})", flush=True)
        html_text = b''.join(chunks).decode('utf-8', errors='replace') if chunks else None
        elapsed = _time.monotonic() - t0
        print(f"    [{abs_url}] response {response.status_code} in {elapsed:.1f}s ({total} bytes, complete={is_complete})", flush=True)
        return html_text, html_url, is_complete
    except Exception as e:
        elapsed = _time.monotonic() - t0
        print(f"    [{abs_url}] HTML fetch failed after {elapsed:.1f}s: {type(e).__name__}: {e}", flush=True)
        return None, html_url, False


def _remove_section_by_heading(soup: BeautifulSoup, heading_texts: list[str]) -> None:
    """Remove content following matching heading tags (h1–h6) and whole <section> blocks."""
    for h in soup.find_all(re.compile("^h[1-6]$")):
        raw = h.get_text().strip().lower()
        text = re.sub(r"^\d[\d.]*[\s.]+", "", raw)  # strip leading "6." / "6.1." numbering
        for target in heading_texts:
            if text.startswith(target.lower()):
                nxt = h.next_sibling
                try:
                    h.decompose()
                except Exception:
                    pass
                while nxt:
                    cur = nxt
                    nxt = nxt.next_sibling
                    if getattr(cur, "name", None) and re.match("^h[1-6]$", cur.name or ""):
                        break
                    try:
                        cur.decompose()
                    except Exception:
                        pass
                break

    for sec in soup.find_all("section"):
        h = sec.find(re.compile("^h[1-6]$"))
        if h:
            raw = h.get_text().strip().lower()
            title = re.sub(r"^\d[\d.]*[\s.]+", "", raw)  # strip leading "6." / "6.1." numbering
            for target in heading_texts:
                if title.startswith(target.lower()):
                    try:
                        sec.decompose()
                    except Exception:
                        pass
                    break


def clean_html_soup(html: str, remove_headings: list[str] | None = None, _label: str = "") -> BeautifulSoup:
    import time as _time
    _t = _time.monotonic()
    def _tick(step: str) -> None:
        print(f"    [{_label}] {step} in {_time.monotonic()-_t:.1f}s", flush=True)

    size_kb = len(html) // 1024
    print(f"    [{_label}] parsing {size_kb}KB…", flush=True)
    soup = BeautifulSoup(html, "lxml")
    _tick(f"parsed {size_kb}KB")

    for selector in [("blockquote", "abstract"), ("div", "abstract"), ("div", "abstract-full")]:
        tag = soup.find(selector[0], class_=selector[1])
        if tag:
            try:
                tag.decompose()
            except Exception:
                pass
    _tick("removed abstracts")

    if remove_headings:
        _remove_section_by_heading(soup, remove_headings)
    _tick("removed headings")

    for tag_name in ["script", "style", "nav", "footer", "header", "aside"]:
        for tag in soup.find_all(tag_name):
            try:
                tag.decompose()
            except Exception:
                pass
    _tick("removed boilerplate tags")

    # arXiv emits <span class="ltx_ERROR undefined">\tera</span> (and similar) when a
    # LaTeX command fails to render (e.g. siunitx \tera, \giga, \mega).  The raw
    # command text leaks into body text and matches language names ("Tera", "Giga" …).
    for tag in soup.find_all("span", class_="ltx_ERROR"):
        try:
            tag.decompose()
        except Exception:
            pass
    _tick("removed ltx_ERROR spans")

    # arXiv renders bibliography entries in a <section class="ltx_bibliography"> that is
    # a sibling of the "References" heading section, not a child — so _remove_section_by_heading
    # only removes the heading section and leaves this one intact.  Remove it explicitly.
    for tag in soup.find_all("section", class_="ltx_bibliography"):
        try:
            tag.decompose()
        except Exception:
            pass
    _tick("removed ltx_bibliography sections")

    # arXiv HTML wraps math in <math><semantics>...<annotation encoding="application/x-tex">
    # Subscript/superscript blocks (msub/msup/msubsup) concatenate child letters via
    # get_text() into garbled tokens (e.g. <mi>i</mi><mi>k</mi> → "ik", falsely matching
    # Inupiaq). Replace those blocks entirely with a space. For all other math elements,
    # just strip the annotation to prevent LaTeX source duplication (the original fix).
    n_math_sub = 0
    n_annotations = 0
    for tag in soup.find_all("math"):
        if tag.find(["msub", "msup", "msubsup"]):
            try:
                tag.replace_with(" ")
                n_math_sub += 1
            except Exception:
                pass
        else:
            for ann in tag.find_all("annotation"):
                try:
                    ann.decompose()
                    n_annotations += 1
                except Exception:
                    pass
    _tick(f"removed {n_math_sub} subscript/superscript math blocks, stripped {n_annotations} annotations")

    return soup


_TEXT_TAGS = ["p", "div", "figcaption", "caption"]
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _walk_document(element, on_text, on_heading=None, skip_ids=frozenset()) -> None:
    """Recursively walk `element`'s subtree in document order, calling
    on_text(str) once per paragraph-text unit found.

    A p/div/figcaption/caption tag becomes its own unit — UNLESS it itself
    contains a nested p/div/figcaption/caption match anywhere in its
    subtree, in which case we recurse into it instead of taking its
    get_text() directly (the nested match becomes its own unit via that
    recursion, so it isn't duplicated). Any other tag (e.g. <figure>,
    <table>, <tr>, <td>, <span>, and h1-h6 not in `skip_ids`) is treated as
    a transparent wrapper and always recursed into, so stray text mixed
    anywhere among matches — no matter how many non-matching tags deep — is
    still found and not silently dropped just because a sibling or cousin
    element happened to contain a match. Loose text between/around matches
    at the same level is buffered and flushed (in place) right before the
    next match, preserving document order.

    h1-h6 tags are NOT excluded as a blanket rule: arXiv's LaTeXML export
    also uses <h6 class="ltx_title_theorem"> for in-body theorem/
    proposition/definition run-in titles, which are real content, not
    structural section headings. Only the specific heading instance(s) in
    `skip_ids` (by `id()`) — the one already used as a section's `title` —
    are excluded. If `on_heading` is given, any *other* h1-h6 encountered
    calls on_heading(tag) instead of being descended into (used by the
    no-<section> fallback to start a new section per heading).
    """
    buffer: list[str] = []

    def flush():
        text = re.sub(r"\s+", " ", "".join(buffer)).strip()
        buffer.clear()
        if text:
            on_text(text)

    def walk(node):
        for child in node.children:
            if isinstance(child, Tag):
                if id(child) in skip_ids:
                    flush()
                elif on_heading is not None and child.name in _HEADING_TAGS:
                    flush()
                    on_heading(child)
                elif child.name in _TEXT_TAGS:
                    if child.find(_TEXT_TAGS):
                        flush()
                        walk(child)
                    else:
                        flush()
                        text = re.sub(r"\s+", " ", child.get_text(separator="", strip=False)).strip()
                        if text:
                            on_text(text)
                else:
                    walk(child)
            else:
                buffer.append(str(child))

    walk(element)
    flush()


def extract_sections_from_soup(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}

    for section in soup.find_all("section"):
        heading = section.find(re.compile("^h[1-6]$"))
        title = heading.get_text(strip=True) if heading else section.get("id") or "section"

        paragraphs: list[str] = []
        skip_ids = {id(heading)} if heading is not None else frozenset()
        _walk_document(section, on_text=paragraphs.append, skip_ids=skip_ids)

        if not paragraphs:
            paragraphs = [section.get_text(separator=" ", strip=True)]
        sections[title] = "\n\n".join(paragraphs).strip()

    # Fallback: split by heading tags if no <section> elements found
    if not sections:
        current_title = "body"
        current_texts: list[str] = []

        def handle_heading(tag):
            nonlocal current_title
            if current_texts:
                sections[current_title] = "\n\n".join(current_texts).strip()
                current_texts.clear()
            current_title = tag.get_text(strip=True)

        _walk_document(soup, on_text=current_texts.append, on_heading=handle_heading)
        if current_texts:
            sections[current_title] = "\n\n".join(current_texts).strip()

    # Final fallback: full body text
    if not sections:
        body = soup.get_text("\n")
        sections["body"] = re.sub(r"\n{2,}", "\n\n", body).strip()

    return sections


def extract_sections_from_html(html: str) -> dict[str, str]:
    return extract_sections_from_soup(clean_html_soup(html))


def recheck_languages_from_html(
    paper_record: dict[str, Any],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    out_dir: Path | None = None,
    remove_headings: list[str] | None = None,
) -> tuple[dict[str, list[str]], bool, list[dict]]:
    """Fetch arXiv HTML, extract sections, run language detection per section.

    Returns (detections, is_html_complete) where:
    - detections maps section title -> list of detected language strings
    - is_html_complete=False means the HTML was a stalled/partial download

    Saves a detailed JSON file to out_dir. Returns ({}, False) if HTML unavailable.
    """
    if remove_headings is None:
        remove_headings = _REMOVE_HEADINGS_DEFAULT

    paper_id = paper_record.get("id") or paper_record.get("pdf_url") or paper_record.get("url")
    if not paper_id:
        return {}, False

    if out_dir is None:
        out_dir = Path("data/processed/weeks/latest/html_cache")
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = str(paper_id).split("/")[-1] or paper_record.get("title", "paper").replace(" ", "_")[:60]
    json_path = out_dir / f"{safe_name}.json"
    html_path = out_dir / f"{safe_name}.html"

    # Return cached result if already processed AND complete. An incomplete
    # cache (_complete=False, a stalled/partial download) is deliberately NOT
    # returned early — a fresh fetch may succeed this time, whereas re-serving
    # the same partial content forever defeats the purpose of retrying it.
    known_incomplete = False
    if json_path.exists():
        try:
            with json_path.open("r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("_unavailable"):
                return {}, False, []  # HTML was tried before and not available — skip
            is_complete = cached.get("_complete", True)  # older caches pre-date this field → assume complete
            if is_complete:
                conflicts = cached.get("_acronym_conflicts", [])
                sections_data = {k: v for k, v in cached.items() if not k.startswith("_")}
                return {title: data.get("detected", []) for title, data in sections_data.items()}, is_complete, conflicts
            known_incomplete = True  # fall through to retry the fetch below
        except Exception:
            pass  # fall through to re-fetch if cache is corrupt

    import time as _time

    # A cached raw .html file is only trustworthy if we don't already know (from
    # the json sidecar above) that it's a stalled/partial download — otherwise
    # this would just re-serve the same incomplete content instead of retrying,
    # the same bug the json_path check above just guarded against.
    if html_path.exists() and not known_incomplete:
        html = html_path.read_text(encoding="utf-8")
        is_complete = True
        print(f"  [{paper_id}] loaded HTML from cache ({len(html) // 1024}KB)", flush=True)
    else:
        html, _html_url, is_complete = fetch_arxiv_html(str(paper_id))
        if not html:
            # Write a sentinel so retry-missing knows HTML was tried and unavailable,
            # preventing a redundant re-fetch on every future retry run.
            try:
                with json_path.open("w", encoding="utf-8") as fh:
                    json.dump({"_complete": False, "_unavailable": True}, fh)
            except Exception:
                pass
            return {}, False, []
        try:
            html_path.write_text(html, encoding="utf-8")
        except Exception:
            pass

    t1 = _time.monotonic()
    soup = clean_html_soup(html, remove_headings, _label=str(paper_id))
    print(f"  [{paper_id}] clean_html_soup done in {_time.monotonic()-t1:.1f}s", flush=True)

    t2 = _time.monotonic()
    sections = extract_sections_from_soup(soup)
    print(f"  [{paper_id}] extract_sections done in {_time.monotonic()-t2:.1f}s ({len(sections)} sections)", flush=True)

    detections_per_section: dict[str, list[str]] = {title: [] for title in sections}
    cleaned_texts_per_section: dict[str, str] = {}

    # Build paper-level acronym set from all sections at once so that an acronym
    # defined in the Introduction (e.g. "Generative Adversarial Network (GAN)") is
    # also stripped from the Method and Experiment sections.
    paper_acronyms = extract_paper_acronyms("\n\n".join(sections.values()))
    # Flag any acronym that shadows a real language name so the frontend can warn.
    acronym_conflicts = find_language_acronym_conflicts(paper_acronyms, lang_classes, languages_to_ignore)

    t3 = _time.monotonic()
    for title, text in sections.items():
        if is_removable_heading(title, remove_headings):
            continue

        cleaned_blocks, _ = clean_paper_text_for_language_screening(text, paper_acronyms=paper_acronyms)
        cleaned_texts_per_section[title] = "\n\n".join(cleaned_blocks)
        if not cleaned_blocks:
            continue

        detected = detect_languages_in_text(
            [title] + cleaned_blocks, lang_classes, languages_to_ignore, paper_id=str(paper_id)
        )
        if detected:
            detections_per_section[title] = detected
    print(f"  [{paper_id}] language detection done in {_time.monotonic()-t3:.1f}s", flush=True)

    payload: dict[str, Any] = {"_complete": is_complete}
    if acronym_conflicts:
        payload["_acronym_conflicts"] = acronym_conflicts
    payload.update({
        title: {
            "text": text,
            "cleaned_text": cleaned_texts_per_section.get(title, ""),
            "detected": detections_per_section.get(title, []),
        }
        for title, text in sections.items()
    })
    try:
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return detections_per_section, is_complete, acronym_conflicts
