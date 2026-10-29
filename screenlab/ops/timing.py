"""timing: rebalance-timing study (`Lab.timing`, see ../timing.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..jsonout import DEFAULT_MAX_POINTS, curve, table
from ..operations import Operation, arg, register_operation
from ._request import BenchmarkRequest, ConfigOverrides, benchmarks, clean, config_overrides, parse_date


def _extract_timing(obj, j, out):
    from ..store import _as_frame
    out.summary[j("distribution")] = obj.distribution()
    out.bulk[j("offsets")] = obj.table
    out.bulk[j("curves")] = obj.curves
    if obj.tranche is not None:
        out.bulk[j("tranche")] = _as_frame(obj.tranche.rename("tranche"))


@register_operation(builtin=True)
class Timing(Operation):
    """Rebalance-timing study: how much the result depends on the start date / phase."""
    name = "timing"
    long_running = True
    registry_order = ("screen", "mode", "stride", "start", "end", "config", "benchmark", "allow_price_benchmark", "lab",
                      "max_points")

    @dataclass
    class Args:
        screen: str = arg(doc="screen name")
        mode: Literal["phase", "start"] = arg("phase", "'phase': shift the rebalance anniversary; 'start': start on a later day")
        stride: int = arg(1, "test every Nth trading day")
        start: str | None = arg(None, "first formation date")
        end: str | None = arg(None, "last date")
        config: ConfigOverrides | None = arg(None, "Config overrides")
        benchmark: str | BenchmarkRequest | None = arg(None, "index name, or {index, dividend_yield, mer, cost_bps}, replacing the lab's passive benchmark")
        allow_price_benchmark: bool = arg(False, "keep a price-index benchmark (excess return is then overstated); by default it is refused")

    def run(self, lab, screen, mode: str = "phase", stride: int = 1, start=None, end=None,
            offsets=None, benchmark=None, progress: bool = False, allow_price_benchmark=None, **cfg):
        """Rebalance-timing study (see timing.py); returns a timing.TimingResult."""
        from .. import timing          # lazy: lets `python -m screenlab.timing` run cleanly
        return timing.study(lab.screen(screen), lab.market, lab._cfg(**cfg), start, end, mode,
                            stride, offsets, lab.benchmark_for(benchmark, allow_price_benchmark, "this timing study"), progress)

    def build(self, lab, a) -> dict:
        cfg = config_overrides(a.config)
        return clean(dict(screen=a.screen, mode=a.mode, stride=a.stride, start=parse_date("start", a.start),
                          end=parse_date("end", a.end), benchmark=benchmarks(a.benchmark, None)[0],
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None, **cfg))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        out = {"benchmark": lab.passive().label, "screen": result.screen, "table": table(result.table),
               "distribution": table(result.distribution())}
        if result.tranche is not None:
            out["tranche"] = curve(result.tranche, max_points)
        return out


from .. import store as _store  # noqa: E402
_store.register_extractor("TimingResult", _extract_timing)
