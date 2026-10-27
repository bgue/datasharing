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

Multi-stage screens. The keys above are sugar for ONE stage. For a funnel, give `stages` instead: a list of
stages, each with the same keys (`universe`, `derive`, `filters`, `graham`, `rank`) plus `keep` (an int).
Stages run in order on the rows that survived the previous one; a stage with a `rank` re-orders the survivors
(best first, rows with a NaN rank column dropped) and `keep` then cuts them to the first `keep`. The final `n`
takes the head of what is left, so leave `keep` off the last stage (`Screen.ranked`, used for runner-ups, then
sees the full ordering). Frugal Dividend, payers among the 300 largest -> 50 lowest volatility -> 10 lowest P/E:

    (ScreenBuilder("frugal", n=10)
     .universe(top=300).where("dividend > 0")
     .then_keep(50, by="vol_260d")           # new stage: rank by vol_260d asc, keep 50
     .then().rank(pe="asc")                  # new stage: rank by P/E asc; n=10 takes the head
     .build())

Builder: `.universe/.derive/.where/.graham/.rank/.keep` act on the current stage; `.then()` opens the next
stage; `.then_keep(n, by, asc=True)` is `.then().rank(by=...).keep(n)` (rank given as `by`, a column or list).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import screens as _screens
from .screens import DEFENSIVE, ENTERPRISING, GrahamTests, Screen, graham_report
from .universe import TopN

PRESETS = {"defensive": DEFENSIVE, "enterprising": ENTERPRISING}


STAGE_KEYS = ("universe", "derive", "filters", "graham", "rank", "keep")


@dataclass
class Stage:
    """One step of a screen: universe cut -> derive -> filters -> Graham tests -> rank -> keep."""
    universe: dict = field(default_factory=dict)   # top, by, sectors, exclude_sectors
    derive: dict = field(default_factory=dict)     # new column -> expression
    filters: list = field(default_factory=list)    # boolean expressions, all must hold
    graham: dict | None = None                     # {"preset": ..., <GrahamTests overrides>}
    rank: dict = field(default_factory=dict)       # column -> "asc" | "desc"
    keep: int | None = None                        # cut to the first `keep` rows after ranking

    def __post_init__(self):
        if self.keep is not None and (int(self.keep) != self.keep or self.keep < 1):
            raise ValueError(f"stage keep must be a positive integer, got {self.keep!r}")

    def to_dict(self) -> dict:
        """Only the keys that are set, so stage JSON stays short."""
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, [])}

    @classmethod
    def from_dict(cls, d: "dict | Stage") -> "Stage":
        if isinstance(d, Stage):
            return d
        bad = set(d) - set(STAGE_KEYS)
        if bad:
            raise TypeError(f"unknown stage keys: {sorted(bad)}")
        return cls(**d)


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
    stages: list = field(default_factory=list)     # list[Stage]; replaces the five single-stage fields above

    def __post_init__(self):
        self.stages = [Stage.from_dict(s) for s in self.stages or ()]
        if self.stages and any((self.universe, self.derive, self.filters, self.graham, self.rank)):
            raise ValueError("give either stages or the single-stage fields (universe, derive, filters, "
                             "graham, rank), not both")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() for s in self.stages]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenSpec":
        return cls(**d)

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), **kw)

    def build(self) -> Screen:
        params = {k: v for k, v in self.to_dict().items() if k not in ("name", "n", "note")}
        if not params["stages"]:
            del params["stages"]                    # plain single-stage spec: params exactly as before
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


def _run_stage(u: pd.DataFrame, st: Stage | dict) -> pd.DataFrame:
    """Apply one stage to the surviving rows; ranked stages return them best first."""
    st = Stage.from_dict(st)
    uni = st.universe or {}
    if uni.get("top"):                       # size cut first, then sector filters
        u = TopN(uni["top"], uni.get("by", "market_cap")).cut(u)
    if uni.get("sectors"):
        u = u[u["sector"].isin(uni["sectors"])]
    if uni.get("exclude_sectors"):
        u = u[~u["sector"].isin(uni["exclude_sectors"])]
    if st.derive:
        u = u.copy()
        for col, expr in st.derive.items():
            u[col] = u.eval(expr)
    for expr in st.filters or ():
        u = u[u.eval(expr).fillna(False).astype(bool)]
    tests = graham_tests(st.graham)
    if tests is not None and len(u):
        rep = graham_report(u, tests)
        u = u[rep["passes"] >= (tests.min_passes or rep.shape[1] - 1)]
    if st.rank:
        score = sum(u[c].rank(ascending=(d.lower() != "desc")) for c, d in st.rank.items())
        score = score[u[list(st.rank)].notna().all(axis=1)]
        u = u.loc[score.sort_values(kind="stable").index]
    if st.keep:
        u = u.head(int(st.keep))
    return u


def rule_screen(s: pd.DataFrame, n: int = 10, universe=None, derive=None, filters=(),
                graham=None, rank=None, stages=None) -> list[str]:
    """Run the stages (or the single-stage sugar arguments as one stage) in order, then take the head n."""
    if stages:
        if universe or derive or filters or graham or rank:
            raise ValueError("rule_screen: give stages or the single-stage arguments, not both")
        sts = list(stages)
    else:
        sts = [Stage(universe or {}, derive or {}, list(filters or ()), graham, rank or {})]
    u = s
    for st in sts:
        u = _run_stage(u, st)
    return u.head(n).index.tolist()


def override_params(params: dict, **kw) -> dict:
    """Params of a spec screen with overrides applied. `universe=<int>` is the legacy size-cut knob: it sets
    `top` of the first stage that has a size cut (the first stage when none does); other keys replace the
    single-stage fields (a screen built from `stages` only accepts `stages` and `universe`)."""
    p = dict(params)
    if "universe" in kw and isinstance(kw["universe"], (int, np.integer)):
        top = int(kw.pop("universe"))
        if p.get("stages"):
            sts = [Stage.from_dict(x).to_dict() for x in p["stages"]]
            i = next((i for i, x in enumerate(sts) if x.get("universe", {}).get("top")), 0)
            sts[i]["universe"] = {**sts[i].get("universe", {}), "top": top}
            p["stages"] = sts
        else:
            p["universe"] = {**(p.get("universe") or {}), "top": top}
    p.update(kw)
    return p


class ScreenBuilder:
    """Fluent wrapper around ScreenSpec.

    `universe/derive/where/graham/rank/keep` act on the current stage. A builder that never calls `then`,
    `then_keep` or `keep` produces the single-stage fields exactly as before; otherwise it produces `stages`.
    """

    def __init__(self, name: str, n: int = 10, note: str = ""):
        self.spec = ScreenSpec(name, n, note)

    @property
    def _cur(self):
        return self.spec.stages[-1] if self.spec.stages else self.spec

    def _multi(self):
        """Switch to multi-stage mode: the single-stage fields move into stage 1."""
        s = self.spec
        if not s.stages:
            s.stages = [Stage(s.universe, s.derive, s.filters, s.graham, s.rank)]
            s.universe, s.derive, s.filters, s.graham, s.rank = {}, {}, [], None, {}

    def universe(self, top: int | None = None, by: str = "market_cap", sectors=None, exclude_sectors=None):
        self._cur.universe = {k: v for k, v in dict(top=top, by=by, sectors=sectors,
                                                     exclude_sectors=exclude_sectors).items() if v}
        return self

    def derive(self, **exprs):
        self._cur.derive.update(exprs)
        return self

    def where(self, *exprs):
        self._cur.filters.extend(exprs)
        return self

    def graham(self, preset: str = "defensive", **overrides):
        self._cur.graham = {"preset": preset, **overrides}
        return self

    def rank(self, *asc_cols, **directions):
        self._cur.rank.update({c: "asc" for c in asc_cols}, **directions)
        return self

    def keep(self, n: int):
        """Cut the current stage to its first n rows after ranking (not on the last stage; use take)."""
        self._multi()
        self.spec.stages[-1].keep = n
        return self

    def then(self):
        """Open the next stage; it runs on the rows the current one kept."""
        self._multi()
        self.spec.stages.append(Stage())
        return self

    def then_keep(self, n: int, by, asc: bool = True):
        """New stage: rank by column `by` (or a list of columns, summed ranks), keep the best n."""
        self.then()
        cols = [by] if isinstance(by, str) else list(by)
        st = self.spec.stages[-1]
        st.rank = {c: "asc" if asc else "desc" for c in cols}
        st.keep = n
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
    return ScreenSpec.from_dict(spec).build()


def load_screens(path) -> list[Screen]:
    """A JSON file holding one spec or a list of specs."""
    data = json.loads(Path(path).read_text())
    return [define_screen(d) for d in (data if isinstance(data, list) else [data])]


_screens._register_spec_screens(ScreenSpec)     # built-in spec screens (specs/*/*.json), see screens.py
