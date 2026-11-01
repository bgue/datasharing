"""TUI Screen tab (docs/SCREEN_BUILDER.md): the pure form <-> spec pieces, the session commands, the headless Textual app and
a RemoteSession over HTTP. # SB2:

    python -m pytest screenlab/tests/test_tui_screen.py -q

The operations `screen_schema` / `screen_preview` / `screen_save` / `screen_load` are the engine's (ops/screen_builder.py);
while they are not registered the `ops` fixture stands in a small pandas implementation of the same JSON shapes, so the
tab can be tested on its own.
"""
from __future__ import annotations

import difflib
import json
import re

import pytest

from screenlab.tests.test_tui import arun, make_registry, parent, reg, settle      # noqa: F401  (fixtures)
from screenlab.tests.test_tui import until as _until
from screenlab.tui import screen_tab as S


async def until(pred, timeout=45.0, pilot=None, what="condition"):
    """The shared wait with a longer default: these tests load screens through the session lock while previews run in
    threads, which takes several times longer on a busy machine than the 15 s default allows."""
    return await _until(pred, timeout, pilot, what)
from screenlab.tui.session import COMMANDS, TABS, Session, SessionError

textual = pytest.importorskip("textual")
from textual.widgets import Button, DataTable, Input, OptionList, Select      # noqa: E402

from screenlab.tui.app import ScreenlabApp                                    # noqa: E402


# ------------------------------------------------------------------ stand-in operations (only when the engine's are absent)
def _have_ops() -> bool:
    from screenlab import operations
    operations.builtin_names()
    return "screen_preview" in operations.OPERATIONS


def _fake_op(self, name, args):
    """pandas stand-in for the four operations, same JSON shapes as docs/SCREEN_BUILDER.md."""
    import os
    from pathlib import Path

    import pandas as pd
    lab = self._lab()
    snap = lab.snapshot().reset_index() if "ticker" not in lab.snapshot().columns else lab.snapshot()
    asof = str(snap["date"].max())[:10]
    cols = [c for c in snap.columns if c not in ("ticker",)]
    if name == "screen_schema":
        out = []
        for c in cols:
            num = pd.api.types.is_numeric_dtype(snap[c]) and not pd.api.types.is_bool_dtype(snap[c])
            out.append({"name": c, "group": "fundamentals", "kind": "number" if num else "text", "dtype": str(snap[c].dtype),
                        "coverage": float(snap[c].notna().mean()), "min": float(snap[c].min()) if num else None,
                        "p50": float(snap[c].median()) if num else None, "max": float(snap[c].max()) if num else None,
                        "example": None, "description": f"the {c} column"})
        return {"asof": asof, "n_names": len(snap), "dataset": self.lab, "columns": out,
                "helpers": [{"name": "pct", "signature": "pct(col)", "description": "percentile rank 0..1"}]}
    if name == "screen_load":
        sp = self._screen_spec({"screen": args["name"]})
        if sp is None:
            raise SessionError(400, f"{args['name']!r} is a function screen")
        return {"name": args["name"], "spec": sp, "note": ""}
    if name == "screen_save":
        folder = Path(args.get("folder") or os.environ["SCREENLAB_SCREENS"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{args['spec']['name']}.json"
        if path.exists() and not args.get("overwrite"):
            raise SessionError(400, f"{path} exists; pass overwrite")
        path.write_text(json.dumps(args["spec"]))
        lab.define(args["spec"])
        return {"name": args["spec"]["name"], "path": str(path), "registered": True}
    spec, df, funnel, errors = args["spec"], snap, [], []
    uni = spec.get("universe") or {}
    if uni.get("top"):
        df = df.nlargest(int(uni["top"]), uni.get("by", "market_cap"))
    funnel.append({"step": "universe", "kind": "universe", "expr": None, "rows_in": len(snap), "rows_out": len(df), "alone": None, "note": None})
    known = set(cols) | set(spec.get("derive") or {})
    for kind, key in (("include", "filters"), ("exclude", "exclude")):
        for i, e in enumerate(spec.get(key) or []):
            bad = [w for w in re.findall(r"[A-Za-z_]\w*", e) if w not in known | {"and", "or", "not", "in"}]
            if bad:
                sug = (difflib.get_close_matches(bad[0], sorted(known), 1) or [None])[0]
                errors.append({"where": f"{key}[{i}]", "message": f"unknown column {bad[0]!r}", "suggestion": sug})
                continue
            m = df.eval(e).fillna(False).astype(bool)
            keep = m if kind == "include" else ~m
            note = "keeps every name" if keep.all() else ("drops every name" if not keep.any() else None)
            funnel.append({"step": f"{kind} {i + 1}", "kind": kind, "expr": e, "rows_in": len(df), "rows_out": int(keep.sum()),
                           "alone": int(keep.sum()) if kind == "include" else None, "note": note})
            df = df[keep]
    picks = [{"rank": k + 1, "ticker": str(r["ticker"]), "sector": r["sector"], "values": {"market_cap": float(r["market_cap"])}}
             for k, (_, r) in enumerate(df.head(int(spec.get("n", 10))).iterrows())]
    return {"ok": not errors, "asof": asof, "n_names": len(snap), "errors": errors, "funnel": funnel, "picks": picks,
            "n_picks": len(picks), "stages": 1}


@pytest.fixture(autouse=True)
def ops(monkeypatch, tmp_path):
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path / "screens"))
    if not _have_ops():
        monkeypatch.setattr(Session, "_screen_op", _fake_op)
    return tmp_path / "screens"


def snapshot_counts(reg_, top=100, expr="pe < 15"):
    """Independent expectation: rows kept by `expr` among the `top` largest names of the last snapshot."""
    snap = reg_.get("synthetic").snapshot()
    sub = snap.nlargest(top, "market_cap")
    return len(sub), int(sub.eval(expr).fillna(False).sum())


# ------------------------------------------------------------------ pure pieces
def full_form():
    f = S.new_form("synthetic")
    S.update_form(f, {"name": "cheap", "note": "n", "top": 80, "by": "market_cap", "n": 7,
                      "include_sectors": "Ind, Tech", "exclude_sectors": ["Fin"],
                      "derive": [{"name": "ey2", "expr": "earnings_yield * 2"}, {"name": "pbx", "expr": "pb * 1"}],
                      "include": ["pe < 15", "roa > 0"], "exclude": ["sector == 'Util'", "isna(pe)"],
                      "rank": [{"col": "div_yield", "dir": "desc"}, {"col": "pe", "dir": "asc"}, {"col": "ey2", "dir": "desc"}]})
    return f



async def pick_option(pilot, ol, name):
    """Highlight the option `name` and press Enter. The dialog's own on_mount highlights its first enabled row, and on a
    busy machine that runs AFTER a test has set its row; so wait for that first highlight, then set ours and check it held."""
    want = ol.get_option_index(name)
    await until(lambda: ol.highlighted is not None, pilot=pilot, what="the dialog's initial highlight")
    await pilot.pause()
    for _ in range(10):
        ol.highlighted = want
        await pilot.pause(0.1)
        await pilot.pause()
        if ol.highlighted == want:
            break
    else:
        raise AssertionError(f"could not highlight {name}: highlighted {ol.highlighted}, wanted {want}")
    await pilot.press("enter")


def test_form_spec_round_trip_keeps_every_part_and_order():
    f = full_form()
    spec = S.form_to_spec(f)
    assert spec == {"name": "cheap", "n": 7, "note": "n",
                    "universe": {"top": 80, "by": "market_cap", "include_sectors": ["Ind", "Tech"], "exclude_sectors": ["Fin"]},
                    "derive": {"ey2": "earnings_yield * 2", "pbx": "pb * 1"}, "filters": ["pe < 15", "roa > 0"],
                    "exclude": ["sector == 'Util'", "isna(pe)"], "rank": {"div_yield": "desc", "pe": "asc", "ey2": "desc"}}
    assert list(spec["rank"].items()) == [("div_yield", "desc"), ("pe", "asc"), ("ey2", "desc")]
    back = S.spec_to_form(json.loads(json.dumps(spec)), f)
    assert {k: back[k] for k in ("name", "note", "n", "top", "by", "include_sectors", "exclude_sectors", "derive", "include",
                                 "exclude", "rank")} == {k: f[k] for k in ("name", "note", "n", "top", "by", "include_sectors",
                                                                           "exclude_sectors", "derive", "include", "exclude", "rank")}
    assert S.form_to_spec(back) == spec


def test_default_form_gives_a_valid_spec():
    from screenlab.spec import ScreenSpec
    spec = S.form_to_spec(S.new_form())
    assert spec == {"name": "my_screen", "n": 10, "universe": {"top": 100, "by": "market_cap"}}
    assert ScreenSpec.from_dict(spec).build().name == "my_screen"
    assert S.form_to_spec(S.new_form()) == spec and "exclude" not in spec and "filters" not in spec


def test_loaded_graham_and_stages_survive_the_form():
    spec = {"name": "g", "n": 5, "universe": {"top": 50}, "graham": {"preset": "defensive"}, "rank": {"pe": "asc"}}
    assert S.form_to_spec(S.spec_to_form(spec)) == {**spec, "universe": {"top": 50, "by": "market_cap"}}
    st = {"name": "m", "n": 3, "stages": [{"universe": {"top": 300}, "keep": 50}, {"rank": {"pe": "asc"}}]}
    got = S.form_to_spec(S.spec_to_form(st))["stages"]               # SB3: stages are in the form now; `by` is written out
    assert got == [{"universe": {"top": 300, "by": "market_cap"}, "keep": 50}, {"rank": {"pe": "asc"}}]


def test_update_form_edits_and_validation():
    f = S.new_form("x")
    S.update_form(f, add_row={"table": "include", "row": "pe < 10"})
    S.update_form(f, add_row={"table": "include", "row": {"expr": "roa > 0"}})
    S.update_form(f, add_row={"table": "rank", "row": {"col": "pe", "dir": "asc"}})
    S.update_form(f, add_row={"table": "rank", "row": {"col": "pe", "dir": "desc"}})            # same column: replaced
    assert f["include"] == ["pe < 10", "roa > 0"] and f["rank"] == [{"col": "pe", "dir": "desc"}]
    S.update_form(f, set_row={"table": "include", "index": 0, "row": "pe < 12"}, remove_row=None)
    S.update_form(f, remove_row={"table": "include", "index": 1})
    assert f["include"] == ["pe < 12"]
    S.update_form(f, {"top": ""})
    assert f["top"] is None and "by" not in S.form_to_spec(f).get("universe", {})
    for bad, msg in [({"fields": {"bogus": 1}}, "unknown screen field"), ({"fields": {"n": 0}}, "at least 1"),
                     ({"fields": {"n": "x"}}, "whole number"), ({"add_row": {"table": "rank", "row": {"col": "pe", "dir": "up"}}}, "direction"),
                     ({"add_row": {"table": "derive", "row": {"name": "1x", "expr": "pe"}}}, "identifier"),
                     ({"add_row": {"table": "include", "row": "  "}}, "empty"), ({"remove_row": {"table": "include", "index": 9}}, "index"),
                     ({"add_row": {"table": "nope", "row": "x"}}, "unknown table"),
                     ({"fields": {"rank": [{"col": "pe"}, {"col": "pe"}]}}, "only once")]:
        with pytest.raises(SessionError, match=msg):
            S.update_form(f, **bad)
    S.update_form(f, reset=True, dataset="x")
    assert f == S.new_form("x")


def test_first_tab_and_keys_are_unchanged():
    assert TABS[0] == "screen" and TABS[1] == "compare"
    assert {"screen_schema", "screen_preview", "screen_save", "screen_load", "screen_add", "set_screen_form"} <= set(COMMANDS)
    keys = {b.key: b.action for b in ScreenlabApp.BINDINGS if isinstance(b.key, str) and b.key.isdigit()}
    assert keys["0"] == "tab('screen')" and keys["1"] == "tab('compare')" and keys["2"] == "tab('curves')" and keys["9"] == "tab('data')"
    from screenlab.tui import ui
    assert ui.TAB_TITLES["screen"] == "Screen"


# ------------------------------------------------------------------ session commands
def test_session_form_commands_view_and_scenario(reg, ops):
    s = Session(reg)
    r = s.command("set_screen_form", {"name": "cheap_pe", "include_sectors": "Ind,Tech",
                                      "add_row": {"table": "include", "row": "pe < 15"}})
    assert r["spec"]["filters"] == ["pe < 15"] and r["spec"]["universe"]["include_sectors"] == ["Ind", "Tech"]
    assert s.analyses["screen_form"]["dataset"] == "synthetic" and s.snapshot()["analyses"]["screen_form"]["name"] == "cheap_pe"
    pv = s.command("screen_preview", {})
    assert pv["ok"] and pv["funnel"][0]["kind"] == "universe" and s.analyses["screen_preview"]["n_picks"] == pv["n_picks"]
    v = s.command("view", {"tab": "screen"})["view"]
    assert "screen 'cheap_pe'" in v and "pe < 15" in v and "keep the rows" in v and "preview as of" in v
    sv = s.command("screen_save", {})
    assert sv["name"] == "cheap_pe" and (ops / "cheap_pe.json").exists() and s.analyses["screen_form"]["saved"] == sv["path"]
    s.command("screen_save", {})                                     # the editor's own file: overwritten without a flag
    with pytest.raises(SessionError):                                # another name, same file would be refused by the engine
        s.command("screen_save", {"spec": {"name": "cheap_pe", "n": 3}, "overwrite": False})
    assert "cheap_pe" in s.screen_names() and "cheap_pe" in s.command("screen_load", {})["screens"]
    assert "graham_defensive" not in s.command("screen_load", {})["screens"] and "dogs" in s.command("screen_load", {})["screens"]
    out = s.command("screen_add", {"run": True, "wait": True})
    assert out["scenario"]["label"] == "cheap_pe" and out["jobs"][0]["status"] == "done"
    assert s.active_tab == "compare" and s.selected == out["scenario"]["id"]
    assert s.scenarios[0].params["screen"]["filters"] == ["pe < 15"]
    ld = s.command("screen_load", {"name": "dogs"})
    assert ld["form"]["name"] == "dogs" and ld["form"]["rank"] == [{"col": "div_yield", "dir": "desc"}] and ld["form"]["top"] == 30
    assert s.analyses["screen_form"]["include"] == ["div_yield > 0"] and s.analyses["screen_form"]["loaded_name"] == "dogs"
    with pytest.raises(SessionError):
        s.command("screen_load", {"name": "graham_defensive"})
    s.close()


# ------------------------------------------------------------------ the Textual app
def cell_texts(table: DataTable) -> list:
    return [[c for c in table.get_row_at(i)] for i in range(table.row_count)]


def plain(c) -> str:
    return getattr(c, "plain", str(c))


async def open_tab(pilot, app):
    await settle(pilot, app)
    await pilot.press("0")
    await until(lambda: app.session.active_tab == "screen" and app.query_one("#main").active == "screen", pilot=pilot, what="screen tab")
    await settle(pilot, app)


def test_app_screen_is_the_first_tab_and_key_0_opens_it(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await settle(pilot, app)
            assert [t.id for t in app.query("TabPane").results() if t.id][0] == "screen"
            await pilot.press("2")
            await settle(pilot, app)
            assert s.active_tab == "curves"
            await open_tab(pilot, app)
            assert "include: keep the rows where" in plain(app.query_one("#sb-help").render()) or "keep the rows" in str(app.query_one("#sb-help").render())
            assert "NaN" in S.HELP and "not excluded" in S.HELP
        s.close()
    arun(go())


def test_new_cli_session_starts_on_the_screen_tab():
    from screenlab.tui import ui
    s = ui.build_session(None, "none")
    try:
        assert s.active_tab == "screen" and not s.scenarios
    finally:
        s.close()


def test_app_schema_panel_funnel_errors_save_add_and_load(reg, ops):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            schema = app.query_one("#sb-schema", DataTable)
            await until(lambda: schema.row_count > 20, pilot=pilot, what="schema panel filled")
            names = {plain(schema.get_row_at(i)[0]) for i in range(schema.row_count)}
            assert {"pe", "market_cap", "sector"} <= names
            assert "pct(col)" in str(app.query_one("#sb-helpers").render())
            filt = app.query_one("#sb-filter", Input)
            filt.value = "div_yield"
            await until(lambda: 0 < schema.row_count < len(names) and all("div_yield" in plain(schema.get_row_at(i)[0]) or "div_yield" in plain(schema.get_row_at(i)[5])
                                                                         for i in range(schema.row_count)), pilot=pilot, what="schema filtered")
            filt.value = ""
            await until(lambda: schema.row_count == len(names), pilot=pilot, what="schema unfiltered")

            # the default form previews: universe step, top 100 names
            funnel, picks = app.query_one("#sb-funnel", DataTable), app.query_one("#sb-picks", DataTable)
            await until(lambda: funnel.row_count >= 1, pilot=pilot, what="first preview")
            assert plain(funnel.get_row_at(0)[3]) == "100"

            # an include criterion typed into the box; Enter adds it, the preview follows after the debounce
            inp = app.query_one("#sb-i-expr", Input)
            inp.focus()
            inp.value = "pe < 15"
            await pilot.press("enter")
            n_top, n_keep = snapshot_counts(reg)
            def inc_row(expr):
                return next(([plain(c) for c in r] for r in cell_texts(funnel) if plain(r[1]) == expr), None)
            await until(lambda: inc_row("pe < 15"), pilot=pilot, what="include row in the funnel")
            row = inc_row("pe < 15")
            assert row[1] == "pe < 15" and row[2] == str(n_top) and row[3] == str(n_keep) and row[4] == str(n_keep)
            assert s.analyses["screen_form"]["include"] == ["pe < 15"]
            assert picks.row_count == min(10, n_keep)
            assert app.query_one("#t-compare").row_count == 1                    # nothing was run

            # Enter on a schema row inserts the column name into the focused expression box
            inp.value = "roa > "
            inp.cursor_position = len(inp.value)
            inp.focus()
            filt.value = "roa"
            await until(lambda: schema.row_count >= 1 and plain(schema.get_row_at(0)[0]) == "roa", pilot=pilot, what="roa row first")
            schema.focus()
            schema.move_cursor(row=0)
            await pilot.press("enter")
            await until(lambda: inp.value == "roa > roa", pilot=pilot, what="column name inserted")
            filt.value = ""
            inp.value = ""

            # an unknown column: red rows with the message and a did-you-mean suggestion
            inp.focus()
            inp.value = "pee < 15"
            await pilot.press("enter")
            await until(lambda: any("pee" in plain(c) for r in cell_texts(funnel) for c in r), pilot=pilot, what="error row")
            err = [r for r in cell_texts(funnel) if "pee" in " ".join(plain(c) for c in r) and any(getattr(c, "style", "") and "red" in str(c.style) for c in r)]
            assert err, cell_texts(funnel)
            assert "pe" in " ".join(plain(c) for c in err[0]) and "did you mean" in " ".join(plain(c) for c in err[0])
            t_inc = app.query_one("#sb-t-include", DataTable)
            t_inc.focus()
            t_inc.move_cursor(row=1)
            app.query_one("#sb-i-del", Button).press()                           # remove the bad row
            await until(lambda: t_inc.row_count == 1 and not any("pee" in plain(c) for r in cell_texts(funnel) for c in r), pilot=pilot, what="error gone")
            # an amber note for a criterion that keeps every name
            inp.value = "market_cap > 0"
            inp.focus()
            await pilot.press("enter")
            await until(lambda: any("keeps every name" in plain(c) for r in cell_texts(funnel) for c in r), pilot=pilot, what="amber note")
            amber = [r for r in cell_texts(funnel) if any("keeps every name" in plain(c) for c in r)][0]
            assert "orange" in str(amber[5].style)
            assert t_inc.row_count == 2
            t_inc.move_cursor(row=1)                                             # back to one criterion
            app.query_one("#sb-i-del", Button).press()
            await until(lambda: t_inc.row_count == 1, pilot=pilot, what="back to one criterion")

            # name it, add an exclude and a rank row, save with the button
            app.query_one("#sb-name", Input).value = "cheap_pe"
            e = app.query_one("#sb-e-expr", Input)
            e.focus()
            e.value = "sector == 'Fin'"
            await pilot.press("enter")
            app.query_one("#sb-r-col", Select).value = "div_yield"
            app.query_one("#sb-r-dir", Select).value = "desc"
            app.query_one("#sb-r-add", Button).press()
            await until(lambda: s.analyses["screen_form"]["name"] == "cheap_pe" and s.analyses["screen_form"]["rank"] == [{"col": "div_yield", "dir": "desc"}]
                        and s.analyses["screen_form"]["exclude"] == ["sector == 'Fin'"], pilot=pilot, what="form pushed")
            await pilot.press("ctrl+s")
            path = ops / "cheap_pe.json"
            await until(path.exists, pilot=pilot, what="file saved")
            saved = json.loads(path.read_text())
            assert saved["filters"] == ["pe < 15"] and saved["exclude"] == ["sector == 'Fin'"] and saved["rank"] == {"div_yield": "desc"}
            await until(lambda: "cheap_pe.json" in str(app.query_one("#sb-status").render()), pilot=pilot, what="status shows the path")
            assert "cheap_pe" in s.screen_names()
            app.action_add()                                                      # the Add dialog (key a) lists it
            await until(lambda: app.screen.__class__.__name__ == "AddScreen", pilot=pilot, what="AddScreen")
            assert "cheap_pe" in app.screen.names
            await pilot.press("escape")
            await until(lambda: app.screen.__class__.__name__ != "AddScreen", pilot=pilot, what="dialog closed")

            # Run: saves (overwriting its own file), adds, selects, shows Compare and the row appears after the run
            app.query_one("#sb-run", Button).press()
            await until(lambda: s.scenarios and s.active_tab == "compare" and s.scenarios[0].status == "done", pilot=pilot, what="scenario done")
            await settle(pilot, app)
            assert s.scenarios[0].label == "cheap_pe" and s.selected == s.scenarios[0].id
            t = app.query_one("#t-compare", DataTable)
            assert [plain(t.get_row_at(i)[0]) for i in range(t.row_count)] == ["● SYN_CAPWT (passive)", "● cheap_pe"]
            assert app.query_one("#main").active == "compare"

            # Load: the dialog lists spec screens only; dogs fills the form
            await pilot.press("0")
            await settle(pilot, app)
            app.query_one("#sb-load", Button).press()
            await until(lambda: app.screen.__class__.__name__ == "AddScreen", pilot=pilot, what="load dialog")
            assert "dogs" in app.screen.names and "graham_defensive" not in app.screen.names
            ol = app.screen.query_one(OptionList)
            await pick_option(pilot, ol, "dogs")
            await until(lambda: app.query_one("#sb-name", Input).value == "dogs", pilot=pilot, what="form loaded")
            assert app.query_one("#sb-top", Input).value == "30" and app.query_one("#sb-n", Input).value == "10"
            assert [plain(c) for c in app.query_one("#sb-t-include", DataTable).get_row_at(0)][1] == "div_yield > 0"
            assert [plain(c) for c in app.query_one("#sb-t-rank", DataTable).get_row_at(0)][1:] == ["div_yield", "desc"]
            assert app.query_one("#sb-t-exclude", DataTable).row_count == 0
            # New resets
            app.query_one("#sb-new", Button).press()
            await until(lambda: app.query_one("#sb-name", Input).value == "my_screen" and app.query_one("#sb-t-include", DataTable).row_count == 0,
                        pilot=pilot, what="reset")
        s.close()
    arun(go())


def test_app_year_buttons_move_the_as_of_date(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            await until(lambda: app.query_one("#sb-schema", DataTable).row_count > 0, pilot=pilot, what="schema")
            last = app.query_one("#sb-asof", Input).placeholder
            app.query_one("#sb-prev", Button).press()
            y = str(int(last[:4]) - 1) + last[4:]
            await until(lambda: s.analyses["screen_form"]["asof"] == y and app.query_one("#sb-asof", Input).value == y, pilot=pilot, what="previous year")
            await until(lambda: (s.analyses["screen_preview"] or {}).get("asof", "")[:4] == y[:4], pilot=pilot, what="preview at the earlier date")
            app.query_one("#sb-next", Button).press()
            await until(lambda: s.analyses["screen_form"]["asof"] == last, pilot=pilot, what="next year")
        s.close()
    arun(go())


# ------------------------------------------------------------------ over HTTP (RemoteSession)
def test_remote_session_screen_commands(parent, ops):
    r = parent.connect()
    out = r.command("set_screen_form", {"name": "remote_pe", "add_row": {"table": "include", "row": "pe < 15"}})
    assert out["spec"]["filters"] == ["pe < 15"] and r.analyses["screen_form"]["name"] == "remote_pe"
    assert parent.session.analyses["screen_form"]["include"] == ["pe < 15"]               # one shared form
    sc = r.command("screen_schema", {})
    assert any(c["name"] == "pe" for c in sc["columns"]) and sc["helpers"]
    pv = r.command("screen_preview", {})
    assert pv["funnel"][0]["kind"] == "universe" and r.analyses["screen_preview"]["n_picks"] == pv["n_picks"]
    assert "remote_pe" in r.view("screen")
    sv = r.command("screen_save", {})
    assert (ops / "remote_pe.json").exists() and r.analyses["screen_form"]["saved"] == sv["path"] and "remote_pe" in r.screen_names()
    assert "dogs" in r.command("screen_load", {})["screens"]
    assert r.command("screen_load", {"name": "dogs"})["form"]["rank"] == [{"col": "div_yield", "dir": "desc"}]
    r.command("set_screen_form", {"reset": True})
    add = r.command("screen_add", {"spec": {"name": "dogs2", "n": 5, "universe": {"top": 30}, "filters": ["div_yield > 0"],
                                            "rank": {"div_yield": "desc"}}, "run": True, "wait": True})
    assert add["jobs"][0]["status"] == "done" and r.active_tab == "compare" and [x.label for x in r.scenarios] == ["dogs2"]
    with pytest.raises(SessionError) as e:
        r.command("set_screen_form", {"n": 0})
    assert e.value.status == 400
    with pytest.raises(SessionError) as e:
        r.command("screen_load", {"name": "graham_defensive"})
    assert e.value.status == 400


def test_app_screen_tab_over_remote_session(parent):
    async def go():
        r = parent.connect()
        app = ScreenlabApp(r, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            funnel = app.query_one("#sb-funnel", DataTable)
            await until(lambda: funnel.row_count >= 1, pilot=pilot, what="remote preview")
            inp = app.query_one("#sb-i-expr", Input)
            inp.focus()
            inp.value = "pe < 15"
            await pilot.press("enter")
            await until(lambda: parent.session.analyses["screen_form"]["include"] == ["pe < 15"], pilot=pilot, what="form in the parent")
            await until(lambda: funnel.row_count >= 2, pilot=pilot, what="include row")
            # another client edits the shared form: this tab follows
            other = parent.connect()
            other.command("set_screen_form", {"name": "from_other"})
            await until(lambda: app.query_one("#sb-name", Input).value == "from_other", pilot=pilot, what="shared form")
    arun(go())


# ------------------------------------------------------------------ SB3: stages in the form
def staged_form():
    """frugal_dividend by hand: payers among the 300 largest -> 50 lowest 260-day vol -> 10 lowest P/E."""
    f = S.new_form("synthetic")
    S.update_form(f, {"name": "frugal", "n": 10, "top": 300, "include": ["dividend > 0"], "rank": [{"col": "vol_260d", "dir": "asc"}]})
    S.update_form(f, add_stage=True)
    S.update_form(f, {"keep": 50}, stage=0)
    S.update_form(f, add_row={"table": "rank", "row": {"col": "pe", "dir": "asc"}, "stage": 1})
    return f


def test_stage_ops_build_a_multi_stage_spec():
    f = staged_form()
    assert len(f["stages"]) == 2 and f["current"] == 1 and f["stages"][0]["keep"] == 50 and f["stages"][1]["keep"] is None
    assert f["include"] == ["dividend > 0"] and f["top"] == 300 and f["rank"] == [{"col": "vol_260d", "dir": "asc"}]     # one-stage keys = stage 0
    spec = S.form_to_spec(f)
    assert spec == {"name": "frugal", "n": 10, "stages": [
        {"universe": {"top": 300, "by": "market_cap"}, "filters": ["dividend > 0"], "rank": {"vol_260d": "asc"}, "keep": 50},
        {"rank": {"pe": "asc"}}]}
    S.update_form(f, select_stage=0)
    assert f["current"] == 0
    S.update_form(f, {"exclude": ["pe > 40"]}, stage=1)                         # tables of another stage, whole list
    S.update_form(f, set_row={"table": "exclude", "index": 0, "row": "pe > 50", "stage": 1})
    S.update_form(f, remove_row={"table": "rank", "index": 0, "stage": 0})
    assert f["stages"][1]["exclude"] == ["pe > 50"] and f["stages"][0]["rank"] == [] and f["rank"] == []
    S.update_form(f, add_stage={"index": 1})                                    # insert between: both neighbours keep a cut
    assert len(f["stages"]) == 3 and f["current"] == 1 and f["stages"][1]["keep"] == 50 and f["stages"][2]["keep"] is None
    assert S.form_to_spec(f)["stages"][2]["exclude"] == ["pe > 50"]
    S.update_form(f, remove_stage=1)
    S.update_form(f, remove_stage=1)
    assert len(f["stages"]) == 1 and f["stages"][0]["keep"] is None and "stages" not in S.form_to_spec(f)    # back to one-stage keys
    assert f["current"] == 0


def test_stage_errors_and_keep_rules():
    f = staged_form()
    for bad, msg in [({"fields": {"keep": 5}, "stage": 1}, "last stage has no keep"), ({"remove_stage": 5}, "remove_stage must be"),
                     ({"select_stage": 2}, "select_stage must be"), ({"stage": 3, "fields": {"top": 5}}, "stage must be"),
                     ({"add_row": {"table": "include", "row": "pe>1", "stage": 9}}, "stage must be"),
                     ({"add_stage": {"index": 0}}, "add_stage index"), ({"fields": {"keep": 0}, "stage": 0}, "at least 1")]:
        with pytest.raises(SessionError, match=msg):
            S.update_form(f, **bad)
    before = json.dumps(f, sort_keys=True)
    with pytest.raises(SessionError):
        S.update_form(f, {"top": 7}, add_row={"table": "rank", "row": {"col": "pe", "dir": "up"}})
    assert json.dumps(f, sort_keys=True) == before                              # a refused edit changes nothing
    S.update_form(f, remove_stage=1)
    with pytest.raises(SessionError, match="at least one stage"):
        S.update_form(f, remove_stage=0)
    for _ in range(S.MAX_STAGES - 1):
        S.update_form(f, add_stage=True)
    with pytest.raises(SessionError, match="at most"):
        S.update_form(f, add_stage=True)


def test_old_one_stage_calls_are_stage_zero_and_graham_is_shown_read_only():
    f = S.new_form("x")
    S.update_form(f, {"top": 50}, add_row={"table": "include", "row": "pe < 9"})
    assert f["stages"][0]["top"] == 50 and f["stages"][0]["include"] == ["pe < 9"] and len(f["stages"]) == 1
    spec = {"name": "g", "n": 5, "universe": {"top": 50}, "graham": {"preset": "defensive", "min_passes": 6}, "rank": {"pe": "asc"}}
    g = S.spec_to_form(spec)
    assert g["stages"][0]["graham"] == spec["graham"] and g["extra"] == {"graham": spec["graham"]}
    assert S.graham_line(g["stages"][0]["graham"]) == "Graham tests: preset defensive, min passes 6"
    S.update_form(g, {"top": 60}, add_stage=True)
    assert S.form_to_spec(g)["stages"][0]["graham"] == spec["graham"]            # untouched by edits
    assert S.graham_line(None) == "" and "preset enterprising" in S.graham_line("enterprising")
    text = S.view_text({"screen_form": g}, lambda cols, rows: "")
    assert "Graham tests: preset defensive, min passes 6" in text and "Stage 1: top 60" in text
    old = {k: v for k, v in S.new_form("x").items() if k not in ("stages", "current", "settings")}      # a form of the older layout
    assert S.form_to_spec(old) == S.form_to_spec(S.new_form("x"))


def test_a_later_stage_keeps_its_own_universe():
    spec = {"name": "u", "n": 3, "stages": [{"universe": {"top": 100}, "rank": {"pe": "asc"}, "keep": 30},
                                           {"universe": {"include_sectors": ["Ind"]}, "rank": {"pb": "asc"}}]}
    f = S.spec_to_form(spec)
    assert not S.has_universe(S.new_stage()) and S.has_universe(f["stages"][1]) and f["stages"][1]["include_sectors"] == ["Ind"]
    assert S.form_to_spec(f)["stages"][1]["universe"] == {"include_sectors": ["Ind"]}


def test_funnel_rows_group_by_stage():
    f = staged_form()
    funnel = [{"step": "stage 1: universe: top 300 by market_cap"}, {"step": "stage 1: rank by vol_260d asc"},
              {"step": "stage 2: rank by pe asc"}, {"step": "take 10"}]
    rows = S.group_funnel(funnel, f)
    assert [r[0] for r in rows] == ["header", "step", "step", "header", "step", "step"]
    assert rows[0][1] == "Stage 1: top 300, 1 include, rank vol_260d asc, keep 50"
    assert rows[3][1] == "Stage 2: rank pe asc, take n=10" and rows[1][1] == "universe: top 300 by market_cap" and rows[5][1] == "take 10"
    assert all(r[0] == "step" for r in S.group_funnel([{"step": "take 10"}, {"step": "include: pe < 9"}], S.new_form()))


def test_session_stage_commands_over_the_form(reg, ops):
    s = Session(reg)
    s.command("screen_load", {"name": "frugal_dividend"})
    f = s.analyses["screen_form"]
    assert [st["keep"] for st in f["stages"]] == [50, None] and f["current"] == 0
    r = s.command("set_screen_form", {"stage": 1, "add_row": {"table": "exclude", "row": "pe > 40"}, "select_stage": 1})
    assert r["spec"]["stages"][1]["exclude"] == ["pe > 40"] and r["form"]["current"] == 1
    r = s.command("set_screen_form", {"add_stage": True, "keep": 20, "stage": 1})
    assert len(r["spec"]["stages"]) == 3 and r["spec"]["stages"][1]["keep"] == 20
    pv = s.command("screen_preview", {})
    assert pv["ok"] and pv["stages"] == 3 and any(x["step"].startswith("stage 2") for x in pv["funnel"])
    assert "Stage 2: " in s.command("view", {"tab": "screen"})["view"]
    r = s.command("set_screen_form", {"remove_stage": 2})
    assert len(r["spec"]["stages"]) == 2 and r["spec"] == S.form_to_spec(s.analyses["screen_form"]) and "keep" not in r["spec"]["stages"][1]
    s.close()


# ------------------------------------------------------------------ SB3: every built-in spec screen round-trips
def spec_screen_names() -> list:
    from screenlab.screens import REGISTRY
    return [n for n, sc in REGISTRY.items() if sc.fn.__name__ == "rule_screen"]


def file_spec(name: str) -> dict:
    from screenlab.screens import spec_files
    for f in spec_files():
        d = json.loads(f.read_text())
        if d["name"] == name:
            return d
    raise KeyError(name)


def normalise(spec: dict) -> dict:
    """The spec with the defaults written out the way the form writes them (`by` next to `top`, `sectors` as include_sectors)
    and empty containers dropped; rank and derive keep their order (compared as item lists)."""
    def stage(d):
        d = {k: v for k, v in d.items() if v not in (None, {}, [])}
        if "universe" in d:
            u = {k: v for k, v in d["universe"].items() if v not in (None, {}, [])}
            if "sectors" in u:
                u["include_sectors"] = [*u.pop("sectors"), *u.get("include_sectors", [])]
            if u.get("top"):
                u.setdefault("by", "market_cap")
            d["universe"] = u
        for k in ("rank", "derive"):
            if k in d:
                d[k] = list(d[k].items())
        return d
    out = stage(spec)
    if "stages" in out:
        out["stages"] = [stage(x) for x in out["stages"]]
    return out


@pytest.fixture(scope="module")
def syn_lab():
    from screenlab import Lab
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def _dates(lab, k=3):
    idx = lab.market.index
    return [idx[int(len(idx) * q)] for q in (0.4, 0.7, 0.98)][:k]


@pytest.mark.parametrize("name", spec_screen_names())
def test_every_builtin_round_trips_through_the_form(name, syn_lab, reg, ops):
    from screenlab.spec import ScreenSpec
    s = Session(reg)
    loaded = s.command("screen_load", {"name": name})
    form = s.analyses["screen_form"]
    assert form["name"] == name and form["loaded_name"] == name
    spec = S.form_to_spec(form)
    assert json.loads(json.dumps(spec)) == spec
    orig = file_spec(name)
    assert normalise(spec) == normalise(orig)                                   # (a) canonical JSON equals the file after defaults
    assert normalise(spec) == normalise(loaded["spec"])
    assert normalise(S.form_to_spec(S.spec_to_form(spec))) == normalise(spec)    # and again through the pure functions
    built, original = ScreenSpec.from_dict(spec).build(), syn_lab.screens[name]
    ranked_seen = 0
    for d in _dates(syn_lab):                                                   # (b) identical picks and ranking at three dates
        snap = syn_lab.snapshot(d)
        assert built(snap) == original(snap), (name, d)
        assert built.ranked(snap) == original.ranked(snap), (name, d)
        ranked_seen += len(original.ranked(snap))
    assert ranked_seen > 0 or name == "bluechip_dividend_growth"                # the synthetic data gives every other screen names
    s.close()


@pytest.mark.parametrize("name", spec_screen_names())
def test_every_builtin_round_trips_on_the_real_sp500_panel(name):
    from screenlab import Lab
    from screenlab.realdata import sp500_panel as sp
    from screenlab.spec import ScreenSpec
    if not sp.available():
        pytest.skip("S&P 500 panel absent (make data-panel)")
    from screenlab.screens import REGISTRY
    lab = _real_lab()
    original = REGISTRY[name]                      # the built-in itself (the panel lab registers its own price-only variants under some names)
    built = ScreenSpec.from_dict(S.form_to_spec(S.spec_to_form(file_spec(name)))).build()
    ran = 0
    for d in _dates(lab):
        snap = lab.snapshot(d)
        try:
            want, want_rank = original(snap), original.ranked(snap)
        except Exception:                                                       # the panel has prices only: fundamentals screens cannot run
            continue
        assert built(snap) == want and built.ranked(snap) == want_rank
        ran += 1
    if not ran:
        pytest.skip(f"{name} needs fundamentals the price-only panel does not have")


_REAL = {}


def _real_lab():
    from screenlab import Lab
    if "lab" not in _REAL:
        _REAL["lab"] = Lab.real_sp500(start="1990-01-01", end="2022-12-31")
    return _REAL["lab"]


def test_the_round_trip_tests_cover_every_registered_spec_screen():
    from screenlab.screens import spec_files
    assert sorted(spec_screen_names()) == sorted(json.loads(f.read_text())["name"] for f in spec_files())
    assert len(spec_screen_names()) >= 12


# ------------------------------------------------------------------ SB3: settings carried with a built-in
def test_builtin_settings_follow_load_add_and_run(reg, ops):
    from screenlab.construction import HoldRule
    from screenlab.ops._request import config_overrides
    s = Session(reg)
    ld = s.command("screen_load", {"name": "bluechip_dividend_growth"})
    assert ld["form"]["settings"] == {"rebalance_months": 12, "hold_rule": {"keep_within": 2.0, "band": 0.25}}
    assert "settings carried with bluechip_dividend_growth" in s.command("view", {"tab": "screen"})["view"]
    s.command("set_screen_form", {"include_sectors": "Ind,Tech", "add_row": {"table": "exclude", "row": "pe > 60"}})   # an edit
    out = s.command("screen_add", {"run": True, "wait": True})
    p = out["scenario"]["params"]
    assert p["rebalance_months"] == 12 and p["hold_rule"] == {"keep_within": 2.0, "band": 0.25}
    assert p["screen"]["exclude"] == ["pe > 60"] and out["jobs"][0]["status"] == "done"
    sc = s.scenarios[0]
    args = s._bt_args(sc)
    assert args["config"] == {"rebalance_months": 12, "hold_rule": {"keep_within": 2.0, "band": 0.25}}
    assert config_overrides(args["config"])["hold_rule"] == HoldRule(keep_within=2.0, band=0.25)
    rep = reg.run_operation("backtest", args, lab="synthetic")
    assert rep["config"]["rebalance_months"] == 12
    # a monthly Globe portfolio: the run's report row rebalances every month, not the lab's default
    s.command("screen_load", {"name": "stable_dividend"})
    assert s.analyses["screen_form"]["settings"] == {"rebalance_months": 1}
    o2 = s.command("screen_add", {"run": True, "wait": True})
    assert o2["scenario"]["params"]["rebalance_months"] == 1 and "hold_rule" not in o2["scenario"]["params"]
    assert reg.run_operation("backtest", s._bt_args(s.scenarios[1]), lab="synthetic")["config"]["rebalance_months"] == 1
    # the same numbers as the registry's own portfolio
    from screenlab.exit_policy import Portfolio
    lab = reg.get("synthetic")
    want = lab.backtest(screens=[Portfolio("stable_dividend", "stable_dividend", rebalance_months=1)])
    got = s.scenarios[1].result["metrics"]
    assert got["CAGR"] == pytest.approx(float(want.table.loc["stable_dividend", "CAGR"]))
    s.close()


def test_builtin_settings_survive_save_under_a_new_name_and_edits_in_the_editor(reg, ops):
    s = Session(reg)
    s.command("screen_load", {"name": "bluechip_dividend_growth"})
    s.command("set_screen_form", {"name": "my_bluechip", "n": 8})
    out = s.command("screen_add", {"save": True, "run": False})
    assert out["saved"]["name"] == "my_bluechip" and (ops / "my_bluechip.json").exists()
    p = out["scenario"]["params"]
    assert p["screen"]["name"] == "my_bluechip" and p["screen"]["n"] == 8
    assert p["rebalance_months"] == 12 and p["hold_rule"] == {"keep_within": 2.0, "band": 0.25}
    assert s.analyses["screen_form"]["settings"]                                  # still there for the next scenario
    out = s.command("screen_add", {"run": False, "rebalance_months": 6})           # an explicit value wins
    assert out["scenario"]["params"]["rebalance_months"] == 6 and out["scenario"]["params"]["hold_rule"]["band"] == 0.25
    s.command("screen_load", {"name": "dogs"})                                     # a built-in without settings clears them
    assert s.analyses["screen_form"]["settings"] == {}
    assert "hold_rule" not in s.command("screen_add", {"run": False})["scenario"]["params"]
    s.command("set_screen_form", {"reset": True})
    assert s.analyses["screen_form"]["settings"] == {}
    s.command("set_params", {"scenario": s.scenarios[0].id, "params": {"hold_rule": {"keep_within": 3, "band": 0.1}}})
    with pytest.raises(SessionError):
        s.command("set_params", {"scenario": s.scenarios[0].id, "params": {"hold_rule": {"keep_within": 0.5}}})
    s.close()


# ------------------------------------------------------------------ SB3: starting points and the Load dialog
def test_screen_groups_and_starting_points(reg, ops):
    s = Session(reg)
    s.command("set_screen_form", {"name": "mine", "add_row": {"table": "include", "row": "pe < 15"}})
    s.command("screen_save", {})
    res = s.command("screen_load", {})
    groups = [(g["title"], g["screens"]) for g in res["groups"]]
    assert [t for t, _ in groups] == ["Globe and Mail", "Classic", "Your saved screens"]
    from screenlab.screens import REGISTRY, SPEC_DIR
    folder = {json.loads(f.read_text())["name"]: f.parent.name for f in SPEC_DIR.glob("*/*.json")}
    assert groups[0][1] == [n for n in REGISTRY if folder.get(n) == "globe"] and groups[0][1][:2] == ["screaming_value", "stable_dividend"]
    assert groups[1][1] == [n for n in REGISTRY if folder.get(n) == "classic"] and groups[1][1][0] == "dogs" and groups[2][1] == ["mine"]
    assert sorted(res["screens"]) == sorted(n for _, ns in groups for n in ns)
    line = res["starting_points"]
    L = reg.get("synthetic")
    assert line.startswith("Starting points")
    for n in groups[0][1] + groups[1][1]:
        d = S.describe(L, n)
        assert n in line and d and d in L.screens[n].note and d in line
    assert "mine" not in line
    s.close()


def test_app_load_dialog_groups_and_stage_bar(reg, ops):
    from screenlab.tui.ui import AddScreen

    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            await until(lambda: "Starting points" in plain(app.query_one("#sb-start").render()), pilot=pilot, what="starting points line")
            start = plain(app.query_one("#sb-start").render())
            assert "frugal_dividend (payers, 50 lowest 260-day vol, 10 lowest P/E)" in start and "dogs" in start
            # one stage: only "+ Add stage" in the bar, no keep box, universe block shown
            assert not app.query_one("#sb-st-0", Button).display and app.query_one("#sb-st-add", Button).display
            assert not app.query_one("#sb-st-del", Button).display and not app.query_one("#sb-keep-box").display
            assert app.query_one("#sb-uni-a").display and not app.query_one("#sb-stage-title").display
            app.query_one("#sb-load", Button).press()
            await until(lambda: isinstance(app.screen, AddScreen), pilot=pilot, what="load dialog")
            ol = app.screen.query_one(OptionList)
            labels = [plain(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
            heads = [x for x in labels if x.startswith("--")]
            # LB: the dialog now has the status groups (all 26 available on the synthetic lab); Globe and Mail first, then Classic
            assert heads == ["-- Available on this dataset --"] and labels[0] == heads[0]
            groups = {g["title"]: g["screens"] for g in s.command("screen_load", {})["groups"]}
            n_globe = len(groups["Globe and Mail"])
            assert labels[1:n_globe + 1] == groups["Globe and Mail"] and labels[n_globe + 1:] == groups["Classic"]
            assert ol.get_option_at_index(0).disabled
            await pick_option(pilot, ol, "frugal_dividend")
            await until(lambda: s.analyses["screen_form"]["loaded_name"] == "frugal_dividend" and len(s.analyses["screen_form"]["stages"]) == 2,
                        pilot=pilot, what="frugal loaded")
            await settle(pilot, app)
            # two stages: the bar shows both, stage 1 is current with its universe, keep box and tables
            await until(lambda: app.query_one("#sb-st-1", Button).display, pilot=pilot, what="stage bar")
            assert app.query_one("#sb-st-0", Button).variant == "primary" and app.query_one("#sb-st-del", Button).display
            assert app.query_one("#sb-keep-box").display and app.query_one("#sb-keep", Input).value == "50"
            assert app.query_one("#sb-top", Input).value == "300"
            assert [plain(c) for c in app.query_one("#sb-t-rank", DataTable).get_row_at(0)] == ["1", "vol_260d", "asc"]
            funnel = app.query_one("#sb-funnel", DataTable)
            await until(lambda: any(plain(funnel.get_row_at(i)[0]).startswith("Stage 2: rank pe asc") for i in range(funnel.row_count)),
                        pilot=pilot, what="grouped funnel")
            assert plain(funnel.get_row_at(0)[0]).startswith("Stage 1: top 300")
            app.query_one("#sb-st-1", Button).press()
            await until(lambda: s.analyses["screen_form"]["current"] == 1 and not app.query_one("#sb-uni-a").display, pilot=pilot, what="stage 2")
            assert not app.query_one("#sb-keep-box").display and app.query_one("#sb-stage-title").display
            assert [plain(c) for c in app.query_one("#sb-t-rank", DataTable).get_row_at(0)] == ["1", "pe", "asc"]
            inp = app.query_one("#sb-e-expr", Input)
            inp.focus()
            inp.value = "pe > 40"
            await pilot.press("enter")
            await until(lambda: s.analyses["screen_form"]["stages"][1]["exclude"] == ["pe > 40"], pilot=pilot, what="exclude in stage 2")
            assert s.analyses["screen_form"]["stages"][0]["exclude"] == []
            app.query_one("#sb-st-add", Button).press()
            await until(lambda: len(s.analyses["screen_form"]["stages"]) == 3 and s.analyses["screen_form"]["current"] == 2, pilot=pilot, what="stage 3")
            await until(lambda: app.query_one("#sb-st-2", Button).display and app.query_one("#sb-keep-box").display is False, pilot=pilot, what="bar of 3")
            app.query_one("#sb-st-del", Button).press()
            await until(lambda: len(s.analyses["screen_form"]["stages"]) == 2, pilot=pilot, what="stage removed")
            assert s.analyses["screen_form"]["stages"][1]["keep"] is None
        s.close()
    arun(go())


def test_graham_line_in_the_pane_for_a_loaded_graham_screen(reg, ops):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            s.command("screen_preview", {"spec": {"name": "g", "n": 5, "universe": {"top": 100}, "graham": {"preset": "defensive", "min_passes": 6}}})
            f = S.spec_to_form({"name": "g", "n": 5, "universe": {"top": 100}, "graham": {"preset": "defensive", "min_passes": 6}}, s.analyses["screen_form"])
            s.analyses["screen_form"].clear()
            s.analyses["screen_form"].update(f)
            s._emit("screen")
            await until(lambda: app.query_one("#sb-graham").display, pilot=pilot, what="graham line")
            assert plain(app.query_one("#sb-graham").render()) == "Graham tests: preset defensive, min passes 6"
        s.close()
    arun(go())


# ------------------------------------------------------------------ LB: the screen library in the TUI
def _rows():
    mk = lambda n, g, miss=(), ok=None, run=True: {"name": n, "n": 10, "note": "", "params": {}, "group": g, "missing": list(miss),
                                                    "available": not miss if ok is None else ok, "runnable": run}
    return [mk("dogs", "Classic", ["market_cap", "div_yield", "price", "sales", "eps", "pe"]), mk("screaming_value", "Globe and Mail"),
            mk("low_vol", "Classic"), mk("momentum_6m", "Globe and Mail"), mk("net_nets", "Classic", ["eps", "ncav_to_mcap"]),
            mk("mine_ok", "Mine"), mk("mine_no", "Mine", ["pe"])]


def test_pure_needs_text_status_groups_and_hint():
    assert S.needs_text([]) == "" and S.needs_text(["a", "b"]) == "needs: a, b"
    assert S.needs_text(list("abcde")) == "needs: a, b, c, d +1 more" and S.needs_text(list("abcdefg")) == "needs: a, b, c, d +3 more"
    groups, dim = S.status_groups(_rows())
    assert groups == [("Available on this dataset", ["screaming_value", "momentum_6m", "low_vol"]),
                      ("Needs data this dataset lacks", ["dogs", "net_nets"]), ("Your saved screens", ["mine_ok", "mine_no"])]
    assert dim == {"dogs": "needs: market_cap, div_yield, price, sales +2 more", "net_nets": "needs: eps, ncav_to_mcap", "mine_no": "needs: pe"}
    g2, d2 = S.status_groups(_rows(), only={"dogs", "low_vol"})
    assert g2 == [("Available on this dataset", ["low_vol"]), ("Needs data this dataset lacks", ["dogs"])] and list(d2) == ["dogs"]
    odd = [{"name": "x", "group": "Classic", "available": True, "missing": [], "runnable": False}]
    assert S.status_groups(odd) == ([("Needs data this dataset lacks", ["x"])], {"x": S.UNCOMPUTABLE})
    assert S.first_run_hint(_rows()) == ""                                       # 3 of 7 runnable... not under half: 2*4 < 7 is false
    few = _rows()[:1] * 3 + _rows()[1:2]
    assert S.first_run_hint(few) == "This dataset can run 1 of 4 screens; the others are listed dimmed with what they need."
    assert S.first_run_hint([]) == ""


def test_pure_named_rows_uses_line_and_funnel_labels():
    ok = {"name": "shy", "group": "Starter columns", "kind": "number", "dtype": "float64", "coverage": 1.0, "min": 0.0, "p50": 0.02,
          "max": 0.1, "description": "shareholder yield", "expr": "coalesce(div_yield, 0)", "missing": []}
    cells, dimmed = S.named_row(ok)
    assert cells == ("shy", "Starter columns", "float64", "100", "0 .. 0.1", "shareholder yield  = coalesce(div_yield, 0)") and not dimmed
    cells, dimmed = S.named_row({**ok, "missing": ["buyback_yield", "sales"], "coverage": 0.0})
    assert dimmed and cells[3] == "" and cells[5].startswith("needs: buyback_yield, sales  shareholder yield")
    assert S.named_names({"named": [ok, {**ok, "name": "z", "missing": ["x"]}]}) == ["shy"]
    assert S.uses_line([]) == ""
    ln = S.uses_line([{"name": "shy", "expr": "coalesce(div_yield, 0) + coalesce(buyback_yield, 0) + coalesce(zzzzzz, 0)"}, {"name": "p", "expr": "a/b"}])
    assert ln.startswith("Uses your columns: shy = coalesce(div_yield, 0) + coalesce(buyback_yield, 0)") and "..." in ln and ln.endswith("; p = a/b")
    fun = [{"step": "universe: top 5 by market_cap", "kind": "universe"},
           {"step": "derive shy", "kind": "derive", "expr": "x", "named": "Starter columns"},
           {"step": "derive x", "kind": "derive", "expr": "y", "named": "My columns"}, {"step": "derive own", "kind": "derive", "expr": "z"},
           {"step": "include: shy > 0", "kind": "include"}]
    assert [t for _, t, _ in S.group_funnel(fun)] == ["universe: top 5 by market_cap", "Starter column shy", "My column x", "derive own", "include: shy > 0"]


@pytest.fixture
def breg():
    """The bundled S&P 500 sample (prices only: 6 of the 26 screens can run)."""
    pytest.importorskip("skfolio")
    from screenlab.realdata.bundled import sp500_lab
    from screenlab.server.registry import LabRegistry
    r = LabRegistry({"sp500": sp500_lab}, default="sp500", mode="thread")
    yield r
    if r._jm is not None:
        r._jm.shutdown(wait=False)


async def _dialog(pilot, app):
    from screenlab.tui.ui import AddScreen
    await until(lambda: isinstance(app.screen, AddScreen) and app.screen.query(OptionList), pilot=pilot, what="dialog list")
    return app.screen.query_one(OptionList)


def _labels(ol):
    return [plain(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]


def test_session_screen_status_and_column_commands(reg, ops):
    assert {"screen_status", "column_define", "column_delete"} <= set(COMMANDS)
    s = Session(reg)
    rows = s.command("screen_status")["screens"]
    assert len(rows) == 26 and all(r["available"] and r["runnable"] for r in rows) and {"name", "missing", "group"} <= set(rows[0])
    d = s.command("column_define", {"name": "dy2", "expr": "coalesce(div_yield, 0) * 2", "note": "twice the yield"})
    assert d["group"] == "My columns" and d["replaced"] is False
    with pytest.raises(SessionError) as e:
        s.command("column_define", {"name": "dy2", "expr": "div_yield"})
    assert e.value.status == 409 and "overwrite" in e.value.message
    assert s.command("column_define", {"name": "dy2", "expr": "div_yield", "overwrite": True})["replaced"] is True
    with pytest.raises(SessionError) as e:
        s.command("column_define", {"name": "bad name", "expr": "div_yield"})
    assert e.value.status == 400
    assert s.command("column_delete", {"name": "dy2"})["deleted"] is True
    with pytest.raises(SessionError) as e:
        s.command("column_delete", {"name": "shy"})
    assert e.value.status == 409 and "starter" in e.value.message
    with pytest.raises(SessionError) as e:
        s.command("column_delete", {"name": "dy2"})
    assert e.value.status == 404
    assert "dogs" in s.command("screen_load", {})["loadable"]
    s.close()


def test_remote_session_parity_for_screen_status_and_columns(parent, ops):
    r = parent.connect()
    assert r.command("screen_status") == parent.session.command("screen_status")
    d = r.command("column_define", {"name": "rdy", "expr": "coalesce(div_yield, 0)", "note": "n"})
    assert d["name"] == "rdy" and any(c["name"] == "rdy" for c in r.command("screen_schema", {})["named"])
    with pytest.raises(SessionError) as e:
        r.command("column_define", {"name": "rdy", "expr": "div_yield"})
    assert e.value.status == 409
    assert r.command("column_delete", {"name": "rdy"})["deleted"] is True
    with pytest.raises(SessionError) as e:
        r.command("column_delete", {"name": "shy"})
    assert e.value.status == 409


def test_add_dialog_on_the_bundled_sample_shows_six_available_and_twenty_dimmed(breg, ops):
    from screenlab.tui.ui import AddScreen

    async def go():
        s = Session(breg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await settle(pilot, app)
            app.action_add()
            ol = await _dialog(pilot, app)
            opts = [ol.get_option_at_index(i) for i in range(ol.option_count)]
            heads = [plain(o.prompt) for o in opts if str(o.id).startswith("head:")]
            assert heads == ["-- Available on this dataset --", "-- Needs data this dataset lacks --"]
            assert [o.id for o in opts if not o.disabled] == ["momentum_6m", "momentum_12m", "momentum_12_1", "low_vol", "high_52w", "trend_momentum"]
            dimmed = [o for o in opts if o.disabled and not str(o.id).startswith("head:")]
            assert len(dimmed) == 20 and len(opts) == 28
            lab = {r["name"]: r for r in s.command("screen_status")["screens"]}
            for o in dimmed:
                want = "needs: " + ", ".join(lab[o.id]["missing"][:4]) + (f" +{len(lab[o.id]['missing']) - 4} more" if len(lab[o.id]["missing"]) > 4 else "")
                assert plain(o.prompt) == f"{o.id}   {want}"
            assert plain(dimmed[0].prompt) == "dogs   needs: market_cap, div_yield"
            assert "make data-free" in plain(app.screen.query_one("#screen-footer").render()) and "synthetic" in plain(app.screen.query_one("#screen-footer").render())
            assert ol.highlighted == 1 and not ol.get_option_at_index(ol.highlighted).disabled
            await pilot.press("escape")
            await until(lambda: not isinstance(app.screen, AddScreen), pilot=pilot, what="closed")
            assert not s.scenarios
            # the Screen tab: Load lists loadable screens, the same groups, and the first-run hint is on the help line
            await pilot.press("0")
            await settle(pilot, app)
            await until(lambda: "can run 6 of 26 screens" in plain(app.query_one("#sb-help").render()), pilot=pilot, what="first-run hint")
            assert "the others are listed dimmed with what they need" in plain(app.query_one("#sb-help").render())
            app.query_one("#sb-load", Button).press()
            lab_ = _labels(await _dialog(pilot, app))
            assert lab_[0] == "-- Available on this dataset --" and "-- Needs data this dataset lacks --" in lab_
            assert "dogs   needs: market_cap, div_yield" in lab_ and "momentum_6m" not in lab_ and lab_[1:3] == ["high_52w", "trend_momentum"]   # function screens do not load
            await pilot.press("escape")
        s.close()
    arun(go())


def test_run_on_a_screen_the_dataset_cannot_run_says_what_it_needs(breg, ops):
    async def go():
        s = Session(breg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            await until(lambda: s.analyses["screen_form"]["stages"][0].get("top") is None, pilot=pilot, what="default size cut cleared")
            s.command("set_screen_form", {"top": 100})                   # a user who types a size cut the data cannot do
            await settle(pilot, app)
            for b in ("#sb-run", "#sb-add"):
                app.query_one(b, Button).press()
                await until(lambda: "cannot run on this dataset" in plain(app.query_one("#sb-status").render()), pilot=pilot, what="message")
                msg = plain(app.query_one("#sb-status").render())
                assert "needs: market_cap" in msg and "Traceback" not in msg and "Clear the Top N" in msg and "make data-free" in msg
                app.query_one("#sb-status").update("")
                app._screen_pane._msg = None
            assert not s.scenarios and not (ops / "my_screen.json").exists()
            # without the size cut (a price-only spec) it runs
            s.command("set_screen_form", {"top": None, "add_row": {"table": "include", "row": "mom_6m > 0"}})
            app.query_one("#sb-add", Button).press()
            await until(lambda: s.scenarios, pilot=pilot, what="scenario added")
        s.close()
    arun(go())


def test_add_dialog_on_the_synthetic_lab_has_every_screen_available(reg, ops):
    from screenlab.tui.ui import AddScreen

    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await settle(pilot, app)
            app.action_add()
            ol = await _dialog(pilot, app)
            opts = [ol.get_option_at_index(i) for i in range(ol.option_count)]
            assert [plain(o.prompt) for o in opts if o.disabled] == ["-- Available on this dataset --"] and len(opts) == 27
            assert not app.screen.dim and not app.screen.query("#screen-footer")
            await pick_option(pilot, ol, "dogs")
            await until(lambda: [x.label for x in s.scenarios] == ["dogs"], pilot=pilot, what="dogs added")
            await pilot.press("0")
            await settle(pilot, app)
            assert "can run" not in plain(app.query_one("#sb-help").render())
        s.close()
    arun(go())


def test_save_row_as_my_column_use_it_overwrite_and_delete(reg, ops):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 80)) as pilot:
            await open_tab(pilot, app)
            pane = app._screen_pane
            say = lambda: plain(app.query_one("#sb-status").render())
            schema = app.query_one("#sb-schema", DataTable)
            await until(lambda: "shy" in pane._schema_rows, pilot=pilot, what="starter rows in the schema panel")
            i = pane._schema_rows.index("shy")
            assert pane._schema_meta[i]["group"] == "Starter columns" and plain(schema.get_row_at(i)[1]) == "Starter columns"
            assert "coalesce(div_yield, 0)" in plain(schema.get_row_at(i)[5]) and "pe" in pane._schema_rows[:i]       # snapshot columns first
            assert not app.query_one("#sb-colbox").display and not app.query_one("#sb-uses").display

            # a Derived row, then Save as my column
            s.command("set_screen_form", {"add_row": {"table": "derive", "row": {"name": "dy2", "expr": "coalesce(div_yield, 0) * 2"}}})
            t = app.query_one("#sb-t-derive", DataTable)
            await until(lambda: t.row_count == 1, pilot=pilot, what="derived row")
            app.query_one("#sb-d-save", Button).press()
            await until(lambda: app.query_one("#sb-colbox").display and app.focused is app.query_one("#sb-col-note"), pilot=pilot, what="note box")
            assert not app.query_one("#sb-col-over", Button).display
            app.query_one("#sb-col-note", Input).value = "twice the yield"
            await pilot.press("enter")
            await until(lambda: "dy2" in pane._schema_rows, pilot=pilot, what="my column in the schema panel")
            assert "saved my column 'dy2'" in say() and not app.query_one("#sb-colbox").display
            j = pane._schema_rows.index("dy2")
            assert pane._schema_meta[j]["group"] == "My columns" and "twice the yield" in plain(schema.get_row_at(j)[5])
            assert s.analyses["screen_form"]["derive"] == [{"name": "dy2", "expr": "coalesce(div_yield, 0) * 2"}]     # the form is untouched

            # use it in an include row: previewed with `uses`, labelled in the funnel, not flagged as unknown
            s.command("set_screen_form", {"derive": [], "add_row": {"table": "include", "row": "dy2 > 0.01 and shy > 0"}})
            ft = app.query_one("#sb-funnel", DataTable)
            await until(lambda: app.query_one("#sb-uses").display, pilot=pilot, what="uses line")
            uses = plain(app.query_one("#sb-uses").render())
            assert uses.startswith("Uses your columns: dy2 = coalesce(div_yield, 0) * 2") and "shy = coalesce(div_yield, 0)" in uses
            pv = s.analyses["screen_preview"]
            assert pv["ok"] and [u["name"] for u in pv["uses"]] == ["dy2", "shy"] and not pv["errors"]
            steps = [plain(ft.get_row_at(k)[0]) for k in range(ft.row_count)]
            assert "My column dy2" in steps and "Starter column shy" in steps

            # a second save under the same name: 409, Overwrite appears, then replaces
            s.command("set_screen_form", {"include": [], "add_row": {"table": "derive", "row": {"name": "dy2", "expr": "coalesce(div_yield, 0) * 3"}}})
            await until(lambda: t.row_count == 1, pilot=pilot, what="derived row again")
            app.query_one("#sb-d-save", Button).press()
            await until(lambda: app.query_one("#sb-colbox").display, pilot=pilot, what="note box")
            await pilot.press("enter")
            await until(lambda: app.query_one("#sb-col-over", Button).display, pilot=pilot, what="Overwrite button")
            assert "already defined" in say() and "overwrite" in say()
            app.query_one("#sb-col-over", Button).press()
            await until(lambda: "(replaced)" in say(), pilot=pilot, what="replaced")
            assert not app.query_one("#sb-colbox").display
            assert next(c for c in s.command("screen_schema", {})["named"] if c["name"] == "dy2")["expr"] == "coalesce(div_yield, 0) * 3"

            # a bad expression: 400 in the status line, no Overwrite
            s.command("set_screen_form", {"derive": [{"name": "bad1", "expr": "nonesuch + 1"}]})
            await until(lambda: plain(t.get_row_at(0)[0]) == "bad1", pilot=pilot, what="bad row")
            app.query_one("#sb-d-save", Button).press()
            await until(lambda: app.query_one("#sb-colbox").display, pilot=pilot, what="note box")
            await pilot.press("enter")
            await until(lambda: "column_define:" in say(), pilot=pilot, what="engine message")
            assert not app.query_one("#sb-col-over", Button).display
            await pilot.press("escape")
            await until(lambda: not app.query_one("#sb-colbox").display, pilot=pilot, what="closed")

            # delete: a starter row refuses with the engine's message; a my-column row asks twice
            schema.focus()
            schema.move_cursor(row=pane._schema_rows.index("shy"))
            await pilot.press("delete")
            await until(lambda: "starter column" in say(), pilot=pilot, what="starter refused")
            assert "shy" in pane._schema_rows
            schema.move_cursor(row=pane._schema_rows.index("pe"))
            await pilot.press("delete")
            await until(lambda: "only your own columns" in say(), pilot=pilot, what="data column refused")
            schema.move_cursor(row=pane._schema_rows.index("dy2"))
            await pilot.press("delete")
            await until(lambda: say() == "press Delete again to remove 'dy2'", pilot=pilot, what="confirmation line")
            assert "dy2" in pane._schema_rows
            await pilot.press("delete")
            await until(lambda: "dy2" not in pane._schema_rows, pilot=pilot, what="deleted")
            assert "removed my column 'dy2'" in say()
        s.close()
    arun(go())


def test_cycle_error_shows_in_red_and_derive_rows_of_named_columns_in_the_view(reg, ops):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await open_tab(pilot, app)
            msg = "column 'a' uses 'b' which uses 'a' (a -> b -> a)"
            s.analyses["screen_preview"] = {"ok": False, "asof": "2009-12-31", "errors": [{"where": "spec", "message": msg, "suggestion": None}],
                                            "funnel": [], "picks": [], "uses": []}
            s._emit("screen")
            ft = app.query_one("#sb-funnel", DataTable)
            await until(lambda: ft.row_count == 1, pilot=pilot, what="error row")
            assert plain(ft.get_row_at(0)[0]) == "spec" and "red" in str(ft.get_row_at(0)[0].style)
            summ = app.query_one("#sb-summary").render()
            assert msg in plain(summ) and "red" in str(getattr(summ, "spans", ""))
        s.close()
    arun(go())
    s = Session(reg)
    s.command("screen_preview", {"spec": {"name": "v", "n": 3, "universe": {"top": 50}, "filters": ["shy > 0"]}})
    v = s.view("screen")
    assert "Uses your columns: shy = coalesce(div_yield, 0) + coalesce(buyback_yield, 0)" in v and "Starter column shy" in v
    s.close()


# ------------------------------------------------------------------ LB: price-only data must not start on a size cut it cannot run
def test_market_cap_helpers():
    assert S.has_market_cap({"columns": [{"name": "market_cap", "coverage": 0.9}]}) is True
    assert S.has_market_cap({"columns": [{"name": "market_cap", "coverage": 0.0}]}) is False
    assert S.has_market_cap({"columns": [{"name": "price", "coverage": 1.0}]}) is False
    f = S.new_form("x")
    assert S.is_default_form(f)
    S.update_form(f, {"name": "mine"}, None, None, None, False, dataset="x")
    assert not S.is_default_form(f)


def test_default_form_has_no_size_cut_on_price_only_data(breg):
    """On the bundled sample (no market cap) the untouched form starts with the size cut off, so Run works at once."""
    async def go():
        s = Session(breg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            await pilot.press("0")
            await until(lambda: (s.analyses["screen_form"]["stages"][0].get("top") is None), pilot=pilot, what="size cut cleared")
            spec = S.form_to_spec(s.analyses["screen_form"])
            assert "top" not in (spec.get("universe") or {})
            s.command("set_screen_form", {"rank": [{"col": "mom_12_1", "dir": "desc"}]})
            out = s.command("screen_add", {"name": "mom_try", "run": False})
            assert out["scenario"]["name"].startswith("mom_try")
        s.close()
    arun(go())
