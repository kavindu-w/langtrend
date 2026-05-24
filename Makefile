PYTHON ?= ./venv/bin/python
DATA_ROOT ?= data
WINDOW_DAYS ?= 7
MAX_RESULTS ?= 100

.PHONY: help setup pipeline web-install web-dev web-build dev build clean

help:
	@echo "Targets:"
	@echo "  make setup       Install Python and web dependencies"
	@echo "  make pipeline    Run the LangTrend snapshot pipeline"
	@echo "  make web-dev     Start the Astro dev server"
	@echo "  make web-build   Build the Astro site"
	@echo "  make dev         Alias for web-dev"
	@echo "  make build       Run pipeline and build the site"

setup:
	pip install -r requirements.txt
	cd web && npm install

pipeline:
	$(PYTHON) scripts/run_langtrend_pipeline.py --data-root $(DATA_ROOT) --window-days $(WINDOW_DAYS) --max-results $(MAX_RESULTS)

web-install:
	cd web && npm install

web-dev: web-install
	cd web && npm run dev

web-build: web-install
	cd web && npm run build

dev: web-dev

build: pipeline web-build

clean:
	rm -rf web/dist
