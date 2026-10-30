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
  the credit. Withholding = `TaxConfig.withholding` rate of the country x foreign income (taxable and TFSA); RRSP: exempt for US, see Foreign holdings.
* **Accounts:** taxable taxes everything; TFSA no tax, foreign withholding lost; RRSP no tax while invested, withholding
  exempt, and the balance taxed at withdrawal (`rrsp_withdrawal_rate` / `_income`, below).
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
| TFSA / RRSP | no tax on Canadian dividends, interest or gains (losses are not carried); only the foreign-dividend call is made. The RRSP is **deferred**: its balance is taxed at withdrawal (see below) |
| Interest | ordinary income in a taxable account (below) |
| Payment | at the year's last bar, from the portfolio (`pay_from_portfolio=True`: the account shrinks, later dollar amounts are smaller); the last bar closes the final (part) year |
| Passive index | taxed the same way on the same account: buy and hold, dividends taxed as received, no sales |

Report columns (only when a tax is set): `AfterTaxCAGR`, `TaxDrag/yr` (pre-tax CAGR minus after-tax CAGR),
`AfterTaxExcess/yr` (vs the equally taxed index). The pre-tax columns do not move. `Result.tax` is the per-year
table, `Result.after_tax_curve` the after-tax value (1.0 at the start); starting wealth in dollars is
`TaxConfig.capital` (default `Config.capital`, $100,000), because marginal rates act on dollars.

## RRSP is tax-deferred (P1)
An `account="rrsp"` run no longer pretends the RRSP is tax-free: money in it grows untaxed but **is taxed as ordinary
income when it comes out**. Give the rate the withdrawal is taxed at, one of:

```python
TaxConfig(account="rrsp", model=CanadianTax("AB"), rrsp_withdrawal_rate=0.30)           # a marginal rate you assume
TaxConfig(account="rrsp", model=CanadianTax("AB"), rrsp_withdrawal_income=60_000)       # rate = tax_ca marginal ordinary rate
                                                                                        # at that retirement income, in the
                                                                                        # model's province and year
```
* At the **end of the simulation** (last bar; independent of `liquidate_at_end`, since gains inside an RRSP are sheltered
  and the whole balance comes out) the balance left after that year's ordinary tax is taxed at the rate:
  `after_tax_curve` drops on the last bar by that share, `Result.tax` has the amount in `tax_rrsp_withdrawal` (also in
  `tax_paid` and `cum_tax_paid`), and `AfterTaxCAGR` / `TaxDrag/yr` reflect it. With `pay_from_portfolio=False` the
  withdrawal tax is `rate x pre-tax balance`, deducted from the curve.
* The passive index row is taxed identically (same account, same rate).
* The report (`str(rep)`) prints the convention: `RRSP: deferred, taxed at withdrawal at 30.0% (assumed)`; also in
  `Result.tax.attrs["convention"]`.
* **No rate given:** today's behaviour (the RRSP is never taxed) is kept, with the line
  `RRSP withdrawals untaxed in this run` in the report and in `Result.tax.attrs["warnings"]`. Treat such an after-tax
  number as pre-tax.
* The rate is a flat marginal rate on the whole balance, an assumption: a large withdrawal in one year would in fact be
  taxed progressively; use `rrsp_withdrawal_rate` with an average rate if that matters. Contribution deductions
  (the refund on the way in) are not modelled. TFSA is unchanged (tax-free). `rrsp_withdrawal_*` is ignored for other accounts.

## Interest is ordinary income (P1)
Cash interest was untaxed; in a **taxable** account it now is, as ordinary income at the model's rate (TFSA / RRSP:
recorded in `interest_income`, not taxed). Sources: the sleeve's unallocated cash (weights summing below 1, the Accrual's
cash return), a segment with nothing held (all cash), and the exit policy's **parked cash** (`park="cash"` or a numeric
annual rate; after `park_mer`). Per bar, income = value at the start of the bar x that bar's return; it is reinvested (it
adds to the parked cost basis, so the later park sale does not tax it a second time as a gain).
* `CanadianTax` stacks interest on the salary *before* dividends: tax on interest = tax(salary + interest) - tax(salary).
  `assess_year(..., interest_income=)` takes it; a custom model without that keyword is asked `tax_on_interest(amount, year)`,
  and without that method interest is untaxed with a warning in `attrs["warnings"]`.
* `park_income_kind` (on `TaxConfig` and `ExitPolicy`, default `"interest"`): `"dividend_eligible"` or
  `"dividend_foreign"` tax parked cash income as that kind of dividend instead (a money-market / dividend-fund park). An
  ExitPolicy's non-default value wins over the TaxConfig's. A parked **index or ticker** is a price series: its return stays
  a capital gain taxed on the switch, and `park_income_kind` is ignored for it.
* Columns: `interest_income`, `tax_interest`.

## Trading costs and the adjusted cost base (P1)
Following the CRA, **buy-side costs (spread / commission) are added to the name's adjusted cost base and sell-side costs
are deducted from the proceeds** of each rebalance, name by name (each name's own cost from the cost model). The realized
gain shrinks by buy cost plus sell cost relative to the free run; a taxable account pays less capital gains tax.
Not applied: the exit policy's switch / park costs (they are inside the exposure path) and the sale at `liquidate_at_end`.

## Return of capital (P1)
A per-security snapshot column `roc` in the fundamentals (fraction of the distribution that is return of capital, e.g. 0.4
for an ETF whose distributions are 40% ROC) turns that share of the per-bar distribution from income into an **ACB
reduction**: it is neither income (`dividends_*`) nor tax now. The whole distribution is reinvested (the prices are total
return), so the ACB moves by `distribution x (1 - roc)` per bar and the later sale shows a larger gain. Any ROC that would
take the ACB below zero is a capital gain of that bar and the ACB is floored at 0 (`TaxStage.roc_step`). Because the
distribution is reinvested the ACB cannot actually fall below zero in a backtest; the floor is a guard. Without a `roc`
column nothing changes. Foreign withholding applies to the income part only (approximation).

## Foreign holdings (P3)
Per-security metadata, withholding by country and wrapper, and gains on a CAD basis.

**Security metadata.** `Dataset.security_meta` (optional) is a table indexed by ticker: `domicile` (ISO country of the
issuer), `listing` (country / exchange), `currency`, `wrapper` in `stock` / `etf_ca` / `etf_us`, `underlying_domicile`
(for an ETF: where the stocks it holds are domiciled), `eligible_dividend` (Canadian corporations). Load it from an optional
`securities.csv` with `CSVBuilder(..., securities="securities.csv")` (columns `ticker` plus any of the above; blank cells
and unlisted tickers get the defaults: domicile CA, currency from the domicile, wrapper `stock`, eligible). Built-in
defaults: bundled S&P 500 names are US / USD / stock / not eligible; the synthetic market is CA / CAD / stock / eligible.
`Market.view` joins the columns onto the snapshot (a fundamentals column of the same name wins), so `foreign_col="domicile"`
and screens can use them. The data card gains `foreign: X% of names (US 100%), currencies USD; withholding: ...`
when some name is foreign. Without `security_meta` nothing changes: a name is foreign only if it is in
`TaxConfig.foreign` or `foreign_col` names another country (and then counts as US unless `foreign_col` says otherwise).

**Withholding.** `TaxConfig.withholding` is a dict of treaty rates by the country the dividend is paid from
(`{"US": 0.15, "default": 0.15}`, a dict you give is merged over it; `default` covers every other country). The old
`us_withholding=` still works as a deprecated alias (it warns, fills `withholding["US"]` and reads back as `None`). What a
rate costs depends on the account and the wrapper:

```
                         taxable                  TFSA                RRSP
US stock / US ETF        withheld at source,      withheld, LOST      EXEMPT (treaty; US only)
(direct, US-listed)      creditable (FTC)
other country, direct    withheld, creditable     withheld, LOST      withheld, LOST (exemption is US only)
etf_ca on US stocks      withheld INSIDE the      withheld inside,    withheld inside, LOST
(underlying_domicile     fund, LOST (no FTC);     LOST                (not recoverable even
 "US")                   the net distribution                         in an RRSP)
                         is foreign income
```
Taxable: the model's foreign tax credit applies to direct withholding (`CanadianTax`: min(withholding, Canadian tax on
the foreign income); the placeholder: max(rate, marginal)). A Canadian-listed ETF's inside withholding is a plain cost in
every account; the net distribution is taxed as foreign income with no credit. Report: `tax_foreign_dividends` holds all of
it (withholding plus Canadian tax), `dividends_foreign` the gross direct income plus the funds' gross distributions;
`TaxDrag/yr` follows. **The ETF assumption:** the price series of an `etf_ca` is taken as gross of its internal
withholding, which the tax stage then charges; if your series is the fund's own NAV total return it is already net,
and the charge double counts it. `eligible_dividend=False` on a Canadian name makes its dividends non-eligible; the others
follow the model's `eligible_share`. A US-listed ETF on non-US stocks is treated as a US direct holding (its inside
withholding is ignored).

**Gains on a CAD basis.** Give `TaxConfig(macro=MacroSeries)` whose `fx` is CAD per unit of `fx_currency` (USD). For a name
whose currency is `fx_currency` (from `security_meta`, else USD for a foreign name), the stage tracks ACB and proceeds
in CAD: bought at the FX of the buy date, sold at the FX of the sell date, dividends at the payment-date FX. A USD gain can
be a CAD loss: +10% while USDCAD falls 15% realizes 1.10 x 0.85 - 1 = -6.5% of capital as a loss. Wealth units are CAD at the
first trade date (`capital` is CAD); the pre-tax curve and the parked asset stay in the portfolio currency, only the tax
ledger is converted. Without an FX series the gain is taxed in the portfolio currency and `Result.tax.attrs["warnings"]`
carries "foreign gains taxed in portfolio currency; load an FX series for CAD basis". A currency other than `fx_currency`
and the home currency has no series and warns the same way. Monte Carlo is still not taxed.

## Approximations (read before trusting a number)
* **Dividend income.** Dividends sit inside the total-return prices, and the data has no dividend dates or amounts. Per
  bar, income = value at the start of the bar x `dividend / price` of the snapshot at the last rebalance x the bar's
  length in years: the yield is held to the next rebalance and paid pro rata. The income is treated as reinvested: it is
  added to the name's cost basis, so the later price gain excludes it. Yields that change between rebalances, special
  dividends and payment timing are lost.
* **Cost basis** is one pooled average-cost lot per name; rebalance trading costs are in the basis / out of the proceeds
  (above). Tax payments are taken pro rata from all holdings without realizing the gain on the part sold.
* **Exit policy.** A switch sells the matching share of the equity sleeve (gain realized) into the parked asset, or sells
  the parked asset to buy equity. The parked asset's gain is taxed as a capital gain; interest on parked cash is ordinary
  income (above). Between switches the equity / park mix is treated as rebalanced for free.
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

## Loss harvesting
`Config.tax.harvest = TaxLossHarvest(threshold_pct=0.10, min_loss=0.0, when="year_end", superficial_days=30, replacement="cash")`
(`screenlab/harvesting.py`) adds a stage to `simulate` after Weighting. Default `None`: nothing changes (run keys, golden
tests, the tax table's old columns).
* **What it does.** At the last bar of each calendar year (`when="year_end"`), at each rebalance (`"rebalance"`) or both, every
  held name whose unrealized loss is more than `threshold_pct` of its cost (and at least `min_loss` dollars, pre-tax wealth x
  `TaxConfig.capital`) is sold; the loss is realized in the ledger. Harvesting overrides a hold-rule `keep` for that name (the
  screen's own pick takes the slot). A year-end harvest is an extra step in the run: only the harvested names trade, the rest
  drift untouched, the proceeds wait in cash (cash rate and trading cost as usual).
* **Superficial loss, as implemented.** For EVERY loss sale of the run (harvested or sold by the screen), a purchase of the same
  name within `superficial_days` calendar days after the sale (days 1..30) denies the loss in proportion to the shares bought
  back (shares = value / total-return price; at most the shares sold): denied = loss x min(1, shares bought / shares sold).
  A partial sale of a name bought within the 30 days before it (and still held afterwards) is denied the same way. The denied
  amount is taken out of the year the loss was realized and **added to the ACB of the shares bought**, exactly as CRA does.
  A name harvested at `when="rebalance"` that the screen picks again the same day is a wash: nothing trades, the whole loss is
  recorded as denied.
* **`replacement`** decides the money and the name for the window: `"cash"` proceeds wait in cash and the screen may buy the
  name back (inside the window the loss is then denied); `"stay_out"` the name is dropped from the picks until the window has
  passed; `"similar"` the screen's next-ranked name not held (`Screen.ranked`) is held instead and swapped back for the original
  name on the first bar after the window (day 31 or the next trading day), unless a rebalance has superseded it.
* **Records.** `Result.harvest`: one row per loss sale (`source` "harvest" = chosen by the stage, "screen" = sold by the screen),
  columns `date, name, source, unrealized_pct, loss, denied, net_loss, acb_added, days_out, replacement, rebought,
  window_end`, in pre-tax wealth units (start = 1.0). `days_out` = days until the name is held again (to the last date if
  never). Report columns (`Lab.backtest`, taxed runs with `harvest` set; the passive index row has none): `Harvested/yr` =
  net losses the stage realized, each as a share of the wealth on the day, per year; `TaxDeferred/yr` = (tax paid without the
  stage - tax paid with it) / starting capital / years, from one extra run of the same portfolio without harvesting
  (`report_deferral=False` skips it and the column).
* **Loss carry-back (`taxes.assess`, always on in a taxable account).** A year that nets to a loss (gains + losses of the
  year, before carry-forward) is first carried back to the previous 3 simulation years, most recent first, against each
  year's taxable gain that is left; the refund is a credit at that year end (`tax_paid` can be negative; with
  `pay_from_portfolio` it is paid into the portfolio). What is not used goes forward without expiry as before. Table columns
  `carried_back` ($ of loss) and `refund` ($); `table.attrs["year_tax"][year]` is a `YearTax` whose `breakdown` has
  `carried_back`, `refund`, `refund_by_year`. A carried-forward balance is never carried back. The refund of a year is its
  remaining capital gains tax x the share of its remaining gain the loss takes: exact for a flat rate (placeholder), an
  underestimate for a progressive stacked model (`CanadianTax`).
* **What it ignores.** Spouse / affiliated persons / RRSP and TFSA purchases of the same name (CRA looks at them); identical
  property across ETFs (a different ETF on the same index counts as different; the stage only knows tickers); settlement dates
  (trade date = settlement); "still held at the end of the 30 days" (a rebuy that is sold again inside the window still
  denies); the ledger's per-name basis, so a name bought again before the sale at another price is one pooled lot; the carry-back
  on the passive index (it never sells); an exit policy (`NotImplementedError`: the parked sleeve and the harvest steps are not
  combined); Monte Carlo (as for any tax config). Year-end steps use the snapshot of that day for dividend yields.

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
