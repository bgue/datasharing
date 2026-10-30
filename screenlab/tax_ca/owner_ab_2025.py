"""Canadian personal income tax (federal + Alberta), 2025 tax year.

Pure functions, no I/O. All rates/thresholds are constants below so the module
can be updated for a new tax year in one place.

Sources (verify before relying on this for real financial decisions):
  - Federal brackets & the mid-year lowest-rate cut (15% -> 14%, effective
    July 1, 2025, giving a blended ~14.5% for the 2025 tax year as a whole).
  - Federal basic personal amount (BPA) 2025: max $16,129, phasing down to
    $14,538 for taxable income between the third and fourth bracket
    thresholds (the phase-out is applied here against taxable income as an
    approximation of net income).
  - Alberta 2025 brackets, including the new 8% bracket introduced on the
    first $60,000 of taxable income.
  - Alberta basic personal amount 2025: $22,323 (Alberta has no provincial
    surtax and no health premium).
  - Eligible / non-eligible dividend gross-up and dividend tax credit (DTC)
    rates, federal and Alberta, 2025.
  - Capital gains inclusion rate: 50% flat for 2025 (the proposed increase
    to 2/3 above $250k was cancelled/deferred; not implemented here).

TAX_YEAR = 2025 -- update constants below for any other year.
"""

from __future__ import annotations

TAX_YEAR = 2025

# ---------------------------------------------------------------------------
# Federal brackets (2025) -- taxable income bands: (lower, upper, rate)
# ---------------------------------------------------------------------------
FEDERAL_LOWEST_RATE = 0.145  # blended 2025 rate (15% Jan-Jun, 14% Jul-Dec)

FEDERAL_BRACKETS_2025: list[tuple[float, float, float]] = [
    (0, 57_375, 0.145),
    (57_375, 114_750, 0.205),
    (114_750, 177_882, 0.26),
    (177_882, 253_414, 0.29),
    (253_414, float("inf"), 0.33),
]

FEDERAL_BPA_MAX = 16_129.0
FEDERAL_BPA_MIN = 14_538.0
FEDERAL_BPA_PHASEOUT_START = 177_882.0  # start of the 3rd->4th bracket band
FEDERAL_BPA_PHASEOUT_END = 253_414.0

# ---------------------------------------------------------------------------
# Alberta brackets (2025) -- includes the new 8% bracket on the first $60,000
# ---------------------------------------------------------------------------
AB_LOWEST_RATE = 0.08

AB_BRACKETS_2025: list[tuple[float, float, float]] = [
    (0, 60_000, 0.08),
    (60_000, 151_234, 0.10),
    (151_234, 181_481, 0.12),
    (181_481, 241_974, 0.13),
    (241_974, 362_961, 0.14),
    (362_961, float("inf"), 0.15),
]

AB_BPA_2025 = 22_323.0

# ---------------------------------------------------------------------------
# Dividend gross-up and dividend tax credit (DTC) rates, 2025
# ---------------------------------------------------------------------------
DIVIDEND_GROSS_UP = {
    "eligible": 0.38,
    "non_eligible": 0.15,
}

FEDERAL_DTC_RATE = {  # % of the grossed-up dividend
    "eligible": 0.150198,
    "non_eligible": 0.090301,
}

AB_DTC_RATE = {  # % of the grossed-up dividend
    "eligible": 0.0812,
    "non_eligible": 0.0218,
}

# ---------------------------------------------------------------------------
# Capital gains (2025)
# ---------------------------------------------------------------------------
CAPITAL_GAINS_INCLUSION_RATE = 0.50


# ---------------------------------------------------------------------------
# Core bracket math
# ---------------------------------------------------------------------------
def _bracket_tax(income: float, brackets: list[tuple[float, float, float]]) -> float:
    """Progressive-bracket tax on `income`, ignoring credits."""
    if income <= 0:
        return 0.0
    tax = 0.0
    for lower, upper, rate in brackets:
        if income <= lower:
            break
        bracket_amount = min(income, upper) - lower
        tax += bracket_amount * rate
    return tax


def federal_bpa(taxable_income: float) -> float:
    """Federal basic personal amount, phased down for high earners."""
    if taxable_income <= FEDERAL_BPA_PHASEOUT_START:
        return FEDERAL_BPA_MAX
    if taxable_income >= FEDERAL_BPA_PHASEOUT_END:
        return FEDERAL_BPA_MIN
    span = FEDERAL_BPA_PHASEOUT_END - FEDERAL_BPA_PHASEOUT_START
    frac = (taxable_income - FEDERAL_BPA_PHASEOUT_START) / span
    return FEDERAL_BPA_MAX - frac * (FEDERAL_BPA_MAX - FEDERAL_BPA_MIN)


def federal_tax(taxable_income: float) -> float:
    """Federal tax payable on ordinary (non-dividend) taxable income."""
    if taxable_income <= 0:
        return 0.0
    base = _bracket_tax(taxable_income, FEDERAL_BRACKETS_2025)
    credit = federal_bpa(taxable_income) * FEDERAL_LOWEST_RATE
    return max(0.0, base - credit)


def alberta_tax(taxable_income: float) -> float:
    """Alberta tax payable on ordinary (non-dividend) taxable income."""
    if taxable_income <= 0:
        return 0.0
    base = _bracket_tax(taxable_income, AB_BRACKETS_2025)
    credit = AB_BPA_2025 * AB_LOWEST_RATE
    return max(0.0, base - credit)


def tax_on_income(taxable_income: float) -> dict:
    """Total federal + Alberta tax on ordinary taxable income (e.g. salary)."""
    fed = federal_tax(taxable_income)
    ab = alberta_tax(taxable_income)
    return {"federal": fed, "alberta": ab, "total": fed + ab}


def combined_tax(
    employment_income: float,
    dividends: float,
    dividend_type: str,
    deductions: float = 0.0,
) -> dict:
    """Total tax on employment income plus dividends stacked on top.

    `deductions` (e.g. deductible loan interest) reduces employment income
    before tax is computed, floored at zero -- it is a real income deduction,
    not a flat-rate approximation, so its effect falls out of the actual
    bracket math (including how it interacts with the BPA phase-out).

    Dividends are grossed up and taxed at the marginal rate above salary,
    then the dividend tax credit (federal + Alberta) is applied. Only the
    final combined amount is floored at zero -- the dividend's own marginal
    contribution (`tax_on_dividend_marginal`) may legitimately be negative
    (a well-known Alberta result for eligible dividends at moderate income),
    since the DTC can exceed the incremental bracket tax from adding the
    grossed-up dividend on top of salary.
    """
    deductible_income = max(0.0, employment_income - deductions)
    salary_only_total = tax_on_income(deductible_income)["total"]

    if dividends == 0:
        return {
            "total_tax": salary_only_total,
            "tax_on_salary": salary_only_total,
            "tax_on_dividend_marginal": 0.0,
            "average_rate": (salary_only_total / deductible_income) if deductible_income > 0 else 0.0,
            "federal_tax": tax_on_income(deductible_income)["federal"],
            "alberta_tax": tax_on_income(deductible_income)["alberta"],
        }

    gross_up = DIVIDEND_GROSS_UP[dividend_type]
    grossed_up_dividend = dividends * (1 + gross_up)
    taxable = deductible_income + grossed_up_dividend

    fed_base = _bracket_tax(taxable, FEDERAL_BRACKETS_2025)
    ab_base = _bracket_tax(taxable, AB_BRACKETS_2025)
    fed_bpa_credit = federal_bpa(taxable) * FEDERAL_LOWEST_RATE
    ab_bpa_credit = AB_BPA_2025 * AB_LOWEST_RATE
    fed_dtc = grossed_up_dividend * FEDERAL_DTC_RATE[dividend_type]
    ab_dtc = grossed_up_dividend * AB_DTC_RATE[dividend_type]

    fed_tax_raw = fed_base - fed_bpa_credit - fed_dtc
    ab_tax_raw = ab_base - ab_bpa_credit - ab_dtc

    total_tax = max(0.0, fed_tax_raw + ab_tax_raw)
    tax_on_dividend_marginal = total_tax - salary_only_total
    average_rate = (total_tax / taxable) if taxable > 0 else 0.0

    return {
        "total_tax": total_tax,
        "tax_on_salary": salary_only_total,
        "tax_on_dividend_marginal": tax_on_dividend_marginal,
        "average_rate": average_rate,
        "federal_tax": fed_tax_raw,
        "alberta_tax": ab_tax_raw,
    }


def marginal_dividend_tax(
    employment_income: float,
    dividends: float,
    dividend_type: str,
    deductions: float = 0.0,
) -> float:
    """Incremental tax attributable to the dividend, net of the DTC.

    May be negative -- see `combined_tax` docstring.
    """
    if dividends == 0:
        return 0.0
    return combined_tax(employment_income, dividends, dividend_type, deductions=deductions)[
        "tax_on_dividend_marginal"
    ]


def capital_gains_tax(realized_gain: float, other_income: float) -> float:
    """Tax on a realized capital gain, stacked marginally on top of other income.

    Only the inclusion-rate-adjusted (50%) portion of the gain is taxable.
    """
    if realized_gain <= 0:
        return 0.0
    taxable_gain = realized_gain * CAPITAL_GAINS_INCLUSION_RATE
    tax_with = tax_on_income(other_income + taxable_gain)["total"]
    tax_without = tax_on_income(other_income)["total"]
    return tax_with - tax_without
