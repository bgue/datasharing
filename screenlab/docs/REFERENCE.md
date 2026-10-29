# Reference: SDK tour, command-line flags, benchmark, data contract, mechanics, research features

Part of the [documentation map](README.md). Screens are in [SCREENS.md](SCREENS.md), exits in
[EXITS.md](EXITS.md), servers and the run store in [SERVERS_AND_STORAGE.md](SERVERS_AND_STORAGE.md).

## Command-line examples
```
python -m screenlab.run                          # synthetic daily market, all screens
python -m screenlab.run --only dogs,piotroski --rebalance 6 --cost-bps 20 --mer 0.002
python -m screenlab.run --graham-report           # funnel: survivors after each Graham test
python -m screenlab.run --set graham_defensive.max_pe=18 --set graham_defensive.min_passes=6
python -m screenlab.timing --screens dogs --stride 1    # every daily start, one year of offsets
python -m screenlab.run --csv fundamentals.csv prices.csv --start 2005-01-31
python -m screenlab.run --data sp500 --only momentum_12_1,low_vol --rebalance 1   # bundled real sample
```
`screenlab-run` and `screenlab-timing` are the installed names for `python -m screenlab.run` and
`python -m screenlab.timing`. `--data {synthetic,sp500}` picks the data (default synthetic; `sp500` is the 20
bundled real names and needs the `data` extra); `--csv` loads your own files instead; `--version` prints the
version.

## SDK tour
```python
from screenlab import Lab, ScreenBuilder, IndexBenchmark

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

Screens can also be written as a dict or JSON; see [SCREENS.md](SCREENS.md).

## Benchmark
Every result is benchmarked against a passive, buy-and-hold
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

### Benchmark kind: price vs total return
Portfolios are total return, so the benchmark has to be. Every index series in a dataset has a **kind**,
recorded in its data card (`DataCard.index_kinds`): `total_return`, `price` (dividends left out), and, for a
benchmark built with a dividend yield, `approx_total_return`. `IndexBenchmark(index, mer, cost_bps, name,
kind=None, dividend_yield=None)` takes its kind from the dataset unless `kind=` overrides it:

| dataset | kind of its index |
|---|---|
| synthetic (`SYN_CAPWT`), the passive index built from the universe (`PASSIVE_CAPWT` / `PASSIVE_EW`), a ticker | `total_return` |
| bundled S&P 500 (`SP500`) | `price` |
| `CSVBuilder` / `Lab.from_csv` | the `kind` column of `indices.csv` (`price` or `total_return`, per index); with no such column `total_return` is **assumed** and the data card warns that it is assumed (`CSVBuilder(index_kind=...)` states it for all) |
| `Dataset.from_frames(..., index_kinds={...})` | what you pass; unlisted indices count as `total_return` |

`dividend_yield=0.02` turns a price index into an approximate total return, `price x (1 + y) ** years`
(years from the benchmark's start, actual days / 365.25; MER and cost apply on top). It is labelled
`SP500 +2.0% div (approx. TR)`, has kind `approx_total_return`, and the data card carries a warning that it
is an approximation (the true S&P 500 yield varied between about 1% and 4%). A yield on a series that is
already total return is an error (it would count dividends twice).

**The rule.** Comparing total-return portfolios with a `kind="price"` benchmark is **refused** with a
`BenchmarkKindError` (a `ValueError`; REST/MCP: 400 with the same message) in `backtest`, `timing`,
`significance`, `extended`, `sweep`, `exit_eval`, `calibrate_exit` and `montecarlo`. The message names the three
fixes: `IndexBenchmark("SP500", dividend_yield=0.02)` (REST/MCP `benchmark={"index": "SP500", "dividend_yield":
0.02}`), a total-return series, or `allow_price_benchmark=True`. The flag (`Lab(allow_price_benchmark=True)`, or
`lab.backtest(..., allow_price_benchmark=True)` per call, `--allow-price-benchmark` on the CLI, a request
field over REST/MCP) keeps the old behaviour: it runs, with the "price index" warning on the data card.
`significance` and `extended` follow the report they are given: a report made with the flag passes, any other
report on a price benchmark is refused. Extra `compare=` rows are not checked, and `exit_eval` /
`calibrate_exit` check only the default (passive) target, not a `target=` you name.

`sp500_lab()` uses `IndexBenchmark("SP500", dividend_yield=0.02)` by default (`sp500_lab(dividend_yield=0.03)`
changes the assumption); `sp500_lab(benchmark="price", allow_price_benchmark=True)` is the raw index.

REST/MCP `benchmark` is an index name, `"topN"`, or an object `{"index": "SP500", "dividend_yield": 0.02,
"mer": 0.0, "cost_bps": 0}` (`kind` is also accepted); `montecarlo`, `timing`, `significance`, `extended` and
`sweep` take it as well as `backtest`.

## Tests
`python -m pytest screenlab/tests -q` runs about 200
tests in roughly 50 seconds, through the SDK where that makes sense:
- exact cost, MER, commission, delisting and benchmark arithmetic on a
  hand-built market;
- spec screens reproducing the built-in screens;
- Graham test toggles;
- the timing windows and tranche blend;
- the CLIs.

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
for the design and `tests/` for worked examples. The exit-related features are in [EXITS.md](EXITS.md); the
servers are in [SERVERS_AND_STORAGE.md](SERVERS_AND_STORAGE.md).

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

## Monte Carlo (`lab.montecarlo`, `ops/montecarlo.py`, `montecarlo/generators.py`)
Question: "how wide is the range of outcomes for this portfolio, and how often does it trail the passive index over
my horizon?" One scenario generator draws a `PathSet` from the lab's dataset; every screen and the passive index run
on the same paths (`pipeline.simulate_paths`), and the distributions are read off per horizon. Worked study:
[research/montecarlo/MONTECARLO_SP500.md](../research/montecarlo/MONTECARLO_SP500.md).
Study of every REGISTRY screen and Globe portfolio on the synthetic market (auto selection, `python -m screenlab.research.montecarlo_all`): [research/montecarlo_all/MONTECARLO_ALL.md](../research/montecarlo_all/MONTECARLO_ALL.md).

```python
from screenlab.realdata.bundled import sp500_lab
lab = sp500_lab(n=5)
r = lab.montecarlo(["momentum_12_1", "low_vol"], generator={"type": "BlockBootstrap", "block_months": 12},
                   n_paths=1000, horizon_years=[5, 10, 20], start_wealth=100_000, rebalance_months=1,
                   cost_bps=25, seed=0, target_wealth=300_000)
print(r)                      # data card, warnings, table
r.table, r.per_path, r.fan    # summary per (portfolio, horizon); one row per path; wealth quantile curves
```

| Argument | Meaning |
|---|---|
| `screens` | any screen of the lab (default: all) |
| `selection` | `"auto"` (default): registered price ranks (`PRICE_RANKS`: `momentum_12_1`, `momentum_6m`, `momentum_12m`, `low_vol` as price-only screens, equal weight) re-rank on every path (`per_path`); every other screen runs `fixed`: picks decided on the base history at each step's source date and shared by paths that land on it, so they do not react to the path's own prices. Or force `"per_path"` / `"fixed"`. `result.selection_mode` and the JSON record the mode per portfolio. Fixed mode with an exit: TrendBreak works, other signals raise `NotImplementedError` naming the signal; a hold rule is not supported |
| `generator` | `BlockBootstrap(12)` default; `StationaryBootstrap(mean_block_months)`, `RegimeBootstrap`, `IIDBootstrap` as objects or `{"type": ..., ...}` |
| `n_paths`, `horizon_years`, `seed` | paths (default 1000), horizons (default `[5, 10, 20]`), seed. Horizons are cuts of one set of paths drawn for the longest horizon, so changing the longest changes the paths |
| `start_wealth`, `contributions`, `withdrawals` | per year, spread over the steps. Flows change terminal wealth and `P(shortfall)` only; CAGR, drawdowns and `P(trail)` stay time-weighted |
| `rebalance_months`, `config` | as in `backtest` (`cost_bps`, `mer`, ...) |
| `exit`, `with_and_without` | an exit policy for every screen (TrendBreak only; rows `name` and `name+exit`) |
| `steps` | `"monthly"` (default: base cut to month-ends first, fast; weekly exit checks act monthly) or `"daily"` (memory heavy: paths x steps x names) |
| `target_wealth` | adds `P(shortfall)` = share of paths ending below it |

The first 12 months of every path are a warm-up (price ranks and trend signals need history); horizons are measured
after it. Result columns (all registered in `metrics.METRICS`): `TermWealth_p5..p95`, `CAGR_p5..p95`, `P(trail)`
(share of paths ending behind the index), `ExcessCAGR_med`, `P(MaxDD<-30%)`, `P(MaxDD<-50%)`, `RecoverYrs_med`
(median years from the worst trough back to its peak; empty when over half of the paths had not recovered),
`P(unrecovered)`, `P(shortfall)`. The result carries the data card and its warnings plus a note that bootstrap paths
inherit the data's biases (survivors; a price-only benchmark is refused unless allowed, see Benchmark kind). The operation is `long_running`: `lab.submit("montecarlo", ...)`,
`POST /labs/{lab}/montecarlo`, `POST /jobs {"kind": "montecarlo"}`, MCP tool `montecarlo`. Each distinct generator
configuration is a new trial of the family. Store: summary metrics as rows `portfolio@10y`, the fan curves as an artifact.

## Free dataset download (`fetch_data`, `realdata/fetch.py`, `screenlab-data`)

`fetch_free(dest="data", sources=("sec","boc","fred","stooq","statcan"), sec_user_agent=None, periods=None,
tickers=("XIU.TO","XIC.TO","SPY.US","^SPX"), start="2000-01-01", opener=None, progress=None) -> Manifest` downloads
the free sources of [ACQUIRE.md](../realdata/ACQUIRE.md) 2A into `data/` (raw files under `data/raw/<source>/`;
`fundamentals_sec.csv`, `macro_boc.csv`, `macro_fred.csv`, `macro_statcan.csv` in `date,series,value` form,
`prices_stooq.csv` as `date,ticker,tri` and `indices_stooq.csv` as `date,index,tri` from the Close). `^SPX` and `SPY.US`
are index proxies. Stooq is price-only unless the symbol is adjusted; SEC needs `sec_user_agent` ("Name email") or
`SEC_USER_AGENT`; `periods` default to the last 12 completed quarters. Each source is isolated: a blocked host, a 403 or a
missing user agent becomes an entry in `errors` with the host to allow, never an exception. `data/MANIFEST.json`
holds `files` (source, path, bytes, status `ok`/`cached`, sha256), `errors`, `allow_hosts`, `labs` and `macro`; later
partial runs merge into it. `register_labs(registry, manifest)` registers the `sec_us` lab (needs SEC fundamentals and
Stooq prices) with `register_csv`.

The operation `fetch_data` (`long_running`, no trials counted; args `sources, dest, sec_user_agent, tickers, start,
periods`) returns `{"manifest": {"files": [{"source","path","bytes","status"}], "errors": [{"source","host","error"}],
"allow_hosts": ["host", ...]}, "labs": [{"name","fundamentals","prices","indices"}]}`. Through a `LabRegistry` (REST
`POST /labs/{lab}/fetch_data`, MCP tool `fetch_data`, `POST /jobs {"kind": "fetch_data"}`) the returned labs are also
registered by name. Command line: `screenlab-data free|status|hosts`; the Makefile has `data-free`, `data-sec`,
`data-boc`, `data-fred`, `data-prices`, `data-status`, `data-hosts`. Not yet run against the real hosts.
