PYTHON      ?= ./venv/bin/python
DATA_ROOT   ?= data
WINDOW_DAYS ?= 7
MAX_RESULTS ?= 1000
WORKERS     ?= 16
JUDGE_WORKERS ?= 4
# END_DATE    ?= 2026-05-04
# END_DATE    ?= 2026-05-11
# END_DATE    ?= 2026-05-18
# END_DATE    ?= 2026-05-25
# END_DATE    ?= 2026-06-01
# END_DATE    ?= 2026-06-08
# END_DATE    ?= 2026-06-15
# END_DATE    ?= 2026-06-22
# END_DATE    ?= 2026-06-29
# NO_PDF      ?= 1

# List of end-dates for *-all targets. Override on the command line:
#   make reprocess-all DATES="2026-05-18 2026-05-25"
# DATES ?= 2026-05-04 2026-05-11
# DATES ?= 2026-05-18 2026-05-25
# DATES ?= 2026-06-01 2026-06-08
DATES ?= 2026-06-15 2026-06-22

# Pass --end-date only when END_DATE is set
_END_DATE_FLAG = $(if $(END_DATE),--end-date $(END_DATE),)
# Pass --no-pdf only when NO_PDF=1 (skips docling; safe to run in multiple terminals)
_NO_PDF_FLAG   = $(if $(NO_PDF),--no-pdf,)

.PHONY: help setup fetch fetch-all fetch-oai process process-all reprocess reprocess-all \
        retry-missing retry-missing-all judge judge-all manifest manifest-all readme-stats \
        pipeline pipeline-all test-paper test test-py test-web \
        web-install web-dev web-build dev build pipeline-diagram clean

help:
	@echo "Single-week targets (controlled by END_DATE):"
	@echo "  make fetch            Fetch papers from arXiv API (skipped if JSONL exists)"
	@echo "  make fetch-oai        Fetch via OAI-PMH harvester instead of arXiv API"
	@echo "  make process          Detect languages via HTML/PDF (skipped if already done)"
	@echo "  make reprocess        Re-run cleaning+detection on cached text only (no downloads)"
	@echo "  make retry-missing    Retry papers with no/incomplete cache (downloads missing PDFs)"
	@echo "  make judge            LLM-verify detected languages (needs LLM_JUDGE_API_KEY in .env);"
	@echo "                          run 'make manifest' afterwards to fold verdicts in"
	@echo "  make manifest         Rebuild manifest from caches (fast, no downloads)"
	@echo "                          Use INPUT=<path.jsonl> to target a specific week"
	@echo "  make readme-stats     Regenerate README badges/table + weekly_summary.csv"
	@echo "  make pipeline         Run fetch + process + manifest in sequence"
	@echo ""
	@echo "Try it on your own paper:"
	@echo "  make test-paper ARXIV_ID=1111.11111   Run one arXiv paper through the detection pipeline"
	@echo "  make test-paper PDF_PATH=paper.pdf    ...or a PDF already on disk (not on arXiv), PDF-only detection"
	@echo "                                          (writes to data/sandbox/, never touches weekly data)"
	@echo "                                          Add JUDGE=1 to also run the LLM judge stage"
	@echo "                                          (PDF_PATH also takes TITLE=\"...\" for the report)"
	@echo ""
	@echo "Multi-week targets (loop over DATES):"
	@echo "  make fetch-all        fetch for each date in DATES"
	@echo "  make process-all      process for each date in DATES"
	@echo "  make reprocess-all    reprocess for each date in DATES"
	@echo "  make retry-missing-all  retry-missing for each date in DATES"
	@echo "  make judge-all        judge for each date in DATES (resumable; reruns skip cached verdicts)"
	@echo "  make manifest-all     rebuild manifest for every week found in metadata dir"
	@echo "  make pipeline-all     full pipeline for each date in DATES"
	@echo ""
	@echo "Web targets:"
	@echo "  make web-dev          Start the Astro dev server"
	@echo "  make web-build        Build the Astro site"
	@echo "  make build            Run pipeline then build the site"
	@echo ""
	@echo "Other:"
	@echo "  make setup            Install Python and Node dependencies"
	@echo "  make test             Run the full test suite (pytest + vitest)"
	@echo "  make pipeline-diagram Re-export the pipeline diagram SVG from the .drawio source"
	@echo "                          (needs the drawio desktop CLI: brew install --cask drawio)"
	@echo "  make clean            Remove build artefacts (web/dist)"
	@echo ""
	@echo "Variables:"
	@echo "  END_DATE=$(if $(END_DATE),$(END_DATE),(auto: last Monday))  — single-week target date"
	@echo "  DATES=\"$(DATES)\""
	@echo "         — space-separated end-dates for *-all targets"
	@echo "  NO_PDF=1    skip docling PDF processing (safe for parallel terminals)"
	@echo "  WORKERS=$(WORKERS)  DATA_ROOT=$(DATA_ROOT)  WINDOW_DAYS=$(WINDOW_DAYS)  MAX_RESULTS=$(MAX_RESULTS)"
	@echo "  JUDGE_WORKERS=$(JUDGE_WORKERS)  (LLM judge config lives in .env — see .env.example)"
	@echo ""
	@echo "Parallel multi-week workflow:"
	@echo "  Terminal 1: make process NO_PDF=1 END_DATE=2026-05-04"
	@echo "  Terminal 2: make process NO_PDF=1 END_DATE=2026-05-11"
	@echo "  Then once:  make retry-missing-all DATES=\"2026-05-04 2026-05-11\""

setup:
	pip install -r requirements.txt
	cd web && npm install

# --- Individual pipeline steps (single week) ---------------------------------

fetch:
	scripts/run_logged.sh fetch \
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		$(_END_DATE_FLAG) \
		--skip-process

fetch-oai:
	scripts/run_logged.sh fetch-oai \
	$(PYTHON) scripts/fetch_arxiv_metadata.py \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		$(_END_DATE_FLAG) \
		--oai-only

process:
	scripts/run_logged.sh process \
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		$(_NO_PDF_FLAG) \
		--skip-fetch

reprocess:
	scripts/run_logged.sh reprocess \
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		--skip-fetch \
		--reprocess-cache

retry-missing:
	scripts/run_logged.sh retry-missing \
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		--skip-fetch \
		--retry-missing

judge:
	scripts/run_logged.sh judge \
	$(PYTHON) scripts/judge_languages.py \
		--window-days $(WINDOW_DAYS) \
		--workers $(JUDGE_WORKERS) \
		$(_END_DATE_FLAG)

manifest:
	scripts/run_logged.sh manifest \
	$(PYTHON) scripts/build_manifest.py \
		--window-days $(WINDOW_DAYS) \
		$(if $(INPUT),--input $(INPUT),)

readme-stats:
	scripts/run_logged.sh readme-stats \
	$(PYTHON) scripts/update_readme_stats.py --data-root $(DATA_ROOT)

pipeline:
	scripts/run_logged.sh pipeline \
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG)

# --- Try it on your own paper -------------------------------------------------

# Pass --judge only when JUDGE=1 (needs LLM_JUDGE_API_KEY in .env)
_JUDGE_FLAG = $(if $(JUDGE),--judge,)
# Pass --title only when TITLE is set (only used with PDF_PATH)
_TITLE_FLAG = $(if $(TITLE),--title "$(TITLE)",)

test-paper:
ifdef PDF_PATH
	$(PYTHON) scripts/test_single_paper.py --pdf-path "$(PDF_PATH)" $(_TITLE_FLAG) $(_JUDGE_FLAG)
else ifdef ARXIV_ID
	$(PYTHON) scripts/test_single_paper.py --arxiv-id "$(ARXIV_ID)" $(_NO_PDF_FLAG) $(_JUDGE_FLAG)
else
	$(error Usage: make test-paper ARXIV_ID=1111.11111  (bare id, versioned id, or full arxiv.org URL) \
	    or: make test-paper PDF_PATH=~/Downloads/some_paper.pdf  (a PDF already on disk, e.g. not on arXiv))
endif

# --- Multi-week targets (loop over DATES) ------------------------------------

fetch-all:
	@for d in $(DATES); do \
		echo ""; echo "=== fetch: $$d ==="; \
		scripts/run_logged.sh fetch-all-$$d \
		$(PYTHON) scripts/run_langtrend_pipeline.py \
			--data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) \
			--max-results $(MAX_RESULTS) --workers $(WORKERS) \
			--end-date $$d --skip-process; \
	done

process-all:
	@for d in $(DATES); do \
		echo ""; echo "=== process: $$d ==="; \
		scripts/run_logged.sh process-all-$$d \
		$(PYTHON) scripts/run_langtrend_pipeline.py \
			--data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) --workers $(WORKERS) \
			--end-date $$d --skip-fetch $(_NO_PDF_FLAG); \
	done

reprocess-all:
	@for d in $(DATES); do \
		echo ""; echo "=== reprocess: $$d ==="; \
		scripts/run_logged.sh reprocess-all-$$d \
		$(PYTHON) scripts/run_langtrend_pipeline.py \
			--data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) --workers $(WORKERS) \
			--end-date $$d --skip-fetch --reprocess-cache; \
	done

retry-missing-all:
	@for d in $(DATES); do \
		echo ""; echo "=== retry-missing: $$d ==="; \
		scripts/run_logged.sh retry-missing-all-$$d \
		$(PYTHON) scripts/run_langtrend_pipeline.py \
			--data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) --workers $(WORKERS) \
			--end-date $$d --skip-fetch --retry-missing; \
	done

judge-all:
	@for d in $(DATES); do \
		echo ""; echo "=== judge: $$d ==="; \
		scripts/run_logged.sh judge-all-$$d \
		$(PYTHON) scripts/judge_languages.py \
			--window-days $(WINDOW_DAYS) --workers $(JUDGE_WORKERS) \
			--end-date $$d; \
	done

manifest-all:
	@for f in $(DATA_ROOT)/raw/extracted_papers_metadata/arxiv_papers_*.jsonl; do \
		echo ""; echo "=== manifest: $$f ==="; \
		scripts/run_logged.sh manifest-all-$$(basename $$f .jsonl) \
		$(PYTHON) scripts/build_manifest.py \
			--input $$f --window-days $(WINDOW_DAYS); \
	done

pipeline-all:
	@for d in $(DATES); do \
		echo ""; echo "=== pipeline: $$d ==="; \
		scripts/run_logged.sh pipeline-all-$$d \
		$(PYTHON) scripts/run_langtrend_pipeline.py \
			--data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) \
			--max-results $(MAX_RESULTS) --workers $(WORKERS) \
			--end-date $$d; \
	done

# --- Web --------------------------------------------------------------------

web-install:
	cd web && npm install

web-dev: web-install
	cd web && npm run dev

web-build: web-install
	scripts/run_logged.sh web-build bash -c "cd web && npm run build"

dev: web-dev

build: pipeline web-build

# --- Tests --------------------------------------------------------------------

test-py:
	$(PYTHON) -m pytest tests/ -v

test-web: web-install
	cd web && npm test

test: test-py test-web

# --- Docs ---------------------------------------------------------------------

pipeline-diagram:
	scripts/export_pipeline_diagram.sh

# --- Housekeeping -----------------------------------------------------------

clean:
	rm -rf web/dist
