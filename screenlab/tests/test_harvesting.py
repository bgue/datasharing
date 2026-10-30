"""Tax-loss harvesting (harvesting.py) and the 3-year loss carry-back (taxes.assess): exact arithmetic on hand-built
markets, the CRA superficial-loss rule, the replacement modes, the hold-rule override, report columns, serialization.

Markets: names A, B, C on business days 2020-01-01 .. 2022-12-31, total-return prices that are step functions, no costs,
no dividends, cash earns 0. The screen is "top n by market_cap" with the market caps set per rebalance row, so the
test controls which names the screen wants (and, through `Screen.ranked`, their order). Flat placeholder model:
capital gains tax = 0.20 of the net gain. Wealth is 1.0 = $100,000.

    python -m pytest screenlab/tests/test_harvesting.py -q
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from screenlab import Config, Lab, Market, ScreenBuilder
from screenlab.backtest import run
from screenlab.construction import HoldRule
from screenlab.data import add_ratios
from screenlab.harvesting import HARVEST_COLS, HarvestRun, TaxLossHarvest, harvested_per_year
from screenlab.metrics import summarize
from screenlab.serial import from_dict, to_dict
from screenlab.taxes import PlaceholderCanadianTax, TaxConfig, YearTax, assess

CAP = 100_000.0
MODEL = PlaceholderCanadianTax(marginal_rate=0.40, federal_dtc=0.15, provincial_dtc=0.10, provincial_dtc_noneligible=0.03)
CG_RATE = 0.20                                    # 0.5 inclusion x 0.40
DATES = pd.bdate_range("2020-01-01", "2022-12-31")
END = DATES[-1]


def step(*pairs):
    """Total-return path: `pairs` = (date, level) from that date on; 1.0 before the first."""
    s = pd.Series(1.0, index=DATES)
    for d, v in pairs:
        s[s.index >= d] = v
    return s


def market(prices: dict, caps: dict):
    """`prices` {ticker: Series}, `caps` {report date: {ticker: market cap}} (the screen's ranking and cap weights)."""
    rows = [dict(date=pd.Timestamp(d), ticker=t, sector="x", price=10.0, market_cap=c, dividend=0.0)
            for d, cs in caps.items() for t, c in cs.items()]
    return Market(add_ratios(pd.DataFrame(rows)), pd.DataFrame(prices))


def screen(n):
    return ScreenBuilder("top", n=n).rank(market_cap="desc").build()


def tax(harvest=None, **kw):
    return TaxConfig(model=MODEL, capital=CAP, pay_from_portfolio=False, harvest=harvest, **kw)


def go(mkt, n, harvest, start="2020-01-01", **cfg):
    c = Config(rebalance_months=12, cost_bps=0, min_holdings=1, tax=tax(harvest), **cfg)
    return run(screen(n), mkt, c, start=start)


A_DROP = step(("2020-07-01", 0.8))                # A falls 20% mid-2020 and stays there
FLAT = step()


def m1(caps21, caps22=None):
    caps = {"2020-01-01": dict(A=3, B=2, C=1), "2021-01-01": caps21, "2022-01-03": caps22 or caps21}
    return market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)


# --------------------------------------------------------------------------- off by default
def test_off_by_default_and_serialization():
    assert TaxConfig().harvest is None and "harvest" not in to_dict(TaxConfig())
    base = go(m1(dict(A=0.5, B=2, C=1)), 1, None)
    assert base.harvest is None and "Harvested/yr" not in base.tax.columns
    h = TaxLossHarvest(threshold_pct=0.15, min_loss=500.0, when="both", replacement="similar", superficial_days=45)
    cfg = Config(tax=tax(h))
    assert from_dict(to_dict(cfg)) == cfg and from_dict(to_dict(h)) == h
    assert to_dict(cfg)["tax"]["harvest"]["when"] == "both"
    for bad in (dict(when="monthly"), dict(replacement="sell"), dict(threshold_pct=1.2), dict(min_loss=-1)):
        with pytest.raises(ValueError):
            TaxLossHarvest(**bad)


# --------------------------------------------------------------------------- the harvest itself
def test_year_end_harvest_realizes_the_loss():
    mk = m1(dict(A=0.5, B=2, C=1))                # the screen drops A at 2021-01-01
    plain = go(mk, 1, None)
    res = go(mk, 1, TaxLossHarvest())
    # A: bought at 1.0 (basis 1.0), worth 0.8 on 2020-12-31: unrealized loss 20% > 10%, 0.2 of wealth
    assert plain.tax.loc[2020, "realized_losses"] == 0
    assert plain.tax.loc[2021, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)   # sold a day later, next year
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    assert res.tax.loc[2021, "realized_losses"] == 0
    assert res.tax.loc[2020, "loss_carry_out"] == pytest.approx(0.2 * CAP, rel=1e-12)
    pd.testing.assert_series_equal(res.curve, plain.curve)                 # only WHEN the loss is realized moves
    h = res.harvest
    assert list(h.columns) == HARVEST_COLS and len(h) == 1
    r = h.iloc[0]
    assert (r["date"], r["name"], r["source"]) == (pd.Timestamp("2020-12-31"), "A", "harvest")
    assert r["loss"] == pytest.approx(0.2, rel=1e-12) and r["unrealized_pct"] == pytest.approx(0.2, rel=1e-12)
    assert r["denied"] == 0 and r["net_loss"] == pytest.approx(0.2, rel=1e-12) and r["replacement"] == "cash"
    # never bought back: no closed window (days_out NaN), the open one runs to the end of the data (days_out_open)
    assert pd.isna(r["days_out"]) and pd.isna(r["rebought"]) and r["days_out_open"] == (END - pd.Timestamp("2020-12-31")).days
    assert HarvestRun.name_days_out(res) == {"closed": 0, "open": 729, "n_closed": 0, "n_open": 1}
    assert r["loss_pre_tax"] == r["loss"] and r["acct_factor"] == 1.0              # first year, tax not paid yet: same scale


def test_threshold_and_min_loss_gate_the_harvest():
    mk = m1(dict(A=0.5, B=2, C=1))
    assert go(mk, 1, TaxLossHarvest(threshold_pct=0.25)).harvest.query("source == 'harvest'").empty     # 20% < 25%
    assert go(mk, 1, TaxLossHarvest(min_loss=25_000.0)).harvest.query("source == 'harvest'").empty       # $20,000 < $25,000
    assert len(go(mk, 1, TaxLossHarvest(min_loss=19_000.0)).harvest.query("source == 'harvest'")) == 1


# --------------------------------------------------------------------------- the superficial-loss rule
def test_repurchase_inside_30_days_denies_the_loss_and_raises_the_acb():
    mk = m1(dict(A=3, B=2, C=1), dict(A=0.5, B=2, C=1))      # the screen wants A back on 2021-01-01, drops it 2022-01-03
    res = go(mk, 1, TaxLossHarvest(on_repick="allow_denial"))              # Q3: the old arithmetic (default now avoids)
    r = res.harvest.iloc[0]
    # sold 1.0 share-equivalent (0.8 of wealth at price 0.8) on 2020-12-31, bought back 1 day later: all of it
    assert r["rebought"] == pd.Timestamp("2021-01-01") and r["days_out"] == 1 and pd.isna(r["days_out_open"])
    nd = HarvestRun.name_days_out(res)                       # the 2021 re-harvest is never bought back: open, not counted as closed
    assert nd["closed"] == 1 and nd["n_closed"] == 1 and nd["n_open"] == 1
    assert r["loss"] == pytest.approx(0.2) and r["denied"] == pytest.approx(0.2, rel=1e-12)
    assert r["acb_added"] == pytest.approx(0.2, rel=1e-12) and r["net_loss"] == pytest.approx(0, abs=1e-15)
    t = res.tax
    assert t.loc[2020, "realized_losses"] == pytest.approx(0, abs=1e-6)                   # the loss is out of 2020
    assert t.loc[2020, "loss_carry_out"] == pytest.approx(0, abs=1e-6)
    # ACB of the repurchased shares = 0.8 paid + 0.2 denied = 1.0: on 2021-12-31 (A still 0.8) the stage harvests the same
    # 20% again, realizing 0.2 -- with an ACB of 0.8 it would see no loss at all
    assert t.loc[2021, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    second = res.harvest.iloc[1]
    assert second["date"] == pd.Timestamp("2021-12-31") and second["loss"] == pytest.approx(0.2, rel=1e-12)
    assert second["denied"] == 0 and second["unrealized_pct"] == pytest.approx(0.2, rel=1e-12)
    # without harvesting the position is simply held with ACB 1.0 and the loss arrives when the screen sells (2022)
    plain = go(mk, 1, None)
    assert plain.tax.loc[2022, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    pd.testing.assert_series_equal(res.curve, plain.curve)


def test_partial_repurchase_denies_in_proportion():
    caps = {"2020-01-01": dict(A=2, B=2, C=1), "2021-01-01": dict(A=1, B=0.5, C=3), "2022-01-03": dict(A=1, B=0.5, C=3)}
    mk = market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)
    res = go(mk, 2, TaxLossHarvest(on_repick="allow_denial"), weighting="cap")
    # 2020-12-31: A = 0.4 of wealth 0.9 (basis 0.5): loss 0.1, 0.5 shares sold at 0.8. 2021-01-01 cap weights A 1/4:
    # 0.9 x 1/4 / 0.8 = 0.28125 shares bought back -> 0.28125 / 0.5 of the loss is denied
    r = res.harvest.iloc[0]
    denied = 0.1 * 0.28125 / 0.5
    assert r["loss"] == pytest.approx(0.1, rel=1e-12) and r["denied"] == pytest.approx(denied, rel=1e-12)
    assert denied == pytest.approx(0.05625)
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-(0.1 - denied) * CAP, rel=1e-9)
    assert r["days_out"] == 1


def test_a_loss_sale_by_the_screen_is_checked_too():
    # no harvest happens (threshold 50%): the screen itself sells A at a loss on 2020-07-01 and wants it back 19 days later
    from screenlab.backtest import simulate
    caps = {"2020-06-01": dict(A=3, B=2), "2020-07-01": dict(A=0.5, B=2), "2020-07-20": dict(A=3, B=2)}
    mk = market(dict(A=A_DROP, B=FLAT), caps)
    rebal = [pd.Timestamp(d) for d in ("2020-06-01", "2020-07-01", "2020-07-20", "2020-09-01")]
    cfg = Config(cost_bps=0, min_holdings=1, tax=tax(TaxLossHarvest(when="rebalance", threshold_pct=0.5)))
    res = simulate(screen(1), mk, cfg, rebal)
    r = res.harvest.iloc[0]
    assert (r["source"], r["name"], r["date"]) == ("screen", "A", pd.Timestamp("2020-07-01"))
    assert r["loss"] == pytest.approx(0.2, rel=1e-12) and r["denied"] == pytest.approx(0.2, rel=1e-12)
    assert r["rebought"] == pd.Timestamp("2020-07-20") and r["days_out"] == 19
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(0, abs=1e-6)       # denied, so no loss in 2020
    # the same run without the stage books the (superficial) loss, which the stage exists to catch
    plain = simulate(screen(1), mk, Config(cost_bps=0, min_holdings=1, tax=tax()), rebal)
    assert plain.tax.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)


def test_stay_out_keeps_the_name_out_until_the_window_has_passed():
    caps = {"2020-01-01": dict(A=3, B=2, C=1), "2021-01-01": dict(A=3, C=2, B=0.5), "2022-01-03": dict(A=3, C=2, B=0.5)}
    mk = market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)
    cash = go(mk, 2, TaxLossHarvest(replacement="cash", on_repick="allow_denial"))
    out = go(mk, 2, TaxLossHarvest(replacement="stay_out"))
    # equal weights: 0.4 of 0.9 wealth sold at 0.8 -> 0.5 shares; "cash" lets the screen buy 0.45 / 0.8 = 0.5625 back on
    # 2021-01-01: the whole loss (0.1) is denied
    assert cash.holdings[pd.Timestamp("2021-01-01")] == ["A", "C"]
    assert cash.harvest.iloc[0]["denied"] == pytest.approx(0.1, rel=1e-12)
    # "stay_out": A is removed from the picks inside the window, C takes the whole portfolio; the loss stands
    assert out.holdings[pd.Timestamp("2021-01-01")] == ["C"]
    r = out.harvest.iloc[0]
    assert r["denied"] == 0 and r["loss"] == pytest.approx(0.1, rel=1e-12)
    assert out.tax.loc[2020, "realized_losses"] == pytest.approx(-0.1 * CAP, rel=1e-12)
    # A comes back at the first rebalance after the window: 2022-01-03, 368 days after the sale
    assert r["rebought"] == pd.Timestamp("2022-01-03") and r["days_out"] == 368
    assert out.holdings[pd.Timestamp("2022-01-03")] == ["A", "C"]


def test_similar_replacement_holds_the_next_ranked_name_for_the_window():
    prices = dict(A=step(("2020-10-01", 0.8)), B=step(("2021-01-15", 1.1)), C=FLAT)
    caps = {d: dict(A=3, B=2, C=1) for d in ("2020-07-01", "2021-07-01", "2022-07-01")}
    mk = market(prices, caps)
    res = go(mk, 1, TaxLossHarvest(replacement="similar"), start="2020-07-01")
    # 2020-12-31: A (worth 0.8, basis 1.0) harvested; next-ranked name not held is B, bought with the 0.8
    # window to 2021-01-30; the swap back is the first bar on or after 2021-02-01 (32 days)
    r = res.harvest.iloc[0]
    assert (r["name"], r["replacement"], r["days_out"]) == ("A", "B", 32)
    assert r["rebought"] == pd.Timestamp("2021-02-01") and r["denied"] == 0
    t = res.tax
    assert t.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    # B rose 10% during the window: 0.8 -> 0.88, a 0.08 gain realized when it is swapped for A on 2021-02-01
    assert t.loc[2021, "realized_gains"] == pytest.approx(0.08 * CAP, rel=1e-12)
    assert t.loc[2021, "loss_carry_out"] == pytest.approx(0.12 * CAP, rel=1e-9)   # the carried 0.2 loss absorbed the 0.08 gain
    assert t.loc[2021, "tax_capital_gains"] == 0
    # the pre-tax curve is the replacement's during the window, then A's
    c = res.curve
    assert c.loc["2021-01-14"] == pytest.approx(0.8) and c.loc["2021-01-15"] == pytest.approx(0.88)
    assert c.loc["2021-12-30"] == pytest.approx(0.88)
    assert (res.holdings[pd.Timestamp("2021-07-01")] == ["A"])


def test_harvesting_overrides_a_hold_rule_keep():
    caps = {"2020-01-01": dict(A=3, B=2, C=1), "2021-01-01": dict(A=2, B=3, C=1)}
    mk = market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)
    hold = dict(hold_rule=HoldRule(keep_within=2.0))
    kept = go(mk, 1, None, **hold)
    assert kept.holdings[pd.Timestamp("2021-01-01")] == ["A"]          # A ranks 2nd, within 2 x 1: kept
    out = go(mk, 1, TaxLossHarvest(when="rebalance", replacement="stay_out"), **hold)
    assert out.holdings[pd.Timestamp("2021-01-01")] == ["B"]           # the loss on A beats the keep
    assert out.tax.loc[2021, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    assert out.harvest.iloc[0]["source"] == "harvest"
    # "cash" with the screen wanting A anyway: sold and bought back the same day = a wash, the whole loss denied
    caps["2021-01-01"] = dict(A=3, B=2, C=1)
    wash = go(market(dict(A=A_DROP, B=FLAT, C=FLAT), caps), 1, TaxLossHarvest(when="rebalance", on_repick="allow_denial"))
    r = wash.harvest.iloc[0]
    assert r["loss"] == pytest.approx(0.2) and r["denied"] == pytest.approx(0.2) and r["net_loss"] == pytest.approx(0, abs=1e-15)
    assert wash.tax.loc[2021, "realized_losses"] == 0


# --------------------------------------------------------------------------- the 3-year carry-back
def carry_events(years):
    idx = pd.date_range("2018-01-01", "2023-12-31", freq="D")
    ev = pd.DataFrame(0.0, index=idx, columns=["div_ca", "div_fx", "gain", "loss"])
    for y, (g, l) in years.items():
        ev.loc[f"{y}-06-01", ["gain", "loss"]] = [g, l]
    return pd.Series(1.0, index=idx), ev


def test_carry_back_refunds_the_most_recent_years_first():
    curve, ev = carry_events({2018: (0.10, 0), 2019: (0.20, 0), 2020: (0.30, 0), 2021: (0.40, 0), 2022: (0, -0.65)})
    t, after = assess(curve, ev, tax(), CAP)
    assert t["tax_capital_gains"].loc[2018:2021].round(6).tolist() == [2_000, 4_000, 6_000, 8_000]
    # 2022 net loss 65,000: 2021 takes 40,000 (refund 8,000), 2020 takes 25,000 of 30,000 (6,000 x 25/30 = 5,000)
    assert t.loc[2022, "carried_back"] == pytest.approx(65_000)
    assert t.loc[2022, "refund"] == pytest.approx(13_000)
    assert t.loc[2022, "tax_paid"] == pytest.approx(-13_000)                      # a credit at the year end
    assert t.loc[2022, "loss_carry_out"] == pytest.approx(0, abs=1e-9)
    assert t.loc[2022, "cum_tax_paid"] == pytest.approx(2_000 + 4_000 + 6_000 + 8_000 - 13_000)
    assert after.iloc[-1] == pytest.approx(1.0 - 7_000 / CAP)
    yt = t.attrs["year_tax"][2022]
    assert isinstance(yt, YearTax) and yt.breakdown["carried_back"] == pytest.approx(65_000)
    assert yt.breakdown["refund"] == pytest.approx(13_000)
    assert yt.breakdown["refund_by_year"] == {2021: pytest.approx(8_000), 2020: pytest.approx(5_000)}


def test_carry_back_stops_at_three_years_and_the_rest_carries_forward():
    curve, ev = carry_events({2018: (0.10, 0), 2019: (0.20, 0), 2020: (0.30, 0), 2021: (0.40, 0), 2022: (0, -1.20),
                              2023: (0.50, 0)})
    t, _ = assess(curve, ev, tax(), CAP)
    # 120,000 loss: 2021 (40,000), 2020 (30,000), 2019 (20,000) = 90,000 back, 2018 is the fourth year back: not reached
    assert t.loc[2022, "carried_back"] == pytest.approx(90_000)
    assert t.loc[2022, "refund"] == pytest.approx(8_000 + 6_000 + 4_000)
    assert t.loc[2022, "loss_carry_out"] == pytest.approx(30_000)                 # the rest goes forward
    # 2023: 50,000 gain - 30,000 carried forward = 20,000 taxable
    assert t.loc[2023, "tax_capital_gains"] == pytest.approx(CG_RATE * 20_000)
    assert t.loc[2023, "loss_carry_out"] == pytest.approx(0, abs=1e-9)
    assert t.loc[2018, "refund"] == 0 and (t["carried_back"].drop(2022) == 0).all()


def test_carry_back_is_applied_once_and_a_forward_balance_is_not_carried_back():
    # 2020 loss 0.05 (no gains before: all forward), 2021 gain 0.20 (taxable 0.15 after the carry-forward),
    # 2022 loss 0.10: only the 15,000 of 2021 can take it back (2020's loss was already used going forward)
    curve, ev = carry_events({2020: (0, -0.05), 2021: (0.20, 0), 2022: (0, -0.10)})
    t, _ = assess(curve, ev, tax(), CAP)
    assert t.loc[2020, "loss_carry_out"] == pytest.approx(5_000) and t.loc[2020, "refund"] == 0
    assert t.loc[2021, "tax_capital_gains"] == pytest.approx(CG_RATE * 15_000)
    assert t.loc[2022, "carried_back"] == pytest.approx(10_000) and t.loc[2022, "refund"] == pytest.approx(CG_RATE * 10_000)
    assert t.loc[2022, "loss_carry_out"] == pytest.approx(0, abs=1e-9)


def test_carry_back_credit_with_tax_paid_from_the_portfolio_and_registered_accounts():
    curve, ev = carry_events({2020: (0.20, 0), 2021: (0, -0.20)})
    t, after = assess(curve, ev, TaxConfig(model=MODEL, capital=CAP), CAP)
    assert t.loc[2021, "refund"] == pytest.approx(CG_RATE * 20_000 * (1 - CG_RATE * 20_000 / CAP))   # f after 2020's tax
    assert after.iloc[-1] > after.loc["2020-12-31"]                                                 # the credit grows the account
    t2, _ = assess(curve, ev, tax(account="tfsa"), CAP)
    assert (t2["refund"] == 0).all() and (t2["carried_back"] == 0).all()


# --------------------------------------------------------------------------- after-tax benefit and the report
def benefit_market():
    prices = dict(A=step(("2020-10-01", 0.8)), B=step(("2020-10-01", 1.3)), C=FLAT)
    caps = {"2020-07-01": dict(A=3, B=2.5, C=1), "2021-07-01": dict(A=3, C=2, B=0.5), "2022-07-01": dict(A=3, C=2, B=0.5)}
    return market(prices, caps)


def test_harvesting_lowers_the_tax_and_raises_after_tax_cagr():
    mk = benefit_market()
    plain, res = go(mk, 2, None, start="2020-07-01"), go(mk, 2, TaxLossHarvest(), start="2020-07-01")
    pd.testing.assert_series_equal(res.curve, plain.curve)                        # A is flat while the cash waits: same pre-tax curve
    # 2020-12-31: A worth 0.4 on basis 0.5: loss 0.1 harvested. 2021-07-01: B (0.65, basis 0.5) is sold: gain 0.15.
    assert plain.tax.loc[2021, "tax_capital_gains"] == pytest.approx(CG_RATE * 0.15 * CAP, rel=1e-12)
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-0.1 * CAP, rel=1e-12)
    assert res.tax.loc[2021, "tax_capital_gains"] == pytest.approx(CG_RATE * 0.05 * CAP, rel=1e-12)
    assert res.tax["cum_tax_paid"].iloc[-1] == pytest.approx(CG_RATE * 0.05 * CAP, rel=1e-12)
    assert res.tax["cum_tax_paid"].iloc[-1] < plain.tax["cum_tax_paid"].iloc[-1]
    assert res.harvest.iloc[0]["denied"] == 0                                    # A comes back 181 days later
    a_h, a_p = summarize(res.after_tax_curve)["CAGR"], summarize(plain.after_tax_curve)["CAGR"]
    assert a_h > a_p


def test_report_columns_harvested_per_year_and_tax_deferred():
    mk = benefit_market()
    lab = Lab(mk, config=Config(min_holdings=1, cost_bps=0, rebalance_months=12))
    sc = screen(2)
    rep = lab.backtest([sc], start="2020-07-01", tax=tax(TaxLossHarvest()))
    row = rep.table.loc["top"]
    yrs = (rep.results["top"].curve.index[-1] - pd.Timestamp("2020-07-01")).days / 365.25
    assert row["Harvested/yr"] == pytest.approx(0.1 / 1.05 / yrs, rel=1e-9)         # 0.1 lost on wealth 1.05 (2020-12-31)
    assert row["TaxDeferred/yr"] == pytest.approx(CG_RATE * 0.10 / yrs, rel=1e-9)  # 0.2 x 0.10 of the starting capital
    assert rep.results["top"].harvest is not None
    assert rep.table.loc[rep.benchmark_label, ["Harvested/yr", "TaxDeferred/yr"]].isna().all()
    off = lab.backtest([sc], start="2020-07-01", tax=tax(TaxLossHarvest(report_deferral=False)))
    assert "Harvested/yr" in off.table.columns and "TaxDeferred/yr" not in off.table.columns
    none = lab.backtest([sc], start="2020-07-01", tax=tax())
    assert not {"Harvested/yr", "TaxDeferred/yr"} & set(none.table.columns)


@pytest.mark.parametrize("repl", ["cash", "stay_out", "similar"])
@pytest.mark.parametrize("when", ["year_end", "rebalance", "both"])
def test_synthetic_market_runs_in_every_mode(when, repl):
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8)
    h = TaxLossHarvest(threshold_pct=0.05, when=when, replacement=repl)
    rep = lab.backtest(["dogs"], rebalance_months=6, tax=TaxConfig(model=MODEL, harvest=h))
    res = rep.results["dogs"]
    assert np.isfinite(res.after_tax_curve).all() and (res.tax["wealth_after_tax"] > 0).all()
    assert list(res.harvest.columns) == HARVEST_COLS
    assert (res.harvest["denied"] <= res.harvest["loss"] + 1e-12).all()
    base = lab.backtest(["dogs"], rebalance_months=6, tax=TaxConfig(model=MODEL))
    assert base.results["dogs"].harvest is None


# --------------------------------------------------------------------------- Q3: second pick, avoidance, exits
# Q3: describe(), the alias and on_repick
def test_q3_describe_alias_and_on_repick_validation():
    assert TaxLossHarvest(replacement="second_pick").describe() == "on (threshold 10%, second pick, 30-day window)"
    assert TaxLossHarvest(replacement="similar").describe() == "on (threshold 10%, second pick, 30-day window)"
    assert TaxLossHarvest(threshold_pct=0.125, replacement="stay_out", superficial_days=45).describe() == \
        "on (threshold 12.5%, stay out, 45-day window)"
    assert "repurchase allowed" in TaxLossHarvest(on_repick="allow_denial").describe()
    assert TaxLossHarvest().on_repick == "avoid" and TaxConfig().harvest is None       # explicit enable only
    with pytest.raises(ValueError):
        TaxLossHarvest(on_repick="ignore")
    h = TaxLossHarvest(on_repick="allow_denial")
    assert from_dict(to_dict(h)) == h


# Q3: a re-pick inside the window is swapped for the next-ranked unblocked name; outside it the original comes back
def test_q3_rebalance_repick_inside_the_window_takes_the_second_pick():
    caps = {d: dict(A=3, B=2, C=1) for d in ("2020-01-01", "2021-01-01", "2022-01-03")}
    mk = market(dict(A=A_DROP, B=FLAT, C=step(("2021-02-01", 1.5))), caps)
    res = go(mk, 2, TaxLossHarvest(), weighting="cap")
    # 2020: A 0.6 (basis 0.6), B 0.4. 2020-12-31: A = 0.6 x 0.8 = 0.48, loss 0.12 (20%); wealth 0.88, A's 0.48 waits in cash
    r = res.harvest.iloc[0]
    assert (r["source"], r["name"], r["date"]) == ("harvest", "A", pd.Timestamp("2020-12-31"))
    assert r["loss"] == pytest.approx(0.12, rel=1e-12) and r["unrealized_pct"] == pytest.approx(0.2, rel=1e-12)
    assert r["blocked_until"] == pd.Timestamp("2021-01-30")                      # sale + 30 days
    # 2021-01-01: the screen wants A, B (cap weights 3:2) -- A is inside its window: C, the next ranked, takes its place;
    # cap weights of B and C are 2/3 and 1/3 of the 0.88: B 0.586667, C 0.293333
    assert res.holdings[pd.Timestamp("2021-01-01")] == ["B", "C"]
    assert r["replacement_name"] == "C" and r["denied"] == 0
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-0.12 * CAP, rel=1e-12)       # the loss stands
    # C rises 50% on 2021-02-01: 0.88 x (2/3 + 1/3 x 1.5) = 0.88 x 7/6 -- the weights above, to the cent
    assert res.curve.loc["2021-02-01"] == pytest.approx(0.88 * 7 / 6, rel=1e-12)
    assert res.curve.loc["2021-01-29"] == pytest.approx(0.88, rel=1e-12)
    # 2022-01-03 is 368 days after the sale: A is bought back, the loss is not denied
    assert res.holdings[pd.Timestamp("2022-01-03")] == ["A", "B"]
    r = res.harvest.iloc[0]
    assert r["rebought"] == pd.Timestamp("2022-01-03") and r["days_out"] == 368 and r["denied"] == 0


def test_q3_blocked_dates_and_the_blocklist_object():
    from screenlab.backtest import simulate
    from screenlab.pipeline import TaxStage
    caps = {"2020-01-01": dict(A=3, B=2, C=1), "2021-01-01": dict(A=3, B=2, C=1)}
    mk = market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)
    cfg = Config(cost_bps=0, min_holdings=1, tax=tax(TaxLossHarvest()))
    hv = cfg.tax.harvest.start(mk, TaxStage(cfg.tax, CAP), screen(1), cfg.construction, 1)
    hv.blocked_until["A"] = pd.Timestamp("2021-01-30")
    assert hv.blocked(pd.Timestamp("2021-01-30")) == {"A"} and hv.blocked(pd.Timestamp("2021-01-31")) == set()
    assert hv.blocked("2020-12-31") == {"A"}
    res = simulate(screen(1), mk, cfg, [pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01")])
    assert res.harvest.iloc[0]["blocked_until"] == pd.Timestamp("2021-01-30")


# Q3: "allow_denial" is the old arithmetic: bought back at once, the loss denied and added to the ACB
def test_q3_allow_denial_reproduces_the_denial_arithmetic():
    caps = {d: dict(A=3, B=2, C=1) for d in ("2020-01-01", "2021-01-01", "2022-01-03")}
    mk = market(dict(A=A_DROP, B=FLAT, C=FLAT), caps)
    avoid = go(mk, 1, TaxLossHarvest())
    allow = go(mk, 1, TaxLossHarvest(on_repick="allow_denial"))
    assert avoid.holdings[pd.Timestamp("2021-01-01")] == ["B"] and allow.holdings[pd.Timestamp("2021-01-01")] == ["A"]
    r = allow.harvest.iloc[0]
    assert r["denied"] == pytest.approx(0.2, rel=1e-12) and r["acb_added"] == pytest.approx(0.2, rel=1e-12)
    assert r["rebought"] == pd.Timestamp("2021-01-01") and pd.isna(r["blocked_until"]) and r["replacement_name"] is None
    assert allow.tax.loc[2020, "realized_losses"] == pytest.approx(0, abs=1e-6)
    assert avoid.tax.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)


# --- exits: an exit policy that parks in cash while the flag is on
class _Flag:
    """Risk flag hand-set: on for the calendar days in [start, end]."""
    name = "flag"

    def __init__(self, start, end):
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)

    def compute(self, mkt, macro=None):
        return pd.Series(((mkt.index >= self.start) & (mkt.index <= self.end)).astype(float), index=mkt.index)


def exit_policy(on_from, on_to):
    from screenlab import ExitPolicy, ExitScreen
    return ExitPolicy(ExitScreen([_Flag(on_from, on_to)], threshold=0.5, lag=0), switch_cost_bps=0.0)


def exit_market():
    # A falls 20% on 2020-07-01, is back at 1.0 on 2020-09-20 and 1.1 on 2020-10-05; B and C are flat
    a = step(("2020-07-01", 0.8), ("2020-09-20", 1.0), ("2020-10-05", 1.1))
    caps = {d: dict(A=3, B=2, C=1) for d in ("2020-01-01", "2021-01-01", "2022-01-03")}
    return market(dict(A=a, B=FLAT, C=FLAT), caps)


def go_exit(clear, harvest, **kw):
    """Flag on from 2020-09-01 (S) to the day before `clear`: out on S, back on `clear`."""
    pol = exit_policy("2020-09-01", pd.Timestamp(clear) - pd.Timedelta(days=1))
    c = Config(rebalance_months=12, cost_bps=0, min_holdings=1, exit=pol, tax=tax(harvest), **kw)
    return run(screen(1), exit_market(), c, start="2020-01-01")


def test_q3_harvest_with_an_exit_policy_no_longer_raises():
    res = go_exit("2020-09-11", TaxLossHarvest())
    assert res.harvest is not None and res.exposure.loc["2020-09-02"] == 0.0 and res.exposure.loc["2020-09-11"] == 1.0


def test_q3_exit_loss_is_a_harvest_and_the_reentry_inside_the_window_takes_the_second_pick():
    S = pd.Timestamp("2020-09-01")
    res = go_exit("2020-09-11", TaxLossHarvest())                   # back in on S + 10
    h = res.harvest
    assert list(h.columns) == HARVEST_COLS and len(h) == 1
    r = h.iloc[0]
    # A: bought at 1.0 (basis 1.0), worth 0.8 when the flag sells it into cash on S: a 0.2 loss realized by the switch
    assert (r["source"], r["name"], r["date"]) == ("exit", "A", S)
    assert r["loss"] == pytest.approx(0.2, rel=1e-12) and r["unrealized_pct"] == pytest.approx(0.2, rel=1e-12)
    assert r["blocked_until"] == S + pd.Timedelta(days=30) == pd.Timestamp("2020-10-01")
    # S + 10: A would be bought back with the cash; it is blocked, B (next ranked, not held) takes the 0.8 instead
    assert r["replacement_name"] == "B" and r["denied"] == 0 and r["net_loss"] == pytest.approx(0.2, rel=1e-12)
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)
    c = res.curve
    assert c.loc["2020-09-10"] == pytest.approx(0.8) and c.loc["2020-09-21"] == pytest.approx(0.8)   # A's rebound is missed
    assert c.loc["2020-10-06"] == pytest.approx(0.8)
    assert r["rebought"] == pd.Timestamp("2021-01-01")                   # the screen's own rebalance, 487 days after S
    assert res.holdings[pd.Timestamp("2021-01-01")] == ["A"]
    # the same run with the denial allowed: A is bought back on S + 10 at 0.8 and the loss is denied
    den = go_exit("2020-09-11", TaxLossHarvest(on_repick="allow_denial"))
    d = den.harvest.iloc[0]
    assert d["source"] == "exit" and d["denied"] == pytest.approx(0.2, rel=1e-12) and d["rebought"] == pd.Timestamp("2020-09-11")
    assert d["days_out"] == 10 and den.tax.loc[2020, "realized_losses"] == pytest.approx(0, abs=1e-6)
    assert den.curve.loc["2020-09-21"] == pytest.approx(1.0) and den.curve.loc["2020-10-06"] == pytest.approx(1.1)


@pytest.mark.parametrize("clear, takes_a", [("2020-09-11", False), ("2020-10-01", False), ("2020-10-02", True)])
def test_q3_exit_reentry_window_edge(clear, takes_a):
    # S = 2020-09-01: S + 30 = 10-01 is still inside the window, S + 31 = 10-02 is outside
    res = go_exit(clear, TaxLossHarvest())
    r = res.harvest.iloc[0]
    assert r["blocked_until"] == pd.Timestamp("2020-10-01")
    if takes_a:                           # A bought on S + 31 at 1.0 with the 0.8; 1.1 on 10-05
        assert res.curve.loc["2020-10-02"] == pytest.approx(0.8) and res.curve.loc["2020-10-05"] == pytest.approx(0.88)
        assert r["denied"] == 0 and r["rebought"] == pd.Timestamp("2020-10-02") and pd.isna(r["replacement_name"])
        assert r["days_out"] == 31
    else:
        assert res.curve.loc["2020-10-05"] == pytest.approx(0.8) and r["replacement_name"] == "B"
    assert res.tax.loc[2020, "realized_losses"] == pytest.approx(-0.2 * CAP, rel=1e-12)


def test_q3_second_pick_mode_swaps_the_original_back_after_the_window():
    res = go_exit("2020-09-11", TaxLossHarvest(replacement="second_pick"))
    r = res.harvest.iloc[0]
    # B holds the 0.8 from S + 10; on S + 31 (10-02) it is swapped for A at 1.0: 0.8 of A, 1.1 on 10-05
    assert r["replacement_name"] == "B" and r["rebought"] == pd.Timestamp("2020-10-02") and r["denied"] == 0
    assert res.curve.loc["2020-10-02"] == pytest.approx(0.8) and res.curve.loc["2020-10-05"] == pytest.approx(0.88)
    stay = go_exit("2020-09-11", TaxLossHarvest(replacement="stay_out"))       # no second pick: the 0.8 waits in cash
    assert stay.curve.loc["2020-10-06"] == pytest.approx(0.8) and stay.harvest.iloc[0]["denied"] == 0


# --------------------------------------------------------------------------- units: the records are in the ledger's units
def paid_market():
    """A doubles by June 2020 (the 2020-07-01 rebalance sells it for B: a gain of 1.0 = $100,000, tax $20,000 paid from the
    portfolio at the 2020 year end, factor 0.9); B falls to 0.8 of its cost in September 2021 and is harvested at the 2021
    year end: a pre-tax loss of 0.4 which the ledger books as 0.4 x 0.9 x capital."""
    caps = {d: dict(A=3, B=2, C=1) if d == "2020-01-01" else dict(A=1, B=3, C=1)
            for d in ("2020-01-01", "2020-07-01", "2021-01-01", "2021-07-01", "2022-01-03", "2022-07-01")}
    return market(dict(A=step(("2020-06-01", 2.0)), B=step(("2021-09-01", 0.8)), C=FLAT), caps)


def go_paid(harvest):
    c = Config(rebalance_months=6, cost_bps=0, min_holdings=1, tax=TaxConfig(model=MODEL, capital=CAP, pay_from_portfolio=True,
                                                                           harvest=harvest))
    return run(screen(1), paid_market(), c, start="2020-01-01")


def test_harvest_records_are_in_ledger_units_when_tax_is_paid_from_the_account():
    res = go_paid(TaxLossHarvest())
    assert res.tax.loc[2020, "tax_paid"] == pytest.approx(0.20 * CAP, rel=1e-12)
    assert res.after_tax_curve.loc["2020-12-31"] / res.curve.loc["2020-12-31"] == pytest.approx(0.9, rel=1e-12)
    h = res.harvest
    r = h[h["source"] == "harvest"].iloc[0]
    assert (r["name"], r["date"]) == ("B", pd.Timestamp("2021-12-31"))
    assert r["loss_pre_tax"] == pytest.approx(0.4, rel=1e-12) and r["acct_factor"] == pytest.approx(0.9, rel=1e-12)
    assert r["loss"] == pytest.approx(0.36, rel=1e-12) and r["denied"] == 0 and r["net_loss"] == pytest.approx(0.36, rel=1e-12)
    ledger = -res.tax.loc[2021, "realized_losses"]
    assert ledger == pytest.approx(0.36 * CAP, rel=1e-12)
    assert ledger / ((h["loss"] - h["denied"])[h["date"].dt.year == 2021].sum() * CAP) == pytest.approx(1.0, abs=1e-12)   # exact
    # the report column is still a share of the pre-tax wealth on the day: unchanged by the units
    w = res.curve.loc["2021-12-31"]
    assert harvested_per_year(res) * ((res.curve.index[-1] - res.curve.index[0]).days / 365.25) == pytest.approx(0.4 / w, rel=1e-12)
    # first-year and outside-paid runs: nothing to rescale
    outside = run(screen(1), paid_market(), Config(rebalance_months=6, cost_bps=0, min_holdings=1, tax=TaxConfig(
        model=MODEL, capital=CAP, pay_from_portfolio=False, harvest=TaxLossHarvest())), start="2020-01-01").harvest
    assert (outside["acct_factor"] == 1.0).all() and (outside["loss"] == outside["loss_pre_tax"]).all()


def test_denied_amount_is_in_ledger_units_too():
    res = go_paid(TaxLossHarvest(on_repick="allow_denial"))      # B is picked again on 2022-01-03: the whole loss is denied
    r = res.harvest[res.harvest["source"] == "harvest"].iloc[0]
    assert r["rebought"] == pd.Timestamp("2022-01-03")
    assert r["denied"] == pytest.approx(0.36, rel=1e-12) and r["denied_pre_tax"] == pytest.approx(0.4, rel=1e-12)
    assert r["acb_added"] == pytest.approx(0.36, rel=1e-12) and r["net_loss"] == pytest.approx(0, abs=1e-15)
    assert res.tax.loc[2021, "realized_losses"] == pytest.approx(0, abs=1e-6)


# --------------------------------------------------------------------------- the real panel (local use only)
PANEL = Path(__file__).resolve().parents[1] / "data" / "panel" / "prices.parquet"


@pytest.mark.skipif(not PANEL.exists(), reason="the S&P 500 panel is absent (make data-panel, local use only)")
def test_real_panel_harvest_records_reconcile_with_the_ledger(monkeypatch):
    """Dogs, taxable Alberta account, harvesting on, 1990-2022. The ledger cannot tell sources apart and also books the
    write-offs of delisted names (and the final liquidation), which are not sales of the stage: so per year
    ledger = capital x factor x (sum of ALL records' net loss, pre-tax + other losses the ledger booked), to 1e-6. The
    brief's ratio of the harvest-source records alone is not 1 for that reason (screen-source sales and delistings)."""
    import sys
    from screenlab.pipeline import TaxStage
    from screenlab.taxes import CanadianTax
    other = {}                                                    # year -> losses booked outside the trades (pre-tax units)
    orig = TaxStage._gains

    def spy(self, date, gains):
        g = np.asarray(gains, float)
        if sys._getframe(1).f_code.co_name != "spy" and g[g < 0].sum() < 0:      # trade losses arrive through the harvest spy
            other[pd.Timestamp(date).year] = other.get(pd.Timestamp(date).year, 0.0) + g[g < 0].sum()
        orig(self, date, gains)
    monkeypatch.setattr(TaxStage, "_gains", spy)
    lab = Lab.real_sp500(start="1990-01-01", end="2022-12-30")
    tx = TaxConfig(harvest=TaxLossHarvest(replacement="second_pick", report_deferral=False),
                   model=CanadianTax("AB", other_income=90000),
                   capital=100000, account="taxable")
    r = lab.backtest(["dogs"], rebalance_months=12, tax=tx).results["dogs"]
    h = r.harvest
    assert (h["denied"] > 0).any() and (h["source"] == "screen").any()
    ratio = r.after_tax_curve / r.curve
    last = r.curve.groupby(r.curve.index.year).apply(lambda s: s.index[-1])
    fac = {y: (1.0 if y - 1 not in last.index else float(ratio.loc[last[y - 1]])) for y in last.index}
    assert fac[2021] < 0.5                                         # the old pre-tax scale was off by this much
    yr = h["date"].dt.year
    net_pre = (h["loss_pre_tax"] - h["denied_pre_tax"]).groupby(yr).sum()
    for y in r.tax.index:
        want = 100000 * fac[y] * (net_pre.get(y, 0.0) - other.get(y, 0.0))
        assert -r.tax.loc[y, "realized_losses"] == pytest.approx(want, rel=1e-6, abs=1e-6), y
    ok = ((h["loss"] - h["denied"]).groupby(yr).sum() * 100000 - 100000 * pd.Series(fac) * net_pre).abs().max()
    assert ok < 1e-6                                               # records in ledger units = pre-tax x the year's factor
    assert h.loc[h["days_out"].isna(), "rebought"].isna().all()                          # no closed window = never bought back
    assert (h["days_out"].isna() == h["days_out_open"].notna()).all() and (h["days_out"].dropna() >= 0).all()
    nd = HarvestRun.name_days_out(r)
    assert nd["n_closed"] + nd["n_open"] == (h["source"] == "harvest").sum() and nd["open"] > nd["closed"]
