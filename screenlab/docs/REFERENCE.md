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
