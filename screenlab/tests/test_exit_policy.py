"""Exit policy: exposure paths, re-entry modes, park assets, backtest integration, Lab/server plumbing.

    python -m pytest screenlab/tests/test_exit_policy.py -q
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab, Market
from screenlab import backtest
from screenlab.backtest import Config, simulate
from screenlab.exit_policy import ExitPolicy, Portfolio, as_policy, policy_overlay
from screenlab.exit_signals import ExitScreen, TrendBreak, Breadth
from screenlab.screens import Screen

DAY = 1 / 365.25


# --------------------------------------------------------------------------- helpers
@dataclass
class Fixed:
    """A hand-set risk score (1.0 = flagged, 0.0 = clear) as a legacy two-argument Signal."""
    flags: pd.Series
    name: str = "fixed"

    def compute(self, mkt, macro=None):
        return self.flags.reindex(mkt.index).astype(float)


def flat_market(n=20, **extra_indices):
    d = pd.date_range("2021-01-01", periods=n, freq="D")
    px = pd.DataFrame({t: 100.0 for t in "ABC"}, index=d)
    fund = pd.DataFrame({"date": [d[0]] * 3, "ticker": list("ABC"), "market_cap": [1.0] * 3})
    idx = pd.DataFrame(extra_indices, index=d) if extra_indices else None
    return Market(fund, px, idx), d


def flags_on(d, *bars):
    return pd.Series([i in bars for i in range(len(d))], index=d, dtype=float)


def policy_for(d, bars, **kw):
    scr = ExitScreen([Fixed(flags_on(d, *bars))], threshold=0.5, lag=kw.pop("lag", 0))
    return ExitPolicy(scr, **kw)


def expo(policy, mkt, reb=None):
    return policy.exposure(mkt, reb if reb is not None else [mkt.index[0]]).tolist()


# --------------------------------------------------------------------------- exposure paths
FLAGGED = (3, 4, 5, 6, 7, 14, 15, 16)          # flagged 3-7 and 14-16, clear from 8 and 17


def test_reentry_signal_immediate_and_lag():
    mkt, d = flat_market()
    pol = policy_for(d, FLAGGED)
    e = expo(pol, mkt)
    assert e == [1] * 3 + [0] * 5 + [1] * 6 + [0] * 3 + [1] * 3
    assert pol.reentry_dates == [d[8], d[17]]
    lagged = policy_for(d, FLAGGED, lag=1).exposure(mkt, [d[0]])
    assert lagged.tolist() == [1.0] + e[:-1]                      # signal at close t -> exposure from t+1
    assert policy_for(d, FLAGGED, lag=1, exposure_when_flagged=0.5).exposure(mkt, [d[0]]).min() == 0.5


def test_reentry_delay_days():
    mkt, d = flat_market()
    pol = policy_for(d, FLAGGED, reentry_delay_days=2)
    e = expo(pol, mkt)
    assert e == [1] * 3 + [0] * 7 + [1] * 4 + [0] * 5 + [1]      # clear 8 -> back at 10; clear 17 -> back at 19
    assert pol.reentry_dates == [d[10], d[19]]
    # a flag inside the confirmation window restarts the clock
    pol = policy_for(d, (3, 4, 9), reentry_delay_days=2)
    # clear from 5 -> back in at 7; flagged again at 9 (out), clear from 10 -> back in at 12
    assert expo(pol, mkt) == [1] * 3 + [0] * 4 + [1] * 2 + [0] * 3 + [1] * 8
    # a flag that returns before the delay is over keeps us out: clear 5, re-flag 6, clear 7 -> back at 9
    pol = policy_for(d, (3, 4, 6), reentry_delay_days=2)
    assert expo(pol, mkt) == [1] * 3 + [0] * 6 + [1] * 11


def test_reentry_steps():
    mkt, d = flat_market()
    pol = policy_for(d, (3, 4, 5, 6, 7), reentry_steps=3)
    e = expo(pol, mkt)
    assert e[:9] == [1, 1, 1, 0, 0, 0, 0, 0, pytest.approx(1 / 3)]
    assert e[9:11] == [pytest.approx(2 / 3), 1.0] and pol.reentry_dates == [d[10]]
    half = policy_for(d, (3, 4, 5, 6, 7), reentry_steps=3, exposure_when_flagged=0.5)
    assert expo(half, mkt)[3:11] == pytest.approx([0.5] * 5 + [0.5 + 0.5 / 3, 0.5 + 1 / 3, 1.0])
    # a flag in mid-ramp drops exposure back to the flagged level and the ramp starts over
    pol = policy_for(d, (3, 4, 5, 6, 7, 9), reentry_steps=3)
    e = expo(pol, mkt)
    assert e[8] == pytest.approx(1 / 3) and e[9] == 0 and e[10] == pytest.approx(1 / 3)
    assert e[12] == 1.0 and pol.reentry_dates == [d[12]]


def test_reentry_steps_follow_check_periods():
    mkt, d = flat_market(30)                                      # 2021-01-01 is a Friday: checks at bars 0, 7, 14, 21
    scr = ExitScreen([Fixed(flags_on(d, 3, 4, 5, 6, 7))], threshold=0.5, lag=0, check="W-FRI")
    pol = ExitPolicy(scr, reentry_steps=3)
    e = pol.exposure(mkt, [d[0]], state=flags_on(d, 3, 4, 5, 6, 7).astype(bool)).to_numpy()
    assert e[8] == pytest.approx(1 / 3) and e[13] == pytest.approx(1 / 3)
    assert e[14] == pytest.approx(2 / 3) and e[20] == pytest.approx(2 / 3) and e[21] == 1.0
    assert pol.reentry_dates == [d[21]]


def test_reentry_next_rebalance():
    mkt, d = flat_market()
    pol = policy_for(d, FLAGGED, reentry="next_rebalance")
    e = expo(pol, mkt, [d[0], d[12]])
    # out at 3; the flag clears at 8 but exposure waits for the rebalance at 12; out again at 14, no later rebalance
    assert e == [1] * 3 + [0] * 9 + [1] * 2 + [0] * 6
    assert pol.reentry_dates == [d[12]]
    # a flag still on at the rebalance keeps you out until it clears
    pol = policy_for(d, (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13), reentry="next_rebalance")
    assert expo(pol, mkt, [d[0], d[10]]) == [1] * 3 + [0] * 11 + [1] * 6


def test_reentry_signal_or_rebalance():
    mkt, d = flat_market()
    pol = policy_for(d, FLAGGED, reentry="signal_or_rebalance")
    assert expo(pol, mkt, [d[0], d[6]]) == [1] * 3 + [0] * 5 + [1] * 6 + [0] * 3 + [1] * 3   # signal at 8 beats rebalance
    long_flag = tuple(range(3, 14))
    pol = policy_for(d, long_flag, reentry="signal_or_rebalance")
    # flag clears at 14, the rebalance at 12 happens while it is still on -> back in at 14
    assert expo(pol, mkt, [d[0], d[12]]) == [1] * 3 + [0] * 11 + [1] * 6
    pol = policy_for(d, (3, 4, 5, 6, 7, 8, 9, 10), reentry="signal_or_rebalance", reentry_delay_days=5)
    # signal needs 5 clear days (bar 16), the rebalance at 12 comes first and the flag is clear
    assert expo(pol, mkt, [d[0], d[12]]) == [1] * 3 + [0] * 9 + [1] * 8


def test_policy_validation_and_wrapping():
    scr = ExitScreen([])
    for bad in (dict(reentry="never"), dict(reentry_steps=0), dict(reentry_delay_days=-1),
                dict(exposure_when_flagged=1.5)):
        with pytest.raises(ValueError):
            ExitPolicy(scr, **bad)
    assert isinstance(as_policy(scr), ExitPolicy) and as_policy(None) is None
    p = ExitPolicy(scr)
    assert as_policy(p) is p and p.reentry_dates == []
    with pytest.raises(TypeError):
        as_policy("x")


# --------------------------------------------------------------------------- exact backtest arithmetic
def run_flat(mkt, d, pol, cash_rate=0.0, rebal=None, end=None, exposure=None):
    scr = Screen("all3", lambda s, n: ["A", "B", "C"], 3)
    cfg = Config(cost_bps=0.0, min_holdings=3, cash_rate=cash_rate, exit=pol)
    return simulate(scr, mkt, cfg, rebal or [d[0]], end=end or d[-1], exposure=exposure)


def expected_curve(e, rates, sw_bps=0.0, pc_bps=0.0, equity=None):
    """value path for a flat (0%) equity sleeve and per-bar park returns `rates`."""
    v, out, prev = 1.0, [1.0], e[0]
    for t in range(1, len(e)):
        eq = 0.0 if equity is None else equity[t]
        de = abs(e[t] - e[t - 1])
        v *= 1 + e[t] * eq + (1 - e[t]) * rates[t] - (sw_bps + pc_bps) / 1e4 * de
        out.append(v)
    return out


def test_flat_sleeve_fixed_park_rate_and_costs():
    mkt, d = flat_market(10)
    pol = policy_for(d, (2, 3), exposure_when_flagged=0.5, park=0.0730, switch_cost_bps=10, park_cost_bps=4)
    res = run_flat(mkt, d, pol, cash_rate=0.05)                     # cash_rate must be ignored for a float park
    c = 1.073 ** DAY - 1
    e = [1, 1, .5, .5, 1, 1, 1, 1, 1, 1]
    assert res.exposure.tolist() == e
    assert res.curve.tolist() == pytest.approx(expected_curve(e, [c] * 10, 10, 4), rel=1e-12)
    assert res.switches == 2 and res.time_out == pytest.approx(0.2)
    assert res.park_curve.tolist() == pytest.approx([(1 + c) ** k for k in range(10)], rel=1e-12)
    assert res.exit is pol


def test_park_in_cash_uses_config_rate_and_mer():
    mkt, d = flat_market(10)
    pol = policy_for(d, (2, 3, 4), park="cash", switch_cost_bps=0)
    res = run_flat(mkt, d, pol, cash_rate=0.0365)
    c = 1.0365 ** DAY - 1
    assert res.curve.tolist() == pytest.approx(expected_curve([1, 1, 0, 0, 0, 1, 1, 1, 1, 1], [c] * 10), rel=1e-12)
    fee = policy_for(d, (2, 3, 4), park="cash", switch_cost_bps=0, park_mer=0.02)
    res = run_flat(mkt, d, fee, cash_rate=0.0365)
    c2 = (1 + c) * 0.98 ** DAY - 1
    assert res.curve.tolist() == pytest.approx(expected_curve([1, 1, 0, 0, 0, 1, 1, 1, 1, 1], [c2] * 10), rel=1e-12)


def test_park_in_a_market_series_with_mer_and_cost():
    d = pd.date_range("2021-01-01", periods=10, freq="D")
    tbill = pd.Series(100 * np.cumprod(np.r_[1, np.linspace(1.001, 1.004, 9)]), index=d)
    mkt, _ = flat_market(10, TBILL=tbill)
    pol = policy_for(d, (2, 3, 4, 5), park="TBILL", park_mer=0.01, park_cost_bps=6, switch_cost_bps=2)
    res = run_flat(mkt, d, pol, cash_rate=0.05)
    r = tbill.pct_change().fillna(0.0).to_numpy()
    rates = (1 + r) * 0.99 ** DAY - 1
    e = [1, 1, 0, 0, 0, 0, 1, 1, 1, 1]
    assert res.curve.tolist() == pytest.approx(expected_curve(e, rates, 2, 6), rel=1e-12)
    # the park curve is the fund: index growth net of the fee
    assert res.park_curve.iloc[-1] == pytest.approx(np.prod(1 + rates[1:]), rel=1e-12)
    with pytest.raises(KeyError):
        run_flat(mkt, d, policy_for(d, (2,), park="NOPE"))


def test_reentry_steps_and_delay_in_the_curve():
    mkt, d = flat_market(12)
    pol = policy_for(d, (2, 3), reentry_delay_days=1, reentry_steps=2, park=0.0365, switch_cost_bps=5)
    res = run_flat(mkt, d, pol)
    e = [1, 1, 0, 0, 0, .5, 1, 1, 1, 1, 1, 1]                      # clear at 4, delay 1 -> first step at 5, full at 6
    assert res.exposure.tolist() == pytest.approx(e)
    c = 1.0365 ** DAY - 1
    assert res.curve.tolist() == pytest.approx(expected_curve(e, [c] * 12, 5), rel=1e-12)
    assert res.switches == 3


def test_default_path_untouched():
    mkt, d = flat_market(8)
    base = run_flat(mkt, d, None)
    assert base.curve.tolist() == pytest.approx([1.0] * 8) and base.exit is None
    assert base.time_out == 0.0 and base.park_curve is None and base.switches == 0


# --------------------------------------------------------------------------- rescreen on re-entry
def rescreen_market():
    d = pd.date_range("2021-01-01", periods=16, freq="D")
    growth = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.05, "E": 1.05}
    px = pd.DataFrame({t: 100.0 * np.r_[1.0, np.full(15, g)].cumprod() for t, g in growth.items()}, index=d)
    fund = pd.DataFrame({"date": [d[0]] * 5, "ticker": list("ABCDE"), "market_cap": [1.0] * 5,
                         "price": [130.0, 120.0, 110.0, 100.0, 90.0]})
    scr = Screen("top3", lambda s, n: s.nlargest(3, "price").index.tolist(), 3)
    return Market(fund, px), d, scr


def test_rescreen_on_reentry_adds_a_rebalance_and_changes_holdings():
    mkt, d, scr = rescreen_market()
    # flagged 3-7, clear from 8 (lag 0); only one scheduled rebalance (annual) so the extra date is the re-entry
    def cfg(rescreen):
        pol = policy_for(d, (3, 4, 5, 6, 7), rescreen_on_reentry=rescreen)
        return Config(cost_bps=100.0, min_holdings=3, exit=pol)
    plain = backtest.run(scr, mkt, cfg(False), start=d[0])
    resc = backtest.run(scr, mkt, cfg(True), start=d[0])
    assert list(plain.holdings) == [d[0]] and list(resc.holdings) == [d[0], d[8]]
    assert set(plain.holdings[d[0]]) == {"A", "B", "C"} == set(resc.holdings[d[0]])
    assert set(resc.holdings[d[8]]) == {"D", "E", "A"}               # D and E overtook B and C while we were out
    assert resc.costs.loc[d[8]] > 0 and plain.costs.sum() < resc.costs.sum()
    assert resc.exposure.loc[d[8]] == 1.0 and resc.exposure.loc[d[7]] == 0.0
    # not a rebalance date when exposure never returns to 1
    never = backtest.run(scr, mkt, Config(cost_bps=100.0, min_holdings=3,
                         exit=policy_for(d, tuple(range(3, 16)), rescreen_on_reentry=True)), start=d[0])
    assert list(never.holdings) == [d[0]]


def test_trade_cost_scales_with_exposure_held_at_the_rebalance():
    mkt, d, scr = rescreen_market()
    pol = policy_for(d, tuple(range(3, 16)), exposure_when_flagged=0.5, switch_cost_bps=0)
    cfg = Config(cost_bps=100.0, min_holdings=3, exit=pol)
    res = simulate(scr, mkt, cfg, [d[0], d[10]], end=d[-1])
    cost = 4 / 3 * 0.01 * 0.5                                        # B,C out, D,E in: 4/3 of weight, half invested
    assert res.costs.loc[d[10]] == pytest.approx(cost)
    ratio = res.curve.loc[d[11]] / res.curve.loc[d[10]]
    # new sleeve D, E, A over d10 -> d11: (5% + 5% + 0) / 3 at half exposure; other half in 0% cash
    assert ratio == pytest.approx((1 - cost) * (1 + 0.5 * 0.1 / 3), rel=1e-12)


# --------------------------------------------------------------------------- Lab-level
@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def synth_policy(**kw):
    scr = ExitScreen([TrendBreak("SYN_CAPWT", window_months=6), Breadth()], threshold=0.6)
    return ExitPolicy(scr, **kw)


def test_with_and_without_labels_and_columns(lab):
    pol = synth_policy(reentry="signal_or_rebalance", reentry_delay_days=30, reentry_steps=2)
    rep = lab.backtest(["dogs", "graham_defensive"], exit=pol, with_and_without=True, cash_rate=0.02)
    names = list(rep.results)
    assert names == ["dogs", "dogs+exit", "graham_defensive", "graham_defensive+exit"]
    t = rep.table
    assert {"TimeOut", "Switches/yr"} <= set(t.columns)
    assert t.loc[["dogs", "graham_defensive", "SYN_CAPWT"], ["TimeOut", "Switches/yr"]].isna().all().all()
    assert 0 < t.loc["dogs+exit", "TimeOut"] < 1 and t.loc["dogs+exit", "Switches/yr"] > 0
    assert "TimeOut" in str(rep) and "dogs+exit" in str(rep)
    # without the exit the curve is the plain backtest
    plain = lab.backtest(["dogs"], cash_rate=0.02)
    assert rep.results["dogs"].curve.to_numpy() == pytest.approx(plain.results["dogs"].curve.to_numpy())
    assert "TimeOut" not in plain.table.columns and "Switches/yr" not in plain.table.columns


def test_end_to_end_synthetic_with_rescreen(lab):
    pol = synth_policy(reentry="signal", reentry_delay_days=30, reentry_steps=2, rescreen_on_reentry=True,
                       park=0.03, park_mer=0.001)
    rep = lab.backtest(["dogs"], exit=pol, with_and_without=True)
    plain, res = rep.results["dogs"], rep.results["dogs+exit"]
    assert res.exposure.between(0, 1).all() and res.exposure.index.equals(res.curve.index)
    assert 0 < res.time_out < 1 and res.switches > 0 and np.isfinite(res.curve).all()
    assert res.park_curve.iloc[0] == 1.0 and res.park_curve.iloc[-1] == pytest.approx(
        (1.03 * 0.999 ** 1) ** ((res.curve.index[-1] - res.curve.index[0]).days / 365.25), rel=2e-3)
    extra = set(res.holdings) - set(plain.holdings)
    assert extra and extra <= set(pol.reentry_dates)                 # re-entries became rebalance dates
    assert set(plain.holdings) <= set(res.holdings)
    assert res.curve.iloc[-1] != plain.curve.iloc[-1]
    # a policy that never reduces exposure reproduces the plain curve (no switches, no extra rebalances)
    same = lab.backtest(["dogs"], exit=synth_policy(exposure_when_flagged=1.0, rescreen_on_reentry=True))
    assert same.results["dogs"].curve.to_numpy() == pytest.approx(plain.curve.to_numpy())


def test_portfolio_objects_carry_their_own_policy(lab):
    cash = synth_policy()
    parked = synth_policy(park=0.05, switch_cost_bps=20)
    rep = lab.backtest([Portfolio("dogs_a", "dogs", exit=cash), Portfolio("dogs_b", "dogs", exit=parked),
                        Portfolio("dogs_6m", "dogs", rebalance_months=6), "graham_defensive"],
                       cash_rate=0.01)
    assert list(rep.results) == ["dogs_a", "dogs_b", "dogs_6m", "graham_defensive"]
    assert rep.results["dogs_a"].exit is cash and rep.results["dogs_b"].exit is parked
    assert rep.results["dogs_6m"].exit is None and len(rep.results["dogs_6m"].holdings) > len(rep.results["graham_defensive"].holdings)
    assert rep.table.loc["dogs_a", "TimeOut"] == pytest.approx(rep.table.loc["dogs_b", "TimeOut"])
    assert rep.table.loc["dogs_a", "Switches/yr"] == rep.table.loc["dogs_b", "Switches/yr"]
    assert rep.results["dogs_b"].curve.iloc[-1] != rep.results["dogs_a"].curve.iloc[-1]
    assert pd.isna(rep.table.loc["dogs_6m", "TimeOut"])
    # a call-level ExitScreen is wrapped in a default policy and Portfolios without one inherit it
    r2 = lab.backtest([Portfolio("dogs_x", "dogs")], exit=cash.screen)
    assert isinstance(r2.results["dogs_x"].exit, ExitPolicy) and r2.results["dogs_x"].exit.park == "cash"


def test_portfolio_stop_signal_gets_the_fully_invested_curve(lab):
    exit_inputs = pytest.importorskip("screenlab.exit_inputs")
    stop = ExitPolicy(ExitScreen([exit_inputs.PortfolioStop(drawdown=0.05)], threshold=0.5))
    assert stop.needs_curve and not synth_policy().needs_curve
    rep = lab.backtest(["dogs"], exit=stop, with_and_without=True)
    assert rep.results["dogs+exit"].switches > 0


def test_exit_eval_and_calibration_accept_policy_keys(lab):
    pol = synth_policy(reentry="next_rebalance", reentry_steps=2, park=0.04)
    ev = lab.exit_eval(pol)
    assert ev.overlay.switches > 0 and ev.overlay.exposure.between(0, 1).all()
    plain = lab.exit_eval(pol.screen)
    assert not np.allclose(ev.overlay.curve.to_numpy(), plain.overlay.curve.to_numpy())
    grid = {"threshold": [0.6, 0.8], "reentry": ["signal", "next_rebalance"], "reentry_steps": [1, 2],
            "reentry_delay_days": [0, 30], "park": ["cash", 0.03], "exposure_when_flagged": [0.0, 0.5]}
    res = lab.calibrate_exit(pol.screen, {k: grid[k] for k in ("reentry", "reentry_steps", "park",
                                                              "exposure_when_flagged")})
    assert len(res.table) == 16 and {"reentry", "park", "reentry_steps"} <= set(res.table.columns)
    assert isinstance(res.policy, ExitPolicy) and res.policy.reentry == res.best["reentry"]
    assert res.policy.park == res.best["park"] and res.policy.exposure_when_flagged == res.best["exposure_when_flagged"]
    assert res.table["Switches"].nunique() > 1
    res2 = lab.calibrate_exit(pol.screen, {"threshold": grid["threshold"], "reentry_delay_days": grid["reentry_delay_days"]},
                              policy=ExitPolicy(pol.screen, park=0.03, reentry="signal_or_rebalance"))
    assert len(res2.table) == 4 and res2.policy.park == 0.03 and res2.policy.reentry == "signal_or_rebalance"
    with pytest.raises(ValueError):
        lab.calibrate_exit(pol.screen, {"bogus": [1]})
