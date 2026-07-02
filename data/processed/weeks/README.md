# data/processed/weeks

One subdirectory per processed week, named `<YYYYMMDD>_to_<YYYYMMDD>` (the window's start/end dates, matching the corresponding file in `data/raw/extracted_papers_metadata/`). Each week is self-contained and immutable once fully processed — re-running the pipeline for an already-processed week reuses these files instead of redownloading/redetecting.

```text
data/processed/weeks/
└── <YYYYMMDD>_to_<YYYYMMDD>/
    ├── langtrend_manifest.json                    This week's manifest
    ├── arxiv_papers_<range>_detected.jsonl         Per-paper detections
    ├── arxiv_papers_<range>_no_detections.json     Papers with no language mentions
    ├── arxiv_papers_<range>_warnings.json          Acronym-conflict warnings
    ├── html_cache/                                 Raw HTML detections (gitignored)
    └── pdf_cache/                                  Raw PDF detections (gitignored)
```

| File | Committed? | Contents |
|------|:----------:|----------|
| `langtrend_manifest.json` | Yes | This week's manifest — paper/flagged/language counts, per-class distribution, and paper-level records (metadata + detections, no raw text). What the website and `scripts/update_readme_stats.py` read. |
| `arxiv_papers_<range>_detected.jsonl` | Yes | Per-paper detection records (one JSON object per line) for papers with at least one language mention. Input to `scripts/build_manifest.py`. |
| `arxiv_papers_<range>_no_detections.json` | Yes | Papers with no language mentions found, including which sources were checked (abstract/html/pdf) — used to compute the `pdf_failed_no_detection` count. |
| `arxiv_papers_<range>_warnings.json` | Yes | Acronym-conflict warnings raised while processing this week (subset later merged into `data/processed/language_screening_warnings.json`). |
| `html_cache/` | **No** (gitignored) | Raw per-paper HTML section extraction + detections. Contains full paper body text, so it's excluded from the repo (see `.gitignore`) — kept locally to make `--reprocess-cache` reruns fast. |
| `pdf_cache/` | **No** (gitignored) | Same as `html_cache/` but for the PDF-fallback path (via `langtrend/pdf_processor.py`). |

Because `html_cache/` and `pdf_cache/` aren't committed, a fresh clone can rebuild `langtrend_manifest.json` from the committed `*_detected.jsonl`/`*_no_detections.json` files (`scripts/build_manifest.py` falls back to these when the caches are missing), but cannot re-run `--reprocess-cache` without re-fetching HTML/PDFs first.
