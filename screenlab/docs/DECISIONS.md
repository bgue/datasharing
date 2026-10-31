# Decision log

Short entries, newest first. Each records a choice that shapes results, why it was made, and what would change it.

## 2026-10-31 Plan tax basis: the tax is paid from the portfolio (outside payment is an option)

`run_plan` used to force `TaxConfig(pay_from_portfolio=False)`: the tax was paid from money outside the account and the after-tax value was the pre-tax value less the tax paid so far, never credited with the growth the tax would have earned. Every other backtest in the library pays the tax from the portfolio, and so does a real investor unless outside income is earmarked for it. The Monte Carlo already took the tax out of the portfolio, so the notebook's TFSA replay (2,879,690) sat about 20% under the straight run (3,610,514) for no reason but the basis. The default is now the portfolio: at each calendar year's last bar the year's tax (assessed on the events so far) is sold pro rata out of the holdings, the cost bases shrink with it (no gain is realized on the part sold), and later flows, dividends and gains sit on the smaller portfolio; the last year's tax, the tax on the gains realized at the end and an RRSP's balance tax come out on the last bar. The units ledger stays exact: flows are priced on the after-tax NAV (value = sum of units x NAV), the pre-tax NAV is the same path with the payments treated as outflows. Consequences: the after-tax series can no longer go negative, the Monte Carlo and the straight run share one basis (the history replayed through `plan_montecarlo(generator="history")` equals `FinalAfterTax` to rounding, tested for TFSA, RRSP and taxable), and after-tax figures are lower than before wherever tax is paid before the end (TFSA with lost withholding, taxable). Approximation: the scaling is by the aggregate portfolio, not cohort by cohort, so the events of later years are exact for a single lump sum (agrees with a per-$1 library backtest to 0.6% on the 33-year dogs) and approximate where money of different ages and gains sits in the same portfolio.

`PlanSpec.tax_from_outside=True` restores the old basis (same numbers as before); there the after-tax value is floored at zero and the dates on which the tax paid exceeded the portfolio are listed on the card. The Monte Carlo of an outside-paid plan still takes the tax from the portfolio (the card says the two bases differ). What would change it: a user who really pays the tax from other money should set the option; a cohort-level ledger would remove the aggregate approximation.

## 2026-10-31 Risk-free rate for Sharpe in the plan module: US 10-year yield, as an expediency

The plan module (`plan.py`) computes Sharpe on monthly time-weighted returns. The risk-free series it uses by default is the US 10-year Treasury yield mirrored in `data/mirrors/us_10y_monthly.csv`, because the Bank of Canada and FRED hosts are not reachable from the build environment and the US series is. This is an expediency, not a view that the US rate is the right one for a Canadian investor. `PlanSpec.risk_free` accepts `"us10y"` (default), `"ca10y"` (the Government of Canada 10-year benchmark, used automatically once the Bank of Canada Valet series V39055 is present under `data/boc/`, fetched by `make data-boc`), or a constant. The report states which series was used. Revisit when the Canadian series is bundled.

## 2026-10-31 Inflation deflator: US CPI by default, Canadian CPI when loaded

Same reason. `data/mirrors/us_cpi_monthly.csv` is used with a warning line; the StatCan all-items CPI (`make data-statcan`, table 18-10-0004-01) replaces it automatically when present. Returns labelled "after inflation" in a report always name the series used.

## 2026-10-31 Contributions: invest on arrival by default, with a hold-until-rebalance switch

`PlanSpec.cash_timing="invest"` buys into the current target weights on the next trading day and pays the per-security trading cost; `"hold"` keeps contributions in cash at the cash rate until the next scheduled rebalance. The default is `invest` because that is what a monthly investor does; the switch exists to show the cost of waiting.

## 2026-10-31 Headline return for dated cash flows: money-weighted (XIRR)

Time-weighted returns are kept for alpha, beta, Sharpe, volatility and drawdown against the index. The headline figure for a plan is the money-weighted return, because the plan's question is what the investor's own dollars earned.
