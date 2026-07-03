# scripts

Command-line entry points that drive the `langtrend/` package. Most of these are invoked through `make` targets (see the root `Makefile`) rather than run directly — the targets pass the right `--data-root`/`--window-days`/`--end-date` flags for you.

```text
scripts/
├── run_langtrend_pipeline.py   Orchestrator — fetch → process → manifest
├── fetch_arxiv_metadata.py     Step 1 — fetch arXiv metadata
├── process_papers.py           Step 2 — extract text and detect languages
├── judge_languages.py          Step 3 — LLM judge: verify detections (studied / mentioned-only / false positive)
├── build_manifest.py           Step 4 — assemble manifest from caches + judge verdicts
├── extract_language_data.py    Regenerate language_data.json from submodule
└── update_readme_stats.py      Regenerate README stats, badges, weekly_summary.csv
```

| Script | Purpose | Typical invocation |
|--------|---------|---------------------|
| `run_langtrend_pipeline.py` | Orchestrator — runs fetch → process → manifest in sequence, skipping any step whose output already exists. This is what `make pipeline` and the GitHub Actions workflow call. | `make pipeline` |
| `fetch_arxiv_metadata.py` | Fetches arXiv `cs.CL` paper metadata + abstracts for a 7-day window and writes JSONL to `data/raw/extracted_papers_metadata/`. Falls back to OAI-PMH harvesting if the arXiv API is unavailable. | `make fetch` |
| `process_papers.py` | For each paper: scans the abstract, then the HTML version section-by-section, then falls back to PDF (via `langtrend.pdf_processor`) if no HTML is available. Writes per-paper detections + cache files under `data/processed/weeks/<range>/`. | `make process` |
| `judge_languages.py` | LLM-as-judge verification: for each paper in `*_detected.jsonl`, assembles context from the cached section text and asks an OpenAI-compatible model (Groq free tier by default — open-weight Llama 3.3 70B; local Ollama works too — see `.env.example`) whether each detected language is `studied`, `mentioned_only`, or a `false_positive`. Writes per-paper verdicts to `judge_cache/`; resumable (cached papers are skipped). Single-paper testing: `--paper-id <id> [--dry-run]`. | `make judge` |
| `build_manifest.py` | Assembles the week's `langtrend_manifest.json` (and the "last 7 days" pointer) from the cached detections, folding in any `judge_cache/` verdicts — no downloads, safe to re-run. | `make manifest` |
| `extract_language_data.py` | Regenerates `data/processed/language_data.json` from the `Some-Languages-are-More-Equal-than-Others` submodule (class taxonomy + false-positive flag list). Run after `git submodule update --remote`. | `python scripts/extract_language_data.py` |
| `update_readme_stats.py` | Regenerates the README's "Latest Run Summary" table, the two shields.io badge JSON files, and `data/processed/weekly_summary.csv` from the committed manifests. Runs automatically in CI (`.github/workflows/langtrend.yml`, job `pdf-retry-2`). | `make readme-stats` |

Corresponding tests live in `tests/` (one `test_<script>.py` per script, plus tests for the underlying `langtrend/` modules).
