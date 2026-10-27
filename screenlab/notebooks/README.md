# Notebooks

`explore_runs.ipynb` runs a few backtests and one background job into a run store, then lists, pivots, compares, plots, queries (SQL) and reruns them. Written for a non-programmer; about 35 seconds end to end.

* Install: `pip install numpy pandas matplotlib jupyterlab nbclient ipykernel skfolio` (skfolio supplies the bundled S&P 500 data; run from the repository root so `screenlab` imports, or `pip install -e` it).
* Interactive: `jupyter lab screenlab/notebooks/explore_runs.ipynb`, then Run All.
* Headless, with outputs saved in place: `cd screenlab/notebooks && python -m nbclient --inplace explore_runs.ipynb` (or `jupyter nbconvert --to notebook --execute --inplace explore_runs.ipynb`).
* It writes a local SQLite file, `screenlab/notebooks/runs.sqlite` (plus `-wal`/`-shm` side files while open), deleted and recreated on every run from the top. It is git-ignored; change `STORE_PATH` / `START_FRESH` in the first code cell. Nothing leaves your machine.
* Column and unit reference for that file: `screenlab/schema/runstore.linkml.yaml` (guarded against drift by `tests/test_schema.py`).
