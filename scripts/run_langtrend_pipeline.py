#!/usr/bin/env python3
"""
LangTrend pipeline orchestrator — called by `make pipeline`.

Steps (each can be skipped if its output already exists):
  1. fetch    — pull papers from arXiv for the current week window
  2. process  — fetch arXiv HTML, download PDFs, detect languages
  3. manifest — assemble manifest from caches (no text content exported)

The default window is always "last Monday midnight → 7 days back", so running
this on any day of the week will produce the same week's output.

Usage:
    python scripts/run_langtrend_pipeline.py
    python scripts/run_langtrend_pipeline.py --data-root data --window-days 7 --max-results 1000
    python scripts/run_langtrend_pipeline.py --skip-fetch      # reuse existing JSONL
    python scripts/run_langtrend_pipeline.py --skip-process    # skip HTML/PDF, rebuild manifest only
    python scripts/run_langtrend_pipeline.py --workers 8
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from build_manifest import build_and_save

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPTS_DIR = Path(__file__).parent


def _last_monday_midnight() -> datetime:
    today = datetime.now()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _expected_raw_path(end_date: datetime, window_days: int, metadata_dir: Path) -> Path:
    start_date = end_date - timedelta(days=window_days)
    filename = (
        f"arxiv_papers_"
        f"{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.jsonl"
    )
    return metadata_dir / filename


def _run(label: str, cmd: list[str]) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\nError: {label} exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full LangTrend snapshot pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-root", type=Path, default=_PROJECT_ROOT / "data")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--max-results", type=int, default=1000)
    parser.add_argument("--query", type=str, default="cat:cs.CL")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers for process step")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip fetch step even if no JSONL exists (will fail if no file found)")
    parser.add_argument("--skip-process", action="store_true",
                        help="Skip process step; rebuild manifest from existing caches only")
    args = parser.parse_args()

    data_root: Path = args.data_root
    metadata_dir = data_root / "raw" / "extracted_papers_metadata"
    output_dir = data_root / "processed"
    lang_data = output_dir / "language_data.json"

    if not lang_data.exists():
        print(f"Error: language_data.json not found at {lang_data}\n"
              "Run scripts/extract_language_data.py first.", file=sys.stderr)
        sys.exit(1)

    end_date = _last_monday_midnight()
    print(f"=== LangTrend pipeline  |  window: {args.window_days}d ending {end_date.strftime('%Y-%m-%d')} ===")

    # -------------------------------------------------------------------------
    # Step 1: Fetch
    # -------------------------------------------------------------------------
    expected_input = _expected_raw_path(end_date, args.window_days, metadata_dir)

    if args.skip_fetch:
        print(f"\nStep 1 [SKIPPED] fetch")
    elif expected_input.exists():
        print(f"\nStep 1 [SKIP] fetch — {expected_input.name} already exists")
    else:
        _run("Step 1: fetch papers from arXiv", [
            sys.executable,
            str(_SCRIPTS_DIR / "fetch_arxiv_metadata.py"),
            "--end-date", end_date.strftime("%Y-%m-%d"),
            "--window-days", str(args.window_days),
            "--max-results", str(args.max_results),
            "--category", args.query,
            "--output-dir", str(metadata_dir),
        ])

    # Resolve the actual input path after fetch (may differ from expected if already existed)
    if not expected_input.exists():
        # Fallback: pick the most recently modified JSONL
        candidates = sorted(metadata_dir.glob("arxiv_papers_*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            print("Error: no input JSONL found after fetch step.", file=sys.stderr)
            sys.exit(1)
        input_path = candidates[-1]
        print(f"  Using fallback input: {input_path.name}")
    else:
        input_path = expected_input

    print(f"  Input: {input_path}")

    # -------------------------------------------------------------------------
    # Step 2: Process papers (HTML + PDF language detection)
    # -------------------------------------------------------------------------
    detected_path = output_dir / f"{input_path.stem}_detected.jsonl"

    if args.skip_process:
        print(f"\nStep 2 [SKIPPED] process")
    elif detected_path.exists():
        print(f"\nStep 2 [SKIP] process — {detected_path.name} already exists")
    else:
        _run("Step 2: process papers (HTML/PDF detection)", [
            sys.executable,
            str(_SCRIPTS_DIR / "process_papers.py"),
            "--input", str(input_path),
            "--output-dir", str(output_dir),
            "--workers", str(args.workers),
        ])

    # -------------------------------------------------------------------------
    # Step 3: Build manifest (always runs — fast, no network)
    # -------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  Step 3: build manifest")
    print(f"{'='*60}")

    build_and_save(
        input_path=input_path,
        output_dir=output_dir,
        lang_data_path=lang_data,
        window_days=args.window_days,
        query=args.query,
    )

    print("\nPipeline complete. Run `make web-build` to regenerate the site.")


if __name__ == "__main__":
    main()
