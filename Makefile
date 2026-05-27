PYTHON      ?= ./venv/bin/python
DATA_ROOT   ?= data
WINDOW_DAYS ?= 7
MAX_RESULTS ?= 1000
WORKERS     ?= 12
# END_DATE    ?= 2026-05-04
# END_DATE    ?= 2026-05-11
# END_DATE    ?= 2026-05-18
END_DATE    ?= 2026-05-25

# Pass --end-date only when END_DATE is set
_END_DATE_FLAG = $(if $(END_DATE),--end-date $(END_DATE),)

.PHONY: help setup fetch process reprocess retry-missing manifest pipeline web-install web-dev web-build dev build clean

help:
	@echo "Pipeline targets:"
	@echo "  make fetch            Fetch papers from arXiv (skipped if JSONL already exists)"
	@echo "  make process          Detect languages in HTML/PDF (skipped if already done)"
	@echo "  make reprocess        Re-run cleaning+detection on cached text only (no downloads)"
	@echo "  make retry-pdf        Retry PDF download for papers where it previously failed"
	@echo "  make retry-missing    Retry papers not yet detected or with no html/pdf cache (uses cache, downloads missing)"
	@echo "  make manifest         Build the web manifest from caches (always re-runs, fast)"
	@echo "  make pipeline         Run all three steps above in sequence"
	@echo ""
	@echo "Web targets:"
	@echo "  make web-dev          Start the Astro dev server"
	@echo "  make web-build        Build the Astro site"
	@echo "  make build            Run pipeline + build the site"
	@echo ""
	@echo "Other:"
	@echo "  make setup            Install Python and web dependencies"
	@echo "  make clean            Remove build artefacts"
	@echo ""
	@echo "Overrides:"
	@echo "  DATA_ROOT=$(DATA_ROOT)  WINDOW_DAYS=$(WINDOW_DAYS)  MAX_RESULTS=$(MAX_RESULTS)"
	@echo "  WORKERS=$(WORKERS)  END_DATE=$(if $(END_DATE),$(END_DATE),(last Monday))"

setup:
	pip install -r requirements.txt
	cd web && npm install

# --- Individual pipeline steps -------------------------------------------

fetch:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		$(_END_DATE_FLAG) \
		--skip-process

process:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		--skip-fetch

reprocess:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		--skip-fetch \
		--reprocess-cache


retry-missing:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG) \
		--skip-fetch \
		--retry-missing

manifest:
	$(PYTHON) scripts/build_manifest.py \
		--window-days $(WINDOW_DAYS)

# --- Full pipeline ----------------------------------------------------------

pipeline:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		--workers $(WORKERS) \
		$(_END_DATE_FLAG)

# --- Web --------------------------------------------------------------------

web-install:
	cd web && npm install

web-dev: web-install
	cd web && npm run dev

web-build: web-install
	cd web && npm run build

dev: web-dev

build: pipeline web-build

# --- Housekeeping -----------------------------------------------------------

clean:
	rm -rf web/dist
