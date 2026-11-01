"""SB1: screen_schema, screen_preview, screen_save, screen_load: the engine side of the Screen tab (docs/SCREEN_BUILDER.md).

None of them trades, runs a backtest or counts as a trial (`tracking_args` is None, `tracked_entry` False).
`preview_screen(snapshot, spec, show)` is the engine behind screen_preview: it validates every expression
(`spec.check_expression`), runs the stages with the very functions `rule_screen` uses, and reports a funnel
(rows in / rows out / alone per criterion) plus the picks. Errors are collected, not raised.
"""
from __future__ import annotations

import difflib
import json
import os
import typing
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..data import COLUMN_INFO
from ..errors import ServerError
from ..jsonout import jsonify, scalar
from ..operations import Operation, arg, model_hook, register_operation
from ..screens import REGISTRY, GrahamTests
from ..spec import (HELPERS, PRESETS, SCREEN_NAME_RE, ExpressionError, ScreenSpec, Stage, _apply_graham, _apply_rank,
                    _universe_cut, check_expression, eval_expr, graham_tests, screens_folder, size_cut_skipped)
from .. import columns as _columns
from ._request import parse_date


# ---------------------------------------------------------------------------------------------------
# typed request pieces (the ScreenSpec JSON, so REST/MCP get a real schema)
# ---------------------------------------------------------------------------------------------------
class GrahamSpec:
    """Marker: {"preset": "defensive" | "enterprising", <GrahamTests field>: value, ...} (pydantic model by hook)."""


@model_hook(GrahamSpec)
def _graham_spec_model():
    from pydantic import ConfigDict, Field, create_model
    import dataclasses
    hints = typing.get_type_hints(GrahamTests)
    flds = {f.name: (hints.get(f.name, typing.Any) | None, Field(None)) for f in dataclasses.fields(GrahamTests)}
    return create_model("GrahamSpec", __config__=ConfigDict(extra="forbid"),
                        __doc__="Graham tests of a stage: a preset plus GrahamTests fields to change.",
                        preset=(typing.Literal["defensive", "enterprising"], Field("defensive")), **flds)


@dataclass
class UniverseSpec:
    """Universe of a stage: size cut first, then the sector lists."""
    top: int | None = arg(None, "keep the `top` largest names by `by`")
    top_optional: bool | None = arg(None, "when true and the `by` column (market cap) is not in the data, skip the size cut instead of failing")
    by: str | None = arg(None, "numeric column the size cut ranks by (default market_cap)")
    include_sectors: list[str] | None = arg(None, "keep only these sectors")
    exclude_sectors: list[str] | None = arg(None, "drop these sectors")
    sectors: list[str] | None = arg(None, "older name of include_sectors")


@dataclass
class StageSpec:
    """One stage of a multi-stage screen."""
    universe: UniverseSpec | None = arg(None, "universe cut")
    derive: dict[str, str] | None = arg(None, "new column name -> expression")
    filters: list[str] | None = arg(None, "include criteria: a name stays when every expression is true")
    exclude: list[str] | None = arg(None, "exclude criteria: a name is dropped when any expression is true (missing is kept)")
    graham: GrahamSpec | None = arg(None, "Graham tests: {preset, <GrahamTests overrides>}")
    rank: dict[str, str] | None = arg(None, "column -> asc | desc; ranks are summed")
    keep: int | None = arg(None, "cut to the best `keep` names after ranking")


@dataclass
class ScreenSpecRequest:
    """A screen spec (the ScreenSpec JSON): single-stage keys, or `stages`."""
    name: str | None = arg(None, "screen name (required to save; a registered name alone previews that screen)")
    n: int | None = arg(None, "number of picks (default 10)")
    note: str | None = arg(None, "one-line description")
    universe: UniverseSpec | None = arg(None, "universe cut")
    derive: dict[str, str] | None = arg(None, "new column name -> expression")
    filters: list[str] | None = arg(None, "include criteria: a name stays when every expression is true")
    exclude: list[str] | None = arg(None, "exclude criteria: a name is dropped when any expression is true (missing is kept)")
    graham: GrahamSpec | None = arg(None, "Graham tests: {preset, <GrahamTests overrides>}")
    rank: dict[str, str] | None = arg(None, "column -> asc | desc; ranks are summed")
    stages: list[StageSpec] | None = arg(None, "stages run in order on the survivors; replaces the single-stage keys")


# ---------------------------------------------------------------------------------------------------
# spec helpers
# ---------------------------------------------------------------------------------------------------
UNIVERSE_KEYS = ("top", "by", "include_sectors", "exclude_sectors", "sectors", "top_optional")
_SPEC_FIELDS = ("name", "n", "note", "universe", "derive", "filters", "exclude", "graham", "rank", "stages")


def _clean_spec(spec) -> dict:
    """The spec as a plain dict without the None entries; a 400 when it is not an object or has unknown keys."""
    if not isinstance(spec, dict):
        raise ServerError(400, "`spec` must be a JSON object (a screen spec)")
    bad = [k for k in spec if k not in _SPEC_FIELDS]
    if bad:
        raise ServerError(400, f"unknown spec field {bad[0]!r}; use {list(_SPEC_FIELDS)}")
    out = {k: v for k, v in spec.items() if v is not None}
    for k, kind in (("universe", dict), ("derive", dict), ("rank", dict), ("graham", (dict, str)),
                    ("filters", list), ("exclude", list), ("stages", list)):
        if k in out and not isinstance(out[k], kind):
            raise ServerError(400, f"spec.{k} must be {'an object' if kind is dict else 'a list' if kind is list else 'an object or a preset name'}")
    return out


def screen_to_spec(sc) -> dict:
    """An editable spec dict from a registered spec screen (single- or multi-stage), or ValueError for a function screen."""
    from ..spec import rule_screen
    if sc.fn is not rule_screen:
        raise ValueError(f"{sc.name!r} is a Python function screen, not a spec screen: it cannot be opened in the "
                         f"screen builder (use picks or backtest to run it)")
    src = getattr(sc, "source_spec", None)                    # LA: a built-in as written (named expressions by name)
    if src is not None:
        p = {k: v for k, v in src.items() if k not in ("name", "n", "note") and v not in (None, {}, [], ())}
        return json.loads(json.dumps({"name": sc.name, "n": src.get("n", sc.n), "note": src.get("note", sc.note), **p}, default=list))
    p = {k: v for k, v in sc.params.items() if v not in (None, {}, [], ())}
    spec = {"name": sc.name, "n": sc.n, "note": "" if sc.note == "spec screen" else sc.note, **p}
    if "stages" in spec:
        spec["stages"] = [Stage.from_dict(s).to_dict() for s in spec["stages"]]
    return json.loads(json.dumps(spec, default=list))


def _spec_for_preview(lab, raw: dict) -> dict:
    """A name-only spec that names a registered spec screen means that screen."""
    body = {k: v for k, v in raw.items() if k not in ("name", "n", "note")}
    name = raw.get("name")
    if not body and name and lab is not None and name in lab.screens:
        spec = screen_to_spec(lab.screens[name])                 # ValueError for a function screen
        if raw.get("n") is not None:
            spec["n"] = raw["n"]
        return spec
    return raw


def _stages_of(sp: ScreenSpec) -> list:
    return list(sp.stages) if sp.stages else [Stage(sp.universe, sp.derive, sp.filters, sp.graham, sp.rank,
                                                     exclude=sp.exclude)]


# ---------------------------------------------------------------------------------------------------
# the preview engine
# ---------------------------------------------------------------------------------------------------
def _pct(x: float) -> str:
    p = x * 100
    return "<1%" if 0 < p < 1 else f"{p:.0f}%"


def _missing_note(frame: pd.DataFrame, rows: pd.Index, cols) -> str | None:
    """"<col> is missing for N% of names" for the column with the largest share missing among `rows`."""
    best = None
    if not len(rows):
        return None
    for c in cols:
        if c in frame:
            share = float(frame.loc[rows, c].isna().mean())
            if share > 0 and (best is None or share > best[1]):
                best = (c, share)
    return f"{best[0]} is missing for {_pct(best[1])} of names" if best else None


def _is_boolean(res) -> bool:
    if isinstance(res, (bool, np.bool_)):
        return True
    if isinstance(res, pd.Series):
        if pd.api.types.is_bool_dtype(res):
            return True
        if res.dtype == object:
            return bool(res.dropna().map(lambda v: isinstance(v, (bool, np.bool_))).all())
    return False


def _suggest(word, choices):
    hit = difflib.get_close_matches(str(word), [str(c) for c in choices], n=1, cutoff=0.6)
    return hit[0] if hit else None


def preview_screen(snap: pd.DataFrame, spec: dict, show: int = 15, named: dict | None = None) -> dict:
    """Run `spec` on the snapshot `snap` step by step. Returns {"ok", "errors", "funnel", "picks", "n_picks",
    "stages"}; see docs/SCREEN_BUILDER.md. Never raises on a bad spec: the problem is an entry of `errors`.
    LA: `named` ({column: group}) marks the derive rows that come from a named expression (key "named" in the funnel row);
    `spec` must already have its named expressions expanded (`columns.expand_spec`)."""
    errors: list = []
    funnel: list = []

    def err(where, message, suggestion=None):
        errors.append({"where": where, "message": message, "suggestion": suggestion})

    try:
        sp = ScreenSpec.from_dict({"name": spec.get("name") or "preview", **{k: v for k, v in spec.items() if k != "name"}})
    except (TypeError, ValueError) as e:
        err("spec", str(e))
        return {"ok": False, "errors": errors, "funnel": funnel, "picks": [], "n_picks": 0, "stages": 0}
    stages = _stages_of(sp)
    multi = bool(sp.stages)
    n = sp.n
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 1:
        err("n", f"n must be a whole number of at least 1, got {n!r}")
        n = 10
    used: list = []                                    # columns the criteria and the ranking use, in order

    def use(*cols):
        for c in cols:
            if c not in used:
                used.append(c)

    def step(k, text, kind, expr, rows_in, rows_out, alone=None, note=None):
        funnel.append({"step": f"stage {k + 1}: {text}" if multi else text, "kind": kind, "expr": expr,
                       "rows_in": int(rows_in), "rows_out": int(rows_out),
                       "alone": None if alone is None else int(alone), "note": note})
        if kind == "derive" and named and text.startswith("derive ") and text[7:] in named:
            funnel[-1]["named"] = named[text[7:]]                  # LA: "Starter columns" | "My columns"

    u = snap
    for k, st in enumerate(stages):
        pre = f"stages[{k}]." if multi else ""
        names = set(u.columns)
        # ---- universe
        uni = {k2: v for k2, v in (st.universe or {}).items() if v is not None}
        n_in = len(u)
        unknown = sorted(set(uni) - set(UNIVERSE_KEYS))
        if unknown:
            err(pre + "universe", f"unknown universe key {unknown[0]!r}; use {list(UNIVERSE_KEYS)}",
                _suggest(unknown[0], UNIVERSE_KEYS))
            uni = {k2: v for k2, v in uni.items() if k2 in UNIVERSE_KEYS}
        top, by = uni.get("top"), uni.get("by") or "market_cap"
        ok_uni = True
        skipped = bool(top) and not isinstance(top, bool) and size_cut_skipped(u, uni)       # LA: top_optional and no `by` data
        if "top" in uni and uni["top"] is not None and (isinstance(top, bool) or not isinstance(top, (int, np.integer)) or top < 1):
            err(pre + "universe", f"universe.top must be a whole number of at least 1, got {top!r}")
            ok_uni = False
        elif top and by not in names and not (uni.get("top_optional")):
            err(pre + "universe", f"universe.by {by!r} is not a column", _suggest(by, names))
            ok_uni = False
        elif top and by in names and not pd.api.types.is_numeric_dtype(u[by]):
            err(pre + "universe", f"universe.by {by!r} is not a number column")
            ok_uni = False
        sec_lists = {k2: list(uni.get(k2) or ()) for k2 in ("sectors", "include_sectors", "exclude_sectors")}
        if any(sec_lists.values()) and "sector" not in names:
            err(pre + "universe", "the data has no sector column, so sector lists cannot be used")
            ok_uni = False
        notes = ["no size cut: market cap not in the data" if by == "market_cap" else f"no size cut: {by} not in the data"] \
            if skipped else []
        if ok_uni:
            try:
                if "sector" in names:
                    have = set(u["sector"].dropna().unique())
                    for lst in sec_lists.values():
                        for s in lst:
                            if s not in have:
                                hint = _suggest(s, have)
                                notes.append(f"sector {s!r} is not in the data" + (f" (did you mean {hint!r}?)" if hint else ""))
                u = _universe_cut(u, uni)
            except Exception as e:                      # noqa: BLE001
                err(pre + "universe", str(e))
        parts = ([f"top {top} by {by}" if not skipped else f"top {top} by {by} (skipped)"] if top else []) + \
                ([f"sectors {'+'.join([*sec_lists['sectors'], *sec_lists['include_sectors']])}"]
                 if sec_lists["sectors"] or sec_lists["include_sectors"] else []) + \
                ([f"without {'+'.join(sec_lists['exclude_sectors'])}"] if sec_lists["exclude_sectors"] else [])
        if k == 0 or parts:
            step(k, "universe: " + (", ".join(parts) if parts else "all names"), "universe", None, n_in, len(u),
                 note="; ".join(notes) or None)
        if top and not skipped:
            use(by)
        # ---- derive
        base = u.copy() if st.derive else u
        for name, expr in (st.derive or {}).items():
            where = f"{pre}derive.{name}"
            if not isinstance(name, str) or not name.isidentifier() or name.startswith("__"):
                err(where, f"{name!r} is not a usable column name (letters, digits and _, not starting with a digit)")
                continue
            res_note = None
            try:
                cols = check_expression(expr, names | set(base.columns))
                use(*cols)
                base[name] = eval_expr(base, expr)
                res_note = _missing_note(base, base.index, [name])
            except ExpressionError as e:
                err(where, e.message, e.suggestion)
                base[name] = np.nan
                res_note = "error: " + e.message
            except Exception as e:                      # noqa: BLE001  (pandas errors on a well-formed expression)
                err(where, str(e) or type(e).__name__)
                base[name] = np.nan
                res_note = "error: " + (str(e) or type(e).__name__)
            names.add(name)
            use(name)
            step(k, f"derive {name}", "derive", expr, len(base), len(base), note=res_note)
        # ---- include / exclude criteria, all on the same rows
        alive = pd.Series(True, index=base.index)
        for kind, exprs, label in (("include", st.filters or [], "filters"), ("exclude", st.exclude or [], "exclude")):
            for i, expr in enumerate(exprs):
                where = f"{pre}{label}[{i}]"
                rows_in = int(alive.sum())
                note, mask, cols = None, None, []
                try:
                    cols = check_expression(expr, names | set(base.columns))
                    use(*cols)
                    res = eval_expr(base, expr)
                    if not _is_boolean(res):
                        err(where, f"{expr!r} gives " + ("numbers" if isinstance(res, (pd.Series, int, float, np.number))
                                                         and not pd.api.types.is_object_dtype(getattr(res, "dtype", float)) else "values")
                            + ", not true/false: use a comparison such as `"
                            + (expr if any(c in expr for c in "<>=") else f"{expr} > 0") + "`")
                        note = "error: not a true/false condition"
                    else:
                        mask = (res if isinstance(res, pd.Series) else pd.Series(bool(res), index=base.index)) \
                            .fillna(False).astype(bool)
                except ExpressionError as e:
                    err(where, e.message, e.suggestion)
                    note = "error: " + e.message
                except Exception as e:                  # noqa: BLE001
                    err(where, str(e) or type(e).__name__)
                    note = "error: " + (str(e) or type(e).__name__)
                alone = None
                if mask is not None:
                    before = alive.copy()
                    if kind == "include":
                        alone = int((mask & before).sum())
                        alive &= mask
                    else:
                        alive &= ~mask
                    rows_out = int(alive.sum())
                    bits = []
                    if rows_in:
                        if rows_out == rows_in:
                            bits.append("keeps every name")
                        elif rows_out == 0:
                            bits.append("drops every name")
                    m = _missing_note(base, base.index[before.to_numpy()], cols)
                    if m:
                        bits.append(m)
                    note = "; ".join(bits) or None
                else:
                    rows_out = rows_in
                step(k, f"{kind}: {expr}", kind, expr, rows_in, rows_out, alone, note)
        u = base[alive]
        # ---- graham
        if st.graham:
            rows_in = len(u)
            g = st.graham
            preset = g if isinstance(g, str) else dict(g).get("preset", "defensive")
            if preset not in PRESETS:
                err(pre + "graham", f"unknown Graham preset {preset!r}; use {sorted(PRESETS)}", _suggest(preset, PRESETS))
                step(k, "graham tests", "include", None, rows_in, rows_in, None, "error: unknown preset")
            else:
                try:
                    tests = graham_tests(g)
                    u = _apply_graham(u, tests)
                    note = ("no names left to test" if not rows_in else "keeps every name" if len(u) == rows_in else
                            "drops every name" if not len(u) else None)
                    step(k, f"graham tests ({preset}, " + (f"at least {tests.min_passes} passes)" if tests.min_passes
                                                          else "all tests but one)"), "include", None, rows_in, len(u), None, note)
                except KeyError as e:
                    err(pre + "graham", f"the Graham tests need the column {e.args[0]!r}, which this data does not have")
                    step(k, "graham tests", "include", None, rows_in, rows_in, None, "error: missing column")
                except (TypeError, ValueError) as e:
                    bad = str(e).split("'")[1] if "'" in str(e) else None
                    err(pre + "graham", str(e), _suggest(bad, [*GrahamTests.__dataclass_fields__, "preset"]) if bad else None)
                    step(k, "graham tests", "include", None, rows_in, rows_in, None, "error: " + str(e))
        # ---- rank
        if st.rank:
            rows_in = len(u)
            ok_rank = True
            for c, d in st.rank.items():
                if c not in u.columns:
                    err(f"{pre}rank.{c}", f"unknown column {c!r}", _suggest(c, u.columns))
                    ok_rank = False
                elif str(d).lower() not in ("asc", "desc"):
                    err(f"{pre}rank.{c}", f"direction must be 'asc' or 'desc', got {d!r}")
                    ok_rank = False
                elif not pd.api.types.is_numeric_dtype(u[c]) and not pd.api.types.is_bool_dtype(u[c]):
                    err(f"{pre}rank.{c}", f"{c!r} is not a number column, so it cannot be ranked")
                    ok_rank = False
            use(*[c for c in st.rank if c in u.columns])
            text = "rank by " + ", ".join(f"{c} {str(d).lower()}" for c, d in st.rank.items())
            if ok_rank:
                note = _missing_note(u, u.index, list(st.rank))
                u = _apply_rank(u, st.rank)
                if note:
                    note += " (they drop out)"
                step(k, text, "rank", None, rows_in, len(u), None, note)
            else:
                step(k, text, "rank", None, rows_in, rows_in, None, "error: see errors")
        # ---- keep
        if st.keep:
            rows_in = len(u)
            u = u.head(int(st.keep))
            step(k, f"keep the best {st.keep}", "take", None, rows_in, len(u))
    # ---- take n
    rows_in = len(u)
    u = u.head(n)
    no_rank = not any(s.rank for s in stages)
    step(len(stages) - 1, f"take {n}", "take", None, rows_in, len(u),
         note="no ranking: takes the first names in data order" if no_rank and rows_in > n else None)
    if multi:                                           # the final take belongs to the screen, not to the last stage
        funnel[-1]["step"] = f"take {n}"
    ok = not errors
    cols = [c for c in used if c in u.columns]
    picks = []
    if ok:
        for i, (tkr, row) in enumerate(u.iterrows(), start=1):
            if i > max(int(show), 0):
                break
            picks.append({"rank": i, "ticker": str(tkr),
                          "sector": scalar(row["sector"]) if "sector" in u.columns else None,
                          "values": {c: scalar(row[c]) for c in cols}})
    return {"ok": ok, "errors": errors, "funnel": funnel, "picks": picks, "n_picks": len(u) if ok else 0,
            "stages": len(stages)}


# ---------------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------------
def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    return "text"


def schema_of(snap: pd.DataFrame) -> list:
    out = []
    for c in snap.columns:
        s, info = snap[c], COLUMN_INFO.get(c, {})
        kind = _kind(s)
        row = {"name": c, "group": info.get("group", "other"), "kind": kind, "dtype": str(s.dtype),
               "coverage": float(s.notna().mean()) if len(s) else 0.0, "min": None, "p50": None, "max": None,
               "example": None, "description": info.get("description", "(no description: a column of this dataset)")}
        nn = s.dropna()
        if len(nn):
            row["example"] = scalar(nn.iloc[0])
            if kind == "number":
                row["min"], row["p50"], row["max"] = scalar(nn.min()), scalar(nn.median()), scalar(nn.max())
            elif kind == "date":
                row["min"], row["max"] = scalar(nn.min()), scalar(nn.max())
                row["p50"] = scalar(nn.sort_values().iloc[len(nn) // 2])
            else:
                vc = nn.astype(str).value_counts().head(5)
                row["top"] = [{"value": str(v), "count": int(k)} for v, k in vc.items()]
        out.append(row)
    return out


def named_schema(snap: pd.DataFrame, library=None) -> list:
    """LA: one row per named expression (starter columns first, then the user's), shaped like `schema_of` rows plus `expr`
    and `missing` (the snapshot columns its formula needs that this data lacks; the row then has coverage 0 and `error`)."""
    from ..spec import expr_names
    entries = (library or _columns.ColumnLibrary()).entries()
    ok, bad = _columns.evaluate_all(snap, entries)
    frame = pd.DataFrame({n: s for n, s in ok.items()}, index=snap.index)
    rows = {r["name"]: r for r in schema_of(frame)} if len(frame.columns) else {}
    out = []
    for n, e in entries.items():
        r = rows.get(n) or {"name": n, "kind": "number", "dtype": "float64", "coverage": 0.0, "min": None, "p50": None,
                            "max": None, "example": None}
        have = set(snap.columns) | set(entries)
        out.append({**r, "name": n, "group": e["group"], "description": e["note"] or "(no note)", "expr": e["expr"],
                    "missing": sorted({c for c in _columns.closure_columns(n, entries) if c not in have}),
                    **({"error": bad[n]} if n in bad else {})})
    return out


def last_full_asof(lab) -> pd.Timestamp:
    """The latest trading day on which at least half as many names have a price as on the busiest of the last 20 days
    (a data file that ends with a few stragglers does not give a thin last snapshot)."""
    px = lab.market.prices
    tail = px.iloc[-20:].notna().sum(axis=1)
    good = tail[tail >= 0.5 * tail.max()]
    return pd.Timestamp(good.index[-1] if len(good) else px.index[-1])


def _resolve_asof(lab, asof) -> pd.Timestamp:
    return lab._asof(asof) if asof else last_full_asof(lab)


# ---------------------------------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------------------------------
class _Untracked(Operation):
    """Shared: not an analysis of the lab's data series, so no trial counting, no recorded run, no metrics."""
    tracked_entry = False
    takes_max_points = False

    def tracking_args(self, lab, *a, **kw):
        return None

    def result_json(self, result, max_points=None, *, lab=None, kw=None):
        return jsonify(result)

    def to_json(self, result, max_points=None, *, lab=None, kw=None):
        return jsonify(result)

    def extract(self, result):
        from ..store import Extracted
        return Extracted()

    def metrics(self, result) -> list:
        return []


@register_operation(builtin=True)
class ScreenSchema(_Untracked):
    """Columns a screen can use on a date (type, coverage, range, description) and the expression helpers."""
    name = "screen_schema"
    rest_method, rest_path = "GET", "screen_schema"
    registry_order = ("asof", "lab")

    @dataclass
    class Args:
        asof: str | None = arg(None, "date (default: the last date with a full snapshot)")

    def run(self, lab, asof=None) -> dict:
        """{asof, n_names, dataset, columns: [{name, group, kind, dtype, coverage, min, p50, max, example,
        description}], named: [{the same keys, group "Starter columns" | "My columns", plus expr, missing}],
        helpers: [{name, signature, description}]}."""
        d = _resolve_asof(lab, asof)
        snap = lab.market.view(d)
        ds = lab.market.dataset
        return {"asof": scalar(d), "n_names": int(len(snap)),
                "dataset": getattr(ds, "name", None) or lab.name or "", "columns": schema_of(snap),
                "named": named_schema(snap),
                "helpers": [{"name": n, "signature": sig, "description": desc} for n, (_, _, sig, desc) in HELPERS.items()]}

    def build(self, lab, a) -> dict:
        return {"asof": parse_date("asof", a.asof)} if a.asof else {}


@register_operation(builtin=True)
class ScreenPreview(_Untracked):
    """Run a screen spec on one date's snapshot and show the funnel (rows in/out per step) and the picks. Never trades."""
    name = "screen_preview"
    registry_order = ("spec", "asof", "show", "lab")

    @dataclass
    class Args:
        spec: ScreenSpecRequest = arg(doc="the screen spec: {name?, n, universe, derive, filters, exclude, graham, rank} or {stages: [...]}")
        asof: str | None = arg(None, "date (default: the last date with a full snapshot)")
        show: int = arg(15, "most picks listed")

    def run(self, lab, spec, asof=None, show: int = 15) -> dict:
        """{ok, asof, n_names, errors: [{where, message, suggestion}], funnel: [{step, kind, expr, rows_in,
        rows_out, alone, note[, named]}], picks: [{rank, ticker, sector, values}], n_picks, stages,
        uses: [{name, group, expr, note}]}. `uses`: the named expressions (starter columns, my columns) the spec used,
        in dependency order; they appear as derive rows of the funnel (with `named`) and the formulas stand in the
        spec the way a save or a run would hold it."""
        raw = _spec_for_preview(lab, _clean_spec(spec))
        d = _resolve_asof(lab, asof)
        snap = lab.market.view(d)
        try:
            raw, uses = _columns.expand_spec(raw)                  # LA: named expressions are inlined into derive
        except ValueError as e:
            return {"ok": False, "asof": scalar(d), "n_names": int(len(snap)), "stages": 0, "uses": [], "n_picks": 0,
                    "errors": [{"where": "spec", "message": str(e), "suggestion": None}], "funnel": [], "picks": []}
        out = preview_screen(snap, raw, show, named={u["name"]: u["group"] for u in uses})
        return {"ok": out["ok"], "asof": scalar(d), "n_names": int(len(snap)), **{k: v for k, v in out.items() if k != "ok"},
                "uses": uses}

    def build(self, lab, a) -> dict:
        _clean_spec(a.spec)
        if isinstance(a.show, bool) or not isinstance(a.show, int) or a.show < 0:
            raise ServerError(400, "`show` must be a whole number, 0 or more")
        kw = {"spec": a.spec, "show": a.show}
        if a.asof:
            kw["asof"] = parse_date("asof", a.asof)
        return kw


def save_spec(lab, spec: dict, folder=None, overwrite: bool = False) -> dict:
    """Validate `spec`, write `<folder>/<name>.json` and register it in `lab`; see ScreenSave."""
    raw = _clean_spec(spec)
    name = raw.get("name")
    if not isinstance(name, str) or not SCREEN_NAME_RE.fullmatch(name):
        raise ValueError(f"a saved screen needs a name of letters, digits, _ and - (starting with a letter, at most 64): got {name!r}")
    if name in REGISTRY or (name in lab.screens and not _is_spec_screen(lab.screens[name])):
        raise ServerError(409, f"{name!r} is a built-in screen and cannot be replaced; save under another name")
    path = screens_folder(folder) / f"{name}.json"
    if path.exists() and not overwrite:
        raise ServerError(409, f"{path} already exists; pass overwrite=true to replace it")
    try:
        raw, _uses = _columns.expand_spec(raw)                   # LA: the saved file holds the formulas, not the names
    except ValueError as e:
        raise ValueError(f"the spec is not valid: {e}") from None
    out = preview_screen(_probe_snapshot(lab), raw, show=0)
    if not out["ok"]:
        e = out["errors"][0]
        raise ValueError(f"the spec is not valid: {e['where']}: {e['message']}" + (f" (did you mean {e['suggestion']!r}?)" if e["suggestion"] else ""))
    sp = ScreenSpec.from_dict({**raw, "name": name})
    body = {"name": name, "n": sp.n, "note": sp.note, **{k: v for k, v in sp.to_dict().items()
                                                         if k not in ("name", "n", "note") and v not in (None, {}, [])}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n")
    os.replace(tmp, path)
    lab.define(body)
    return {"name": name, "path": str(path), "registered": True}


def _is_spec_screen(sc) -> bool:
    from ..spec import rule_screen
    return sc.fn is rule_screen


def _probe_snapshot(lab) -> pd.DataFrame:
    return lab.market.view(last_full_asof(lab))


@register_operation(builtin=True)
class ScreenSave(_Untracked):
    """Validate a screen spec, save it as <folder>/<name>.json and register it in the lab so it can be added and backtested."""
    name = "screen_save"
    registry_order = ("spec", "folder", "overwrite", "lab")

    @dataclass
    class Args:
        spec: ScreenSpecRequest = arg(doc="the screen spec, with a `name`")
        folder: str | None = arg(None, "folder to write into (default: env SCREENLAB_SCREENS, else ~/.screenlab/screens)")
        overwrite: bool = arg(False, "replace an existing saved screen of that name")

    def run(self, lab, spec, folder=None, overwrite: bool = False) -> dict:
        """{name, path, registered}. 409 for a built-in name or an existing file without overwrite; 400 for an invalid spec."""
        return save_spec(lab, spec, folder, overwrite)

    def build(self, lab, a) -> dict:
        raw = _clean_spec(a.spec)
        if not isinstance(raw.get("name"), str) or not SCREEN_NAME_RE.fullmatch(raw["name"]):
            raise ServerError(400, f"a saved screen needs a name of letters, digits, _ and - (starting with a letter): got {raw.get('name')!r}")
        kw = {"spec": a.spec}
        if a.folder:
            kw["folder"] = a.folder
        if a.overwrite:
            kw["overwrite"] = True
        return kw


@register_operation(builtin=True)
class ScreenLoad(_Untracked):
    """A registered spec screen as an editable spec (400 for a Python function screen)."""
    name = "screen_load"
    rest_method, rest_path = "GET", "screen_load/{name}"
    registry_order = ("name", "lab")

    @dataclass
    class Args:
        name: str = arg(doc="screen name")

    def run(self, lab, name) -> dict:
        """{name, spec, note}."""
        if name not in lab.screens:
            raise KeyError(f"unknown screen: {name}")
        sc = lab.screens[name]
        spec = screen_to_spec(sc)
        note = ("built-in spec screen: change it and save it under another name (a built-in cannot be replaced)"
                if name in REGISTRY else "a screen defined in this lab: saving under the same name replaces it")
        return {"name": name, "spec": spec, "note": note}

    def build(self, lab, a) -> dict:
        return {"name": a.name}


# ---------------------------------------------------------------------------------------------------
# LA: named expressions ("my columns"), docs/SCREEN_LIBRARY.md section 4
# ---------------------------------------------------------------------------------------------------
@register_operation(builtin=True)
class ColumnDefine(_Untracked):
    """Define a named expression ("my column") for use in any screen; stored in <screens folder>/columns.json."""
    name = "column_define"
    registry_order = ("name", "expr", "note", "overwrite", "lab")

    @dataclass
    class Args:
        name: str = arg(doc="column name: letters, digits and _, not a snapshot column, helper or starter column")
        expr: str = arg(doc="the expression over snapshot columns, helpers and other named expressions, e.g. 'coalesce(div_yield, 0) + coalesce(buyback_yield, 0)'")
        note: str = arg("", "one-line description")
        overwrite: bool = arg(False, "replace an existing column of yours with that name")

    def run(self, lab, name, expr, note: str = "", overwrite: bool = False) -> dict:
        """{name, expr, note, group: "My columns", path, replaced, uses: [named expressions it uses], warnings, coverage, asof}.
        400: bad name, expression error, cycle (the message names the path), or an expression that raises on the last full
        snapshot; 409: the name is a snapshot column, helper or starter column, or taken (without overwrite)."""
        d = last_full_asof(lab)
        return _columns.define_column(lab.market.view(d), d, name, expr, note, overwrite)

    def build(self, lab, a) -> dict:
        kw = {"name": a.name, "expr": a.expr}
        if a.note:
            kw["note"] = a.note
        if a.overwrite:
            kw["overwrite"] = True
        return kw


@register_operation(builtin=True)
class ColumnDelete(_Untracked):
    """Delete one of your named expressions (starter columns cannot be deleted)."""
    name = "column_delete"
    registry_order = ("name", "lab")

    @dataclass
    class Args:
        name: str = arg(doc="the column to delete")

    def run(self, lab, name) -> dict:
        """{name, deleted: true, expr, path}; 404 when you have no such column, 409 for a starter column."""
        return _columns.delete_column(name)

    def build(self, lab, a) -> dict:
        return {"name": a.name}
