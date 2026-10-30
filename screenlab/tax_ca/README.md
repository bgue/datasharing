# tax_ca: Canadian personal income tax by jurisdiction

Canada only, not tax advice. Entry points: `api.compute(province, year, ...)` -> `TaxResult`, `api.marginal_rates(...)`.

## Files
- `owner_ab_2025.py` reference module (federal + Alberta, 2025); untouched; tests reproduce it to 1e-6.
- `core.py` bracket math, `Brackets`, `BPA`, `DividendRates`, `LowIncomeReduction`, `HealthPremium`, `Jurisdiction`.
- `tables.py` `FEDERAL`, `ALBERTA`, `BRITISH_COLUMBIA` (each `{year: Jurisdiction}`), `JURISDICTIONS`, `TAX_YEARS`.
- `api.py` the contract.

## Tables (2025)
Federal and Alberta: copied exactly from the owner's file (sources are the owner's, see its docstring).
British Columbia (sources in comments in `tables.py`):
- Brackets 5.06% to 49,279; 7.70% to 98,560; 10.50% to 113,158; 12.29% to 137,407; 14.70% to 186,306; 16.80% to 259,829; 20.50% above. wealthsimple.com/en-ca/learn/bc-tax-brackets, taxtips.ca/taxrates/bc.htm (agreeing search results).
- BPA 12,932 (flat, credit at 5.06%): www2.gov.bc.ca/gov/content/taxes/income-taxes/personal/credits/basic.
- DTC (of grossed-up amount) eligible 12%, non-eligible 1.96%: same BC government page.
- Tax reduction credit: base 562, threshold 25,020, 3.56% reduction, zero at 40,807 (BC government page, taxtips.ca/bctax/personal-tax-credits.htm). 2026 differs (690 / 25,570).

## Stacking and totals
salary (less deductions, floored at 0) -> dividends (grossed up, DTC) -> foreign income (ordinary) -> net capital gains (50% inclusion). Each breakdown marginal = total with the layer minus total without; may be negative. Like the owner's module: with no dividends each jurisdiction is floored at 0 separately; with dividends only the combined total is floored. When the total is floored to 0, `federal`/`provincial` are reported as 0. The BC reduction credit is tested against taxable income (grossed-up dividends and taxable gains included), an approximation of net income.

## Foreign tax credit (approximation)
FTC = min(foreign tax paid, Canadian tax on the foreign income stacked after salary and dividends), split federal/provincial pro rata. The CRA computes it per-country, per-source with a net-income ratio limit and separate federal/provincial credits; not modelled.

## Unverified
- BC non-eligible DTC 1.96% vs a possible 1.5% (EY, taxnews.ey.com/news/2025-0615-canada-british-columbia-budget-2025-26): not resolved which year.
- BC has no surtax and no health premium (MSP ended 2020): stated from knowledge, not confirmed by search.
- BC pages were only reached through search snippets; the gov.bc.ca and taxtips.ca hosts were not fetchable from the session.

## Adding a year or province
Add `{year: Jurisdiction(...)}` entries in `tables.py` (federal and each province), the year to `TAX_YEARS` and `CAPITAL_GAINS_INCLUSION`, a province code to `JURISDICTIONS`. Add a test with a hand-worked value.
