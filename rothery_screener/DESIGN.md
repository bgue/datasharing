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

---------------------------------------------------------------------------
## F. Exit-signal timing and calibration (`exit_signals.py` additions, `calibration.py`)

Two clocks, kept separate:
- The **rebalance clock** belongs to the entry screen: every
  `rebalance_months`, pick names, set weights. Nothing here changes it.
- The **exit clock** belongs to the exit screen: a decision cadence at which
  the flag may change. Default daily (`check="D"`), because the signals are
  price-driven and the data is daily. `check="W-FRI"` or `"M"` are allowed
  for slower regimes; intraday (`"H"`) is reserved for when intraday bars
  exist — signals are already written in calendar windows, so a finer index
  must work without code changes beyond the data layer.

### F1. `ExitScreen` timing fields
- `check: str = "D"` — decision cadence. `state()` is computed on the
  market index, then sampled at the check points (last bar on/before each
  check date) and padded forward, so the flag only changes at check points.
- `lag: int = 1` — execution lag in bars (signal at close t, exposure from
  close t+lag). `overlay()` takes the same `lag`.
- `min_hold_days: int = 0` — once flagged (or unflagged), keep the state at
  least this many calendar days: prevents whipsaw on top of hysteresis.
- Keep every signal strictly causal (test exists). Document in the class
  docstring that `check` and `rebalance_months` are independent.

### F2. Backtest integration — `backtest.Config.exit_overlay`
`ExitOverlay(screen: ExitScreen, exposure_when_flagged=0.0, cost_bps=5.0,
macro=None)` precomputes the exposure series once per Market (cache on
`id(mkt)`), then `simulate()` applies it *inside* each rebalance segment:
daily portfolio return = e_t · r_t + (1 − e_t) · cash_t − cost · |Δe_t|,
with e_t the lagged exposure. Picks and weights are untouched (the entry
screen never sees the flag). The `Result` gains `exposure: pd.Series` and
`switches: int`. Default `exit_overlay=None` must reproduce today's curves
exactly (existing tests pin them).

### F3. `calibration.py`
`calibrate_exit(exit_screen, mkt, target, grid, objective="Calmar",
splits=dict(train_years=.., test_years=..), constraints=None, macro=None)`:
- `grid`: dict over `threshold`, `hysteresis`, `check`, `lag`,
  `min_hold_days`, `weights` (list of weight vectors) and
  `exposure_when_flagged`; dotted keys `signals.<Name>.<param>` (e.g.
  `signals.TrendBreak.window_months: [8, 10, 12]`) rebuild that signal.
- `objective`: "Calmar" (CAGR / |MaxDD| of the overlay curve), "Sharpe",
  "CAGR", "MaxDD", "ForwardGap" (unflagged minus flagged mean 12-month
  forward return), or a callable `(OverlayResult, ExitEvaluation) -> float`.
- `constraints`: `max_switches_per_year`, `max_time_flagged`,
  `min_time_flagged`; points that violate them are kept in the table but
  excluded from selection.
- Walk-forward exactly as in `sweep.walk_forward`: pick the best point per
  train window, report its test-window objective; summary = mean OOS,
  OOS/IS, and **stability** = fraction of windows won by the modal point.
- Returns a `CalibrationResult` with `.table`, `.windows`, `.summary`,
  `.best` (the modal point) and `.screen` (an ExitScreen built from `.best`).
- Reuse `stats.summarize_trials` for the DSR of the winner.
Tests: on a hand-built index with two crashes the calibrated threshold
flags both and the OOS objective beats always-in; constraints exclude
points; stability is 1.0 when one point wins every window; daily and
monthly indexes both run.

---------------------------------------------------------------------------
## G. Service layer (`server/`)

Both servers wrap one `LabRegistry` (`server/registry.py`): named Labs
(`"synthetic"`, `"sp500"`, or `from_csv(...)` paths) created lazily and
cached; every call takes a `lab` name (default `"sp500"` if skfolio is
installed, else `"synthetic"`). Results are plain JSON: tables as
`{"columns": [...], "rows": [...]}` records, curves downsampled to at most
`max_points` (default 500) dated pairs.

### G1. REST — `server/rest.py` (FastAPI)
`GET /labs`, `GET /labs/{lab}/screens`, `POST /labs/{lab}/screens` (a
ScreenSpec JSON), `GET /labs/{lab}/picks/{screen}?asof=`,
`POST /labs/{lab}/backtest` (screens, start, end, config overrides),
`POST /labs/{lab}/timing`, `GET /labs/{lab}/graham?preset=&overrides`,
`POST /labs/{lab}/sweep`, `POST /labs/{lab}/significance`,
`POST /labs/{lab}/exit/evaluate` (signal list + params),
`POST /labs/{lab}/exit/calibrate`, `POST /labs/{lab}/extended`.
`python -m rothery_screener.server.rest` serves on :8000. Tests use
`fastapi.testclient.TestClient` (no network).

### G2. MCP — `server/mcp_server.py` (`mcp` package, FastMCP)
One tool per endpoint above with the same names and JSON shapes, plus
`describe_columns` (the fundamentals column contract) and
`list_signals`. `python -m rothery_screener.server.mcp_server` runs stdio
transport. Document the Claude Code registration command in
`server/README.md` (`claude mcp add rothery -- python -m
rothery_screener.server.mcp_server`). Tests call the tool functions
in-process (the MCP transport itself is not tested).

Dependencies (`fastapi`, `httpx`, `mcp`) are optional: import lazily, and
tests skip when absent. Add `requirements-server.txt`.

---------------------------------------------------------------------------
## H. Exit policy, re-entry and more exit inputs

The exit *screen* says **when** risk is elevated. The exit *policy* says
**what to do**: where assets go, how much, and when and how they come back.
A portfolio for backtesting is `entry screen + optional exit policy`, and
the same screen can be run with and without it side by side.

### H1. `ExitPolicy` (`exit_policy.py`)
```
ExitPolicy(
    screen: ExitScreen,
    exposure_when_flagged: float = 0.0,  # 0 = fully out, 0.5 = half out
    park: str | float = "cash",          # where the exited money sits:
                                         #   "cash" -> cfg.cash_rate
                                         #   a float -> that annual rate
                                         #   an index/ticker name in Market (e.g. a T-bill or bond
                                         #   ETF total-return series) -> hold that series, with
                                         #   park_mer and park_cost_bps applied like a fund
    park_mer: float = 0.0, park_cost_bps: float = 0.0,
    switch_cost_bps: float = 5.0,        # charged on |Δexposure| of the equity sleeve
    reentry: str = "signal",             # "signal": when the flag clears (after hysteresis and
                                         #     min_hold_days)
                                         # "next_rebalance": stay out until the entry screen's next
                                         #     scheduled rebalance, even if the flag clears earlier
                                         # "signal_or_rebalance": whichever comes first
    reentry_delay_days: int = 0,         # confirmation: flag must stay clear this long
    reentry_steps: int = 1,              # scale back in over k check periods (1 = all at once)
    rescreen_on_reentry: bool = False,   # True: run the entry screen fresh on the re-entry day
                                         #   (an unscheduled rebalance, costs charged as usual);
                                         #   False: return to the paper sleeve that kept running
)
```
`policy.exposure(mkt, rebalance_dates, curve=None) -> pd.Series` turns the
screen's state into the equity-sleeve exposure path (values in [0, 1],
lagged by `screen.lag`, stepped by `reentry_steps`, held down by
`reentry_delay_days` / `reentry="next_rebalance"`). `policy.reentry_dates`
is the list of days exposure returns to 1 from below; with
`rescreen_on_reentry` those days become extra rebalance dates.

### H2. Backtest integration (replaces F2's `ExitOverlay` as the public hook)
- `Config.exit: ExitPolicy | None = None`. Keep `exit_overlay` working as a
  thin alias that builds an `ExitPolicy(screen, exposure_when_flagged,
  switch_cost_bps=cost_bps, park="cash")`.
- `run()` becomes two-pass when a policy is set: pass 1 simulates the
  fully-invested sleeve (as today) to obtain the curve some signals need
  (`PortfolioStop`), computes the exposure path from the policy, extends
  the rebalance schedule with any rescreen re-entry dates, then pass 2
  simulates with the exposure applied inside each segment:
  `r_t = e_t·r_equity + (1−e_t)·r_park − switch_cost·|Δe_t| − park_cost·|Δ(1−e_t)|`.
  `Result` gains `exposure`, `switches`, `time_out` (fraction of days with
  e < 1) and `park_curve`.
- `Lab.backtest(screens, ..., exit=None, with_and_without=False)`: `exit`
  is an `ExitPolicy` (or `ExitScreen`, wrapped in a default policy) applied
  to every screen in the call; `with_and_without=True` runs each screen
  twice and labels the rows `name` and `name+exit`. Also accept
  `Portfolio(name, screen, exit=None, **config_overrides)` objects in the
  `screens` list so different portfolios can carry different policies in
  one report. The table gains `TimeOut` and `Switches/yr` columns (blank
  for rows without a policy).
- CLI (`run.py`): `--exit "TrendBreak:index=SYN_CAPWT,Breadth"
  --exit-threshold 0.7 --exit-park cash --exit-reentry signal
  --exit-compare` (runs with and without).
- `server/registry.py`: `backtest` accepts an `exit` object with the policy
  fields; `exit_evaluate` and `exit_calibrate` accept the same `policy`
  fields so calibration can sweep `reentry`, `reentry_delay_days`,
  `reentry_steps`, `park` and `exposure_when_flagged` too (add those keys
  to `calibrate_exit`'s grid handling).

### H3. Additional exit inputs (`exit_inputs.py`, registered into
`exit_signals.BUILTINS` on import; `Signal.compute(mkt, macro=None,
curve=None)` — the `curve` argument is the portfolio's own fully-invested
curve, passed by the policy, `None` when evaluating against an index).
Price-based (universe + index):
- `CorrelationSpike(window_days=21)` — mean pairwise correlation of daily
  returns across the universe (crises are when everything moves together).
- `NewLowsMinusHighs(window_days=252)` — fraction of names at a 52-week low
  minus fraction at a 52-week high.
- `VolTermStructure(short=21, long=126)` — short/long realized vol ratio of
  the index (spike = stress).
- `DeathCross(fast=50, slow=200)` — fast MA below slow MA, scaled by gap.
- `DistanceFromHigh(index, window_days=252)` — % below the 52-week high.
- `DownsideDeviation(window=63)` — semi-deviation percentile of the index.
Portfolio-based (need `curve`):
- `PortfolioStop(drawdown=0.15)` — the portfolio's own drawdown from its
  running peak (a trailing stop); score 1 when past `drawdown`.
- `PortfolioVsIndex(months=3)` — the portfolio's relative drawdown vs the
  index.
Fundamentals-based (monthly sampled like `ValuationStretch`, NaN when the
columns are absent):
- `EarningsBreadth()` — fraction of names with `eps < eps_prev`.
- `YieldSpreadCompression()` — universe median earnings yield minus `rf`
  (Fed-model style); lower = riskier.
Macro-based (NaN without the series):
- `YieldCurve(short="rf", long="long_rate")` — long minus short; inverted =
  risky.
- `CreditSpread(series="credit_spread", months=3)` — level and 3-month
  change of a credit-spread series.
Each ships with the same causality test as the existing signals; a
`describe()` classmethod returns its parameters for `list_signals`.
