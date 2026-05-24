#!/usr/bin/env python3
from __future__ import annotations

import argparse

from langtrend.pipeline import run_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LangTrend snapshot pipeline")
    parser.add_argument("--data-root", default="data", help="Root data directory")
    parser.add_argument("--window-days", type=int, default=7, help="Rolling window in days")
    parser.add_argument("--max-results", type=int, default=100, help="Maximum arXiv results")
    parser.add_argument("--category-query", default="cat:cs.CL", help="arXiv query prefix")
    parser.add_argument(
        "--process-html-sections",
        action="store_true",
        help="Fetch and save HTML section detections for flagged papers",
    )
    args = parser.parse_args()

    manifest = run_snapshot(
        data_root=args.data_root,
        window_days=args.window_days,
        max_results=args.max_results,
        category_query=args.category_query,
        process_html_sections=args.process_html_sections,
    )

    print(f"Generated snapshot with {manifest['counts']['papers']} papers and {manifest['counts']['flagged_papers']} flagged papers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())