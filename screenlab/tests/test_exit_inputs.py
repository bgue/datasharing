"""Additional exit inputs (H3): causality of each, hand-built checks, timing, runs inside an ExitScreen.

    python -m pytest screenlab/tests/test_exit_inputs.py -q
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab, Market
from screenlab import exit_signals as es
from screenlab.exit_inputs import (CorrelationSpike, CreditSpread, DeathCross, DistanceFromHigh,
                                          DownsideDeviation, EarningsBreadth, NewLowsMinusHighs,
                                          PortfolioStop, PortfolioVsIndex, VolTermStructure, YieldCurve,
                                          YieldSpreadCompression)
from screenlab.exit_signals import ExitScreen
from screenlab.realdata.bundled import sp500_lab

IDX = "SYN_CAPWT"
ALL = [CorrelationSpike(), NewLowsMinusHighs(), VolTermStructure(index=IDX), DeathCross(index=IDX),
       DistanceFromHigh(IDX), DownsideDeviation(index=IDX), PortfolioStop(),
       PortfolioVsIndex(index=IDX), EarningsBreadth(), YieldSpreadCompression(), YieldCurve(),
       CreditSpread()]


def _macro(index, seed=1):
    rng = np.random.default_rng(seed)
    d = pd.date_range(index[0] - pd.DateOffset(months=2), index[-1] + pd.DateOffset(months=1), freq="ME")
    n = len(d)
    return {"rf": pd.Series(0.03 + np.cumsum(rng.normal(0, 0.002, n)), index=d),
            "long_rate": pd.Series(0.04 + np.cumsum(rng.normal(0, 0.002, n)), index=d),
            "credit_spread": pd.Series(0.02 + np.abs(np.cumsum(rng.normal(0, 0.002, n))), index=d)}


def _curve(mkt, seed=2):
    rng = np.random.default_rng(seed)
    return pd.Series(np.cumprod(1 + rng.normal(0.0004, 0.01, len(mkt.index))), index=mkt.index)


def _perturb(mkt, cut, seed=0):
    rng = np.random.default_rng(seed)
    after = mkt.index > cut
    px = mkt.prices.copy()
    px[after] = px[after] * rng.uniform(0.5, 1.5, (after.sum(), px.shape[1]))
    ind = mkt.indices.copy()
    ind[after] = ind[after] * rng.uniform(0.5, 1.5, (after.sum(), ind.shape[1]))
    f = mkt.fund.copy()
    late = f["date"] > cut
    for c in ("eps", "eps_prev", "ebit", "enterprise_value"):
        f.loc[late, c] = f.loc[late, c] * rng.uniform(0.2, 3, late.sum())
    return Market(f, px, ind)


@pytest.fixture(scope="module", params=["B", "ME"])
def lab(request):
    if request.param == "B":
        return Lab.synthetic(freq="B", n_stocks=40, years=6, seed=5)
    return Lab.synthetic(freq="ME", n_stocks=60, years=12, seed=5)


@pytest.mark.parametrize("sig", ALL, ids=lambda s: s.name)
def test_signals_are_causal(sig, lab):
    mkt = lab.market
    macro, curve = _macro(mkt.index), _curve(mkt)
    cut = mkt.index[int(len(mkt.index) * 0.65)]
    mkt2 = _perturb(mkt, cut)
    macro2 = {k: v.where(v.index <= cut, v * 3 + 1) for k, v in macro.items()}
    curve2 = curve.where(curve.index <= cut, curve * 0.5)
    a = sig.compute(mkt, macro, curve=curve)
    b = sig.compute(mkt2, macro2, curve=curve2)
    assert a[:cut].notna().sum() > 0, "signal should have scores before the cut"
    pd.testing.assert_series_equal(a[:cut], b[:cut], check_exact=False)
    assert not a[cut:].equals(b[cut:])                      # the perturbation does matter later
    assert a.index.equals(mkt.index)


def test_describe_and_registration():
    for sig in ALL:
        d = type(sig).describe()
        assert set(d) == {"name", "params", "needs"} and d["name"] == sig.name and d["needs"]
        assert all(n in ("prices", "index", "curve", "fundamentals") or n.startswith("macro:") for n in d["needs"])
    assert CorrelationSpike.describe()["params"] == {"window_days": 21, "min_names": 10}
    assert PortfolioStop.describe()["params"] == {"drawdown": 0.15}
    assert YieldCurve.describe()["needs"] == ["macro:rf", "macro:long_rate"]
    names = es.BUILTINS if isinstance(es.BUILTINS, dict) else {c.__name__: c for c in es.BUILTINS}
    for sig in ALL:
        assert names[sig.name] is type(sig)


def _mkt_from(idx: pd.Series, extra=None):
    fund = pd.DataFrame({"date": pd.to_datetime([]), "ticker": []})
    return Market(fund, pd.DataFrame({"A": idx}), pd.DataFrame({"IDX": idx}))


# --------------------------------------------------------------------------- hand-built
def test_portfolio_stop_hits_one_after_15pct_fall():
    d = pd.bdate_range("2020-01-01", periods=60)
    c = pd.Series(np.r_[np.linspace(100, 120, 30), np.linspace(120, 101, 30)], index=d)   # -15.8%
    mkt = _mkt_from(c)
    s = PortfolioStop(0.15).compute(mkt, curve=c)
    assert s[:30].max() == pytest.approx(0.0)
    assert s.iloc[-1] == 1.0 and s.max() == 1.0
    mid = c.index[30 + np.argmin(abs(c.values[30:] - 120 * 0.925))]          # about half-way (7.5% down)
    assert 0.4 < s[mid] < 0.6
    assert PortfolioStop().compute(mkt).isna().all()               # no curve -> NaN


def test_death_cross_flags_flat_then_falling():
    d = pd.bdate_range("2015-01-01", periods=700)
    p = np.r_[np.full(450, 100.0), 100 * np.exp(-0.004 * np.arange(1, 251))]
    p = pd.Series(p * (1 + 0.0005 * np.sin(np.arange(700))), index=d)
    mkt = _mkt_from(p)
    sc = DeathCross(index="IDX").compute(mkt)
    assert sc.iloc[-1] > 0.9
    raw = DeathCross(index="IDX").raw(mkt)
    assert raw.iloc[-1] > 0 and abs(raw[d[440]]) < 0.01


def test_yield_curve_nan_without_macro_and_positive_when_inverted(lab):
    mkt = lab.market
    assert YieldCurve().compute(mkt).isna().all()
    assert YieldCurve().compute(mkt, {"rf": _macro(mkt.index)["rf"]}).isna().all()
    d = _macro(mkt.index)["rf"].index
    inv = {"rf": pd.Series(0.05, index=d), "long_rate": pd.Series(0.03, index=d)}
    raw = YieldCurve().raw(mkt, inv)
    assert (raw.dropna() > 0).all() and raw.notna().any()
    norm = {"rf": inv["long_rate"], "long_rate": inv["rf"]}
    assert (YieldCurve().raw(mkt, norm).dropna() < 0).all()
    # percentile: steepening then inverting scores higher at the end
    rf = pd.Series(np.linspace(0.01, 0.06, len(d)), index=d)
    s = YieldCurve().compute(mkt, {"rf": rf, "long_rate": pd.Series(0.035, index=d)})
    assert s.dropna().iloc[-1] > 0.9


def test_earnings_breadth_nan_on_price_only_lab():
    lab = sp500_lab()
    assert EarningsBreadth().compute(lab.market).isna().all()
    assert YieldSpreadCompression().compute(lab.market, {"rf": pd.Series(0.03, index=lab.market.index)}).isna().all()


def test_fundamentals_signals_on_synthetic(lab):
    mkt = lab.market
    e = EarningsBreadth().compute(mkt)
    assert e.notna().sum() > 0 and e.dropna().between(0, 1).all()
    y = YieldSpreadCompression().compute(mkt, _macro(mkt.index))
    assert y.notna().sum() > 0
    assert YieldSpreadCompression().compute(mkt).isna().all()      # no macro -> NaN


def test_missing_inputs_are_nan(lab):
    mkt = lab.market
    for s in (PortfolioStop(), PortfolioVsIndex(index=IDX), YieldCurve(), CreditSpread()):
        assert s.compute(mkt).isna().all(), s.name


def test_portfolio_vs_index_responds_to_underperformance():
    d = pd.bdate_range("2018-01-01", periods=800)
    rng = np.random.default_rng(0)
    idx = pd.Series(np.cumprod(1 + rng.normal(0.0004, 0.008, 800)), index=d)
    port = idx.copy()
    port[600:] = port[600:] * np.linspace(1, 0.8, 200)             # portfolio slides vs index
    mkt = _mkt_from(idx)
    raw = PortfolioVsIndex(index="IDX").raw(mkt, curve=port)
    assert raw.iloc[-1] > 0.05 and raw[:d[590]].max() < 0.01
    assert PortfolioVsIndex(index="IDX").compute(mkt, curve=port).iloc[-1] > 0.95


def test_correlation_spike_detects_common_shock():
    rng = np.random.default_rng(0)
    d = pd.bdate_range("2010-01-01", periods=1500)
    f = np.zeros(1500)
    f[1000:1040] = rng.normal(0, 0.03, 40)                         # a common factor appears
    r = rng.normal(0, 0.01, (1500, 30)) + f[:, None]
    mkt = Market(pd.DataFrame({"date": pd.to_datetime([]), "ticker": []}),
                 pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=d), None)
    raw = CorrelationSpike().raw(mkt)
    assert raw[d[800]:d[990]].max() < 0.15
    assert raw[d[1020]:d[1040]].max() > 0.6


# --------------------------------------------------------------------------- timing and runs
def test_correlation_spike_timing_large_daily():
    big = Lab.synthetic(freq="B", n_stocks=250, years=25)
    t0 = time.perf_counter()
    s = CorrelationSpike().compute(big.market)
    dt = time.perf_counter() - t0
    print(f"CorrelationSpike 250x25y: {dt:.2f}s")
    assert dt < 5, dt
    assert s.notna().sum() > 5000 and s.dropna().between(0, 1).all()


def _bound(sig, curve):
    class Bound:
        name = sig.name

        def compute(self, mkt, macro=None, curve_=None, curve=curve):
            return sig.compute(mkt, macro, curve=curve)
    return Bound()


def test_all_signals_in_exit_screen_synthetic():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    mkt = lab.market
    macro, curve = _macro(mkt.index), _curve(mkt)
    sigs = [_bound(s, curve) for s in ALL]
    screen = ExitScreen(sigs, threshold=0.8)
    comp = screen.components(mkt, macro)
    assert set(comp.columns) == {s.name for s in ALL}
    sc, st = screen.score(mkt, macro, comp), screen.state(mkt, macro, comp)
    assert sc.notna().sum() > 0 and st.dtype == bool and 0 <= st.mean() < 1
    for s in ALL:
        v = s.compute(mkt, macro, curve=curve).dropna()
        assert v.between(0, 1).all(), s.name


def test_all_signals_on_sp500_lab(capsys):
    lab = sp500_lab()
    mkt = lab.market
    curve = mkt.indices["SP500"]
    out = {}
    for s in ALL:
        s = type(s)(**({"index": "SP500"} if "index" in type(s).describe()["params"] else {}))
        t0 = time.perf_counter()
        scr = ExitScreen([_bound(s, curve)], threshold=0.8)
        comp = scr.components(mkt, None)
        dt = time.perf_counter() - t0
        out[s.name] = (float(scr.state(mkt, None, comp).mean()) if s.name in comp else None, dt)
    with capsys.disabled():
        print()
        for k, (f, dt) in out.items():
            print(f"  {k:24s} flagged {('n/a (no data)' if f is None else f'{f:6.1%}')}   {dt:5.2f}s")
    price_based = ["CorrelationSpike", "NewLowsMinusHighs", "VolTermStructure", "DeathCross",
                   "DistanceFromHigh", "DownsideDeviation", "PortfolioStop", "PortfolioVsIndex"]
    for k in price_based:
        assert out[k][0] is not None, k
    for k in ("EarningsBreadth", "YieldSpreadCompression", "YieldCurve", "CreditSpread"):
        assert out[k][0] is None, k
