"""Declarative screen definitions.

A screen is: universe -> derived columns -> filters -> (optional) Graham
tests -> ranking -> take n. Define one with the builder:

    from screenlab import ScreenBuilder
    cheap_yield = (ScreenBuilder("cheap_yield")
                   .universe(top=100, exclude_sectors=["Fin"])
                   .derive(pe_x_pb="pe * pb")
                   .where("pe < 15", "div_yield > 0.03", "f_score >= 6")
                   .rank(div_yield="desc", pe_x_pb="asc")       # composite: sum of ranks
                   .take(10)
                   .build())

or from a plain dict / JSON file (same keys as ScreenSpec):

    {"name": "cheap_yield", "n": 10,
     "universe": {"top": 100, "by": "market_cap", "exclude_sectors": ["Fin"]},
     "derive": {"pe_x_pb": "pe * pb"},
     "filters": ["pe < 15", "div_yield > 0.03"],
     "graham": {"preset": "defensive", "max_pe": 18, "min_passes": 6},
     "rank": {"div_yield": "desc", "pe_x_pb": "asc"}}

The universe's `top` size cut is applied before its sector filters.
Filters and derived columns are pandas expressions over the snapshot columns
(see data.py), e.g. "sector not in ['Fin', 'Util']" or "eps > eps_prev".
Rank columns are combined by summing their ranks, as in Greenblatt's formula.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import pandas as pd

from .screens import DEFENSIVE, ENTERPRISING, GrahamTests, Screen, graham_report

PRESETS = {"defensive": DEFENSIVE, "enterprising": ENTERPRISING}


@dataclass
class ScreenSpec:
    name: str
    n: int = 10
    note: str = ""
    universe: dict = field(default_factory=dict)   # top, by, sectors, exclude_sectors
    derive: dict = field(default_factory=dict)     # new column -> expression
    filters: list = field(default_factory=list)    # boolean expressions, all must hold
    graham: dict | None = None                     # {"preset": ..., <GrahamTests overrides>}
    rank: dict = field(default_factory=dict)       # column -> "asc" | "desc"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), **kw)

    def build(self) -> Screen:
        params = {k: v for k, v in self.to_dict().items() if k not in ("name", "n", "note")}
        return Screen(self.name, rule_screen, self.n, params, self.note or "spec screen")


def graham_tests(g: dict | str | GrahamTests | None) -> GrahamTests | None:
    if g is None or isinstance(g, GrahamTests):
        return g
    if isinstance(g, str):
        if g not in PRESETS:
            raise KeyError(f"unknown graham preset: {g}")
        return PRESETS[g]
    g = dict(g)
    base = PRESETS[g.pop("preset", "defensive")]
    if "utility_sectors" in g:
        g["utility_sectors"] = tuple(g["utility_sectors"])
    return replace(base, **g)


def rule_screen(s: pd.DataFrame, n: int = 10, universe=None, derive=None, filters=(),
                graham=None, rank=None) -> list[str]:
    u = s
    uni = universe or {}
    if uni.get("top"):                       # size cut first, then sector filters
        u = u.nlargest(uni["top"], uni.get("by", "market_cap"))
    if uni.get("sectors"):
        u = u[u["sector"].isin(uni["sectors"])]
    if uni.get("exclude_sectors"):
        u = u[~u["sector"].isin(uni["exclude_sectors"])]
    if derive:
        u = u.copy()
        for col, expr in derive.items():
            u[col] = u.eval(expr)
    for expr in filters or ():
        u = u[u.eval(expr).fillna(False).astype(bool)]
    tests = graham_tests(graham)
    if tests is not None and len(u):
        rep = graham_report(u, tests)
        u = u[rep["passes"] >= (tests.min_passes or rep.shape[1] - 1)]
    if not rank:
        return u.head(n).index.tolist()
    score = sum(u[c].rank(ascending=(d.lower() != "desc")) for c, d in rank.items())
    score = score[u[list(rank)].notna().all(axis=1)]
    return score.sort_values(kind="stable").head(n).index.tolist()


class ScreenBuilder:
    """Fluent wrapper around ScreenSpec."""

    def __init__(self, name: str, n: int = 10, note: str = ""):
        self.spec = ScreenSpec(name, n, note)

    def universe(self, top: int | None = None, by: str = "market_cap", sectors=None, exclude_sectors=None):
        self.spec.universe = {k: v for k, v in dict(top=top, by=by, sectors=sectors,
                                                     exclude_sectors=exclude_sectors).items() if v}
        return self

    def derive(self, **exprs):
        self.spec.derive.update(exprs)
        return self

    def where(self, *exprs):
        self.spec.filters.extend(exprs)
        return self

    def graham(self, preset: str = "defensive", **overrides):
        self.spec.graham = {"preset": preset, **overrides}
        return self

    def rank(self, *asc_cols, **directions):
        self.spec.rank.update({c: "asc" for c in asc_cols}, **directions)
        return self

    def take(self, n: int):
        self.spec.n = n
        return self

    def build(self) -> Screen:
        return self.spec.build()


def define_screen(spec) -> Screen:
    """Screen from a Screen, ScreenBuilder, ScreenSpec, dict, JSON string or .json path."""
    if isinstance(spec, Screen):
        return spec
    if isinstance(spec, (ScreenBuilder, ScreenSpec)):
        return spec.build()
    if isinstance(spec, (str, Path)):
        text = Path(spec).read_text() if str(spec).endswith(".json") else spec
        spec = json.loads(text)
    return ScreenSpec(**spec).build()


def load_screens(path) -> list[Screen]:
    """A JSON file holding one spec or a list of specs."""
    data = json.loads(Path(path).read_text())
    return [define_screen(d) for d in (data if isinstance(data, list) else [data])]
