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


@pytest.fixture(scope="module")
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
            await pilot.pause(0.5)
            port = app._control.port
            assert any("charts in the browser" in line for line in s._log)
            await pilot.press("w")
            await pilot.pause(0.3)
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
            await pilot.pause(0.5)
            port = app._control.port
            assert app.query_one("#chartsbar").display
            await pilot.press("w")
            await pilot.pause(0.2)
            assert any(f"charts: http://127.0.0.1:{port}/" in line for line in s._log)
            code, ctype, body = await asyncio.to_thread(raw, port, "/")
            assert code == 200 and b"http://localhost:8000" in body
        s.close()
    arun(go())
