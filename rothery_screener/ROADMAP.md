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

## 1. Real data and correctness (next)
Nothing the tool says about real stocks counts until this phase is done.
- **Data source.** Canadian point-in-time fundamentals that include report
  dates and delisted companies (candidates: Refinitiv/LSEG, S&P Capital IQ,
  EODHD, Morningstar). Write one adapter per source that outputs the existing
  CSV format.
- **Report lag.** Apply each figure on its filing date, not its period-end
  date.
- **Price vs total return.** Load a price series alongside the total-return
  index, so P/E and yield are marked to price only. This removes today's
  small dividend bias.
- **Universe history.** Use historical TSX/S&P 500 membership and listing
  status to avoid survivorship bias. Replace the flat `delist_return` with
  actual delisting proceeds where the data has them.
- **Benchmarks and currency.** Load real index series (S&P/TSX Composite TR,
  TSX 60 TR, S&P 500 TR in CAD). Add ETF presets with their real MERs
  (XIC, XIU, VCN, ZCN, ...). Handle USD/CAD.
- **Reconciliation.** Re-create a few published Globe screen portfolios and
  check the picks and returns against the columns. This is the main test
  that the screen definitions match.

## 2. Research features
- **Weighting and construction.** Add equal, cap and inverse-vol weights,
  sector caps, and position limits.
- **Turnover control.** Add a "keep if still within the top 2N" hold rule
  and rebalance bands.
- **Combined portfolios.** Add screen-of-screens and blended portfolios.
- **Taxes.** Model taxes for taxable, TFSA and RRSP accounts (dividend tax
  credit, capital gains inclusion, US withholding).
- **Statistics.** Add bootstrap confidence intervals on excess return, and a
  t-stat or deflated Sharpe ratio across many screens.
- **Parameter sweeps.** Run sweeps through `--set` grids, with
  walk-forward / out-of-sample splits to guard against overfitting.
- **Faster timing studies.** Parallelize the timing sweep (offsets are
  independent) and cache views on disk.
- **Charts.** Plot equity curves, CAGR against rebalance date, and the
  Graham funnel over time.

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
