# Rothery-style multi-portfolio screener & backtester (sketch)

This sketch runs several classic screens side by side, each as its own
equal-weight portfolio rebalanced on a fixed schedule. It works the way Norman
Rothery tracks his screen portfolios in The Globe and Mail: pick the stocks,
hold them for a year, compare against the index, then repeat.

```
python -m rothery_screener.run                      # synthetic market, all screens
python -m rothery_screener.run --only dogs,piotroski --rebalance 6 --cost-bps 30
python -m rothery_screener.run --csv fundamentals.csv prices.csv --start 2005-01-31
```

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
- `fundamentals.csv` has columns `date,ticker,...` (see the `data.py` docstring).
  Each row must hold only what was **known** on that date, so shift each value
  by its reporting lag before you load the file.
- `prices.csv` has columns `date,ticker,tri`: a monthly total-return index.
- Delisted names have to stay in both files. The backtester charges
  `delist_return` when a holding disappears.

## Backtest mechanics
- On each rebalance date the backtester takes a snapshot, runs the screen and
  equal-weights the picks. If fewer than `min_holdings` stocks pass, the
  portfolio sits in cash.
- Costs are charged on one-way turnover (`cost_bps`). Weights drift between
  rebalances.
- The benchmark is an equal-weight portfolio of the 60 largest names, standing
  in for the TSX 60. Swap in a real index series if you have one.
- Reported stats: CAGR, volatility, Sharpe, max drawdown, worst 12 months, the
  share of years that beat the benchmark, excess CAGR, average holdings and
  turnover.

## Next steps
- Use real data. A TMX/Refinitiv/EODHD export works, or a Globe screener
  export for the current picks only.
- Add a TSX/S&P 500 universe switch and country benchmarks.
- Run staggered rebalance months to average out timing luck. Rothery often
  notes how much the start date matters.
- Add a combo or "screen of screens" portfolio, plus bootstrap confidence
  intervals on excess returns.
- Write an HTML report with wealth curves and yearly return tables.
