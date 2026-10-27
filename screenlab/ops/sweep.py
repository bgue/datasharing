"""sweep: grid / random-search sweep with walk-forward checks (`Lab.sweep`, see ../sweep.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..jsonout import DEFAULT_MAX_POINTS, jsonify, scalar, table
from ..metrics import M
from ..operations import Operation, arg, register_operation
from ..tracking import grid_points, note_points
from ._request import SplitsRequest, clean, parse_date


@register_operation(builtin=True)
class Sweep(Operation):
    """Parameter sweep. grid: {"dogs.n": [5, 10], "cost_bps": [10, 50]} (dotted = screen, bare = Config)."""
    name = "sweep"
    long_running = True
    counts_points = True
    takes_max_points = False
    registry_order = ("screen", "grid", "objective", "start", "end", "splits", "lab")

    @dataclass
    class Args:
        screen: str = arg(doc="screen name")
        grid: dict[str, list[Any]] = arg(doc="{parameter: [values]}; dotted keys hit the screen, bare keys Config")
        objective: str = arg(M.EXCESS_YR, "column of the backtest table to maximise")
        start: str | None = arg(None, "first formation date")
        end: str | None = arg(None, "last date")
        splits: SplitsRequest | None = arg(None, "walk-forward windows (adds out-of-sample columns)")

    def run(self, lab, screen, grid, objective=M.EXCESS_YR, start=None, end=None, splits=None,
            benchmark=None) -> pd.DataFrame:
        """Grid / random-search sweep; dotted keys ("dogs.n") hit the screen, bare keys Config.
        `splits=dict(train_years=.., test_years=..)` adds walk-forward out-of-sample columns.
        Every grid point counts as a trial: `result.attrs["n_trials"]` (also `trials_in_family`,
        `trials_scope`) is what `stats.summarize_trials(result, n_trials=...)` should deflate by."""
        from ..sweep import Grid, run_sweep
        if isinstance(grid, dict):
            grid = Grid(grid)
        n = note_points(lab, "sweep", lab._tl.args or {}, grid_points(grid.spec if isinstance(grid, Grid) else grid)) \
            if getattr(lab._tl, "args", None) is not None else 0
        tbl = run_sweep(lab, lab.screen(screen), grid, objective, start, end, splits, lab.passive(benchmark))
        info = lab.last_trials() or {}
        tbl.attrs.update(info, n_trials=max(len(tbl), n))
        return tbl

    def build(self, lab, a) -> dict:
        return clean(dict(screen=a.screen, grid=a.grid, objective=a.objective, start=parse_date("start", a.start),
                          end=parse_date("end", a.end), splits=a.splits))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        kw = kw or {}
        out = {"benchmark": lab.passive().label, "screen": kw["screen"], "objective": kw.get("objective", M.EXCESS_YR),
               "table": table(result, index=False)}
        summ = result.attrs.get("summary")
        if summ is not None:
            out["summary"] = jsonify(summ)
        if "walk_forward" in result.attrs:
            out["walk_forward"] = jsonify(result.attrs["walk_forward"])
        try:
            from ..stats import summarize_trials
            n = int(result.attrs.get("n_trials") or len(result))
            out["n_trials"] = n
            out["dsr"] = scalar(summarize_trials(result, n_trials=n)[M.DSR].max())
        except Exception:                                           # e.g. a custom objective table without Sharpe
            pass
        return out
