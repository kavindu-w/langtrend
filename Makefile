PYTHON      ?= ./venv/bin/python
DATA_ROOT   ?= data
WINDOW_DAYS ?= 7
MAX_RESULTS ?= 1000
WORKERS     ?= 12

.PHONY: help setup fetch process manifest pipeline web-install web-dev web-build dev build clean

help:
	@echo "Pipeline targets:"
	@echo "  make fetch       Fetch papers from arXiv (skipped if JSONL already exists)"
	@echo "  make process     Detect languages in HTML/PDF (skipped if already done)"
	@echo "  make manifest    Build the web manifest from caches (always re-runs, fast)"
	@echo "  make pipeline    Run all three steps above in sequence"
	@echo ""
	@echo "Web targets:"
	@echo "  make web-dev     Start the Astro dev server"
	@echo "  make web-build   Build the Astro site"
	@echo "  make build       Run pipeline + build the site"
	@echo ""
	@echo "Other:"
	@echo "  make setup       Install Python and web dependencies"
	@echo "  make clean       Remove build artefacts"
	@echo ""
	@echo "Overrides:  DATA_ROOT=$(DATA_ROOT)  WINDOW_DAYS=$(WINDOW_DAYS)  MAX_RESULTS=$(MAX_RESULTS)  WORKERS=$(WORKERS)"

setup:
	pip install -r requirements.txt
	cd web && npm install

# --- Individual pipeline steps -------------------------------------------

fetch:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		--skip-process

process:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--workers $(WORKERS) \
		--skip-fetch

manifest:
	$(PYTHON) scripts/build_manifest.py \
		--output-dir $(DATA_ROOT)/processed \
		--window-days $(WINDOW_DAYS)

# --- Full pipeline ----------------------------------------------------------

pipeline:
	$(PYTHON) scripts/run_langtrend_pipeline.py \
		--data-root $(DATA_ROOT) \
		--window-days $(WINDOW_DAYS) \
		--max-results $(MAX_RESULTS) \
		--workers $(WORKERS)

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
