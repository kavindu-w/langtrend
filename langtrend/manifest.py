from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def save_json(data: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_snapshot_manifest(
    papers: list[dict[str, Any]],
    flagged_papers: list[dict[str, Any]],
    window_days: int,
    category_query: str,
) -> dict[str, Any]:
    language_counts: Counter[str] = Counter()
    class_counts: Counter[int] = Counter()
    daily_papers: Counter[str] = Counter()
    daily_flagged: Counter[str] = Counter()

    for paper in papers:
        published = str(paper.get("published", ""))[:10]
        if published:
            daily_papers[published] += 1

    for flagged in flagged_papers:
        paper = flagged.get("paper", {})
        published = str(paper.get("published", ""))[:10]
        if published:
            daily_flagged[published] += 1
        for detected in flagged.get("languages", []):
            language = detected.get("language")
            class_id = detected.get("class_id")
            if language:
                language_counts[language] += 1
            if class_id is not None:
                class_counts[int(class_id)] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "query": category_query,
        "counts": {
            "papers": len(papers),
            "flagged_papers": len(flagged_papers),
            "unique_languages": len(language_counts),
        },
        "language_counts": [
            {"language": language, "count": count}
            for language, count in language_counts.most_common()
        ],
        "class_counts": [
            {"class_id": class_id, "count": count}
            for class_id, count in sorted(class_counts.items())
        ],
        "daily_series": [
            {
                "date": date,
                "papers": daily_papers.get(date, 0),
                "flagged": daily_flagged.get(date, 0),
            }
            for date in sorted(daily_papers.keys() | daily_flagged.keys())
        ],
        "papers": papers,
        "flagged_papers": flagged_papers,
    }


def load_snapshot_inputs(
    data_root: str | Path = "data",
    window_days: int = 7,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(data_root)
    raw_path = root / "raw" / f"arxiv_papers_last_{window_days}_days.jsonl"
    flagged_path = root / "processed" / f"papers_with_tracked_langs_last_{window_days}_days.jsonl"
    return _load_jsonl(raw_path), _load_jsonl(flagged_path)
