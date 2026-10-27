"""Exit-signal calibration: two hand-built crashes, constraints, stability, daily and monthly indexes.

    python -m pytest rothery_screener/tests/test_calibration.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rothery_screener import Lab, Market, metrics
from rothery_screener.calibration import CalibrationResult, calibrate_exit
from rothery_screener.exit_policy import ExitPolicy
from rothery_screener.exit_signals import ExitScreen, TrendBreak, VolRegime

CRASHES = (("2004-03-01", "2004-10-01"), ("2010-03-01", "2010-10-01"))


def make_index(freq="B"):
    dates = pd.date_range("2000-01-03", "2014-12-31", freq=freq)
    n = len(dates)
    rng = np.random.default_rng(1)
    per_year = 252 if freq == "B" else 12
    sd = 0.004 if freq == "B" else 0.02
    r = 0.09 / per_year + rng.normal(0, sd, n)
    for a, b in CRASHES:
        i0, i1 = dates.searchsorted(pd.Timestamp(a)), dates.searchsorted(pd.Timestamp(b))
        r[i0:i1] = np.log(0.65) / (i1 - i0) + rng.normal(0, sd / 2, i1 - i0)
    idx = pd.Series(np.exp(np.cumsum(r)), index=dates)
    fund = pd.DataFrame({"date": pd.to_datetime([]), "ticker": []})
    return Market(fund, pd.DataFrame({"A": idx}), pd.DataFrame({"IDX": idx})), idx


@pytest.fixture(scope="module")
def daily():
    return make_index("B")


@pytest.fixture(scope="module")
def cal(daily):
    mkt, idx = daily
    grid = {"threshold": [0.5, 0.7, 0.9], "hysteresis": [0.1], "check": ["D", "W-FRI"]}
    return calibrate_exit(ExitScreen([TrendBreak("IDX", window_months=6)]), mkt, "IDX", grid,
                          splits=dict(train_years=5, test_years=2), cost_bps=5.0)


def test_result_shape_and_tables(cal):
    assert isinstance(cal, CalibrationResult) and len(cal.table) == 6
    assert {"Calmar", "Sharpe", "FracFlagged", "Switches", "Feasible", "OOS", "TrainMean", "Picked", "DSR",
            "Obs", "PerYr", "Skew", "Kurt"} <= set(cal.table.columns)
    assert cal.table["Picked"].sum() == len(cal.windows) == cal.summary["windows"] >= 4
    assert cal.summary["mean_oos"] == pytest.approx(cal.windows["oos"].mean())
    assert cal.summary["oos_over_is"] == pytest.approx(cal.summary["mean_oos"] / cal.summary["mean_is"])
    assert 0 < cal.summary["dsr"] <= 1 and cal.summary["stability"] == cal.stability
    assert isinstance(cal.screen, ExitScreen) and isinstance(cal.policy, ExitPolicy)
    assert cal.screen.threshold == cal.best["threshold"] and cal.screen.check == cal.best["check"]
    wf = cal.windows
    assert (wf["train_start"] < wf["train_end"]).all() and (wf["train_end"] < wf["test_end"]).all()


def test_calibrated_screen_flags_both_crashes_and_beats_always_in_oos(daily, cal):
    mkt, idx = daily
    st = cal.screen.state(mkt)
    for a, b in CRASHES:
        w = st[pd.Timestamp(a):pd.Timestamp(b)]
        assert w.any() and w.mean() > 0.3
    # out of sample (mean over test windows) the overlay Calmar beats always-in on the same windows
    always = []
    for _, r in cal.windows.iterrows():
        s = metrics.summarize(idx.loc[r.train_end:r.test_end])
        always.append(s["CAGR"] / abs(s["MaxDD"]))
    assert cal.summary["mean_oos"] > np.mean(always)


def test_constraints_exclude_points_from_selection(daily):
    mkt, idx = daily
    scr = ExitScreen([TrendBreak("IDX", window_months=6)])
    grid = {"threshold": [0.5, 0.7, 0.9], "hysteresis": [0.0, 0.1]}
    free = calibrate_exit(scr, mkt, "IDX", grid)
    cap = float(free.table["SwitchesPerYr"].median())
    res = calibrate_exit(scr, mkt, "IDX", grid, constraints={"max_switches_per_year": cap})
    t = res.table
    assert (~t["Feasible"]).any() and t["Feasible"].any()
    assert (t.loc[~t["Feasible"], "SwitchesPerYr"] > cap).all()
    assert len(t) == len(free.table)                                     # violators stay in the table
    chosen = t[(t[list(grid)] == pd.Series(res.best)).all(axis=1)].iloc[0]
    assert chosen["Feasible"] and chosen["SwitchesPerYr"] <= cap
    with pytest.raises(ValueError, match="constraints"):
        calibrate_exit(scr, mkt, "IDX", grid, constraints={"min_time_flagged": 0.99})
    # with splits the same constraint applies to every train window's pick
    wf = calibrate_exit(scr, mkt, "IDX", grid, splits=dict(train_years=5, test_years=2),
                        constraints={"max_time_flagged": 0.12})
    assert (wf.table.loc[wf.table["Picked"] > 0, "FracFlagged"] < 0.2).all()


def test_stability_is_one_when_one_point_always_wins(daily):
    mkt, _ = daily
    res = calibrate_exit(ExitScreen([TrendBreak("IDX", window_months=6)]), mkt, "IDX",
                         {"threshold": [0.7], "check": ["D"]}, splits=dict(train_years=5, test_years=2))
    assert res.stability == 1.0 and res.table["Picked"].iloc[0] == len(res.windows)


def test_dotted_signal_keys_rebuild_signals_and_other_keys(daily):
    mkt, _ = daily
    scr = ExitScreen([TrendBreak("IDX"), VolRegime("IDX")])
    res = calibrate_exit(scr, mkt, "IDX", {"signals.TrendBreak.window_months": [4, 10], "lag": [1, 2],
                                           "min_hold_days": [0, 30], "exposure_when_flagged": [0.0, 0.5]})
    assert len(res.table) == 16 and res.table["Calmar"].nunique() > 4
    assert res.screen.signals[0].window_months == res.best["signals.TrendBreak.window_months"]
    assert res.policy.exposure_when_flagged == res.best["exposure_when_flagged"]
    assert scr.signals[0].window_months == 10                            # base screen untouched
    with pytest.raises(ValueError, match="unknown grid key"):
        calibrate_exit(scr, mkt, "IDX", {"bogus": [1]})
    with pytest.raises(ValueError, match="not in the screen"):
        calibrate_exit(scr, mkt, "IDX", {"signals.Nope.x": [1]})


@pytest.mark.parametrize("objective", ["Sharpe", "CAGR", "MaxDD", "ForwardGap",
                                       lambda ov, ev: float(ov.curve.iloc[-1] - ev.frac_flagged)])
def test_objectives_run(daily, objective):
    mkt, _ = daily
    res = calibrate_exit(ExitScreen([TrendBreak("IDX", window_months=6)]), mkt, "IDX",
                         {"threshold": [0.6, 0.9]}, objective=objective,
                         splits=dict(train_years=5, test_years=2))
    assert np.isfinite(res.summary["mean_oos"]) and len(res.windows) >= 3


def test_monthly_index_runs():
    mkt, idx = make_index("ME")
    res = calibrate_exit(ExitScreen([TrendBreak("IDX", window_months=6)]), mkt, idx,
                         {"threshold": [0.6, 0.9], "check": ["ME", "D"]}, splits=dict(train_years=5, test_years=2))
    assert res.table["PerYr"].iloc[0] == pytest.approx(12, rel=0.02) and len(res.windows) >= 4


def test_best_overlay_plugs_into_backtest_and_lab_method():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    res = lab.calibrate_exit(ExitScreen([TrendBreak("SYN_CAPWT", window_months=6)]),
                             {"threshold": [0.6, 0.8]}, splits=dict(train_years=4, test_years=2))
    assert len(res.windows) >= 2
    rep = lab.backtest(["dogs"], exit=res.policy)
    assert rep.results["dogs"].exposure.min() <= 1.0


def test_calibration_passes_target_curve_to_curve_signals():
    """Regression: curve-based signals (PortfolioStop) used to be dropped silently in calibration."""
    from rothery_screener import Lab, ExitScreen, PortfolioStop
    from rothery_screener.calibration import calibrate_exit
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8, seed=5)
    curve = lab.market.series("SYN_CAPWT")
    res = calibrate_exit(ExitScreen([PortfolioStop(0.10)]), lab.market, curve,
                         {"threshold": [0.5, 0.9]}, "Calmar")
    assert (res.table["FracFlagged"] > 0).any()
