# Research-phase design sketch

Scope of this round: real data adapters, portfolio-construction research
features, statistics and parameter sweeps, extended return metrics, and an
exit-signal screen. No live tracking.

Conventions for every module below:
- Pure functions over pandas objects where possible; `Lab` (api.py) is the
  only place that stitches them together, and only the integrator edits
  `api.py`, `__init__.py`, `README.md`, `ROADMAP.md`.
- Each module has its own `tests/test_<module>.py`, run through the public
  SDK where that makes sense, with exact-arithmetic checks on tiny hand-built
  inputs plus behavioural checks on `Lab.synthetic(freq="ME", n_stocks=150,
  years=10, seed=3)`.
- Keep the existing code style: short docstrings that say what and why,
  dataclasses for configs, no new heavy dependencies by default (optional
  ones are imported lazily and skipped in tests when absent).
- Daily and monthly price frequencies both have to work; use calendar
  offsets (`pd.DateOffset(months=...)`) not row counts.

---------------------------------------------------------------------------
## A. Real data (`realdata/`)

Goal: run the pipeline on real prices today, and have adapters ready for the
sources we still have to acquire. Network from this environment is blocked
for every data host, so anything that downloads must (1) work from a
previously saved file, and (2) be tested against a small fixture that
mirrors the real format exactly.

### A1. Bundled datasets (no network) — `realdata/bundled.py`
- `skfolio` wheel ships `skfolio/datasets/data/sp500_dataset.csv.gz` (Date +
  20 tickers, daily adjusted close 1990-01-02 .. 2022-12-28, 8313 rows),
  `sp500_index.csv.gz` (Date, SP500) and `factors_dataset.csv.gz` (5 factor
  ETFs 2014–2022). Adjusted close is a fair stand-in for a total-return
  index. Load via `skfolio.datasets.load_sp500_dataset()` etc. if skfolio is
  installed, else read the csv.gz directly from a path the user supplies.
- `statsmodels.datasets.macrodata` — quarterly US CPI (`cpi`), 3-month
  T-bill (`tbilrate`), `infl`, `realint`, 1959Q1–2009Q3. Real inflation and
  a risk-free rate for the extended metrics demo.
- `sp500_lab()` -> `Lab` with: prices = the 20 names, indices = {"SP500"},
  a price-only fundamentals table (ticker, date, price, sector from a
  hand-written 20-name sector map, market_cap = NaN). `Market.view` and
  `add_ratios` must tolerate missing fundamental columns (fill NaN, skip
  derived columns whose inputs are absent) — this is the one permitted edit
  to `data.py`. Screens that only use price data (`value_momentum` needs
  pe/pb so no; `low_vol_dividend` needs dividends so no) — so add two
  built-in price-only screens in `realdata/bundled.py` and register them
  on that Lab: `momentum_12_1` (top n by mom_12_1 among names with 12 months
  of history) and `low_vol` (bottom n by vol_1y).
- `macro_us()` -> DataFrame indexed by quarter-end with cpi, tbilrate.

### A2. Bank of Canada Valet — `realdata/boc_valet.py`
API: `https://www.bankofcanada.ca/valet/observations/{SERIES}/json?start_date=&end_date=`
Response: `{"observations": [{"d": "2024-01-02", "FXUSDCAD": {"v": "1.3316"}, ...}]}`.
Series we want: `FXUSDCAD` (daily USD/CAD), `V80691311` (prime rate, daily),
`V41690973` (CPI all-items, monthly), `V39079` (overnight target).
`fetch(series, start, end, cache_path)` saves JSON; `load(cache_path)`
returns a DataFrame; `to_macro_csv()` writes `date,series,value`. Tests use a
fixture JSON in the documented shape.

### A3. SEC Financial Statement Data Sets — `realdata/sec_fsds.py`
Free, point-in-time US fundamentals: quarterly zips at
`https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip`
containing `sub.txt` (adsh, cik, name, sic, form, period, fy, fp, filed),
`num.txt` (adsh, tag, version, ddate, qtrs, uom, value), tab-separated.
Adapter: `to_fundamentals(sub, num, tickers_map)` -> our fundamentals.csv
columns, with `date = filed` (knowable date, so no extra lag needed) and a
tag map (Revenues/SalesRevenueNet -> sales, EarningsPerShareDiluted -> eps,
StockholdersEquity/shares -> bvps, AssetsCurrent/LiabilitiesCurrent ->
current_ratio, LongTermDebtNoncurrent -> lt_debt, NetIncomeLoss, Assets,
NetCashProvidedByUsedInOperatingActivities, CommonStockDividendsPerShareDeclared,
WeightedAverageNumberOfDilutedSharesOutstanding, OperatingIncomeLoss -> ebit).
Derive prev-year fields by joining to the same tag one fiscal year earlier;
loss_years_* by counting negative annual NetIncomeLoss. Columns that can't be
derived stay NaN. Test on a tiny fixture of sub/num rows for two companies.

### A4. `realdata/ACQUIRE.md`
A checklist of what to acquire, per source: URL, cost, licence, what
columns it fills, and which hosts need allowing in this environment's
network policy. Cover: SEC FSDS, Bank of Canada Valet, Statistics Canada
(CPI table 18-10-0004), FRED (CPIAUCSL, DPRIME, DEXCAUS, DGS3MO), Stooq
(free daily TSX prices, e.g. `xiu.ca`), EODHD (fundamentals + prices, paid),
TMX/S&P Capital IQ/Refinitiv (institutional), and index TR series
(S&P/TSX Composite TR: not free; proxies XIC/XIU adjusted close).

---------------------------------------------------------------------------
## B. Portfolio construction research (`construction.py` + hooks in `backtest.py`)

Add to `backtest.Config`:
- `weighting: str | Callable = "equal"` — "equal", "cap" (market_cap),
  "inv_vol" (1/vol_1y), or `f(snapshot.loc[picks]) -> weights summing to 1`.
- `max_weight: float | None`, `sector_cap: float | None` — iterative
  clipping with redistribution of the excess pro rata to unclipped names;
  if infeasible (n * max_weight < 1) hold the remainder in cash.
- `hold_rule: HoldRule | None` — turnover control:
  `HoldRule(keep_within=2.0)` keeps a current holding if it still ranks
  within `keep_within * n` on the screen's ranking (needs the screen to
  expose a ranking: add optional `Screen.ranked(snapshot) -> list` that
  defaults to `screen(snapshot)` for n = len(universe); the spec screens in
  `spec.py` can implement it by taking a larger n). Also
  `HoldRule(band=0.2)`: do not trade a name whose weight is within ±band of
  target (relative), to cut rebalance churn.
- `cash_rate` already exists; leave.

`construction.py` holds the weight functions, the clipping algorithm, the
HoldRule dataclass and `blend(results: dict[str, Result], weights) -> Result`
(screen-of-screens: a fixed-weight blend of several Results' curves with
periodic rebalancing between sleeves at the same `rebalance_months`), plus
`union_screen(screens, n)`: a Screen that takes the union of several
screens' picks (dedup, round-robin order).

Tests: exact weights for cap/inv_vol/clipping on 4 hand-built names; hold
rule reduces turnover on the synthetic market without changing the picks
when `keep_within=1`; blend of a curve with itself equals itself; union
screen on synthetic equals set union.

---------------------------------------------------------------------------
## C. Statistics and sweeps (`stats.py`, `sweep.py`, `docs/BAYESIAN_SWEEPS.md`)

### C1. `stats.py`
- `bootstrap_excess(port: pd.Series, bench: pd.Series, block_months=12,
  n=2000, seed=0)` — stationary/block bootstrap of the *paired* periodic
  returns (port − bench), returning mean annualized excess, CI(5%, 95%),
  P(excess > 0). Works at any frequency (infer periods per year).
- `t_stat_excess(port, bench)` — Newey–West t-stat of mean excess return.
- `deflated_sharpe(sharpes: Sequence[float], n_obs, skew=0, kurt=3)` —
  Bailey & López de Prado's DSR for the best of `len(sharpes)` trials.
- `summarize_trials(table: DataFrame)` adds `DSR` and a Bonferroni-style
  haircut column to a sweep/backtest table.

### C2. `sweep.py`
- `Grid(spec: dict[str, list])` -> iterates over `--set`-style overrides
  (`{"dogs.n": [5, 10], "cost_bps": [10, 25]}`: dotted keys go to the
  screen, bare keys to Config).
- `run_sweep(lab, screen, grid, objective="Excess/yr", start, end,
  splits=None)` -> DataFrame, one row per point, with the objective in-sample
  and, when `splits` is given, out-of-sample.
- `walk_forward(dates, train_years, test_years, step_years)` -> list of
  (train_start, train_end, test_end) windows; `run_sweep` picks the best
  point per train window and reports its test-window result; summary =
  mean OOS objective and the ratio OOS/IS (overfitting haircut).
- `random_search(grid, n, seed)` and `bayes_search(space, n, seed)` — the
  latter via `optuna` if installed (TPE sampler), else raise with a clear
  message. The search space is the same dict with (low, high) tuples or
  lists. Objective is evaluated through the same `run_sweep` machinery so
  walk-forward applies to it too.

### C3. `docs/BAYESIAN_SWEEPS.md` — pros and cons, concretely for this tool:
Pro: far fewer backtests than a grid when the space has > 3 dims (each
daily backtest is seconds, a timing sweep is minutes); handles mixed
continuous/discrete; gives a posterior over which params matter.
Con: objective is noisy and nearly flat (annual rebalances => ~20 obs per
run), so the surrogate chases noise; it hides the multiple-comparisons
problem a grid makes obvious (DSR needs the number of trials, and a
Bayesian loop's effective trial count is ill-defined); results are not
reproducible across seeds; the Graham-style screens are mostly thresholds
where a coarse grid with walk-forward is interpretable and good enough.
Recommendation: grid or random search with walk-forward and DSR by default;
Bayesian only for continuous construction params (bands, caps, vol
lookbacks) and only with an OOS objective.

---------------------------------------------------------------------------
## D. Extended return metrics (`macro.py`, `metrics_ext.py`)

### D1. `macro.py`
`MacroSeries` — a container of named daily series aligned (pad-filled) to
a trading-day index: `cpi` (index level), `fx` (CAD per unit of the
portfolio's currency, so a USD portfolio for a CAD investor uses USDCAD),
`prime`, `rf` (annual rates as decimals, e.g. 0.0695), plus arbitrary extras.
Constructors: `from_frame(df)`, `from_csv("date,series,value")`,
`from_statsmodels_macrodata()` (quarterly -> pad), `from_boc_csv()`.
`align(index)` returns a copy on the trading-day index. Rates convert to a
daily accrual series via `(1+r)**(dt/365.25)`; CPI to a deflator
`cpi[t]/cpi[t0]`.

### D2. `metrics_ext.py`
`extended_summary(curve, macro, bench=None, spread_over_prime=0.0,
leverage=1.0) -> dict` adds to `metrics.summarize`:
- Real: `RealCAGR` = (1+CAGR)/(1+inflation)−1 with inflation the CPI CAGR
  over the same window; `RealMaxDD` on the deflated curve; `RealWorst12m`.
- Currency: `CAGR_home` of the curve translated by `fx`; `FX_contrib` =
  CAGR_home − CAGR (geometric: (1+home)/(1+local)−1).
- Financing / cost of capital: hurdle curve `H(t) = Π(1 + prime + spread)`
  accrued daily; `NetOfFinancingCAGR` = CAGR of curve/H; `YearsAboveHurdle`
  fraction of calendar years beating H; `BreakevenSpread` = the constant
  spread over prime at which NetOfFinancing = 0 (solve by bisection);
  with `leverage` L > 1, the levered curve = L·curve − (L−1)·H (borrow to
  invest, interest accrued), report its CAGR and MaxDD.
- Excess over rf: `ExcessOverRf`, `SharpeRf` using the time-varying rf
  series instead of a constant.
- Decomposition table (`decompose(curve, macro)`): nominal = real ⊕
  inflation; home = local ⊕ fx; net = nominal ⊖ financing — geometric so the
  pieces multiply back to the total.
All computed on the curve's own index so daily and monthly both work.
Tests: constant 10%/yr curve, constant 2% CPI, constant 1.5 FX drift,
constant 5% prime: every number is known in closed form.

---------------------------------------------------------------------------
## E. Exit-signal screen (`exit_signals.py`)

Purpose: separate from entry screens, a dated risk score that says when
*upcoming* returns are at greater risk, so a portfolio can derisk (cash,
hedge, or just stop adding). Evaluated on its own, never tuned jointly with
the entry screen.

Design:
- `Signal` protocol: `compute(mkt, macro=None) -> pd.Series` of floats on the
  trading-day index, higher = riskier, each normalized to a rolling
  percentile rank (lookback 10y, expanding before that) so signals are
  comparable. Built-ins:
  - `TrendBreak(index="SP500", window_months=10)` — index below its 10-month
    SMA (Faber); score = −(price/SMA − 1) scaled.
  - `Breadth(window_days=200)` — 1 − fraction of universe names above their
    200-day MA.
  - `VolRegime(window=63)` — realized vol percentile of the index.
  - `DrawdownFromPeak(index)` — current drawdown depth.
  - `ValuationStretch(col="pe", agg="median")` — universe median P/E (or
    CAPE-like `price / eps_avg3`) percentile; skipped if the column is NaN.
  - `MomentumCrash()` — 12-1 momentum spread (top − bottom decile) negative
    after a drawdown.
  - `RateShock(series="prime", months=12)` — change in prime/rf over the
    last year (needs macro).
  - `Dispersion()` — cross-sectional std of 1-month returns.
- `ExitScreen(signals, weights=None, threshold=0.7, min_votes=None)` ->
  `score(mkt, macro)` (weighted average of percentiles) and
  `state(mkt, macro)` (bool "risk-on" per day, with hysteresis: enter at
  `threshold`, exit at `threshold − 0.1`).
- Evaluation (`evaluate(exit_screen, mkt, target_curve, horizons=(1,3,12))`):
  conditional forward returns of `target_curve` (a screen portfolio or the
  index) over each horizon in flagged vs unflagged states: mean, median,
  hit rate, worst; the fraction of time flagged; the lead time before the
  largest drawdowns; and an overlay backtest: `overlay(curve, state,
  exposure_when_flagged=0.0, cash_rate)` -> the derisked curve, compared to
  always-in on CAGR, MaxDD, Sharpe, and turnover of the switch.
- Signals must only use information available on the day (rolling, no
  centered windows, no full-sample normalization).
Tests: hand-built index with a crash — TrendBreak flags before the bottom;
overlay with exposure 0 during the flagged window has a smaller drawdown;
evaluate() returns the right shapes; every signal is strictly causal (score
on day t unchanged when later prices are perturbed).
