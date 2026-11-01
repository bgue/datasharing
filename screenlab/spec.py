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

# SB1: Exclude criteria, sector include list and expression helpers.
`exclude` is a list of expressions like `filters`, with the opposite meaning: a row is dropped when the expression
is TRUE. A missing value (NaN) is never excluded, while an include filter drops it (`pe < 15` drops a name with no
P/E, `exclude: ["pe > 40"]` keeps it). The order inside a stage is: universe cut, derive, filters, exclude, graham,
rank, keep. `universe.include_sectors` keeps only those sectors (`exclude_sectors` drops them; the older
`sectors` key means the same as `include_sectors`). All of derive, filters and exclude see the same rows, those
that remain after the stage's universe cut, so a helper such as `pct(pe)` ranks a name among the stage's universe
and not among the survivors of the filter before it. Rank keys are column names (derive an expression first).

    {"universe": {"top": 100, "include_sectors": ["Ind", "Tech"]},
     "filters": ["pct(pe) < 0.3", "div_yield > 0.02"],
     "exclude": ["z(vol_1y) > 2", "eps < 0"]}

Expression helpers, usable in derive, filters and exclude next to the snapshot columns (reserved names, they
shadow a column of the same name). `col` is any column or expression; the statistics are taken over the rows
the stage works on (see above), skipping missing values:
    pct(col)         percentile rank, 0..1: the share of rows whose value is <= this row's (1/N for the lowest,
                     1.0 for the highest, ties share the average); NaN stays NaN
    q(col, p)        the p-quantile (p in 0..1) of col, linear interpolation: one number
    z(col)           z-score: (col - mean) / sample standard deviation (ddof=1); NaN when there is no spread
    log(x) sqrt(x)   natural log and square root; NaN (not an error or -inf) where x <= 0 or x < 0
    abs(x)           absolute value
    isna(col)        true where col is missing
    coalesce(a, b)   a where it is present, else b (a column, expression or number)
Everything else in an expression is a column name, a number, a string, a list or tuple of literals for
`in` / `not in`, arithmetic (+ - * / % ** //), comparison and `and` / `or` / `not` (also `&` `|` `~`).
`check_expression(text, names)` verifies this whitelist (no attribute access, no other calls, no `@`, no
subscripts, no dunder names; only `col.notna()` / `col.isna()` survive as method calls) before anything is evaluated; the screen-builder operations run it.

Builder: `.universe/.derive/.where/.exclude/.graham/.rank/.keep` act on the current stage; `.then()` opens the next
stage; `.then_keep(n, by, asc=True)` is `.then().rank(by=...).keep(n)` (rank given as `by`, a column or list).
"""
from __future__ import annotations

import ast
import difflib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import screens as _screens
from .screens import DEFENSIVE, ENTERPRISING, GrahamTests, Screen, graham_report
from .universe import TopN

PRESETS = {"defensive": DEFENSIVE, "enterprising": ENTERPRISING}


STAGE_KEYS = ("universe", "derive", "filters", "graham", "rank", "keep", "exclude")


@dataclass
class Stage:
    """One step of a screen: universe cut -> derive -> filters -> Graham tests -> rank -> keep."""
    universe: dict = field(default_factory=dict)   # top, by, sectors, exclude_sectors
    derive: dict = field(default_factory=dict)     # new column -> expression
    filters: list = field(default_factory=list)    # boolean expressions, all must hold
    graham: dict | None = None                     # {"preset": ..., <GrahamTests overrides>}
    rank: dict = field(default_factory=dict)       # column -> "asc" | "desc"
    keep: int | None = None                        # cut to the first `keep` rows after ranking
    exclude: list = field(default_factory=list)    # SB1: boolean expressions; a row is dropped when one is true

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
    universe: dict = field(default_factory=dict)   # top, by, include_sectors, exclude_sectors
    derive: dict = field(default_factory=dict)     # new column -> expression
    filters: list = field(default_factory=list)    # boolean expressions, all must hold
    graham: dict | None = None                     # {"preset": ..., <GrahamTests overrides>}
    rank: dict = field(default_factory=dict)       # column -> "asc" | "desc"
    stages: list = field(default_factory=list)     # list[Stage]; replaces the single-stage fields above
    exclude: list = field(default_factory=list)    # SB1: boolean expressions; a row is dropped when one is true

    def __post_init__(self):
        self.stages = [Stage.from_dict(s) for s in self.stages or ()]
        if self.stages and any((self.universe, self.derive, self.filters, self.graham, self.rank, self.exclude)):
            raise ValueError("give either stages or the single-stage fields (universe, derive, filters, "
                             "exclude, graham, rank), not both")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() for s in self.stages]
        if not d["exclude"]:                        # SB1: serialised only when set: old fingerprints stay byte-identical
            del d["exclude"]
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


# SB1: ---- expression helpers and the safety check --------------------------------------------------------
HELPERS = {                       # name -> (min args, max args or None, signature, one-line description)
    "pct": (1, 1, "pct(col)", "percentile rank 0..1 among the rows the stage works on (1/N lowest, 1.0 highest); NaN stays NaN"),
    "q": (2, 2, "q(col, p)", "the p-quantile (p in 0..1) of col among those rows: one number"),
    "z": (1, 1, "z(col)", "z-score among those rows: (col - mean) / sample standard deviation"),
    "log": (1, 1, "log(x)", "natural log; missing where x <= 0"),
    "sqrt": (1, 1, "sqrt(x)", "square root; missing where x < 0"),
    "abs": (1, 1, "abs(x)", "absolute value"),
    "isna": (1, 1, "isna(col)", "true where col is missing"),
    "coalesce": (2, None, "coalesce(a, b)", "a where it is present, else b"),
}


class ExpressionError(ValueError):
    """An expression the whitelist (or Python's parser) refuses. `position` is the 1-based character (None when
    it is about the whole text), `suggestion` a did-you-mean name or None."""

    def __init__(self, message: str, position: int | None = None, suggestion: str | None = None):
        super().__init__(message)
        self.message, self.position, self.suggestion = message, position, suggestion


def _suggest(word: str, choices) -> str | None:
    hit = difflib.get_close_matches(word, [c for c in choices if isinstance(c, str)], n=1, cutoff=0.6)
    return hit[0] if hit else None


_BIN_OK = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv, ast.BitAnd, ast.BitOr)
_CMP_OK = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
_UNARY_OK = (ast.Not, ast.USub, ast.UAdd, ast.Invert)
_NA_METHODS = ("notna", "isna", "notnull", "isnull")


def check_expression(text: str, names) -> list:
    """Verify `text` is a safe expression over the column `names` and the helpers; returns the column names it uses
    (in order, no repeats) or raises `ExpressionError` (message, 1-based `position`, did-you-mean `suggestion`).
    Allowed: names from `names`, numbers, strings, True/False/None, list/tuple of literals (right side of in / not in),
    arithmetic, comparison, and/or/not, & | ~, and calls of the helpers (HELPERS) with positional arguments.
    Not allowed: attribute access, subscripts, other calls, `@`, lambdas, comprehensions, dunder names. The one
    exception is the argument-free `col.notna()` / `col.isna()` (also notnull / isnull) on a column, because the
    built-in value_momentum spec is written that way; `isna(col)` is the preferred form."""
    if not isinstance(text, str) or not text.strip():
        raise ExpressionError("the expression is empty")
    names = set(names)
    lead = len(text) - len(text.lstrip())
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        pos = (e.offset or 1) + lead
        raise ExpressionError(f"syntax error at character {pos}: {e.msg}", pos) from None
    used: list = []

    def at(node) -> int:
        return node.col_offset + 1 + lead

    def bad(node, what):
        raise ExpressionError(f"{what} is not allowed in an expression (character {at(node)})", at(node))

    def literal(node):
        return (isinstance(node, ast.Constant) or
                (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and literal(node.operand)))

    def walk(node):
        if isinstance(node, ast.Constant):
            if node.value is Ellipsis or isinstance(node.value, (bytes, complex)):
                bad(node, "this constant")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                bad(node, f"the name {node.id!r}")
            if node.id in HELPERS:
                raise ExpressionError(f"{node.id!r} is a function: write {HELPERS[node.id][2]}", at(node))
            if node.id not in names:
                raise ExpressionError(f"unknown column {node.id!r} (character {at(node)})", at(node),
                                      _suggest(node.id, [*names, *HELPERS]))
            if node.id not in used:
                used.append(node.id)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                walk(v)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY_OK):
                bad(node, "this operator")
            walk(node.operand)
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BIN_OK):
                bad(node, "this operator (matrix multiply, shifts and xor are not available)")
            walk(node.left)
            walk(node.right)
        elif isinstance(node, ast.Compare):
            walk(node.left)
            for op, right in zip(node.ops, node.comparators):
                if not isinstance(op, _CMP_OK):
                    bad(node, "this comparison (use ==, !=, <, <=, >, >=, in, not in)")
                if isinstance(op, (ast.In, ast.NotIn)):
                    if not (isinstance(right, (ast.List, ast.Tuple)) and all(literal(e) for e in right.elts)):
                        raise ExpressionError("`in` / `not in` needs a list of literals on the right, like ['Fin', 'Util']",
                                              at(right))
                else:
                    walk(right)
        elif isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr in _NA_METHODS and isinstance(fn.value, ast.Name)
                    and not node.args and not node.keywords):
                walk(fn.value)                      # the one method form kept: `col.notna()` (built-in value_momentum)
                return
            if not isinstance(fn, ast.Name):
                bad(node, "a call of anything but a helper")
            if fn.id not in HELPERS:
                raise ExpressionError(f"unknown function {fn.id!r} (character {at(fn)}); available: "
                                      f"{', '.join(HELPERS)}", at(fn), _suggest(fn.id, HELPERS))
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                bad(node, "keyword or starred arguments")
            lo, hi, sig, _ = HELPERS[fn.id]
            if len(node.args) < lo or (hi is not None and len(node.args) > hi):
                raise ExpressionError(f"{fn.id} takes {'exactly ' + str(lo) if hi == lo else 'at least ' + str(lo)} "
                                      f"argument(s): {sig}", at(node))
            for a in node.args:
                walk(a)
        elif isinstance(node, (ast.List, ast.Tuple)):
            bad(node, "a list or tuple here (only as the right side of in / not in)")
        else:
            bad(node, {"Attribute": "attribute access", "Subscript": "indexing", "Lambda": "lambda",
                       "IfExp": "if/else", "ListComp": "a comprehension", "Dict": "a dict",
                       "NamedExpr": ":="}.get(type(node).__name__, type(node).__name__))

    walk(tree.body)
    return used


def _called(expr: str) -> set:
    """Names of the functions an expression calls (empty when it does not parse: pandas then reports the error)."""
    try:
        return {n.func.id for n in ast.walk(ast.parse(expr.strip(), mode="eval"))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    except SyntaxError:
        return set()


def _helper_fns(index) -> dict:
    """The helper functions as Series/scalar callables (see HELPERS); `index` backs scalar arguments."""
    def ser(x):
        return x if isinstance(x, pd.Series) else pd.Series(x, index=index, dtype=float)

    def pct(col):
        return ser(col).rank(pct=True)

    def q(col, p):
        return float(ser(col).quantile(p))

    def z(col):
        c = ser(col).astype(float)
        sd = c.std()
        return (c - c.mean()) / sd if sd and sd == sd else c * np.nan

    def log(x):
        s = ser(x).astype(float)
        return np.log(s.where(s > 0))

    def sqrt(x):
        s = ser(x).astype(float)
        return np.sqrt(s.where(s >= 0))

    def coalesce(a, *rest):
        out = ser(a)
        for b in rest:
            out = out.fillna(b.reindex(out.index) if isinstance(b, pd.Series) else b)
        return out
    return {"pct": pct, "q": q, "z": z, "log": log, "sqrt": sqrt, "abs": lambda x: ser(x).abs(),
            "isna": lambda col: ser(col).isna(), "coalesce": coalesce}


def eval_expr(u: pd.DataFrame, expr: str):
    """`u.eval(expr)`; when the expression calls a helper (pct, q, z, log, sqrt, abs, isna, coalesce) they are
    evaluated over the rows of `u` (python engine). Expressions without helper calls take exactly the old path."""
    if _called(expr) & set(HELPERS):
        return u.eval(expr, engine="python", resolvers=[_helper_fns(u.index)])
    return u.eval(expr)


def _mask(u: pd.DataFrame, expr: str) -> pd.Series:
    """True where the expression is true; missing counts as false."""
    return eval_expr(u, expr).fillna(False).astype(bool)


def _universe_cut(u: pd.DataFrame, uni: dict) -> pd.DataFrame:
    """The size cut first, then the sector filters."""
    if uni.get("top"):
        u = TopN(uni["top"], uni.get("by") or "market_cap").cut(u)
    inc = [*(uni.get("sectors") or ()), *(uni.get("include_sectors") or ())]
    if inc:
        u = u[u["sector"].isin(inc)]
    if uni.get("exclude_sectors"):
        u = u[~u["sector"].isin(uni["exclude_sectors"])]
    return u


def _apply_graham(u: pd.DataFrame, g) -> pd.DataFrame:
    tests = graham_tests(g)
    if tests is not None and len(u):
        rep = graham_report(u, tests)
        u = u[rep["passes"] >= (tests.min_passes or rep.shape[1] - 1)]
    return u


def _apply_rank(u: pd.DataFrame, rank: dict) -> pd.DataFrame:
    if rank:
        score = sum(u[c].rank(ascending=(d.lower() != "desc")) for c, d in rank.items())
        score = score[u[list(rank)].notna().all(axis=1)]
        u = u.loc[score.sort_values(kind="stable").index]
    return u


def _run_stage(u: pd.DataFrame, st: Stage | dict) -> pd.DataFrame:
    """Apply one stage to the surviving rows; ranked stages return them best first."""
    st = Stage.from_dict(st)
    u = _universe_cut(u, st.universe or {})
    if st.derive:
        u = u.copy()
        for col, expr in st.derive.items():
            u[col] = eval_expr(u, expr)
    if st.filters or st.exclude:                 # SB1: every criterion sees the same rows (matters for pct, q, z)
        keep = pd.Series(True, index=u.index)
        for expr in st.filters or ():
            keep &= _mask(u, expr)
        for expr in st.exclude or ():
            keep &= ~_mask(u, expr)              # missing is never excluded
        u = u[keep]
    u = _apply_graham(u, st.graham)
    u = _apply_rank(u, st.rank)
    if st.keep:
        u = u.head(int(st.keep))
    return u


def rule_screen(s: pd.DataFrame, n: int = 10, universe=None, derive=None, filters=(),
                graham=None, rank=None, stages=None, exclude=()) -> list[str]:
    """Run the stages (or the single-stage sugar arguments as one stage) in order, then take the head n."""
    if stages:
        if universe or derive or filters or graham or rank or exclude:
            raise ValueError("rule_screen: give stages or the single-stage arguments, not both")
        sts = list(stages)
    else:
        sts = [Stage(universe or {}, derive or {}, list(filters or ()), graham, rank or {}, exclude=list(exclude or ()))]
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
            s.stages = [Stage(s.universe, s.derive, s.filters, s.graham, s.rank, exclude=s.exclude)]
            s.universe, s.derive, s.filters, s.graham, s.rank, s.exclude = {}, {}, [], None, {}, []

    def universe(self, top: int | None = None, by: str = "market_cap", sectors=None, exclude_sectors=None,
                 include_sectors=None):
        self._cur.universe = {k: v for k, v in dict(top=top, by=by, sectors=sectors, include_sectors=include_sectors,
                                                     exclude_sectors=exclude_sectors).items() if v}
        return self

    def derive(self, **exprs):
        self._cur.derive.update(exprs)
        return self

    def where(self, *exprs):
        self._cur.filters.extend(exprs)
        return self

    def exclude(self, *exprs):
        """SB1: drop the rows for which any expression is true (missing values are kept)."""
        self._cur.exclude.extend(exprs)
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


# SB1: ---- saved screens: <folder>/<name>.json ------------------------------------------------------------
_log = logging.getLogger(__name__)
SCREEN_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")


def screens_folder(folder=None) -> Path:
    """Where saved screens live: `folder`, else the SCREENLAB_SCREENS environment variable, else ~/.screenlab/screens."""
    if folder:
        return Path(folder).expanduser()
    return Path(os.environ.get("SCREENLAB_SCREENS") or Path.home() / ".screenlab" / "screens").expanduser()


def load_saved_screens(lab, folder=None) -> list:
    """Define every `*.json` spec in the screens folder in `lab` (`lab.define`) and return the names loaded. A screen
    already in `lab.screens` is left alone (built-ins can never be replaced by a file); a file that is not a valid
    spec is skipped with a log line, never fatal. A missing folder loads nothing."""
    d = screens_folder(folder)
    done: list = []
    if not d.is_dir():
        return done
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if not isinstance(data, dict):
                raise ValueError("a saved screen file holds one spec object")
            sp = ScreenSpec.from_dict(data)
            if not SCREEN_NAME_RE.fullmatch(sp.name or ""):
                raise ValueError(f"bad screen name {sp.name!r}")
            if sp.name in lab.screens:
                _log.info("saved screen %s skipped: %r is already defined in the lab", f.name, sp.name)
                continue
            lab.define(sp)
            done.append(sp.name)
        except Exception as e:                      # noqa: BLE001  (a bad file must not stop a server or the TUI)
            _log.warning("saved screen %s skipped: %s", f, e)
    return done


_screens._register_spec_screens(ScreenSpec)     # built-in spec screens (specs/*/*.json), see screens.py
