# tests

Pytest suite for the Python side of the pipeline (`langtrend/` and `scripts/`). The Astro frontend has its own test suite under `web/` (Vitest), not covered here.

```text
tests/
├── test_text_cleaning.py         langtrend/text_cleaning.py
├── test_html_processor.py        langtrend/html_processor.py
├── test_pdf_fallback.py          langtrend/pdf_processor.py
├── test_manifest.py              langtrend/manifest.py
├── test_fetch_arxiv_metadata.py  scripts/fetch_arxiv_metadata.py
├── test_process_papers.py        scripts/process_papers.py
├── test_build_manifest.py        scripts/build_manifest.py
├── test_extract_language_data.py scripts/extract_language_data.py
└── test_update_readme_stats.py   scripts/update_readme_stats.py
```

| Test file | Covers |
|-----------|--------|
| `test_text_cleaning.py` | Text normalization, language detection, acronym extraction/conflict detection (`langtrend/text_cleaning.py`). |
| `test_html_processor.py` | arXiv HTML fetch + per-section extraction (`langtrend/html_processor.py`). |
| `test_pdf_fallback.py` | Docling-based PDF text extraction (`langtrend/pdf_processor.py`). |
| `test_manifest.py` | Manifest assembly + `save_json` helper (`langtrend/manifest.py`). |
| `test_fetch_arxiv_metadata.py` | arXiv API/OAI-PMH metadata fetching (`scripts/fetch_arxiv_metadata.py`). |
| `test_process_papers.py` | Abstract → HTML → PDF detection fallback chain (`scripts/process_papers.py`). |
| `test_build_manifest.py` | Manifest assembly from cached detections (`scripts/build_manifest.py`). |
| `test_extract_language_data.py` | Language taxonomy extraction from the submodule (`scripts/extract_language_data.py`). |
| `test_update_readme_stats.py` | README stats table, badge JSON, and `weekly_summary.csv` generation (`scripts/update_readme_stats.py`). |

Naming mirrors what's under test — `test_<module_or_script>.py` for each file in `langtrend/` or `scripts/`. Tests import scripts directly (`sys.path.insert(0, ".../scripts"); import build_manifest as bm`) rather than through the package, matching how each script is actually run from the command line.

Run the whole suite:

```bash
pytest tests/ -v
```

Run a single file:

```bash
pytest tests/test_text_cleaning.py -v
```

Coverage is tracked via Codecov (see the `Coverage (Python)` badge in the root README) and reported by the `tests.yml` GitHub Actions workflow on every push.
