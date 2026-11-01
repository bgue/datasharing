"""LA: named expressions ("my columns"): docs/SCREEN_LIBRARY.md section 4, docs/SCREENS.md "Screens that say what they
need, and your own columns".

A named expression is a formula with a name (`shy = coalesce(div_yield, 0) + coalesce(buyback_yield, 0)`) that a screen
may use wherever it can use a column. Two groups:

    Starter columns   shipped in code (STARTER), read-only: shy, ps, payout, gpa, pct_of_high
    My columns        <screens folder>/columns.json  ({"name": {"expr": "...", "note": "..."}}), written atomically

`expand_spec(spec)` inlines the named expressions a spec uses (and the ones they use, in dependency order) into `derive` of
every stage that needs them, ahead of the stage's own derived columns, so the expanded spec is self-contained: a saved
screen, its run key and its fingerprint hold the formulas, and a later edit of the library never changes an old run.
"""
from __future__ import annotations

import json
import keyword
import logging
import os
import re

import pandas as pd

from .errors import ServerError
from .spec import HELPERS, ExpressionError, check_expression, eval_expr, expr_names, screens_folder

_log = logging.getLogger(__name__)

GROUP_STARTER, GROUP_MINE = "Starter columns", "My columns"
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
STARTER: dict = {
    "shy": {"expr": "coalesce(div_yield, 0) + coalesce(buyback_yield, 0)",
            "note": "shareholder yield: dividend yield plus buyback yield (a missing part counts as 0)"},
    "ps": {"expr": "market_cap / sales", "note": "price to sales: market cap over annual revenue"},
    "payout": {"expr": "dividend / eps", "note": "dividend payout ratio: dividends per share over earnings per share"},
    "gpa": {"expr": "gross_margin * asset_turnover",
            "note": "gross profit over assets (Novy-Marx): gross margin times asset turnover"},
    "pct_of_high": {"expr": "price / high_52w", "note": "price as a fraction of its 52-week high (1.0 = at the high)"},
}


class ColumnLibrary:
    """The starter columns plus the user's `columns.json` in the screens folder (`SCREENLAB_SCREENS` or
    `~/.screenlab/screens`). `starter_only` ignores the file (what the built-in registry uses, so built-ins never
    depend on the user's folder). The file is read on every call: it is small and edits show at once."""

    def __init__(self, folder=None, starter_only: bool = False):
        self.folder, self.starter_only = folder, starter_only

    @property
    def path(self):
        return screens_folder(self.folder) / "columns.json"

    def mine(self) -> dict:
        """{name: {"expr", "note"}} of the user's columns; a missing file is empty and a bad file or entry is skipped
        with a log line."""
        if self.starter_only or not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("columns.json holds one object: {name: {expr, note}}")
        except Exception as e:                                  # noqa: BLE001  (a bad file must not stop a lab or a run)
            _log.warning("columns file %s ignored: %s", self.path, e)
            return {}
        out = {}
        for n, v in raw.items():
            if (isinstance(v, dict) and isinstance(v.get("expr"), str) and v["expr"].strip() and NAME_RE.fullmatch(str(n))
                    and n not in STARTER):
                out[n] = {"expr": v["expr"], "note": str(v.get("note") or "")}
            else:
                _log.warning("columns file %s: entry %r skipped", self.path, n)
        return out

    def entries(self) -> dict:
        """{name: {"expr", "note", "group"}}: the starter columns, then the user's."""
        return {**{n: {**v, "group": GROUP_STARTER} for n, v in STARTER.items()},
                **{n: {**v, "group": GROUP_MINE} for n, v in self.mine().items()}}

    def exprs(self) -> dict:
        return {n: v["expr"] for n, v in self.entries().items()}

    def write(self, mine: dict) -> None:
        """Replace the user's columns by `mine` ({name: {expr, note}}): written to a temp file, then moved over."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({n: {"expr": v["expr"], "note": v.get("note", "")} for n, v in mine.items()}, indent=2) + "\n")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------------------------------
# dependency order and expansion
# ---------------------------------------------------------------------------------------------------
def closure(names, exprs: dict, skip=frozenset()) -> list:
    """`names` and everything they use, dependencies first (each once). Names in `skip` (already columns of the rows) and
    names that are not in `exprs` are leaves. A cycle raises ValueError with its path: "a -> b -> a"."""
    order: list = []
    state: dict = {}

    def visit(n, path):
        if n in skip or n not in exprs:
            return
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            cyc = path[path.index(n):] + [n]
            raise ValueError("a named expression cannot use itself, directly or through others: " + " -> ".join(cyc))
        state[n] = 1
        for d in expr_names(exprs[n]):
            visit(d, path + [n])
        state[n] = 2
        order.append(n)
    for n in names:
        visit(n, [])
    return order


def _stage_refs(st: dict) -> list:
    """Names a stage's expressions and rank keys refer to, in order (the universe `by` is not among them)."""
    refs: list = []
    for expr in [*(st.get("derive") or {}).values(), *(st.get("filters") or ()), *(st.get("exclude") or ())]:
        if isinstance(expr, str):
            refs += expr_names(expr)
    refs += [c for c in (st.get("rank") or {}) if isinstance(c, str)]
    return list(dict.fromkeys(refs))


_STAGE_KEYS = ("universe", "derive", "filters", "graham", "rank", "exclude")


def expand_spec(spec: dict, library: ColumnLibrary | None = None):
    """(expanded spec, uses). Every named expression the spec uses (and its dependencies) is added to `derive` of each stage
    that uses it and does not already have the column (from the stage's own `derive` or an earlier stage), ahead of the
    stage's own derived columns. A named expression used as `universe.by` goes into a derive-only stage placed before the
    stage (the size cut runs before `derive`). `uses` lists {"name", "group", "expr", "note"} in order of first use.
    A spec that uses none comes back unchanged (the same object). A cycle in the library raises ValueError."""
    lib = (library if library is not None else ColumnLibrary()).entries()
    if not lib or not isinstance(spec, dict):
        return spec, []
    exprs = {n: v["expr"] for n, v in lib.items()}
    multi = bool(spec.get("stages"))
    stages = [dict(s) if isinstance(s, dict) else s for s in spec["stages"]] if multi else \
        [{k: spec[k] for k in _STAGE_KEYS if k in spec}]
    if not all(isinstance(s, dict) for s in stages):
        return spec, []
    for s in stages:
        if not isinstance(s.get("derive") or {}, dict) or not isinstance(s.get("rank") or {}, dict):
            return spec, []
    out_stages: list = []
    defined: set = set()
    uses: dict = {}
    changed = False

    def take(order):
        for n in order:
            uses.setdefault(n, {"name": n, "group": lib[n]["group"], "expr": exprs[n], "note": lib[n]["note"]})
    for st in stages:
        own = dict(st.get("derive") or {})
        uni = st.get("universe") or {}
        by = uni.get("by") if isinstance(uni, dict) and uni.get("top") else None
        if isinstance(by, str) and by in exprs and by not in defined and by not in own:
            order = closure([by], exprs, defined)
            out_stages.append({"derive": {n: exprs[n] for n in order}})
            defined |= set(order)
            take(order)
            changed = True
        need = [n for n in _stage_refs(st) if n in exprs and n not in defined and n not in own]
        if need:
            order = closure(need, exprs, defined)
            st = {**st, "derive": {**{n: exprs[n] for n in order}, **own}}
            defined |= set(order)
            take(order)
            changed = True
        defined |= set(own)
        out_stages.append(st)
    if not changed:
        return spec, []
    if multi or len(out_stages) > 1:
        body = {k: v for k, v in spec.items() if k not in _STAGE_KEYS and k != "stages"}
        return {**body, "stages": out_stages}, list(uses.values())
    return {**spec, **{k: v for k, v in out_stages[0].items() if k == "derive"}}, list(uses.values())


# ---------------------------------------------------------------------------------------------------
# evaluation, validation, define / delete
# ---------------------------------------------------------------------------------------------------
def closure_columns(name: str, entries: dict) -> list:
    """Names (not themselves named expressions) that `name`'s formula and its dependencies refer to."""
    exprs = {n: v["expr"] for n, v in entries.items()}
    try:
        order = closure([name], exprs)
    except ValueError:
        order = [name]
    return [c for n in order for c in expr_names(exprs[n]) if c not in exprs]


def evaluate(snap: pd.DataFrame, name: str, exprs: dict) -> pd.Series:
    """The named expression `name` evaluated on `snap` (its dependencies first, as the expansion would)."""
    u = snap.copy()
    for n in closure([name], exprs):
        u[n] = eval_expr(u, exprs[n])
    return u[name]


def evaluate_all(snap: pd.DataFrame, entries: dict):
    """({name: Series} for the columns that evaluate on `snap`, {name: error message} for those that do not)."""
    exprs = {n: v["expr"] for n, v in entries.items()}
    ok, bad = {}, {}
    for n in entries:
        try:
            ok[n] = evaluate(snap, n, exprs)
        except Exception as e:                                  # noqa: BLE001  (a missing column, a type problem)
            bad[n] = str(e) or type(e).__name__
    return ok, bad


def reserved_names(snap: pd.DataFrame) -> set:
    """Names a new column may not take: snapshot columns (and every documented column), helpers, starter columns."""
    from .data import COLUMN_INFO
    return set(snap.columns) | set(COLUMN_INFO) | set(HELPERS) | set(STARTER)


def define_column(snap: pd.DataFrame, asof, name: str, expr: str, note: str = "", overwrite: bool = False,
                  library: ColumnLibrary | None = None) -> dict:
    """Validate and store a named expression; `snap` is the last full snapshot. Raises ValueError (400: bad name, bad
    expression, cycle, an expression that raises on `snap`) or ServerError(409: name taken). Returns
    {"name", "expr", "note", "group", "path", "replaced", "uses", "warnings", "coverage", "asof"}."""
    lib = library if library is not None else ColumnLibrary()
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or keyword.iskeyword(name) or name.startswith("__"):
        raise ValueError(f"{name!r} is not a usable column name: letters, digits and _, starting with a letter or _, at most 64")
    if name in STARTER:
        raise ServerError(409, f"{name!r} is a starter column and cannot be redefined; choose another name")
    if name in HELPERS:
        raise ServerError(409, f"{name!r} is an expression helper; choose another name")
    if name in reserved_names(snap):
        raise ServerError(409, f"{name!r} is a column of the data (or a documented column); choose another name")
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("the expression is empty")
    expr = expr.strip()
    mine = lib.mine()
    if name in mine and not overwrite:
        raise ServerError(409, f"{name!r} is already defined as {mine[name]['expr']!r}; pass overwrite=true to replace it")
    entries = lib.entries()
    try:
        used = check_expression(expr, set(snap.columns) | set(entries) | {name})
    except ExpressionError as e:
        raise ValueError(e.message + (f" (did you mean {e.suggestion!r}?)" if e.suggestion else "")) from None
    exprs = {**{n: v["expr"] for n, v in entries.items()}, name: expr}
    deps = closure([name], exprs)                                # raises on a cycle, with the path
    try:
        res = evaluate(snap, name, exprs)
    except Exception as e:                                       # noqa: BLE001
        raise ValueError(f"the expression does not evaluate on the {pd.Timestamp(asof).date()} snapshot: "
                         f"{str(e) or type(e).__name__}") from None
    if not isinstance(res, pd.Series):
        raise ValueError("the expression gives a single value; a column needs one value per name")
    warnings = []
    cov = float(res.notna().mean()) if len(res) else 0.0
    if not res.notna().any():
        warnings.append(f"{name} is missing for every name on {pd.Timestamp(asof).date()}")
    existed = name in mine
    mine[name] = {"expr": expr, "note": str(note or "")}
    lib.write(mine)
    return {"name": name, "expr": expr, "note": mine[name]["note"], "group": GROUP_MINE, "path": str(lib.path),
            "replaced": existed, "uses": [d for d in deps if d != name],
            "warnings": warnings, "coverage": cov, "asof": str(pd.Timestamp(asof).date())}


def delete_column(name: str, library: ColumnLibrary | None = None) -> dict:
    """Remove a user column. ServerError 404 when there is none, 409 for a starter column."""
    lib = library if library is not None else ColumnLibrary()
    if name in STARTER:
        raise ServerError(409, f"{name!r} is a starter column and cannot be deleted")
    mine = lib.mine()
    if name not in mine:
        raise ServerError(404, f"no column of yours named {name!r}; yours: {sorted(mine)}")
    gone = mine.pop(name)
    lib.write(mine)
    return {"name": name, "deleted": True, "expr": gone["expr"], "path": str(lib.path)}
