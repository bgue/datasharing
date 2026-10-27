"""Exit-signal screen: hand-built crash, output shapes, causality of every signal, synthetic run.

    python -m pytest rothery_screener/tests/test_exit_signals.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rothery_screener import Lab, Market
from rothery_screener.exit_signals import (Breadth, Dispersion, DrawdownFromPeak, ExitScreen, MomentumCrash,
                                           RateShock, Signal, TrendBreak, ValuationStretch, VolRegime,
                                           evaluate, overlay, rolling_percentile)


# --------------------------------------------------------------------------- hand-built crash
@pytest.fixture(scope="module")
def crash():
    """Slow rise, crash from 2005-01 to 2005-09 (-40%), recovery; one flat-ish ticker as universe."""
    dates = pd.bdate_range("2000-01-03", "2008-12-31")
    n = len(dates)
    rng = np.random.default_rng(0)
    r = 0.0004 + rng.normal(0, 0.004, n)
    i0 = dates.searchsorted(pd.Timestamp("2005-01-03"))
    i1 = dates.searchsorted(pd.Timestamp("2005-09-01"))
    r[i0:i1] = np.log(0.6) / (i1 - i0) + rng.normal(0, 0.004, i1 - i0)
    r[i1:] = 0.0006 + rng.normal(0, 0.004, n - i1)
    idx = pd.Series(np.exp(np.cumsum(r)), index=dates)
    fund = pd.DataFrame({"date": pd.to_datetime([]), "ticker": []})
    mkt = Market(fund, pd.DataFrame({"A": idx}), pd.DataFrame({"IDX": idx}))
    return mkt, idx, dates[i0], dates[i1]


def test_trend_break_flags_before_bottom(crash):
    mkt, idx, start, bottom = crash
    screen = ExitScreen([TrendBreak("IDX", window_months=10)], threshold=0.95)
    score, state = screen.score(mkt), screen.state(mkt)
    first = state[start:].idxmax()
    assert state[first] and start < first < bottom
    assert state[start:bottom].mean() > 0.5 > state[:start].mean() + 0.25   # mostly flagged in the crash
    assert score[first] >= 0.95


def test_overlay_exposure_zero_cuts_drawdown(crash):
    mkt, idx, start, bottom = crash
    state = ExitScreen([TrendBreak("IDX")], threshold=0.9).state(mkt)
    res = overlay(idx, state, exposure_when_flagged=0.0)
    assert res.stats.loc["derisked", "MaxDD"] > res.stats.loc["always_in", "MaxDD"] + 0.1
    assert res.stats.loc["always_in", "MaxDD"] < -0.35
    assert res.switches >= 2 and res.curve.iloc[0] == 1.0
    # exposure 1.0 when flagged reproduces the always-in curve exactly
    same = overlay(idx, state, exposure_when_flagged=1.0)
    assert same.curve.iloc[-1] == pytest.approx(same.baseline.iloc[-1]) and same.switches == 0


def test_overlay_exact_arithmetic():
    d = pd.bdate_range("2020-01-01", periods=5)
    c = pd.Series([100, 110, 99, 99, 108.9], index=d)
    st = pd.Series([False, True, True, False, False], index=d)   # flagged at close of day 1 and 2
    res = overlay(c, st, 0.0, lag=1)
    # exposure held: day1 in, day2 out (flag seen day1), day3 out, day4 in
    assert list(res.exposure) == [1, 1, 0, 0, 1]
    assert res.curve.iloc[-1] == pytest.approx(1.1 * 1.0 * 1.0 * 1.1)
    assert res.switches == 2 and res.turnover == pytest.approx(2.0)


def test_hysteresis_and_min_votes(crash):
    mkt, idx, *_ = crash
    s = ExitScreen([TrendBreak("IDX"), DrawdownFromPeak("IDX")], threshold=0.8)
    sc, st = s.score(mkt), s.state(mkt)
    on = st & ~st.shift(1, fill_value=False)
    assert on.any() and (sc[on] >= 0.8).all()
    stay = st & st.shift(1, fill_value=False)
    assert (sc[stay] >= 0.7 - 1e-12).all()                     # stays on down to threshold - 0.1
    never = ExitScreen([TrendBreak("IDX")], threshold=0.8, min_votes=2).state(mkt)
    assert not never.any()


def test_rolling_percentile_basic():
    x = pd.Series(np.arange(30.0), index=pd.date_range("2000-01-31", periods=30, freq="ME"))
    p = rolling_percentile(x, years=10, min_obs=5)
    assert p.iloc[:4].isna().all() and p.iloc[10] == pytest.approx((10 + 0.5) / 11)
    q = rolling_percentile(x.iloc[::-1].reset_index(drop=True).set_axis(x.index), years=10, min_obs=5)
    assert q.iloc[-1] < 0.1


# --------------------------------------------------------------------------- evaluate shapes
def test_evaluate_shapes(crash):
    mkt, idx, start, bottom = crash
    screen = ExitScreen([TrendBreak("IDX"), VolRegime("IDX")], threshold=0.8)
    ev = evaluate(screen, mkt, idx, horizons=(1, 3, 12))
    assert ev.forward.index.names == ["horizon_months", "state"]
    assert len(ev.forward) == 6
    assert list(ev.forward.columns) == ["n", "mean", "median", "hit_rate", "worst"]
    assert 0 < ev.frac_flagged < 1
    assert len(ev.drawdowns) == 3 and ev.drawdowns.loc[0, "depth"] < -0.35
    assert {"peak", "trough", "first_flag", "lead_days"} <= set(ev.drawdowns.columns)
    assert ev.drawdowns.loc[0, "lead_days"] > 0
    # flagged windows precede worse 3m returns than unflagged ones in this crash
    assert ev.forward.loc[(3, "flagged"), "mean"] < ev.forward.loc[(3, "unflagged"), "mean"]
    assert isinstance(str(ev), str)


# --------------------------------------------------------------------------- causality
def _perturbed(mkt: Market, cut: pd.Timestamp, seed=0) -> Market:
    rng = np.random.default_rng(seed)
    after = mkt.index > cut
    px = mkt.prices.copy()
    px[after] = px[after] * rng.uniform(0.5, 1.5, (after.sum(), px.shape[1]))
    ind = mkt.indices.copy()
    ind[after] = ind[after] * rng.uniform(0.5, 1.5, (after.sum(), ind.shape[1]))
    return Market(mkt.fund, px, ind)


def _all_signals():
    return [TrendBreak("SYN_CAPWT"), Breadth(), VolRegime("SYN_CAPWT"), DrawdownFromPeak("SYN_CAPWT"),
            ValuationStretch("pe"), ValuationStretch("pe_avg3"), MomentumCrash(), RateShock("prime"),
            Dispersion()]


@pytest.fixture(scope="module")
def synth_daily():
    lab = Lab.synthetic(freq="B", n_stocks=40, years=6, seed=5)
    rng = np.random.default_rng(1)
    d = pd.date_range("1999-06-30", "2006-12-31", freq="ME")
    prime = pd.Series(4 + np.cumsum(rng.normal(0, 0.2, len(d))), index=d)
    return lab.market, {"prime": prime}


@pytest.mark.parametrize("sig", _all_signals(), ids=lambda s: f"{s.name}")
def test_signals_are_causal(sig, synth_daily):
    mkt, macro = synth_daily
    cut = mkt.index[int(len(mkt.index) * 0.65)]
    mkt2 = _perturbed(mkt, cut)
    macro2 = {"prime": macro["prime"].where(macro["prime"].index <= cut, macro["prime"] * 3 + 1)}
    a, b = sig.compute(mkt, macro), sig.compute(mkt2, macro2)
    assert a[:cut].notna().sum() > 0, "signal should have scores before the cut"
    pd.testing.assert_series_equal(a[:cut], b[:cut], check_exact=False)
    assert not a[cut:].equals(b[cut:]) or a[cut:].isna().all()    # the perturbation does matter later


def test_signal_protocol_and_ranges(synth_daily):
    mkt, macro = synth_daily
    for s in _all_signals():
        assert isinstance(s, Signal)
        c = s.compute(mkt, macro)
        assert c.index.equals(mkt.index)
        v = c.dropna()
        assert ((v >= 0) & (v <= 1)).all()


def test_missing_macro_and_columns_are_skipped(synth_daily):
    mkt, _ = synth_daily
    assert RateShock("prime").compute(mkt, None).isna().all()
    assert RateShock("prime").compute(mkt, {}).isna().all()
    assert ValuationStretch("no_such_col").compute(mkt).isna().all()
    s = ExitScreen([TrendBreak("SYN_CAPWT"), RateShock("prime")])
    assert list(s.components(mkt, None).columns) == ["TrendBreak"]


def test_macro_object_with_get(synth_daily):
    mkt, macro = synth_daily

    class M:
        def get(self, name):
            return macro.get(name)
    a = RateShock("prime").compute(mkt, M())
    assert a.notna().any()
    pd.testing.assert_series_equal(a, RateShock("prime").compute(mkt, macro))


# --------------------------------------------------------------------------- synthetic monthly market
def test_synthetic_monthly_end_to_end():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    mkt = lab.market
    target = mkt.series("SYN_CAPWT")
    rng = np.random.default_rng(0)
    d = pd.date_range("1999-06-30", "2010-12-31", freq="ME")
    macro = {"prime": pd.Series(4 + np.cumsum(rng.normal(0, 0.2, len(d))), index=d)}
    screen = ExitScreen([TrendBreak("SYN_CAPWT"), Breadth(), VolRegime("SYN_CAPWT"),
                         DrawdownFromPeak("SYN_CAPWT"), ValuationStretch(), MomentumCrash(),
                         RateShock("prime"), Dispersion()], threshold=0.6)
    comp = screen.components(mkt, macro)
    assert comp.shape[0] == len(mkt.index) and comp.shape[1] == 8
    ev = evaluate(screen, mkt, target, horizons=(1, 3, 12), macro=macro)
    assert ev.forward.shape == (6, 5) and ev.score.notna().sum() > 40
    assert 0 <= ev.frac_flagged <= 1
    assert set(ev.overlay.stats.index) == {"always_in", "derisked"}
    assert ev.overlay.curve.index.equals(target.dropna().index)
    # backtest curve works as a target too
    res = lab.backtest(["graham_defensive"] if "graham_defensive" in lab.screens else None).results
    curve = next(iter(res.values())).curve
    assert evaluate(screen, mkt, curve, macro=macro).forward.shape == (6, 5)
