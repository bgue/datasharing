"""Screen tab: define a screen (universe, derived columns, include / exclude criteria, ranking) and see what it does to the
names before running anything (operations `screen_schema`, `screen_preview`, `screen_save`, `screen_load`; contract in
docs/SCREEN_BUILDER.md).

# SB2: The session side (`new_form`, `update_form`, `form_to_spec`, `spec_to_form`, `view_text`) imports no Textual, so
`session.py` can use it; the Textual pane is built by `pane(app)`, which imports Textual when called.

Wiring, all in one place: ui.py mounts `pane(app)` first in the tab bar and binds key `0`, F5 (preview) and Ctrl+S (save);
`pane()` registers `app._draw_screen`. The form lives in `session.analyses["screen_form"]` and the last preview in
`session.analyses["screen_preview"]`; every edit goes through the session commands `set_screen_form`, `screen_preview`,
`screen_save`, `screen_load`, `screen_add` (see PROTOCOL.md), so `--serve` / `--remote` tabs and MCP `tui_command` share it.
"""
from __future__ import annotations

import copy
import json
import re

# SB3: stages. `form["stages"]` is the list of stages (each: top, by, include_sectors, exclude_sectors, derive, include, exclude, rank,
# keep, graham, universe_extra); `form["current"]` is the stage the Screen tab shows. The one-stage keys (top, by, include_sectors,
# exclude_sectors, derive, include, exclude, rank, extra) mirror stage 0 (`sync_form`), so the old one-stage commands keep working.
TAB = "screen"
TABLES = ("derive", "include", "exclude", "rank")
STAGE_FIELDS = ("top", "by", "include_sectors", "exclude_sectors", "keep")      # SB3: per-stage scalars
MAX_STAGES = 8
_MIRROR = ("top", "by", "include_sectors", "exclude_sectors", *TABLES)
FORM_DEFAULT = {"name": "my_screen", "note": "", "dataset": None, "asof": None, "top": 100, "by": "market_cap",
                "include_sectors": [], "exclude_sectors": [], "derive": [], "include": [], "exclude": [], "rank": [],
                "n": 10, "extra": {}, "saved": None, "saved_name": None, "loaded_name": None,
                "stages": [], "current": 0, "settings": {}}                      # SB3: stages, shown stage, backtest settings of a built-in
SCALARS = ("name", "note", "asof", "top", "by", "include_sectors", "exclude_sectors", "n", "keep")
DIRS = ("asc", "desc")
HELP = ("include: keep the rows where the expression is true.  exclude: drop the rows where it is true.  "
        "rank: the sum of the ranks of the columns, best first.  A missing value (NaN) fails an include criterion "
        "but is not excluded by an exclude criterion.")
AMBER = ("keeps every name", "drops every name")


# ------------------------------------------------------------------ session side (no Textual)
def new_stage(top=None) -> dict:
    """SB3: an empty stage."""
    return {"top": top, "by": "market_cap", "include_sectors": [], "exclude_sectors": [], "derive": [], "include": [],
            "exclude": [], "rank": [], "keep": None, "graham": None, "universe_extra": {}}


def sync_form(form: dict) -> dict:
    """SB3: make the one-stage keys mirror stage 0 and `current` a valid index."""
    sts = form["stages"]
    for k in _MIRROR:
        form[k] = copy.deepcopy(sts[0][k])
    form["extra"] = {"graham": copy.deepcopy(sts[0]["graham"])} if sts[0].get("graham") else {}
    form["current"] = min(max(int(form.get("current") or 0), 0), len(sts) - 1)
    return form


def _upgrade(form: dict) -> dict:
    """SB3: a form without `stages` (older layout) becomes a one-stage form."""
    if not form.get("stages"):
        st = new_stage()
        for k in _MIRROR:
            if k in form:
                st[k] = copy.deepcopy(form[k])
        if (form.get("extra") or {}).get("graham"):
            st["graham"] = copy.deepcopy(form["extra"]["graham"])
        form["stages"] = [st]
    form.setdefault("current", 0)
    form.setdefault("settings", {})
    return sync_form(form)


def new_form(dataset=None) -> dict:
    f = copy.deepcopy(FORM_DEFAULT)
    f["dataset"] = dataset
    f["stages"] = [new_stage(100)]
    return sync_form(f)


def _err(msg):
    from .session import SessionError
    return SessionError(400, msg)


def _int(name, v, minimum=1, optional=False):
    if v in (None, "") and optional:
        return None
    try:
        out = int(float(v))
    except (TypeError, ValueError):
        raise _err(f"screen {name} must be a whole number, got {v!r}") from None
    if out < minimum:
        raise _err(f"screen {name} must be at least {minimum}, got {out}")
    return out


def _sectors(v) -> list:
    if v in (None, ""):
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [str(x).strip() for x in v if str(x).strip()]


def _row(table: str, row):
    """A table row as the form stores it: derive `{name, expr}`, include / exclude a string, rank `{col, dir}`."""
    if table == "derive":
        r = dict(row) if isinstance(row, dict) else {}
        name, expr = str(r.get("name") or "").strip(), str(r.get("expr") or "").strip()
        if not name.isidentifier():
            raise _err(f"derived column name must be an identifier, got {name!r}")
        if not expr:
            raise _err("derived column needs an expression")
        return {"name": name, "expr": expr}
    if table in ("include", "exclude"):
        text = str(row.get("expr") if isinstance(row, dict) else row or "").strip()
        if not text:
            raise _err(f"{table} criterion is empty")
        return text
    if table == "rank":
        r = dict(row) if isinstance(row, dict) else {}
        col, d = str(r.get("col") or "").strip(), str(r.get("dir") or "asc").strip().lower()
        if not col:
            raise _err("rank needs a column")
        if d not in DIRS:
            raise _err(f"rank direction must be one of {list(DIRS)}, got {d!r}")
        return {"col": col, "dir": d}
    raise _err(f"unknown table {table!r}; one of {list(TABLES)}")


def _rows(table: str, rows) -> list:
    if table == "derive" and isinstance(rows, dict):
        rows = [{"name": k, "expr": v} for k, v in rows.items()]
    if table == "rank" and isinstance(rows, dict):
        rows = [{"col": k, "dir": v} for k, v in rows.items()]
    if not isinstance(rows, (list, tuple)):
        raise _err(f"{table} must be a list of rows")
    out = [_row(table, r) for r in rows]
    if table == "rank" and len({r["col"] for r in out}) != len(out):
        raise _err("a column can be ranked only once")
    return out


def _table_target(table):
    if table not in TABLES:
        raise _err(f"unknown table {table!r}; one of {list(TABLES)}")
    return table



def _stage_index(sts: list, v, what="stage") -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < len(sts):
        raise _err(f"{what} must be 0..{len(sts) - 1} (stages are numbered from 0), got {v!r}")
    return v


def default_keep(n) -> int:
    """SB3: the `keep` a stage gets when another stage is added after it: 50, or twice n when that is more."""
    return max(50, 2 * int(n or 10))


def update_form(form: dict, fields: dict | None = None, add_row=None, remove_row=None, set_row=None,
                reset: bool = False, dataset=None, stage=None, add_stage=None, remove_stage=None, select_stage=None) -> dict:
    """Edit the form in place, validated. `fields`: any of name, note, asof, n (whole screen), top, by, include_sectors,
    exclude_sectors, keep (one stage; sectors: a list or a comma separated string) and the four tables (derive, include,
    exclude, rank) as whole lists. `add_row` `{table, row}` appends (rank: a column already ranked is replaced), `remove_row`
    `{table, index}` deletes, `set_row` `{table, index, row}` replaces one row. `reset` starts again from the default form.

    # SB3: stages are numbered from 0. `stage` is the stage the per-stage fields and the row operations act on (default 0;
    a row operation may carry its own `stage`). `add_stage` (true, or `{"index": k}` to insert before k) appends an empty
    stage and shows it; `remove_stage` (an index) deletes one; `select_stage` shows one in the tab. `keep` is the cut after
    ranking and belongs to every stage but the last (the last stage's cut is n). Order of work: add_stage, remove_stage,
    fields and rows, select_stage."""
    if reset:
        form.clear()
        form.update(new_form(dataset))
    if dataset is not None:
        form["dataset"] = dataset
    _upgrade(form)
    fields = dict(fields or {})
    bad = [k for k in fields if k not in SCALARS and k not in TABLES]
    if bad:
        raise _err(f"unknown screen field {bad[0]!r}; use {[*SCALARS, *TABLES]}")
    sts = copy.deepcopy(form["stages"])
    cur = form["current"]
    glob: dict = {}

    n_now = fields["n"] if "n" in fields else form["n"]
    try:
        n_now = int(float(n_now))
    except (TypeError, ValueError):
        n_now = 10

    if add_stage:
        at = len(sts)
        if isinstance(add_stage, dict):
            at = add_stage.get("index", at)
        if isinstance(at, bool) or not isinstance(at, int) or not 1 <= at <= len(sts):
            raise _err(f"add_stage index must be 1..{len(sts)}, got {at!r}")
        if len(sts) >= MAX_STAGES:
            raise _err(f"a screen has at most {MAX_STAGES} stages")
        sts.insert(at, new_stage())
        for j in (at - 1, at):                           # every stage but the last needs a cut
            if j < len(sts) - 1 and sts[j]["keep"] is None:
                sts[j]["keep"] = default_keep(n_now)
        cur = at
    if remove_stage is not None:
        i = _stage_index(sts, remove_stage, "remove_stage")
        if len(sts) < 2:
            raise _err("a screen needs at least one stage")
        del sts[i]
        if i >= len(sts) or len(sts) == 1:                  # the new last stage has no keep: its cut is n
            sts[-1]["keep"] = None
        cur = min(cur - 1 if i < cur else cur, len(sts) - 1)

    fi = _stage_index(sts, stage)
    for k, v in fields.items():
        if k in ("name", "by"):
            v = str(v or "").strip()
            if not v:
                raise _err(f"screen {k} cannot be empty")
            (glob if k == "name" else sts[fi])[k] = v
        elif k == "note":
            glob[k] = str(v or "")
        elif k == "asof":
            glob[k] = None if v in (None, "") else str(v)[:10]
        elif k == "top":
            sts[fi][k] = _int(k, v, 1, optional=True)
        elif k == "n":
            glob[k] = _int(k, v, 1)
        elif k == "keep":
            kv = _int(k, v, 1, optional=True)
            if kv is not None and fi == len(sts) - 1:
                raise _err("the last stage has no keep: its cut is the pick count n (add a stage after it, or set n)")
            sts[fi][k] = kv
        elif k in ("include_sectors", "exclude_sectors"):
            sts[fi][k] = _sectors(v)
        else:
            sts[fi][k] = _rows(k, v)

    def target(op, what):
        t = _table_target((op or {}).get("table"))
        i = _stage_index(sts, op.get("stage", stage))
        return t, i
    if add_row is not None:
        t, i = target(add_row, "add_row")
        r = _row(t, add_row.get("row"))
        rows = list(sts[i][t])
        if t == "rank":
            rows = [x for x in rows if x["col"] != r["col"]]
        sts[i][t] = [*rows, r]
    if remove_row is not None:
        t, i = target(remove_row, "remove_row")
        rows = list(sts[i][t])
        j = remove_row.get("index")
        if not isinstance(j, int) or not 0 <= j < len(rows):
            raise _err(f"remove_row index must be 0..{len(rows) - 1} for {t}, got {j!r}")
        del rows[j]
        sts[i][t] = rows
    if set_row is not None:
        t, i = target(set_row, "set_row")
        rows = list(sts[i][t])
        j = set_row.get("index")
        if not isinstance(j, int) or not 0 <= j < len(rows):
            raise _err(f"set_row index must be 0..{len(rows) - 1} for {t}, got {j!r}")
        rows[j] = _row(t, set_row.get("row"))
        sts[i][t] = _rows(t, rows)
    if select_stage is not None:
        cur = _stage_index(sts, select_stage, "select_stage")
    form.update(glob)
    form["stages"], form["current"] = sts, cur
    return sync_form(form)


# ---- SB3: form <-> spec, stage by stage
def _stage_spec(st: dict) -> dict:
    out = {}
    uni = dict(st.get("universe_extra") or {})
    if st.get("top"):
        uni["top"] = int(st["top"])
        uni["by"] = st.get("by") or "market_cap"
    if st.get("include_sectors"):
        uni["include_sectors"] = list(st["include_sectors"])
    if st.get("exclude_sectors"):
        uni["exclude_sectors"] = list(st["exclude_sectors"])
    if uni:
        out["universe"] = uni
    if st.get("derive"):
        out["derive"] = {r["name"]: r["expr"] for r in st["derive"]}
    if st.get("include"):
        out["filters"] = list(st["include"])
    if st.get("exclude"):
        out["exclude"] = list(st["exclude"])
    if st.get("graham"):
        out["graham"] = copy.deepcopy(st["graham"])
    if st.get("rank"):
        out["rank"] = {r["col"]: r["dir"] for r in st["rank"]}
    if st.get("keep"):
        out["keep"] = int(st["keep"])
    return out


def form_to_spec(form: dict) -> dict:
    """The ScreenSpec dict of the form (only the keys that are set). One stage without a `keep` gives the one-stage keys
    (universe, derive, filters, exclude, graham, rank); otherwise `stages`, one entry per stage. Unknown universe keys and
    Graham tests of a loaded screen are kept as loaded."""
    spec = {"name": form["name"], "n": int(form["n"])}
    if form.get("note"):
        spec["note"] = form["note"]
    sts = _upgrade(form)["stages"] if not form.get("stages") else form["stages"]
    if len(sts) == 1 and not sts[0].get("keep"):
        spec.update(_stage_spec(sts[0]))
    else:
        spec["stages"] = [_stage_spec(st) for st in sts]
    return spec


def _stage_from_spec(d: dict) -> dict:
    st = new_stage()
    uni = dict(d.get("universe") or {})
    top = uni.pop("top", None)
    st["top"] = int(top) if top else None
    st["by"] = uni.pop("by", None) or "market_cap"
    inc = _sectors(uni.pop("include_sectors", None)) + [s for s in _sectors(uni.pop("sectors", None))]
    st["include_sectors"] = list(dict.fromkeys(inc))
    st["exclude_sectors"] = _sectors(uni.pop("exclude_sectors", None))
    st["universe_extra"] = copy.deepcopy(uni)
    st["derive"] = [{"name": k, "expr": str(v)} for k, v in (d.get("derive") or {}).items()]
    st["include"] = [str(x) for x in d.get("filters") or []]
    st["exclude"] = [str(x) for x in d.get("exclude") or []]
    st["rank"] = [{"col": k, "dir": str(v)} for k, v in (d.get("rank") or {}).items()]
    st["keep"] = int(d["keep"]) if d.get("keep") else None
    st["graham"] = copy.deepcopy(d["graham"]) if d.get("graham") else None
    return st


def spec_to_form(spec: dict, form: dict | None = None) -> dict:
    """The form of a spec dict (keeps `dataset` and `asof` of `form`), stage by stage. Graham tests are kept in their stage
    untouched (shown read-only) and written back by `form_to_spec`."""
    old = form or {}
    f = new_form(old.get("dataset"))
    f["asof"] = old.get("asof")
    f["name"], f["note"], f["n"] = str(spec.get("name") or "my_screen"), str(spec.get("note") or ""), int(spec.get("n") or 10)
    raw = spec.get("stages") or [spec]
    f["stages"] = [_stage_from_spec(dict(d)) for d in raw]
    f["current"] = 0
    return sync_form(f)


# ---- SB3: text pieces shared by the pane, the view and the tests
def graham_line(g) -> str:
    """'Graham tests: preset defensive, min passes 6' for a stage's Graham tests ('' when it has none)."""
    if not g:
        return ""
    from ..spec import PRESETS
    g = {"preset": g} if isinstance(g, str) else dict(g)
    preset = g.get("preset") or "defensive"
    parts = [f"preset {preset}"]
    mp = g.get("min_passes", getattr(PRESETS.get(preset), "min_passes", None))
    if mp is not None:
        parts.append(f"min passes {mp}")
    parts += [f"{k.replace('_', ' ')} {v}" for k, v in g.items() if k not in ("preset", "min_passes")]
    return "Graham tests: " + ", ".join(parts)


def has_universe(st: dict) -> bool:
    return bool(st.get("top") or st.get("include_sectors") or st.get("exclude_sectors") or st.get("universe_extra"))


def stage_summary(st: dict) -> str:
    """'top 300, 1 include, rank vol_260d asc, keep 50': one stage in a line."""
    parts = []
    if st.get("top"):
        parts.append(f"top {st['top']}")
    for key, word in (("derive", "derived"), ("include", "include"), ("exclude", "exclude")):
        if st.get(key):
            parts.append(f"{len(st[key])} {word}")
    if st.get("graham"):
        parts.append("Graham tests")
    if st.get("rank"):
        parts.append("rank " + ", ".join(f"{r['col']} {r['dir']}" for r in st["rank"]))
    if st.get("keep"):
        parts.append(f"keep {st['keep']}")
    return ", ".join(parts) or "everything passes"


def stage_title(form: dict, i: int) -> str:
    """'Stage 2: rank vol_260d asc, keep 50' (a stage's own words, for the funnel and the view)."""
    sts = form["stages"]
    last = i == len(sts) - 1
    return f"Stage {i + 1}: {stage_summary(sts[i])}" + (f", take n={form['n']}" if last and len(sts) > 1 else "")


_STAGE_STEP = re.compile(r"^stage (\d+): ?(.*)$")


# LB: named expressions in the funnel; the dialogs' status groups; the schema rows of the starter / own columns
DATA_HINT = ("these need fundamentals: use --lab synthetic, or download data with make data-free / make data-panel; see the Data tab")
DIM_FOOTER = "Dimmed screens need data this dataset lacks: " + DATA_HINT.removeprefix("these need fundamentals: ") + "."
G_AVAILABLE, G_DIMMED, G_MINE = "Available on this dataset", "Needs data this dataset lacks", "Your saved screens"
NAMED_GROUPS = ("Starter columns", "My columns")
UNCOMPUTABLE = "not loaded in this lab"


def needs_text(missing, k: int = 4) -> str:
    """`needs: a, b, c, d +N more` from the first `k` missing columns; empty when nothing is missing."""
    m = [str(x) for x in missing or []]
    return "" if not m else "needs: " + ", ".join(m[:k]) + (f" +{len(m) - k} more" if len(m) > k else "")


def can_pick(row: dict) -> bool:
    """A `screen_status` row the dialogs let you select: the data has what it needs and the lab can run it."""
    return bool(row.get("available")) and row.get("runnable", True) is not False


def status_groups(rows, only=None):
    """The dialogs' rows from `screen_status` (REST `list_screens`): `(groups, dim)` with `groups` = [(heading, [names])] for
    "Available on this dataset" (Globe and Mail first, then Classic, registry order within), "Needs data this dataset lacks" and
    "Your saved screens" (available or not); empty groups left out. `dim` = {name: needs text} for every row that cannot be
    picked. `only`: names to keep (the Load dialog lists the loadable ones)."""
    rows = [r for r in rows or [] if only is None or r["name"] in only]
    rank = {"Globe and Mail": 0, "Classic": 1}
    avail = sorted((r for r in rows if r.get("group") != "Mine" and can_pick(r)), key=lambda r: rank.get(r.get("group"), 1))
    lacks = [r for r in rows if r.get("group") != "Mine" and not can_pick(r)]
    mine = [r for r in rows if r.get("group") == "Mine"]
    groups = [(t, [r["name"] for r in g]) for t, g in ((G_AVAILABLE, avail), (G_DIMMED, lacks), (G_MINE, mine)) if g]
    dim = {r["name"]: needs_text(r.get("missing")) or UNCOMPUTABLE for r in rows if not can_pick(r)}
    return groups, dim


def first_run_hint(rows) -> str:
    """The extra help sentence when fewer than half the registered screens can run on this dataset, else ''."""
    rows = list(rows or [])
    n = sum(can_pick(r) for r in rows)
    return f"This dataset can run {n} of {len(rows)} screens; the others are listed dimmed with what they need." if rows and 2 * n < len(rows) else ""


def loadable_names(lab) -> list:
    """Every registered screen the editor can load (a spec screen), runnable here or not."""
    from ..screens import REGISTRY
    spec = set(_builtin_groups())
    out = []
    for n in [*REGISTRY, *(n for n in lab.screens if n not in REGISTRY)]:
        sc = lab.screens.get(n)
        if (getattr(sc.fn, "__name__", "") == "rule_screen") if sc is not None else (n in spec):
            out.append(n)
    return out


def named_names(schema, computable: bool = True) -> list:
    """Names of the starter / own columns of a `screen_schema` result (those the data can compute, by default)."""
    return [c["name"] for c in (schema or {}).get("named") or [] if not (computable and (c.get("missing") or c.get("error")))]


def named_row(c: dict) -> tuple:
    """Schema panel cells of one named expression: ((name, group, type, cov %, range, description + expr), dimmed)."""
    miss = c.get("missing") or []
    cov = c.get("coverage")
    desc = "  ".join(x for x in (needs_text(miss), f"error: {c['error']}" if c.get("error") else "", (c.get("description") or "").strip(),
                                 f"= {c['expr']}" if c.get("expr") else "") if x)
    return ((c["name"], c.get("group") or "", c.get("dtype") or c.get("kind") or "", "" if cov is None or miss else f"{100 * cov:.0f}",
             "" if miss else value_range(c), desc), bool(miss or c.get("error")))


def uses_line(uses, width: int = 60) -> str:
    """`Uses your columns: shy = coalesce(div_yield, 0) + ...; x = ...` for the preview's `uses` ('' when there are none)."""
    if not uses:
        return ""
    cut = lambda e: e if len(e) <= width else e[:width - 4].rstrip() + " ..."
    return "Uses your columns: " + "; ".join(f"{u['name']} = {cut(str(u.get('expr') or ''))}" for u in uses)


def named_step(text: str, x: dict) -> str:
    """The funnel label of a derive row that comes from a named expression: 'Starter column shy' / 'My column x'."""
    g = x.get("named")
    if not g or x.get("kind") != "derive":
        return text
    return f"{'Starter column' if str(g).startswith('Starter') else 'My column'} {text.removeprefix('derive ').strip()}"


def group_funnel(funnel: list, form: dict | None = None) -> list:
    """The preview funnel as display rows `(kind, step, entry or None)` with a `header` row before each stage of a
    multi-stage screen ("Stage 2: rank vol_260d asc, keep 50"); a one-stage funnel has no headers."""
    out, seen = [], None
    for x in funnel or []:
        m = _STAGE_STEP.match(x.get("step") or "")
        if m:
            k = int(m.group(1))
            if k != seen:
                seen = k
                sts = (form or {}).get("stages") or []
                title = stage_title(form, k - 1) if 0 < k <= len(sts) else f"Stage {k}"
                out.append(("header", title, None))
            out.append(("step", named_step(m.group(2), x), x))                # LB
        else:
            out.append(("step", named_step(x.get("step") or "", x), x))
    return out


# ---- SB3: built-ins as starting points; the backtest settings that come with one
GROUPS = (("Globe and Mail", "globe"), ("Classic", "classic"), ("Your saved screens", None))


def _builtin_groups() -> dict:
    """Spec screen name -> 'globe' | 'classic', from the folder of its JSON file (specs/<group>/<name>.json)."""
    from .. import screens as _screens
    out = {}
    for f in _screens.spec_files():
        try:
            out[json.loads(f.read_text())["name"]] = f.parent.name
        except (OSError, ValueError, KeyError):
            continue
    return out


def screen_groups(lab) -> list:
    """The loadable spec screens in the order of the Load dialog: [{"title", "screens": [names]}] for "Globe and Mail",
    "Classic", then "Your saved screens" (every other spec screen of the lab: the ones from SCREENLAB_SCREENS). Empty groups
    are left out."""
    from ..screens import REGISTRY
    kind = _builtin_groups()
    order = list(REGISTRY)
    names = sorted(spec_screens(lab), key=lambda n: (order.index(n), n) if n in order else (len(order), n))
    out = []
    for title, key in GROUPS:
        got = [n for n in names if (kind.get(n) == key if key else n not in kind)]
        if got:
            out.append({"title": title, "screens": got})
    return out


def describe(lab, name: str) -> str:
    """One line for a screen from its spec `note`; the Globe notes lose their 'Follows the Globe's X:' lead."""
    note = (getattr(lab.screens.get(name), "note", "") or "").strip()
    if note == "spec screen":
        note = ""
    m = re.match(r"^Follows the Globe's [^:]+: (.*)$", note)
    return m.group(1) if m else note


def starting_points(lab) -> str:
    """The 'Starting points' line of the Screen tab help: the Globe portfolios by name with their one-line description,
    then the classic ones (press Load to open any of them)."""
    parts = []
    for g in screen_groups(lab)[:2]:
        parts.append(f"{g['title']}: " + "; ".join(f"{n} ({d})" if (d := describe(lab, n)) else n for n in g["screens"]))
    return "Starting points (Load): " + "   |   ".join(parts) if parts else ""


def builtin_settings(name: str) -> dict:
    """The backtest settings a built-in carries beyond its screen (rebalance_months, hold_rule {keep_within, band}, costs...),
    as scenario params. Empty for a screen that is not a built-in or carries none (those use the lab's defaults)."""
    from ..screens import GLOBE_PORTFOLIOS, REGISTRY
    p = GLOBE_PORTFOLIOS.get(name) if name in REGISTRY else None
    out = {}
    for k, v in (p.config_overrides if p else {}).items():
        if k == "hold_rule":
            v = {a: getattr(v, a) for a in ("keep_within", "band") if getattr(v, a, None) is not None}
        if v not in (None, {}):
            out[k] = v
    return out


def spec_screens(lab) -> list:
    """Names of the registered screens that are specs (loadable into the editor)."""
    return sorted(n for n, sc in lab.screens.items() if getattr(sc.fn, "__name__", "") == "rule_screen")


def _cell(v, nd=4) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}g}"
    return str(v)


def value_range(c: dict) -> str:
    """The range cell of a schema column: `min .. max` for numbers and dates, the top values for text."""
    if c.get("min") is not None or c.get("max") is not None:
        return f"{_cell(c.get('min'))} .. {_cell(c.get('max'))}"
    top = c.get("top") or c.get("values") or c.get("top_values")
    if isinstance(top, dict):
        top = list(top)
    if top:
        return ", ".join(str(x[0] if isinstance(x, (list, tuple)) else x) for x in list(top)[:4])
    return ""


def numeric_columns(schema: dict | None) -> list:
    return [c["name"] for c in (schema or {}).get("columns", []) if c.get("kind") == "number"]


def has_market_cap(schema: dict | None) -> bool:
    """LB: True when the data has a usable market cap, i.e. a "N largest" size cut can run on it."""
    for c in (schema or {}).get("columns", []):
        if c.get("name") == "market_cap":
            return (c.get("coverage") or 0) > 0
    return False


def is_default_form(form: dict) -> bool:
    """LB: True while the form is still the untouched default (nothing typed, nothing loaded)."""
    return form_to_spec(form) == form_to_spec(new_form(form.get("dataset"))) and not form.get("loaded_name")


def error_rows(res: dict) -> list:
    """Preview errors as `(where, message, suggestion text)`."""
    return [(e.get("where") or "", e.get("message") or "", f"did you mean {e['suggestion']!r}?" if e.get("suggestion") else "")
            for e in (res or {}).get("errors") or []]


def view_text(analyses: dict, grid) -> str:
    """Plain text of the tab for `Session.view`; `grid(columns, rows)` formats a table."""
    f = _upgrade(analyses.get("screen_form") or new_form())
    out = [f"screen {f['name']!r} on {f.get('dataset') or '?'}, as of {f.get('asof') or 'last full snapshot'}"
           + (f"; saved to {f['saved']}" if f.get("saved") else ""),
           "spec: " + json.dumps(form_to_spec(f)), HELP]
    if len(f["stages"]) > 1:                                                                  # SB3
        out += [("> " if i == f["current"] else "  ") + stage_title(f, i) for i in range(len(f["stages"]))]
    out += [f"  stage {i + 1} {graham_line(st['graham'])} (read-only)" for i, st in enumerate(f["stages"]) if st.get("graham")]
    if f.get("settings"):
        out.append("settings carried with " + str(f.get("loaded_name") or "the screen") + ": "
                   + ", ".join(f"{k} {json.dumps(v)}" for k, v in f["settings"].items()))
    pv = analyses.get("screen_preview")
    if not pv:
        return "\n".join(out + ["no preview yet: open the Screen tab (key 0, F5) or run the `screen_preview` command"])
    out.append(f"preview as of {pv.get('asof')}: {pv.get('n_names')} names, {pv.get('n_picks')} picks, {pv.get('stages')} stage(s)")
    if uses_line(pv.get("uses")):                                                              # LB
        out.append(uses_line(pv.get("uses")))
    rows = []
    for kind, text, x in group_funnel(pv.get("funnel") or [], f):
        if kind == "header":
            rows.append([text, "", "", "", "", ""])
        else:
            rows.append([text, x.get("expr") or "", x.get("rows_in"), x.get("rows_out"),
                         "" if x.get("alone") is None else x["alone"], x.get("note") or ""])
    if rows:
        out.append(grid(["step", "expression", "in", "out", "alone", "note"], rows))
    out += [f"ERROR {w}: {m}" + (f" ({s})" if s else "") for w, m, s in error_rows(pv)]
    picks = pv.get("picks") or []
    if picks:
        cols = list(dict.fromkeys(k for p in picks for k in (p.get("values") or {})))
        out.append(grid(["rank", "ticker", "sector", *cols],
                        [[p["rank"], p["ticker"], p.get("sector") or "", *[(p.get("values") or {}).get(c) for c in cols]]
                         for p in picks]))
    return "\n".join(out)


# ------------------------------------------------------------------ Textual pane
def pane(app):
    """The Screen `TabPane` (mounted first in ScreenlabApp.compose). Registers `app._draw_screen` and `app._screen_pane`."""
    from datetime import date

    from rich.text import Text
    from textual import events, on
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, DataTable, Input, Label, Select, Static, TabPane

    from .session import SessionError

    SECTIONS = (("derive", "Derived columns (name = expression)", "d"), ("include", "Include criteria (keep rows where true)", "i"),
                ("exclude", "Exclude criteria (drop rows where true)", "e"), ("rank", "Rank (sum of ranks, best first)", "r"))
    PREVIEW_DELAY, PUSH_DELAY = 0.6, 0.35

    def labelled(label, widget, cls="sb-field"):
        return Vertical(Label(label), widget, classes=cls)

    class ScreenPane(TabPane):
        DEFAULT_CSS = """
        ScreenPane #sb-body { height: 1fr; }
        ScreenPane .sb-col { width: 1fr; height: 1fr; padding: 0 1; }
        ScreenPane .sb-row { height: auto; }
        ScreenPane .sb-field { width: 1fr; height: auto; padding: 0 1 0 0; }
        ScreenPane .sb-field Label { color: $text-muted; }
        ScreenPane Input { height: 3; }
        ScreenPane Select { height: 3; }
        ScreenPane Button { min-width: 8; }
        ScreenPane #sb-help { height: auto; padding: 0 1; color: $text-muted; }
        ScreenPane #sb-status { height: auto; padding: 0 1; }
        ScreenPane #sb-dataset { width: 1fr; padding: 1 1 0 1; color: $text-muted; }
        ScreenPane #sb-asof { width: 16; }
        ScreenPane .sb-small { height: 7; }
        ScreenPane #sb-schema { height: 14; }
        ScreenPane #sb-helpers { height: auto; padding: 0 1; color: $text-muted; }
        ScreenPane #sb-start { height: auto; max-height: 6; padding: 0 1; color: $text-muted; }
        ScreenPane #sb-stagebar { height: auto; }
        ScreenPane #sb-stagebar Button { min-width: 11; }
        ScreenPane #sb-stage-title { height: auto; color: $accent; }
        ScreenPane #sb-graham { height: auto; padding: 0 1; color: $text-muted; }
        ScreenPane #sb-funnel { height: 12; }
        ScreenPane #sb-picks { height: 12; }
        ScreenPane #sb-summary { height: auto; padding: 0 1; }
        ScreenPane #sb-uses { height: auto; padding: 0 1; color: $text-muted; }
        ScreenPane #sb-colbox { height: auto; }
        ScreenPane #sb-colbox Label { padding: 1 1 0 0; }
        ScreenPane #sb-col-note { width: 1fr; }
        ScreenPane .sb-btns { height: auto; }
        """

        def __init__(self):
            super().__init__("Screen", id=TAB)
            self._seen: dict | None = None          # the form last drawn (or pushed by this pane)
            self._pv_seen = None                    # the preview last drawn
            self._pv_sig = None                     # (spec, asof) last asked for
            self._pv_timer = None
            self._push_timer = None
            self._pv_busy = False
            self._pv_dirty = False
            self._schema: dict | None = None
            self._schema_key = "unset"
            self._schema_busy = False
            self._schema_rows: list = []
            self._expr_focus = None
            self._msg: Text | None = None
            self._opts: dict = {}
            self._start_done = False
            self._schema_meta: list = []            # LB: per schema row: the named-expression dict or None
            self._del_pending = None                # LB: name of the my-column row whose Delete awaits confirmation
            self._col_pending = None                # LB: (name, expr) being saved as my column
            self._schema_redo = False

        # ---- layout
        def compose(self):
            yield Static(HELP, id="sb-help")
            yield Static("", id="sb-start")
            with Horizontal(id="sb-body"):
                with VerticalScroll(classes="sb-col", id="sb-left"):
                    with Horizontal(classes="sb-row"):
                        yield labelled("Name", Input(id="sb-name"))
                        yield labelled("Note", Input(id="sb-note"))
                        yield Static("", id="sb-dataset")
                    with Horizontal(classes="sb-row"):
                        yield Button("< year", id="sb-prev")
                        yield labelled("As of (blank = last full snapshot)", Input(id="sb-asof", placeholder="YYYY-MM-DD"))
                        yield Button("year >", id="sb-next")
                    with Horizontal(id="sb-stagebar"):                                    # SB3: stages
                        for k in range(MAX_STAGES):
                            yield Button(f"Stage {k + 1}", id=f"sb-st-{k}")
                        yield Button("+ Add stage", id="sb-st-add")
                        yield Button("- Remove stage", id="sb-st-del")
                    yield Static("", id="sb-stage-title")
                    yield Static("Universe", classes="pane-title", id="sb-uni-title")
                    with Horizontal(classes="sb-row", id="sb-uni-a"):
                        yield labelled("Top N (blank = all)", Input(id="sb-top"))
                        yield labelled("By", Select([("market_cap", "market_cap")], id="sb-by", allow_blank=False, value="market_cap"))
                    with Horizontal(classes="sb-row", id="sb-uni-b"):
                        yield labelled("Include sectors (comma separated)", Input(id="sb-inc-sec"))
                        yield labelled("Exclude sectors (comma separated)", Input(id="sb-exc-sec"))
                    with Horizontal(classes="sb-row"):
                        yield labelled("Pick count n", Input(id="sb-n"))
                        yield Vertical(Label("Keep (best N of this stage go on)"), Input(id="sb-keep"), classes="sb-field", id="sb-keep-box")
                    yield Static("", id="sb-graham")
                    for key, title, k in SECTIONS:
                        yield Static(title, classes="pane-title")
                        yield DataTable(id=f"sb-t-{key}", cursor_type="row", classes="sb-small")
                        with Horizontal(classes="sb-row"):
                            if key == "derive":
                                yield labelled("Name", Input(id="sb-d-name"))
                                yield labelled("Expression", Input(id="sb-d-expr", classes="sb-expr", placeholder="e.g. earn_yield = 1 / pe"))
                            elif key == "rank":
                                yield labelled("Column", Select([("market_cap", "market_cap")], id="sb-r-col", allow_blank=False, value="market_cap"))
                                yield labelled("Order", Select([(d, d) for d in DIRS], id="sb-r-dir", allow_blank=False, value="asc"))
                            else:
                                yield labelled("Expression", Input(id=f"sb-{k}-expr", classes="sb-expr",
                                                                   placeholder="e.g. pe < 15 and roe > 0.1" if key == "include" else "e.g. sector == 'Fin'"))
                            yield Horizontal(Button("Add", id=f"sb-{k}-add"), Button("Update", id=f"sb-{k}-set"),
                                             Button("Remove", id=f"sb-{k}-del"),
                                             *([Button("Save as my column", id="sb-d-save")] if key == "derive" else []), classes="sb-btns")
                        if key == "derive":                                           # LB: inline note box for Save as my column
                            with Horizontal(id="sb-colbox", classes="sb-row"):
                                yield Label("Note for my column (Enter saves, Esc cancels)", id="sb-col-label")
                                yield Input(id="sb-col-note", placeholder="what it means, one line")
                                yield Button("Overwrite", id="sb-col-over", variant="warning")
                with VerticalScroll(classes="sb-col", id="sb-right"):
                    with Horizontal(classes="sb-btns"):
                        yield Button("Load", id="sb-load")
                        yield Button("New", id="sb-new")
                        yield Button("Save", id="sb-save")
                        yield Button("Preview", id="sb-preview")
                        yield Button("Add as backtest", id="sb-add", variant="primary")
                        yield Button("Run", id="sb-run", variant="success")
                    yield Static("", id="sb-status")
                    yield Static("Funnel: what each step does to the names", classes="pane-title")
                    yield Static("", id="sb-uses")
                    yield Static("", id="sb-summary")
                    yield DataTable(id="sb-funnel", cursor_type="row")
                    yield Static("Picks", classes="pane-title")
                    yield DataTable(id="sb-picks", cursor_type="row")
                    yield Static("Columns (filter, Enter inserts the name into the expression box)", classes="pane-title")
                    yield Input(id="sb-filter", placeholder="filter columns")
                    yield DataTable(id="sb-schema", cursor_type="row")
                    yield Static("", id="sb-helpers")

        def on_mount(self) -> None:
            self.query_one("#sb-colbox").display = False
            self.query_one("#sb-uses").display = False
            for key, cols in (("funnel", ("Step", "Expression", "In", "Out", "Alone", "Note")),
                              ("picks", ("Rank", "Ticker", "Sector")),
                              ("schema", ("Name", "Group", "Type", "Cov %", "Range", "Description"))):
                self.query_one(f"#sb-{key}", DataTable).add_columns(*cols)

        # ---- calling the session
        def _say(self, text: str, error: bool = False) -> None:
            self._msg = Text(text, style="bold red" if error else "")
            self.query_one("#sb-status", Static).update(self._msg)

        def _call(self, cmd: str, args: dict | None = None):
            try:
                return self.app.session.command(cmd, args or {})
            except SessionError as e:
                self._say(f"{cmd}: {e.message}", True)
                self.app.notify(e.message, severity="error", title=cmd)
                self.app.session.log(f"{cmd}: {e.message}")
                return None

        def _spec(self) -> dict:
            return form_to_spec(self._seen or self.app.session.analyses.get("screen_form") or new_form())

        # ---- form <-> widgets
        def _scalars(self) -> dict:
            q = lambda i: self.query_one(i, Input).value.strip()
            out = {"name": q("#sb-name") or None, "note": self.query_one("#sb-note", Input).value, "asof": q("#sb-asof") or None,
                   "include_sectors": q("#sb-inc-sec"), "exclude_sectors": q("#sb-exc-sec")}
            by = self.query_one("#sb-by", Select).value
            if isinstance(by, str):
                out["by"] = by
            for k, i in (("top", "#sb-top"), ("n", "#sb-n"), ("keep", "#sb-keep")):
                try:
                    out[k] = _int(k, q(i), 1, optional=(k != "n"))
                except SessionError as e:
                    self._say(e.message, True)
            if out["name"] is None:
                del out["name"]
            return out

        @staticmethod
        def _form_scalars(f: dict) -> dict:
            st = f["stages"][f["current"]]                                       # SB3: per-stage fields of the shown stage
            return {"name": f["name"], "note": f["note"], "asof": f["asof"], "include_sectors": ", ".join(st["include_sectors"]),
                    "exclude_sectors": ", ".join(st["exclude_sectors"]), "by": st["by"], "top": st["top"], "n": f["n"],
                    "keep": st["keep"]}

        def _stage(self) -> int:
            return (self._seen or {}).get("current", 0)

        def _changed_scalars(self) -> dict:
            base = self._form_scalars(self._seen or new_form())
            cur = self._scalars()
            norm = lambda k, v: ", ".join(_sectors(v)) if k in ("include_sectors", "exclude_sectors") else v
            return {k: v for k, v in cur.items() if norm(k, v) != base.get(k)}

        def _push(self) -> None:
            ch = self._changed_scalars()
            if not ch:
                return
            res = self._call("set_screen_form", {**ch, "stage": self._stage()})
            if res:
                self._seen = copy.deepcopy(res["form"])         # our own edit: draw must not refill the boxes under the cursor
                self._msg = None
                self._schedule_preview(res["form"])

        def _later_push(self) -> None:
            if self._push_timer is not None:
                self._push_timer.stop()
            self._push_timer = self.set_timer(PUSH_DELAY, self._push)

        @on(Input.Changed, "#sb-name, #sb-note, #sb-asof, #sb-top, #sb-n, #sb-inc-sec, #sb-exc-sec, #sb-keep")
        def _scalar_changed(self) -> None:
            if self._seen is not None and self._changed_scalars():
                self._later_push()

        @on(Select.Changed, "#sb-by")
        def _by_changed(self) -> None:
            if self._seen is not None and self._changed_scalars():
                self._later_push()

        def _fill(self, f: dict) -> None:
            vals = self._form_scalars(f)
            for i, k in (("#sb-name", "name"), ("#sb-note", "note"), ("#sb-asof", "asof"), ("#sb-top", "top"), ("#sb-n", "n"),
                         ("#sb-inc-sec", "include_sectors"), ("#sb-exc-sec", "exclude_sectors"), ("#sb-keep", "keep")):
                w = self.query_one(i, Input)
                new = "" if vals[k] is None else str(vals[k])
                if w.value != new:
                    w.value = new
            st = f["stages"][f["current"]]
            self._set_select("#sb-by", st["by"], numeric_columns(self._schema))
            self.query_one("#sb-dataset", Static).update(f"Dataset: {f.get('dataset') or '?'}")
            self._draw_stages(f)

        def _draw_stages(self, f: dict) -> None:
            """SB3: the stage bar (one stage: only "+ Add stage"), the universe block (stage 1, or a later stage that has one),
            the keep box (every stage but the last) and the read-only Graham line."""
            sts, cur = f["stages"], f["current"]
            multi = len(sts) > 1
            for k in range(MAX_STAGES):
                b = self.query_one(f"#sb-st-{k}", Button)
                b.display = multi and k < len(sts)
                b.variant = "primary" if k == cur else "default"
            self.query_one("#sb-st-add", Button).display = len(sts) < MAX_STAGES
            self.query_one("#sb-st-del", Button).display = multi
            t = self.query_one("#sb-stage-title", Static)
            t.display = multi
            if multi:
                t.update(f"Editing {stage_title(f, cur)}")
            st = sts[cur]
            uni = cur == 0 or has_universe(st)
            for wid in ("#sb-uni-title", "#sb-uni-a", "#sb-uni-b"):
                self.query_one(wid).display = uni
            self.query_one("#sb-keep-box").display = multi and cur < len(sts) - 1
            g = self.query_one("#sb-graham", Static)
            g.update(graham_line(st.get("graham")))
            g.display = bool(st.get("graham"))

        def _set_select(self, wid: str, value, extra=()) -> None:
            sel = self.query_one(wid, Select)
            opts = list(dict.fromkeys(n for n in [value, *extra] if isinstance(n, str) and n)) or ["market_cap"]
            if self._opts.get(wid) != opts:
                self._opts[wid] = opts
                sel.set_options([(o, o) for o in opts])
            if isinstance(value, str) and value and sel.value != value:
                sel.value = value

        # ---- tables
        def _tables(self, f: dict) -> None:
            def fill(key, cols, rows):
                t = self.query_one(f"#sb-t-{key}", DataTable)
                t.clear(columns=True)
                t.add_columns(*cols)
                for r in rows:
                    t.add_row(*r)
            st = f["stages"][f["current"]]
            fill("derive", ("Name", "Expression"), [(r["name"], r["expr"]) for r in st["derive"]])
            fill("include", ("#", "Expression"), [(str(i + 1), x) for i, x in enumerate(st["include"])])
            fill("exclude", ("#", "Expression"), [(str(i + 1), x) for i, x in enumerate(st["exclude"])])
            fill("rank", ("#", "Column", "Order"), [(str(i + 1), r["col"], r["dir"]) for i, r in enumerate(st["rank"])])
            cur = self.query_one("#sb-r-col", Select).value
            self._set_select("#sb-r-col", cur if isinstance(cur, str) else None,
                             [*numeric_columns(self._schema), *[r["name"] for r in st["derive"]], *named_names(self._schema)])

        def _row_from_inputs(self, key: str):
            q = lambda i: self.query_one(i, Input).value.strip()
            if key == "derive":
                return {"name": q("#sb-d-name"), "expr": q("#sb-d-expr")}
            if key == "rank":
                col, d = self.query_one("#sb-r-col", Select).value, self.query_one("#sb-r-dir", Select).value
                return {"col": col if isinstance(col, str) else "", "dir": d if isinstance(d, str) else "asc"}
            return q(f"#sb-{'i' if key == 'include' else 'e'}-expr")

        def _clear_inputs(self, key: str) -> None:
            for i in {"derive": ("#sb-d-name", "#sb-d-expr"), "include": ("#sb-i-expr",), "exclude": ("#sb-e-expr",)}.get(key, ()):
                self.query_one(i, Input).value = ""

        def _edit(self, key: str, op: str) -> None:
            t = self.query_one(f"#sb-t-{key}", DataTable)
            if op == "add":
                args = {"add_row": {"table": key, "row": self._row_from_inputs(key), "stage": self._stage()}}
            elif not t.row_count:
                return
            elif op == "set":
                args = {"set_row": {"table": key, "index": int(t.cursor_row), "row": self._row_from_inputs(key), "stage": self._stage()}}
            else:
                args = {"remove_row": {"table": key, "index": int(t.cursor_row), "stage": self._stage()}}
            self._push_pending()
            res = self._call("set_screen_form", args)
            if res:
                self._msg = None
                if op == "add":
                    self._clear_inputs(key)
                self._seen = None                                # redraw everything from the session
                self.draw()

        def _push_pending(self) -> None:
            if self._push_timer is not None:
                self._push_timer.stop()
            self._push()

        @on(Button.Pressed, "#sb-d-add, #sb-d-set, #sb-d-del, #sb-i-add, #sb-i-set, #sb-i-del, #sb-e-add, #sb-e-set, "
                            "#sb-e-del, #sb-r-add, #sb-r-set, #sb-r-del")
        def _table_button(self, ev: Button.Pressed) -> None:
            _, k, op = ev.button.id.split("-")
            self._edit({"d": "derive", "i": "include", "e": "exclude", "r": "rank"}[k], "remove" if op == "del" else op)

        @on(Input.Submitted, "#sb-i-expr, #sb-e-expr, #sb-d-expr")
        def _submit_expr(self, ev: Input.Submitted) -> None:
            self._edit({"sb-i-expr": "include", "sb-e-expr": "exclude", "sb-d-expr": "derive"}[ev.input.id], "add")

        @on(Input.Submitted, "#sb-d-name")
        def _submit_name(self) -> None:
            self.query_one("#sb-d-expr", Input).focus()

        @on(DataTable.RowSelected, "#sb-t-derive, #sb-t-include, #sb-t-exclude, #sb-t-rank")
        def _load_row(self, ev: DataTable.RowSelected) -> None:
            key = ev.data_table.id.removeprefix("sb-t-")
            f = self._seen or new_form()
            i = ev.cursor_row
            rows = f["stages"][f["current"]][key]
            if i is None or i >= len(rows):
                return
            r = rows[i]
            if key == "derive":
                self.query_one("#sb-d-name", Input).value, self.query_one("#sb-d-expr", Input).value = r["name"], r["expr"]
            elif key == "rank":
                self._set_select("#sb-r-col", r["col"], [*numeric_columns(self._schema), *named_names(self._schema)])
                self.query_one("#sb-r-dir", Select).value = r["dir"]
            else:
                self.query_one(f"#sb-{'i' if key == 'include' else 'e'}-expr", Input).value = r

        # ---- schema panel
        @on(events.DescendantFocus)
        def _remember_focus(self, ev: events.DescendantFocus) -> None:
            if isinstance(ev.widget, Input) and ev.widget.has_class("sb-expr"):
                self._expr_focus = ev.widget

        @on(Input.Changed, "#sb-filter")
        def _filter(self) -> None:
            self._draw_schema()

        @on(DataTable.RowSelected, "#sb-schema")
        def _insert(self, ev: DataTable.RowSelected) -> None:
            if ev.cursor_row is None or ev.cursor_row >= len(self._schema_rows):
                return
            name = self._schema_rows[ev.cursor_row]
            self._del_pending = None
            w = self._expr_focus or self.query_one("#sb-i-expr", Input)
            w.insert_text_at_cursor(name)
            w.focus()

        # ---- LB: my columns (delete, save a derived row as one)
        def on_key(self, ev: events.Key) -> None:
            f = self.app.focused
            if ev.key == "delete" and isinstance(f, DataTable) and f.id == "sb-schema":
                ev.stop()
                self._delete_column(f.cursor_row)
            elif ev.key == "escape" and isinstance(f, Input) and f.id == "sb-col-note" and self.query_one("#sb-colbox").display:
                ev.stop()
                self._close_colbox()

        def _delete_column(self, row) -> None:
            c = self._schema_meta[row] if row is not None and 0 <= row < len(self._schema_meta) else None
            if c is None:
                self._del_pending = None
                self._say("only your own columns (group My columns) can be removed; press Delete on one of them")
                return
            name = c["name"]
            if c.get("group") == "My columns" and self._del_pending != name:
                self._del_pending = name
                self._say(f"press Delete again to remove {name!r}")
                return
            self._del_pending = None
            res = self._call("column_delete", {"name": name})            # a starter column: the engine's 409 message, in red
            if res:
                self._say(f"removed my column {name!r}" + (f" (it was {res['expr']})" if res.get("expr") else ""))
                self._refresh_schema()
                self._pv_sig = None
                self._schedule_preview(self._seen or new_form())

        def _close_colbox(self) -> None:
            self._col_pending = None
            self.query_one("#sb-colbox").display = False
            self.query_one("#sb-col-over", Button).display = False
            self.query_one("#sb-col-note", Input).value = ""

        @on(Button.Pressed, "#sb-d-save")
        def _b_save_column(self) -> None:
            t = self.query_one("#sb-t-derive", DataTable)
            rows = (self._seen or new_form())["stages"][(self._seen or new_form())["current"]]["derive"]
            i = t.cursor_row
            if not t.row_count or i is None or i >= len(rows):
                self._say("select a Derived row first (the table above), then Save as my column", True)
                return
            self._col_pending = (rows[i]["name"], rows[i]["expr"])
            self.query_one("#sb-col-label", Label).update(f"Note for my column {rows[i]['name']!r} (Enter saves, Esc cancels)")
            self.query_one("#sb-colbox").display = True
            self.query_one("#sb-col-over", Button).display = False
            self.query_one("#sb-col-note", Input).focus()
            self._say(f"saving {rows[i]['name']} = {rows[i]['expr']} as my column: type a one-line note, then Enter")

        def _define(self, overwrite: bool) -> None:
            if not self._col_pending:
                return
            name, expr = self._col_pending
            note = self.query_one("#sb-col-note", Input).value.strip()
            try:
                res = self.app.session.command("column_define", {"name": name, "expr": expr, "note": note, "overwrite": overwrite})
            except SessionError as e:
                self._say(f"column_define: {e.message}", True)
                self.query_one("#sb-col-over", Button).display = e.status == 409 and "overwrite" in e.message.lower()
                return
            warn = res.get("warnings") or []
            self._say(f"saved my column {res['name']!r} = {res['expr']}" + (" (replaced)" if res.get("replaced") else "")
                      + ("; warning: " + "; ".join(warn) if warn else "") + "; type the name in a criterion to use it")
            self._close_colbox()
            self._refresh_schema()
            self._pv_sig = None
            self._schedule_preview(self._seen or new_form())

        @on(Input.Submitted, "#sb-col-note")
        def _submit_note(self) -> None:
            self._define(False)

        @on(Button.Pressed, "#sb-col-over")
        def _b_overwrite(self) -> None:
            self._define(True)

        def _draw_schema(self) -> None:
            t = self.query_one("#sb-schema", DataTable)
            t.clear()
            self._schema_rows, self._schema_meta = [], []
            q = self.query_one("#sb-filter", Input).value.strip().lower()
            for c in (self._schema or {}).get("columns", []):
                hay = f"{c['name']} {c.get('group') or ''} {c.get('description') or ''}".lower()
                if q and q not in hay:
                    continue
                self._schema_rows.append(c["name"])
                self._schema_meta.append(None)
                cov = c.get("coverage")
                t.add_row(c["name"], c.get("group") or "", c.get("kind") or "", "" if cov is None else f"{100 * cov:.0f}",
                          value_range(c), c.get("description") or "")
            for c in (self._schema or {}).get("named") or []:                       # LB: starter and own columns
                cells, dimmed = named_row(c)
                if q and q not in " ".join((cells[0], cells[1], cells[5], c.get("expr") or "")).lower():
                    continue
                self._schema_rows.append(c["name"])
                self._schema_meta.append(c)
                t.add_row(*[Text(x, style="dim") if dimmed else x for x in cells])
            hs = (self._schema or {}).get("helpers") or []
            self.query_one("#sb-helpers", Static).update(
                "Helpers: " + "   ".join(f"{h.get('signature') or h['name']}: {h.get('description') or ''}" for h in hs) if hs else "")

        def _load_schema(self, asof) -> None:
            if self._schema_busy:
                return
            self._schema_busy, key = True, asof

            def work():
                try:
                    res = self.app.session.command("screen_schema", {"asof": asof} if asof else {})
                    err = None
                except SessionError as e:
                    res, err = None, e.message
                self.app.call_from_thread(self._schema_done, key, res, err)
            self.app.run_worker(work, thread=True, group="sb-schema")

        def _refresh_schema(self) -> None:
            """LB: reload the schema panel (a column was defined or deleted)."""
            self._schema_key = "unset"
            if self._schema_busy:
                self._schema_redo = True
            else:
                self._load_schema((self._seen or {}).get("asof"))

        def _schema_done(self, key, res, err) -> None:
            self._schema_busy = False
            redo, self._schema_redo = self._schema_redo, False
            if err:
                self._say(f"screen_schema: {err}", True)
                self._schema_key = key
                return
            self._schema, self._schema_key = res, key
            self._draw_schema()
            if not self._start_done:                                                 # SB3: needs the lab, which the schema built
                self._start_done = True
                got = self._call("screen_load", {})
                if got:
                    self.query_one("#sb-start", Static).update(got.get("starting_points") or "")
                try:                                                                 # LB: first-run hint
                    hint = first_run_hint(self.app.session.command("screen_status")["screens"])
                except SessionError:
                    hint = ""
                self.query_one("#sb-help", Static).update(HELP + (" " + hint if hint else ""))
            f = self._seen or new_form()
            if not has_market_cap(res) and is_default_form(f):               # LB: price-only data (e.g. the bundled sample)
                if self._call("set_screen_form", {"top": None}) is not None:
                    f = self.app.session.analyses.get("screen_form") or f
                    self._say("This data has no market cap, so the default size cut (the 100 largest) is off.")
            self._fill(f)
            self._tables(f)
            self.query_one("#sb-asof", Input).placeholder = res.get("asof") or "YYYY-MM-DD"
            cur = (self._seen or {}).get("asof")
            if cur != key or redo:                                  # the form moved on while the schema loaded
                self._schema_key = "unset"
                if redo:
                    self._load_schema(cur)

        # ---- preview
        def _schedule_preview(self, f: dict) -> None:
            sig = json.dumps([form_to_spec(f), f.get("asof")], sort_keys=True)
            if sig == self._pv_sig:
                return
            self._pv_sig = sig
            if self._pv_timer is not None:
                self._pv_timer.stop()
            self._query_summary("previewing...")
            self._pv_timer = self.set_timer(PREVIEW_DELAY, self.run_preview)

        def _query_summary(self, text: str) -> None:
            self.query_one("#sb-summary", Static).update(text)

        def run_preview(self) -> None:
            """Preview now (F5, the Preview button, or the debounce timer)."""
            self._push_pending()
            if self._pv_timer is not None:
                self._pv_timer.stop()
                self._pv_timer = None
            if self._pv_busy:
                self._pv_dirty = True
                return
            f = self._seen or self.app.session.analyses.get("screen_form") or new_form()
            self._pv_sig = json.dumps([form_to_spec(f), f.get("asof")], sort_keys=True)
            self._pv_busy, args = True, {"spec": form_to_spec(f), **({"asof": f["asof"]} if f.get("asof") else {})}
            self._query_summary("previewing...")

            def work():
                try:
                    self.app.session.command("screen_preview", args)
                    err = None
                except SessionError as e:
                    err = e.message
                self.app.call_from_thread(self._preview_done, err)
            self.app.run_worker(work, thread=True, group="sb-preview")

        def _preview_done(self, err) -> None:
            self._pv_busy = False
            if err:
                self._say(f"screen_preview: {err}", True)
                self._query_summary("")
            if self._pv_dirty:
                self._pv_dirty = False
                self.run_preview()
            else:
                self._pv_seen = None
                self._draw_preview(self.app.session.analyses.get("screen_preview"))

        def _draw_preview(self, pv) -> None:
            if pv == self._pv_seen:
                return
            self._pv_seen = copy.deepcopy(pv)
            ft, pt = self.query_one("#sb-funnel", DataTable), self.query_one("#sb-picks", DataTable)
            ft.clear()
            pt.clear(columns=True)
            pt.add_columns("Rank", "Ticker", "Sector")
            if not pv:
                self.query_one("#sb-uses").display = False
                return
            ul = self.query_one("#sb-uses", Static)                                  # LB: named expressions the spec uses
            ul.update(Text(uses_line(pv.get("uses")), no_wrap=True, overflow="ellipsis"))
            ul.display = bool(pv.get("uses"))
            spec_err = next((e for e in pv.get("errors") or [] if e.get("where") == "spec"), None)
            if spec_err:                                                             # LB: a cycle among named columns, in red
                self._query_summary(Text(spec_err.get("message") or "bad spec", style="bold red"))
            else:
                self._query_summary(f"{pv.get('n_names')} names as of {pv.get('asof')}; {pv.get('n_picks')} picks; {pv.get('stages')} stage(s)")
            for kind, text, x in group_funnel(pv.get("funnel") or [], self._seen or self.app.session.analyses.get("screen_form")):
                if kind == "header":                                                 # SB3: "Stage 2: rank vol_260d asc, keep 50"
                    ft.add_row(Text(text, style="bold"), "", "", "", "", "")
                    continue
                note = x.get("note") or ""
                st = "bold dark_orange" if any(a in note for a in AMBER) else ""
                ft.add_row(Text(text, style=st), Text(x.get("expr") or "", style=st),
                           Text(_cell(x.get("rows_in")), justify="right"), Text(_cell(x.get("rows_out")), justify="right", style=st),
                           Text(_cell(x.get("alone")), justify="right", style=st), Text(note, style=st))
            for where, msg, sug in error_rows(pv):
                st = "bold red"
                ft.add_row(Text(where, style=st), Text(msg, style=st), "", "", "", Text(sug, style=st))
            picks = pv.get("picks") or []
            cols = list(dict.fromkeys(k for p in picks for k in (p.get("values") or {})))
            pt.clear(columns=True)
            pt.add_columns("Rank", "Ticker", "Sector", *cols)
            for p in picks:
                pt.add_row(Text(str(p["rank"]), justify="right"), p["ticker"], p.get("sector") or "",
                           *[Text(_cell((p.get("values") or {}).get(c)), justify="right") for c in cols])

        # ---- stages (SB3)
        def _stage_op(self, args: dict) -> None:
            self._push_pending()
            if self._call("set_screen_form", args):
                self._msg = None
                self._seen = None
                self.draw()

        @on(Button.Pressed, "#sb-st-add")
        def _b_stage_add(self) -> None:
            self._stage_op({"add_stage": True})

        @on(Button.Pressed, "#sb-st-del")
        def _b_stage_del(self) -> None:
            self._stage_op({"remove_stage": self._stage()})

        @on(Button.Pressed, ",".join(f"#sb-st-{k}" for k in range(MAX_STAGES)))
        def _b_stage_pick(self, ev: Button.Pressed) -> None:
            self._stage_op({"select_stage": int(ev.button.id.rsplit("-", 1)[1])})

        # ---- buttons
        @on(Button.Pressed, "#sb-preview")
        def _b_preview(self) -> None:
            self.run_preview()

        @on(Button.Pressed, "#sb-prev, #sb-next")
        def _year(self, ev: Button.Pressed) -> None:
            self._push_pending()
            f = self._seen or new_form()
            base = f.get("asof") or (self._schema or {}).get("asof")
            if not base:
                self._say("as-of date not known yet", True)
                return
            d = date.fromisoformat(str(base)[:10])
            y = d.year + (1 if ev.button.id == "sb-next" else -1)
            try:
                d = d.replace(year=y)
            except ValueError:
                d = d.replace(year=y, day=28)
            if self._call("set_screen_form", {"asof": d.isoformat()}):
                self._seen = None
                self.draw()

        @on(Button.Pressed, "#sb-new")
        def _b_new(self) -> None:
            self._push_pending()
            if self._call("set_screen_form", {"reset": True}):
                self._msg = None
                self.query_one("#sb-status", Static).update("")
                self._seen = None
                self.draw()

        @on(Button.Pressed, "#sb-load")
        def action_load(self) -> None:
            from .ui import AddScreen
            res = self._call("screen_load", {})
            if res is None:
                return
            try:                                                                     # LB: every loadable screen, dimmed where the data lacks
                groups, dim = status_groups(self.app.session.command("screen_status")["screens"], set(res.get("loadable") or []))
            except SessionError:
                groups = None
            if not groups:
                groups, dim = [(g["title"], g["screens"]) for g in res.get("groups") or []], {}

            def done(name):
                if name:
                    got = self._call("screen_load", {"name": name})
                    if got:
                        self._say(f"loaded {name}: {got.get('note') or ''}".rstrip(": "))
                        self._seen = None
                        self.draw()
            self.app.push_screen(AddScreen(res["screens"], "Load a screen into the editor (Enter), Esc cancels", groups=groups,
                                           dim=dim, footer=DIM_FOOTER), done)

        @on(Button.Pressed, "#sb-save")
        def action_save(self) -> None:
            self._push_pending()
            res = self._call("screen_save", {})
            if res:
                self._say(f"saved {res['name']} to {res['path']}")
                self._seen = None
                self.draw()

        def _add(self, run: bool) -> None:
            self._push_pending()
            res = self._call("screen_add", {"save": True, "run": run})
            if res:
                self._say(f"added backtest {res['scenario']['label']}" + (f"; saved to {res['saved']['path']}" if res.get("saved") else ""))

        @on(Button.Pressed, "#sb-add")
        def _b_add(self) -> None:
            self._add(False)

        @on(Button.Pressed, "#sb-run")
        def _b_run(self) -> None:
            self._add(True)

        # ---- repaint from the session
        def draw(self) -> None:
            s = self.app.session
            f = s.analyses.get("screen_form") or new_form()
            if self._seen != f:
                self._seen = copy.deepcopy(f)
                self._fill(f)
                self._tables(f)
                self._schedule_preview(f)
            if not self._schema_busy and self._schema_key != f.get("asof"):
                self._load_schema(f.get("asof"))
            self._draw_preview(s.analyses.get("screen_preview"))
            if self._msg is None:
                self.query_one("#sb-status", Static).update(f"saved: {f['saved']}" if f.get("saved") else "")

    p = ScreenPane()
    app._draw_screen = p.draw
    app._screen_pane = p
    return p
