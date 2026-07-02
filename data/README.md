# data

All pipeline data, split into what's fetched from arXiv (`raw/`) and what the pipeline derives from it (`processed/`). Most files here are committed to the repository — see the "Open data" bullet in the root README — the only large/gitignored content is the local PDF and HTML/PDF-detection caches used during processing.

```text
data/
├── raw/          arXiv metadata (committed) + downloaded PDFs (gitignored)
└── processed/    Manifests, taxonomy, badges, and summary tables
```

| Directory | Contents |
|-----------|----------|
| `raw/` | arXiv metadata JSONL (committed) and downloaded PDF files (gitignored local cache, PDF-fallback input only). See `raw/README.md`. |
| `processed/` | Everything `scripts/build_manifest.py` and `scripts/update_readme_stats.py` derive from `raw/`: per-week manifests, the language class taxonomy, README badges/summary, and the per-week detection caches. See `processed/README.md`. |

Data is produced by the `make` pipeline targets described in the root README (`make fetch`, `make process`, `make manifest`, or `make pipeline` for all three) and, in CI, by `.github/workflows/langtrend.yml`.
