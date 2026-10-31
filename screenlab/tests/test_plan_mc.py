"""plan_montecarlo (montecarlo/plan_mc.py): exact tests on degenerate and hand-built histories, determinism, the
sequence-of-returns study, plots. # PB: whole file.

    python -m pytest screenlab/tests/test_plan_mc.py -q
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from screenlab.montecarlo import plan_mc as pm
from screenlab.montecarlo.plan_mc import FlowGrid, TaxRates, replay

M = 36
R_S, R_I = 0.01, 0.005


def hist(n=M, rs=R_S, ri=R_I, start="2000-01-31"):
    idx = pd.date_range(start, periods=n, freq="ME")
    return pd.DataFrame({"strategy": rs, "index": ri, "rf": 0.002, "cpi": 0.002}, index=idx)


def mid_month_flows(n=M, amount=100.0, start="2000-01-31"):
    """One flow on the 15th of each of the first `n` months (month k of the grid)."""
    d = pd.date_range(start, periods=n, freq="ME").to_period("M").start_time + pd.Timedelta(days=14)
    return pd.DataFrame({"date": d, "amount": amount})


def closed_form(r, flows, n_months, E0=pd.Timestamp("1999-12-31")):
    """Independent annuity: sum a (1+r)^(months remaining incl. the flow's own, less its day fraction)."""
    E = pd.date_range(E0, periods=n_months + 1, freq="ME")
    tot = 0.0
    for d, a in zip(pd.to_datetime(flows["date"]), flows["amount"]):
        k = (d.year - E0.year) * 12 + d.month - E0.month
        if not 1 <= k <= n_months:
            continue
        f = (d - E[k - 1]).days / (E[k] - E[k - 1]).days
        tot += a * (1 + r) ** (n_months - k + 1 - f)
    return tot


GENERATORS = ["block", "stationary", "iid", "regime"]


# --------------------------------------------------------------------------- degenerate history: every path equal
@pytest.mark.parametrize("gen", GENERATORS)
def test_zero_volatility_every_path_equals_the_straight_run(gen):
    h, fl = hist(), mid_month_flows(60)
    mc = pm.simulate(h, fl, "2004-12-31", n_paths=8, generator=gen, seed=1)       # 60 months, 24 past the history
    for col, r in (("strategy_pre", R_S), ("index_pre", R_I)):
        assert np.allclose(mc.terminal[col], closed_form(r, fl, 60), rtol=0, atol=1e-9)
    assert (mc.terminal["strategy_after_tax"] == mc.terminal["strategy_pre"]).all()     # no tax config
    assert np.allclose(mc.terminal["xirr_strategy"], (1 + R_S) ** 12 - 1, atol=1e-9)
    assert np.allclose(mc.terminal["xirr_index"], (1 + R_I) ** 12 - 1, atol=1e-9)
    assert np.allclose(mc.terminal["maxdd_strategy"], 0.0)
    # real = nominal / (1 + cpi)^60
    assert np.allclose(mc.terminal["strategy_real"], mc.terminal["strategy_pre"] / 1.002 ** 60, rtol=1e-12)
    assert mc.terminal["strategy_pre"].nunique() == 1 and mc.p_beat_index == 1.0
    # the deterministic historical replay is the straight run on the history span: equal to every path of that length
    straight = pm.simulate(h, fl, "2002-12-31", n_paths=2, seed=1).terminal["strategy_pre"].iloc[0]
    assert straight == pytest.approx(closed_form(R_S, fl, 36), abs=1e-9)
    assert mc.reversal.loc["forward", "strategy_pre"] == pytest.approx(straight, abs=1e-9)


def test_iid_single_return_lump_sum_is_analytic():
    h = hist(24, rs=0.02, ri=0.01)
    fl = pd.DataFrame({"date": [pd.Timestamp("2000-01-31")], "amount": [1000.0]})       # f = 1: invested at month end
    mc = pm.simulate(h, fl, "2001-12-31", n_paths=5, generator="iid", seed=3)
    # 24 months simulated from E0=1999-12-31; the lump lands at the end of month 1, then 23 growth months
    assert np.allclose(mc.terminal["strategy_pre"], 1000 * 1.02 ** 23, atol=1e-9)
    assert np.allclose(mc.terminal["index_pre"], 1000 * 1.01 ** 23, atol=1e-9)


# --------------------------------------------------------------------------- random histories
def noisy(n=48, seed=0, edge=0.0):
    rng = np.random.default_rng(seed)
    i = rng.normal(0.006, 0.04, n)
    idx = pd.date_range("2000-01-31", periods=n, freq="ME")
    return pd.DataFrame({"strategy": i + edge + rng.normal(0, 0.005, n) * (edge == 0), "index": i,
                         "rf": 0.002, "cpi": rng.normal(0.002, 0.002, n)}, index=idx)


def test_p_beat_index_is_one_when_strategy_never_below_index():
    mc = pm.simulate(noisy(edge=0.002), mid_month_flows(48), "2003-12-31", n_paths=60, seed=2)
    assert mc.p_beat_index == 1.0 and (mc.paired > 0).all()
    h = noisy(edge=0.002)
    h["strategy"], h["index"] = h["index"], h["strategy"]
    assert pm.simulate(h, mid_month_flows(48), "2003-12-31", n_paths=60, seed=2).p_beat_index == 0.0


def test_paired_mean_equals_mean_difference_and_quantiles():
    mc = pm.simulate(noisy(), mid_month_flows(48), "2003-12-31", n_paths=80, seed=4)
    t = mc.terminal
    assert mc.paired.mean() == pytest.approx((t.strategy_after_tax - t.index_after_tax).mean(), abs=1e-9)
    assert mc.paired.mean() == pytest.approx(t.strategy_after_tax.mean() - t.index_after_tax.mean(), abs=1e-9)
    assert list(mc.quantiles.index) == [5, 25, 50, 75, 95] and list(mc.quantiles.columns) == pm.TERMINAL_COLS
    assert mc.quantiles.loc[50, "strategy_after_tax"] == pytest.approx(t.strategy_after_tax.median())
    assert 0 < mc.p_beat_index < 1 and (t.maxdd_strategy < 0).all()


def test_shortfall_probability_on_a_constructed_case():
    h = hist(2, rs=0.0, ri=0.0)
    h["strategy"] = [0.10, -0.10]
    h["index"] = [0.10, -0.10]
    fl = pd.DataFrame({"date": [pd.Timestamp("2000-01-31")], "amount": [100.0]})
    # horizon 1 month (grid E0=1999-12-31 .. 2000-01-31): the flow at f=1 never grows: terminal 100 on every path
    mc = pm.simulate(h, fl, "2000-01-31", n_paths=50, generator="iid", seed=0, goal=100.0)
    assert (mc.terminal["strategy_pre"] == 100.0).all() and mc.p_shortfall == 0.0     # not below the goal
    mc = pm.simulate(h, fl, "2000-01-31", n_paths=50, generator="iid", seed=0, goal=100.01)
    assert mc.p_shortfall == 1.0 and mc.p_shortfall_index == 1.0
    # mid-month flow: 100 * (1 + r)^(1 - f) with r = +/-0.10 (two outcomes); the goal splits them
    fl = pd.DataFrame({"date": [pd.Timestamp("2000-01-16")], "amount": [100.0]})
    f = 16 / 31
    hi_, lo_ = 100 * 1.1 ** (1 - f), 100 * 0.9 ** (1 - f)
    mc = pm.simulate(h, fl, "2000-01-31", n_paths=200, generator="iid", seed=0, goal=100.0)
    assert set(np.round(mc.terminal["strategy_pre"].unique(), 9)) == {round(hi_, 9), round(lo_, 9)}
    assert mc.p_shortfall == pytest.approx((mc.terminal["strategy_pre"] < 100).mean())
    assert 0.35 < mc.p_shortfall < 0.65


# --------------------------------------------------------------------------- determinism
def test_seed_reproduces_and_n_paths_does_not_change_path_k():
    h, fl = noisy(), mid_month_flows(48)
    a = pm.simulate(h, fl, "2005-12-31", n_paths=20, seed=7, goal=1e5)
    b = pm.simulate(h, fl, "2005-12-31", n_paths=20, seed=7, goal=1e5)
    pd.testing.assert_frame_equal(a.terminal, b.terminal)
    c = pm.simulate(h, fl, "2005-12-31", n_paths=8, seed=7)
    pd.testing.assert_frame_equal(a.terminal.iloc[:8], c.terminal)
    assert not pm.simulate(h, fl, "2005-12-31", n_paths=8, seed=8).terminal.equals(c.terminal)
    long = pm.simulate(h, fl, "2008-12-31", n_paths=8, seed=7)             # a longer horizon: same first months
    assert long.fan.iloc[:5].index.equals(c.fan.iloc[:5].index)


# --------------------------------------------------------------------------- sequence of returns
def test_reversed_order_with_contributions_late_crash_is_worse():
    h = hist(2)
    h["strategy"] = [0.5, -0.5]
    h["index"] = [0.5, -0.5]
    # contributions of 100 at each month end (f = 1), history 2 months
    fl = pd.DataFrame({"date": [pd.Timestamp("2000-01-31"), pd.Timestamp("2000-02-29")], "amount": 100.0})
    mc = pm.simulate(h, fl, "2000-02-29", n_paths=2, seed=0)
    fwd, rev = mc.reversal.loc["forward"], mc.reversal.loc["reversed"]
    # forward: 100 -> *0.5 + 100 = 150 (late crash); reversed: 100 -> *1.5 + 100 = 250
    assert fwd["strategy_pre"] == pytest.approx(150.0, abs=1e-12) and rev["strategy_pre"] == pytest.approx(250.0, abs=1e-12)
    assert mc.reversal.loc["reversed_minus_forward", "index_after_tax"] == pytest.approx(100.0, abs=1e-12)
    assert mc.reversal.loc["forward", "contributed"] == 200.0
    # the same, contributions at the start of each month (frac 0): 125 vs 225
    g = FlowGrid.make([1, 2], [0.0, 0.0], [100.0, 100.0])
    f, _, _ = replay(np.array([[0, 0.5, -0.5]]), g)
    r, _, _ = replay(np.array([[0, -0.5, 0.5]]), g)
    assert f[0, -1] == pytest.approx(125.0) and r[0, -1] == pytest.approx(225.0)


def test_worst_12_months_group_and_study_table():
    N = 37                                              # 36 months
    def path(crash_start):
        r = np.zeros(N)
        r[crash_start:crash_start + 12] = -0.05
        return r
    r = np.stack([path(1), path(13), path(25)])
    assert list(pm._worst12_group(r)) == [0, 1, 2]
    # boundaries: centre (s + 5.5)/36 < 1/3 iff s <= 6 (first); >= 2/3 iff s >= 19 (last)
    assert list(pm._worst12_group(np.stack([path(6), path(7), path(18), path(19)]))) == [0, 1, 1, 2]
    mc = pm.simulate(noisy(60, seed=5), mid_month_flows(60), "2004-12-31", n_paths=90, seed=1, goal=1e4)
    s = mc.sequence
    assert list(s.index) == list(pm.GROUPS) and s["n"].sum() == 90
    assert s["strategy_mean"].notna().all() and ((s["p_shortfall"] >= 0) & (s["p_shortfall"] <= 1)).all()
    with pytest.raises(ValueError):
        pm._worst12_group(np.zeros((2, 8)))


# --------------------------------------------------------------------------- tax approximation
def test_tax_drag_account_and_liquidation_are_exact():
    g = FlowGrid.make([1], [1.0], [1000.0])
    div = pd.Series({2000: 0.012, 2001: 0.012})
    E = pd.date_range("1999-12-31", periods=13, freq="ME")
    r = np.full((1, 13), 0.01)
    tr = TaxRates("taxable", div=div, gain=pd.Series(dtype=float))
    dm = 1 - (1 - 0.012) ** (1 / 12)
    pre, aft, basis = replay(r, g, tr, E)
    assert pre[0, -1] == pytest.approx(1000 * 1.01 ** 11, abs=1e-9)
    expect = 1000 * (1 - dm)
    for _ in range(11):
        expect *= 1.01 * (1 - dm)
    assert aft[0, -1] == pytest.approx(expect, abs=1e-9) and basis[0] == 1000.0
    liq = TaxRates("taxable", div=div, gain=pd.Series(dtype=float), gain_eff=0.25, liquidate=True)
    assert pm.terminal_after_tax(aft[:, -1], basis, liq)[0] == pytest.approx(expect - 0.25 * (expect - 1000), abs=1e-9)
    assert pm.terminal_after_tax(aft[:, -1], basis, TaxRates("tfsa"))[0] == aft[0, -1]
    assert pm.terminal_after_tax(aft[:, -1], basis, TaxRates("rrsp", rrsp=0.3))[0] == pytest.approx(0.7 * aft[0, -1])


def test_rates_from_table_and_path_after_tax_below_pre():
    tab = pd.DataFrame({"tax_dividends": [10.0, 12.0], "tax_foreign_dividends": [0.0, 0.0], "tax_interest": [0.0, 0.0],
                        "tax_capital_gains": [0.0, 50.0], "refund": [0.0, 0.0], "realized_gains": [0.0, 200.0],
                        "realized_losses": [0.0, 0.0]}, index=pd.Index([2000, 2001], name="year"))
    pre = pd.Series(1000.0, index=pd.date_range("2000-01-03", "2001-12-31", freq="B"))
    cfg = SimpleNamespace(account="taxable", liquidate_at_end=False, rrsp_withdrawal_rate=None)
    tr = pm.rates_from_table(tab, pre, cfg)
    assert tr.div[2000] == pytest.approx(0.01) and tr.gain[2001] == pytest.approx(0.05) and tr.gain_eff == pytest.approx(0.25)
    assert pm.rates_from_table(None, pre, cfg) is None
    mc = pm.simulate(hist(24), mid_month_flows(24), "2001-12-31", n_paths=3, rates={"strategy": tr}, seed=0)
    assert (mc.terminal.strategy_after_tax < mc.terminal.strategy_pre).all()
    assert (mc.terminal.index_after_tax == mc.terminal.index_pre).all()


# --------------------------------------------------------------------------- withdrawals, xirr, errors
def test_withdrawals_floor_at_zero_and_xirr_roundtrip():
    g = FlowGrid.make([1, 2], [1.0, 1.0], [100.0, -500.0])
    pre, _, basis = replay(np.array([[0, 0.0, 0.0]]), g)
    assert pre[0, -1] == 0.0 and basis[0] == 0.0
    g = FlowGrid.make([1, 7], [0.5, 1.0], [1000.0, 0.0])
    x = pm.xirr(g, np.array([1000 * 1.01 ** 11.5]), 13)                      # 12 months; 1000 at t = 0.5/12 years
    assert x[0] == pytest.approx((1.01) ** 12 - 1, abs=1e-9)
    assert np.isnan(pm.xirr(FlowGrid.make([], [], []), np.array([1.0]), 13)[0])


def test_bad_arguments():
    with pytest.raises(ValueError):
        pm.make_generator("nope")
    with pytest.raises(ValueError):
        pm.simulate(hist(), mid_month_flows(), "2002-12-31", cpi="x")


def test_cpi_mean_holds_cpi_constant():
    h = noisy()
    a = pm.simulate(h, mid_month_flows(48), "2003-12-31", n_paths=10, seed=1, cpi="mean")
    ratio = a.terminal.strategy_after_tax / a.terminal.strategy_real
    assert np.allclose(ratio, (1 + h["cpi"].mean()) ** 48)
    assert any("held at its historical mean" in n for n in a.card.notes)


# --------------------------------------------------------------------------- the PlanSpec entry point
@pytest.fixture
def fake_plan(monkeypatch):
    import screenlab.plan as plan
    h = noisy(48, seed=9)
    days = pd.bdate_range("2000-01-03", "2003-12-31")
    defl = pd.Series(np.cumprod(1 + np.interp(days.view("i8"), h.index.view("i8"), h["cpi"].to_numpy()) / 21), index=days)
    series = pd.DataFrame({"cpi_deflator": defl, "strategy_pre": 1.0, "index_pre": 1.0}, index=days)
    res = SimpleNamespace(monthly=h[["strategy", "index", "rf"]], series=series, tax=None, flows=None)
    seen = {}
    monkeypatch.setattr(plan, "run_plan", lambda lab, spec: res)

    def sched(spec, index, cpi):
        seen["index"], seen["cpi"] = index, cpi
        return mid_month_flows(len(pd.date_range(index[0], index[-1], freq="ME")))
    monkeypatch.setattr(plan, "schedule", sched)
    return plan, seen


def test_plan_montecarlo_entry_point_extends_past_the_data(fake_plan):
    plan, seen = fake_plan
    spec = plan.PlanSpec(screen="x", start="2000-01-03", end="2005-12-31", periodic=100.0, goal=1e9)
    mc = pm.plan_montecarlo(None, spec, n_paths=12, seed=3)
    assert len(mc.terminal) == 12 and mc.fan.index[-1] == pd.Timestamp("2005-12-31") and mc.p_shortfall == 1.0
    assert seen["index"][-1] <= pd.Timestamp("2005-12-31") and seen["cpi"].index[-1] >= pd.Timestamp("2005-12-30")
    assert any("past the last data month" in n for n in mc.card.notes) and mc.card.seed == 3
    assert mc.card.generator["__t"] == "BlockBootstrap"
    again = pm.plan_montecarlo(None, spec, n_paths=12, seed=3)
    pd.testing.assert_frame_equal(mc.terminal, again.terminal)
    reg = pm.plan_montecarlo(None, spec, n_paths=6, seed=3, generator="regime", horizon_end="2004-12-31")
    assert reg.fan.index[-1] == pd.Timestamp("2004-12-31")


# --------------------------------------------------------------------------- plots
def test_plot_smoke_every_figure_has_legend_and_the_index():
    from screenlab.research import plan_plots as pp
    mc = pm.simulate(noisy(60, seed=5), mid_month_flows(60), "2004-12-31", n_paths=40, seed=1, goal=2e4)
    figs = pp.figures(mc)
    assert set(figs) == {"fan", "terminal", "paired", "sequence", "reversal"}
    for name, fig in figs.items():
        ax = fig.axes[0]
        assert ax.get_legend() is not None, name
        txt = " ".join(t.get_text() for t in ax.get_legend().get_texts())
        assert "index" in txt.lower(), name
        plt.close(fig)
    ax = pp.terminal_hist(mc, legend=False)
    assert ax.get_legend() is None
    plt.close(ax.figure)
    ax = pp.fan_chart(mc, log=True)
    assert "Goal" in " ".join(t.get_text() for t in ax.get_legend().get_texts())
    plt.close(ax.figure)
