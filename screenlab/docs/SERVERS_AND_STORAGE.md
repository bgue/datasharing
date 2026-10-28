# Servers, asynchronous runs and the run store

Part of the [documentation map](README.md). Endpoint-by-endpoint detail is in
[server/README.md](../server/README.md); installation of the servers is in [GETTING_STARTED.md](GETTING_STARTED.md).

## Servers
- **Servers** (`server/`): a FastAPI REST API and an MCP server expose the
  same operations (define, picks, backtest, timing, graham, sweep,
  significance, extended, exit evaluate / calibrate) as JSON over one
  `LabRegistry`. `pip install -r screenlab/requirements-server.txt`,
  then `python -m screenlab.server.rest` or `claude mcp add screenlab
  -- python -m screenlab.server.mcp_server`. See `server/README.md`.
  Every error is `{"error": message}` with a status: 400 bad input (the
  message names the offending field), 404 unknown lab / screen, 501 not
  supported; unexpected failures are a bare 500 `internal error`. In the SDK,
  `Lab.screen` / `Lab.picks` raise `KeyError("unknown screen: <name>")`.


## Asynchronous runs and persistence
Long studies (a daily timing study is minutes, a calibration longer) run as jobs, and nothing has to be
recomputed. Three independent pieces (DESIGN.md section I):

    from screenlab import Lab, SQLiteStore, JobManager, RunSpec

    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    lab.name, lab.store = "synthetic", SQLiteStore("runs.sqlite")     # or env SCREENLAB_STORE=runs.sqlite
    lab.backtest(["dogs"], rebalance_months=6)         # recorded: spec, metrics, trial counted
    job = lab.submit("timing", screen="dogs", stride=1)   # runs in the background
    job.status, job.progress, job.message              # queued/running/done/failed/cancelled, 0..1
    # job.cancel()                                     # would stop it within one rebalance / offset / point
    res = job.result()                                 # the object lab.timing(...) returns (a StoredResult with .table on a cache hit)
    lab.submit("timing", screen="dogs", stride=1).cache_hit    # identical run: from the store, instantly

- **`RunSpec(kind, dataset, args, seed, label, tags)`** (`runspec.py`) is a run as plain data. `to_dict` /
  `from_dict` serialize every input (Config with HoldRule / ExitPolicy / per-security maps / `cap_tiered` tiers,
  ExitScreen and signals by name + params, Portfolio, benchmarks, screens, Grid, MacroSeries by reference).
  The types own this (`to_dict()` / `from_dict()`, `serial.py`; `cap_tiered(...)` returns the picklable `CapTiered`),
  and deprecated signal parameter names are replaced by the new ones first, so `Breadth(window_days=200)` and the
  equivalent `Breadth(days=...)` share a run key.
  `run_key = sha256(kind, args, seed, dataset_id, code_version)`; arguments that are None or equal to the
  default are dropped, so `lab.timing("dogs")` and `{"screen": "dogs", "stride": 1}` are one run. A Python
  callable input (function weighting, custom cost, a `Screen(fn=...)` whose function is a lambda, closure or
  lives in `__main__`) still runs, but the spec is not
  `persistable`: no key, no cache, no `rerun`. A `Screen` whose `fn` is an importable module-level function (like the
  S&P 500 lab's price-only screens) is persistable: it is stored as `module:qualname` and re-imported, and the
  reference is part of the lab-context hash. A MacroSeries needs `runspec.macro_ref(macro, name)` to be
  persistable. The `run_key` of an SDK call ignores `lab.config` and `lab.screens`; pass overrides as arguments.
- **Config overrides over REST/MCP** (`ConfigOverrides` in `ops/_request.py`, typed in `/openapi.json`): besides the
  scalar fields, `weighting` (`equal` / `cap` / `inv_vol`), `max_weight`, `sector_cap` and
  `hold_rule` (`{"keep_within": number >= 1 | null, "band": number >= 0 | null}`, becomes a `HoldRule`) reach `Config`
  for every operation that takes `config`; a bad value is a 400 naming the field, e.g.
  `config.hold_rule.keep_within must be a number >= 1, got 'x'`. Different `hold_rule`s give different run keys.
- **Operations** (`operations.py`, `ops/*.py`): each analysis (picks, backtest, timing, graham, sweep, significance,
  extended, exit_evaluate, exit_calibrate) is one `Operation` in `OPERATIONS`: name, typed `Args`, `run(lab, **kw)`,
  `to_json`, `metrics`, `family`, `long_running`. `Lab.<name>`, `lab.run(name, ...)`, job kinds, run keys, store metrics,
  REST routes (typed OpenAPI bodies, `GET /operations`) and MCP tools are all derived from the registry, so a new
  analysis is one module: define a class, decorate it with `@register_operation`
  (see `tests/test_operations.py` for a complete example).
- **`metrics.METRICS`**: every metric name with label, unit, better-direction, format and description; code uses
  `metrics.M.CAGR` etc. and the LinkML `StandardMetric` enum is generated from it.
- **`JobManager(store, workers, mode="process"|"thread", labs={name: factory})`** (`jobs.py`): spawn-based
  process pool (factories must be importable module-level callables; thread mode is for tests and small
  machines). Dedups against the store and against jobs already running, reports progress and honours cancel
  through `progress.py` hooks in `backtest.simulate`, `timing.study`, `run_sweep` and `calibrate_exit`.
  Workers talk to the parent through the store's `jobs` table, or a multiprocessing Manager without a store.
  `Lab(jobs=...)` shares a pool; without one `lab.submit` uses a private thread pool.
- **`SQLiteStore(path)` / `NullStore`** (`store.py`): runs, long-format metrics, gzip-CSV artifacts, datasets,
  trials and jobs in one SQLite file (WAL, safe for several processes). Off in the SDK by default (no files are
  written). Levels `persist="none|inputs|metrics|full"` (`full` keeps curves and per-offset tables). Query with
  `store.find(kind=, screen=, dataset=, since=, where={"Excess/yr": (">", 0.02)})`, compare with
  `store.compare([k1, k2])`, check reproducibility with `store.rerun(key, executor)`, and manage with
  `export`, `delete`, `prune(keep_last=, older_than=)`.
- **Trial counting**: each distinct run_key is a trial of its family (kind + dataset + screen base); every
  sweep / calibration grid point counts too. `significance`, `sweep` and `calibrate_exit` deflate the Sharpe by
  that count (`result.attrs["n_trials"]`, `CalibrationResult.summary["n_trials"]`), and every server result
  JSON carries `trials_in_family` and `trials_scope` ("store", or "process" when there is no store).
- **Server**: the store is on by default, at `~/.screenlab/runs.sqlite` (override with `SCREENLAB_STORE`; `none`
  turns it off). `POST /jobs {kind, lab, args, seed?, label?, tags?, force?}` -> `{job_id, run_key, status,
  cache_hit}`, `GET /jobs/{id}` (status, progress), `GET /jobs/{id}/result` (the synchronous endpoint's JSON; 409
  until done), `DELETE /jobs/{id}`, `GET /jobs?status=`; MCP: `submit_run, run_status, run_result,
  cancel_run, list_runs`. Synchronous endpoints stop after `SCREENLAB_SYNC_TIMEOUT` seconds (default 120) with a
  408 that points at `/jobs`. Details in `server/README.md`.

### Schema and walk-through of the run store
`schema/runstore.linkml.yaml` documents every table and column of the SQLite store (units, how `run_key` is built,
enums for kind / status / persist level, the standard metric names); `tests/test_schema.py` fails when it drifts from
`store.py`. `notebooks/explore_runs.ipynb` is a plain-language tour: run backtests and a background job into a store,
then list, pivot, compare, plot, query with SQL and rerun them (see `notebooks/README.md`).
