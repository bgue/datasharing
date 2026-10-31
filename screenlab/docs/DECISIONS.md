# Decision log

Short entries, newest first. Each records a choice that shapes results, why it was made, and what would change it.

## 2026-10-31 Risk-free rate for Sharpe in the plan module: US 10-year yield, as an expediency

The plan module (`plan.py`) computes Sharpe on monthly time-weighted returns. The risk-free series it uses by default is the US 10-year Treasury yield mirrored in `data/mirrors/us_10y_monthly.csv`, because the Bank of Canada and FRED hosts are not reachable from the build environment and the US series is. This is an expediency, not a view that the US rate is the right one for a Canadian investor. `PlanSpec.risk_free` accepts `"us10y"` (default), `"ca10y"` (the Government of Canada 10-year benchmark, used automatically once the Bank of Canada Valet series V39055 is present under `data/boc/`, fetched by `make data-boc`), or a constant. The report states which series was used. Revisit when the Canadian series is bundled.

## 2026-10-31 Inflation deflator: US CPI by default, Canadian CPI when loaded

Same reason. `data/mirrors/us_cpi_monthly.csv` is used with a warning line; the StatCan all-items CPI (`make data-statcan`, table 18-10-0004-01) replaces it automatically when present. Returns labelled "after inflation" in a report always name the series used.

## 2026-10-31 Contributions: invest on arrival by default, with a hold-until-rebalance switch

`PlanSpec.cash_timing="invest"` buys into the current target weights on the next trading day and pays the per-security trading cost; `"hold"` keeps contributions in cash at the cash rate until the next scheduled rebalance. The default is `invest` because that is what a monthly investor does; the switch exists to show the cost of waiting.

## 2026-10-31 Headline return for dated cash flows: money-weighted (XIRR)

Time-weighted returns are kept for alpha, beta, Sharpe, volatility and drawdown against the index. The headline figure for a plan is the money-weighted return, because the plan's question is what the investor's own dollars earned.
