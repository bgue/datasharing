"""exit_evaluate: what an exit-signal screen (and optionally a policy) would have done (`Lab.exit_eval`)."""
from __future__ import annotations

from dataclasses import dataclass

from .. import backtest
from ..exit_policy import ExitPolicy, policy_overlay
from ..jsonout import DEFAULT_MAX_POINTS, curve, scalar, table
from ..operations import Operation, arg, register_operation
from ._request import MacroInput, PolicyRequest, ScreenParams, SignalSpec, clean, exit_screen, macro_from, policy_from


class _ExitArgs:
    """Request fields shared by the exit operations (documentation only)."""


@register_operation(builtin=True)
class ExitEvaluate(Operation):
    """Evaluate an exit screen (policy: ExitPolicy fields such as park, reentry, reentry_steps)."""
    name = "exit_evaluate"
    by_signals = True
    method_names = ("exit_eval",)
    rest_path = "exit/evaluate"
    registry_order = ("signals", "weights", "threshold", "min_votes", "hysteresis", "screen_params", "target",
                      "horizons", "macro", "exposure_when_flagged", "cash_rate", "lab", "max_points", "policy")

    @dataclass
    class Args:
        signals: list[SignalSpec] = arg(doc='exit signals, e.g. [{"type": "TrendBreak", "window_months": 10}, '
                                            '{"type": "Breadth", "days": 138}] (see list_signals)')
        weights: dict[str, float] | None = arg(None, "signal name -> weight (default equal)")
        threshold: float = arg(0.7, "flag turns on at this score")
        min_votes: int | None = arg(None, "signals individually at or above the threshold needed to flag")
        hysteresis: float = arg(0.1, "flag turns off below threshold - hysteresis")
        screen_params: ScreenParams | None = arg(None, "other ExitScreen fields (check, lag, min_hold_days)")
        target: str | None = arg(None, "index name or ticker to evaluate against (default: the passive index)")
        horizons: tuple[int, ...] = arg((1, 3, 12), "forward-return horizons in months")
        macro: MacroInput | None = arg(None, "macro series for the signals")
        exposure_when_flagged: float = arg(0.0, "equity exposure while flagged")
        cash_rate: float = arg(0.0, "annual rate for park='cash'")
        policy: PolicyRequest | None = arg(None, "ExitPolicy fields (park, reentry, reentry_delay_days, reentry_steps, "
                                                 "park_mer, ...); when given, the overlay trades that policy")

    def run(self, lab, exit_screen, target=None, horizons=(1, 3, 12), macro=None, policy=None, **kw):
        """Evaluate an ExitScreen against a target curve (default: the passive index).

        `exit_screen` may also be an ExitPolicy; with `policy` (or a policy as the first argument) the
        overlay in the result trades that policy (reentry, park, steps, delay, costs) instead of the
        plain flag. `cash_rate` is the rate for park="cash"."""
        from ..exit_signals import evaluate
        if isinstance(exit_screen, ExitPolicy):
            policy, exit_screen = exit_screen, exit_screen.screen
        if target is None:
            target = lab.passive().curve(lab.market, lab.config, lab.market.index[0])
        elif isinstance(target, str):
            target = lab.market.series(target)
        macro = macro if macro is not None else (policy.macro if policy is not None else None)
        if policy is None:
            return evaluate(exit_screen, lab.market, target, horizons, macro, **kw)
        ev = evaluate(exit_screen, lab.market, target, horizons, macro, **kw)
        rebal = backtest.schedule(lab.market, target.dropna().index[0], lab.config.rebalance_months)
        ev.overlay = policy_overlay(target, policy, lab.market, rebal, float(kw.get("cash_rate", 0.0)),
                                    state=ev.state, signal_curve=kw.get("curve"))
        return ev

    def build(self, lab, a) -> dict:
        es = exit_screen(lab, a.signals, a.weights, a.threshold, a.min_votes, a.hysteresis, a.screen_params)
        pol = policy_from(es, a.policy, a.exposure_when_flagged) if a.policy else None
        return clean(dict(exit_screen=es, target=a.target, horizons=tuple(a.horizons or (1, 3, 12)),
                          macro=macro_from(a.macro, lab), policy=pol,
                          exposure_when_flagged=a.exposure_when_flagged, cash_rate=a.cash_rate))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        ov = result.overlay
        return {"benchmark": lab.passive().label, "frac_flagged": scalar(result.frac_flagged),
                "forward": table(result.forward), "drawdowns": table(result.drawdowns, index=False),
                "overlay_stats": table(ov.stats), "switches": ov.switches, "turnover": scalar(ov.turnover),
                "curves": {"derisked": curve(ov.curve, max_points), "always_in": curve(ov.baseline, max_points)},
                "score": curve(result.score, max_points),
                "state": [[d, bool(v)] for d, v in curve(result.state.astype(float), max_points)]}
