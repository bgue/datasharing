"""picks: the names a screen selects on a date, with key ratios."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..jsonout import DEFAULT_MAX_POINTS, scalar, table
from ..operations import Operation, arg, register_operation
from ._request import clean, parse_date


@register_operation(builtin=True)
class Picks(Operation):
    """Names a screen selects on a date (default: latest), with key ratios."""
    name = "picks"
    takes_max_points = False
    rest_method, rest_path = "GET", "picks/{screen}"
    registry_order = ("screen", "asof", "lab", "columns")

    @dataclass
    class Args:
        screen: str = arg(doc="screen name")
        asof: str | None = arg(None, "date (default: the latest)")
        columns: list[str] | None = arg(None, "snapshot columns to show")

    def run(self, lab, screen, asof=None, columns=("sector", "market_cap", "pe", "pb", "div_yield",
                                                    "f_score", "mom_12_1"),
            runners_up: int = 0) -> pd.DataFrame:
        """Current picks with key ratios. `runners_up=k` appends the next k names by the screen's own
        ranking (`Screen.ranked`) and adds a boolean `runner_up` column (False for the picks)."""
        s = lab.snapshot(asof)
        sc, d = lab.screen(screen), lab._asof(asof)
        names = list(lab.market.picks(sc, d))
        n_picks = len(names)
        if runners_up:
            have = set(names)
            names += [t for t in sc.ranked(s) if t not in have][:runners_up]
        out = s.loc[names, [c for c in columns if c in s]]
        if runners_up:
            out = out.assign(runner_up=[i >= n_picks for i in range(len(names))])
        return out

    def build(self, lab, a) -> dict:
        kw = {"screen": a.screen, "asof": parse_date("asof", a.asof)}
        if a.columns:
            kw["columns"] = tuple(a.columns)
        return clean(kw)

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        return {"screen": kw["screen"], "asof": scalar(lab._asof(kw.get("asof"))), **table(result)}
