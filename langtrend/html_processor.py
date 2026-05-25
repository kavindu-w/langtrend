from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from .text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text

# One Session per thread — reuses TCP connections within each worker
_thread_local = threading.local()

# Cap concurrent arXiv HTML requests regardless of worker count
_ARXIV_SEMAPHORE = threading.Semaphore(6)


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session()
    return _thread_local.session

_REMOVE_HEADINGS_DEFAULT = [
    "Abstract",
    "References",
    "Bibliography",
    "Related work",
    "Related Work",
    "Related Works",
    "Literature Review",
    "Acknowledgements",
    "Acknowledgement",
    "Acknowledgments",
    "Acknowledgment",
    "Funding",
    "Ethics",
    "Ethics Statement",
]


def fetch_arxiv_html(abs_url: str, timeout: int = 30) -> tuple[str | None, str | None]:
    if not abs_url:
        return None, None
    html_url = abs_url.replace("/abs/", "/html/")
    try:
        with _ARXIV_SEMAPHORE:
            response = _get_session().get(html_url, timeout=timeout)
        response.raise_for_status()
        return response.text, html_url
    except Exception:
        return None, html_url


def _remove_section_by_heading(soup: BeautifulSoup, heading_texts: list[str]) -> None:
    """Remove content following matching heading tags (h1–h6) and whole <section> blocks."""
    for h in soup.find_all(re.compile("^h[1-6]$")):
        text = h.get_text().strip().lower()
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
            title = h.get_text().strip().lower()
            for target in heading_texts:
                if title.startswith(target.lower()):
                    try:
                        sec.decompose()
                    except Exception:
                        pass
                    break


def clean_html_soup(html: str, remove_headings: list[str] | None = None) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")

    for selector in [("blockquote", "abstract"), ("div", "abstract"), ("div", "abstract-full")]:
        tag = soup.find(selector[0], class_=selector[1])
        if tag:
            try:
                tag.decompose()
            except Exception:
                pass

    if remove_headings:
        _remove_section_by_heading(soup, remove_headings)

    for tag_name in ["script", "style", "nav", "footer", "header", "aside"]:
        for tag in soup.find_all(tag_name):
            try:
                tag.decompose()
            except Exception:
                pass

    # arXiv HTML wraps math in <math><semantics>...<annotation encoding="application/x-tex">
    # The annotation contains the LaTeX source, which get_text() picks up and concatenates
    # with the MathML display text, producing artifacts like "UtU_{t}" from U_t.
    # Remove annotations before text extraction to keep only the display rendering.
    for tag in soup.find_all("annotation"):
        try:
            tag.decompose()
        except Exception:
            pass

    return soup


def extract_sections_from_soup(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}

    for section in soup.find_all("section"):
        heading = section.find(re.compile("^h[1-6]$"))
        title = heading.get_text(strip=True) if heading else section.get("id") or "section"

        paragraphs: list[str] = []
        for element in section.find_all(["p", "div"]):
            text = re.sub(r"\s+", " ", element.get_text(separator="", strip=False)).strip()
            if not text:
                continue
            if re.match(r"^[A-Z][\w\s\-:,]{0,100}$", text) and len(text.split()) < 6 and text.endswith(":"):
                continue
            paragraphs.append(text)

        if not paragraphs:
            paragraphs = [section.get_text(separator=" ", strip=True)]
        sections[title] = "\n\n".join(paragraphs).strip()

    # Fallback: split by heading tags if no <section> elements found
    if not sections:
        current_title = "body"
        current_texts: list[str] = []
        for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div"]):
            if node.name and re.match("^h[1-6]$", node.name):
                if current_texts:
                    sections[current_title] = "\n\n".join(current_texts).strip()
                current_title = node.get_text(strip=True)
                current_texts = []
            else:
                txt = re.sub(r"\s+", " ", node.get_text(separator="", strip=False)).strip()
                if txt:
                    current_texts.append(txt)
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
) -> dict[str, list[str]]:
    """Fetch arXiv HTML, extract sections, run language detection per section.

    Returns dict mapping section title -> list of detected language strings.
    Saves a detailed JSON file to out_dir (or data/processed/html_cache/ by default).
    Returns empty dict if HTML is unavailable.
    """
    if remove_headings is None:
        remove_headings = _REMOVE_HEADINGS_DEFAULT

    paper_id = paper_record.get("id") or paper_record.get("pdf_url") or paper_record.get("url")
    if not paper_id:
        return {}

    if out_dir is None:
        out_dir = Path("data/processed/html_cache")
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = str(paper_id).split("/")[-1] or paper_record.get("title", "paper").replace(" ", "_")[:60]
    json_path = out_dir / f"{safe_name}.json"

    # Return cached result if already processed
    if json_path.exists():
        try:
            with json_path.open("r", encoding="utf-8") as fh:
                cached = json.load(fh)
            return {title: data.get("detected", []) for title, data in cached.items()}
        except Exception:
            pass  # fall through to re-fetch if cache is corrupt

    html, _html_url = fetch_arxiv_html(str(paper_id))
    if not html:
        return {}

    soup = clean_html_soup(html, remove_headings)
    sections = extract_sections_from_soup(soup)

    detections_per_section: dict[str, list[str]] = {title: [] for title in sections}
    cleaned_texts_per_section: dict[str, str] = {}

    for title, text in sections.items():
        if re.search("|".join(re.escape(h) for h in remove_headings), title, re.IGNORECASE):
            continue

        cleaned_blocks, _ = clean_paper_text_for_language_screening(text)
        cleaned_texts_per_section[title] = "\n\n".join(cleaned_blocks)
        if not cleaned_blocks:
            continue

        detected = detect_languages_in_text(
            [title] + cleaned_blocks, lang_classes, languages_to_ignore, paper_id=str(paper_id)
        )
        if detected:
            detections_per_section[title] = detected

    payload = {
        title: {
            "text": text,
            "cleaned_text": cleaned_texts_per_section.get(title, ""),
            "detected": detections_per_section.get(title, []),
        }
        for title, text in sections.items()
    }
    try:
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return detections_per_section
