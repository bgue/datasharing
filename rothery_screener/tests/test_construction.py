"""Portfolio construction: exact weights on 4 hand-built names, plus synthetic-market behaviour.

    python -m pytest rothery_screener/tests/test_construction.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rothery_screener import Lab
from rothery_screener.backtest import Result
from rothery_screener.construction import (HoldRule, blend, cap_weights, clip_weights, inv_vol_weights,
                                           target_weights, union_screen)
from rothery_screener.screens import REGISTRY


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def four():
    return pd.DataFrame({"market_cap": [6000., 2000., 1000., 1000.], "vol_1y": [0.1, 0.2, 0.2, 0.4],
                         "sector": ["A", "A", "B", "C"]}, index=list("wxyz"))


# --------------------------------------------------------------------------- exact arithmetic
def test_cap_weights_exact():
    assert cap_weights(four()).tolist() == pytest.approx([0.6, 0.2, 0.1, 0.1])


def test_inv_vol_weights_exact():
    w = inv_vol_weights(four())
    inv = np.array([10, 5, 5, 2.5])
    assert w.tolist() == pytest.approx((inv / inv.sum()).tolist())
    assert w.sum() == pytest.approx(1)


def test_max_weight_clipping_redistributes_pro_rata():
    w = clip_weights(pd.Series([0.6, 0.2, 0.1, 0.1], index=list("wxyz")), max_weight=0.4)
    # w -> 0.4; excess 0.2 goes 2:1:1 to x,y,z -> 0.3, 0.15, 0.15 (all under 0.4)
    assert w.tolist() == pytest.approx([0.4, 0.3, 0.15, 0.15])


def test_clipping_cascades():
    w = clip_weights(pd.Series([0.6, 0.3, 0.05, 0.05], index=list("wxyz")), max_weight=0.35)
    # w -> .35 (excess .25); x would reach .3+.25*.3/.4=.4875 -> clipped .35; rest split equally
    assert w.tolist() == pytest.approx([0.35, 0.35, 0.15, 0.15])


def test_infeasible_max_weight_holds_cash():
    w = clip_weights(pd.Series(0.25, index=list("wxyz")), max_weight=0.2)
    assert w.tolist() == pytest.approx([0.2] * 4)
    assert w.sum() == pytest.approx(0.8)


def test_sector_cap_exact():
    s = four()
    w = clip_weights(cap_weights(s), sector_cap=0.5, sectors=s["sector"])
    # sector A (0.8) -> 0.5 split 3:1; y,z share the remaining 0.5 equally
    assert w.tolist() == pytest.approx([0.375, 0.125, 0.25, 0.25])


def test_target_weights_callable_and_unknown():
    s = four()
    w = target_weights(s, ["w", "x"], lambda r: pd.Series([3.0, 1.0], index=r.index) / 4)
    assert w.tolist() == pytest.approx([0.75, 0.25])
    with pytest.raises(ValueError):
        target_weights(s, ["w"], "nope")


def test_hold_rule_keep_logic():
    ranked = list("abcdefgh")
    out = HoldRule(keep_within=2.0).keep(["a", "b", "c"], ranked, ["d", "h"], n=3)
    assert set(out) == {"a", "b", "d"}          # d ranks 4 < 6 and stays; h (rank 8) goes
    assert set(HoldRule(keep_within=1.0).keep(["a", "b", "c"], ranked, ["d", "h"], n=3)) == {"a", "b", "c"}


def test_band_freezes_names_near_target():
    target = pd.Series(0.25, index=list("wxyz"))
    prev = pd.Series({"w": 0.28, "x": 0.20, "y": 0.10})       # w,x inside +-20% (0.2-0.3); y out
    out = HoldRule(band=0.2).apply_band(target, prev)
    assert out["w"] == 0.28 and out["x"] == 0.20
    assert out.sum() == pytest.approx(1)
    assert out["y"] == pytest.approx(0.26) and out["z"] == pytest.approx(0.26)


def test_blend_of_curve_with_itself_is_itself(lab):
    r = lab.backtest(["dogs"]).results["dogs"]
    b = blend({"a": r, "b": r}, [0.3, 0.7], rebalance_months=12)
    pd.testing.assert_series_equal(b.curve, r.curve, check_names=False, rtol=1e-12)


def test_blend_flat_and_growing_sleeves():
    idx = pd.date_range("2020-01-31", periods=25, freq="ME")
    flat = Result("f", pd.Series(1.0, index=idx), {idx[0]: ["A"]})
    grow = Result("g", pd.Series(2.0 ** (np.arange(25) / 12), index=idx), {idx[0]: ["B"]})
    b = blend({"f": flat, "g": grow}, {"f": 0.5, "g": 0.5}, rebalance_months=12)
    # year 1: 0.5 + 0.5*2 = 1.5; weights drift to 1/3, 2/3 (turnover 1/6) then reset; year 2: 1.5 * 1.5
    assert b.curve.iloc[12] == pytest.approx(1.5)
    assert b.curve.iloc[24] == pytest.approx(2.25)
    assert b.turnover.iloc[1] == pytest.approx(1 / 6)


# --------------------------------------------------------------------------- behaviour on the synthetic market
def test_defaults_reproduce_equal_weight(lab):
    base = lab.backtest(["dogs", "value_momentum"])
    explicit = lab.backtest(["dogs", "value_momentum"], weighting="equal", max_weight=None, hold_rule=None)
    for k in base.results:
        pd.testing.assert_series_equal(base.results[k].curve, explicit.results[k].curve)


def test_weightings_run_and_differ(lab):
    eq = lab.backtest(["dogs"]).results["dogs"].curve
    for wt in ("cap", "inv_vol"):
        c = lab.backtest(["dogs"], weighting=wt).results["dogs"].curve
        assert np.isfinite(c).all() and len(c) == len(eq)
        assert not np.allclose(c.to_numpy(), eq.to_numpy())


def test_max_weight_one_over_n_equals_equal(lab):
    eq = lab.backtest(["dogs"]).results["dogs"].curve
    c = lab.backtest(["dogs"], weighting="cap", max_weight=0.1).results["dogs"].curve
    pd.testing.assert_series_equal(c, eq, rtol=1e-9)


def test_infeasible_cap_is_partly_cash(lab):
    full = lab.backtest(["dogs"]).results["dogs"].curve
    half = lab.backtest(["dogs"], max_weight=0.05).results["dogs"].curve
    assert half.iloc[-1] != pytest.approx(full.iloc[-1])


def test_hold_rule_reduces_turnover_keep1_unchanged(lab):
    base = lab.backtest(["dogs"]).results["dogs"]
    k1 = lab.backtest(["dogs"], hold_rule=HoldRule(keep_within=1.0)).results["dogs"]
    assert {d: set(v) for d, v in k1.holdings.items()} == {d: set(v) for d, v in base.holdings.items()}
    pd.testing.assert_series_equal(k1.curve, base.curve)
    k2 = lab.backtest(["dogs"], hold_rule=HoldRule(keep_within=2.0)).results["dogs"]
    assert k2.turnover.iloc[1:].mean() < base.turnover.iloc[1:].mean()
    assert all(len(v) == len(base.holdings[d]) for d, v in k2.holdings.items())


def test_band_reduces_turnover(lab):
    base = lab.backtest(["dogs"]).results["dogs"]
    b = lab.backtest(["dogs"], hold_rule=HoldRule(band=0.2)).results["dogs"]
    assert b.turnover.iloc[1:].mean() < base.turnover.iloc[1:].mean()


def test_ranked_extends_picks(lab):
    snap = lab.snapshot()
    for name, sc in REGISTRY.items():
        assert sc.ranked(snap)[:len(sc(snap))] == sc(snap), name
    sp = lab.define({"name": "sp", "n": 5, "rank": {"div_yield": "desc"}})
    assert len(sp.ranked(snap)) > 5 and sp.ranked(snap)[:5] == sp(snap)


def test_union_screen_equals_set_union(lab):
    scs = [REGISTRY["dogs"], REGISTRY["value_momentum"], REGISTRY["low_vol_dividend"]]
    u = union_screen(scs, n=100)
    for asof in ("2018-06-30", "2022-06-30"):
        s = lab.snapshot(asof)
        got = u(s)
        assert set(got) == set().union(*[set(sc(s)) for sc in scs])
        assert len(got) == len(set(got))
        assert got[0] == scs[0](s)[0]                      # round-robin: first screen leads
    assert np.isfinite(lab.backtest([u]).results[u.name].curve).all()


def test_sector_cap_runs(lab):
    r = lab.backtest(["dogs"], sector_cap=0.3, max_weight=0.15).results["dogs"]
    assert np.isfinite(r.curve).all()
