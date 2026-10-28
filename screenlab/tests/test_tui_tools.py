"""tui_* MCP tools against a stub control server implementing tui/PROTOCOL.md, plus one real-TUI test."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("httpx")

from screenlab.errors import ServerError
from screenlab.server.mcp_server import build_tools
from screenlab.server.tui_tools import TuiClient, build_tui_tools

TUI_NAMES = {"tui_status", "tui_state", "tui_view", "tui_command", "tui_add_scenario", "tui_set_params",
             "tui_run", "tui_compare", "tui_show", "tui_montecarlo", "tui_export"}


class Stub:
    def __init__(self, token=None):
        self.token, self.calls, self.headers = token, [], []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _authed(self):
                stub.headers.append(dict(self.headers))
                if stub.token and self.path != "/health" and \
                        self.headers.get("Authorization") != f"Bearer {stub.token}":
                    self._send(401, {"error": "unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._authed():
                    return
                if self.path == "/health":
                    self._send(200, {"ok": True, "version": "0.1.0", "protocol": 1, "lab": "synthetic"})
                elif self.path == "/state":
                    self._send(200, {"protocol": 1, "seq": 1, "scenarios": []})
                elif self.path == "/view":
                    self._send(200, {"tab": "compare", "view": "COMPARE"})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._authed():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.calls.append(body)
                if body["cmd"] == "nope":
                    self._send(404, {"error": "unknown command 'nope'"})
                elif body["cmd"] == "set_params" and not body["args"].get("params"):
                    self._send(400, {"error": "no parameter changed"})
                else:
                    self._send(200, {"echo": body})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def tools(stub, token=None):
    return build_tui_tools(TuiClient(stub.url, token))


def test_status_state_view(stub):
    t = tools(stub)
    st = t["tui_status"]()
    assert st["running"] is True and st["lab"] == "synthetic" and st["url"] == stub.url
    assert t["tui_state"]()["protocol"] == 1
    assert t["tui_view"]() == {"tab": "compare", "view": "COMPARE"}


def test_command_and_wrappers(stub):
    t = tools(stub)
    assert t["tui_command"]("snapshot")["echo"] == {"cmd": "snapshot", "args": {}}
    t["tui_add_scenario"]("dogs", params={"cost_bps": 10}, run=True)
    t["tui_set_params"]({"cost_bps": 25}, scenario="dogs")
    t["tui_run"]("all", ci=True)
    t["tui_compare"](["dogs", "dogs v2"])
    t["tui_show"]("curves")
    t["tui_montecarlo"](["dogs"], n_paths=200, horizon_years=[5])
    t["tui_export"]("out", "csv")
    c = stub.calls
    assert c[1] == {"cmd": "add_scenario", "args": {"cost_bps": 10, "screen": "dogs", "run": True}}
    assert c[2] == {"cmd": "set_params", "args": {"scenario": "dogs", "params": {"cost_bps": 25}, "run": False}}
    assert c[3] == {"cmd": "run", "args": {"scenario": "all", "ci": True, "force": False, "wait": False}}
    assert c[4] == {"cmd": "compare", "args": {"scenarios": ["dogs", "dogs v2"], "ci": False}}
    assert c[5] == {"cmd": "show", "args": {"tab": "curves"}}
    assert c[6] == {"cmd": "montecarlo", "args": {"scenarios": ["dogs"], "n_paths": 200, "horizon_years": [5],
                                                  "wait": False}}
    assert c[7] == {"cmd": "export", "args": {"path": "out", "format": "csv"}}


def test_token_header():
    s = Stub(token="sekret")
    try:
        assert tools(s, "sekret")["tui_state"]()["protocol"] == 1
        assert s.headers[-1]["Authorization"] == "Bearer sekret"
        with pytest.raises(ServerError) as e:
            tools(s, "wrong")["tui_state"]()
        assert e.value.status == 401 and "SCREENLAB_TUI_TOKEN" in e.value.message
        with pytest.raises(ServerError):
            tools(s)["tui_command"]("snapshot")
        assert tools(s)["tui_status"]()["running"] is True        # /health needs no token
    finally:
        s.close()


def test_env_configuration(stub, monkeypatch):
    monkeypatch.setenv("SCREENLAB_TUI_URL", stub.url + "/")
    monkeypatch.setenv("SCREENLAB_TUI_TOKEN", "abc")
    t = build_tui_tools()                     # client built from the environment
    t["tui_state"]()
    assert stub.headers[-1]["Authorization"] == "Bearer abc"


def test_error_mapping(stub):
    t = tools(stub)
    with pytest.raises(ServerError) as e:
        t["tui_command"]("nope")
    assert (e.value.status, e.value.message) == (404, "unknown command 'nope'")
    with pytest.raises(ServerError) as e:
        t["tui_set_params"]({})
    assert (e.value.status, e.value.message) == (400, "no parameter changed")


def test_unreachable():
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    t = build_tui_tools(TuiClient(f"http://127.0.0.1:{port}"))
    st = t["tui_status"]()
    assert st["running"] is False and "screenlab-tui --control-port 8765" in st["hint"]
    for name, args in [("tui_state", ()), ("tui_view", ()), ("tui_run", ()), ("tui_show", ("log",))]:
        with pytest.raises(ServerError) as e:
            t[name](*args)
        assert e.value.status == 503 and "screenlab-tui --control-port 8765" in e.value.message


def test_build_tools_lists_tui_tools_without_changing_others():
    from screenlab.server import mcp_server
    from screenlab.server import tui_tools
    base = build_tui_tools
    try:
        tui_tools.build_tui_tools = lambda client=None: {}
        before = set(build_tools())
    finally:
        tui_tools.build_tui_tools = base
    after = set(build_tools())
    assert TUI_NAMES <= after and after - before == TUI_NAMES and before <= after
    assert not (TUI_NAMES & before)
    assert {"backtest", "timing", "montecarlo", "list_labs", "submit_run"} <= before


def test_mcp_server_registers_tui_tools():
    pytest.importorskip("mcp")
    from screenlab.server.mcp_server import create_server
    import asyncio
    srv = create_server()
    names = {x.name for x in asyncio.run(srv.list_tools())}
    assert TUI_NAMES <= names and "backtest" in names


def test_real_tui_control_endpoint():
    """Round trip against the real ControlServer + Session (the app is replaced by a direct-call shim; skipped
    unless screenlab.tui imports)."""
    try:
        from screenlab import Lab
        from screenlab.server.registry import LabRegistry
        from screenlab.tui.control import ControlServer
        from screenlab.tui.session import Session
    except Exception as e:                           # not built yet / textual missing
        pytest.skip(f"screenlab.tui not importable: {e!r}")

    class App:
        def call_from_thread(self, fn, *a):
            return fn(*a)

    reg = LabRegistry({"syn": lambda: Lab.synthetic(freq="ME", n_stocks=40, years=6, seed=1)}, default="syn",
                      mode="thread")
    srv = ControlServer(App(), Session(reg), port=0, token="tok").start()
    try:
        t = build_tui_tools(TuiClient(f"http://127.0.0.1:{srv.port}", "tok"))
        assert t["tui_status"]()["running"] is True
        r = t["tui_add_scenario"]("dogs", params={"rebalance_months": 12}, run=True)
        assert r["scenario"]["name"] == "dogs"
        t["tui_run"]("dogs", wait=True, timeout=120)
        r2 = t["tui_set_params"]({"cost_bps": 25}, scenario="dogs", run=True)
        assert r2["scenario"]["version"] == 2
        t["tui_run"]("all", wait=True, timeout=120)
        cmp = t["tui_compare"]()
        assert cmp["rows"][0]["passive"] is True and len(cmp["rows"]) == 3
        t["tui_show"]("curves")
        assert t["tui_view"]()["tab"] == "curves"
        assert {s["label"] for s in t["tui_state"]()["scenarios"]} >= {"dogs", "dogs v2"}
        with pytest.raises(ServerError) as e:
            t["tui_command"]("nope")
        assert e.value.status == 404
        with pytest.raises(ServerError) as e:
            build_tui_tools(TuiClient(srv.url if hasattr(srv, "url") else f"http://127.0.0.1:{srv.port}", "bad"))["tui_state"]()
        assert e.value.status == 401
    finally:
        srv.stop()
