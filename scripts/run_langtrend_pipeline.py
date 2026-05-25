#!/usr/bin/env python3
"""
LangTrend pipeline orchestrator — called by `make pipeline`.

Current behaviour (while process_papers.py is not yet integrated):
  1. Build the manifest from existing html/pdf caches + abstract scanning.

Future behaviour (once process_papers.py is wired in):
  1. fetch_arxiv_metadata.py  — pull latest week from arXiv
  2. process_papers.py        — HTML/PDF fetch + language detection
  3. build_manifest.py        — assemble manifest (no text content exported)

Usage:
    python scripts/run_langtrend_pipeline.py
    python scripts/run_langtrend_pipeline.py --data-root data --window-days 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from build_manifest import build_and_save, _find_latest_input

_PROJECT_ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangTrend snapshot pipeline")
    parser.add_argument("--data-root", type=Path, default=_PROJECT_ROOT / "data")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--max-results", type=int, default=100,
                        help="Max arXiv results (used when fetch step is enabled)")
    parser.add_argument("--query", type=str, default="cs.CL")
    args = parser.parse_args()

    data_root = args.data_root
    output_dir = data_root / "processed"
    lang_data = output_dir / "language_data.json"

    # Locate the input JSONL for this window
    metadata_dir = data_root / "raw" / "extracted_papers_metadata"
    candidates = sorted(metadata_dir.glob("arxiv_papers_*.jsonl"), key=lambda p: p.stat().st_mtime)
    input_path = candidates[-1] if candidates else None

    if input_path is None:
        print("No input JSONL found. Run fetch_arxiv_metadata.py first.", file=sys.stderr)
        sys.exit(1)

    if not lang_data.exists():
        print(f"language_data.json not found at {lang_data}\n"
              "Run scripts/extract_language_data.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"=== LangTrend pipeline ===")
    print(f"Input  : {input_path}")
    print(f"Output : {output_dir}")
    print()

    build_and_save(
        input_path=input_path,
        output_dir=output_dir,
        lang_data_path=lang_data,
        window_days=args.window_days,
        query=args.query,
    )

    print("\nDone. Run `make web-build` to regenerate the site.")


if __name__ == "__main__":
    main()
