# Notebooks

`explore_runs.ipynb` runs a few backtests and one background job into a run store, then lists, pivots, compares, plots, queries (SQL) and reruns them. Written for a non-programmer; about 35 seconds end to end.

* Install: `pip install numpy pandas matplotlib jupyterlab nbclient ipykernel skfolio` (skfolio supplies the bundled S&P 500 data; run from the repository root so `screenlab` imports, or `pip install -e` it).
* Interactive: `jupyter lab screenlab/notebooks/explore_runs.ipynb`, then Run All.
* Headless, with outputs saved in place: `cd screenlab/notebooks && python -m nbclient --inplace explore_runs.ipynb` (or `jupyter nbconvert --to notebook --execute --inplace explore_runs.ipynb`).
* It writes a local SQLite file, `screenlab/notebooks/runs.sqlite` (plus `-wal`/`-shm` side files while open), deleted and recreated on every run from the top. It is git-ignored; change `STORE_PATH` / `START_FRESH` in the first code cell. Nothing leaves your machine.
* Column and unit reference for that file: `screenlab/schema/runstore.linkml.yaml` (guarded against drift by `tests/test_schema.py`).

## `montecarlo.ipynb`

How wide is the range of outcomes of momentum and low volatility (each with and without the fixed trend exit) against the S&P 500, on 1,000 resampled futures? Plain-language walk through the data card and warnings, the quantile table, the fan chart, 10-year growth histograms against the index, the paired per-path differences and the worst-fall (drawdown) histograms, then "how to read this" and "what this cannot tell you" (survivorship, +10 points a year; price-only index). About 20 seconds; it writes no files. Headless: `cd screenlab/notebooks && python -c "import nbformat; from nbclient import NotebookClient as C; nb=nbformat.read('montecarlo.ipynb',4); C(nb,kernel_name='python3').execute(); nbformat.write(nb,'montecarlo.ipynb')"`. Its charts come from `research/montecarlo_histograms.py`, so the notebook and the research script share one set of plotting functions.

## `accounts_and_foreign.ipynb`

Where should a portfolio live, a taxable account, a TFSA or an RRSP? Runs `lab.account_compare(...)` (the same portfolio and the passive index under each account, Canadian model for Alberta, other income $90,000 a year, RRSP withdrawal taxed at 30%) on a Globe and Mail dividend screen over the synthetic Canadian market and on two screens over the bundled S&P 500 sample (US names, with an assumed 2% yield so that foreign withholding exists). It shows the comparison table (after-tax CAGR, tax drag, after-tax excess over the equally taxed index, withholding lost / credited / exempt, tax paid on dividends, gains and the RRSP withdrawal, rank), a bar chart of after-tax CAGR by account, the withholding treatment matrix for each kind of holding, and a reading guide with what it ignores (contribution room, the RRSP deduction refund, AMT). About 20 seconds, offline, writes no files. Headless: `cd screenlab/notebooks && python -c "import nbformat; from nbclient import NotebookClient as C; nb=nbformat.read('accounts_and_foreign.ipynb',4); C(nb,kernel_name='python3').execute(); nbformat.write(nb,'accounts_and_foreign.ipynb')"`. `tests/test_account_compare.py` executes its code cells too. The Accounts tab of the TUI ([../tui/README.md](../tui/README.md)) shows the same comparison for the selected scenario.
