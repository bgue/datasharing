# Taxes (placeholder model, owner's module drops in)

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

## THE RATES ARE PLACEHOLDERS
`PlaceholderCanadianTax` exists so the stage runs end to end. **Its rates are illustrative, from memory, and must be
replaced by the owner's Canadian dividend / salary tax module and checked against the CRA (and provincial) tables for
the tax year before any number is relied on.** Only Ontario has defaults; other provinces must be given every rate.
It is not tax advice.

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
* Constant marginal rate: the simulated income does not push the investor into higher brackets. Province and year are
  parameters; the placeholder ignores `year` (a real module may use it).

## Plugging in the owner's module
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
in the process that reads a stored run. To check the owner's numbers on a dividend-only portfolio, run a screen whose
total return is flat price plus dividends (see `tests/test_taxes.py` for the hand-built market) and compare
`rep.tax[...]` with the module's own output year by year.

## Where it lives
`taxes.py` (protocol, models, `TaxConfig`, `assess` yearly engine, index taxation), `pipeline.TaxStage` (the event
ledger inside `backtest.simulate`), `Config.tax`, `Result.tax` / `Result.after_tax_curve`, `BacktestReport.tax`.
