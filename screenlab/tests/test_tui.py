"""Terminal UI tests (DESIGN.md L5): Session without Textual, the Textual app under `run_test`, the control endpoint
round trip, and the `--serve` smoke test.

    python -m pytest screenlab/tests/test_tui.py -q
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from screenlab import Lab
from screenlab.server.registry import LabRegistry
from screenlab.store import SQLiteStore
from screenlab.tui.session import PALETTE, PASSIVE_COLOR, Session, SessionError


def make_registry(store=None) -> LabRegistry:
    return LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)},
                       default="synthetic", mode="thread", store=store)


@pytest.fixture(scope="module")
def reg():
    r = make_registry()
    yield r
    if r._jm is not None:
        r._jm.shutdown(wait=False)


@pytest.fixture
def sess(reg):
    return Session(reg)


def run_scenario(s: Session, screen="dogs", **kw):
    return s.command("add_scenario", {"screen": screen, "run": True, "wait": True, **kw})


# ------------------------------------------------------------------ Session (no Textual)
def test_session_has_no_textual_import():
    code = "import sys; import screenlab.tui.session; assert not [m for m in sys.modules if m.startswith('textual')]"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_add_run_compare_passive_first(sess):
    r = run_scenario(sess, cost_bps=10)
    assert r["scenario"]["label"] == "dogs" and r["scenario"]["color"] == PALETTE[0]
    assert r["jobs"][0]["status"] == "done"
    c = sess.command("compare")
    assert [x["passive"] for x in c["rows"]] == [True, False]
    assert c["rows"][0]["label"] == c["benchmark"] == "SYN_CAPWT" and c["rows"][0]["color"] == PASSIVE_COLOR
    assert c["rows"][1]["values"]["CAGR"] is not None and "Excess/yr" in c["columns"]
    assert c["missing"] == []


def test_set_params_creates_version_and_keeps_colours(sess):
    run_scenario(sess, cost_bps=10)
    r = sess.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 30, "n": 15}, "run": True, "wait": True})
    assert r["scenario"]["label"] == "dogs v2" and r["previous"] == "dogs"
    assert {d["param"] for d in r["diff"]} == {"cost_bps", "n"}
    assert r["diff_line"].startswith("dogs -> dogs v2:") and "cost_bps 10.0 -> 30.0" in r["diff_line"]
    st = sess.command("snapshot")
    assert [x["label"] for x in st["scenarios"]] == ["dogs", "dogs v2"]
    assert st["selected"] == r["scenario"]["id"]
    c1, c2 = (x["color"] for x in st["scenarios"])
    assert c1 == PALETTE[0] and c2 == PALETTE[1]
    rows = sess.command("compare")["rows"]
    assert [x["label"] for x in rows] == ["SYN_CAPWT", "dogs", "dogs v2"]
    assert rows[1]["values"]["AvgN"] == 10 and rows[2]["values"]["AvgN"] == 15
    sess.command("remove_scenario", {"scenario": "dogs"})                   # colours are never reassigned
    nxt = sess.command("clone_scenario", {"scenario": "dogs v2", "name": "third"})["scenario"]
    assert nxt["color"] == PALETTE[2]
    assert [x["color"] for x in sess.command("snapshot")["scenarios"]][0] == c2


def test_command_errors(sess):
    run_scenario(sess)
    for cmd, args, status in [("nope", {}, 404), ("set_params", {"scenario": "dogs", "params": {"bogus": 1}}, 400),
                              ("set_params", {"scenario": "dogs", "params": {}}, 400),
                              ("remove_scenario", {"scenario": "passive"}, 400),
                              ("select", {"scenario": "ghost"}, 404), ("show", {"tab": "x"}, 400),
                              ("add_scenario", {"screen": "no_such_screen"}, 404), ("add_scenario", {}, 400),
                              ("set_params", {"scenario": "dogs", "params": {"n": "many"}}, 400)]:
        with pytest.raises(SessionError) as e:
            sess.command(cmd, args)
        assert e.value.status == status, (cmd, e.value.message)
    assert len(sess.scenarios) == 1                                          # nothing half-changed


def test_inline_spec_scenario_and_save_spec(sess, tmp_path):
    spec = {"name": "cheap", "n": 8, "universe": {"top": 100}, "filters": ["pe < 20"], "rank": {"div_yield": "desc"}}
    r = run_scenario(sess, screen=spec)
    assert r["jobs"][0]["status"] == "done"
    out = sess.command("save_spec", {"scenario": "cheap", "path": str(tmp_path / "cheap.json")})
    assert json.loads((tmp_path / "cheap.json").read_text())["name"] == "cheap" and out["spec"]["n"] == 8


def test_picks_timing_exit_montecarlo(sess):
    run_scenario(sess, screen="momentum_12m")
    p = sess.command("picks", {"scenario": "momentum_12m"})                  # wait defaults to true
    assert p["columns"][0] == "ticker" and len(p["rows"]) > 0
    t = sess.command("timing", {"scenario": "momentum_12m", "stride": 10, "wait": True})
    assert t["jobs"][0]["status"] == "done"
    assert "distribution" in sess.command("snapshot")["analyses"]["timing"]["momentum_12m"]
    with pytest.raises(SessionError):
        sess.command("exit_eval", {"scenario": "momentum_12m"})              # no exit policy
    ex = {"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}], "threshold": 0.8}
    sess.command("set_params", {"scenario": "momentum_12m", "params": {"exit": ex}})
    e = sess.command("exit_eval", {"wait": True})
    assert e["jobs"][0]["status"] == "done" and sess.analyses["exit"]
    mc = sess.command("montecarlo", {"n_paths": 30, "horizon_years": [3], "wait": True})
    assert mc["jobs"][0]["status"] == "done"
    assert sess.command("snapshot")["analyses"]["montecarlo"]["n_paths"] == 30
    for tab in ("timing", "picks", "exit", "montecarlo", "curves", "log", "runs", "compare"):
        sess.command("show", {"tab": tab})
        v = sess.command("view")
        assert v["tab"] == tab and "SYN_CAPWT" in v["view"]


def test_ci_run_adds_interval_columns(sess):
    run_scenario(sess, ci=True)
    c = sess.command("compare")
    assert {"CI5", "CI95"} <= set(c["columns"])


def test_failed_job_reports_error(sess):
    r = sess.command("add_scenario", {"screen": "dogs", "start": "1990-01-01", "end": "1990-02-01"})
    out = sess.command("run", {"scenario": r["scenario"]["id"], "wait": True})
    assert out["jobs"][0]["status"] == "failed" and out["errors"]
    assert sess.command("snapshot")["scenarios"][0]["status"] == "failed"


def test_store_history_export_and_rerun(tmp_path):
    store = SQLiteStore(tmp_path / "runs.sqlite")
    s = Session(make_registry(store), own=True)
    run_scenario(s)
    h = s.command("history", {})
    assert h["store"].endswith("runs.sqlite") and h["runs"][0]["kind"] == "backtest" and h["runs"][0]["screen"] == "dogs"
    assert s.command("history", {"rerun": h["runs"][0]["run_key"]})["match"] is True
    out = s.command("export", {"path": str(tmp_path / "x")})
    md = (tmp_path / "x.md").read_text()
    assert "SYN_CAPWT" in md and (tmp_path / "x.csv").read_text().startswith("scenario,passive,color")
    assert len(out["files"]) == 2
    s.close()


def test_snapshot_is_json_and_seq_advances(sess):
    s0 = sess.seq
    run_scenario(sess)
    st = sess.command("snapshot")
    json.dumps(st)
    assert st["seq"] > s0 and st["protocol"] == 1 and st["passive"]["color"] == PASSIVE_COLOR
    events = []
    sess.subscribe(lambda s, ev: events.append(ev))
    sess.command("show", {"tab": "log"})
    assert events == ["tab"]


# ------------------------------------------------------------------ Textual app
textual = pytest.importorskip("textual")
from screenlab.tui.app import ScreenlabApp                                    # noqa: E402


def arun(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_app_launch_apply_versions_and_tabs(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause(0.6)
            assert "SYN_CAPWT" in str(app.query_one("#head").render())
            s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
            await pilot.pause(0.6)
            t = app.query_one("#t-compare")
            assert [str(t.get_row_at(i)[0]) for i in range(t.row_count)] == ["● SYN_CAPWT (passive)", "● dogs"]
            app.query_one("#f-cost_bps").value = "40"                    # edit a field, then Apply (F2)
            await pilot.press("f2")
            for _ in range(40):
                await pilot.pause(0.25)
                if t.row_count == 3 and all(x.status == "done" for x in s.scenarios):
                    break
            await pilot.pause(0.4)
            assert [x.label for x in s.scenarios] == ["dogs", "dogs v2"]
            assert [str(t.get_row_at(i)[0]) for i in range(t.row_count)] == [
                "● SYN_CAPWT (passive)", "● dogs", "● dogs v2"]
            assert "cost_bps 10.0 -> 40.0" in str(app.query_one("#diff").render())
            await pilot.press("2")
            await pilot.pause(0.4)
            assert s.active_tab == "curves" and app.query_one("#main").active == "curves"
            await pilot.press("8")
            await pilot.pause(0.3)
            assert s.active_tab == "log"
            await pilot.press("question_mark")
            await pilot.pause(0.2)
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
        s.close()
    arun(go())


def test_app_toggle_exit_switch_and_theme(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None, theme="textual-light")
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause(0.5)
            assert app.theme == "textual-light"
            s.command("add_scenario", {"screen": "dogs"})
            await pilot.pause(0.5)
            await pilot.press("x")
            assert app.query_one("#f-exit").value is True
            await pilot.press("T")
            await pilot.pause(0.5)
            assert app.theme == "textual-dark" and s.theme == "textual-dark"
        s.close()
    arun(go())


# ------------------------------------------------------------------ control endpoint
def http(port, method, path, body=None, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_control_endpoint_round_trip(reg, monkeypatch):
    monkeypatch.setenv("SCREENLAB_TUI_TOKEN", "sekret")

    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=0)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause(0.5)
            port = app._control.port
            call = lambda *a, **k: asyncio.to_thread(http, port, *a, **k)
            code, h = await call("GET", "/health")                       # no token needed
            assert code == 200 and h["protocol"] == 1 and "SYN_CAPWT" in h["dataset"]
            assert (await call("GET", "/state"))[0] == 401
            assert (await call("POST", "/command", {"cmd": "snapshot"}))[0] == 401
            code, r = await call("POST", "/command", {"cmd": "add_scenario", "args": {
                "screen": "dogs", "run": True, "wait": True, "cost_bps": 10}}, token="sekret")
            assert code == 200 and r["jobs"][0]["status"] == "done", r
            code, r = await call("POST", "/command", {"cmd": "set_params", "args": {
                "scenario": "dogs", "params": {"cost_bps": 30}, "run": True, "wait": True}}, token="sekret")
            assert code == 200 and r["scenario"]["label"] == "dogs v2"
            code, st = await call("GET", "/state", token="sekret")
            assert [x["label"] for x in st["scenarios"]] == ["dogs", "dogs v2"] and st["scenarios"][1]["status"] == "done"
            code, c = await call("POST", "/command", {"cmd": "compare"}, token="sekret")
            assert [x["label"] for x in c["rows"]] == ["SYN_CAPWT", "dogs", "dogs v2"]
            await call("POST", "/command", {"cmd": "show", "args": {"tab": "curves"}}, token="sekret")
            await pilot.pause(0.5)
            assert app.query_one("#main").active == "curves"             # the UI follows the command
            code, v = await call("GET", "/view", token="sekret")
            assert v["tab"] == "curves" and "dogs v2" in v["view"]
            code, e = await call("POST", "/command", {"cmd": "nope"}, token="sekret")
            assert code == 404 and "unknown command" in e["error"]
            code, e = await call("POST", "/command", {"cmd": "set_params", "args": {"scenario": "ghost", "params": {"n": 5}}}, token="sekret")
            assert code == 404
            code, e = await call("POST", "/command", {"cmd": "add_scenario", "args": {}}, token="sekret")
            assert code == 400 and "error" in e
            assert (await call("GET", "/nothing", token="sekret"))[0] == 404
        assert app._control._httpd is None                               # stopped on exit
        s.close()
    arun(go())


# ------------------------------------------------------------------ --serve smoke
def test_serve_smoke():
    pytest.importorskip("textual_serve")
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    p = subprocess.Popen([sys.executable, "-m", "screenlab.tui.app", "--serve", "--host", "127.0.0.1", "--port",
                          str(port), "--lab", "synthetic", "--store", "none", "--control-port", "0"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        body = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    body = r.read().decode()
                break
            except OSError:
                time.sleep(0.5)
        assert body is not None and "<html" in body.lower()
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
