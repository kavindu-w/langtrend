from __future__ import annotations

from pathlib import Path
from typing import Any

from .arxiv_pipeline import fetch_recent_arxiv_papers, save_jsonl
from .html_sections import recheck_languages_from_html
from .language_detection import flag_papers, load_language_data
from .manifest import build_snapshot_manifest, save_json


def run_snapshot(
    data_root: str | Path = "data",
    window_days: int = 7,
    max_results: int = 100,
    category_query: str = "cat:cs.CL",
    process_html_sections: bool = False,
) -> dict[str, Any]:
    root = Path(data_root)
    processed_root = root / "processed"
    raw_root = root / "raw"

    lang_data_path = processed_root / "language_data.json"
    lang_classes, languages_to_ignore = load_language_data(lang_data_path)

    papers = fetch_recent_arxiv_papers(
        window_days=window_days,
        max_results=max_results,
        category_query=category_query,
    )
    save_jsonl(papers, raw_root / f"arxiv_papers_last_{window_days}_days.jsonl")

    flagged = flag_papers(papers, lang_classes, languages_to_ignore)
    flagged_path = processed_root / f"papers_with_tracked_langs_last_{window_days}_days.jsonl"
    save_jsonl(flagged, flagged_path)

    if process_html_sections:
        for item in flagged:
            recheck_languages_from_html(item.get("paper", {}), lang_classes, languages_to_ignore)

    manifest = build_snapshot_manifest(
        papers=papers,
        flagged_papers=flagged,
        window_days=window_days,
        category_query=category_query,
    )
    save_json(manifest, processed_root / f"langtrend_manifest_last_{window_days}_days.json")
    return manifest
