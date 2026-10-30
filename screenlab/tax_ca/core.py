"""Jurisdiction-agnostic bracket math and the dataclasses a tax table is built from."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

INF = float("inf")


@dataclass(frozen=True)
class Brackets:
    """Progressive bands as (lower, upper, rate) tuples, taxable income."""
    bands: tuple[tuple[float, float, float], ...]

    def __iter__(self):
        return iter(self.bands)


def bracket_tax(income: float, brackets) -> float:
    """Progressive-bracket tax on `income`, ignoring credits. `brackets`: Brackets or list of bands."""
    if income <= 0:
        return 0.0
    tax = 0.0
    for lower, upper, rate in brackets:
        if income <= lower:
            break
        tax += (min(income, upper) - lower) * rate
    return tax


def phase_out(value: float, start: float, end: float, at_start: float, at_end: float) -> float:
    """Linear interpolation: `at_start` for value <= start, `at_end` for value >= end."""
    if value <= start:
        return at_start
    if value >= end:
        return at_end
    return at_start - (value - start) / (end - start) * (at_start - at_end)


@dataclass(frozen=True)
class BPA:
    """Basic personal amount: flat (`minimum` None) or phased down between start and end."""
    maximum: float
    minimum: Optional[float] = None
    phaseout_start: Optional[float] = None
    phaseout_end: Optional[float] = None

    def amount(self, income: float) -> float:
        if self.minimum is None:
            return self.maximum
        return phase_out(income, self.phaseout_start, self.phaseout_end, self.maximum, self.minimum)


@dataclass(frozen=True)
class DividendRates:
    gross_up: dict   # {"eligible": .., "non_eligible": ..}
    dtc_rate: dict   # % of the grossed-up dividend


@dataclass(frozen=True)
class LowIncomeReduction:
    """Credit = max(0, base - rate * max(0, income - threshold)) (BC-style tax reduction credit)."""
    base: float
    threshold: float
    rate: float

    def credit(self, income: float) -> float:
        return max(0.0, self.base - self.rate * max(0.0, income - self.threshold))


@dataclass(frozen=True)
class HealthPremium:
    """Stepped premium: tiers of (income_from, amount); the last tier with income_from <= income applies."""
    tiers: tuple[tuple[float, float], ...]

    def amount(self, income: float) -> float:
        amt = 0.0
        for lo, a in self.tiers:
            if income >= lo:
                amt = a
        return amt


@dataclass(frozen=True)
class Jurisdiction:
    name: str
    year: int
    brackets: Brackets
    bpa: BPA
    lowest_rate: float          # rate applied to the BPA credit
    dtc: DividendRates
    surtax: Optional[tuple[tuple[float, float], ...]] = None   # (threshold on basic tax, rate); not used by AB/BC
    low_income_reduction: Optional[LowIncomeReduction] = None
    health_premium: Optional[HealthPremium] = None
