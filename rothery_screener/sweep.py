"""Parameter sweeps over a screen, with walk-forward out-of-sample checks.

    from rothery_screener.sweep import Grid, run_sweep
    g = Grid({"dogs.n": [5, 10], "universe": ...})   # dotted -> screen, bare -> Config

    tbl = run_sweep(lab, "dogs", Grid({"dogs.n": [5, 10], "dogs.universe": [20, 30], "cost_bps": [25]}),
                    splits=dict(train_years=5, test_years=1))
    tbl.attrs["walk_forward"], tbl.attrs["summary"]

Each point is backtested once over the full range; in-sample and out-of-sample numbers are read off
slices of that one equity curve, so walk-forward costs no extra backtests. A point's picks at any
date use only data available then, so a test-window slice is out-of-sample for the choice of
parameters made on the preceding train window.
"""
from __future__ import annotations

import itertools
from typing import Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from . import metrics
from .stats import periods_per_year


class Grid:
    """Cartesian product of {key: [values]}; dotted keys ("dogs.n") go to the screen, bare to Config."""

    def __init__(self, spec: Mapping[str, list]):
        self.spec = {k: list(v) for k, v in spec.items()}

    def __iter__(self):
        keys = list(self.spec)
        for combo in itertools.product(*self.spec.values()):
            yield dict(zip(keys, combo))

    def __len__(self):
        return int(np.prod([len(v) for v in self.spec.values()])) if self.spec else 0


def split_point(point: Mapping, screen_name: str) -> tuple[dict, dict]:
    """(screen overrides, Config overrides) for one grid point."""
    sk, ck = {}, {}
    for k, v in point.items():
        if "." in k:
            name, param = k.split(".", 1)
            if name != screen_name:
                raise ValueError(f"key {k!r} targets screen {name!r}, but the sweep is over {screen_name!r}")
            sk[param] = v
        else:
            ck[k] = v
    return sk, ck


def walk_forward(dates, train_years: float, test_years: float, step_years: float | None = None,
                 expanding: bool = False) -> list[tuple]:
    """Windows (train_start, train_end, test_end), all snapped to the nearest dates in the index.

    Train spans `train_years` (or from the first date onward if expanding), test the next
    `test_years`; windows advance by `step_years` (default = test_years, so test windows tile
    the index end to end). Uses calendar offsets, so daily and monthly indexes both work. A window
    is kept only if its test_end fits inside the index (within one observation of the last date).
    """
    idx = pd.DatetimeIndex(dates)
    step = test_years if step_years is None else step_years
    m = lambda y: int(round(12 * y))
    d0, last = idx[0], idx[-1]
    slack = pd.Series(idx).diff().median() if len(idx) > 1 else pd.Timedelta(0)

    def snap(months):
        b = d0 + pd.DateOffset(months=months)
        if b > last + slack:
            return None
        i = idx.searchsorted(b)                     # nearest date (month-ends: Feb 28 vs Feb 29)
        i = min(i, len(idx) - 1)
        return idx[i - 1] if i > 0 and abs(idx[i - 1] - b) < abs(idx[i] - b) else idx[i]

    out, k = [], 0
    while True:
        s_m = 0 if expanding else k * m(step)
        te = snap(k * m(step) + m(train_years) + m(test_years))
        if te is None:
            break
        out.append((snap(s_m), snap(k * m(step) + m(train_years)), te))
        k += 1
    return out


def _sample(v, rng):
    if isinstance(v, tuple) and len(v) == 2:
        lo, hi = v
        return int(rng.integers(lo, hi + 1)) if isinstance(lo, int) and isinstance(hi, int) \
            else float(rng.uniform(lo, hi))
    return v[int(rng.integers(len(v)))]


def random_search(grid, n: int, seed: int = 0) -> list[dict]:
    """n random points. Lists are choices, (low, high) tuples ranges (ints stay ints).

    If every dimension is a list and n covers the whole grid, you get the grid (no repeats)."""
    spec = grid.spec if isinstance(grid, Grid) else grid
    rng = np.random.default_rng(seed)
    if all(isinstance(v, list) for v in spec.values()):
        full = list(Grid(spec))
        if n >= len(full):
            return full
        return [full[i] for i in rng.choice(len(full), n, replace=False)]
    return [{k: _sample(v, rng) for k, v in spec.items()} for _ in range(n)]


class _Evaluator:
    """Runs and caches one backtest per point; slices its curve for any window."""

    def __init__(self, lab, screen, objective, start, end, benchmark=None):
        self.lab, self.start, self.end, self.benchmark = lab, start, end, benchmark
        self.screen = lab.screen(screen)
        self.objective = objective
        self.obj_name = objective if isinstance(objective, str) else "objective"
        self.cache: dict = {}

    def run(self, point):
        key = tuple(sorted(point.items(), key=lambda kv: kv[0]))
        if key not in self.cache:
            sk, ck = split_point(point, self.screen.name)
            sc = self.screen.with_overrides(**sk) if sk else self.screen
            rep = self.lab.backtest([sc], self.start, self.end, benchmark=self.benchmark, **ck)
            self.cache[key] = (rep.results[sc.name].curve, rep.benchmark, rep.table.loc[sc.name])
        return self.cache[key]

    def value(self, point, a=None, b=None) -> float:
        curve, bench, _ = self.run(point)
        c, bn = curve.loc[a:b], bench.loc[a:b]
        if len(c) < 3:
            return np.nan
        if callable(self.objective):
            return float(self.objective(c, bn))
        return float(metrics.summarize(c, bn).get(self.objective, np.nan))

    def row(self, point) -> dict:
        curve, bench, tab = self.run(point)
        r = curve.pct_change().dropna()
        row = dict(point)
        row.update(tab.to_dict())
        row[self.obj_name] = self.value(point)
        row.update(Obs=len(r), PerYr=periods_per_year(curve.index),
                   Skew=float(r.skew()), Kurt=float(r.kurt() + 3))
        return row


def run_sweep(lab, screen, grid, objective: str | Callable = "Excess/yr", start=None, end=None,
              splits=None, benchmark=None, _ev: _Evaluator | None = None) -> pd.DataFrame:
    """Backtest every point of `grid` (a Grid, or a list of point dicts) and tabulate.

    objective: a column of the backtest table ("Excess/yr", "Sharpe", ...) or a callable
    (curve, bench_curve) -> float; higher is better. One row per point: the parameters, the full-
    range metrics, Obs/PerYr/Skew/Kurt (inputs for stats.summarize_trials), and the objective
    in-sample.

    splits: dict(train_years, test_years[, step_years, expanding]) or a list of (train_start,
    train_end, test_end) windows. Then for each window the best point on the train slice is
    chosen and scored on the test slice. Extra columns: TrainMean / OOS (a point's mean objective
    over all train / test slices) and Picked (windows in which it won). Detail is in
    df.attrs["walk_forward"] (one row per window), summary in df.attrs["summary"]: mean OOS of the
    picks, mean train objective of the picks, and ratio OOS/IS (the overfitting haircut).
    """
    ev = _ev or _Evaluator(lab, screen, objective, start, end, benchmark)
    points = list(grid)
    if not points:
        raise ValueError("empty grid")
    tbl = pd.DataFrame([ev.row(p) for p in points])
    pcols = list(dict.fromkeys(k for p in points for k in p))
    if splits is None:
        return tbl
    wins = (walk_forward(ev.run(points[0])[0].index, **splits) if isinstance(splits, Mapping)
            else list(splits))
    if not wins:
        raise ValueError("no walk-forward window fits in the sample; shorten train/test years")
    train = np.array([[ev.value(p, a, b) for (a, b, _) in wins] for p in points])
    test = np.array([[ev.value(p, b, c) for (_, b, c) in wins] for p in points])
    rows = []
    picked = np.zeros(len(points), int)
    for j, (a, b, c) in enumerate(wins):
        i = int(np.nanargmax(np.where(np.isnan(train[:, j]), -np.inf, train[:, j])))
        picked[i] += 1
        rows.append({"train_start": a, "train_end": b, "test_end": c, **{k: points[i].get(k) for k in pcols},
                     "train": train[i, j], "oos": test[i, j]})
    wf = pd.DataFrame(rows)
    tbl["TrainMean"], tbl["OOS"], tbl["Picked"] = np.nanmean(train, axis=1), np.nanmean(test, axis=1), picked
    is_, oos = wf["train"].mean(), wf["oos"].mean()
    tbl.attrs["walk_forward"] = wf
    tbl.attrs["summary"] = {"mean_oos": float(oos), "mean_is": float(is_),
                            "oos_over_is": float(oos / is_) if abs(is_) > 1e-12 else float("nan"),
                            "windows": len(wf), "points": len(points)}
    return tbl


def bayes_search(lab, screen, space: Mapping, n: int = 30, seed: int = 0,
                 objective: str | Callable = "Excess/yr", start=None, end=None, splits=None,
                 benchmark=None) -> pd.DataFrame:
    """Optuna TPE search over `space` ({key: list | (low, high)}), then the usual run_sweep table.

    With splits, the quantity maximized is the mean objective over the *train* slices of the
    walk-forward windows (never the test slices); the returned table then reports each tried
    point's OOS as run_sweep does. The trial count that DSR needs is len(result).
    """
    try:
        import optuna
    except ImportError as exc:
        raise ImportError("bayes_search needs the optional package 'optuna' (pip install optuna). "
                          "Use random_search with run_sweep for a dependency-free alternative.") from exc
    ev = _Evaluator(lab, screen, objective, start, end, benchmark)
    state: dict = {}

    def target(point):
        if splits is None:
            return ev.value(point)
        if "wins" not in state:
            state["wins"] = (walk_forward(ev.run(point)[0].index, **splits) if isinstance(splits, Mapping)
                             else list(splits))
        v = np.nanmean([ev.value(point, a, b) for (a, b, _) in state["wins"]])
        return v

    def objective_fn(trial):
        point = {}
        for k, v in space.items():
            if isinstance(v, tuple) and len(v) == 2:
                point[k] = (trial.suggest_int(k, *v) if isinstance(v[0], int) and isinstance(v[1], int)
                            else trial.suggest_float(k, *v))
            else:
                point[k] = trial.suggest_categorical(k, list(v))
        val = target(point)
        return -1e9 if not np.isfinite(val) else val

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective_fn, n_trials=n)
    tried = []
    for t in study.trials:
        if t.params not in tried:
            tried.append(dict(t.params))
    return run_sweep(lab, screen, tried, objective, start, end, splits, benchmark, _ev=ev)
