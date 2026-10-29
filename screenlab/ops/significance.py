"""significance: bootstrap CI, t-statistic and deflated Sharpe of excess return (`Lab.significance`)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..jsonout import DEFAULT_MAX_POINTS, table
from ..metrics import M
from ..operations import Operation, _sig_defaults, arg, register_operation
from ._request import BenchmarkRequest, ConfigOverrides, benchmarks, clean, config_overrides, parse_date
from .backtest import Backtest


@register_operation(builtin=True)
class Significance(Operation):
    """Bootstrap CI, t-stat and deflated Sharpe for screens' excess return over the benchmark."""
    name = "significance"
    tracked_entry = False               # `run` composes two tracked Lab calls (backtest, then significance)
    takes_max_points = False
    registry_order = ("screens", "start", "end", "config", "n", "seed", "benchmark", "allow_price_benchmark", "lab")

    @dataclass
    class Args:
        screens: list[str] | None = arg(None, "screen names (default: every screen of the lab)")
        start: str | None = arg(None, "first formation date")
        end: str | None = arg(None, "last date")
        config: ConfigOverrides | None = arg(None, "Config overrides")
        n: int = arg(2000, "bootstrap samples")
        seed: int = arg(0, "random seed")
        benchmark: str | BenchmarkRequest | None = arg(None, "index name, or {index, dividend_yield, mer, cost_bps}, replacing the lab's passive benchmark")
        allow_price_benchmark: bool = arg(False, "keep a price-index benchmark (excess return is then overstated); by default it is refused")

    def run(self, lab, n=2000, seed=0, **bt):
        """Backtest (`bt`: the backtest arguments) and test its excess return."""
        return lab.significance(lab.backtest(**bt), n=n, seed=seed)

    def method(self, lab, report, n: int = 2000, seed: int = 0, allow_price_benchmark=None) -> pd.DataFrame:
        """Bootstrap CI on excess return and Newey-West t-stat per screen, plus DSR across screens.

        The deflated Sharpe uses `n_trials` = the family's trial count (distinct significance runs on this
        dataset and screen base, from the store or this process) when that exceeds the number of screens;
        `result.attrs` carries `trials_in_family` and `trials_scope`."""
        from .. import stats
        lab.check_report(report, allow_price_benchmark, "this significance test")
        rows = {}
        for name, res in report.results.items():
            b = stats.bootstrap_excess(res.curve, report.benchmark, n=n, seed=seed, curves=True)
            rows[name] = {M.EXCESS_YR: b["mean"], M.CI5: b["ci_low"], M.CI95: b["ci_high"],
                          M.P_POSITIVE: b["p_positive"],
                          M.T_STAT: stats.t_stat_excess(res.curve, report.benchmark, curves=True)}
        out = pd.DataFrame(rows).T
        out[M.SHARPE] = report.table.loc[out.index, M.SHARPE]
        n_obs = int(report.benchmark.pct_change().dropna().shape[0])
        info = lab.last_trials() or {}
        n_trials = max(len(out), int(info.get("trials_in_family", 0)))
        res = stats.summarize_trials(out, n_obs=n_obs, n_trials=n_trials,
                                     periods_per_year=stats.periods_per_year(report.benchmark.index))
        res.attrs.update(info, n_trials=n_trials)
        return res

    def tracking_args(self, lab, report, n=2000, seed=0):
        return {**getattr(report, "_args", {"screens": list(report.results)}), "n": n, "seed": seed}

    def defaults(self) -> dict:
        return {**Backtest().defaults(), **_sig_defaults(self.run)}

    def build(self, lab, a) -> dict:
        bm, _ = benchmarks(a.benchmark, None)
        return clean(dict(screens=a.screens, start=parse_date("start", a.start), end=parse_date("end", a.end),
                          n=a.n, seed=a.seed, benchmark=bm,
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None, **config_overrides(a.config)))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        return {"benchmark": lab.passive((kw or {}).get("benchmark")).label, "table": table(result)}
