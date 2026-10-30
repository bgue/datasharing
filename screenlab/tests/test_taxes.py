"""Tax stage (taxes.py, pipeline.TaxStage): exact arithmetic on a hand-built two-name market, loss carry-forward,
account types, serialization, report columns, and an end-to-end synthetic run.

The hand-built market: names A and B on business days 2020-01-01 .. 2021-12-31, 12-month rebalance, no costs.
A is picked on 2020-01-01 (yield 4% = 0.4 / 10), B on 2021-01-01 (yield 2%). A's total-return index is flat at 1
until 2020-12-31 and steps to `A_AFTER` on 2021-01-01 (the sale date); B stays at 1. Wealth is 1.0, $100,000.
Dividend income of a bar = value at the start of the bar x yield x days / 365.25.

    python -m pytest screenlab/tests/test_taxes.py -q
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from screenlab import Config, ExitPolicy, ExitScreen, Lab, Market, ScreenBuilder, TrendBreak
from screenlab.backtest import run
from screenlab.data import add_ratios
from screenlab.pipeline import simulate_paths
from screenlab.serial import from_dict, to_dict
from screenlab.tax_ca import compute
from screenlab.tax_ca import owner_ab_2025 as owner
from screenlab.taxes import (TAX_MODELS, CanadianTax, NoTax, PlaceholderCanadianTax, TaxConfig, TaxModel, YearTax, assess,
                             register_tax_model)

CAP = 100_000.0
# marginal 0.40, eligible dividends: 1.38 x (0.40 - 0.15 - 0.10) = 0.207 of the cash amount; gains: 0.5 x 0.40 = 0.20
MODEL = PlaceholderCanadianTax(marginal_rate=0.40, federal_dtc=0.15, provincial_dtc=0.10, provincial_dtc_noneligible=0.03)
DIV_RATE, CG_RATE = 0.207, 0.20
D = 365.25


def market(a_after=1.5):
    dates = pd.bdate_range("2020-01-01", "2021-12-31")
    prices = pd.DataFrame({"A": np.where(dates < "2021-01-01", 1.0, a_after), "B": 1.0}, index=dates)

    def row(d, t, sector, div):
        return dict(date=pd.Timestamp(d), ticker=t, sector=sector, price=10.0, market_cap=1000.0, dividend=div)
    fund = pd.DataFrame([row("2020-01-01", "A", "in", 0.4), row("2020-01-01", "B", "out", 0.2),
                         row("2021-01-01", "A", "out", 0.4), row("2021-01-01", "B", "in", 0.2)])
    return Market(add_ratios(fund), prices)


SCREEN = ScreenBuilder("pick", n=1).where("sector == 'in'").build()


def go(tax, a_after=1.5):
    cfg = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tax)
    return run(SCREEN, market(a_after), cfg, start="2020-01-01")


def tx(account="taxable", model=MODEL, **kw):
    return TaxConfig(model=model, account=account, capital=CAP, **kw)


# expected pre-tax units. 2020: 365 days of A dividends; the 2021-01-01 bar adds 1 day (A, value 1.0 at its start);
# A's basis at the sale is 1 + 0.04 x 366 / 365.25 (dividends reinvested); the sale brings in A_AFTER.
DIV_A_2020 = 0.04 * 365 / D
DIV_A_2021 = 0.04 * 1 / D
DIV_B_2021 = 0.02 * 1.5 * 364 / D                     # B held from 2021-01-01 (value 1.5) to 2021-12-31
BASIS_A = 1 + 0.04 * 366 / D


def test_no_tax_by_default_leaves_result_unchanged():
    base = run(SCREEN, market(), Config(rebalance_months=12, cost_bps=0, min_holdings=1), start="2020-01-01")
    taxed = go(tx())
    assert base.tax is None and base.after_tax_curve is None
    pd.testing.assert_series_equal(base.curve, taxed.curve)           # tax never touches the pre-tax curve
    assert Config().tax is None and "tax" not in Config().to_dict()


def test_taxable_exact_dividends_and_one_sale():
    res = go(tx())
    t = res.tax
    assert list(t.index) == [2020, 2021]
    # 2020: dividends only
    inc20 = CAP * DIV_A_2020
    assert t.loc[2020, "dividends_ca"] == pytest.approx(inc20, rel=1e-12)
    assert t.loc[2020, "tax_dividends"] == pytest.approx(DIV_RATE * inc20, rel=1e-12)
    assert t.loc[2020, "realized_gains"] == 0 and t.loc[2020, "tax_capital_gains"] == 0
    f1 = 1 - DIV_RATE * inc20 / CAP                                    # tax paid from the portfolio on 2020-12-31
    # 2021: A sold at 1.5 on 2021-01-01 (gain 1.5 - basis), B's dividends; all dollars scaled by the factor f1
    inc21 = CAP * f1 * (DIV_A_2021 + DIV_B_2021)
    gain21 = CAP * f1 * (1.5 - BASIS_A)
    assert t.loc[2021, "dividends_ca"] == pytest.approx(inc21, rel=1e-12)
    assert t.loc[2021, "realized_gains"] == pytest.approx(gain21, rel=1e-12)
    assert t.loc[2021, "tax_capital_gains"] == pytest.approx(CG_RATE * gain21, rel=1e-12)
    tax21 = DIV_RATE * inc21 + CG_RATE * gain21
    assert t.loc[2021, "tax_paid"] == pytest.approx(tax21, rel=1e-12)
    assert t.loc[2021, "cum_tax_paid"] == pytest.approx(DIV_RATE * inc20 + tax21, rel=1e-12)
    # the after-tax curve: pre-tax x (1 - tax / wealth) at each year end
    f2 = f1 * (1 - tax21 / (CAP * f1 * 1.5))
    assert res.after_tax_curve.iloc[-1] == pytest.approx(1.5 * f2, rel=1e-12)
    assert res.after_tax_curve.loc["2020-12-30"] == pytest.approx(1.0)  # before the year-end payment
    assert res.after_tax_curve.loc["2020-12-31"] == pytest.approx(f1)
    assert t.loc[2021, "wealth_after_tax"] == pytest.approx(CAP * 1.5 * f2, rel=1e-12)
    assert t.loc[2021, "cum_drag"] == pytest.approx(1 - f2, rel=1e-12)


@pytest.mark.parametrize("account", ["tfsa", "rrsp"])
def test_registered_accounts_pay_no_domestic_tax(account):
    res = go(tx(account))
    assert (res.tax["tax_paid"] == 0).all() and (res.tax["cum_drag"] == 0).all()
    pd.testing.assert_series_equal(res.after_tax_curve, res.curve, check_names=False)
    assert res.tax.loc[2021, "realized_gains"] == pytest.approx(CAP * (1.5 - BASIS_A), rel=1e-12)   # still recorded


def test_us_withholding_by_account():
    inc20 = CAP * DIV_A_2020                                            # all of A's income is foreign
    taxable = go(tx(foreign=("A",))).tax
    assert taxable.loc[2020, "dividends_foreign"] == pytest.approx(inc20, rel=1e-12)
    assert taxable.loc[2020, "tax_foreign_dividends"] == pytest.approx(inc20 * 0.40, rel=1e-12)   # max(15%, marginal)
    assert taxable.loc[2020, "tax_dividends"] == 0
    tfsa = go(tx("tfsa", foreign=("A",))).tax
    assert tfsa.loc[2020, "tax_foreign_dividends"] == pytest.approx(inc20 * 0.15, rel=1e-12)       # not recoverable
    rrsp = go(tx("rrsp", foreign=("A",))).tax
    assert rrsp.loc[2020, "tax_foreign_dividends"] == 0                                              # treaty exempt
    wh20 = go(tx("tfsa", foreign=("A",), withholding={"US": 0.30})).tax
    assert wh20.loc[2020, "tax_foreign_dividends"] == pytest.approx(inc20 * 0.30, rel=1e-12)


def test_loss_is_realized_and_carried_not_taxed():
    res = go(tx(), a_after=0.6)
    t = res.tax
    loss = CAP * (1 - DIV_RATE * CAP * DIV_A_2020 / CAP) * (0.6 - BASIS_A)
    assert t.loc[2021, "realized_losses"] == pytest.approx(loss, rel=1e-12) and loss < 0
    assert t.loc[2021, "tax_capital_gains"] == 0
    assert t.loc[2021, "loss_carry_out"] == pytest.approx(-loss, rel=1e-12)


def test_losses_carry_forward_across_years():
    idx = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    curve = pd.Series(1.0, index=idx)
    ev = pd.DataFrame({"div_ca": 0.0, "div_fx": 0.0, "gain": 0.0, "loss": 0.0}, index=idx)
    ev.loc["2020-06-01", "loss"] = -0.30                                  # year 1: net loss 0.30
    ev.loc["2021-06-01", ["gain", "loss"]] = [0.10, -0.05]                # year 2: net +0.05, absorbed
    ev.loc["2022-06-01", "gain"] = 0.50                                   # year 3: 0.50 - carry 0.25 = 0.25 taxable
    ev.loc["2023-06-01", "gain"] = 0.10                                   # year 4: no carry left
    t, after = assess(curve, ev, tx(pay_from_portfolio=False), CAP)
    assert list(t["loss_carry_out"].round(10)) == [30_000, 25_000, 0, 0]
    assert t["tax_capital_gains"].round(6).tolist() == [0, 0, CG_RATE * 25_000, CG_RATE * 10_000]
    assert t["tax_paid"].round(6).tolist() == [0, 0, 5_000, 2_000]
    assert after.iloc[-1] == pytest.approx(1.0 - 7_000 / CAP)             # paid from outside: curve less cumulative tax
    t2, _ = assess(curve, ev, tx("tfsa"), CAP)                            # sheltered: no tax, no carry
    assert (t2["tax_paid"] == 0).all() and (t2["loss_carry_out"] == 0).all()


def test_pay_from_portfolio_flag():
    inside, outside = go(tx()), go(tx(pay_from_portfolio=False))
    # paid from outside: dollars are never scaled down, the curve is the pre-tax curve less the cumulative tax
    paid = CAP * (DIV_RATE * (DIV_A_2020 + DIV_A_2021 + DIV_B_2021) + CG_RATE * (1.5 - BASIS_A))
    assert outside.tax["tax_paid"].sum() == pytest.approx(paid, rel=1e-12)
    assert outside.after_tax_curve.iloc[-1] == pytest.approx(1.5 - paid / CAP, rel=1e-12)
    assert outside.after_tax_curve.loc["2020-12-30"] == pytest.approx(1.0)
    assert outside.tax["tax_paid"].sum() > inside.tax["tax_paid"].sum()       # inside, later events are on a smaller account


def test_liquidate_at_end_realizes_the_remaining_gain():
    base, liq = go(tx()).tax, go(tx(liquidate_at_end=True)).tax
    f1 = 1 - DIV_RATE * CAP * DIV_A_2020 / CAP
    # B was bought at 1.5 and its reinvested dividends raised the cost basis above the (flat) value: a small loss
    assert base.loc[2021, "realized_losses"] == 0
    assert liq.loc[2021, "realized_losses"] == pytest.approx(-CAP * f1 * DIV_B_2021, rel=1e-9)
    assert liq.loc[2021, "realized_gains"] == base.loc[2021, "realized_gains"]


def test_no_tax_model():
    res = go(TaxConfig(model=NoTax(), capital=CAP))
    assert res.tax["tax_paid"].sum() == 0
    pd.testing.assert_series_equal(res.after_tax_curve, res.curve, check_names=False)


def test_model_validation_and_describe():
    assert isinstance(MODEL, TaxModel) and isinstance(NoTax(), TaxModel)
    with pytest.raises(ValueError, match="ON only"):
        PlaceholderCanadianTax(province="BC")
    with pytest.raises(ValueError, match="account"):
        TaxConfig(account="margin")
    d = MODEL.describe()
    assert d["PLACEHOLDER"] is True and d["province"] == "ON" and d["marginal_rate"] == 0.40
    assert MODEL.tax_on_dividends(100.0, True, 2024) == pytest.approx(20.7)
    assert MODEL.tax_on_capital_gain(100.0, 2024) == pytest.approx(20.0)
    assert MODEL.tax_on_capital_gain(-100.0, 2024) == 0.0
    assert PlaceholderCanadianTax(income=100_000).marginal == 0.3148          # bracket lookup (illustrative)


# --------------------------------------------------------------------------- serialization
@register_tax_model("TestFlatTax")
@dataclass(frozen=True)
class FlatTax:
    """A stand-in for the owner's module: a flat rate on everything."""
    rate: float = 0.25
    province: str = "ON"

    def tax_on_dividends(self, amount, eligible, year):
        return amount * self.rate

    def tax_on_capital_gain(self, gain, year):
        return max(gain, 0.0) * self.rate

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        return amount * (0.0 if account == "rrsp" else self.rate)

    def describe(self):
        return {"model": "FlatTax", "rate": self.rate}


def test_serialization_round_trip_and_custom_model():
    t = TaxConfig(model=MODEL, account="tfsa", foreign=("A", "B"), withholding={"US": 0.1}, capital=5_000.0)
    assert from_dict(to_dict(t)) == t
    cfg = Config(cost_bps=10, tax=t)
    back = from_dict(cfg.to_dict())
    assert back == cfg and back.tax == t
    assert "tax" not in Config(cost_bps=10).to_dict()                     # run keys of untaxed inputs are unchanged
    mine = TaxConfig(model=FlatTax(0.3))
    assert from_dict(to_dict(mine)).model == FlatTax(0.3) and to_dict(mine)["model"]["__t"] == "TestFlatTax"
    res = go(tx(model=FlatTax(0.25)))                                      # a custom model plugs straight in
    assert res.tax["tax_dividends"].iloc[0] == pytest.approx(0.25 * CAP * DIV_A_2020, rel=1e-12)


def test_monte_carlo_with_tax_is_not_supported():
    with pytest.raises(NotImplementedError, match="Config.tax"):
        simulate_paths(None, Config(tax=TaxConfig()), returns=np.zeros((1, 3, 2)), dates=pd.bdate_range("2020-01-01", periods=3))


# --------------------------------------------------------------------------- report and end to end
@pytest.fixture(scope="module")
def syn():
    return Lab.synthetic(freq="ME", n_stocks=80, years=15)


def test_report_columns_only_with_tax(syn):
    plain = syn.backtest(["dogs"])
    assert not {"AfterTaxCAGR", "TaxDrag/yr", "AfterTaxExcess/yr"} & set(plain.table.columns) and plain.tax == {}
    rep = syn.backtest(["dogs"], tax=TaxConfig())
    assert {"AfterTaxCAGR", "TaxDrag/yr", "AfterTaxExcess/yr"} <= set(rep.table.columns)
    assert set(rep.tax) == {"dogs", rep.benchmark_label}                  # the index is taxed too
    assert rep.table.loc[rep.benchmark_label, "AfterTaxCAGR"] < rep.table.loc[rep.benchmark_label, "CAGR"]
    row = rep.table.loc["dogs"]
    ex = row["AfterTaxCAGR"] - rep.table.loc[rep.benchmark_label, "AfterTaxCAGR"]
    assert row["AfterTaxExcess/yr"] == pytest.approx(ex)
    assert "AfterTaxCAGR" in str(rep)
    for c in ("CAGR", "Vol", "Excess/yr"):                                 # pre-tax numbers do not move
        assert row[c] == pytest.approx(plain.table.loc["dogs", c], rel=1e-12)


def test_dividend_screen_after_tax_cagr_taxable_vs_tfsa(syn):
    taxable = syn.backtest(["dogs"], tax=TaxConfig(account="taxable")).table.loc["dogs"]
    assert taxable["AfterTaxCAGR"] < taxable["CAGR"] and taxable["TaxDrag/yr"] > 0
    tfsa = syn.backtest(["dogs"], tax=TaxConfig(account="tfsa")).table.loc["dogs"]
    assert tfsa["AfterTaxCAGR"] == pytest.approx(tfsa["CAGR"], abs=1e-12) and tfsa["TaxDrag/yr"] == pytest.approx(0, abs=1e-12)


def test_exit_policy_switches_are_taxable_events(syn):
    name = syn.market.indices.columns[0]
    pol = ExitPolicy(ExitScreen([TrendBreak(name, window_months=4)], threshold=0.5, check="ME", lag=1))
    plain = syn.backtest(["dogs"], rebalance_months=12, tax=TaxConfig(model=MODEL), exit=None)
    withx = syn.backtest(["dogs"], rebalance_months=12, tax=TaxConfig(model=MODEL), exit=pol)
    r = withx.results["dogs"]
    assert r.switches > 0 and r.tax["realized_gains"].sum() + r.tax["realized_losses"].sum() != \
        plain.results["dogs"].tax["realized_gains"].sum() + plain.results["dogs"].tax["realized_losses"].sum()
    assert np.isfinite(r.after_tax_curve).all() and (r.tax["wealth_after_tax"] > 0).all()


# --------------------------------------------------------------------------- CanadianTax (tax_ca) joint path
SAL = 90_000.0


def ca(prov="AB", **kw):
    return CanadianTax(province=prov, year=2025, employment_income=SAL, **kw)


def test_registry_and_validation():
    assert TAX_MODELS == {"none": NoTax, "placeholder": PlaceholderCanadianTax, "canadian": CanadianTax}
    assert isinstance(ca(), TaxModel) and hasattr(ca(), "assess_year")
    with pytest.raises(ValueError):
        CanadianTax(province="ON")
    with pytest.raises(ValueError):
        CanadianTax(eligible_share=1.5)
    m = from_dict(to_dict(ca("BC", eligible_share=0.5)))
    assert m == ca("BC", eligible_share=0.5) and m.describe()["province"] == "BC"


def test_owner_numbers_dividend_only_ab():
    """Dividend-only portfolio (A held all of 2020, eligible, no sale in 2020), taxable AB account, salary 90,000.
    expected = owner_ab_2025.combined_tax(90000, D, "eligible")["tax_on_dividend_marginal"], D = the ledger's 2020 dividends"""
    t = go(tx(model=ca())).tax
    d = float(t.loc[2020, "dividends_ca"])
    assert d == pytest.approx(CAP * DIV_A_2020, rel=1e-12)
    expected = owner.combined_tax(SAL, d, "eligible")["tax_on_dividend_marginal"]
    assert expected > 0
    assert t.loc[2020, "tax_dividends"] == pytest.approx(expected, abs=1e-6)
    assert t.loc[2020, "tax_paid"] == pytest.approx(expected, abs=1e-6)
    assert t.loc[2020, "tax_capital_gains"] == 0 and t.loc[2020, "tax_foreign_dividends"] == 0


def test_owner_numbers_dividends_plus_one_gain_ab():
    """2021: dividends d (A one day, B the rest, eligible) plus the sale gain G of A, stacked salary -> dividends -> gains.
    expected = combined_tax(90000, d)["tax_on_dividend_marginal"] + capital_gains_tax(G, 90000 + 1.38 d)"""
    t = go(tx(model=ca())).tax
    d, g = float(t.loc[2021, "dividends_ca"]), float(t.loc[2021, "realized_gains"])
    assert g > 0
    div = owner.combined_tax(SAL, d, "eligible")["tax_on_dividend_marginal"]
    cg = owner.capital_gains_tax(g, SAL + 1.38 * d)
    assert t.loc[2021, "tax_dividends"] == pytest.approx(div, abs=1e-6)
    assert t.loc[2021, "tax_capital_gains"] == pytest.approx(cg, abs=1e-6)
    assert t.loc[2021, "tax_paid"] == pytest.approx(div + cg, abs=1e-6)


def test_bc_matches_compute_stacking():
    t = go(tx(model=ca("BC"))).tax
    d, g = float(t.loc[2021, "dividends_ca"]), float(t.loc[2021, "realized_gains"])
    base = compute("BC", 2025, employment_income=SAL).total
    with_div = compute("BC", 2025, employment_income=SAL, eligible_dividends=d).total
    both = compute("BC", 2025, employment_income=SAL, eligible_dividends=d, capital_gains=g).total
    assert t.loc[2021, "tax_dividends"] == pytest.approx(with_div - base, abs=1e-6)
    assert t.loc[2021, "tax_capital_gains"] == pytest.approx(both - with_div, abs=1e-6)
    d20 = float(t.loc[2020, "dividends_ca"])
    assert t.loc[2020, "tax_dividends"] == pytest.approx(
        compute("BC", 2025, employment_income=SAL, eligible_dividends=d20).total - base, abs=1e-6)


def test_eligible_share_splits_dividends():
    t = go(tx(model=ca(eligible_share=0.25))).tax
    d = float(t.loc[2020, "dividends_ca"])
    base = compute("AB", 2025, employment_income=SAL).total
    want = compute("AB", 2025, employment_income=SAL, eligible_dividends=0.25 * d, non_eligible_dividends=0.75 * d).total - base
    assert t.loc[2020, "tax_dividends"] == pytest.approx(want, abs=1e-6)


def test_years_beyond_tables_warn_and_use_latest():
    t = go(tx(model=ca())).tax                            # simulated 2020-2021, tables: 2025
    w = t.attrs["warnings"]
    assert any("2020" in x for x in w) and any("2021" in x for x in w)
    y = ca().assess_year(2025, eligible_dividends=1000.0)
    assert y.breakdown["warnings"] == [] and isinstance(y, YearTax)


def test_foreign_ftc_and_accounts():
    m = ca()
    wh = 150.0
    y = m.assess_year(2025, foreign_income=1000.0, foreign_withholding=wh, account="taxable")
    r = compute("AB", 2025, employment_income=SAL, foreign_income=1000.0, foreign_tax_paid=wh)
    base = compute("AB", 2025, employment_income=SAL).total
    assert y.total == pytest.approx(r.total - base + wh, abs=1e-9)        # Canadian tax net of FTC + withholding
    assert y.breakdown["foreign_tax_credit"] == pytest.approx(wh)         # marginal rate on 1000 well above 15%
    assert m.assess_year(2025, foreign_income=1000.0, foreign_withholding=wh, account="tfsa").total == wh
    assert m.assess_year(2025, foreign_income=1000.0, foreign_withholding=wh, account="rrsp").total == 0.0
    assert m.assess_year(2025, eligible_dividends=5e3, capital_gains_net=5e3, account="tfsa").total == 0.0
    # end to end: TFSA / RRSP through assess
    inc20 = CAP * DIV_A_2020
    assert go(tx("tfsa", model=m, foreign=("A",))).tax.loc[2020, "tax_foreign_dividends"] == pytest.approx(inc20 * 0.15, rel=1e-12)
    assert go(tx("rrsp", model=m, foreign=("A",))).tax.loc[2020, "tax_foreign_dividends"] == 0
    res = go(tx("tfsa", model=m))
    assert (res.tax["tax_paid"] == 0).all()


def test_loss_carry_forward_with_joint_path():
    res = go(tx(model=ca()), a_after=0.6)
    t = res.tax
    assert t.loc[2021, "realized_losses"] < 0 and t.loc[2021, "tax_capital_gains"] == 0
    assert t.loc[2021, "loss_carry_out"] == pytest.approx(-t.loc[2021, "realized_losses"], rel=1e-12)
    # direct: year 1 loss, year 2 gain larger -> only the excess is taxed, through compute
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    curve = pd.Series(1.0, index=idx)
    ev = pd.DataFrame(0.0, index=idx, columns=["div_ca", "div_fx", "gain", "loss"])
    ev.loc["2020-06-01", "loss"] = -0.02
    ev.loc["2021-06-01", "gain"] = 0.05
    cfg = TaxConfig(model=ca(), capital=CAP, pay_from_portfolio=False)
    tab, _ = assess(curve, ev, cfg, CAP)
    excess = CAP * (0.05 - 0.02)
    want = compute("AB", 2025, employment_income=SAL, capital_gains=excess).total - compute("AB", 2025, employment_income=SAL).total
    assert tab.loc[2020, "loss_carry_out"] == pytest.approx(CAP * 0.02) and tab.loc[2021, "tax_capital_gains"] == pytest.approx(want, abs=1e-6)
    assert tab.loc[2021, "loss_carry_out"] == 0


def test_taxconfig_with_canadian_round_trips_and_columns_unchanged():
    cfg = tx(model=ca("BC"))
    assert from_dict(to_dict(cfg)) == cfg
    t = go(cfg).tax
    from screenlab.taxes import TAX_COLS
    assert list(t.columns) == TAX_COLS


# ----------------------------------------------------------------------------- P3: foreign holdings
from screenlab.data import security_meta                                        # noqa: E402
from screenlab.dataset import Dataset                                           # noqa: E402
from screenlab.macro import MacroSeries                                         # noqa: E402


def meta_market(names, a_after=1.0, fund_rows=None, ccy="USD"):
    """3 names picked together on 2020-01-01 (equal weight, prices flat), yield 4% each, one year (2020)."""
    dates = pd.bdate_range("2020-01-01", "2020-12-31")
    prices = pd.DataFrame({t: 1.0 for t in names}, index=dates)
    fund = pd.DataFrame([dict(date=pd.Timestamp("2020-01-01"), ticker=t, sector="in", price=10.0, market_cap=1000.0,
                              dividend=0.4) for t in names])
    meta = security_meta(names, pd.DataFrame(fund_rows).set_index("ticker"))
    return Market(Dataset.from_frames(add_ratios(fund), prices, security_meta=meta))


def go3(account, rows, withholding=None, names=("S", "U", "E")):
    cfg = Config(rebalance_months=12, cost_bps=0, min_holdings=1,
                 tax=tx(account, **({"withholding": withholding} if withholding else {})))
    screen = ScreenBuilder("all3", n=3).where("sector == 'in'").build()
    return run(screen, meta_market(names, fund_rows=rows), cfg, start="2020-01-01").tax.loc[2020]


ROWS = [dict(ticker="S", domicile="US", wrapper="stock"),                       # US stock
        dict(ticker="U", domicile="US", wrapper="etf_us"),                      # US-listed ETF
        dict(ticker="E", domicile="CA", wrapper="etf_ca", underlying_domicile="US", currency="CAD")]   # Canadian ETF on US stocks


def test_withholding_matrix_by_account_and_wrapper():  # P3
    inc = CAP / 3 * 0.04 * 365 / D                                              # each name's gross income, $
    t = go3("taxable", ROWS)
    assert t["dividends_foreign"] == pytest.approx(3 * inc, rel=1e-12)
    # direct: max(15% withholding, 40% marginal) = 0.40 (withholding creditable); fund: 15% lost + 40% of the other 85%
    assert t["tax_foreign_dividends"] == pytest.approx(inc * (0.40 + 0.40 + (0.15 + 0.85 * 0.40)), rel=1e-12)
    t = go3("tfsa", ROWS)
    assert t["tax_foreign_dividends"] == pytest.approx(inc * 0.45, rel=1e-12)   # all three lost, 15% each
    t = go3("rrsp", ROWS)
    assert t["tax_foreign_dividends"] == pytest.approx(inc * 0.15, rel=1e-12)   # US stock and US ETF exempt; Canadian ETF loses
    assert t["tax_capital_gains"] == 0


def test_withholding_table_by_country_and_rrsp_exemption_is_us_only():  # P3
    rows = [dict(ticker="S", domicile="US"), dict(ticker="G", domicile="GB", currency="GBP"), dict(ticker="E", domicile="CA")]
    inc = CAP / 3 * 0.04 * 365 / D
    wh = {"GB": 0.10, "default": 0.25}
    assert go3("tfsa", rows, wh, names=("S", "G", "E"))["tax_foreign_dividends"] == pytest.approx(inc * (0.15 + 0.10), rel=1e-12)
    # RRSP: the treaty exemption is US only, GB withholding is lost; a country not in the table uses `default`
    assert go3("rrsp", rows, wh, names=("S", "G", "E"))["tax_foreign_dividends"] == pytest.approx(inc * 0.10, rel=1e-12)
    assert TaxConfig(withholding={"GB": 0.1}).rate_for("FR") == 0.15 and TaxConfig(withholding={"GB": 0.1}).rate_for("GB") == 0.1


def test_us_withholding_alias_warns_and_maps():  # P3
    with pytest.warns(DeprecationWarning, match="us_withholding"):
        cfg = TaxConfig(us_withholding=0.30)
    assert cfg.withholding == {"US": 0.30, "default": 0.15} and cfg.us_withholding is None
    assert TaxConfig().withholding == {"US": 0.15, "default": 0.15}
    assert from_dict(to_dict(cfg)).withholding == cfg.withholding
    with pytest.raises(ValueError):
        TaxConfig(withholding={"US": 1.0})


def fx_market(a_after, fx_after):
    """The two-name market of the module, A and B are USD names (domicile US), dividends 4% / 2%."""
    m = market(a_after)
    meta = security_meta(["A", "B"], domicile="US", currency="USD")
    ds = Dataset.from_frames(m.dataset.fundamentals, m.dataset.prices, security_meta=meta)
    dates = ds.prices.index
    fx = pd.DataFrame({"fx": np.where(dates < "2021-01-01", 1.40, 1.40 * fx_after)}, index=dates)
    return Market(ds), MacroSeries.from_frame(fx)


def test_gain_on_cad_basis_usd_gain_cad_loss():  # P3
    m, macro = fx_market(1.10, 0.85)
    cfg = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx(macro=macro, pay_from_portfolio=False))
    t = run(SCREEN, m, cfg, start="2020-01-01").tax
    # A: bought 2020-01-01 at fx 1.40 (factor 1), sold 2021-01-01 at 1.19 (factor 0.85); local basis includes the
    # reinvested dividends (taxed on a CAD basis at the payment date, factor 1 through 2020 and 0.85 on 2021-01-01)
    basis = 1 + 0.04 * 365 / D + 0.04 * 1 / D * 0.85
    proceeds = 1.10 * 0.85
    assert t.loc[2021, "realized_losses"] == pytest.approx(CAP * (proceeds - basis), rel=1e-9)
    assert t.loc[2021, "realized_losses"] < 0 and t.loc[2021, "realized_gains"] == 0
    # dividends of 2021 are converted at the payment-date FX
    div21 = 0.04 * 1 / D + 0.02 * 1.10 * 364 / D
    assert t.loc[2021, "dividends_foreign"] == pytest.approx(CAP * div21 * 0.85, rel=1e-9)
    assert FX_WARN not in t.attrs["warnings"]
    # the USD gain without an FX series: taxed in the portfolio currency, with the warning
    t0 = run(SCREEN, m, Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx(pay_from_portfolio=False)), start="2020-01-01").tax
    assert t0.loc[2021, "realized_gains"] == pytest.approx(CAP * (1.10 - (1 + 0.04 * 366 / D)), rel=1e-9)
    assert FX_WARN in t0.attrs["warnings"]


def test_gain_on_cad_basis_usd_loss_cad_gain():  # P3
    m, macro = fx_market(0.95, 1.15)
    t = run(SCREEN, m, Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx(macro=macro, pay_from_portfolio=False)), start="2020-01-01").tax
    basis = 1 + 0.04 * 365 / D + 0.04 * 1 / D * 1.15
    assert t.loc[2021, "realized_gains"] == pytest.approx(CAP * (0.95 * 1.15 - basis), rel=1e-9)


FX_WARN = "foreign gains taxed in portfolio currency; load an FX series for CAD basis"


def test_pure_gain_cad_loss_exact():  # P3
    """No dividends: a USD name +10% while USDCAD falls 15% is a CAD loss of exactly 1.10 x 0.85 - 1 = -6.5% of capital."""
    m, macro = fx_market(1.10, 0.85)
    fund = m.dataset.fundamentals.assign(dividend=0.0)
    ds = Dataset.from_frames(add_ratios(fund.drop(columns=[c for c in fund.columns if c in ("div_yield",)])),
                             m.dataset.prices, security_meta=m.dataset.security_meta)
    t = run(SCREEN, Market(ds), Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx(macro=macro, pay_from_portfolio=False)),
            start="2020-01-01").tax
    assert t.loc[2021, "realized_losses"] == pytest.approx(-0.065 * CAP, rel=1e-12)
    assert t.loc[2021, "realized_gains"] == 0 and t.loc[2021, "tax_capital_gains"] == 0


# =========================================================================== P1: RRSP deferral, taxed interest, ACB, ROC
# Exact arithmetic on the hand-built two-name market above (MODEL: marginal 0.40, eligible dividends 0.207, gains 0.20).
# Pre-tax end wealth C = res.curve.iloc[-1]; with a tfsa/rrsp account nothing is paid before the end, so f = 1.
RATE = 0.30


def test_rrsp_deferred_is_taxed_at_withdrawal_on_the_last_bar():
    res = go(tx("rrsp", rrsp_withdrawal_rate=RATE))
    C = float(res.curve.iloc[-1])
    assert C == pytest.approx(1.5)
    a, t = res.after_tax_curve, res.tax
    pd.testing.assert_series_equal(a.iloc[:-1], res.curve.iloc[:-1], check_names=False)       # untouched until the end
    assert a.iloc[-1] == pytest.approx(C * (1 - RATE), rel=1e-12)                              # 1.5 x 0.7 = 1.05
    assert t.loc[2020, "tax_rrsp_withdrawal"] == 0 and t.loc[2020, "tax_paid"] == 0
    assert t.loc[2021, "tax_rrsp_withdrawal"] == pytest.approx(CAP * C * RATE, rel=1e-12)      # 45,000
    assert t.loc[2021, "tax_paid"] == pytest.approx(CAP * C * RATE, rel=1e-12)
    assert t.loc[2021, "wealth_after_tax"] == pytest.approx(CAP * C * (1 - RATE), rel=1e-12)
    assert t.loc[2021, "cum_drag"] == pytest.approx(RATE, rel=1e-12)
    assert t.attrs["convention"] == "RRSP: deferred, taxed at withdrawal at 30.0% (assumed)" and not t.attrs["warnings"]


def test_rrsp_withdrawal_outside_paid_and_tfsa_unchanged():
    res = go(tx("rrsp", rrsp_withdrawal_rate=RATE, pay_from_portfolio=False))
    C = float(res.curve.iloc[-1])
    assert res.after_tax_curve.iloc[-1] == pytest.approx(C - RATE * C, rel=1e-12)
    tf = go(tx("tfsa", rrsp_withdrawal_rate=RATE))                                             # the rate is ignored off-RRSP
    pd.testing.assert_series_equal(tf.after_tax_curve, tf.curve, check_names=False)
    assert tf.tax.attrs["convention"] is None and (tf.tax["tax_rrsp_withdrawal"] == 0).all()


def test_rrsp_without_a_rate_keeps_untaxed_behaviour_with_a_warning():
    res = go(tx("rrsp"))
    pd.testing.assert_series_equal(res.after_tax_curve, res.curve, check_names=False)
    assert "RRSP withdrawals untaxed in this run" in res.tax.attrs["warnings"]
    assert res.tax.attrs["convention"] == "RRSP withdrawals untaxed in this run"


def test_rrsp_rate_from_retirement_income_and_validation():
    from screenlab.tax_ca import marginal_rates
    t = tx("rrsp", model=ca("AB"), rrsp_withdrawal_income=60_000.0)
    want = marginal_rates("AB", 2025, 60_000.0)["ordinary"]
    assert t.rrsp_rate() == pytest.approx(want, rel=1e-12) and 0.2 < want < 0.4
    assert "retirement income 60,000" in t.rrsp_convention()
    C = float(go(t).curve.iloc[-1])
    assert go(t).after_tax_curve.iloc[-1] == pytest.approx(C * (1 - want), rel=1e-9)
    with pytest.raises(ValueError, match="not both"):
        tx("rrsp", rrsp_withdrawal_rate=0.3, rrsp_withdrawal_income=50_000.0)
    with pytest.raises(ValueError, match="rrsp_withdrawal_income needs"):
        tx("rrsp", rrsp_withdrawal_income=50_000.0)                                           # placeholder model: Ontario
    with pytest.raises(ValueError, match="rrsp_withdrawal_rate"):
        tx("rrsp", rrsp_withdrawal_rate=1.2)
    cfg = tx("rrsp", model=ca("BC"), rrsp_withdrawal_income=60_000.0)
    assert from_dict(to_dict(cfg)) == cfg


def test_rrsp_passive_index_is_deferred_too(syn=None):
    from screenlab.taxes import tax_buy_and_hold
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    curve = pd.Series(np.linspace(1.0, 2.0, len(idx)), index=idx)
    tab, a = tax_buy_and_hold(curve, 0.0, tx("rrsp", rrsp_withdrawal_rate=RATE), CAP)
    assert a.iloc[-1] == pytest.approx(2.0 * (1 - RATE), rel=1e-12) and a.iloc[-2] == pytest.approx(curve.iloc[-2])
    assert tab.attrs["convention"].startswith("RRSP: deferred")


def test_rrsp_report_shows_convention_and_after_tax_cagr_drops():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8)
    plain = lab.backtest(["dogs"], tax=TaxConfig(account="rrsp"))
    deferred = lab.backtest(["dogs"], tax=TaxConfig(account="rrsp", rrsp_withdrawal_rate=0.3))
    assert "RRSP withdrawals untaxed in this run" in str(plain)
    assert "RRSP: deferred, taxed at withdrawal at 30.0% (assumed)" in str(deferred)
    for lbl in ("dogs", deferred.benchmark_label):
        p, d = plain.table.loc[lbl], deferred.table.loc[lbl]
        assert p["AfterTaxCAGR"] == pytest.approx(p["CAGR"], abs=1e-12)
        assert d["AfterTaxCAGR"] < d["CAGR"] and d["TaxDrag/yr"] > 0.0


# ---- interest: ordinary income in a taxable account. All-cash screen: nothing is picked, value sits in cash.
CASH_R = 0.04
NOTHING = ScreenBuilder("nothing", n=1).where("sector == 'zzz'").build()


def go_cash(tax, **cfg):
    c = Config(rebalance_months=12, cost_bps=0, min_holdings=1, cash_rate=CASH_R, tax=tax, **cfg)
    return run(NOTHING, market(), c, start="2020-01-01")


def test_cash_interest_is_taxed_as_ordinary_income():
    g = 1 + CASH_R
    res = go_cash(tx())
    t = res.tax
    i20 = CAP * (g ** (365 / D) - 1)                              # 2020-01-01 -> 2020-12-31, 365 days
    assert t.loc[2020, "interest_income"] == pytest.approx(i20, rel=1e-12)
    assert t.loc[2020, "tax_interest"] == pytest.approx(0.40 * i20, rel=1e-12)                # marginal 40%
    f1 = 1 - 0.40 * i20 / (CAP * g ** (365 / D))                                               # paid from the portfolio
    i21 = CAP * f1 * (g ** (730 / D) - g ** (365 / D))
    assert t.loc[2021, "interest_income"] == pytest.approx(i21, rel=1e-12)
    assert t.loc[2021, "tax_interest"] == pytest.approx(0.40 * i21, rel=1e-12)
    assert t.loc[2021, "tax_paid"] == pytest.approx(0.40 * i21, rel=1e-12)
    assert (t["dividends_ca"] == 0).all() and (t["realized_gains"] == 0).all()
    assert res.after_tax_curve.iloc[-1] < res.curve.iloc[-1]
    for acct in ("tfsa", "rrsp"):                                                              # sheltered: income recorded, not taxed
        s = go_cash(tx(acct)).tax
        assert s.loc[2020, "interest_income"] == pytest.approx(i20, rel=1e-12) and (s["tax_paid"] == 0).all()


def test_canadian_tax_stacks_interest_on_the_salary():
    t = go_cash(tx(model=ca("AB"))).tax                        # ca(): AB 2025, salary SAL
    i20 = float(t.loc[2020, "interest_income"])
    want = compute("AB", 2025, employment_income=SAL + i20).total - compute("AB", 2025, employment_income=SAL).total
    assert t.loc[2020, "tax_interest"] == pytest.approx(want, rel=1e-9) and want > 0.2 * i20


def test_unallocated_sleeve_cash_earns_taxed_interest():
    # one pick capped at weight 0.5: half in A (flat), half in cash. Cash interest of 2020 = 0.5 x CAP x ((1+r)^(365/D) - 1).
    c = Config(rebalance_months=12, cost_bps=0, min_holdings=1, max_weight=0.5, cash_rate=CASH_R, tax=tx("tfsa"))
    res = run(SCREEN, market(), c, start="2020-01-01")
    assert res.tax.loc[2020, "interest_income"] == pytest.approx(0.5 * CAP * ((1 + CASH_R) ** (365 / D) - 1), rel=1e-12)


def test_custom_model_without_interest_support_warns():
    t = go_cash(tx(model=FlatTax())).tax if hasattr(FlatTax, "tax_on_interest") is False else None
    if t is not None:
        assert any("interest untaxed" in w for w in t.attrs["warnings"]) and (t["tax_interest"] == 0).all()


# ---- parked cash of an exit policy
def market_with_index(drop="2020-05-01"):
    m = market()
    dates = m.prices.index
    idx = pd.DataFrame({"IDX": np.where(dates < drop, 100.0, 60.0)}, index=dates)
    return Market(m.fundamentals if hasattr(m, "fundamentals") else add_ratios(pd.DataFrame()), m.prices, idx) if False else \
        Market(add_ratios(_FUND), m.prices, idx)


_FUND = pd.DataFrame([dict(date=pd.Timestamp(d), ticker=t, sector=s, price=10.0, market_cap=1000.0, dividend=v)
                      for d, t, s, v in (("2020-01-01", "A", "in", 0.4), ("2020-01-01", "B", "out", 0.2),
                                         ("2021-01-01", "A", "out", 0.4), ("2021-01-01", "B", "in", 0.2))])


def go_park(tax, kind="interest", park=0.04):
    pol = ExitPolicy(ExitScreen([TrendBreak("IDX", window_months=2)], threshold=0.5, check="ME", lag=1), park=park,
                     park_income_kind=kind)
    cfg = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tax, exit=pol)
    return run(SCREEN, market_with_index(), cfg, start="2020-01-01")


def test_parked_cash_interest_is_ordinary_income_and_joins_the_park_basis():
    res = go_park(tx("tfsa"))
    e, V, pr = res.exposure, res.curve, res.park_curve.pct_change().fillna(0.0)
    assert res.switches > 0 and (e < 1).any()
    want = float((V.shift(1) * (1 - e.shift(1)) * pr).iloc[1:].sum())
    t = res.tax
    assert want > 0 and t["interest_income"].sum() == pytest.approx(CAP * want, rel=1e-9)
    # interest is reinvested in the park: the gain on selling the park back excludes it (no phantom gain on plain interest)
    flat = go_park(tx("tfsa"), park=0.0)
    assert flat.tax["interest_income"].sum() == 0
    assert t["realized_gains"].sum() + t["realized_losses"].sum() == pytest.approx(
        flat.tax["realized_gains"].sum() + flat.tax["realized_losses"].sum() + 0.0, abs=CAP * 0.05)


@pytest.mark.parametrize("kind,col,rate", [("interest", "tax_interest", 0.40), ("dividend_eligible", "tax_dividends", DIV_RATE)])
def test_park_income_kind_selects_the_tax_treatment(kind, col, rate):
    base = go_park(tx("tfsa"), kind="interest").tax              # sheltered: the dollar amounts do not depend on tax paid
    other = go_park(tx("tfsa"), kind=kind).tax
    inc = float(base["interest_income"].sum())
    assert inc > 0
    if kind == "interest":
        assert go_park(tx(), kind="interest").tax["tax_interest"].sum() > 0.39 * inc * 0.9
    else:
        assert other["interest_income"].sum() == 0
        assert other["dividends_ca"].sum() - base["dividends_ca"].sum() == pytest.approx(inc, rel=1e-12)
        taxed = go_park(tx(), kind=kind).tax
        assert taxed["tax_interest"].sum() == 0 and taxed["tax_dividends"].sum() > go_park(tx(), kind="interest").tax["tax_dividends"].sum()
    with pytest.raises(ValueError, match="park_income_kind"):
        ExitPolicy(ExitScreen([TrendBreak("IDX")]), park_income_kind="bogus")
    with pytest.raises(ValueError, match="park_income_kind"):
        tx(park_income_kind="bogus")


# ---- trading costs in the adjusted cost base
def test_trading_costs_are_in_the_cost_base_and_out_of_the_proceeds():
    bps = 100.0
    c = Config(rebalance_months=12, cost_bps=bps, min_holdings=1, tax=tx("tfsa"))
    res = run(SCREEN, market(), c, start="2020-01-01")
    free = go(tx("tfsa"))
    k = bps / 1e4
    v0 = 1 - k                                           # wealth after the first buy (1.0 bought, cost 1%)
    basis = (1 + k) + v0 * 0.04 * 366 / D                # buy cost into the ACB; dividends reinvested on v0
    value21 = v0 * 1.5                                   # wealth when A is sold (A's TRI 1.5)
    gain = value21 - k * value21 - basis                 # sell cost (1% of the value) out of the proceeds
    assert res.tax.loc[2021, "realized_gains"] == pytest.approx(CAP * gain, rel=1e-12)
    no_cost_gain = value21 - (1 + v0 * 0.04 * 366 / D)
    assert gain < no_cost_gain                            # the costs shrink the realized gain ...
    assert no_cost_gain - gain == pytest.approx(k * 1.0 + k * value21, rel=1e-12)    # ... by buy cost + sell cost
    assert free.tax.loc[2021, "realized_gains"] == pytest.approx(CAP * (1.5 - BASIS_A), rel=1e-12)   # free run unchanged


def test_trading_costs_lower_the_taxable_gain_exactly():
    c = Config(rebalance_months=12, cost_bps=100.0, min_holdings=1, tax=tx())
    t = run(SCREEN, market(), c, start="2020-01-01").tax
    assert t.loc[2021, "tax_capital_gains"] == pytest.approx(CG_RATE * t.loc[2021, "realized_gains"], rel=1e-12)


# ---- return of capital
def market_roc(roc):
    f = _FUND.copy()
    f["roc"] = [roc, 0.0, roc, 0.0]
    return Market(add_ratios(f), market().prices)


def test_return_of_capital_reduces_acb_and_is_not_income():
    q = 0.5
    c = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx("tfsa"))
    res = run(SCREEN, market_roc(q), c, start="2020-01-01")
    t, base = res.tax, go(tx("tfsa")).tax
    assert t.loc[2020, "dividends_ca"] == pytest.approx(CAP * DIV_A_2020 * (1 - q), rel=1e-12)      # only the income part
    acb = 1 + 0.04 * 366 / D * (1 - q)                    # the whole distribution is reinvested, the ROC part lowered the ACB
    assert t.loc[2021, "realized_gains"] == pytest.approx(CAP * (1.5 - acb), rel=1e-12)
    assert t.loc[2021, "realized_gains"] - base.loc[2021, "realized_gains"] == pytest.approx(CAP * 0.04 * 366 / D * q, abs=1e-6)
    # taxable: less dividend tax now, more gains tax later
    tt = run(SCREEN, market_roc(q), Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tx(pay_from_portfolio=False)), start="2020-01-01").tax
    assert tt.loc[2020, "tax_dividends"] == pytest.approx(DIV_RATE * CAP * DIV_A_2020 * (1 - q), rel=1e-12)


def test_roc_beyond_the_acb_is_a_capital_gain():
    from screenlab.pipeline import TaxStage
    b, gain = TaxStage.roc_step(0.01, 0.04, 0.03)         # ACB 0.01, distribution 0.04 of which 0.03 is ROC
    assert gain == pytest.approx(0.02) and b == pytest.approx(0.04)
    b, gain = TaxStage.roc_step(1.0, 0.04, 0.04)          # plenty of ACB: no gain, ACB grows by the income part (0)
    assert gain == 0.0 and b == pytest.approx(1.0)
