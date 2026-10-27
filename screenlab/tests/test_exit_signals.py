"""Exit-signal screen: hand-built crash, output shapes, causality of every signal, synthetic run.

    python -m pytest screenlab/tests/test_exit_signals.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab, Market
from screenlab.backtest import Config, simulate
from screenlab.exit_policy import ExitPolicy
from screenlab.exit_signals import (Breadth, Dispersion, DrawdownFromPeak, ExitScreen,
                                           MomentumCrash, RateShock, Signal, TrendBreak, ValuationStretch,
                                           VolRegime, evaluate, hold_min_days, overlay, rolling_percentile,
                                           sample_at_checks)
from screenlab.screens import Screen


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


# --------------------------------------------------------------------------- F1: timing fields
def test_check_cadence_only_changes_at_check_points(crash):
    mkt, idx, start, bottom = crash
    daily = ExitScreen([TrendBreak("IDX")], threshold=0.9).state(mkt)
    weekly = ExitScreen([TrendBreak("IDX")], threshold=0.9, check="W-FRI").state(mkt)
    monthly = ExitScreen([TrendBreak("IDX")], threshold=0.9, check="M").state(mkt)
    assert daily.sum() > 0 and weekly.sum() > 0
    chg = lambda st: st.index[st.ne(st.shift(1, fill_value=False))]
    assert len(chg(weekly)) >= 2 and len(chg(daily)) >= 2
    assert all(t.weekday() == 4 for t in chg(weekly))              # flag moves only on check bars (Fridays)
    me = pd.Series(mkt.index, mkt.index).groupby(mkt.index.to_period("M")).last()
    assert len(chg(monthly)) >= 2 and all(t in set(me) for t in chg(monthly))
    # the weekly flag equals the daily flag on each Friday and is carried between them
    fri = daily.index[daily.index.weekday == 4]
    assert (weekly.loc[fri] == daily.loc[fri]).all()
    assert (weekly.reindex(daily.index, method="pad").to_numpy() == weekly.to_numpy()).all()


def test_sample_at_checks_exact():
    d = pd.bdate_range("2021-01-04", periods=10)             # Mon 4 Jan .. Fri 15 Jan
    flag = pd.Series([True, True, False, False, True, True, True, False, False, False], index=d)
    out = sample_at_checks(flag, "W-FRI")
    # first Friday is bar 4 (True); before it the flag is off; second Friday bar 9 (False)
    assert out.tolist() == [False] * 4 + [True] * 5 + [False]
    assert sample_at_checks(flag, "D") is flag


def test_min_hold_days_exact():
    d = pd.date_range("2021-01-01", periods=10, freq="D")
    flag = pd.Series([False, True, False, True, True, False, False, False, True, False], index=d)
    out = hold_min_days(flag, 3)
    # on at day1; flip-offs at day2 (1d) ignored; day3 on anyway; off at day5 (4d) accepted; on at day8 (3d) accepted
    assert out.tolist() == [False, True, True, True, True, False, False, False, True, True]
    assert hold_min_days(flag, 0) is flag


def test_min_hold_days_cuts_switches(crash):
    mkt, idx, *_ = crash
    base = ExitScreen([TrendBreak("IDX")], threshold=0.7, hysteresis=0.0)
    held = ExitScreen([TrendBreak("IDX")], threshold=0.7, hysteresis=0.0, min_hold_days=60)
    n = lambda st: int(st.ne(st.shift(1, fill_value=False)).sum())
    assert n(held.state(mkt)) < n(base.state(mkt))
    ch = held.state(mkt)
    d = ch.index[ch.ne(ch.shift(1, fill_value=False))]
    assert all((b - a).days >= 60 for a, b in zip(d[:-1], d[1:]))


def test_lag_shifts_overlay_and_evaluate_honours_screen_lag(crash):
    mkt, idx, *_ = crash
    scr = ExitScreen([TrendBreak("IDX")], threshold=0.9)
    st = scr.state(mkt)
    o1, o3 = overlay(idx, st, lag=1), overlay(idx, st, lag=3)
    assert o3.exposure.iloc[3:].to_numpy() == pytest.approx(o1.exposure.shift(2).iloc[3:].to_numpy())
    ev = evaluate(ExitScreen([TrendBreak("IDX")], threshold=0.9, lag=3), mkt, idx)
    assert ev.overlay.curve.iloc[-1] == pytest.approx(o3.curve.iloc[-1])
    assert ExitScreen([TrendBreak("IDX")]).lag == 1 and ExitScreen([]).check == "D"


# --------------------------------------------------------------------------- F2: backtest overlay
def tiny_market():
    d = pd.date_range("2021-01-01", periods=5, freq="D")
    px = pd.Series([100, 110, 99, 99, 108.9], index=d)
    prices = pd.DataFrame({t: px for t in "ABC"})
    fund = pd.DataFrame({"date": [d[0]] * 3, "ticker": list("ABC"), "market_cap": [1.0] * 3})
    return Market(fund, prices), d


def test_backtest_overlay_exact_arithmetic():
    mkt, d = tiny_market()
    scr = Screen("all3", lambda s, n: ["A", "B", "C"], 3)
    pol = ExitPolicy(ExitScreen([]), exposure_when_flagged=0.5, switch_cost_bps=10.0)
    e = pd.Series([1.0, 1.0, 0.5, 0.5, 1.0], index=d)               # hand-set (already lagged) exposure
    cfg = Config(cost_bps=0.0, min_holdings=3, cash_rate=0.0365, exit=pol)
    res = simulate(scr, mkt, cfg, [d[0]], end=d[-1], exposure=e)
    c = 1.0365 ** (1 / 365.25) - 1                                  # one calendar day of cash
    r = [0.10, -0.10, 0.0, 0.10]
    expect, v = [1.0], 1.0
    for ret, ex, dx in zip(r, [1.0, 0.5, 0.5, 1.0], [0.0, 0.5, 0.0, 0.5]):
        v *= 1 + ex * ret + (1 - ex) * c - 0.001 * dx
        expect.append(v)
    assert res.curve.tolist() == pytest.approx(expect, rel=1e-12)
    assert res.exposure.tolist() == e.tolist() and res.switches == 2
    # without the overlay: plain buy-and-hold, exposure 1, no switches
    base = simulate(scr, mkt, Config(cost_bps=0.0, min_holdings=3), [d[0]], end=d[-1])
    assert base.curve.tolist() == pytest.approx([1.0, 1.1, 0.99, 0.99, 1.089])
    assert base.switches == 0 and (base.exposure == 1.0).all()


def test_backtest_overlay_on_synthetic_market():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    plain = lab.backtest(["dogs"])
    scr = ExitScreen([TrendBreak("SYN_CAPWT", window_months=6)], threshold=0.6)
    flat = lab.backtest(["dogs"], exit=ExitPolicy(scr, exposure_when_flagged=1.0, switch_cost_bps=0.0))
    assert flat.results["dogs"].curve.to_numpy() == pytest.approx(plain.results["dogs"].curve.to_numpy())
    off = lab.backtest(["dogs"], exit=ExitPolicy(scr, exposure_when_flagged=0.0, switch_cost_bps=5.0))
    res = off.results["dogs"]
    assert res.switches >= 1 and 0.0 <= res.exposure.min() < 1.0
    assert res.exposure.index.equals(res.curve.index)
    assert plain.results["dogs"].exposure.eq(1.0).all() and plain.results["dogs"].switches == 0
    assert res.holdings.keys() == plain.results["dogs"].holdings.keys()    # picks untouched


# --------------------------------------------------------------------------- one window convention (J2)
import json
import warnings
from dataclasses import fields, replace
from pathlib import Path

from screenlab import exit_inputs as ei
from screenlab import exit_signals as es
from screenlab.exit_signals import calendar_window, median_step, window_not_full

BASELINE = json.loads((Path(__file__).parent / "fixtures" / "signals_baseline.json").read_text())


def test_calendar_window_daily_and_monthly_index():
    daily = pd.bdate_range("2020-01-01", periods=300)
    monthly = pd.date_range("2010-01-31", periods=60, freq="ME")
    assert median_step(daily) == 1.0 and median_step(monthly) in (30.0, 31.0)
    assert median_step(daily[:1]) == 1.0
    # trading days -> x 365.25/252 (63 td ~ 91 calendar days); months -> x 30.4375
    assert calendar_window(daily, days=63) == 91 and calendar_window(daily, days=252) == 365
    assert calendar_window(daily, months=10) == 304 and calendar_window(monthly, months=10) == 304
    # same trading-day request, different data frequency: widened to min_obs observations on monthly data
    assert calendar_window(daily, days=21, min_obs=6) == 30
    assert calendar_window(monthly, days=21, min_obs=6) == int(np.ceil(6 * median_step(monthly)))
    assert calendar_window(monthly, days=21, min_obs=1) == int(median_step(monthly))   # one bar
    assert calendar_window(daily, days=0.1) == 1                       # never below one day
    for bad in ({}, {"days": 5, "months": 1}):
        with pytest.raises(ValueError):
            calendar_window(daily, **bad)
    nf = window_not_full(daily, 30)                                    # first 30 calendar days = 22 bdays
    assert nf[:22].all() and not nf[22:].any() and nf.sum() == (daily < daily[0] + pd.Timedelta(days=30)).sum()


def _markets():
    return {"monthly": Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3).market,
            "daily": Lab.synthetic(freq="B", n_stocks=40, years=6, seed=5).market}


def _macro_in(index, seed=1):
    rng = np.random.default_rng(seed)
    d = pd.date_range(index[0] - pd.DateOffset(months=2), index[-1] + pd.DateOffset(months=1), freq="ME")
    n = len(d)
    return {"rf": pd.Series(0.03 + np.cumsum(rng.normal(0, 0.002, n)), index=d),
            "long_rate": pd.Series(0.04 + np.cumsum(rng.normal(0, 0.002, n)), index=d),
            "credit_spread": pd.Series(0.02 + np.abs(np.cumsum(rng.normal(0, 0.002, n))), index=d),
            "prime": pd.Series(0.05 + np.cumsum(rng.normal(0, 0.003, n)), index=d)}


def _curve_in(mkt, seed=2):
    rng = np.random.default_rng(seed)
    return pd.Series(np.cumprod(1 + rng.normal(0.0004, 0.01, len(mkt.index))), index=mkt.index)


@pytest.fixture(scope="module")
def baseline_markets():
    out = {}
    for name, mkt in _markets().items():
        out[name] = (mkt, _macro_in(mkt.index), _curve_in(mkt))
    return out


def _check_against(summary, s: pd.Series, label: str):
    v = s.to_numpy(float)
    ok = np.isfinite(v)
    assert len(v) == summary["n"] and int(ok.sum()) == summary["n_valid"], label
    import hashlib
    assert hashlib.md5(ok.tobytes()).hexdigest() == summary["nan_mask_md5"], f"{label}: NaN pattern changed"
    pos = np.linspace(0, len(v) - 1, 31).astype(int)
    for i, want in zip(pos, summary["samples"]):
        if want is None:
            assert not ok[i], label
        else:
            assert v[i] == pytest.approx(want, abs=1e-9), f"{label} @ {i}"
    if summary["sum"] is not None:
        assert float(v[ok].sum()) == pytest.approx(summary["sum"], abs=1e-8), label


def _expected(market: str, label: str) -> dict:
    return BASELINE["known_changes"].get(market, {}).get(label) or BASELINE["results"][market][label]


@pytest.mark.parametrize("market", ["monthly", "daily"])
@pytest.mark.parametrize("label", [k for k in BASELINE["specs"] if k.startswith("default:")])
def test_defaults_numerically_unchanged(label, market, baseline_markets):
    """Defaults reproduce the pre-refactor outputs (fixtures/signals_baseline.json), daily and monthly."""
    mkt, macro, curve = baseline_markets[market]
    spec = BASELINE["specs"][label]
    sig = es.BUILTINS[spec["cls"]](**spec["new_kwargs"])
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)            # defaults must not hit any deprecated path
        s = sig.compute(mkt, macro, curve=curve)
    _check_against(BASELINE["results"][market][label], s, f"{market}/{label}")


@pytest.mark.parametrize("market", ["monthly", "daily"])
@pytest.mark.parametrize("label", [k for k in BASELINE["specs"] if k.startswith("param:")])
def test_nondefault_params_new_and_deprecated_names(label, market, baseline_markets):
    """New names and the deprecated old names give the same series; only documented cases moved."""
    mkt, macro, curve = baseline_markets[market]
    spec = BASELINE["specs"][label]
    want = _expected(market, label)
    new = es.BUILTINS[spec["cls"]](**spec["new_kwargs"])
    _check_against(want, new.compute(mkt, macro, curve=curve), f"new {market}/{label}")
    if spec["old_kwargs"] != spec["new_kwargs"]:
        with pytest.warns(DeprecationWarning):
            old = es.BUILTINS[spec["cls"]](**spec["old_kwargs"])
    else:                                                              # name unchanged: must not warn
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            old = es.BUILTINS[spec["cls"]](**spec["old_kwargs"])
    _check_against(want, old.compute(mkt, macro, curve=curve), f"old {market}/{label}")


def test_known_value_changes_are_only_the_documented_one():
    assert {m: sorted(v) for m, v in BASELINE["known_changes"].items()} == {"monthly": ["param:Breadth_100d"]}


RENAMED = [(es.Breadth, "window_days", "days", 200, 200 / es.CAL_PER_TRADING_DAY),
           (es.VolRegime, "window", "days", 21, 21), (es.RateShock, "lag_days", "lag_calendar_days", 5, 5),
           (ei.CorrelationSpike, "window_days", "days", 10, 10),
           (ei.NewLowsMinusHighs, "window_days", "days", 100, 100),
           (ei.VolTermStructure, "short", "short_days", 10, 10), (ei.VolTermStructure, "long", "long_days", 90, 90),
           (ei.DeathCross, "fast", "fast_days", 20, 20), (ei.DeathCross, "slow", "slow_days", 100, 100),
           (ei.DistanceFromHigh, "window_days", "days", 100, 100),
           (ei.DownsideDeviation, "window", "days", 21, 21),
           (ei.YieldCurve, "lag_days", "lag_calendar_days", 3, 3),
           (ei.CreditSpread, "lag_days", "lag_calendar_days", 3, 3)]


@pytest.mark.parametrize("cls,old,new,val,want", RENAMED, ids=lambda x: x if isinstance(x, str) else None)
def test_deprecated_names_construct_read_write_and_warn(cls, old, new, val, want):
    with pytest.warns(DeprecationWarning, match=old):
        s = cls(**{old: val})
    assert getattr(s, new) == pytest.approx(want)
    with pytest.warns(DeprecationWarning):
        assert getattr(s, old) == pytest.approx(val)                 # old attribute reads back in old units
    with pytest.warns(DeprecationWarning):
        s2 = replace(s, **{old: val})                                # grid keys / dataclasses.replace path
    assert getattr(s2, new) == pytest.approx(want)
    with pytest.warns(DeprecationWarning):
        setattr(s, old, val)
    assert getattr(s, new) == pytest.approx(want)
    with pytest.warns(DeprecationWarning):
        both = cls(**{old: val, new: want * 3 + 1})                  # both given: the deprecated one wins
    assert getattr(both, new) == pytest.approx(want)
    assert old not in {f.name for f in fields(cls)}                   # serialized specs use the new name
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cls(**{new: want})                                           # new names never warn


def test_breadth_default_is_200_calendar_days_and_unit_change():
    assert calendar_window(pd.bdate_range("2020-01-01", periods=10), days=es.Breadth().days, min_obs=6) == 200
    with pytest.warns(DeprecationWarning):
        b = es.Breadth(window_days=250)
    assert calendar_window(pd.bdate_range("2020-01-01", periods=10), days=b.days, min_obs=6) == 250


def test_describe_every_signal_has_units_for_window_params():
    import re
    assert len(es.BUILTINS) == 20
    for name, cls in es.BUILTINS.items():
        d = cls.describe()
        assert set(d) == {"name", "params", "units", "needs"} and d["name"] == name
        assert d["params"] == {f.name: f.default for f in fields(cls)}
        for p, default in d["params"].items():
            if isinstance(default, (int, float)) and re.search(r"(^|_)(days|months|window|short|long|fast|slow)($|_)", p):
                assert d["units"].get(p) in ("trading days", "calendar months", "calendar days"), (name, p)
        for p, u in d["units"].items():
            assert p in d["params"]
            assert (u == "trading days") == (p == "days" or p.endswith("_days") and not p.startswith("lag"))
            assert (u == "calendar months") == (p == "months" or p.endswith("_months"))
        inst = cls().describe()                                       # instance form reports current values
        assert inst["params"] == d["params"]
    assert es.TrendBreak(window_months=6).describe()["params"]["window_months"] == 6
    assert es.Breadth.describe()["units"] == {"days": "trading days"}
    assert es.RateShock.describe()["units"] == {"months": "calendar months", "lag_calendar_days": "calendar days"}


def test_one_base_family():
    from screenlab import exit_inputs
    assert not hasattr(es, "_MonthlySampled") and not hasattr(exit_inputs, "_In")
    assert all(issubclass(c, es._Base) for c in es.BUILTINS.values())
    assert {n for n, c in es.BUILTINS.items() if c.sampling == "monthly"} == {
        "ValuationStretch", "MomentumCrash", "EarningsBreadth", "YieldSpreadCompression"}
