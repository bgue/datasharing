"""graham: Graham-criteria funnel and per-test pass table."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace

import pandas as pd

from ..errors import ServerError
from ..jsonout import DEFAULT_MAX_POINTS, series_table, table
from ..operations import Operation, arg, register_operation
from ..screens import graham_funnel, graham_report
from ..spec import graham_tests
from ._request import GrahamOverrides, clean, parse_date


@register_operation(builtin=True)
class Graham(Operation):
    """Graham tests: per-name pass table and cumulative funnel. overrides are GrahamTests fields."""
    name = "graham"
    takes_max_points = False
    rest_method, rest_path = "GET", "graham"
    registry_order = ("preset", "asof", "overrides", "lab")

    @dataclass
    class Args:
        preset: str = arg("defensive", "GrahamTests preset")
        asof: str | None = arg(None, "date (default: the latest)")
        overrides: GrahamOverrides | None = arg(None, "GrahamTests fields to change")

    def run(self, lab, tests="defensive", asof=None, **overrides) -> tuple[pd.DataFrame, pd.Series]:
        """(per-name pass/fail table, cumulative funnel) for a preset or GrahamTests."""
        t = graham_tests(tests)
        t = replace(t, **overrides) if overrides else t
        s = lab.snapshot(asof)
        return graham_report(s, t), graham_funnel(s, t)

    def build(self, lab, a) -> dict:
        overrides = a.overrides
        if overrides is not None and not isinstance(overrides, dict):
            raise ServerError(400, "`overrides` must be an object of GrahamTests fields")
        from ..screens import GrahamTests
        known = [f.name for f in dataclasses.fields(GrahamTests)]
        bad = sorted(set(overrides or {}) - set(known))
        if bad:
            raise ServerError(400, f"unknown graham override {bad[0]!r}; use {known}")
        return clean(dict(tests=a.preset, asof=parse_date("asof", a.asof), **(overrides or {})))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        rep, funnel = result
        return {"preset": (kw or {}).get("tests", "defensive"), "table": table(rep),
                "funnel": series_table(funnel, "step", "count")}
