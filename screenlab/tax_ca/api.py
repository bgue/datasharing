"""Public contract of the tax_ca package: `compute` and `marginal_rates`.

Canada only. Stacking order: salary -> dividends (grossed up) -> foreign income -> capital gains.
Each marginal = total with the layer - total without it; only the final total is floored at zero
(the dividend / foreign / gain marginals may therefore be negative). Not tax advice; see README.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .core import Jurisdiction, bracket_tax
from .tables import FEDERAL, JURISDICTIONS, TAX_YEARS, CAPITAL_GAINS_INCLUSION

__all__ = ["TaxResult", "TaxCaError", "compute", "marginal_rates", "income_for_marginal_rate"]

_INCREMENT = 100.0


class TaxCaError(ValueError):
    """Unknown province or unsupported tax year."""


@dataclass
class TaxResult:
    total: float
    federal: float
    provincial: float
    taxable_income: float
    marginal_rate_ordinary: float
    average_rate: float
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _lookup(province: str, year: int) -> tuple[Jurisdiction, Jurisdiction, float]:
    key = province.upper() if isinstance(province, str) else province
    if key not in JURISDICTIONS:
        raise TaxCaError(f"unknown province {province!r}; supported: {sorted(JURISDICTIONS)}")
    if year not in TAX_YEARS or year not in JURISDICTIONS[key] or year not in FEDERAL:
        raise TaxCaError(f"unsupported tax year {year!r} for {key}; supported: {list(TAX_YEARS)}")
    return FEDERAL[year], JURISDICTIONS[key][year], CAPITAL_GAINS_INCLUSION[year]


def _one(j: Jurisdiction, taxable: float, ordinary: float, elig: float, nonelig: float, is_prov: bool) -> float:
    """Raw (un-floored) tax of one jurisdiction on `taxable` income (grossed-up dividends included)."""
    gu_e = elig * (1 + j.dtc.gross_up["eligible"])
    gu_n = nonelig * (1 + j.dtc.gross_up["non_eligible"])
    tax = bracket_tax(taxable, j.brackets)
    tax -= j.bpa.amount(taxable) * j.lowest_rate
    tax -= gu_e * j.dtc.dtc_rate["eligible"] + gu_n * j.dtc.dtc_rate["non_eligible"]
    if j.low_income_reduction is not None:
        tax -= j.low_income_reduction.credit(taxable)
    if j.surtax:
        tax += sum(max(0.0, tax - thr) * r for thr, r in j.surtax)
    if j.health_premium:
        tax += j.health_premium.amount(taxable)
    return tax


def _tax(fed: Jurisdiction, prov: Jurisdiction, ordinary: float, elig: float, nonelig: float,
         foreign: float, taxable_gain: float) -> tuple[float, float, float, float]:
    """(federal, provincial, total, taxable_income) before any foreign tax credit.

    With no dividends each jurisdiction is floored at zero separately (as the owner's `tax_on_income`);
    with dividends only the combined total is floored (as the owner's `combined_tax`).
    """
    gu = elig * (1 + fed.dtc.gross_up["eligible"]) + nonelig * (1 + fed.dtc.gross_up["non_eligible"])
    taxable = max(0.0, ordinary) + gu + foreign + taxable_gain
    f = _one(fed, taxable, ordinary, elig, nonelig, False)
    p = _one(prov, taxable, ordinary, elig, nonelig, True)
    if taxable <= 0:
        return 0.0, 0.0, 0.0, taxable
    if elig + nonelig > 0:
        return f, p, max(0.0, f + p), taxable
    f, p = max(0.0, f), max(0.0, p)
    return f, p, f + p, taxable


def compute(province: str, year: int, *, employment_income: float = 0.0, deductions: float = 0.0,
            eligible_dividends: float = 0.0, non_eligible_dividends: float = 0.0,
            foreign_income: float = 0.0, foreign_tax_paid: float = 0.0,
            capital_gains: float = 0.0, capital_losses_applied: float = 0.0) -> TaxResult:
    """Federal + provincial tax for one individual and year. Dividend amounts are cash (pre gross-up)."""
    fed, prov, incl = _lookup(province, year)
    sal = max(0.0, employment_income - deductions)
    e, n = max(0.0, eligible_dividends), max(0.0, non_eligible_dividends)
    fi = max(0.0, foreign_income)
    tg = max(0.0, capital_gains - capital_losses_applied) * incl

    t0 = _tax(fed, prov, sal, 0, 0, 0, 0)
    t1 = _tax(fed, prov, sal, e, n, 0, 0)
    t2 = _tax(fed, prov, sal, e, n, fi, 0)
    t3 = _tax(fed, prov, sal, e, n, fi, tg)

    # Foreign tax credit (APPROXIMATION of the CRA's FTC): min(foreign tax paid, Canadian tax on that
    # income stacked after salary + dividends), allocated federal/provincial pro rata to their increments.
    d_f, d_p = t2[0] - t1[0], t2[1] - t1[1]
    can_tax_foreign = max(0.0, t2[2] - t1[2])
    ftc = min(max(0.0, foreign_tax_paid), can_tax_foreign) if fi > 0 else 0.0
    w_f = d_f / (d_f + d_p) if (d_f + d_p) > 0 else 0.5
    ftc_f, ftc_p = ftc * w_f, ftc * (1 - w_f)

    f3, p3 = t3[0] - ftc_f, t3[1] - ftc_p
    total = max(0.0, f3 + p3) if (e + n + fi + tg) > 0 else t3[2]
    t2_tot = max(0.0, t2[0] + t2[1] - ftc) if (e + n + fi) > 0 else t2[2]
    t1_tot = t1[2]
    t0_tot = t0[2]
    taxable = t3[3]

    # marginal rate on the next dollar of ordinary income, whole position, $100 numerical
    up = _tax(fed, prov, sal + _INCREMENT, e, n, fi, tg)
    up_tot = max(0.0, up[0] + up[1] - ftc) if (e + n + fi + tg) > 0 else up[2]
    marginal = (up_tot - total) / _INCREMENT

    breakdown = {
        "tax_on_salary": t0_tot,
        "tax_on_dividends_marginal": t1_tot - t0_tot,
        "tax_on_foreign_income_marginal": t2_tot - t1_tot,
        "tax_on_capital_gains_marginal": total - t2_tot,
        "foreign_tax_credit": ftc,
    }
    if prov.low_income_reduction is not None:
        breakdown["low_income_reduction"] = prov.low_income_reduction.credit(taxable)
    return TaxResult(total=total, federal=f3 if total > 0 else 0.0,
                     provincial=(total - f3) if total > 0 else 0.0, taxable_income=taxable,
                     marginal_rate_ordinary=marginal,
                     average_rate=(total / taxable) if taxable > 0 else 0.0, breakdown=breakdown)


def marginal_rates(province: str, year: int, employment_income: float, deductions: float = 0.0) -> dict:
    """Marginal rate on the next $1 of ordinary income, eligible dividend, non-eligible dividend and
    capital gain (cash amounts), numerically with a $100 increment, stacked on salary less deductions."""
    base = compute(province, year, employment_income=employment_income, deductions=deductions).total
    h = _INCREMENT

    def rate(**kw) -> float:
        return (compute(province, year, employment_income=employment_income, deductions=deductions,
                        **kw).total - base) / h

    sal_up = compute(province, year, employment_income=employment_income + h, deductions=deductions).total
    return {
        "ordinary": (sal_up - base) / h,
        "eligible_dividend": rate(eligible_dividends=h),
        "non_eligible_dividend": rate(non_eligible_dividends=h),
        "capital_gain": rate(capital_gains=h),
    }


def income_for_marginal_rate(province: str, year: int, rate: float, *, max_income: float = 500_000.0) -> float:
    """# Q1: the other income ($, on a $100 grid) whose marginal ORDINARY rate is nearest to `rate` (a fraction).
    Ties go to the lower income. Used to turn a direct `marginal_rate` into a bracket position."""
    best, best_gap = 0.0, float("inf")
    for i in range(int(max_income / _INCREMENT) + 1):
        inc = i * _INCREMENT
        gap = abs(compute(province, year, employment_income=inc).marginal_rate_ordinary - rate)
        if gap < best_gap - 1e-12:
            best, best_gap = inc, gap
    return best
