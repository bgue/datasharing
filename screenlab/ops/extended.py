"""extended: real-return, currency and financing adjusted metrics (`Lab.extended`)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..errors import ServerError
from ..jsonout import DEFAULT_MAX_POINTS, table
from ..operations import Operation, _sig_defaults, arg, register_operation
from ._request import BenchmarkRequest, ConfigOverrides, MacroInput, benchmarks, clean, config_overrides, macro_from, parse_date
from .backtest import Backtest


@register_operation(builtin=True)
class Extended(Operation):
    """Real / FX / financing-adjusted metrics."""
    name = "extended"
    tracked_entry = False
    takes_max_points = False
    registry_order = ("screens", "start", "end", "config", "macro", "spread_over_prime", "leverage", "benchmark",
                      "allow_price_benchmark", "lab")
    tool_order = ("macro", "screens", "start", "end", "config", "spread_over_prime", "leverage", "benchmark",
                  "allow_price_benchmark", "lab")

    @dataclass
    class Args:
        screens: list[str] | None = arg(None, "screen names (default: every screen of the lab)")
        start: str | None = arg(None, "first formation date")
        end: str | None = arg(None, "last date")
        config: ConfigOverrides | None = arg(None, "Config overrides")
        macro: MacroInput | None = arg(None, "[{date, series, value}] records, a {series: {date: value}} mapping, "
                                             "or \"statsmodels\"")
        spread_over_prime: float = arg(0.0, "financing spread over the prime rate")
        leverage: float = arg(1.0, "leverage applied before financing costs")
        benchmark: str | BenchmarkRequest | None = arg(None, "index name, or {index, dividend_yield, mer, cost_bps}, replacing the lab's passive benchmark")
        allow_price_benchmark: bool = arg(False, "keep a price-index benchmark (excess return is then overstated); by default it is refused")

    def run(self, lab, macro=None, spread_over_prime=0.0, leverage=1.0, **bt):
        """Backtest (`bt`: the backtest arguments) and adjust it with macro series."""
        return lab.extended(lab.backtest(**bt), macro, spread_over_prime, leverage)

    def method(self, lab, report, macro, spread_over_prime: float = 0.0, leverage: float = 1.0,
               allow_price_benchmark=None) -> pd.DataFrame:
        """Real / home-currency / net-of-financing / excess-over-rf metrics for every curve in a report."""
        from ..metrics_ext import extended_summary
        lab.check_report(report, allow_price_benchmark, "this extended analysis")
        rows = {report.benchmark_label: extended_summary(report.benchmark, macro, None, spread_over_prime, leverage)}
        for name, c in report.compare.items():
            rows[name] = extended_summary(c, macro, report.benchmark, spread_over_prime, leverage)
        for name, res in report.results.items():
            rows[name] = extended_summary(res.curve, macro, report.benchmark, spread_over_prime, leverage)
        return pd.DataFrame(rows).T

    def tracking_args(self, lab, report, macro, spread_over_prime=0.0, leverage=1.0):
        return {**getattr(report, "_args", {"screens": list(report.results)}), "macro": macro,
                "spread_over_prime": spread_over_prime, "leverage": leverage}

    def defaults(self) -> dict:
        return {**Backtest().defaults(), **_sig_defaults(self.run)}

    def build(self, lab, a) -> dict:
        m = macro_from(a.macro, lab)
        if m is None:
            raise ServerError(400, "extended needs `macro`: records [{date, series, value}], "
                                   "a {series: {date: value}} mapping, or \"statsmodels\"")
        return clean(dict(screens=a.screens, start=parse_date("start", a.start), end=parse_date("end", a.end),
                          macro=m, spread_over_prime=a.spread_over_prime, leverage=a.leverage,
                          benchmark=benchmarks(a.benchmark, None)[0],
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None,
                          **config_overrides(a.config)))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        return {"benchmark": lab.passive((kw or {}).get("benchmark")).label, "table": table(result)}
