"""exit_calibrate: walk-forward calibration of an exit screen's thresholds and timing (`Lab.calibrate_exit`)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import ServerError
from ..jsonout import DEFAULT_MAX_POINTS, jsonify
from ..metrics import M
from ..operations import Operation, arg, register_operation
from ..tracking import grid_points, note_points
from ._request import (ConstraintsRequest, MacroInput, PolicyRequest, ScreenParams, SignalSpec, SplitsRequest,
                       clean, exit_screen, macro_from, policy_from)


@register_operation(builtin=True)
class ExitCalibrate(Operation):
    """Walk-forward calibration of an exit screen (policy: fixed ExitPolicy fields for every grid point)."""
    name = "exit_calibrate"
    by_signals = True
    method_names = ("calibrate_exit",)
    long_running = True
    counts_points = True
    takes_max_points = False
    rest_path = "exit/calibrate"
    registry_order = ("signals", "grid", "weights", "threshold", "min_votes", "hysteresis", "screen_params",
                      "objective", "splits", "constraints", "target", "macro", "lab", "policy", "allow_price_benchmark")

    @dataclass
    class Args:
        signals: list[SignalSpec] = arg(doc="exit signals (see list_signals)")
        grid: dict[str, list[Any]] = arg(doc='{parameter: [values]}, e.g. {"threshold": [0.6, 0.8], '
                                             '"signals.Breadth.days": [100, 138]}; may also sweep reentry, park, ...')
        weights: dict[str, float] | None = arg(None, "signal name -> weight")
        threshold: float = arg(0.7, "base flag threshold")
        min_votes: int | None = arg(None, "signals needed to flag")
        hysteresis: float = arg(0.1, "base hysteresis")
        screen_params: ScreenParams | None = arg(None, "other ExitScreen fields")
        objective: str = arg(M.CALMAR, "Calmar, Sharpe, CAGR, MaxDD or ForwardGap")
        splits: SplitsRequest | None = arg(None, "walk-forward windows")
        constraints: ConstraintsRequest | None = arg(None, "feasibility constraints")
        target: str | None = arg(None, "index name or ticker (default: the passive index)")
        macro: MacroInput | None = arg(None, "macro series for the signals")
        policy: PolicyRequest | None = arg(None, "fixed ExitPolicy fields for every grid point")
        allow_price_benchmark: bool = arg(False, "calibrate against a price-index passive benchmark (refused by default)")

    def run(self, lab, exit_screen, grid, target=None, objective=M.CALMAR, splits=None,
            constraints=None, macro=None, allow_price_benchmark=None, **kw):
        """Walk-forward calibration of an ExitScreen's thresholds / timing (see calibration.py).
        `target`: index name, ticker or curve (default: the passive index curve)."""
        from ..calibration import calibrate_exit
        kw.setdefault("rebalance_months", lab.config.rebalance_months)
        if target is None:
            target = lab.benchmark_for(None, allow_price_benchmark, "this exit calibration").curve(
                lab.market, lab.config, lab.market.index[0])
        n = (note_points(lab, "exit_calibrate", lab._tl.args, grid_points(grid))
             if getattr(lab._tl, "args", None) is not None else 0)
        res = calibrate_exit(exit_screen, lab.market, target, grid, objective, splits, constraints, macro,
                             n_trials=n or None, **kw)
        res.summary.update(lab.last_trials() or {})
        return res

    def build(self, lab, a) -> dict:
        if lab is not None and getattr(lab, "calibrate_exit", None) is None:
            raise ServerError(501, "Lab.calibrate_exit is not available in this build of screenlab")
        es = exit_screen(lab, a.signals, a.weights, a.threshold, a.min_votes, a.hysteresis, a.screen_params)
        kw = {"policy": policy_from(es, a.policy, 0.0)} if a.policy else {}
        return clean(dict(exit_screen=es, grid=a.grid, objective=a.objective, splits=a.splits,
                          constraints=a.constraints, target=a.target, macro=macro_from(a.macro, lab),
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None, **kw))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        out = jsonify(result, max_points)
        if isinstance(out, dict):
            out = {"benchmark": lab.passive().label, **out}
        return out
