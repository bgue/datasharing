"""tax_ca: AB reproduces the owner's reference module; BC hand-worked checks; sanity and errors."""
import importlib.util
import itertools
from pathlib import Path

import pytest

from screenlab.tax_ca import TaxCaError, compute, marginal_rates

_p = Path(__file__).resolve().parents[1] / "tax_ca" / "owner_ab_2025.py"
_spec = importlib.util.spec_from_file_location("owner_ab_2025", _p)
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)

INCOMES = (0, 20_000, 50_000, 90_000, 150_000, 200_000, 300_000)
DIVS = (0, 10_000, 50_000)
TYPES = ("eligible", "non_eligible")


@pytest.mark.parametrize("inc,div,typ", list(itertools.product(INCOMES, DIVS, TYPES)))
def test_ab_matches_owner(inc, div, typ):
    kw = {"eligible_dividends": div} if typ == "eligible" else {"non_eligible_dividends": div}
    r = compute("AB", 2025, employment_income=inc, **kw)
    o = owner.combined_tax(inc, div, typ)
    assert r.total == pytest.approx(o["total_tax"], abs=1e-6)
    assert r.breakdown["tax_on_salary"] == pytest.approx(o["tax_on_salary"], abs=1e-6)
    assert r.breakdown["tax_on_dividends_marginal"] == pytest.approx(o["tax_on_dividend_marginal"], abs=1e-6)
    assert r.average_rate == pytest.approx(o["average_rate"], abs=1e-9)
    if div and o["total_tax"] > 0:  # when the total is floored to 0, the split is reported as 0/0
        assert r.federal == pytest.approx(o["federal_tax"], abs=1e-6)
        assert r.provincial == pytest.approx(o["alberta_tax"], abs=1e-6)


@pytest.mark.parametrize("inc,deduct", [(50_000, 5_000), (90_000, 20_000), (10_000, 30_000)])
def test_ab_deductions_match_owner(inc, deduct):
    r = compute("AB", 2025, employment_income=inc, deductions=deduct, eligible_dividends=10_000)
    o = owner.combined_tax(inc, 10_000, "eligible", deductions=deduct)
    assert r.total == pytest.approx(o["total_tax"], abs=1e-6)


@pytest.mark.parametrize("gain,other", list(itertools.product((5_000, 20_000, 100_000), INCOMES)))
def test_ab_capital_gains_match_owner(gain, other):
    r = compute("AB", 2025, employment_income=other, capital_gains=gain)
    assert r.breakdown["tax_on_capital_gains_marginal"] == pytest.approx(
        owner.capital_gains_tax(gain, other), abs=1e-6)


def test_ab_negative_marginal_dividend_at_moderate_income():
    for inc in (20_000, 30_000):  # owner: -274.1 and -88.3
        r = compute("AB", 2025, employment_income=inc, eligible_dividends=10_000)
        assert r.breakdown["tax_on_dividends_marginal"] < 0
        assert r.breakdown["tax_on_dividends_marginal"] == pytest.approx(
            owner.marginal_dividend_tax(inc, 10_000, "eligible"), abs=1e-6)
        assert r.total >= 0


# BC hand-worked (2025 tables: brackets 5.06% to 49,279, 7.70% to 98,560; BPA 12,932; fed BPA 16,129 @14.5%)
def test_bc_salary_60k_hand_worked():
    # Fed: 57,375*.145 + 2,625*.205 = 8,857.50; - 16,129*.145 (2,338.705) = 6,518.795
    # BC : 49,279*.0506 + 10,721*.077 = 3,319.0344; - 12,932*.0506 (654.3592) = 2,664.6752; no reduction (>40,807)
    r = compute("BC", 2025, employment_income=60_000)
    assert r.federal == pytest.approx(6_518.795, abs=1e-3)
    assert r.provincial == pytest.approx(2_664.6752, abs=1e-3)
    assert r.total == pytest.approx(9_183.4702, abs=1e-3)
    assert r.breakdown["low_income_reduction"] == 0


def test_bc_salary_30k_low_income_reduction():
    # Fed: 30,000*.145 - 2,338.705 = 2,011.295
    # BC : 30,000*.0506 - 654.3592 = 863.6408; reduction 562 - .0356*(30,000-25,020) = 384.712 -> 478.9288
    r = compute("BC", 2025, employment_income=30_000)
    assert r.breakdown["low_income_reduction"] == pytest.approx(384.712, abs=1e-6)
    assert r.provincial == pytest.approx(478.9288, abs=1e-3)
    assert r.total == pytest.approx(2_490.2238, abs=1e-3)


def test_bc_eligible_dividend_on_60k():
    # gross-up 13,800; taxable 73,800.
    # Fed: 8,319.375 + 16,425*.205 (3,367.125) = 11,686.5 - 2,338.705 - 13,800*.150198 (2,072.7324) = 7,275.0626
    # BC : 2,493.5174 + 24,521*.077 (1,888.117) = 4,381.6344 - 654.3592 - 13,800*.12 (1,656) = 2,071.2752
    r = compute("BC", 2025, employment_income=60_000, eligible_dividends=10_000)
    assert r.total == pytest.approx(9_346.3378, abs=1e-3)
    assert r.breakdown["tax_on_dividends_marginal"] == pytest.approx(9_346.3378 - 9_183.4702, abs=1e-3)


def test_bc_zero_income():
    assert compute("BC", 2025).total == 0.0


def test_stacking_cg_and_foreign():
    r = compute("BC", 2025, employment_income=80_000, foreign_income=2_000, foreign_tax_paid=300,
                capital_gains=10_000, capital_losses_applied=4_000)
    b = r.breakdown
    assert r.total == pytest.approx(b["tax_on_salary"] + b["tax_on_dividends_marginal"]
                                    + b["tax_on_foreign_income_marginal"] + b["tax_on_capital_gains_marginal"])
    assert 0 < b["foreign_tax_credit"] <= 300
    assert r.taxable_income == pytest.approx(80_000 + 2_000 + 3_000)
    assert r.to_dict()["total"] == r.total


@pytest.mark.parametrize("prov", ["AB", "BC"])
def test_marginal_rates_sanity(prov):
    m = marginal_rates(prov, 2025, 90_000)
    assert m["eligible_dividend"] < m["ordinary"]
    assert m["capital_gain"] < m["ordinary"]
    assert m["ordinary"] > 0


def test_unknown_province_and_year():
    with pytest.raises(TaxCaError, match="unknown province"):
        compute("ZZ", 2025)
    with pytest.raises(TaxCaError, match="unsupported tax year"):
        compute("AB", 1999)
    with pytest.raises(ValueError):
        marginal_rates("ON", 2025, 50_000)
