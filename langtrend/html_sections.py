from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


def fetch_arxiv_html(abs_url: str, timeout: int = 30) -> tuple[str | None, str | None]:
    if not abs_url:
        return None, None

    html_url = abs_url.replace("/abs/", "/html/")
    try:
        response = requests.get(html_url, timeout=timeout)
        response.raise_for_status()
        return response.text, html_url
    except Exception:
        return None, html_url


def clean_html_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")

    for selector in [("blockquote", "abstract"), ("div", "abstract"), ("div", "abstract-full")]:
        tag = soup.find(selector[0], class_=selector[1])
        if tag:
            tag.decompose()

    for tag_name in ["script", "style", "nav", "footer", "header", "aside"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    return soup


def extract_sections_from_soup(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}

    for section in soup.find_all("section"):
        heading = section.find(re.compile("^h[1-6]$"))
        title = heading.get_text(strip=True) if heading else section.get("id") or "section"

        paragraphs: list[str] = []
        for element in section.find_all(["p", "div"]):
            text = element.get_text(separator=" ", strip=True)
            if text:
                paragraphs.append(text)

        if not paragraphs:
            paragraphs = [section.get_text(separator=" ", strip=True)]

        sections[title] = "\n\n".join(paragraphs).strip()

    if not sections:
        body_text = soup.get_text("\n")
        sections["body"] = re.sub(r"\n{2,}", "\n\n", body_text).strip()

    return sections


def extract_sections_from_html(html: str) -> dict[str, str]:
    return extract_sections_from_soup(clean_html_soup(html))


def recheck_languages_from_html(
    paper_record: dict[str, Any],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Re-scan an arXiv HTML page and map language detections to sections."""

    paper_id = paper_record.get("id") or paper_record.get("pdf_url") or paper_record.get("url")
    html, html_url = fetch_arxiv_html(str(paper_id) if paper_id else "")
    if not html:
        return {}

    sections = extract_sections_from_html(html)
    detections: dict[str, list[dict[str, Any]]] = {title: [] for title in sections}
    ignored = {value.lower() for value in languages_to_ignore}

    for title, text in sections.items():
        if re.search(r"reference|related work|related works|abstract|acknowledg", title, re.IGNORECASE):
            continue

        for class_id, languages in lang_classes.items():
            for language in languages:
                if not language or language in languages_to_ignore or language.lower() in ignored:
                    continue
                pattern = r"\b" + re.escape(language) + r"\b"
                if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, paper_record.get("title", ""), re.IGNORECASE):
                    detections[title].append({"language": language, "class_id": class_id})

    out_dir = Path("data/processed/html_sections")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = str(paper_id).split("/")[-1] if paper_id else paper_record.get("title", "paper").replace(" ", "_")[:60]
    out_path = out_dir / f"{safe_name}.json"

    payload = {
        title: {"text": text, "detected": detections.get(title, [])}
        for title, text in sections.items()
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    return detections
