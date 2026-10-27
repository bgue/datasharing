"""The Globe and Mail screen portfolios: exact picks on a hand-built snapshot, buyback / EV/EBIT / trailing
stat arithmetic, runner-ups, and backtests of GLOBE_PORTFOLIOS.

    python -m pytest screenlab/tests/test_globe_portfolios.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import GLOBE_PORTFOLIOS, Lab, Market, REGISTRY
from screenlab.data import add_ratios
from screenlab.screens import (basic_buyback, bluechip_dividend_growth, frugal_dividend, momentum_6m,
                               momentum_12m, screaming_value, stable_dividend, stable_high_yield)

NEW = ["screaming_value", "stable_dividend", "frugal_dividend", "stable_high_yield", "basic_buyback",
       "momentum_6m", "momentum_12m", "bluechip_dividend_growth"]

# ticker: (market_cap, dividend per share at price 10, vol_260d, pe, ev_ebit, buyback, mom_6m, mom_12m, div_years)
# B pays no dividend; D has no positive EBIT; H has negative EPS; Z is the smallest name with the best of everything.
ROWS = {
    "A": (120, 1.0, .10, 8, 5, .05, .10, .5, 30),
    "B": (110, 0.0, .05, 5, 3, .02, .30, .1, 40),
    "C": (100, 0.5, .20, 12, 8, -.03, -.10, .6, 5),
    "D": (90, 0.8, .15, 6, None, .10, .25, .2, 20),
    "E": (80, 0.4, .12, 20, 4, .00, .05, .7, 15),
    "F": (70, 0.6, .08, 15, 6, .07, .20, .3, 12),
    "G": (60, 0.2, .30, 4, 7, .01, .40, .0, 50),
    "H": (50, 0.9, .18, None, 2, .04, .15, .8, 25),
    "I": (40, 0.3, .25, 9, 9, .08, .00, .4, 3),
    "J": (30, 0.7, .22, 7, 10, .03, .35, .15, 11),
    "K": (20, 0.1, .11, 10, 12, .06, .12, .05, 9),
    "L": (10, 0.5, .09, 11, 11, .09, .22, .9, 18),
    "Z": (5, 2.0, .01, 1, 1, .50, 9.0, 9.0, 99),
}
DECLINING_EPS = {"D", "F"}


@pytest.fixture(scope="module")
def snap():
    rows = []
    for t, (cap, div, vol, pe, evx, bb, m6, m12, dy) in ROWS.items():
        eps = 10 / pe if pe else -1.0
        rows.append(dict(ticker=t, price=10.0, market_cap=cap, dividend=div, vol_260d=vol, eps=eps,
                         eps_prev=eps * (1.1 if t in DECLINING_EPS else 0.9),
                         ebit=120 / evx if evx else -5.0, enterprise_value=120.0,
                         shares=100 * (1 - bb), shares_prev=100.0, mom_6m=m6, mom_12m=m12, div_years=dy))
    return add_ratios(pd.DataFrame(rows).set_index("ticker"))


U = 12   # universe: the 12 largest, which leaves Z out


def test_screaming_value(snap):
    assert screaming_value(snap, n=3, universe=U) == ["H", "B", "E"]          # D (EBIT < 0) and Z (outside) excluded
    assert "Z" in screaming_value(snap, n=3, universe=13)


def test_stable_dividend(snap):
    assert stable_dividend(snap, n=4, universe=U) == ["F", "L", "A", "K"]     # B (lowest vol) pays nothing


def test_frugal_dividend_applies_three_steps_in_order(snap):
    assert frugal_dividend(snap, n=3, universe=U, vol_pool=6) == ["D", "A", "K"]
    # step order matters: P/E first, then volatility, gives something else
    payers = snap.nlargest(U, "market_cap").query("dividend > 0")
    wrong = payers.nsmallest(6, "pe").nsmallest(3, "vol_260d").index.tolist()
    assert wrong != ["D", "A", "K"]
    # the volatility pool is a real cut: a wide pool lets the cheap, volatile names in
    assert frugal_dividend(snap, n=3, universe=U, vol_pool=50) == ["G", "D", "J"]
    # H has negative EPS (P/E NaN): never picked even though its volatility is low enough to be in the pool
    assert "H" not in frugal_dividend(snap, n=10, universe=U, vol_pool=50)


def test_stable_high_yield(snap):
    assert stable_high_yield(snap, n=3, universe=U, vol_pool=6) == ["A", "D", "F"]


def test_basic_buyback(snap):
    assert basic_buyback(snap, n=3, universe=U) == ["D", "L", "I"]
    full = basic_buyback(snap, n=12, universe=U)
    assert "C" not in full and "E" not in full and len(full) == 10          # issuers and zero change excluded


def test_momentum(snap):
    assert momentum_6m(snap, n=3, universe=U) == ["G", "J", "B"]
    assert momentum_12m(snap, n=3, universe=U) == ["L", "H", "E"]


def test_bluechip_dividend_growth(snap):
    # payers with >= 10 dividend years and EPS up (drops C, D, F, H, I, K, B): A E G J L, then 3 lowest vol
    assert bluechip_dividend_growth(snap, n=3, universe=U, min_div_years=10) == ["L", "A", "E"]


def test_registry_and_notes():
    for k in NEW:
        assert k in REGISTRY and REGISTRY[k].note
    assert "APPROXIMATION" in REGISTRY["bluechip_dividend_growth"].note
    assert REGISTRY["screaming_value"].n == 10 and REGISTRY["stable_dividend"].n == 20
    assert REGISTRY["basic_buyback"].n == 20 and REGISTRY["bluechip_dividend_growth"].n == 12


# --------------------------------------------------------------------------- ratios
def test_buyback_yield_and_ev_ebit_arithmetic():
    f = pd.DataFrame({"shares": [95.0, 100.0, 110.0, 50.0], "shares_prev": [100.0, 100.0, 100.0, 0.0],
                      "ebit": [50.0, 0.0, -10.0, 25.0], "enterprise_value": [500.0, 500.0, 500.0, 100.0],
                      "price": 10.0})
    r = add_ratios(f)
    assert r["buyback_yield"].iloc[:3].tolist() == pytest.approx([0.05, 0.0, -0.10])
    assert np.isnan(r["buyback_yield"].iloc[3])
    assert r["ev_ebit"].iloc[0] == pytest.approx(10.0) and r["ev_ebit"].iloc[3] == pytest.approx(4.0)
    assert r["ev_ebit"].iloc[[1, 2]].isna().all()
    assert "buyback_yield" not in add_ratios(f.drop(columns=["shares", "shares_prev"]))


# --------------------------------------------------------------------------- trailing stats
def _random_market(freq, n=700, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n) if freq == "B" else pd.date_range("2000-01-31", periods=n // 5, freq="ME")
    tri = pd.DataFrame(np.exp(np.cumsum(rng.normal(0.0004, 0.01, (len(idx), 3)), axis=0)), index=idx,
                       columns=list("XYZ"))
    fund = pd.DataFrame({"date": idx[0], "ticker": list(tri.columns), "price": 1.0, "market_cap": 1.0})
    return Market(add_ratios(fund), tri)


def test_trailing_stats_daily():
    m = _random_market("B")
    asof = m.index[-1]
    ts = m.trailing_stats(asof)
    tri = m.prices
    want_vol = np.log(tri.iloc[-261:]).diff().dropna().std(ddof=1) * np.sqrt(m.ppy)
    assert ts["vol_260d"].to_numpy() == pytest.approx(want_vol.to_numpy())
    for col, months in (("mom_6m", 6), ("mom_12m", 12)):
        j = tri.index.searchsorted(asof - pd.DateOffset(months=months))
        assert ts[col].to_numpy() == pytest.approx((tri.iloc[-1] / tri.iloc[j] - 1).to_numpy())
    # too little history: NaN, not a partial window
    early = m.trailing_stats(m.index[200])
    assert early["vol_260d"].isna().all() and early["mom_12m"].isna().all() and early["mom_6m"].notna().all()


def test_trailing_stats_monthly_uses_calendar_window():
    m = _random_market("ME", n=300)
    ts = m.trailing_stats(m.index[-1])
    assert ts["vol_260d"].to_numpy() == pytest.approx(ts["vol_1y"].to_numpy())
    want = np.log(m.prices.iloc[-13:]).diff().dropna().std(ddof=1) * np.sqrt(m.ppy)
    assert ts["vol_260d"].to_numpy() == pytest.approx(want.to_numpy())
    assert ts["mom_6m"].to_numpy() == pytest.approx((m.prices.iloc[-1] / m.prices.iloc[-7] - 1).to_numpy())
    assert ts["mom_12m"].to_numpy() == pytest.approx((m.prices.iloc[-1] / m.prices.iloc[-13] - 1).to_numpy())


# --------------------------------------------------------------------------- lab-level
@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


@pytest.mark.parametrize("screen", ["dogs", "frugal_dividend", "bluechip_dividend_growth"])
def test_runners_up(lab, screen):
    if screen == "bluechip_dividend_growth":      # only ~8 names qualify, so shrink n to leave runner-ups
        screen = REGISTRY[screen].with_overrides(n=5)
    base = lab.picks(screen)
    p = lab.picks(screen, runners_up=2)
    assert len(p) == len(base) + 2 and p["runner_up"].sum() == 2
    assert p.index[:len(base)].tolist() == base.index.tolist() and not p["runner_up"].iloc[:len(base)].any()
    sc, s = lab.screen(screen), lab.snapshot()
    nxt = [t for t in sc.ranked(s) if t not in set(base.index)][:2]
    assert p.index[-2:].tolist() == nxt and p["runner_up"].iloc[-2:].all()
    assert "runner_up" not in base


def test_globe_portfolios_backtest(lab):
    assert set(GLOBE_PORTFOLIOS) == set(NEW)
    for name, port in GLOBE_PORTFOLIOS.items():
        want = 12 if name == "bluechip_dividend_growth" else 1
        assert port.config_overrides["rebalance_months"] == want
    assert GLOBE_PORTFOLIOS["bluechip_dividend_growth"].config_overrides["hold_rule"].keep_within
    rep = lab.backtest(list(GLOBE_PORTFOLIOS.values()))
    assert set(NEW) < set(rep.table.index) and len(rep.table) == len(NEW) + 1
    assert rep.table.loc[NEW, "CAGR"].notna().all()
    assert (rep.table.loc[NEW, "AvgN"] > 0).all()


def test_sp500_momentum_6m_12m():
    pytest.importorskip("skfolio")
    from screenlab.realdata.bundled import sp500_lab
    lab = sp500_lab()
    rep = lab.backtest(["momentum_6m", "momentum_12m"], rebalance_months=1, start="1995-01-03")
    assert set(rep.table.index) == {"SP500", "momentum_6m", "momentum_12m"}
    assert rep.table.loc[["momentum_6m", "momentum_12m"], "CAGR"].notna().all()
    p6, p12 = lab.picks("momentum_6m", "2020-06-30"), lab.picks("momentum_12m", "2020-06-30")
    assert len(p6) == len(p12) == 5
