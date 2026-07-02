#!/usr/bin/env python3
"""
Regenerate README.md's "Latest Run Summary" table and shields.io badge JSON
from already-committed manifest data.

Reads:
  - data/processed/langtrend_manifest_last_7_days.json           — latest-week counts
  - data/processed/weeks/YYYYMMDD_to_YYYYMMDD/langtrend_manifest.json  — per-week archives

Writes:
  - data/processed/badges/papers_badge.json                      — shields.io endpoint badge
  - data/processed/badges/languages_badge.json                   — shields.io endpoint badge
  - README.md                                                     — content between
    <!-- LANGTREND_STATS_START --> and <!-- LANGTREND_STATS_END --> markers

Usage:
    python scripts/update_readme_stats.py
    python scripts/update_readme_stats.py --data-root data --readme README.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.manifest import save_json

_PROJECT_ROOT = Path(__file__).parent.parent
_STATS_START = "<!-- LANGTREND_STATS_START -->"
_STATS_END = "<!-- LANGTREND_STATS_END -->"
_WEEK_RE = re.compile(r"^\d{8}_to_\d{8}$")


def find_week_manifests(weeks_dir: Path) -> list[Path]:
    """Every weeks/<8digits>_to_<8digits>/langtrend_manifest.json, sorted by week_start."""
    if not weeks_dir.exists():
        return []
    week_dirs = sorted(p for p in weeks_dir.iterdir() if p.is_dir() and _WEEK_RE.match(p.name))
    return [d / "langtrend_manifest.json" for d in week_dirs if (d / "langtrend_manifest.json").exists()]


def load_manifest(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def compute_latest_stats(latest_manifest: dict | None) -> dict:
    manifest = latest_manifest or {}
    counts = manifest.get("counts", {})
    return {
        "week_start": manifest.get("week_start"),
        "week_end": manifest.get("week_end"),
        "papers": counts.get("papers", 0),
        "flagged_papers": counts.get("flagged_papers", 0),
        "unique_languages": counts.get("unique_languages", 0),
    }


def compute_cumulative_stats(week_manifests: list[dict]) -> dict:
    """Sum papers/flagged across all weeks; union of language names for the all-time count.

    Mirrors the "Overall trends" aggregation in web/src/components/TrendCharts.astro.
    """
    total_papers = sum(m.get("counts", {}).get("papers", 0) for m in week_manifests)
    total_flagged_papers = sum(m.get("counts", {}).get("flagged_papers", 0) for m in week_manifests)

    all_languages: set[str] = set()
    for m in week_manifests:
        for entry in m.get("language_counts", []):
            language = entry.get("language")
            if language:
                all_languages.add(language)

    week_starts = [m["week_start"] for m in week_manifests if m.get("week_start")]

    return {
        "total_papers": total_papers,
        "total_flagged_papers": total_flagged_papers,
        "total_unique_languages": len(all_languages),
        "weeks_tracked": len(week_manifests),
        "earliest_week_start": min(week_starts) if week_starts else None,
    }


def _endpoint_badge(label: str, message: str, color: str) -> dict:
    return {"schemaVersion": 1, "label": label, "message": message, "color": color}


def write_badge_files(cumulative: dict, out_dir: Path) -> list[Path]:
    papers_badge = _endpoint_badge(
        "papers analysed", f"{cumulative['total_papers']:,}", "0f6c5d"
    )
    languages_badge = _endpoint_badge(
        "languages tracked", f"{cumulative['total_unique_languages']:,}", "0f6c5d"
    )
    return [
        save_json(papers_badge, out_dir / "papers_badge.json"),
        save_json(languages_badge, out_dir / "languages_badge.json"),
    ]


def render_stats_block(latest: dict, cumulative: dict) -> str:
    week_start = latest["week_start"] or "N/A"
    week_end = latest["week_end"] or "N/A"
    earliest_week_start = cumulative["earliest_week_start"] or "N/A"

    lines = [
        _STATS_START,
        "",
        "## Latest Run Summary",
        "",
        f"_Latest processed week: **{week_start} – {week_end}**._",
        "",
        "| Metric | This week | All-time |",
        "|--------|----------:|---------:|",
        f"| Papers scanned | {latest['papers']:,} | {cumulative['total_papers']:,} |",
        f"| Papers with language mentions | {latest['flagged_papers']:,} | {cumulative['total_flagged_papers']:,} |",
        f"| Unique languages detected | {latest['unique_languages']:,} | {cumulative['total_unique_languages']:,} |",
        f"| Weeks tracked | — | {cumulative['weeks_tracked']:,} (since {earliest_week_start}) |",
        "",
        _STATS_END,
    ]
    return "\n".join(lines)


def update_readme(block: str, readme_path: Path) -> bool:
    """Replace the content between the stats markers. Returns True if the file changed."""
    text = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(_STATS_START) + r".*?" + re.escape(_STATS_END), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(
            f"{readme_path} is missing the {_STATS_START} / {_STATS_END} markers. "
            "Add them once manually before running this script."
        )
    new_text = pattern.sub(block, text)
    if new_text == text:
        return False
    readme_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate README stats table and badges from committed manifest data.",
    )
    parser.add_argument("--data-root", type=Path, default=_PROJECT_ROOT / "data")
    parser.add_argument("--readme", type=Path, default=_PROJECT_ROOT / "README.md")
    args = parser.parse_args()

    processed_dir = args.data_root / "processed"
    latest_manifest = load_manifest(processed_dir / "langtrend_manifest_last_7_days.json")
    week_paths = find_week_manifests(processed_dir / "weeks")
    week_manifests = [m for p in week_paths if (m := load_manifest(p)) is not None]

    latest = compute_latest_stats(latest_manifest)
    cumulative = compute_cumulative_stats(week_manifests)

    badge_paths = write_badge_files(cumulative, processed_dir / "badges")
    block = render_stats_block(latest, cumulative)
    changed = update_readme(block, args.readme)

    print(f"Latest week: {latest['week_start']} -> {latest['week_end']}")
    print(f"Cumulative: {cumulative}")
    print(f"README changed: {changed}")
    print(f"Badge files written: {[str(p) for p in badge_paths]}")


if __name__ == "__main__":
    main()
