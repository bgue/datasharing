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
from screenlab.taxes import (NoTax, PlaceholderCanadianTax, TaxConfig, TaxModel, assess, register_tax_model)

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
    wh20 = go(tx("tfsa", foreign=("A",), us_withholding=0.30)).tax
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
    t = TaxConfig(model=MODEL, account="tfsa", foreign=("A", "B"), us_withholding=0.1, capital=5_000.0)
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
