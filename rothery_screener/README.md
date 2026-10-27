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

**Benchmark.** The default is a buy-and-hold `IndexBenchmark`: one purchase
on the portfolio's start day, then daily MER drag. Its MER and trading cost
are set separately from the portfolio's and default to 0. It uses the first
loaded index series; with the synthetic data that is the cap-weighted
`SYN_CAPWT`. If no index is loaded, the benchmark falls back to an
equal-weight portfolio of the 60 largest names, which pays the portfolio's
costs. CLI flags: `--benchmark NAME|top60 --bench-mer X --bench-cost-bps X`.

**Tests.** `python -m pytest rothery_screener/tests -q` runs 20 tests in
about 10 seconds, all through the SDK:
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
- The benchmark is an index bought and held (see SDK above), or optionally a
  screen.

## Next steps
See [ROADMAP.md](ROADMAP.md).
