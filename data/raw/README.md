# data/raw

Inputs fetched directly from arXiv, before any language detection runs.

```text
data/raw/
├── extracted_papers_metadata/   Weekly JSONL of paper metadata + abstracts
└── pdfs/                        Downloaded PDF cache, one folder per arXiv ID
```

| Directory | Committed? | Contents |
|-----------|:----------:|----------|
| `extracted_papers_metadata/` | Yes | One JSONL file per week, `arxiv_papers_<YYYYMMDD>_to_<YYYYMMDD>.jsonl`, one line per paper with its arXiv ID, title, authors, category list, and abstract. Written by `scripts/fetch_arxiv_metadata.py` (or `run_langtrend_pipeline.py`). This is the "open data" input the rest of the pipeline is built on. |
| `pdfs/` | **No** (gitignored) | Downloaded PDF files, one subdirectory per arXiv ID, used only when a paper has no usable HTML version (`langtrend/pdf_processor.py`'s docling fallback). Disposable local cache to avoid re-downloading PDFs across pipeline runs — not needed to reproduce the manifests, since PDF *detections* (not the PDFs themselves) are cached separately under `data/processed/weeks/<range>/pdf_cache/`. |
