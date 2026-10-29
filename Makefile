# screenlab task menu. `make` or `make help` lists the targets.
.DEFAULT_GOAL := help
PY ?= python
DEST ?= data
SOURCES ?= sec,boc,fred,stooq,statcan
TICKERS ?= XIU.TO,XIC.TO,SPY.US,^SPX
PERIODS ?=
START ?= 2000-01-01
PORT ?= 8000
SEC_USER_AGENT ?=
DATE := $(shell date +%Y%m%d)
FETCH = $(PY) -m screenlab.realdata.cli free --dest "$(DEST)" --start $(START) \
	$(if $(SEC_USER_AGENT),--sec-user-agent "$(SEC_USER_AGENT)") $(if $(PERIODS),--periods $(PERIODS))

.PHONY: help install test test-fast tui tui-serve rest mcp notebook zip data-free data-sec data-boc \
	data-fred data-prices data-tr data-status data-hosts clean

help:  ## list the targets
	@echo "screenlab targets (variables: SEC_USER_AGENT, SOURCES, TICKERS, PERIODS, DEST, START, PORT)"
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:  ## install with all extras (uv if present, else pip)
	@if command -v uv >/dev/null 2>&1; then uv pip install -e ".[all]"; else $(PY) -m pip install -e ".[all]"; fi

test:  ## full test suite
	$(PY) -m pytest screenlab/tests -q -p no:cacheprovider

test-fast:  ## golden + API tests only
	$(PY) -m pytest screenlab/tests/test_golden.py screenlab/tests/test_api.py -q -p no:cacheprovider

tui:  ## terminal UI
	$(PY) -m screenlab.tui.app

tui-serve:  ## terminal UI in a browser (PORT=8000)
	$(PY) -m screenlab.tui.app --serve --port $(PORT)

rest:  ## REST server (PORT=8000)
	$(PY) -m screenlab.server.rest --port $(PORT)

mcp:  ## MCP server on stdio
	$(PY) -m screenlab.server.mcp_server

notebook:  ## JupyterLab on the explore_runs notebook
	jupyter lab screenlab/notebooks/explore_runs.ipynb

zip:  ## git archive of HEAD into dist/screenlab-<date>.zip
	@mkdir -p dist
	git archive --format=zip --prefix=screenlab-$(DATE)/ -o dist/screenlab-$(DATE).zip HEAD
	@echo dist/screenlab-$(DATE).zip

data-free:  ## download all free datasets (SEC_USER_AGENT="Name email" needed for SEC)
	$(FETCH) --sources $(SOURCES) --tickers $(TICKERS)

data-sec:  ## SEC fundamentals only
	$(FETCH) --sources sec

data-boc:  ## Bank of Canada macro only
	$(FETCH) --sources boc

data-fred:  ## FRED macro only
	$(FETCH) --sources fred

data-prices:  ## Stooq prices and index proxies only
	$(FETCH) --sources stooq --tickers $(TICKERS)

data-tr:  ## total-return benchmarks: Shiller S&P 500 (monthly, free) + Tiingo (TIINGO_API_KEY)
	$(FETCH) --sources shiller,tiingo

data-status:  ## show what is in data/ and which labs can be built
	$(PY) -m screenlab.realdata.cli status --dest "$(DEST)"

data-hosts:  ## print the network allow-list (incl. shillerdata.com, api.tiingo.com)
	$(PY) -m screenlab.realdata.cli hosts

clean:  ## remove caches and dist/ (never data/)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf dist .pytest_cache
