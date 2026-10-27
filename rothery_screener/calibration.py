"""Calibrating an exit screen's timing and thresholds, with walk-forward out-of-sample checks.

    from rothery_screener.calibration import calibrate_exit
    res = calibrate_exit(ExitScreen([TrendBreak("SP500"), VolRegime("SP500")]), mkt, "SP500",
                         grid={"threshold": [0.6, 0.7, 0.8], "check": ["D", "W-FRI"],
                               "signals.TrendBreak.window_months": [8, 10, 12]},
                         splits=dict(train_years=8, test_years=4))
    res.table, res.windows, res.summary, res.best, res.screen

Each grid point is run once over the whole target curve (signal components are cached per distinct
set of signal parameters); in-sample and out-of-sample numbers are read off slices of that one
overlay curve, exactly as `sweep.run_sweep` does for entry screens. Every signal is causal, so the
flag in a test window uses nothing from after it. Within a window the best *feasible* point on the
train slice is chosen and scored on the test slice; `stability` is the share of windows won by the
most frequent point.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from . import metrics
from .exit_policy import ExitPolicy, policy_overlay
from .exit_signals import (ExitOverlay, ExitScreen, OverlayResult, evaluate_state, overlay)
from .stats import periods_per_year, summarize_trials
from .sweep import walk_forward

SCREEN_KEYS = ("threshold", "hysteresis", "check", "lag", "min_hold_days", "weights", "min_votes")
OVERLAY_KEYS = ("exposure_when_flagged",)
POLICY_KEYS = ("reentry", "reentry_delay_days", "reentry_steps", "park", "exposure_when_flagged")
OBJECTIVES = ("Calmar", "Sharpe", "CAGR", "MaxDD", "ForwardGap")


@dataclass
class CalibrationResult:
    table: pd.DataFrame               # one row per grid point: parameters, full-range metrics, OOS, Picked
    windows: pd.DataFrame             # one row per walk-forward window (empty without splits)
    summary: dict                     # mean_oos, mean_is, oos_over_is, stability, dsr, ...
    best: dict                        # the modal grid point (most windows won), or best in-sample if no splits
    screen: ExitScreen                # ExitScreen built from `best`
    exit_overlay: ExitOverlay         # `screen` + best exposure_when_flagged, ready for Config.exit_overlay
    policy: ExitPolicy = None         # `screen` + every best policy key, ready for Config.exit / Lab.backtest(exit=)

    @property
    def stability(self) -> float:
        return self.summary.get("stability", float("nan"))

    def __str__(self):
        with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 140):
            return (f"best: {self.best}\n{self.summary}\n\n{self.windows}")


def _screen_for(base: ExitScreen, point: Mapping) -> tuple[ExitScreen, float | None]:
    """(ExitScreen with the point applied, exposure_when_flagged override)."""
    fields, sig_over, expo = {}, {}, None
    point = {k: v for k, v in point.items() if k not in POLICY_KEYS or k == "exposure_when_flagged"}
    for k, v in point.items():
        if k.startswith("signals."):
            try:
                _, name, param = k.split(".", 2)
            except ValueError:
                raise ValueError(f"signal key must look like 'signals.<Name>.<param>', got {k!r}") from None
            sig_over.setdefault(name, {})[param] = v
        elif k in SCREEN_KEYS:
            fields[k] = v
        elif k in OVERLAY_KEYS:
            expo = float(v)
        else:
            raise ValueError(f"unknown grid key {k!r}; use {SCREEN_KEYS + POLICY_KEYS} or signals.<Name>.<param>")
    if sig_over:
        names = {s.name for s in base.signals}
        missing = set(sig_over) - names
        if missing:
            raise ValueError(f"grid targets signals {sorted(missing)} that are not in the screen {sorted(names)}")
        fields["signals"] = [replace(s, **sig_over[s.name]) if s.name in sig_over else s for s in base.signals]
    return replace(base, **fields), expo


def _policy_for(base: ExitPolicy | None, scr: ExitScreen, point: Mapping, expo: float,
                cost_bps: float, macro) -> ExitPolicy | None:
    """ExitPolicy for a grid point when it (or the base policy) sets anything beyond the plain flag, else None."""
    over = {k: point[k] for k in POLICY_KEYS if k in point and k != "exposure_when_flagged"}
    if base is None and not over:
        return None
    if base is None:
        base = ExitPolicy(scr, switch_cost_bps=cost_bps, macro=macro)
    return replace(base, screen=scr, exposure_when_flagged=expo, **over)


def _key(x):
    return repr(sorted(x.items())) if isinstance(x, Mapping) else repr(x)


def _slice_stats(ov: OverlayResult, state: pd.Series, a, b, rf: float) -> dict:
    c = ov.curve.loc[a:b]
    if len(c) < 3:
        return {}
    s = metrics.summarize(c, rf=rf)
    yrs = max((c.index[-1] - c.index[0]).days / 365.25, 1e-9)
    sw = int((ov.exposure.loc[a:b].diff().abs() > 1e-12).sum())
    s["Calmar"] = s["CAGR"] / max(abs(s["MaxDD"]), 1e-9)
    s["Switches"], s["SwitchesPerYr"] = sw, sw / yrs
    s["FracFlagged"] = float(state.loc[a:b].mean())
    return s


def calibrate_exit(exit_screen: ExitScreen, mkt, target, grid, objective: str | Callable = "Calmar",
                   splits=None, constraints: Mapping | None = None, macro=None,
                   exposure_when_flagged: float = 0.0, cash_rate: float | pd.Series = 0.0,
                   cost_bps: float = 5.0, policy: ExitPolicy | None = None, rebalance_months: int = 12,
                   rebalance_dates=None) -> CalibrationResult:
    """Grid-search an ExitScreen's parameters against `target` (a series name in `mkt`, or a curve).

    grid: {key: [values]} or a list of point dicts. Keys: threshold, hysteresis, check, lag,
    min_hold_days, min_votes, weights (list of weight dicts), exposure_when_flagged, and dotted
    "signals.<Name>.<param>" which rebuilds that signal (e.g. signals.TrendBreak.window_months).
    objective: "Calmar" (CAGR / |MaxDD| of the overlay curve), "Sharpe", "CAGR", "MaxDD", "ForwardGap"
    (unflagged minus flagged mean 12m forward return, horizons clipped at the window end) or a callable
    (OverlayResult, ExitEvaluation) -> float, evaluated on each window slice. Higher is better.
    constraints: max_switches_per_year, max_time_flagged, min_time_flagged. Violating points stay in
    the table (column Feasible, over the full range) but are never selected; selection applies the
    constraints to the *train* slice only.
    splits: dict(train_years, test_years[, step_years, expanding]) as in sweep.walk_forward, or a
    list of (train_start, train_end, test_end).
    `cost_bps` is charged per unit of exposure traded (overlay `cost`), `cash_rate` earns on the
    derisked part.
    Policy keys: reentry, reentry_delay_days, reentry_steps, park and exposure_when_flagged may also be
    grid keys (or fixed through `policy`, an ExitPolicy whose other fields -- park_mer, switch_cost_bps,
    ... -- apply to every point; its screen is ignored in favour of `exit_screen`). Such points are traded
    by `exit_policy.policy_overlay`; the "next_rebalance" modes use the entry rebalance dates
    (`rebalance_dates`, else every `rebalance_months` from the target's first date). A callable objective
    still gets the plain-flag `evaluate_state` overlay, not the policy one.
    """
    curve = (mkt.series(target) if isinstance(target, str) else target).dropna().astype(float)
    spec = grid if isinstance(grid, Mapping) else None
    points = ([dict(zip(spec, c)) for c in itertools.product(*[list(v) for v in spec.values()])]
              if spec is not None else [dict(p) for p in grid])
    if not points:
        raise ValueError("empty grid")
    if not (callable(objective) or objective in OBJECTIVES):
        raise ValueError(f"objective must be one of {OBJECTIVES} or a callable")
    cons = dict(constraints or {})
    bad = set(cons) - {"max_switches_per_year", "max_time_flagged", "min_time_flagged"}
    if bad:
        raise ValueError(f"unknown constraints {sorted(bad)}")
    rf = float(cash_rate.mean()) if isinstance(cash_rate, pd.Series) else float(cash_rate)
    cost = cost_bps / 1e4
    obj_name = objective if isinstance(objective, str) else "objective"

    reb = rebalance_dates
    if reb is None and (policy is not None or any(k in POLICY_KEYS and k != "exposure_when_flagged"
                                                   for p in points for k in p)):
        from .backtest import schedule
        reb = schedule(mkt, curve.index[0], rebalance_months)
    comp_cache: dict = {}
    runs = []
    for p in points:
        scr, expo = _screen_for(exit_screen, p)
        expo = (policy.exposure_when_flagged if policy is not None else exposure_when_flagged) if expo is None else expo
        ck = tuple(sorted((k, _key(v)) for k, v in p.items() if k.startswith("signals.")))
        if ck not in comp_cache:
            comp_cache[ck] = scr.components(mkt, macro)
        comp = comp_cache[ck]
        score, state = scr.score(mkt, macro, comp), scr.state(mkt, macro, comp)
        st = state.reindex(curve.index, method="pad").fillna(False)
        pol = _policy_for(policy, scr, p, expo, cost_bps, macro)
        if pol is None:
            ov = overlay(curve, st, expo, cash_rate, cost, scr.lag)
        else:
            ov = policy_overlay(curve, pol, mkt, reb, rf, state=state)
        runs.append(dict(point=p, screen=scr, expo=expo, score=score, state=st, ov=ov, policy=pol))

    def feasible(stats: dict) -> bool:
        if not stats:
            return False
        return (stats["SwitchesPerYr"] <= cons.get("max_switches_per_year", np.inf)
                and stats["FracFlagged"] <= cons.get("max_time_flagged", 1.0)
                and stats["FracFlagged"] >= cons.get("min_time_flagged", 0.0))

    def value(r, a, b) -> float:
        c = r["ov"].curve.loc[a:b]
        if len(c) < 3:
            return np.nan
        if objective == "ForwardGap" or callable(objective):
            ev = evaluate_state(r["score"].loc[a:b], r["state"].loc[a:b], curve.loc[a:b],
                                (12,) if objective == "ForwardGap" else (1, 3, 12),
                                exposure_when_flagged=r["expo"], cash_rate=cash_rate, cost=cost,
                                lag=r["screen"].lag)
            if callable(objective):
                return float(objective(ev.overlay, ev))
            f = ev.forward
            try:
                return float(f.loc[(12, "unflagged"), "mean"] - f.loc[(12, "flagged"), "mean"])
            except KeyError:
                return np.nan
        s = _slice_stats(r["ov"], r["state"], a, b, rf)
        return float(s.get(objective, np.nan)) if s else np.nan

    rows = []
    for r in runs:
        full = _slice_stats(r["ov"], r["state"], curve.index[0], curve.index[-1], rf)
        rets = r["ov"].curve.pct_change().dropna()
        row = dict(r["point"])
        row.update(CAGR=full["CAGR"], MaxDD=full["MaxDD"], Sharpe=full["Sharpe"], Calmar=full["Calmar"],
                   FracFlagged=full["FracFlagged"], Switches=full["Switches"],
                   SwitchesPerYr=full["SwitchesPerYr"], Feasible=feasible(full))
        row[obj_name] = value(r, curve.index[0], curve.index[-1])
        row.update(Obs=len(rets), PerYr=periods_per_year(r["ov"].curve.index),
                   Skew=float(rets.skew()), Kurt=float(rets.kurt() + 3))
        rows.append(row)
    tbl = pd.DataFrame(rows)
    pcols = list(dict.fromkeys(k for p in points for k in p))

    wf = pd.DataFrame()
    summary: dict = {"points": len(points), "feasible_points": int(tbl["Feasible"].sum())}
    best_i = None
    if splits is not None:
        wins = (walk_forward(curve.index, **splits) if isinstance(splits, Mapping) else list(splits))
        if not wins:
            raise ValueError("no walk-forward window fits in the sample; shorten train/test years")
        train = np.array([[value(r, a, b) for (a, b, _) in wins] for r in runs])
        test = np.array([[value(r, b, c) for (_, b, c) in wins] for r in runs])
        ok = np.array([[feasible(_slice_stats(r["ov"], r["state"], a, b, rf)) for (a, b, _) in wins]
                       for r in runs])
        picked = np.zeros(len(points), int)
        wrows = []
        for j, (a, b, c) in enumerate(wins):
            tr = np.where(ok[:, j] & np.isfinite(train[:, j]), train[:, j], -np.inf)
            if not np.isfinite(tr).any():
                continue
            i = int(np.argmax(tr))
            picked[i] += 1
            wrows.append({"train_start": a, "train_end": b, "test_end": c,
                          **{k: points[i].get(k) for k in pcols}, "train": train[i, j], "oos": test[i, j]})
        if not wrows:
            raise ValueError("no grid point satisfies the constraints in any train window")
        wf = pd.DataFrame(wrows)
        tbl["TrainMean"], tbl["OOS"], tbl["Picked"] = np.nanmean(train, axis=1), np.nanmean(test, axis=1), picked
        best_i = int(np.argmax(picked))                 # modal point; ties -> first in grid order
        is_, oos = wf["train"].mean(), wf["oos"].mean()
        summary.update(mean_oos=float(oos), mean_is=float(is_),
                       oos_over_is=float(oos / is_) if abs(is_) > 1e-12 else float("nan"),
                       windows=len(wf), stability=float(picked.max() / len(wf)))
    else:
        score = np.where(tbl["Feasible"] & tbl[obj_name].notna(), tbl[obj_name], -np.inf)
        if not np.isfinite(score).any():
            raise ValueError("no grid point satisfies the constraints")
        best_i = int(np.argmax(score))

    tr = summarize_trials(tbl.assign(Sharpe=tbl["Sharpe"].astype(float)))
    tbl["DSR"] = tr["DSR"].to_numpy()
    summary["dsr"] = float(tbl["DSR"].iloc[best_i])
    tbl.attrs["walk_forward"], tbl.attrs["summary"] = wf, summary
    best = dict(points[best_i])
    scr = runs[best_i]["screen"]
    return CalibrationResult(tbl, wf, summary, best, scr,
                             ExitOverlay(scr, runs[best_i]["expo"], cost_bps, macro),
                             runs[best_i]["policy"] or ExitPolicy(scr, runs[best_i]["expo"],
                                                                  switch_cost_bps=cost_bps, macro=macro))
