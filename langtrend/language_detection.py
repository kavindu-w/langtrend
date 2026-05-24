from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


def load_language_data(path: str | Path) -> tuple[dict[int, set[str]], set[str]]:
    """Load the language class mapping and ignore list used by the notebook."""

    data_path = Path(path)
    with data_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)

    lang_classes = {int(key): set(values) for key, values in loaded["lang_classes"].items()}
    languages_to_ignore = set(loaded.get("languages_to_ignore", []))
    return lang_classes, languages_to_ignore


def _should_skip_language(language: str, languages_to_ignore: set[str]) -> bool:
    lower_ignored = {value.lower() for value in languages_to_ignore}
    return language in languages_to_ignore or language.lower() in lower_ignored


def scan_languages_in_text(
    title: str,
    abstract: str,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
) -> list[dict[str, Any]]:
    """Detect tracked language mentions in a paper title and abstract."""

    detected: list[dict[str, Any]] = []
    for class_id, languages in lang_classes.items():
        for language in languages:
            if not language or _should_skip_language(language, languages_to_ignore):
                continue
            pattern = r"\b" + re.escape(language) + r"\b"
            if re.search(pattern, abstract, re.IGNORECASE) or re.search(pattern, title, re.IGNORECASE):
                detected.append({"language": language, "class_id": class_id})

    return detected


def flag_papers(
    papers: list[dict[str, Any]],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
) -> list[dict[str, Any]]:
    """Attach detected language mentions to the papers that mention them."""

    flagged: list[dict[str, Any]] = []
    for paper in papers:
        detected = scan_languages_in_text(
            title=paper.get("title", ""),
            abstract=paper.get("abstract", ""),
            lang_classes=lang_classes,
            languages_to_ignore=languages_to_ignore,
        )
        if detected:
            flagged.append({"paper": paper, "languages": detected})

    return flagged


def count_languages(flagged_papers: list[dict[str, Any]]) -> Counter[str]:
    """Count language mentions across flagged papers."""

    counter: Counter[str] = Counter()
    for item in flagged_papers:
        for detected in item.get("languages", []):
            language = detected.get("language")
            if language:
                counter[language] += 1
    return counter
