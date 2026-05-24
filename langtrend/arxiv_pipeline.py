from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import arxiv


def fetch_recent_arxiv_papers(
    window_days: int = 7,
    max_results: int = 100,
    category_query: str = "cat:cs.CL",
) -> list[dict[str, Any]]:
    """Fetch recent arXiv papers for the configured rolling window."""

    client = arxiv.Client()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=window_days)

    query = (
        f"{category_query} AND submittedDate:["
        f"{start_date.strftime('%Y%m%d%H%M')} TO {end_date.strftime('%Y%m%d%H%M')}]"
    )
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    papers: list[dict[str, Any]] = []
    for result in client.results(search):
        papers.append(
            {
                "id": result.entry_id,
                "title": result.title,
                "abstract": result.summary,
                "authors": [author.name for author in result.authors],
                "published": result.published.isoformat(),
                "updated": result.updated.isoformat(),
                "categories": list(result.categories),
                "pdf_url": result.pdf_url,
            }
        )

    return papers


def save_jsonl(items: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Write items to a JSONL file and return the path."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    return path
