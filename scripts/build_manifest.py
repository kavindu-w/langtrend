#!/usr/bin/env python3
"""
Build the LangTrend manifest from existing data caches.

Reads:
  - data/raw/extracted_papers_metadata/*.jsonl              — paper metadata + abstracts
  - data/processed/weeks/YYYYMMDD_to_YYYYMMDD/html_cache/  — HTML section detections
  - data/processed/weeks/YYYYMMDD_to_YYYYMMDD/pdf_cache/   — PDF body detections
  - data/processed/language_data.json                       — language class definitions

Writes:
  - data/processed/weeks/YYYYMMDD_to_YYYYMMDD/langtrend_manifest.json  (week archive)
  - data/processed/langtrend_manifest_last_7_days.json                  (latest pointer)

Privacy: only paper metadata, abstracts (public on arXiv), and language detection results
are written to the manifest. All raw HTML/PDF text content stays in the local caches.

Usage:
    python scripts/build_manifest.py
    python scripts/build_manifest.py --input data/raw/extracted_papers_metadata/arxiv_papers_20260518_to_20260525.jsonl
    python scripts/build_manifest.py --input <file.jsonl> --window-days 7
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.manifest import build_detections, build_snapshot_manifest, save_json
from langtrend.text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_LANG_DATA = _PROJECT_ROOT / "data/processed/language_data.json"
_DEFAULT_PROCESSED_DIR = _PROJECT_ROOT / "data/processed"
_METADATA_DIR = _PROJECT_ROOT / "data/raw/extracted_papers_metadata"

# Regex to parse week dates from filenames like arxiv_papers_20260518_to_20260525.jsonl
_WEEK_RE = re.compile(r"(\d{8})_to_(\d{8})")


def _iso_date(raw: str) -> str:
    """Convert 20260518 → 2026-05-18."""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _find_latest_input() -> Path | None:
    """Return the most recently modified JSONL in the metadata directory."""
    candidates = sorted(_METADATA_DIR.glob("arxiv_papers_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _load_papers(jsonl_path: Path) -> list[dict]:
    papers = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    papers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return papers


def _load_language_data(path: Path) -> tuple[dict[int, set[str]], set[str], dict[str, str]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    lang_classes = {int(k): set(v) for k, v in data["lang_classes"].items()}
    languages_to_ignore = set(data["languages_to_ignore"])
    possible_false_positives: dict[str, str] = data.get("possible_false_positive_languages", {})
    return lang_classes, languages_to_ignore, possible_false_positives


def _scan_abstract(
    paper: dict,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positives: dict[str, str],
) -> list[dict]:
    abstract = paper.get("abstract", "")
    if not abstract:
        return []
    paper_id = str(paper.get("id", ""))
    blocks, _ = clean_paper_text_for_language_screening(abstract)
    if not blocks:
        return []
    raw = detect_languages_in_text(blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
    return build_detections(raw, lang_classes, possible_false_positives, languages_to_ignore)


def _load_html_detections(
    safe_id: str,
    html_cache_dir: Path,
    lang_classes: dict[int, set[str]],
    possible_false_positives: dict[str, str],
    languages_to_ignore: set[str] | None = None,
) -> tuple[list[dict], list[str]] | tuple[None, list]:
    """Return (flat detections list, sections that had hits) or (None, []) if not cached."""
    path = html_cache_dir / f"{safe_id}.json"
    if not path.exists():
        return None, []
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None, []

    all_dets: list[dict] = []
    sections_hit: list[str] = []
    for section_name, section_data in data.items():
        detected_strings: list[str] = section_data.get("detected", [])
        if detected_strings:
            dets = build_detections(
                detected_strings, lang_classes, possible_false_positives, languages_to_ignore
            )
            if dets:
                all_dets.extend(dets)
                sections_hit.append(section_name)
    return all_dets, sections_hit


def _load_pdf_detections(
    safe_id: str,
    pdf_cache_dir: Path,
    languages_to_ignore: set[str] | None = None,
) -> list[dict] | None:
    """Return detection objects from pdf_cache, or None if not cached.
    Strips any text-content fields before returning."""
    path = pdf_cache_dir / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    dets = data.get("detected_languages", [])
    if languages_to_ignore:
        ignore_lower = {v.lower() for v in languages_to_ignore}
        dets = [d for d in dets if d.get("language", "") not in languages_to_ignore
                and d.get("language", "").lower() not in ignore_lower]
    return dets


def _merge_detections(groups: list[tuple[list[dict], str]]) -> list[dict]:
    """Deduplicate by (language, class), merging source lists.

    Each group is (detections_list, source_name). Within a paper a language is
    counted once regardless of how many sections or sources detected it.
    """
    merged: dict[tuple, dict] = {}
    for dets, source in groups:
        for det in dets:
            lang = det.get("language")
            cls = det.get("class")
            if not lang:
                continue
            key = (lang, cls)
            if key not in merged:
                # Copy all fields except "sources", then add our own
                merged[key] = {k: v for k, v in det.items() if k != "sources"}
                merged[key]["sources"] = []
            if source not in merged[key]["sources"]:
                merged[key]["sources"].append(source)
    return list(merged.values())


def assemble_flagged_papers(
    papers: list[dict],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positives: dict[str, str],
    html_cache_dir: Path,
    pdf_cache_dir: Path,
) -> list[dict]:
    """For each paper, combine abstract + HTML cache + PDF cache detections.

    Returns records with paper metadata (including abstract) and language
    detections only — no HTML or PDF text content is included.
    """
    flagged: list[dict] = []
    html_cached = pdf_cached = 0

    for paper in papers:
        paper_id = str(paper.get("id", ""))
        safe_id = paper_id.split("/")[-1]

        sources_checked: list[str] = []
        sections_with_detections: list[str] = []
        detection_groups: list[tuple[list[dict], str]] = []

        # 1. Abstract (always scanned — public on arXiv)
        abs_dets = _scan_abstract(paper, lang_classes, languages_to_ignore, possible_false_positives)
        sources_checked.append("abstract")
        if abs_dets:
            detection_groups.append((abs_dets, "abstract"))
            sections_with_detections.append("abstract")

        # 2. HTML cache
        html_dets, html_sections = _load_html_detections(
            safe_id, html_cache_dir, lang_classes, possible_false_positives, languages_to_ignore
        )
        if html_dets is not None:
            sources_checked.append("html")
            html_cached += 1
            if html_dets:
                detection_groups.append((html_dets, "html"))
                sections_with_detections.extend(html_sections)

        # 3. PDF cache
        pdf_dets = _load_pdf_detections(safe_id, pdf_cache_dir, languages_to_ignore)
        if pdf_dets is not None:
            sources_checked.append("pdf")
            pdf_cached += 1
            if pdf_dets:
                detection_groups.append((pdf_dets, "pdf"))
                sections_with_detections.append("pdf_full_text")

        languages = _merge_detections(detection_groups)
        if languages:
            flagged.append({
                "paper": paper,
                "languages": languages,
                "sources_checked": sources_checked,
                "sections_with_detections": sections_with_detections,
            })

    print(f"  HTML cache hits : {html_cached}/{len(papers)}")
    print(f"  PDF cache hits  : {pdf_cached}/{len(papers)}")
    print(f"  Papers flagged  : {len(flagged)}/{len(papers)}")
    return flagged


def _week_dir(input_path: Path, processed_dir: Path | None = None) -> Path:
    """Derive the week subdirectory from the input filename's date slug."""
    root = processed_dir or _DEFAULT_PROCESSED_DIR
    m = _WEEK_RE.search(input_path.stem)
    slug = f"{m.group(1)}_to_{m.group(2)}" if m else input_path.stem
    return root / "weeks" / slug


def build_and_save(
    input_path: Path,
    output_dir: Path,
    lang_data_path: Path,
    window_days: int = 7,
    query: str = "cs.CL",
) -> Path:
    print(f"Loading papers from {input_path}")
    papers = _load_papers(input_path)
    print(f"  {len(papers)} papers loaded")

    print(f"Loading language data from {lang_data_path}")
    lang_classes, languages_to_ignore, possible_false_positives = _load_language_data(lang_data_path)
    print(f"  {sum(len(v) for v in lang_classes.values())} language entries, "
          f"{len(possible_false_positives)} flagged for review")

    html_cache_dir = output_dir / "html_cache"
    pdf_cache_dir = output_dir / "pdf_cache"

    print("Assembling detections from abstract + caches…")
    flagged_papers = assemble_flagged_papers(
        papers, lang_classes, languages_to_ignore, possible_false_positives,
        html_cache_dir, pdf_cache_dir,
    )

    # Parse week dates from filename (e.g. arxiv_papers_20260518_to_20260525.jsonl)
    week_start = week_end = None
    m = _WEEK_RE.search(input_path.stem)
    if m:
        week_start = _iso_date(m.group(1))
        week_end = _iso_date(m.group(2))

    print("Building manifest…")
    manifest = build_snapshot_manifest(
        papers=papers,
        flagged_papers=flagged_papers,
        window_days=window_days,
        category_query=query,
        week_start=week_start,
        week_end=week_end,
    )

    # Week-specific manifest lives inside the week folder
    output_dir.mkdir(parents=True, exist_ok=True)
    week_manifest_path = output_dir / "langtrend_manifest.json"
    save_json(manifest, week_manifest_path)
    print(f"Saved: {week_manifest_path}")

    # "Latest" pointer lives at the top of processed/ regardless of output_dir depth
    processed_root = output_dir.parent.parent if output_dir.parent.name == "weeks" else output_dir
    latest_path = processed_root / f"langtrend_manifest_last_{window_days}_days.json"
    save_json(manifest, latest_path)
    print(f"Saved: {latest_path}")

    return week_manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build LangTrend manifest from paper metadata and detection caches",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", type=Path, default=None,
        help="Input JSONL file (default: most recent file in data/raw/extracted_papers_metadata/)",
    )
    parser.add_argument(
        "--language-data", type=Path, default=_DEFAULT_LANG_DATA,
        help=f"language_data.json (default: {_DEFAULT_LANG_DATA})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Week output directory (default: auto-derived from input filename as data/processed/weeks/YYYYMMDD_to_YYYYMMDD/)",
    )
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--query", type=str, default="cs.CL")
    args = parser.parse_args()

    input_path = args.input or _find_latest_input()
    if input_path is None or not input_path.exists():
        print(f"Error: no input JSONL found. Pass --input or put files in {_METADATA_DIR}", file=sys.stderr)
        sys.exit(1)
    if not args.language_data.exists():
        print(f"Error: language data not found: {args.language_data}\n"
              "Run scripts/extract_language_data.py first.", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir or _week_dir(input_path)
    build_and_save(input_path, output_dir, args.language_data, args.window_days, args.query)


if __name__ == "__main__":
    main()
