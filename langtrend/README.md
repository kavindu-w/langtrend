# langtrend

Core pipeline package — the language-detection logic shared by every script in `scripts/`. Nothing here talks to the filesystem convention (`data/raw`, `data/processed`, etc.) directly; that orchestration lives in `scripts/`.

```text
langtrend/
├── __init__.py          Public API re-exports
├── text_cleaning.py     Text normalization + language/acronym detection
├── html_processor.py    arXiv HTML fetch + per-section extraction
├── pdf_processor.py     Docling-based PDF text extraction (HTML fallback)
└── manifest.py          Manifest assembly + shared save_json helper
```

| Module | Responsibility |
|--------|----------------|
| `__init__.py` | Re-exports the small set of functions each script actually imports (`build_snapshot_manifest`, `save_json`, `extract_sections_from_html`, `PDFProcessor`, the text-cleaning/detection functions) — import from `langtrend` rather than the submodule directly. |
| `text_cleaning.py` | Normalizes raw text for language screening (strips math artifacts, LaTeX commands, subscripts), then detects mentioned language names against the class taxonomy. Also handles acronym extraction and acronym/language-name conflict detection (e.g. a paper defining "GAN" shouldn't flag the Gan language). |
| `html_processor.py` | Fetches a paper's arXiv HTML version and extracts per-section text for detection, so mentions in the Experiments/Data sections are caught, not just the abstract. Excludes references/acknowledgements sections. Rate-limited to be a polite arXiv client (single in-flight request, honest User-Agent). |
| `pdf_processor.py` | Fallback for papers without an HTML version — extracts layout-aware text from the PDF via [docling](https://github.com/DS4SD/docling) (forced to CPU; docling's layout model doesn't support Apple MPS float64). |
| `manifest.py` | Assembles the final per-week `langtrend_manifest.json` (paper counts, flagged papers, language/class distributions) from whatever detections `scripts/process_papers.py` produced. Also has the shared `save_json` helper used by every script that writes JSON output. |

Unit tests for these modules live in `tests/` (one test file per module, e.g. `tests/test_text_cleaning.py`).
