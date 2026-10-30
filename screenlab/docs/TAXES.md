# Taxes (`CanadianTax` from `tax_ca`; the placeholder is only for tests)

`Config.tax` turns the backtest into an after-tax one. The default is `None`: no tax, every number exactly as before.

```python
from screenlab import Lab
from screenlab.taxes import TaxConfig
lab = Lab.synthetic()
rep = lab.backtest(["dogs"], tax=TaxConfig(account="taxable"))     # or "tfsa", "rrsp"
rep.table[["CAGR", "AfterTaxCAGR", "TaxDrag/yr", "AfterTaxExcess/yr"]]
rep.tax["dogs"]            # per calendar year: dividends, gains, losses, loss carry, tax by kind, tax paid, drag
rep.results["dogs"].after_tax_curve
```

## CanadianTax (the real model)
```python
from screenlab.taxes import CanadianTax, TaxConfig, TAX_MODELS     # TAX_MODELS: "none" | "placeholder" | "canadian"
tax = TaxConfig(model=CanadianTax(province="AB", year=2025, employment_income=90_000, deductions=0.0,
                                  eligible_share=1.0), account="taxable", capital=250_000)
```
* **Provinces:** `"AB"`, `"BC"` (federal + provincial tables in `screenlab/tax_ca`, built on the owner's
  `owner_ab_2025.py`; AB reproduces it exactly). Table years: 2025 only. A simulated calendar year with no table uses the
  nearest table year and records a warning in `Result.tax.attrs["warnings"]` (the 2020 backtests of the synthetic data
  all warn); do not rely on those years' numbers.
* **Joint yearly assessment.** `CanadianTax.assess_year` taxes a year at once. Stacking order: salary
  (`employment_income - deductions`) -> Canadian dividends (grossed up; `eligible_share` eligible, the rest non-eligible)
  -> foreign income -> net capital gains (after the inclusion rate). Each layer's tax is the increase in the total, so
  the tax charged to the portfolio is total(with investments) - total(salary only); the salary tax itself is never paid
  by the account. A negative layer (dividend credit above the bracket tax) is passed through inside the year; the
  year's total is floored at 0 (no refund is modelled).
* **Foreign tax credit (approximation):** credit = min(withholding, Canadian tax on the foreign income stacked after
  salary and dividends), split federal / provincial pro rata. Reported foreign tax = withholding + Canadian tax net of
  the credit. Withholding = `us_withholding` x foreign income (taxable and TFSA); RRSP: exempt (treaty, US-listed).
* **Accounts:** taxable taxes everything; TFSA no tax, foreign withholding lost; RRSP no tax, withholding exempt.
* **Loss carry-forward** is the stage's: gains net against losses of the year and carried losses, and only the net
  taxable gain reaches `assess_year`.
* **Protocol:** `assess` uses a model's optional `assess_year(year, *, eligible_dividends, non_eligible_dividends,
  foreign_income, foreign_withholding, capital_gains_net, account) -> YearTax(total, breakdown)` when it has one, else
  the per-item calls below. The per-item methods of `CanadianTax` are the joint call with one layer.
* The AB rates are the owner's; BC and the federal tables should be checked against CRA / BC sources for the year. Not tax advice.

## PlaceholderCanadianTax: only for tests
`PlaceholderCanadianTax` has constant, illustrative rates (Ontario defaults from memory, unchecked against CRA tables)
and a constant marginal rate. It exists so the stage and its exact-arithmetic tests run without tables. **Do not use it
for numbers you rely on.**

## What is modelled
| Event | How it is taxed |
|---|---|
| Canadian dividends | cash dividend, grossed up and credited by the model, at the year's marginal rate (`tax_on_dividends`) |
| Foreign dividends (`TaxConfig.foreign` tickers or `foreign_col`) | `tax_on_foreign_dividends`: taxable pays the larger of withholding and the Canadian rate; TFSA pays the withholding (15% US, not recoverable); RRSP none (treaty, assumed US-listed) |
| Capital gains | realized on every rebalance sale and on exit-policy switches; average cost per name; inclusion rate x marginal rate (`tax_on_capital_gain`) |
| Capital losses | netted within the year, then carried forward (nominal, no expiry) against later gains. Stage logic, not the model's |
| TFSA / RRSP | no tax on Canadian dividends or gains (losses are not carried); only the foreign-dividend call is made |
| Payment | at the year's last bar, from the portfolio (`pay_from_portfolio=True`: the account shrinks, later dollar amounts are smaller); the last bar closes the final (part) year |
| Passive index | taxed the same way on the same account: buy and hold, dividends taxed as received, no sales |

Report columns (only when a tax is set): `AfterTaxCAGR`, `TaxDrag/yr` (pre-tax CAGR minus after-tax CAGR),
`AfterTaxExcess/yr` (vs the equally taxed index). The pre-tax columns do not move. `Result.tax` is the per-year
table, `Result.after_tax_curve` the after-tax value (1.0 at the start); starting wealth in dollars is
`TaxConfig.capital` (default `Config.capital`, $100,000), because marginal rates act on dollars.

## Approximations (read before trusting a number)
* **Dividend income.** Dividends sit inside the total-return prices, and the data has no dividend dates or amounts. Per
  bar, income = value at the start of the bar x `dividend / price` of the snapshot at the last rebalance x the bar's
  length in years: the yield is held to the next rebalance and paid pro rata. The income is treated as reinvested: it is
  added to the name's cost basis, so the later price gain excludes it. Yields that change between rebalances, special
  dividends and payment timing are lost.
* **Cost basis** is one pooled average-cost lot per name. Trading costs are neither added to the basis nor deducted
  from proceeds. Tax payments are taken pro rata from all holdings without realizing the gain on the part sold.
* **Exit policy.** A switch sells the matching share of the equity sleeve (gain realized) into the parked asset, or sells
  the parked asset to buy equity. The parked asset's gain is taxed as a capital gain; interest on cash is **not** taxed
  (real interest is ordinary income). Between switches the equity / park mix is treated as rebalanced for free.
* **Delisting** realizes the loss at the haircut value when the name's segment ends.
* **Unrealized gains at the end are not taxed** unless `liquidate_at_end=True`.
* **Passive index.** A total-return series has no yield of its own. The index is taxed with the benchmark's
  `dividend_yield` if it has one, else `TaxConfig.index_dividend_yield`, else the cap-weighted dividend yield of the
  universe at the start date (an approximation; check it). `index_foreign_share` sets how much of it is foreign (default 0).
  The index never sells, so only its dividends are taxed.
* **Monte Carlo** (`simulate_paths`) raises `NotImplementedError` with a tax config: the per-lot ledger is path by path.
* The simulated income is stacked on the investor's other income year by year (`CanadianTax`), but the investor's salary is
  constant and not inflated; RRSP contribution room, pension income and AMT are not modelled. The placeholder has a constant
  marginal rate and ignores `year`.

## Writing another model
Implement the `TaxModel` protocol (`screenlab/taxes.py`) and register it with a tag so it survives a RunSpec / run key:

```python
from dataclasses import dataclass
from screenlab.taxes import TaxConfig, register_tax_model

@register_tax_model("OwnerCanadianTax")
@dataclass(frozen=True)
class OwnerCanadianTax:
    province: str = "ON"
    salary: float = 120_000.0

    def tax_on_dividends(self, amount, eligible, year):            # cash dividend -> tax, $
        return owner_module.dividend_tax(self.salary, amount, eligible, self.province, year)

    def tax_on_capital_gain(self, gain, year):                     # NET taxable gain of the year, > 0
        return owner_module.capital_gain_tax(self.salary, gain, self.province, year)

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        # ALL tax on foreign dividends: withholding + Canadian tax net of the foreign tax credit
        if account == "rrsp":
            return 0.0
        if account == "tfsa":
            return amount * withholding_rate
        return owner_module.foreign_dividend_tax(self.salary, amount, withholding_rate, self.province, year)

    def describe(self):
        return {"model": "OwnerCanadianTax", "province": self.province, "salary": self.salary}

lab.backtest(["dogs"], tax=TaxConfig(model=OwnerCanadianTax(), account="taxable", capital=250_000))
```
Rules for a model: amounts are dollars and the return is non-negative dollars; `tax_on_capital_gain` is only called with
a net gain above zero (the stage nets and carries losses); in `tfsa` / `rrsp` the stage calls only
`tax_on_foreign_dividends`; the model must be a dataclass (or define `to_dict` / `from_dict`) with the tag registered
in the process that reads a stored run. The owner's numbers are checked in `tests/test_taxes.py` (dividend-only portfolio and one gain, AB salary 90,000, equal to
`owner_ab_2025` to 1e-6); do the same for a new model on the hand-built market.

## Where it lives
`taxes.py` (protocol, models, `TaxConfig`, `assess` yearly engine, index taxation), `pipeline.TaxStage` (the event
ledger inside `backtest.simulate`), `Config.tax`, `Result.tax` / `Result.after_tax_curve`, `BacktestReport.tax`.
