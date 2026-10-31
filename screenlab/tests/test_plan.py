"""Plan module (plan.py): schedule, units ledger over `simulate(flows=)`, index leg, XIRR and the metric table.

The hand-built market: names A, B, C on business days 2020-01-01 .. 2021-12-31, all picked on 2020-01-01 (equal weight 1/3),
12-month rebalance, `cost_bps=100` (1% of every amount bought or sold), no dividends. `prices(step)` holds every price at 1
and multiplies it by `STEP` from `step_date` on. Dollars, exact arithmetic (rel 1e-12).

    python -m pytest screenlab/tests/test_plan.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import Config, Lab, Market, ScreenBuilder, TaxConfig
from screenlab.backtest import simulate
from screenlab.data import add_ratios
from screenlab.metrics import METRICS as REGISTRY
from screenlab.pipeline import CashAccrual
from screenlab.plan import METRICS, CashFlow, Phase, PlanSpec, capm_stats, metrics_table, run_plan, schedule, xirr
from screenlab.serial import from_dict, to_dict
from screenlab.taxes import PlaceholderCanadianTax, index_ledger, tax_buy_and_hold_flows

DATES = pd.bdate_range("2020-01-01", "2021-12-31")
MODEL = PlaceholderCanadianTax(marginal_rate=0.40, federal_dtc=0.15, provincial_dtc=0.10, provincial_dtc_noneligible=0.03)
CG = 0.20                                                    # 0.5 x 0.40


def make_lab(step_date=None, step=2.0, **cfg):
    px = pd.DataFrame({t: np.where(DATES >= (step_date or "2100-01-01"), step, 1.0) for t in "ABC"}, index=DATES)
    fund = pd.DataFrame([dict(date=pd.Timestamp("2020-01-01"), ticker=t, sector="in", price=10.0, market_cap=1000.0, dividend=0.0)
                         for t in "ABC"])
    config = Config(**{"rebalance_months": 12, "cost_bps": 100, "min_holdings": 1, **cfg})
    lab = Lab(Market(add_ratios(fund), px), config=config)
    lab.define(ScreenBuilder("all3", n=3).where("sector == 'in'").build())
    return lab


def spec(**kw):
    return PlanSpec("all3", "2020-01-01", index_mer=0.0, **kw)


# ------------------------------------------------------------------------------------------------ XIRR
def test_xirr_known_cases():
    assert xirr(["2021-01-01", "2022-01-01"], [-100, 110]) == pytest.approx(0.10, abs=1e-12)          # 365 days
    assert xirr(["2021-01-01", "2023-01-01"], [-100, 121]) == pytest.approx(0.10, abs=1e-12)          # 730 days
    assert xirr(["2021-01-01", "2022-01-01"], [-100, 80]) == pytest.approx(-0.20, abs=1e-12)
    # two contributions: -100 at 0, -100 after one year, +231 after two: 100 (1+r)^2 + 100 (1+r) = 231 -> r = 10%
    assert xirr(["2021-01-01", "2022-01-01", "2023-01-01"], [-100, -100, 231]) == pytest.approx(0.10, abs=1e-12)
    # a withdrawal in the middle: -100, +55 after a year, +60.5 after two: 100 = 55/(1+r) + 60.5/(1+r)^2 -> r = 10%
    assert xirr(["2021-01-01", "2022-01-01", "2023-01-01"], [-100, 55, 60.5]) == pytest.approx(0.10, abs=1e-12)
    assert xirr(["2021-01-01", "2022-01-01"], [-100, -100]) == -1.0                                 # nothing comes back
    assert np.isnan(xirr(["2021-01-01", "2022-01-01"], [100, 100]))
    assert xirr(["2021-01-01", "2022-01-01"], [-100, 0]) == -1.0


def test_capm_stats_alpha_beta_exact():
    rng = np.random.default_rng(1)
    ri = rng.normal(0.008, 0.04, 60)
    rf = np.full(60, 0.003)
    rs = rf + 0.002 + 1.5 * (ri - rf)                          # alpha 0.2%/month, beta 1.5, no noise
    s = capm_stats(rs, ri, rf)
    assert s["Beta"] == pytest.approx(1.5, abs=1e-10)
    assert s["Alpha"] == pytest.approx(0.002 * 12, abs=1e-10)
    assert s["Sharpe"] == pytest.approx((rs - rf).mean() / (rs - rf).std(ddof=1) * np.sqrt(12), rel=1e-12)
    assert s["Vol"] == pytest.approx(rs.std(ddof=1) * np.sqrt(12), rel=1e-12)
    d = rs - ri
    assert s["TrackingError"] == pytest.approx(d.std(ddof=1) * np.sqrt(12), rel=1e-12)
    assert s["InfoRatio"] == pytest.approx(d.mean() * 12 / (d.std(ddof=1) * np.sqrt(12)), rel=1e-12)
    b = capm_stats(ri, ri, rf, benchmark=True)
    assert (b["Alpha"], b["Beta"], b["TrackingError"]) == (0.0, 1.0, 0.0)


# ------------------------------------------------------------------------------------------------ schedule
IDX = pd.bdate_range("2019-12-02", "2031-12-31")


def test_schedule_monthly_next_trading_day_and_merge():
    s = schedule(PlanSpec("x", "2020-02-01", initial=1000, periodic=100, end="2020-05-15", index_mer=0.0), IDX, None)
    assert list(s["kind"]) == ["initial", "periodic", "periodic", "periodic", "periodic"]
    assert list(s["date"]) == list(pd.to_datetime(["2020-02-01"] * 2 + ["2020-03-01", "2020-04-01", "2020-05-01"]))
    # 2020-02-01 is a Saturday, 2020-03-01 a Sunday: next trading day
    assert list(s["executed"]) == list(pd.to_datetime(["2020-02-03"] * 2 + ["2020-03-02", "2020-04-01", "2020-05-01"]))
    assert list(s["amount"]) == [1000, 100, 100, 100, 100]


def test_schedule_frequencies_and_month_end_clamp():
    bi = schedule(PlanSpec("x", "2020-01-06", periodic=50, frequency="biweekly", end="2020-03-01", index_mer=0.0), IDX, None)
    assert list(bi["date"]) == list(pd.date_range("2020-01-06", periods=4, freq="14D"))
    q = schedule(PlanSpec("x", "2020-01-31", periodic=1, frequency="quarterly", end="2020-12-31", index_mer=0.0), IDX, None)
    assert list(q["date"]) == list(pd.to_datetime(["2020-01-31", "2020-04-30", "2020-07-31", "2020-10-31"]))
    a = schedule(PlanSpec("x", "2020-01-02", periodic=1, frequency="annual", end="2023-01-02", index_mer=0.0), IDX, None)
    assert list(a["date"]) == list(pd.to_datetime(["2020-01-02", "2021-01-02", "2022-01-02", "2023-01-02"]))


def test_schedule_growth_each_anniversary():
    s = schedule(PlanSpec("x", "2020-01-02", periodic=100, growth=0.03, end="2022-02-15", index_mer=0.0), IDX, None)
    amt = s.set_index("date")["amount"]
    assert amt["2020-12-02"] == pytest.approx(100.0)
    assert amt["2021-01-02"] == pytest.approx(103.0)               # first anniversary
    assert amt["2021-12-02"] == pytest.approx(103.0)
    assert amt["2022-01-02"] == pytest.approx(100 * 1.03 ** 2)


def test_schedule_cpi_indexing_follows_the_anniversary_level():
    cpi = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2019-01-01", "2021-01-01", "2022-01-01"]))
    s = schedule(PlanSpec("x", "2020-01-02", periodic=100, growth="cpi", end="2022-03-01", index_mer=0.0), IDX, cpi)
    amt = s.set_index("date")["amount"]
    assert amt["2020-06-02"] == pytest.approx(100.0)               # CPI at the anchor = 100 (level in force on 2020-01-02)
    assert amt["2021-01-02"] == pytest.approx(110.0)               # 110 / 100
    assert amt["2022-01-02"] == pytest.approx(121.0)
    with pytest.raises(ValueError):
        schedule(PlanSpec("x", "2020-01-02", periodic=100, growth="cpi", index_mer=0.0), IDX, None)


# V2: phased contributions
BASE = (Phase(0, 10, 500, "early"), Phase(10, 13, 3000, "peak"), Phase(13, 20, 1000, "late"))


def test_phases_base_case_counts_and_sums_exact():
    s = schedule(PlanSpec("x", "2020-01-02", phases=BASE, end="2040-01-01", index_mer=0.0), IDX, None)
    assert set(s["kind"]) == {"periodic"}
    g = s.groupby("note")["amount"].agg(["count", "sum"])
    assert g.loc["early"].tolist() == [120, 60000.0]
    assert g.loc["peak"].tolist() == [36, 108000.0]
    assert g.loc["late"].tolist() == [84, 84000.0]
    assert s["date"].min() == pd.Timestamp("2020-01-02") and s.loc[s["note"] == "early", "date"].max() == pd.Timestamp("2029-12-02")
    assert s.loc[s["note"] == "peak", "date"].min() == pd.Timestamp("2030-01-02")     # half-open: year 10 belongs to the next phase


def test_phases_gap_means_no_contribution_and_none_runs_to_withdraw_from():
    ph = (Phase(0, 1, 100), Phase(2, None, 50))
    s = schedule(PlanSpec("x", "2020-01-02", phases=ph, end="2023-01-01", withdraw_from="2022-07-02", index_mer=0.0), IDX, None)
    assert (s["amount"] == 100).sum() == 12
    d = s["date"]
    assert not ((d >= "2021-01-02") & (d < "2022-01-02")).any()                       # the gap
    assert d.max() == pd.Timestamp("2022-06-02") and (s["amount"] == 50).sum() == 6   # stops at withdraw_from


def test_phases_cpi_indexing_from_the_plan_start_and_nominal_switch():
    cpi = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2019-01-01", "2021-01-01", "2022-01-01"]))
    ph = (Phase(0, 1, 100), Phase(1, 3, 200))
    s = schedule(PlanSpec("x", "2020-01-02", phases=ph, growth="cpi", end="2022-03-01", index_mer=0.0), IDX, cpi).set_index("date")["amount"]
    assert s["2020-06-02"] == pytest.approx(100.0) and s["2021-01-02"] == pytest.approx(220.0) and s["2022-01-02"] == pytest.approx(242.0)
    n = schedule(PlanSpec("x", "2020-01-02", phases=ph, growth="cpi", phase_growth="nominal", end="2022-03-01", index_mer=0.0),
                 IDX, None).set_index("date")["amount"]
    assert n["2021-01-02"] == 200 and n["2022-01-02"] == 200


def test_phases_validation_and_serial_roundtrip():
    for bad in ((Phase(0, 2, 1), Phase(1, 3, 1)), (Phase(2, 3, 1), Phase(0, 1, 1)), (Phase(0, None, 1), Phase(1, 2, 1)),
                (Phase(0, 1, -1),), (Phase(-1, 1, 1),), (Phase(1, 1, 1),)):
        with pytest.raises(ValueError):
            PlanSpec("x", "2020-01-02", phases=bad)
    with pytest.raises(ValueError):
        PlanSpec("x", "2020-01-02", phase_growth="real")
    sp = PlanSpec("x", "2020-01-02", phases=BASE)
    assert from_dict(to_dict(sp)) == sp


def test_phases_replace_periodic_and_the_card_warns():
    sp = spec(periodic=999, phases=(Phase(0, 1, 100),), end="2020-06-30")
    assert set(schedule(sp, IDX, None)["amount"]) == {100.0}
    res = run_plan(make_lab(), sp)
    assert any("flat periodic" in w for w in res.card.warnings()) and (res.flows["amount"] <= 100).all()


def test_schedule_withdrawals_extras_and_decumulation():
    s = schedule(PlanSpec("x", "2020-01-02", initial=1000, periodic=100, end="2020-12-31", withdrawals=40, withdraw_from="2020-07-01",
                          withdraw_growth=0.5, extra=(CashFlow("2020-03-10", 500, "bonus"), CashFlow("2020-09-15", -200, "car")),
                          index_mer=0.0), IDX, None)
    per = s[s["kind"] == "periodic"]
    assert per["date"].max() == pd.Timestamp("2020-06-02")          # contributions stop at withdraw_from
    wd = s[(s["kind"] == "withdrawal") & (s["note"] == "")]
    assert list(wd["date"]) == list(pd.to_datetime(["2020-07-01", "2020-08-01", "2020-09-01", "2020-10-01", "2020-11-01", "2020-12-01"]))
    assert (wd["amount"] == -40).all()                              # growth only after the first anniversary
    assert s[s["note"] == "bonus"]["kind"].item() == "extra"
    car = s[s["note"] == "car"]
    assert car["kind"].item() == "withdrawal" and car["amount"].item() == -200
    s2 = schedule(PlanSpec("x", "2020-01-02", initial=1, end="2022-08-15", withdrawals=40, withdraw_from="2020-07-01", withdraw_growth=0.5,
                           index_mer=0.0), IDX, None)
    assert s2[s2["date"] == "2021-07-01"]["amount"].item() == pytest.approx(-60.0)
    assert s2[s2["date"] == "2022-07-01"]["amount"].item() == pytest.approx(-90.0)


def test_schedule_runs_past_the_index_with_nat():
    short = pd.bdate_range("2020-01-01", "2020-03-31")
    s = schedule(PlanSpec("x", "2020-01-01", periodic=1, end="2020-06-01", index_mer=0.0), short, None)
    assert len(s) == 6 and s["executed"].isna().sum() == 3         # May-Jun... dates after the last bar have no execution day


# ------------------------------------------------------------------------------------------------ strategy leg: exact arithmetic
def test_flat_prices_final_is_contributions_minus_costs_exactly():
    lab = make_lab()
    r = run_plan(lab, spec(initial=1000, periodic=100, end="2020-03-15"))     # Jan 1: 1000 + 100, Feb 1: 100, Mar 1: 100
    assert list(r.flows["amount"]) == [1000, 100, 100, 100]
    contributions, costs = 1000 + 3 * 100, 0.01 * (1100 + 100 + 100)
    s = r.series
    assert s["contributed"].iloc[-1] == contributions
    assert s["strategy_pre"].iloc[-1] == pytest.approx(contributions - costs, rel=1e-12)          # 1287
    assert s["strategy_pre"].loc["2020-01-02"] == pytest.approx(1089.0, rel=1e-12)               # the first purchase cost 1% of 1100
    assert s["index_pre"].iloc[-1] == pytest.approx(1300.0, rel=1e-12)                           # index: no cost, no MER
    m = r.metrics
    assert m.loc["strategy", "FinalPreTax"] == pytest.approx(1287.0, rel=1e-12)
    assert m.loc["strategy", "Gain"] == pytest.approx(-13.0, rel=1e-12)
    assert m.loc["difference", "FinalPreTax"] == pytest.approx(-13.0, rel=1e-12)
    # units ledger: value = units x NAV exactly; units bought = amount / NAV at the execution day
    units = r.flows["strategy_units"].cumsum()
    assert r.flows["strategy_units"].iloc[0] == pytest.approx(1000.0)
    assert r.flows["strategy_units"].iloc[2] == pytest.approx(100 / 0.99, rel=1e-12)
    assert units.iloc[-1] * s["strategy_unit"].iloc[-1] == pytest.approx(1287.0, rel=1e-12)


def test_prices_doubling_between_flows_xirr_exact():
    lab = make_lab("2021-01-01", 2.0, cost_bps=0)
    r = run_plan(lab, spec(initial=100, end="2021-12-31"))
    assert r.series["strategy_pre"].iloc[-1] == pytest.approx(200.0, rel=1e-12)
    want = 2 ** (365 / 730) - 1                                                    # 730 days from 2020-01-01 to 2021-12-31
    assert r.metrics.loc["strategy", "XIRR"] == pytest.approx(want, abs=1e-9)
    assert r.metrics.loc["index", "XIRR"] == pytest.approx(want, abs=1e-9)
    assert r.metrics.loc["strategy", "TWR"] == pytest.approx(2 ** (365.25 / 730) - 1, abs=1e-6)
    # second contribution after the doubling: bought at 2.0, the first 100 became 200: final 300; XIRR solves NPV = 0
    r2 = run_plan(lab, spec(initial=100, end="2021-12-31", extra=(CashFlow("2021-01-04", 100),)))
    assert r2.series["strategy_pre"].iloc[-1] == pytest.approx(300.0, rel=1e-12)
    x = r2.metrics.loc["strategy", "XIRR"]
    t1, t2 = 369 / 365, 730 / 365                                                  # 2021-01-04 is day 369
    assert 100 + 100 / (1 + x) ** t1 == pytest.approx(300 / (1 + x) ** t2, rel=1e-9)


def test_withdrawal_realizing_a_gain_exact_in_the_ledger():
    lab = make_lab("2020-07-01", 2.0, cost_bps=0, tax=TaxConfig(model=MODEL, account="taxable"))
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", -500),), tax_from_outside=True))
    t = r.tax["strategy"]
    # 2020-07-01: value 2000, sells 500 = 25% of every holding: gain 500 - 0.25 x 1000 = 250; the rest (1500 vs basis 750) at the end
    assert t.loc[2020, "realized_gains"] == pytest.approx(250.0, rel=1e-12)
    assert t.loc[2021, "realized_gains"] == pytest.approx(750.0, rel=1e-12)
    assert t.loc[2020, "tax_capital_gains"] == pytest.approx(CG * 250, rel=1e-12)
    assert r.series["strategy_pre"].iloc[-1] == pytest.approx(1500.0, rel=1e-12)
    assert r.series["strategy_after_tax"].iloc[-1] == pytest.approx(1500 - CG * 1000, rel=1e-12)    # taxes paid from outside
    assert r.metrics.loc["strategy", "TaxPaid"] == pytest.approx(200.0, rel=1e-12)
    assert r.metrics.loc["strategy", "Contributed"] == pytest.approx(500.0, rel=1e-12)
    # the index leg is taxed with the same arithmetic (no dividends, no costs)
    ti = r.tax["index"]
    assert ti.loc[2020, "realized_gains"] == pytest.approx(250.0, rel=1e-12)
    assert ti.loc[2021, "realized_gains"] == pytest.approx(750.0, rel=1e-12)
    assert r.metrics.loc["difference", "FinalAfterTax"] == pytest.approx(0.0, abs=1e-9)


def test_withdrawal_cost_comes_out_of_the_sale_and_the_investor_receives_the_amount():
    lab = make_lab(cost_bps=100)
    r = run_plan(lab, spec(initial=1000, end="2020-12-31", extra=(CashFlow("2020-06-01", -200),)))
    sale = 200 / 0.99                                                             # gross sold so that sale - 1% = 200
    assert r.flows.set_index("kind").loc["withdrawal", "received"] == pytest.approx(200.0, rel=1e-12)
    assert r.series["strategy_pre"].iloc[-1] == pytest.approx(990 - sale, rel=1e-12)


def test_rrsp_withdrawals_taxed_at_the_rate_and_balance_haircut():
    tx = TaxConfig(model=MODEL, account="rrsp", rrsp_withdrawal_rate=0.30)
    lab = make_lab(cost_bps=0, tax=tx)
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-06-01", -400),)))
    assert r.flows.set_index("kind").loc["withdrawal", "flow_tax"] == pytest.approx(120.0, rel=1e-12)
    t = r.tax["strategy"]
    assert t.loc[2021, "tax_rrsp_withdrawal"] == pytest.approx(0.30 * 600, rel=1e-12)             # the balance at the end
    assert t.loc[2020, "tax_rrsp_withdrawal"] == pytest.approx(120.0, rel=1e-12)                  # the withdrawal
    assert r.metrics.loc["strategy", "TaxPaid"] == pytest.approx(300.0, rel=1e-12)
    assert r.metrics.loc["strategy", "FinalAfterTax"] == pytest.approx(0.70 * 600, rel=1e-12)
    # after-tax XIRR: -1000 on day 0, +280 (400 less 30%) on 2020-06-01, +420 at the end
    x = r.metrics.loc["strategy", "XIRRAfterTax"]
    d1, d2 = (pd.Timestamp("2020-06-01") - pd.Timestamp("2020-01-01")).days / 365, 730 / 365
    assert 1000 == pytest.approx(280 / (1 + x) ** d1 + 420 / (1 + x) ** d2, rel=1e-9)


def test_tfsa_pays_no_tax_and_no_pre_after_gap():
    lab = make_lab("2020-07-01", 2.0, cost_bps=0, tax=TaxConfig(model=MODEL, account="tfsa"))
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", -500),)))
    assert r.metrics.loc["strategy", "TaxPaid"] == 0
    assert r.metrics.loc["strategy", "FinalAfterTax"] == r.metrics.loc["strategy", "FinalPreTax"]


def test_hold_cash_earns_the_accrual_rate_until_the_rebalance():
    lab = make_lab(cost_bps=100, cash_rate=0.05)
    sp = spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", 500),), cash_timing="hold")
    r = run_plan(lab, sp)
    yrs = (pd.Timestamp("2020-12-31") - pd.Timestamp("2020-07-01")).days / 365.25
    assert r.series["cash"].loc["2020-07-01"] == 0.0                                           # the flow itself shows next bar
    assert r.series["cash"].loc["2020-12-31"] == pytest.approx(500 * 1.05 ** yrs, rel=1e-12)
    assert r.series["strategy_pre"].loc["2020-12-31"] == pytest.approx(990 + 500 * 1.05 ** yrs, rel=1e-12)
    # on the rebalance (2021-01-01) the cash joins: the trade buys cash / total of each name, 1% cost on that
    yrs2 = (pd.Timestamp("2021-01-01") - pd.Timestamp("2020-07-01")).days / 365.25
    C = 500 * 1.05 ** yrs2
    assert r.series["cash"].iloc[-1] == 0.0
    assert r.series["strategy_pre"].iloc[-1] == pytest.approx(990 + 0.99 * C, rel=1e-12)
    # invest: the contribution buys at once, 1% cost, then nothing earns anything (flat prices)
    ri = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", 500),)))
    assert ri.series["strategy_pre"].iloc[-1] == pytest.approx(990 + 495, rel=1e-12)
    assert ri.series["cash"].abs().max() == 0.0


def test_hold_interest_is_taxed_as_income_in_a_taxable_account():
    tx = TaxConfig(model=MODEL, account="taxable")
    lab = make_lab(cost_bps=0, cash_rate=0.05, tax=tx)
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", 500),), cash_timing="hold", tax_from_outside=True))
    yrs = (pd.Timestamp("2021-01-01") - pd.Timestamp("2020-07-01")).days / 365.25
    interest = 500 * (1.05 ** yrs - 1)
    got = r.tax["strategy"]["interest_income"].sum()
    assert got == pytest.approx(interest, rel=1e-9)


def test_flows_off_leaves_simulate_untouched():
    lab = make_lab()
    sc = lab.screen("all3")
    rebal = [pd.Timestamp("2020-01-01")]
    a = simulate(sc, lab.market, lab.config, rebal)
    b = simulate(sc, lab.market, lab.config, rebal, flows=None)
    pd.testing.assert_series_equal(a.curve, b.curve)
    assert a.flows is None and a.cash_curve is None
    with pytest.raises(ValueError):
        simulate(sc, lab.market, lab.config, rebal, flows=pd.DataFrame({"amount": [5.0]}, index=[pd.Timestamp("2020-02-03")]))


def test_flow_on_a_rebalance_date_joins_the_rebalance_trade():
    lab = make_lab(cost_bps=100)
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", extra=(CashFlow("2021-01-01", 300),)))
    # 2021-01-01 is the 12-month rebalance: the 300 is bought by the same trade as the (already balanced) names: cost 1% of 300
    assert r.series["strategy_pre"].iloc[-1] == pytest.approx(990 + 297, rel=1e-12)


# ------------------------------------------------------------------------------------------------ index leg
def test_index_ledger_and_tax_buy_and_hold_flows_exact():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    growth = pd.Series(np.where(idx >= "2020-07-01", 2.0, 1.0), index=idx)
    flows = pd.Series({pd.Timestamp("2020-01-01"): 1000.0, pd.Timestamp("2020-07-01"): -500.0})
    tx = TaxConfig(model=MODEL, account="taxable", capital=1.0, pay_from_portfolio=False, liquidate_at_end=True)
    tab, pre, after = tax_buy_and_hold_flows(growth, flows, 0.0, tx, cost_bps=100)
    # 990 units; at the doubling the value is 1980; to receive 500 sell 500/0.99 of value = share g/1980
    g = 500 / 0.99
    sh = g / 1980.0
    gain = 500 - 1000 * sh                                                         # basis 1000 (the amount spent, cost included)
    assert tab.loc[2020, "realized_gains"] == pytest.approx(gain + (1980 - g - 1000 * (1 - sh)), rel=1e-12)
    u, bought, recv, held = index_ledger(growth, flows, 100)
    assert held.iloc[-1] * 2.0 == pytest.approx(1980 - g, rel=1e-12)
    assert pre.iloc[-1] == pytest.approx(1980 - g, rel=1e-12)
    # dividends: flat price, yield 4%/yr on the previous bar's value, reinvested into the cost base
    flat = pd.Series(1.0, index=idx)
    tab2, pre2, _ = tax_buy_and_hold_flows(flat, pd.Series({idx[0]: 1000.0}), 0.04, tx)
    assert tab2.loc[2020, "dividends_ca"] == pytest.approx(1000 * 0.04 * (idx[-1] - idx[0]).days / 365.25, rel=1e-12)
    assert tab2.loc[2020, "realized_gains"] == pytest.approx(0.0, abs=1e-9)        # the dividends raised the basis: no gain at the end... value 1000, basis 1000 + dividends
    assert tab2.loc[2020, "realized_losses"] < 0                                   # (a price-flat total return: the basis exceeds the value)


def test_index_mer_accrues_daily_and_cost_applies_to_flows():
    lab = make_lab(cost_bps=0)
    r = run_plan(lab, PlanSpec("all3", "2020-01-01", initial=1000, end="2021-12-31", index_mer=0.01))
    yrs = (r.series.index[-1] - r.series.index[0]).days / 365.25
    assert r.series["index_pre"].iloc[-1] == pytest.approx(1000 * 0.99 ** yrs, rel=1e-12)
    assert r.metrics.loc["index", "MERDrag/yr"] == 0.01


# ------------------------------------------------------------------------------------------------ plan-level behaviour
def test_end_past_the_data_runs_to_the_last_bar_and_names_both_dates():
    lab = make_lab()
    r = run_plan(lab, spec(initial=1000, end="2023-06-30"))
    assert r.series.index[-1] == pd.Timestamp("2021-12-31")
    w = " ".join(r.card.warnings())
    assert "2023-06-30" in w and "2021-12-31" in w


def test_inflation_auto_warns_for_us_cpi_and_real_values_use_the_deflator():
    lab = make_lab(cost_bps=0)
    r = run_plan(lab, spec(initial=1000, end="2021-12-31"))
    assert any("after-inflation returns use US CPI (expediency, see DECISIONS.md)" in w for w in r.card.warnings())
    d = r.series["cpi_deflator"]
    assert d.iloc[0] == 1.0
    assert r.metrics.loc["strategy", "FinalReal"] == pytest.approx(r.metrics.loc["strategy", "FinalPreTax"] / d.iloc[-1], rel=1e-12)
    assert r.metrics.loc["strategy", "XIRRReal"] < r.metrics.loc["strategy", "XIRR"]
    c = run_plan(lab, spec(initial=1000, end="2021-12-31", inflation=0.10))                 # a constant 10%/yr
    assert c.series["cpi_deflator"].iloc[-1] == pytest.approx(1.10 ** (730 / 365.25), rel=1e-12)
    assert not any("US CPI" in w for w in c.card.warnings())


def test_risk_free_ca10y_falls_back_with_a_warning_and_constant_works():
    lab = make_lab(cost_bps=0)
    r = run_plan(lab, spec(initial=1000, periodic=10, end="2021-12-31", risk_free="ca10y"))
    assert any("V39055" in w for w in r.card.warnings())
    k = run_plan(lab, spec(initial=1000, periodic=10, end="2021-12-31", risk_free=0.03))
    assert k.monthly["rf"].iloc[0] == pytest.approx(1.03 ** (1 / 12) - 1, rel=1e-12)


def test_goal_years_and_shortfall():
    lab = make_lab("2021-01-01", 2.0, cost_bps=0)
    r = run_plan(lab, spec(initial=1000, end="2021-12-31", goal=1500))
    assert r.goal["reached"] == pd.Timestamp("2021-01-01")
    assert r.goal["years"] == pytest.approx(366 / 365.25, rel=1e-12)
    assert r.goal["final_vs_goal"] == pytest.approx(500.0)
    assert r.metrics.loc["strategy", "GoalShortfall"] == 0.0
    miss = run_plan(lab, spec(initial=1000, end="2021-12-31", goal=5000))
    assert miss.goal["reached"] is None and miss.goal["final_vs_goal"] == pytest.approx(-3000.0)
    assert miss.metrics.loc["strategy", "GoalShortfall"] == pytest.approx(3000.0)
    assert np.isnan(miss.metrics.loc["strategy", "YearsToGoal"])
    assert run_plan(lab, spec(initial=1000, end="2021-12-31")).goal is None


def test_metric_table_shape_and_registry():
    lab = make_lab()
    r = run_plan(lab, spec(initial=1000, periodic=50, end="2021-12-31"))
    assert list(r.metrics.index) == ["strategy", "index", "difference"]
    assert list(r.metrics.columns) == METRICS
    assert set(METRICS) <= set(REGISTRY)
    assert list(metrics_table(r).columns) == METRICS
    pd.testing.assert_frame_equal(metrics_table(r), r.metrics)
    assert {"strategy", "index", "rf"} <= set(r.monthly.columns)
    assert {"contributed", "strategy_pre", "strategy_after_tax", "index_pre", "index_after_tax", "cpi_deflator",
            "strategy_real", "index_real", "cash"} <= set(r.series.columns)


def test_spec_round_trips_through_serialization():
    sp = PlanSpec("dogs", "1990-01-02", initial=10_000, periodic=500, growth="cpi", extra=(CashFlow("2000-01-03", 5000, "x"),),
                  withdrawals=800, withdraw_from="2018-01-02", cash_timing="hold", goal=1e6)
    assert from_dict(to_dict(sp)) == sp
    with pytest.raises(ValueError):
        PlanSpec("dogs", "1990-01-02", frequency="weekly")


# ------------------------------------------------------------------------------------------------ one tax basis
def _taxable(**kw):
    return make_lab("2020-07-01", 2.0, cost_bps=0, tax=TaxConfig(model=MODEL, account="taxable", **kw))


def test_tax_from_the_portfolio_one_year_of_gains_reconciles_with_the_ledger():
    """1000 in, doubled by 2020-07-01, 500 withdrawn (25% of every holding: gain 250, tax 0.20 x 250 = 50 paid at the
    2020 year end FROM the portfolio: 1500 -> 1450, the cost base 750 -> 725), flat in 2021; the 2021 end liquidates:
    gain 1450 - 725 = 725, tax 145. After tax 1305. Paid from outside the account the same plan ends at 1500 - 50 - 150."""
    sp = dict(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", -500),))
    r = run_plan(_taxable(), spec(**sp))
    s, t = r.series, r.tax["strategy"]
    assert t.loc[2020, "tax_paid"] == pytest.approx(50.0, rel=1e-12) and t.loc[2021, "tax_paid"] == pytest.approx(145.0, rel=1e-12)
    assert t.loc[2021, "realized_gains"] == pytest.approx(725.0, rel=1e-12)
    assert t.loc[2021, "wealth_pre_tax"] == pytest.approx(1450.0, rel=1e-12)             # the portfolio that paid its 2020 tax
    assert s["strategy_pre"].iloc[-1] == pytest.approx(1500.0, rel=1e-12)                # the same plan with no tax at all
    assert s["strategy_after_tax"].loc["2020-12-30"] == pytest.approx(1500.0, rel=1e-12)  # the 2020 payment is on the last bar of the year
    assert s["strategy_after_tax"].loc["2021-01-04"] == pytest.approx(1450.0, rel=1e-12)
    assert s["strategy_after_tax"].iloc[-1] == pytest.approx(1500.0 - 50.0 - 145.0, rel=1e-12)
    assert r.metrics.loc["strategy", "FinalAfterTax"] == pytest.approx(1305.0, rel=1e-12)
    assert r.metrics.loc["strategy", "TaxPaid"] == pytest.approx(195.0, rel=1e-12)
    # units on the after-tax NAV: V = (sum of units) x NAV exactly, on the day before the final settlement and on the last bar
    u = r.flows["strategy_units"]
    assert list(u.round(9)) == [1000.0, -250.0]                                          # 500 sold at NAV 2.0
    assert u.sum() * s["strategy_unit_after_tax"].iloc[-1] == pytest.approx(1305.0, rel=1e-12)
    assert u.sum() * s["strategy_unit_after_tax"].iloc[-2] == pytest.approx(s["strategy_after_tax"].iloc[-2], rel=1e-12)
    # the pre-tax NAV is the flow-free price path: V_pre = units bought on it x NAV
    assert s["strategy_unit"].iloc[-1] == pytest.approx(2.0, rel=1e-12)


def test_tax_from_outside_restores_the_old_numbers():
    r = run_plan(_taxable(), spec(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", -500),), tax_from_outside=True))
    t = r.tax["strategy"]
    assert t.loc[2021, "tax_paid"] == pytest.approx(150.0, rel=1e-12) and t.loc[2021, "realized_gains"] == pytest.approx(750.0, rel=1e-12)
    assert r.metrics.loc["strategy", "FinalAfterTax"] == pytest.approx(1300.0, rel=1e-12)
    assert r.series["strategy_after_tax"].loc["2021-01-04"] == pytest.approx(1450.0, rel=1e-12)
    assert "strategy_unit_after_tax" not in r.series
    assert r.extras["tax_basis"] == "outside" and any("outside the account" in n for n in r.card.notes)


def test_tax_from_outside_after_tax_is_floored_at_zero_and_the_dates_are_on_the_card():
    days = DATES
    px = pd.DataFrame({t: np.select([days < "2020-07-01", days < "2021-03-01", days < "2021-09-01"], [1.0, 2.0, 0.05], 2.0)
                       for t in "ABC"}, index=days)
    fund = pd.DataFrame([dict(date=pd.Timestamp("2020-01-01"), ticker=t, sector="in", price=10.0, market_cap=1000.0, dividend=0.0)
                         for t in "ABC"])
    cfg = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=TaxConfig(model=MODEL, account="taxable"))
    lab = Lab(Market(add_ratios(fund), px), config=cfg)
    lab.define(ScreenBuilder("all3", n=3).where("sector == 'in'").build())
    # 1000 -> 2000; 1000 withdrawn on 2020-07-01 (gain 500, tax 100 at the 2020 year end); a crash to 25 in 2021-03..08, back to 1000
    sp = dict(initial=1000, end="2021-12-31", extra=(CashFlow("2020-07-01", -1000),))
    out = run_plan(lab, spec(**sp, tax_from_outside=True))
    a = out.series["strategy_after_tax"]
    assert (a >= 0).all() and a.min() == 0.0
    bad = out.extras["floor_dates"]["strategy"]
    assert bad[0] >= pd.Timestamp("2021-03-01") and bad[-1] < pd.Timestamp("2021-09-01")
    assert any("floored at zero" in n and "strategy" in n and str(bad[0].date()) in n for n in out.card.notes)
    assert a.iloc[-1] == pytest.approx(1000.0 - 100.0 - 100.0, rel=1e-12)            # 2020 tax 100, liquidation tax 0.2 x (1000 - 500)
    inp = run_plan(lab, spec(**sp))                                                    # from the portfolio it cannot go negative
    b = inp.series["strategy_after_tax"]
    assert (b > 0).all() and not inp.extras["floor_dates"]
    assert b.iloc[-1] == pytest.approx(900.0 - 0.2 * (900.0 - 450.0), rel=1e-12)       # the 2020 tax shrank the holding and its cost base


def test_flow_steps_do_not_take_a_full_snapshot(monkeypatch):
    lab = make_lab(cost_bps=0, tax=TaxConfig(model=MODEL, account="taxable"))
    seen = []
    orig = type(lab.market).view

    def counting(self, asof):
        seen.append(pd.Timestamp(asof))
        return orig(self, asof)
    monkeypatch.setattr(type(lab.market), "view", counting)
    r = run_plan(lab, spec(initial=1000, periodic=100, end="2021-12-31"))
    n_flows = int(r.flows["applied"].sum())
    assert n_flows >= 24 and len(set(seen)) <= 4          # the 12-month rebalances (and nothing per flow or per tax payment)


@pytest.mark.skipif(not __import__("screenlab.realdata.sp500_panel", fromlist=["x"]).available(), reason="S&P 500 panel not on this machine")
def test_run_plan_cold_start_on_the_real_panel_is_fast_enough():
    import time
    from screenlab.taxes import CanadianTax
    lab = Lab.real_sp500(start="1990-01-01", end="2022-12-30")
    tx = TaxConfig(model=CanadianTax("AB", other_income=90_000), macro=lab.macro, account="taxable")
    t0 = time.time()
    r = lab.plan("dogs", "1990-01-02", tax=tx, end="2022-12-30", initial=10_000, periodic=500, growth="cpi", rebalance_months=12)
    dt = time.time() - t0
    print(f"run_plan cold start on the real panel: {dt:.1f} s")
    assert dt < 40 and r.metrics.loc["strategy", "FinalAfterTax"] > 0        # target 20 s on a quiet machine; 60 s before the change
