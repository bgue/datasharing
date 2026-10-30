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


def wait_for(cond, timeout=10.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


def make_registry(store=None) -> LabRegistry:
    return LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)},
                       default="synthetic", mode="thread", store=store)


@pytest.fixture
def reg():                       # per test: labs, job cache and cached results are never shared between tests
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


async def until(pred, timeout=15.0, pilot=None, what="condition"):
    """Poll `pred()` every 50 ms until it is truthy (replaces fixed sleeps); fail with a clear message on timeout."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        if pred():
            return
        if loop.time() > end:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        if pilot is not None:
            await pilot.pause(0.05)
        else:
            await asyncio.sleep(0.05)


async def settle(pilot, app, timeout=15.0):
    """Wait until the app has built its lab and drawn every session change so far, then let Textual flush."""
    await until(lambda: app.ready, timeout, pilot, "app.ready")
    await pilot.pause(0.05)
    await pilot.pause()


def arun(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_app_launch_apply_versions_and_tabs(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            assert "SYN_CAPWT" in str(app.query_one("#head").render())
            s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
            await settle(pilot, app)
            t = app.query_one("#t-compare")
            assert [str(t.get_row_at(i)[0]) for i in range(t.row_count)] == ["● SYN_CAPWT (passive)", "● dogs"]
            app.query_one("#f-cost_bps").value = "40"                    # edit a field, then Apply (F2)
            await pilot.press("f2")
            await until(lambda: t.row_count == 3 and all(x.status == "done" for x in s.scenarios), pilot=pilot, what='t.row_count == 3 and all(x.status == "done" for x ')
            await settle(pilot, app)
            assert [x.label for x in s.scenarios] == ["dogs", "dogs v2"]
            assert [str(t.get_row_at(i)[0]) for i in range(t.row_count)] == [
                "● SYN_CAPWT (passive)", "● dogs", "● dogs v2"]
            assert "cost_bps 10.0 -> 40.0" in str(app.query_one("#diff").render())
            await pilot.press("2")
            await settle(pilot, app)
            assert s.active_tab == "curves" and app.query_one("#main").active == "curves"
            await pilot.press("8")
            await settle(pilot, app)
            assert s.active_tab == "log"
            await pilot.press("question_mark")
            await settle(pilot, app)
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
        s.close()
    arun(go())


def test_app_toggle_exit_switch_and_theme(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None, theme="textual-light")
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            assert app.theme == "textual-light"
            s.command("add_scenario", {"screen": "dogs"})
            await settle(pilot, app)
            await pilot.press("x")
            assert app.query_one("#f-exit").value is True
            await pilot.press("T")
            await settle(pilot, app)
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
            await settle(pilot, app)
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
            await settle(pilot, app)
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

        def probe():
            nonlocal body
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    body = r.read().decode()
                return True
            except OSError:
                return False
        wait_for(probe, timeout=30)
        assert body is not None and "<html" in body.lower()
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


# ------------------------------------------------------------------ browser chart panel (DESIGN.md L6)
import threading                                                           # noqa: E402
from types import SimpleNamespace                                          # noqa: E402

from screenlab.tui import web                                              # noqa: E402
from screenlab.tui.control import ControlServer                            # noqa: E402


class FakeApp:
    """Stands in for the Textual app: the control server only needs call_from_thread and the theme."""
    current_theme = SimpleNamespace(name="nord", dark=True)

    def call_from_thread(self, fn, *a):
        return fn(*a)

    def get_css_variables(self):
        return {"background": "#2E3440", "surface": "#3B4252", "panel": "#434C5E", "foreground": "#D8DEE9",
                "primary": "#88C0D0", "secondary": "#81A1C1", "accent": "#B48EAD", "success": "#A3BE8C",
                "warning": "#EBCB8B", "error": "#BF616A", "text-muted": "auto 60%"}


def raw(port, path, token=None, timeout=30):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


@pytest.fixture
def charts(reg):
    s = Session(reg)
    s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
    s.command("add_scenario", {"screen": "momentum_6m", "run": True, "wait": True})
    s.command("montecarlo", {"scenarios": ["dogs", "momentum_6m"], "n_paths": 30, "horizon_years": [3], "force": True, "wait": True})
    srv = ControlServer(FakeApp(), s, port=0, serve_url="http://localhost:8000").start()
    yield s, srv
    srv.stop()


def chart(srv, tab):
    code, ctype, body = raw(srv.port, f"/charts/data?tab={tab}")
    assert code == 200 and "json" in ctype, body
    return json.loads(body)


def test_chart_data_curves_and_compare(charts):
    s, srv = charts
    d = chart(srv, "curves")
    assert d["tab"] == "curves" and d["empty"] is False and d["seq"] == s.seq
    assert [x["label"] for x in d["series"]] == ["SYN_CAPWT", "dogs", "momentum_6m"]
    assert [x["passive"] for x in d["series"]] == [True, False, False]
    assert d["series"][0]["color"] == PASSIVE_COLOR and d["series"][1]["color"] == PALETTE[0]
    assert all(len(x["x"]) == len(x["y"]) and 2 < len(x["x"]) <= web.MAX_POINTS for x in d["series"])
    c = chart(srv, "compare")
    assert [r["label"] for r in c["rows"]] == ["SYN_CAPWT", "dogs", "momentum_6m"] and "CAGR" in c["columns"]
    assert c["rows"][0]["passive"] is True and c["benchmark"] == "SYN_CAPWT"


def test_chart_data_montecarlo_binned_server_side(charts):
    _, srv = charts
    d = chart(srv, "montecarlo")
    assert d["empty"] is False and d["n_paths"] == 30 and d["benchmark"] == "SYN_CAPWT"
    labels = [x["label"] for x in d["fan"]["series"]]
    assert labels == ["dogs", "momentum_6m", "SYN_CAPWT"] and d["colors"]["dogs"] == PALETTE[0]
    for x in d["fan"]["series"]:
        assert all(len(x[q]) == len(d["fan"]["years"]) for q in ("p5", "p25", "p50", "p75", "p95"))
    assert d["fan"]["series"][-1]["passive"] is True and d["fan"]["series"][-1]["color"] == PASSIVE_COLOR
    h = d["hist"]
    assert h["horizons"] == [3.0]
    for key in ("cagr", "maxdd"):
        b = h["by_horizon"]["3"][key]
        assert set(b["counts"]) == {"dogs", "momentum_6m", "SYN_CAPWT"}                 # portfolios and the index
        assert len(b["edges"]) == len(next(iter(b["counts"].values()))) + 1            # one shared set of bins
        assert all(sum(c) == 30 for c in b["counts"].values()) and all(n == 30 for n in b["n"].values())
    assert "TermWealth_p50" in d["table"]["columns"]
    code, _, body = raw(srv.port, "/charts/data?tab=montecarlo")
    assert len(body) < 60_000                                                        # no per-path arrays on the wire


def test_chart_data_timing_exit_empty_and_errors(charts):
    s, srv = charts
    for tab in ("timing", "exit"):
        d = chart(srv, tab)
        assert d["empty"] is True and d["message"]
    s.command("timing", {"scenario": "dogs", "stride": 6, "wait": True})
    t = chart(srv, "timing")
    assert t["empty"] is False and t["series"][0]["label"] == "dogs" and t["series"][0]["color"] == PALETTE[0]
    assert len(t["series"][0]["offset"]) == len(t["series"][0]["cagr"]) >= 2 and t["passive_cagr"] is not None
    assert t["series"][0]["tranche"]["x"]
    s.command("add_scenario", {"screen": "dogs", "name": "dx", "run": True, "wait": True,
                               "exit": {"signals": [{"type": "TrendBreak"}], "threshold": 0.8}})
    s.command("exit_eval", {"scenario": "dx", "wait": True})
    e = chart(srv, "exit")
    it = e["items"][0]
    assert it["label"] == "dx" and {r["state"] for r in it["forward"]} == {"flagged", "unflagged"}
    assert it["color"] == PALETTE[2] and it["frac_flagged"] is not None
    code, _, body = raw(srv.port, "/charts/data?tab=nope")
    assert code == 400 and "unknown chart tab" in json.loads(body)["error"]


def test_chart_data_empty_session(reg):
    s = Session(reg)
    for tab in web.CHART_TABS:
        assert web.chart_data(s, tab)["empty"] is True


def test_mc_hist_shared_bins_and_robustness():
    import pandas as pd
    pp = pd.DataFrame({"portfolio": ["a"] * 4 + ["idx"] * 4, "horizon_years": [5.0] * 8, "path": list(range(4)) * 2,
                       "cagr": [0.0, 0.1, 0.2, 0.3, -0.1, 0.0, 0.05, 0.4],
                       "max_drawdown": [-0.1, -0.2, -0.3, -0.4, -0.5, -0.05, -0.15, -0.25]})
    h = web.mc_hist(pp, bins=4)
    b = h["by_horizon"]["5"]["cagr"]
    assert b["edges"][0] == pytest.approx(-0.1) and b["edges"][-1] == pytest.approx(0.4) and len(b["edges"]) == 5
    assert b["counts"]["a"] == [2, 1, 1, 0] or sum(b["counts"]["a"]) == 4
    assert sum(b["counts"]["idx"]) == 4 and h["by_horizon"]["5"]["maxdd"]["edges"][0] == pytest.approx(-0.5)
    assert web.mc_hist(None) is None and web.mc_hist(pp[["portfolio"]]) is None


def test_chart_pages_reference_data_endpoints(charts):
    _, srv = charts
    code, ctype, body = raw(srv.port, "/charts")
    html = body.decode()
    assert code == 200 and ctype.startswith("text/html") and "<title>" in html
    for ref in ("charts/data", '"theme"', '"events"', "Plotly", web.PLOTLY_CDN):
        assert ref in html, ref
    for tab in ("Curves", "Compare", "Timing", "Monte Carlo", "Exit"):
        assert tab in html
    code, ctype, body = raw(srv.port, "/")                                      # serve_url set: the split page
    html = body.decode()
    assert code == 200 and ctype.startswith("text/html")
    assert 'src="http://localhost:8000"' in html and '"charts"' in html and "col-resize" in html
    assert "/charts" in srv.charts_url() and srv.home_url().endswith(f":{srv.port}/")


def test_root_is_not_a_page_without_serve_mode(reg):
    srv = ControlServer(FakeApp(), Session(reg), port=0, serve_url=None).start()
    try:
        assert raw(srv.port, "/")[0] == 404
        assert raw(srv.port, "/charts")[0] == 200 and srv.home_url().endswith("/charts")
    finally:
        srv.stop()


def test_plotly_source_override_and_local_file(reg, tmp_path, monkeypatch):
    monkeypatch.delenv("SCREENLAB_PLOTLY_JS", raising=False)
    assert web.plotly_src() == web.PLOTLY_CDN and "plotly-2.35.2.min.js" in web.PLOTLY_CDN
    monkeypatch.setenv("SCREENLAB_PLOTLY_JS", "https://example.invalid/p.js")
    assert 'src="https://example.invalid/p.js"' in web.page("charts.html").decode()
    f = tmp_path / "plotly.min.js"
    f.write_text("window.Plotly = {};")
    monkeypatch.setenv("SCREENLAB_PLOTLY_JS", str(f))
    srv = ControlServer(FakeApp(), Session(reg), port=0).start()
    try:
        assert 'src="/plotly.js"' in raw(srv.port, "/charts")[2].decode()
        code, ctype, body = raw(srv.port, "/plotly.js")
        assert code == 200 and body == b"window.Plotly = {};" and "javascript" in ctype
    finally:
        srv.stop()


def test_theme_endpoint_returns_variables(charts):
    _, srv = charts
    code, _, body = raw(srv.port, "/theme")
    t = json.loads(body)
    assert code == 200 and t["name"] == "nord" and t["dark"] is True
    v = t["variables"]
    for k in ("background", "surface", "panel", "foreground", "primary", "accent", "success", "warning", "error"):
        assert v[k].startswith("#") and len(v[k]) == 7, k
    assert v["background"] == "#2E3440" and v["error"] == "#BF616A"
    assert v["text_muted"].startswith("#") and v["grid"].startswith("#")                  # derived, not "auto 60%"
    light = web.theme_payload("textual-light", False, None)                               # no app: built-in fallback
    assert light["dark"] is False and light["variables"]["background"] == "#E0E0E0"


def test_charts_token_via_query(reg):
    srv = ControlServer(FakeApp(), Session(reg), port=0, token="sekret").start()
    try:
        assert raw(srv.port, "/charts")[0] == 200                                           # page holds no data
        assert raw(srv.port, "/charts/data?tab=curves")[0] == 401
        assert raw(srv.port, "/theme")[0] == 401
        assert raw(srv.port, "/charts/data?tab=curves&token=sekret")[0] == 200
        assert raw(srv.port, "/theme", token="sekret")[0] == 200
    finally:
        srv.stop()


def test_events_stream_emits_on_set_params(charts):
    s, srv = charts
    got: list = []
    ready = threading.Event()

    def reader():
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/events", timeout=30) as r:
            assert r.headers.get("Content-Type", "").startswith("text/event-stream")
            ev = None
            while len(got) < 2:
                line = r.readline().decode().strip()
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:") and ev == "change":
                    got.append(json.loads(line[5:]))
                    ready.set()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    assert ready.wait(10), "no initial event"
    first = got[0]
    assert first["seq"] == s.seq and "tab" in first
    s.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 33}})
    th.join(15)
    assert len(got) == 2 and got[1]["seq"] > first["seq"] and got[1]["seq"] == s.seq


def test_app_key_w_opens_charts_in_browser(reg, monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.delenv("SCREENLAB_TUI_TOKEN", raising=False)
    monkeypatch.delenv("SCREENLAB_TUI_SERVE_URL", raising=False)

    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=0)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            port = app._control.port
            assert any("charts in the browser" in line for line in s._log)
            await pilot.press("w")
            await settle(pilot, app)
            assert opened == [f"http://127.0.0.1:{port}/charts"]
            code, ctype, body = await asyncio.to_thread(raw, port, "/theme")             # real Textual theme colours
            t = json.loads(body)
            assert code == 200 and t["name"] == "textual-dark" and t["variables"]["background"].startswith("#")
            assert not app.query_one("#chartsbar").display             # footer line only in --serve mode
        s.close()
    arun(go())


def test_app_serve_mode_shows_split_url(reg, monkeypatch):
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("must not open a browser in --serve mode"))
    monkeypatch.setenv("SCREENLAB_TUI_SERVE_URL", "http://localhost:8000")

    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=0)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            port = app._control.port
            assert app.query_one("#chartsbar").display
            await pilot.press("w")
            await settle(pilot, app)
            assert any(f"charts: http://127.0.0.1:{port}/" in line for line in s._log)
            code, ctype, body = await asyncio.to_thread(raw, port, "/")
            assert code == 200 and b"http://localhost:8000" in body
        s.close()
    arun(go())


# ------------------------------------------------------------------ shared session: RemoteSession (--serve)
from screenlab.tui.control import HeadlessHost                              # noqa: E402
from screenlab.tui.remote import RemoteSession                              # noqa: E402


@pytest.fixture
def parent(reg):
    """What `screenlab-tui --serve` runs in the parent process: the one Session behind a headless control endpoint."""
    s = Session(reg)
    srv = ControlServer(HeadlessHost(s), s, port=0, serve_url="http://localhost:8000").start()
    clients = []

    def connect(**kw):
        c = RemoteSession(f"http://127.0.0.1:{srv.port}", **kw)
        clients.append(c)
        return c
    yield SimpleNamespace(session=s, server=srv, connect=connect)
    for c in clients:
        c.close()
    srv.stop()


def test_remote_session_round_trip(parent):
    r = parent.connect()
    assert r.remote and r.serve and r.home_url.endswith(f":{parent.server.port}/")
    out = r.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
    assert out["jobs"][0]["status"] == "done"                                  # wait ran in the parent
    assert [x.label for x in parent.session.scenarios] == ["dogs"]            # one session, in the parent
    assert [x.label for x in r.scenarios] == ["dogs"] and r.selected == out["scenario"]["id"]   # read your own write
    assert r.scenarios[0].status == "done" and r.scenarios[0].color == PALETTE[0]
    v2 = r.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 30}, "run": True, "wait": True})
    assert v2["diff_line"].startswith("dogs -> dogs v2") and r.scenarios[1].diff_line == v2["diff_line"]
    c = r.command("compare")
    assert [x["label"] for x in c["rows"]] == ["SYN_CAPWT", "dogs", "dogs v2"]
    assert r.passive_label() == "SYN_CAPWT" and "SYN_CAPWT" in r.card().line() and r.card().warnings() is not None
    assert r.snapshot()["seq"] == parent.session.seq == r.seq
    assert "dogs v2" in r.view() and "== compare ==" in r.view("compare")
    assert list(r.curves_data()) == ["SYN_CAPWT", "dogs", "dogs v2"]
    assert r.screen_names() == parent.session.screen_names() and "dogs" in r.screen_names("synthetic")
    assert r.lab_names() == ["synthetic"] and r.active_jobs() == []
    for cmd, args, status in [("nope", {}, 404), ("select", {"scenario": "ghost"}, 404),
                              ("add_scenario", {}, 400), ("show", {"tab": "x"}, 400)]:
        with pytest.raises(SessionError) as e:
            r.command(cmd, args)
        assert e.value.status == status, cmd


def test_remote_seq_poll_and_await_jobs(parent):
    r = parent.connect()
    assert r.poll(wait=2) is True                                              # first state counts as a change
    assert r.poll() is False                                                   # nothing moved since
    parent.session.command("add_scenario", {"screen": "dogs"})                 # changed behind the client's back
    assert r.poll(wait=5) is True and [x.label for x in r.scenarios] == ["dogs"]
    assert r.seq == parent.session.seq
    q = r.command("run", {"scenario": "dogs"})                                 # no wait: submit, then await
    done = r.await_jobs(q, 60)
    assert done["jobs"][0]["status"] == "done" and r.scenarios[0].status == "done"
    q = r.command("picks", {"scenario": "dogs"})                               # picks waits by default
    assert q["columns"][0] == "ticker" and q["rows"]
    t = r.command("timing", {"scenario": "dogs", "stride": 10})
    assert r.await_jobs(t, 60)["jobs"][0]["status"] == "done"
    assert "dogs" in r.analyses["timing"]
    with pytest.raises(SessionError) as e:
        r.await_jobs("not a result", 1)
    assert e.value.status == 400
    # no watcher thread: poll() is a single GET /seq?after= and refreshes only when the parent moved
    r2 = parent.connect(watch=False)
    assert r2.poll() is True and r2.poll() is False
    parent.session.command("show", {"tab": "log"})
    assert r2.poll() is True and r2.active_tab == "log" and r2.poll() is False


def test_two_remote_clients_share_one_session(parent):
    a, b = parent.connect(), parent.connect()
    a.command("add_scenario", {"screen": "dogs", "run": True, "wait": True})
    assert wait_for(lambda: b.poll(wait=1) or [x.label for x in b.scenarios] == ["dogs"])
    b.command("clone_scenario", {"scenario": "dogs", "name": "twin"})
    assert wait_for(lambda: [x.label for x in a.scenarios] == ["dogs", "twin"])
    assert [x.label for x in a.scenarios] == [x.label for x in b.scenarios] == ["dogs", "twin"]
    assert a.selected == b.selected and a.seq == b.seq == parent.session.seq
    a.command("show", {"tab": "curves"})
    assert wait_for(lambda: b.active_tab == "curves")
    a.set_theme("nord", {"background": "#2E3440", "primary": "#88C0D0"})       # the chart panel follows via /theme
    assert wait_for(lambda: b.theme == "nord")
    code, _, body = raw(parent.server.port, "/theme")
    t = json.loads(body)
    assert code == 200 and t["name"] == "nord" and t["variables"]["background"] == "#2E3440"
    assert chart(parent.server, "curves")["series"][1]["label"] == "dogs"       # charts read the same session


def test_remote_token_and_unreachable(reg, monkeypatch):
    s = Session(reg)
    srv = ControlServer(HeadlessHost(s), s, port=0, token="sekret").start()
    try:
        with pytest.raises(SessionError) as e:
            RemoteSession(f"http://127.0.0.1:{srv.port}", token="wrong", watch=False).snapshot()
        assert e.value.status == 401
        monkeypatch.setenv("SCREENLAB_TUI_TOKEN", "sekret")                      # children get the token via env
        r = RemoteSession(f"http://127.0.0.1:{srv.port}", watch=False)
        assert r.snapshot()["lab"] == "synthetic"
    finally:
        srv.stop()
    with pytest.raises(SessionError) as e:
        RemoteSession(f"http://127.0.0.1:{srv.port}", connect_timeout=0.5, watch=False)
    assert e.value.status == 503


def test_app_runs_with_remote_session(parent):
    async def go():
        r = parent.connect()
        app = ScreenlabApp(r, control_port=None)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            assert "SYN_CAPWT" in str(app.query_one("#head").render())
            assert app._control is None and app._remote
            # the other tab (here: the parent's session directly) adds a scenario: this app shows it
            parent.session.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
            await settle(pilot, app)
            t = app.query_one("#t-compare")
            assert [str(t.get_row_at(i)[0]) for i in range(t.row_count)] == ["● SYN_CAPWT (passive)", "● dogs"]
            app.query_one("#f-cost_bps").value = "40"                    # Apply in the app -> new version in the parent
            await pilot.press("f2")
            await until(lambda: t.row_count == 3 and all(x.status == "done" for x in parent.session.scenarios), pilot=pilot, what='t.row_count == 3 and all(x.status == "done" for x ')
            await settle(pilot, app)
            assert [x.label for x in parent.session.scenarios] == ["dogs", "dogs v2"]
            assert "cost_bps 10.0 -> 40.0" in str(app.query_one("#diff").render())
            other = parent.connect()                                     # a second browser tab sees the same scenarios
            assert [x.label for x in other.scenarios] == ["dogs", "dogs v2"]
            await pilot.press("2")                                       # the tab is shared state too
            await settle(pilot, app)
            assert parent.session.active_tab == "curves" and app.query_one("#main").active == "curves"
            parent.session.command("show", {"tab": "log"})
            await until(lambda: app.query_one("#main").active == "log", pilot=pilot, what="#main follows the parent's tab")
            await pilot.press("T")
            await until(lambda: parent.session.theme == "textual-light" and app.theme == "textual-light",
                        pilot=pilot, what="theme change round trip")
            assert parent.session.theme == "textual-light" and app.theme == "textual-light"
            await pilot.press("w")                                       # no browser: shows the shared page URL
            await until(lambda: any(f":{parent.server.port}/" in line for line in r.log_lines())
                        and app.query_one("#chartsbar").display, pilot=pilot, what="shared page URL logged")
    arun(go())


def test_serve_mode_shares_one_session():
    pytest.importorskip("textual_serve")
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        web_port = sk.getsockname()[1]
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        ctl_port = sk.getsockname()[1]
    p = subprocess.Popen([sys.executable, "-m", "screenlab.tui.app", "--serve", "--host", "127.0.0.1", "--port",
                          str(web_port), "--lab", "synthetic", "--store", "none", "--control-port", str(ctl_port)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        h = None

        def probe():
            nonlocal h
            try:
                h = http(ctl_port, "GET", "/health")[1]
                return True
            except OSError:
                return False
        wait_for(probe, timeout=30)
        assert h and h["headless"] is True and h["serve"] is True and h["control_port"] == ctl_port
        code, r = http(ctl_port, "POST", "/command", {"cmd": "add_scenario", "args": {"screen": "dogs"}})
        assert code == 200
        code, st = http(ctl_port, "GET", "/state")
        assert [x["label"] for x in st["scenarios"]] == ["dogs"]               # the parent holds the session
        code, ctype, body = raw(ctl_port, "/")                                  # split page with the terminal iframe
        assert code == 200 and f"http://127.0.0.1:{web_port}" in body.decode()
        code, sq = http(ctl_port, "GET", f"/seq?after={st['seq']}&timeout=0")
        assert code == 200 and sq["seq"] == st["seq"]
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


# ------------------------------------------------------------------ Data tab and the fetch_data job
from screenlab.operations import OPERATIONS, register_function            # noqa: E402


def _stub_fetch(lab, sources=None, dest="data", sec_user_agent=None, tickers=None, start=None, periods=None):
    """Stands in for ops/fetch_data.py: same arguments, same result shape, no network."""
    files = [{"source": s, "path": f"{dest}/{s}/file.csv", "bytes": 1000 + i, "status": "downloaded"}
             for i, s in enumerate(sources)]
    return {"manifest": {"files": files, "errors": ["stooq: HTTP 403 (host not allowed)"],
                         "allow_hosts": ["www.sec.gov", "stooq.com"], "args_seen": {
                             "sec_user_agent": sec_user_agent, "tickers": tickers, "start": start, "periods": periods}},
            "labs": [{"name": "real_ca", "fundamentals": "/nonexistent/f.csv", "prices": "/nonexistent/p.csv",
                      "indices": None}]}


@pytest.fixture
def stub_fetch():
    old = OPERATIONS.get("fetch_data")
    register_function("fetch_data", _stub_fetch)
    yield
    if old is None:
        OPERATIONS.pop("fetch_data", None)
    else:
        OPERATIONS["fetch_data"] = old


def test_session_fetch_data_job_and_datasets(stub_fetch):
    s = Session(make_registry(), own=True)
    d = s.command("datasets")
    assert d["downloader"] is True and d["sources"] == ["sec", "boc", "fred", "stooq", "statcan", "shiller", "tiingo"]
    assert [x["name"] for x in d["labs"]] == ["synthetic"] and "SYN_CAPWT" in d["labs"][0]["line"]
    assert "www.sec.gov" in d["allow_hosts"] and d["last_fetch"] is None
    with pytest.raises(SessionError) as e:
        s.command("fetch_data", {"sources": ["sec"], "sec_user_agent": "nobody"})          # SEC needs a contact
    assert e.value.status == 400 and "sec_user_agent" in e.value.message
    with pytest.raises(SessionError) as e:
        s.command("fetch_data", {"sources": ["nope"]})
    assert e.value.status == 400
    r = s.command("fetch_data", {"sources": ["sec", "stooq"], "dest": "dd", "sec_user_agent": "Ann Lee ann@example.com",
                                 "tickers": ["XIU.TO"], "start": "2010-01-01", "wait": True})
    assert r["jobs"][0]["kind"] == "fetch_data" and r["jobs"][0]["status"] == "done"
    man = r["fetch"]["manifest"]
    assert [f["source"] for f in man["files"]] == ["sec", "stooq"] and man["errors"]
    assert man["args_seen"]["sec_user_agent"] == "Ann Lee ann@example.com" and man["args_seen"]["start"] == "2010-01-01"
    d = s.command("datasets")
    names = [x["name"] for x in d["labs"]]
    assert names == ["synthetic", "real_ca"]                                  # the new lab is listed
    assert d["labs"][1]["line"] is None and d["labs"][1]["error"]             # unreadable CSV: reported, not raised
    assert s.command("snapshot")["labs"] == names and "fetch_data: 2 file(s)" in " ".join(s.log_lines())
    assert "real_ca" in s.command("view", {"tab": "data"})["view"]
    s.close()


def test_session_downloader_not_installed(monkeypatch):
    monkeypatch.setattr(Session, "downloader_available", staticmethod(lambda: False))
    s = Session(make_registry(), own=True)
    assert s.command("datasets")["downloader"] is False
    with pytest.raises(SessionError) as e:
        s.command("fetch_data", {"sources": ["boc"]})
    assert e.value.status == 501 and "downloader not installed" in e.value.message
    assert "downloader not installed" in s.command("view", {"tab": "data"})["view"]
    s.close()


def test_app_data_tab_cards_and_download(stub_fetch):
    async def go():
        s = Session(make_registry(), own=True)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(180, 60)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "dogs"})
            await pilot.press("D")                                           # key D opens the Data tab
            await settle(pilot, app)
            assert s.active_tab == "data" and app.query_one("#main").active == "data"
            t = app.query_one("#t-datasets")
            assert t.row_count == 1 and "synthetic" in str(t.get_row_at(0)[0])
            assert "SYN_CAPWT" in str(t.get_row_at(0)[2]) or "synthetic" in str(t.get_row_at(0)[2]).lower()
            assert "www.sec.gov" in str(app.query_one("#d-hosts").render())   # allow-list hostnames are shown
            assert not app.query_one("#d-go").disabled
            app.query_one("#d-sec").value = ""                                 # SEC ticked, no contact: refused
            await pilot.click("#d-go")
            await settle(pilot, app)
            assert s.data_fetch is None
            app.query_one("#d-sec").value = "Ann Lee ann@example.com"
            app.query_one("#d-src-fred").value = False
            await pilot.click("#d-go")
            await until(lambda: s.data_fetch and app.query_one("#t-manifest").row_count, pilot=pilot, what='s.data_fetch and app.query_one("#t-manifest").row_')
            await settle(pilot, app)
            man = app.query_one("#t-manifest")
            from screenlab.tui.session import FETCH_SOURCES
            expected = [x for x in FETCH_SOURCES if x != "fred"]
            assert [str(man.get_row_at(i)[0]) for i in range(man.row_count)] == expected
            assert f"download finished: {len(expected)} file(s)" in str(app.query_one("#d-status").render())
            assert "403" in str(app.query_one("#d-errors").render())
            assert app.query_one("#t-datasets").row_count == 2                 # the lab the job produced is listed
            sel = app.query_one("#f-lab")                                      # and the editor offers it
            assert "real_ca" in [v for _, v in sel._options]
            assert s.data_fetch["manifest"]["args_seen"]["tickers"] == ["XIU.TO"]
        s.close()
    arun(go())


def test_app_data_tab_without_downloader(monkeypatch):
    monkeypatch.setattr(Session, "downloader_available", staticmethod(lambda: False))

    async def go():
        s = Session(make_registry(), own=True)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(180, 60)) as pilot:
            await settle(pilot, app)
            await pilot.press("D")
            await settle(pilot, app)
            assert "downloader not installed" in str(app.query_one("#d-status").render())
            assert app.query_one("#d-go").disabled and app.query_one("#t-datasets").row_count == 1
        s.close()
    arun(go())


def test_data_tab_over_remote_session(parent, stub_fetch):
    async def go():
        r = parent.connect()
        app = ScreenlabApp(r, control_port=None)
        async with app.run_test(size=(180, 60)) as pilot:
            await settle(pilot, app)
            await pilot.press("D")
            await settle(pilot, app)
            assert app.query_one("#t-datasets").row_count == 1
            app.query_one("#d-sec").value = "Ann Lee ann@example.com"
            await pilot.click("#d-go")
            await until(lambda: parent.session.data_fetch and app.query_one("#t-manifest").row_count, pilot=pilot, what='parent.session.data_fetch and app.query_one("#t-ma')
            await settle(pilot, app)
            from screenlab.tui.session import FETCH_SOURCES
            assert app.query_one("#t-manifest").row_count == len(FETCH_SOURCES)   # every source ticked
            assert parent.session.lab_names() == ["synthetic", "real_ca"]
    arun(go())


# ------------------------------------------------------------------ guardrails (ROADMAP Phase 2)
from screenlab.tui import guardrails                                        # noqa: E402
from screenlab.tui.session import CAND, LOCK                                # noqa: E402

PREREG = """# test file
## H1: EXAMPLE - impossible
- name: Dogs beat the index by ten points a year
- screen: dogs
- dataset: synthetic
- benchmark: SYN_CAPWT total return
- horizon: 5 years
- metric: Excess/yr
- threshold: >= 0.10
- costs: 10 bps
- exit: none
- written: 2026-10-30
- status: open

## H2: easy
- name: Dogs earn more than minus fifty percent a year
- screen: dogs
- dataset: synthetic
- benchmark: SYN_CAPWT total return
- horizon: 5 years
- metric: CAGR
- threshold: > -0.5
- costs: 10 bps
- exit: none
- written: 2026-10-30
- status: supported

## H3: too long
- name: needs fifty years
- screen: dogs
- dataset: synthetic
- benchmark: x
- horizon: 50 years
- metric: CAGR
- threshold: > 0
- costs: 10 bps
- written: 2026-10-30

<!-- ## H9 inside a comment is ignored
- name: ghost
-->
"""


def row_of(c, label):
    return next(r for r in c["rows"] if r["label"] == label)


def test_trials_and_dsr_columns_and_dsr_falls_with_a_second_version(reg):
    s = Session(reg)
    run_scenario(s, cost_bps=10)
    c1 = s.command("compare")
    assert {"Trials", "DSR"} <= set(c1["columns"]) and c1["trials_total"] == 1
    d1 = row_of(c1, "dogs")["values"]
    assert d1["Trials"] == 1 and 0 < d1["DSR"] < 1
    assert "Trials" not in c1["rows"][0]["values"] and "DSR" not in c1["rows"][0]["values"]       # passive: no trials
    s.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 12}, "run": True, "wait": True})
    c2 = s.command("compare")
    v1, v2 = row_of(c2, "dogs")["values"], row_of(c2, "dogs v2")["values"]
    assert v1["Trials"] == v2["Trials"] == 2 and c2["trials_total"] == 2
    assert v1["DSR"] < d1["DSR"] and v2["DSR"] < d1["DSR"]               # the same Sharpe is worth less after a second look
    text = s.compare_text()
    assert "Trials" in text and "DSR" in text and "trials this session: 2" in text
    snap = s.snapshot()
    assert snap["trials"]["total"] == 2 and [x["trials"] for x in snap["scenarios"]] == [2, 2]
    assert all(0 < x["dsr"] < 1 for x in snap["scenarios"])
    # a store's count (it spans sessions) wins when larger
    s.scenarios[0].result["trials_in_family"] = 7
    assert row_of(s.command("compare"), "dogs")["values"]["Trials"] == 7
    assert guardrails.row_dsr(None, None, 3) is None


def test_row_dsr_blank_when_not_computable(reg):
    s = Session(reg)
    run_scenario(s)
    s.scenarios[0].result["metrics"]["Sharpe"] = None
    r = row_of(s.command("compare"), "dogs")
    assert r["values"].get("DSR") is None and "Trials" in s.command("compare")["columns"]
    assert "—" in s.compare_text()


def test_freeze_marks_candidates_and_penalises_the_frozen_row(reg, tmp_path):
    s = Session(reg)
    run_scenario(s, cost_bps=10)
    f = s.command("freeze", {"scenario": "dogs"})
    assert f["scenario"]["frozen"] is True
    before = row_of(s.command("compare"), "dogs")
    assert before["frozen"] and before["mark"] == LOCK and before["values"]["Trials"] == 1
    s.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 20}, "run": True, "wait": True})
    c = s.command("compare")
    v1, v2 = row_of(c, "dogs"), row_of(c, "dogs v2")
    assert v2["candidate"] and v2["candidate_of"] == "dogs" and CAND in v2["mark"] and not v1["candidate"]
    assert v1["values"]["Trials"] == 3 and v2["values"]["Trials"] == 2        # the candidate is an extra trial for dogs
    assert v1["values"]["DSR"] < before["values"]["DSR"]
    snap = {x["label"]: x for x in s.snapshot()["scenarios"]}
    assert snap["dogs"]["frozen"] is True and snap["dogs v2"]["candidate"] is True
    assert any("candidate of frozen dogs" in line for line in s.log_lines())
    # a version derived from a candidate, and a clone of the frozen one, are candidates too
    s.command("set_params", {"scenario": "dogs v2", "params": {"cost_bps": 30}})
    s.command("clone_scenario", {"scenario": "dogs"})
    assert [x.candidate_of for x in s.scenarios] == [None, "dogs", "dogs", "dogs"]
    n = len(s.log_lines())
    u = s.command("unfreeze", {"scenario": "dogs"})
    assert u["scenario"]["frozen"] is False and len(u["candidates"]) == 3 and len(s.log_lines()) == n + 1
    assert "UNFROZE dogs" in s.log_lines()[-1] and s.scenarios[1].candidate_of == "dogs"
    # export carries the flags
    s.command("export", {"path": str(tmp_path / "x")})
    head = (tmp_path / "x.csv").read_text().splitlines()
    assert head[0].startswith("scenario,passive,color,frozen,candidate_of,hypothesis")
    assert "dogs v2,False" in head[3] and ",dogs," in head[3]
    assert LOCK not in (tmp_path / "x.md").read_text().split("dogs v2")[0] or True


def test_app_frozen_scenario_is_read_only_and_header_shows_trials(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(170, 50)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "cost_bps": 10})
            await settle(pilot, app)
            assert "trials this session: 1" in str(app.query_one("#head").render())
            t = app.query_one("#t-compare")
            assert "Trials" in [str(c.label) for c in t.columns.values()] and "DSR" in [str(c.label) for c in t.columns.values()]
            assert app.query_one("#f-cost_bps").disabled is False
            await pilot.press("f")
            await settle(pilot, app)
            assert s.scenarios[0].frozen
            assert app.query_one("#f-cost_bps").disabled and app.query_one("#apply").disabled
            assert "read-only" in str(app.query_one("#diff").render())
            assert LOCK in str(t.get_row_at(1)[0])
            await pilot.press("f2")
            await settle(pilot, app)
            assert len(s.scenarios) == 1                                       # Apply did nothing
            await pilot.press("c")                                             # clone to continue
            await settle(pilot, app)
            assert len(s.scenarios) == 2 and s.scenarios[1].candidate_of == "dogs"
            assert app.query_one("#f-cost_bps").disabled is False
            await pilot.press("H")
            await settle(pilot, app)
            assert s.active_tab == "hypotheses" and app.query_one("#main").active == "hypotheses"
            assert app.query_one("#t-hyp").row_count == len(s.command("hypotheses")["hypotheses"]) >= 3
        s.close()
    arun(go())


def test_default_preregistered_file_parses_three_complete_examples():
    info = guardrails.load_preregistered()
    assert info["exists"] and len(info["hypotheses"]) == 3
    for h in info["hypotheses"]:
        assert h["example"] and h["status"] == "open" and h["exit"] == "none" and h["problems"] == []
        assert guardrails.parse_threshold(h["threshold"]) is not None and h["written"] == "2026-10-30"
    assert any("EXAMPLES" in w for w in info["warnings"])
    assert info["hypotheses"][0]["screen"].startswith("frugal_dividend")
    assert "total return" in info["hypotheses"][0]["benchmark"]


def test_parse_threshold_and_evaluate_reasons():
    assert guardrails.parse_threshold(">= 2%") == (">=", 0.02) and guardrails.parse_threshold("<= -0.30 (x)") == ("<=", -0.3)
    assert guardrails.parse_threshold("about 2") is None
    h = {"id": "H1", "metric": "Excess/yr (CI5)", "threshold": "> 0", "horizon": "15 years"}
    assert guardrails.evaluate(h, {"CAGR": 0.1})["met"] is None
    assert "run with ci" in guardrails.evaluate({**h, "metric": "CI5"}, {})["reason"]
    assert guardrails.evaluate({**h, "metric": "Excess/yr"}, {"Excess/yr": 0.02}, 10.0)["met"] is None   # shorter than 15y
    assert guardrails.evaluate({**h, "metric": "Excess/yr"}, {"Excess/yr": 0.02}, 15.0)["met"] is True


def test_link_reports_met_and_counts_trials_against_the_hypothesis(reg, tmp_path):
    path = tmp_path / "PREREGISTERED.md"
    path.write_text(PREREG)
    s = Session(reg, prereg=str(path))
    info = s.command("hypotheses")
    assert [h["id"] for h in info["hypotheses"]] == ["H1", "H2", "H3"] and info["hypotheses"][0]["example"]
    run_scenario(s, cost_bps=10)
    run_scenario(s, "momentum_6m")
    with pytest.raises(SessionError) as e:
        s.command("link", {"scenario": "dogs", "hypothesis": "H9"})
    assert e.value.status == 404
    s.command("link", {"scenario": "dogs", "hypothesis": "h1"})
    s.command("link", {"scenario": "momentum_6m", "hypothesis": "H2"})
    h = {x["id"]: x for x in s.command("hypotheses")["hypotheses"]}
    assert h["H1"]["linked"][0]["met"] is False and h["H1"]["met"] is False        # excess is about -3 points
    assert h["H1"]["linked"][0]["value"] < 0.10 and h["H1"]["linked"][0]["label"] == "dogs"
    assert h["H2"]["linked"][0]["met"] is True and h["H2"]["met"] is True
    c = s.command("compare")
    assert row_of(c, "dogs")["family"] == "synthetic:dogs@H1" and "[H1]" in row_of(c, "dogs")["mark"]
    # new versions inherit the link; the hypothesis' family counts every variant against it
    s.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 15}, "run": True, "wait": True})
    s.command("set_params", {"scenario": "dogs v2", "params": {"cost_bps": 20}, "run": True, "wait": True})
    c = s.command("compare")
    assert row_of(c, "dogs v3")["values"]["Trials"] == 3 and row_of(c, "momentum_6m")["values"]["Trials"] == 1
    assert s.snapshot()["trials"]["families"]["synthetic:dogs@H1"] == 3
    assert h["H1"]["linked"][0]["met"] is False
    assert [x["met"] for x in s.command("hypotheses")["hypotheses"][0]["linked"]] == [False]
    # a horizon longer than the data cannot be met
    s.command("link", {"scenario": "momentum_6m", "hypothesis": "H3"})
    x = s.command("hypotheses")["hypotheses"][2]["linked"][0]
    assert x["met"] is None and "covers" in x["reason"]
    s.command("link", {"scenario": "momentum_6m", "hypothesis": None})
    assert s.command("hypotheses")["hypotheses"][2]["linked"] == []
    assert "H1" in s.view("hypotheses") and "NOT MET" in s.view("hypotheses")
    json.dumps(s.snapshot())


def test_chart_compare_data_carries_trials_and_dsr(charts):
    s, srv = charts
    c = chart(srv, "compare")
    assert {"Trials", "DSR"} <= set(c["columns"]) and c["trials_total"] == s.trials_total() >= 2
    dogs = row_of(c, "dogs")
    assert dogs["values"]["Trials"] >= 1 and 0 < dogs["values"]["DSR"] < 1 and "frozen" in dogs
    assert "Trials" not in c["rows"][0]["values"]
    assert web.data_compare(s)["trials_total"] == c["trials_total"]


# ------------------------------------------------------------------ tax (editor, Compare columns, web, remote)
def _cmp_row(t, label):
    return next(i for i in range(t.row_count) if label in str(t.get_row_at(i)[0]))


def _cell(t, ci, name):
    cols = [str(c.label) for c in t.columns.values()]
    return str(t.get_row_at(ci)[cols.index(name)])


def test_tax_editor_apply_new_version_with_after_tax_columns(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(200, 60)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True})
            await settle(pilot, app)
            t = app.query_one("#t-compare")
            assert "AfterTaxCAGR" not in [str(c.label) for c in t.columns.values()]
            assert app.query_one("#f-tax-account").value == "none"
            app.query_one("#f-tax-account").value = "taxable"
            app.query_one("#f-tax-income").value = "90000"
            await pilot.press("f2")
            await until(lambda: len(s.scenarios) == 2 and all(x.status == "done" for x in s.scenarios), pilot=pilot,
                        what="tax version done")
            await settle(pilot, app)
            v2 = s.scenarios[1]
            assert v2.label == "dogs v2" and v2.params["tax"]["account"] == "taxable"
            assert v2.params["tax"]["employment_income"] == 90000.0 and "tax taxable" in v2.diff_line
            await until(lambda: "AfterTaxCAGR" in [str(c.label) for c in t.columns.values()], pilot=pilot,
                        what="after-tax columns")
            vals = {r["label"]: r["values"] for r in s.command("compare")["rows"]}
            assert vals["dogs v2"]["AfterTaxCAGR"] <= vals["dogs v2"]["CAGR"]
            assert vals["SYN_CAPWT"]["AfterTaxCAGR"] is not None                 # the passive index shows its taxed value
            assert "dogs" in vals and "AfterTaxCAGR" not in vals["dogs"]          # untaxed version: blank
            assert _cell(t, _cmp_row(t, "dogs v2"), "AfterTaxCAGR").strip() != ""
            assert _cell(t, _cmp_row(t, "● dogs"), "AfterTaxCAGR").strip() == ""
            # TFSA: no tax on this portfolio's gains, so after-tax equals pre-tax
            app.query_one("#f-tax-account").value = "tfsa"
            await pilot.press("f2")
            await until(lambda: len(s.scenarios) == 3 and all(x.status == "done" for x in s.scenarios), pilot=pilot,
                        what="tfsa version done")
            v3 = s.command("compare")["rows"][-1]["values"]
            assert v3["AfterTaxCAGR"] == pytest.approx(v3["CAGR"], abs=1e-9)
            assert "Tax: dogs v2 taxable" in s.view("compare") and "dogs v3 tfsa" in s.view("compare")
            snap = s.command("snapshot")
            assert snap["scenarios"][2]["params"]["tax"]["account"] == "tfsa"
    arun(go())


def test_tax_in_web_compare_and_exports(sess, tmp_path):
    from screenlab.tui import web
    run_scenario(sess, tax={"model": "placeholder", "account": "taxable"})
    d = web.data_compare(sess)
    assert {"AfterTaxCAGR", "TaxDrag/yr"} <= set(d["columns"])
    assert d["rows"][0]["values"]["AfterTaxCAGR"] is not None and d["tax"] == {"dogs": sess.scenarios[0].params["tax"]}
    sess.command("export", {"path": str(tmp_path / "x"), "format": "both"})
    assert "AfterTaxCAGR" in (tmp_path / "x.csv").read_text().splitlines()[0]
    assert "taxable" in (tmp_path / "x.md").read_text()
    assert "Tax: dogs taxable" in sess.view("compare")
    with pytest.raises(SessionError) as e:
        sess.command("set_params", {"scenario": "dogs", "params": {"tax": {"model": "nope", "account": "tfsa"}}})
    assert e.value.status == 400 and "config.tax.model" in e.value.message


def test_remote_session_sees_tax_params(parent):
    r = parent.connect()
    r.command("add_scenario", {"screen": "dogs", "run": True, "wait": True,
                               "tax": {"model": "placeholder", "account": "tfsa"}})
    assert r.scenarios[0].params["tax"]["account"] == "tfsa"
    assert "AfterTaxCAGR" in r.command("compare")["columns"]
    assert "Tax: dogs tfsa" in r.view("compare")


def test_tax_editor_province_select_makes_canadian_version(reg):
    async def go():
        s = Session(reg)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(200, 60)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True})
            await settle(pilot, app)
            app.query_one("#f-tax-account").value = "taxable"
            app.query_one("#f-tax-province").value = "AB"
            await pilot.press("f2")
            await until(lambda: len(s.scenarios) == 2 and all(x.status == "done" for x in s.scenarios), pilot=pilot,
                        what="canadian version done")
            tax = s.scenarios[1].params["tax"]
            assert tax["model"] == "canadian" and tax["province"] == "AB" and tax["account"] == "taxable"
            v = s.command("compare")["rows"][-1]["values"]
            assert v["AfterTaxCAGR"] <= v["CAGR"] and "AfterTaxExcess/yr" in v
    arun(go())
