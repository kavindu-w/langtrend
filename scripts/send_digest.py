#!/usr/bin/env python3
"""
Build this week's email digest and hand it to Buttondown.

Reads:
  - data/processed/langtrend_manifest_last_7_days.json  (or --manifest)

Modes:
  --mode dry-run  (default) print subject + body, or write them with --output; no network
  --mode draft    create a Buttondown *draft* to review and send by hand
  --mode send     create and send immediately

draft/send look for an existing email with the same subject first and do
nothing if they find one, so it's safe to run after every deploy (the daily
catch-up deploys included) — each week gets at most one digest. Because the
match is by exact subject, deleting or renaming a week's draft in Buttondown
makes the next deploy recreate it; to skip a week, leave the draft unsent.

Environment:
  BUTTONDOWN_API_KEY   required for draft/send

Usage:
    python scripts/send_digest.py
    python scripts/send_digest.py --output /tmp/digest.md
    python scripts/send_digest.py --mode draft
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.buttondown import ButtondownClient, ButtondownError
from langtrend.digest import extract_run_summary, render_digest

_PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_MANIFEST = _PROJECT_ROOT / "data" / "processed" / "langtrend_manifest_last_7_days.json"
DEFAULT_README = _PROJECT_ROOT / "README.md"
DEFAULT_SITE_URL = "https://kavindu-w.github.io/langtrend"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_KEY = 3  # not 2: argparse already exits 2 on bad arguments


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README, help="source of the run-summary stats table")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--mode", choices=["dry-run", "draft", "send"], default="dry-run")
    parser.add_argument("--output", type=Path, help="dry-run only: write the rendered digest here instead of stdout")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, client_factory=ButtondownClient) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not manifest.get("week_start") or not manifest.get("week_end"):
        print(f"ERROR: {args.manifest} has no week_start/week_end", file=sys.stderr)
        return EXIT_ERROR
    try:
        run_summary = extract_run_summary(args.readme.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"WARNING: no run summary ({exc})", file=sys.stderr)
        run_summary = None
    subject, body = render_digest(manifest, args.site_url, run_summary=run_summary)

    if args.mode == "dry-run":
        rendered = f"Subject: {subject}\n\n{body}"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
            print(f"Wrote digest to {args.output}")
        else:
            print(rendered)
        return EXIT_OK

    api_key = os.environ.get("BUTTONDOWN_API_KEY", "").strip()
    if not api_key:
        print("ERROR: BUTTONDOWN_API_KEY is not set", file=sys.stderr)
        return EXIT_NO_KEY

    try:
        client = client_factory(api_key)
        existing = client.find_email_by_subject(subject)
        if existing:
            print(f"Digest already exists ({existing.get('status', 'unknown status')}): {subject} — skipping")
            return EXIT_OK
        created = client.create_email(subject, body, send=args.mode == "send")
    except ButtondownError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    action = "Queued for sending" if args.mode == "send" else "Created draft"
    print(f"{action}: {subject} (id={created.get('id', '?')})")
    return EXIT_OK


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
