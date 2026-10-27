"""sweep.py: grids, walk-forward windows, random/bayes search, and a small end-to-end sweep.

    python -m pytest rothery_screener/tests/test_sweep.py -q
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

from rothery_screener import Lab
from rothery_screener.stats import summarize_trials
from rothery_screener.sweep import Grid, bayes_search, random_search, run_sweep, split_point, walk_forward


def test_grid_product_and_split_point():
    g = Grid({"dogs.n": [5, 10], "cost_bps": [10, 25, 50]})
    pts = list(g)
    assert len(g) == len(pts) == 6 and {"dogs.n": 10, "cost_bps": 25} in pts
    assert split_point(pts[0], "dogs") == ({"n": 5}, {"cost_bps": 10})
    with pytest.raises(ValueError, match="targets screen"):
        split_point({"piotroski.n": 5}, "dogs")


def test_walk_forward_monthly_tiles_the_index():
    idx = pd.date_range("2010-01-31", periods=120, freq="ME")           # 10 years
    w = walk_forward(idx, train_years=5, test_years=1)
    assert len(w) == 5
    for (a, b, c) in w:
        assert a < b < c
        assert a in idx and b in idx and c in idx
        assert (b.year - a.year) * 12 + b.month - a.month == 60
        assert (c.year - b.year) * 12 + c.month - b.month in (12, 11)   # last test may stop at the last date
    assert w[0][0] == idx[0] and w[-1][2] == idx[-1]
    assert [x[2] for x in w[:-1]] == [x[1] for x in w[1:]]              # test windows tile
    exp = walk_forward(idx, 3, 2, 1, expanding=True)
    assert all(x[0] == idx[0] for x in exp) and len(exp) == 6          # test_end 5..10 years


def test_walk_forward_daily_and_too_short():
    idx = pd.bdate_range("2015-01-01", "2024-12-31")
    w = walk_forward(idx, 6, 2)
    assert len(w) == 2 and w[0][1] - w[0][0] > pd.Timedelta(days=2180)
    assert abs((w[0][2] - w[0][1]).days - 730) <= 3 and w[1][2] <= idx[-1]
    assert walk_forward(idx, 9, 3) == []


def test_random_search_reproducible_and_bounded():
    spec = {"a": [1, 2, 3], "b": (0.0, 1.0), "c": (5, 9)}
    p1, p2 = random_search(spec, 20, seed=1), random_search(spec, 20, seed=1)
    assert p1 == p2 and p1 != random_search(spec, 20, seed=2)
    assert all(p["a"] in (1, 2, 3) and 0 <= p["b"] <= 1 and 5 <= p["c"] <= 9 and isinstance(p["c"], int)
               for p in p1)
    g = Grid({"x": [1, 2], "y": [3, 4]})
    assert len(random_search(g, 3, seed=0)) == 3 and len({tuple(p.values()) for p in random_search(g, 3)}) == 3
    assert random_search(g, 10) == list(g)


def test_bayes_search_without_optuna_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "optuna", None)                    # makes `import optuna` fail
    with pytest.raises(ImportError, match="optuna"):
        bayes_search(None, "dogs", {"dogs.n": (3, 8)})


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


@pytest.fixture(scope="module")
def swept(lab):
    grid = Grid({"dogs.n": [5, 10], "cost_bps": [10, 100]})
    return run_sweep(lab, "dogs", grid, splits=dict(train_years=4, test_years=1))


def test_run_sweep_routes_keys_and_tabulates(swept):
    assert len(swept) == 4
    assert list(swept["AvgN"]) == [5, 5, 10, 10]                        # dotted key reached the screen
    cheap, dear = swept[swept["dogs.n"] == 5].sort_values("cost_bps")["CAGR"]
    assert cheap > dear                                                 # bare key reached Config
    assert {"Obs", "PerYr", "Skew", "Kurt", "TrainMean", "OOS", "Picked"} <= set(swept.columns)
    assert swept["PerYr"].iloc[0] == pytest.approx(12, rel=0.02)


def test_run_sweep_walk_forward_summary(swept):
    wf, s = swept.attrs["walk_forward"], swept.attrs["summary"]
    assert len(wf) == s["windows"] >= 4 and swept["Picked"].sum() == len(wf)
    assert s["mean_oos"] == pytest.approx(wf["oos"].mean())
    assert s["oos_over_is"] == pytest.approx(s["mean_oos"] / s["mean_is"])
    # the pick on each window is the argmax of train objective over the 4 points
    for _, r in wf.iterrows():
        assert r["train"] >= -1.0 and r["train_start"] < r["train_end"] < r["test_end"]
    assert (wf["test_end"].iloc[:-1].to_numpy() == wf["train_end"].iloc[1:].to_numpy()).all()


def test_run_sweep_feeds_summarize_trials_and_custom_objective(lab, swept):
    out = summarize_trials(swept)
    assert out["DSR"].between(0, 1).all()
    t = run_sweep(lab, "dogs", [{"dogs.n": 5}, {"dogs.n": 10}], objective=lambda c, b: float(c.iloc[-1] / c.iloc[0]))
    assert list(t.columns).count("objective") == 1 and (t["objective"] > 0).all()
    assert "OOS" not in t and "walk_forward" not in t.attrs
