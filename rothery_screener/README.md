# Rothery-style multi-portfolio screener & backtester (sketch)

This sketch runs several classic screens side by side, each as its own
equal-weight portfolio rebalanced on a fixed schedule. It works the way Norman
Rothery tracks his screen portfolios in The Globe and Mail: pick the stocks,
hold them for a year, compare against the index, then repeat.

```
python -m rothery_screener.run                          # synthetic daily market, all screens
python -m rothery_screener.run --only dogs,piotroski --rebalance 6 --cost-bps 20 --mer 0.002
python -m rothery_screener.run --graham-report           # funnel: survivors after each Graham test
python -m rothery_screener.run --set graham_defensive.max_pe=18 --set graham_defensive.min_passes=6
python -m rothery_screener.timing --screens dogs --stride 1    # every daily start, one year of offsets
python -m rothery_screener.run --csv fundamentals.csv prices.csv --start 2005-01-31
```

## SDK
```python
from rothery_screener import Lab, ScreenBuilder, IndexBenchmark

lab = Lab.synthetic()                       # or Lab.from_csv("fund.csv", "prices.csv", "indices.csv")
lab.benchmark = IndexBenchmark("SYN_CAPWT", mer=0.0006, cost_bps=0)   # buy-and-hold index fund

lab.define(ScreenBuilder("cheap_yield")
           .universe(top=100, exclude_sectors=["Fin"])
           .where("pe < 15", "div_yield > 0.03", "f_score >= 6")
           .rank(div_yield="desc", pb="asc")        # composite: sum of ranks
           .take(10))

lab.picks("cheap_yield")                             # current names + key ratios
rep = lab.backtest(["dogs", "cheap_yield"], rebalance_months=6, cost_bps=20, mer=0.002)
print(rep); rep.table; rep.curves                    # formatted / raw stats / equity curves
tr = lab.timing("cheap_yield", stride=5)             # daily-start timing study
tr.distribution(); tr.tranche
report, funnel = lab.graham("defensive", max_pe=18, min_passes=6)
```

You can also write a screen as a dict or JSON, and load it with
`lab.define(...)` or `--screen-file`:

```json
{"name": "cheap_yield", "n": 10,
 "universe": {"top": 100, "exclude_sectors": ["Fin"]},
 "derive": {"pe_x_pb": "pe * pb"},
 "filters": ["pe < 15", "div_yield > 0.03"],
 "graham": {"preset": "defensive", "max_pe": 18, "min_passes": 6},
 "rank": {"div_yield": "desc", "pe_x_pb": "asc"}}
```

The steps run in this order: universe (size cut first, then sectors) →
derived columns → filters → Graham tests → rank → take n. Filters are pandas
expressions over the snapshot columns. For anything the spec can't express,
register a plain function as `Screen(name, fn, n)` with
`fn(snapshot, n, **params) -> [tickers]`.

**Benchmark.** Every result is benchmarked against a passive, buy-and-hold
index: one purchase on the portfolio's start day, then daily MER drag (MER and
trading cost are set separately from the portfolio's and default to 0). That
is an explicit `IndexBenchmark`, else the first loaded index series (with the
synthetic data, the cap-weighted `SYN_CAPWT`), else an index built from the
universe and registered in `market.indices`: `PASSIVE_CAPWT` (cap-weighted
total return of all live names) when market caps exist, otherwise
`PASSIVE_EW` (equal weight at the first date, never rebalanced). Excess/yr,
BeatBenchYrs, timing, significance, sweeps and the exit-signal default target
all use it. A screen benchmark such as `equal_weight_top(60)` (passed as
`benchmark=` or in `compare=`) only adds a `... (compare)` row and never
replaces it. CLI: `--benchmark NAME` (index, replaces the passive one),
`--compare NAME|top60`, `--bench-mer X --bench-cost-bps X`.

**Tests.** `python -m pytest rothery_screener/tests -q` runs about 200
tests in roughly 50 seconds, through the SDK where that makes sense:
- exact cost, MER, commission, delisting and benchmark arithmetic on a
  hand-built market;
- spec screens reproducing the built-in screens;
- Graham test toggles;
- the timing windows and tranche blend;
- the CLIs.

| screen | idea |
|---|---|
| `dogs` | the 10 highest yielders among the 30 largest names (Dogs of the TSX, Beat the TSX) |
| `graham_defensive` | Graham's defensive investor: size, CR ≥ 2, no losses, a long dividend record, P/E ≤ 15, P/E×P/B ≤ 22.5 |
| `graham_enterprising` | Graham's looser enterprising rules: P/E ≤ 10, P/B ≤ 1.2 |
| `net_nets` | price below net current asset value |
| `piotroski` | F-score ≥ 8 in the cheapest P/B quintile |
| `magic_formula` | Greenblatt's ranking on earnings yield plus return on capital |
| `value_momentum` | low P/E and low P/B, then the best 12-1 momentum |
| `low_vol_dividend` | the least volatile dividend payers among large caps |

All thresholds are keyword arguments, so you can change them through
`Screen(params=...)` in `screens.REGISTRY`. To add a screen, write
`fn(snapshot, n, **params) -> list[ticker]` and register it.

## Data contract
- `fundamentals.csv` has columns `date,ticker,...` (see the `data.py` docstring;
  the Graham tests need `sales, eps_avg3, eps_avg3_10y_ago, loss_years_5y, loss_years_10y`).
  Each row must hold only what was **known** on that date, so shift each value
  by its reporting lag before you load the file.
- `indices.csv` (optional) has columns `date,index,tri` and holds the
  benchmark series.
- `prices.csv` has columns `date,ticker,tri`: a total-return index. Use daily
  data for timing studies; monthly is enough for everything else.
- Delisted names have to stay in both files. The backtester charges
  `delist_return` when a holding disappears.

## Parameters
| flag | what it does |
|---|---|
| `--rebalance N` | months between rebalances (12 = annual, like the Globe screen portfolios) |
| `--cost-bps X` | one-way cost per security in bps (spread + slippage), charged on each trade's weight change |
| `--cost-tiers '0:75,1000:30,10000:10'` | bps by market cap in $M, so small caps cost more to trade |
| `--commission $ --capital $` | flat dollar commission per trade, scaled by portfolio size |
| `--mer X` | annual expense ratio on every holding (e.g. 0.0025), accrued daily |
| `--security-file f.csv` | per-ticker `cost_bps` and/or `mer` overrides (columns: `ticker,cost_bps,mer`) |
| `--set screen.param=value` | override any screen parameter or Graham test; repeatable |

In code, `Config.cost_bps` and `Config.mer` take a float, a `{ticker: value}`
dict (with an optional `"_default"` key), or a function of the snapshot.

### Graham tests
`GrahamTests` (in `screens.py`) holds one threshold per test. Set a threshold
to `None` to drop that test, or set `min_passes` to accept names that fail a
few. There are two presets, `DEFENSIVE` (ch. 14) and `ENTERPRISING` (ch. 15):

size (sales), current ratio, debt (LT debt vs net current assets; utilities
use debt/equity instead), earnings stability (years with losses in a 5- or
10-year window), dividend record, earnings growth (3-yr average EPS vs 10
years ago), P/E (on 3-yr average or trailing EPS), and P/B *or* P/E x P/B.

`--graham-report` prints how many names survive each test in turn, and how
many tests each name passes.

## Timing (daily starts)
`timing.py` reruns one screen with its rebalance date moved forward one
trading day at a time, across a full rebalance period. There are two modes:

- `--mode phase` (default): every variant starts and ends on the same days,
  and only the rebalance anniversary moves. Differences between the variants
  are therefore pure timing luck. The tool also reports a **tranche blend**:
  capital split evenly across all anniversaries, the usual cure for timing luck.
- `--mode start`: actually start investing on each day and hold to the end.
  Compare these runs on excess return, because each covers a different window.

The output shows the spread of CAGR, excess return, drawdown, volatility and
the share of years that beat the benchmark (min / 5% / median / 95% / max),
plus the best and worst anniversary. `--out file.csv` saves the per-offset
table. A full daily sweep takes about 70 seconds per screen on the synthetic
data; `--stride 5` tests weekly offsets instead.

## Backtest mechanics
- Each rebalance day screens a snapshot of the latest *reported*
  fundamentals. Price, market cap and EV are marked to that day's price, so
  P/E, P/B and yield are current.
- Picks are equal-weighted, and weights drift between rebalances. If fewer
  than `min_holdings` stocks pass, the portfolio sits in cash, which is
  common for strict Graham screens.
- When a holding is delisted, its last value is cut by `delist_return` and
  held as cash until the next rebalance.
- The benchmark is a passive index bought and held (see above); a screen
  can only be an extra comparison row.

## Research features
Each lives in its own module and is reachable from `Lab`; see `DESIGN.md`
for the design and `tests/` for worked examples.

- **Real data** (`realdata/`): `sp500_lab()` runs on 20 real S&P 500 names
  and the S&P 500 index (1990–2022, bundled with `skfolio`); `macro_us()`
  gives real US CPI and T-bill rates (bundled with `statsmodels`). Adapters
  for the Bank of Canada Valet API (FX, prime, CPI) and the SEC Financial
  Statement Data Sets (point-in-time US fundamentals) are written and tested
  on fixtures; `realdata/ACQUIRE.md` lists what to acquire and from where.
- **Construction** (`construction.py`, `Config`): `weighting` = equal / cap /
  inverse-vol / callable, `max_weight`, `sector_cap`, `HoldRule(keep_within,
  band)` for turnover control, `union_screen` and `blend` for
  screen-of-screens portfolios.
- **Statistics and sweeps** (`stats.py`, `sweep.py`): `lab.significance(rep)`
  gives a block-bootstrap CI and Newey–West t-stat on excess return plus the
  deflated Sharpe ratio across the screens tried; `lab.sweep(screen, grid,
  splits=...)` runs grid or random sweeps with walk-forward out-of-sample
  scoring. `bayes_search` uses `optuna` when installed;
  `docs/BAYESIAN_SWEEPS.md` explains when (rarely) that is a good idea.
- **Extended metrics** (`macro.py`, `metrics_ext.py`): `lab.extended(rep,
  macro)` adds real (CPI-deflated) returns, home-currency returns via an FX
  series, net-of-financing returns against prime plus a spread (with a
  breakeven spread and a borrow-to-invest levered curve), and excess over a
  time-varying risk-free rate, plus a geometric decomposition.
- **Exit-signal screen** (`exit_signals.py`): a dated risk score built from
  causal signals (trend break, breadth, volatility regime, drawdown,
  valuation stretch, momentum crash, rate shock, dispersion) with
  hysteresis (on at `threshold`, off again only below `threshold −
  hysteresis`, so the flag doesn't flicker). It runs on its own clock,
  separate from the rebalance calendar: `check="D"` (default), `"W-FRI"` or
  `"M"` says how often the flag may change, `lag` is the bars between
  signal and trade, `min_hold_days` stops whipsaw. `lab.exit_eval(...)`
  reports forward returns in flagged vs unflagged states, lead time before
  the largest drawdowns and a derisking overlay; `Config(exit=
  ExitPolicy(screen))` (or `lab.backtest(..., exit=...)`) applies the flag
  day by day inside a backtest without the entry screen ever seeing it.
- **Exit policy** (`exit_policy.py`): the screen says *when*; `ExitPolicy`
  says *what happens*. `park` = cash, a fixed rate, or any loaded series
  (a T-bill or bond ETF total-return index) with its own MER and cost;
  `exposure_when_flagged` (0 = fully out); `reentry` = `"signal"` (flag
  clears), `"next_rebalance"` (stay out until the entry screen's next
  scheduled date) or `"signal_or_rebalance"`; `reentry_delay_days`
  (confirmation), `reentry_steps` (scale back in gradually);
  `rescreen_on_reentry` (re-run the entry screen on the way back in instead
  of returning to the paper portfolio). `lab.backtest(screens, exit=policy,
  with_and_without=True)` runs every screen with and without it (rows
  `name` / `name+exit`, columns `TimeOut`, `Switches/yr`); `Portfolio(name,
  screen, exit=...)` lets portfolios in one report carry different
  policies. Row labels are unique: two entries with the same name are both
  kept as `dogs`, `dogs#2`, ... (a name equal to the benchmark's gets `#2`
  too). `Config.exit` is the only way to attach an exit. CLI: `--exit "TrendBreak:index=SP500,Breadth" --exit-compare
  --exit-park cash --exit-reentry signal`. The entry picks never depend on
  the flag.
- **More exit inputs** (`exit_inputs.py`): correlation spike, new lows minus
  new highs, short/long vol ratio, death cross, distance from 52-week high,
  downside deviation, a trailing stop on the portfolio's own drawdown,
  portfolio-vs-index drawdown, earnings breadth, earnings yield minus rf,
  yield-curve inversion, credit spread. Each declares what data it needs
  and returns NaN without it.
- **Calibration** (`calibration.py`): `lab.calibrate_exit(screen, grid,
  splits=...)` sweeps threshold, hysteresis, cadence, lag, hold period,
  signal weights and signal parameters, scored by Calmar (default), Sharpe,
  CAGR, MaxDD or the flagged/unflagged forward-return gap, walk-forward,
  under constraints (max switches per year, time flagged). It reports
  out-of-sample vs in-sample, **stability** (how often the same point wins
  across windows) and the deflated Sharpe of the winner, and hands back a
  ready `ExitPolicy` (`result.policy`).
- **Servers** (`server/`): a FastAPI REST API and an MCP server expose the
  same operations (define, picks, backtest, timing, graham, sweep,
  significance, extended, exit evaluate / calibrate) as JSON over one
  `LabRegistry`. `pip install -r rothery_screener/requirements-server.txt`,
  then `python -m rothery_screener.server.rest` or `claude mcp add rothery
  -- python -m rothery_screener.server.mcp_server`. See `server/README.md`.
  Every error is `{"error": message}` with a status: 400 bad input (the
  message names the offending field), 404 unknown lab / screen, 501 not
  supported; unexpected failures are a bare 500 `internal error`. In the SDK,
  `Lab.screen` / `Lab.picks` raise `KeyError("unknown screen: <name>")`.

## Asynchronous runs and persistence
Long studies (a daily timing study is minutes, a calibration longer) run as jobs, and nothing has to be
recomputed. Three independent pieces (DESIGN.md section I):

    from rothery_screener import Lab, SQLiteStore, JobManager, RunSpec

    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    lab.name, lab.store = "synthetic", SQLiteStore("runs.sqlite")     # or env ROTHERY_STORE=runs.sqlite
    lab.backtest(["dogs"], rebalance_months=6)         # recorded: spec, metrics, trial counted
    job = lab.submit("timing", screen="dogs", stride=1)   # runs in the background
    job.status, job.progress, job.message              # queued/running/done/failed/cancelled, 0..1
    job.cancel()                                       # stops within one rebalance / offset / point
    res = job.result()                                 # the same object lab.timing(...) returns
    lab.submit("timing", screen="dogs", stride=1).cache_hit    # identical run: from the store, instantly

- **`RunSpec(kind, dataset, args, seed, label, tags)`** (`runspec.py`) is a run as plain data. `to_dict` /
  `from_dict` serialize every input (Config with HoldRule / ExitPolicy / per-security maps / `cap_tiered` tiers,
  ExitScreen and signals by name + params, Portfolio, benchmarks, screens, Grid, MacroSeries by reference).
  `run_key = sha256(kind, args, seed, dataset_id, code_version)`; arguments that are None or equal to the
  default are dropped, so `lab.timing("dogs")` and `{"screen": "dogs", "stride": 1}` are one run. A Python
  callable input (function weighting, custom cost, `Screen(fn=...)`) still runs, but the spec is not
  `persistable`: no key, no cache, no `rerun`. A MacroSeries needs `runspec.macro_ref(macro, name)` to be
  persistable. The `run_key` of an SDK call ignores `lab.config` and `lab.screens`; pass overrides as arguments.
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
- **Server**: the store is on by default, at `~/.rothery/runs.sqlite` (override with `ROTHERY_STORE`; `none`
  turns it off). `POST /jobs {kind, lab, args, seed?, label?, tags?, force?}` -> `{job_id, run_key, status,
  cache_hit}`, `GET /jobs/{id}` (status, progress), `GET /jobs/{id}/result` (the synchronous endpoint's JSON; 409
  until done), `DELETE /jobs/{id}`, `GET /jobs?status=`; MCP: `submit_run, run_status, run_result,
  cancel_run, list_runs`. Synchronous endpoints stop after `ROTHERY_SYNC_TIMEOUT` seconds (default 120) with a
  408 that points at `/jobs`. Details in `server/README.md`.

## Next steps
See [ROADMAP.md](ROADMAP.md).
