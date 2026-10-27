# Roadmap

## Done (sketch)
- Eight built-in screens: Dogs, Graham defensive and enterprising, net-nets,
  Piotroski, Magic Formula, value + momentum, low-vol dividend.
- Screen definition through a builder or JSON/dict specs: universe, derived
  columns, filters, Graham tests, composite rank.
- Equal-weight periodic-rebalance backtester that runs on daily or monthly
  data.
- Per-security trading cost (flat, cap-tiered or per ticker), flat
  commissions, per-security MER, and a delisting haircut.
- Buy-and-hold index benchmark with its own MER and trading cost (default 0),
  or a screen as the benchmark.
- Daily-start timing study (phase and start modes) with a tranche blend.
- `Lab` SDK, CLIs, and a pytest suite that runs through the SDK.

- Real data: bundled S&P 500 prices + index and US CPI/T-bill; Bank of
  Canada and SEC FSDS adapters tested on fixtures (`realdata/ACQUIRE.md`).
- Construction: cap / inverse-vol weights, position and sector caps, hold
  rules and bands, union and blended portfolios.
- Statistics: block-bootstrap CI, Newey–West t-stat, deflated Sharpe; grid /
  random / optional Bayesian sweeps with walk-forward scoring.
- Extended metrics: real, home-currency, net-of-financing (prime + spread,
  leverage), excess over time-varying rf, geometric decomposition.
- Exit-signal screen with causal signals, hysteresis, conditional forward
  returns and a derisking overlay.

## 1. Real data and correctness (next)
Nothing the tool says about real stocks counts until this phase is done.
- **Open the network** for www.sec.gov, www.bankofcanada.ca, stooq.com (or
  eodhd.com), fred.stlouisfed.org and run the adapters for real; the
  bundled 20-name S&P 500 set is survivor-biased and only shows the
  pipeline works.
- **Fundamentals.** SEC FSDS for US names today (adapter ready); Canadian
  point-in-time fundamentals need a paid source (EODHD cheapest, TMX /
  Capital IQ / Refinitiv proper).
- **Price vs total return.** Load a price series alongside the total-return
  index so P/E and yield are marked to price only.
- **Universe history.** Historical TSX / S&P 500 membership and delisting
  proceeds instead of the flat `delist_return`.
- **Benchmarks and currency.** Real index TR series, ETF presets with real
  MERs, USD/CAD from the Bank of Canada.
- **Reconciliation.** Re-create published Globe screen portfolios and check
  picks and returns against the columns.

## 2. Research features (remaining)
- **Taxes.** Taxable / TFSA / RRSP accounts (dividend tax credit, capital
  gains inclusion, US withholding).
- **Exit signals on real data.** Calibrate thresholds and hysteresis
  walk-forward on the S&P 500 set; add credit-spread and yield-curve
  signals once FRED is reachable; combine with entry screens only at the
  evaluation stage (overlay), never in the screen definition.
- **Faster timing studies.** Parallelize the offset sweep and cache views.
- **Charts.** Equity curves, CAGR by rebalance date, Graham funnel over
  time, exit-signal score over the index.

## 3. Service and reports
- **REST API.** Expose `define`, `picks`, `backtest`, `timing` and `graham`
  over HTTP (FastAPI). The JSON screen specs are already the wire format.
- **Saved screens.** Version screen specs and keep a results history keyed
  by spec, data snapshot and config.
- **HTML report.** Build a shareable report page per run.

## 4. Live tracking
- **Scheduled picks.** Refresh "current picks" on a schedule and flag adds
  and drops.
- **Paper portfolios.** Track paper portfolios started on chosen dates,
  reported against the index benchmark.
