#!/usr/bin/env python3
"""
Fetch arXiv paper metadata for a given time window and save as JSONL.

Defaults to the 7-day window ending last Monday at midnight — designed to run
on Monday morning as a scheduled task.

Usage:
    python scripts/fetch_arxiv_metadata.py
    python scripts/fetch_arxiv_metadata.py --window-days 30 --max-results 2000
    python scripts/fetch_arxiv_metadata.py --end-date 2026-05-25
    python scripts/fetch_arxiv_metadata.py --category "cat:cs.CL OR cat:cs.AI"
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import arxiv

_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data/raw/extracted_papers_metadata"


def _last_monday_midnight() -> datetime:
    today = datetime.now()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_and_save(
    end_date: datetime,
    window_days: int,
    max_results: int,
    category_query: str,
    output_dir: Path,
) -> Path:
    start_date = end_date - timedelta(days=window_days)
    start_str = start_date.strftime("%Y%m%d%H%M")
    end_str = end_date.strftime("%Y%m%d%H%M")

    query = f"{category_query} AND submittedDate:[{start_str} TO {end_str}]"
    print(f"Query:       {query}")
    print(f"Max results: {max_results}")
    print(f"Window:      {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")

    client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    results_list = list(client.results(search))
    print(f"Retrieved {len(results_list)} papers")

    if len(results_list) >= max_results:
        print(
            f"Warning: max_results limit ({max_results}) reached — "
            "there may be more papers in this window. Consider increasing --max-results."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"arxiv_papers_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.jsonl"
    )
    output_path = output_dir / filename

    with output_path.open("w", encoding="utf-8") as fp:
        for result in results_list:
            paper_data = {
                "id": result.entry_id,
                "title": result.title,
                "abstract": result.summary,
                "authors": [author.name for author in result.authors],
                "published": result.published.isoformat(),
                "updated": result.updated.isoformat(),
                "categories": list(result.categories),
                "pdf_url": result.pdf_url,
            }
            fp.write(json.dumps(paper_data, ensure_ascii=False) + "\n")

    print(f"Saved to {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch arXiv paper metadata for a time window",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--window-days", type=int, default=7, help="Number of days to look back (default: 7)")
    parser.add_argument("--max-results", type=int, default=1000, help="Max papers to fetch (default: 1000)")
    parser.add_argument("--category", default="cat:cs.CL", help='arXiv category query (default: "cat:cs.CL")')
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Output directory for JSONL files (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="End date for the window (default: last Monday at midnight)",
    )
    args = parser.parse_args()

    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    else:
        end_date = _last_monday_midnight()
        print(f"Using last Monday as end date: {end_date.strftime('%Y-%m-%d')}")

    fetch_and_save(
        end_date=end_date,
        window_days=args.window_days,
        max_results=args.max_results,
        category_query=args.category,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
