"""stats.py: exact checks on hand-built series.

    python -m pytest screenlab/tests/test_stats.py -q
"""
from __future__ import annotations

from math import sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from screenlab.stats import (bootstrap_excess, deflated_sharpe, expected_max_sharpe,
                                    periods_per_year, probabilistic_sharpe, summarize_trials,
                                    t_stat_excess)

N = NormalDist()


def monthly(vals):
    return pd.Series(vals, index=pd.date_range("2015-01-31", periods=len(vals), freq="ME"))


def test_periods_per_year_monthly_and_daily():
    assert periods_per_year(monthly([0.0] * 60).index) == pytest.approx(12, rel=0.01)
    d = pd.Series(0.0, index=pd.bdate_range("2020-01-01", "2022-12-30"))
    assert 255 < periods_per_year(d.index) < 265


def test_bootstrap_constant_excess_ci_excludes_zero():
    bench = monthly([0.004] * 60)
    port = bench + 0.005
    r = bootstrap_excess(port, bench, n=200)
    assert r["ci_low"] == pytest.approx(r["ci_high"]) == pytest.approx(r["mean"])
    assert r["mean"] == pytest.approx(0.005 * r["periods_per_year"])
    assert r["ci_low"] > 0 and r["p_positive"] == 1.0


def test_bootstrap_zero_mean_noise_contains_zero_and_is_seeded():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.03, 120)
    x -= x.mean()                                      # exactly zero mean excess
    port, bench = monthly(x), monthly([0.0] * 120)
    a, b = bootstrap_excess(port, bench, seed=4), bootstrap_excess(port, bench, seed=4)
    assert a == b
    assert a["ci_low"] < 0 < a["ci_high"] and 0.2 < a["p_positive"] < 0.8
    assert bootstrap_excess(port, bench, seed=5) != a


def test_bootstrap_curves_input_matches_returns_and_works_on_daily():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2018-01-01", periods=600)
    rb = pd.Series(rng.normal(0.0003, 0.01, 600), index=idx)
    rb.iloc[0] = 0.0
    rp = rb + 0.0004
    rp.iloc[0] = 0.0                                   # curves start at 1.0
    cb, cp = (1 + rb).cumprod(), (1 + rp).cumprod()
    a = bootstrap_excess(rp.iloc[1:], rb.iloc[1:], n=300)
    b = bootstrap_excess(cp, cb, n=300, curves=True)
    assert b["mean"] == pytest.approx(a["mean"], rel=1e-6)
    assert a["block"] == pytest.approx(periods_per_year(idx), rel=0.01) and a["ci_low"] > 0


def test_newey_west_lag0_is_plain_t_and_autocorrelation_lowers_it():
    rng = np.random.default_rng(2)
    x = rng.normal(0.002, 0.02, 200)
    s, zero = monthly(x), monthly([0.0] * 200)
    plain = x.mean() / (x.std(ddof=0) / sqrt(200))
    assert t_stat_excess(s, zero, lags=0) == pytest.approx(plain)
    # smooth (strongly autocorrelated) series: overlapping 12-period averages
    smooth = pd.Series(x).rolling(12).mean().dropna()
    smooth = monthly(smooth.to_numpy())
    z = monthly([0.0] * len(smooth))
    assert t_stat_excess(smooth, z, lags=11) < 0.5 * t_stat_excess(smooth, z, lags=0)


def test_newey_west_hand_computed_lag1():
    x = np.array([1.0, -1.0, 2.0, 0.0, 1.0, -2.0])
    u = x - x.mean()
    T = 6
    s = u @ u / T + 2 * 0.5 * (u[1:] @ u[:-1]) / T
    expected = x.mean() / sqrt(s / T)
    assert t_stat_excess(monthly(x), monthly([0.0] * 6), lags=1) == pytest.approx(expected)


def test_psr_matches_formula():
    sr, n, sk, ku = 0.1, 101, -0.5, 5.0
    z = sr * 10 / sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    assert probabilistic_sharpe(sr, n, sk, ku) == pytest.approx(N.cdf(z))


def test_deflated_sharpe_single_trial_equals_psr():
    assert deflated_sharpe([0.12], 90, skew=-0.3, kurt=4.0) == pytest.approx(
        probabilistic_sharpe(0.12, 90, -0.3, 4.0, 0.0))
    # annualized input: converted to per-period units
    assert deflated_sharpe([0.9], 120, periods_per_year=12) == pytest.approx(
        probabilistic_sharpe(0.9 / sqrt(12), 120))


def test_deflated_sharpe_two_trials_by_hand():
    sr = [0.1, 0.3]
    var = np.var(sr, ddof=1)
    g = 0.5772156649015329
    sr0 = sqrt(var) * ((1 - g) * N.inv_cdf(0.5) + g * N.inv_cdf(1 - 1 / (2 * np.e)))
    assert expected_max_sharpe(var, 2) == pytest.approx(sr0)
    assert deflated_sharpe(sr, 100) == pytest.approx(N.cdf((0.3 - sr0) * sqrt(99) / sqrt(1 + 0.5 * 0.09)))


def test_more_trials_means_lower_dsr():
    sr = [0.5, 0.2, 0.3, 0.1]
    base = deflated_sharpe(sr, 60, periods_per_year=12)
    assert deflated_sharpe(sr, 60, periods_per_year=12, n_trials=40) < base < deflated_sharpe([0.5], 60, periods_per_year=12)


def test_summarize_trials_columns_and_consistency():
    tbl = pd.DataFrame({"Sharpe": [0.2, 0.9, 0.5, np.nan]}, index=list("abcd"))
    with pytest.raises(ValueError):
        summarize_trials(tbl)
    out = summarize_trials(tbl, n_obs=120, periods_per_year=12)
    assert {"DSR", "BonfP", "Haircut"} <= set(out.columns) and "DSR" not in tbl
    assert out.loc["b", "DSR"] == pytest.approx(deflated_sharpe([0.2, 0.9, 0.5], 120, periods_per_year=12))
    assert out["DSR"].idxmax() == "b" and np.isnan(out.loc["d", "DSR"])
    p = 1 - probabilistic_sharpe(0.9 / sqrt(12), 120)
    assert out.loc["b", "BonfP"] == pytest.approx(min(1, 3 * p))
    assert 0 <= out.loc["b", "Haircut"] <= 1
