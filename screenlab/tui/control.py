"""Control endpoint of the running TUI (DESIGN.md L3, contract in PROTOCOL.md).

A stdlib `http.server` in a daemon thread. Every command is marshalled into the app's event loop with
`app.call_from_thread`, so the UI and the session are only touched from the app thread. Commands with
`wait` submit on the app thread and then wait for the jobs in the HTTP thread (the UI keeps running).
Bound to 127.0.0.1; if env `SCREENLAB_TUI_TOKEN` is set every request except `GET /health` and the static pages
must carry it (`Authorization: Bearer <token>`, `X-Screenlab-Token`, or `?token=` for the browser pages, whose
fetches and event stream cannot set headers).

Browser chart panel (DESIGN.md L6, tui/web.py): `GET /charts` (page), `GET /charts/data?tab=` (JSON), `GET /theme`,
`GET /events` (server-sent events on every `Session.seq` change), `GET /plotly.js` (local Plotly copy when
`SCREENLAB_PLOTLY_JS` is a file) and, in `--serve` mode, `GET /` (terminal + charts split page)."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import web
from .session import PROTOCOL, SessionError


def _version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:
        return "0.1.0"


class HeadlessHost:
    """Stands in for the Textual app when the control endpoint runs without a UI (`screenlab-tui --serve`: the
    parent process owns the one Session and the endpoint; the app processes are remote clients, remote.py).
    `call_from_thread` runs the function directly under one lock (the role the app thread plays otherwise) and a
    poller thread keeps `Session.poll()` going, since no app tick does it."""
    headless = True

    def __init__(self, session, interval: float = 0.2):
        self.session, self.interval = session, interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def call_from_thread(self, fn, *a):
        with self._lock:
            return fn(*a)

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.session.poll()
                except Exception:                                # noqa: BLE001  (a failed poll must not kill the loop)
                    pass
        self._thread = threading.Thread(target=loop, name="screenlab-tui-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class ControlServer:
    """`ControlServer(app, session, port).start()`; `.port` is the bound port (use 0 for an ephemeral one in
    tests); `.stop()` shuts it down."""

    def __init__(self, app, session, port: int = 8765, host: str = "127.0.0.1", token: str | None = None,
                 serve_url: str | None = None):
        self.app, self.session, self.host = app, session, host
        self.serve_url = serve_url if serve_url is not None else os.environ.get("SCREENLAB_TUI_SERVE_URL") or None
        self._stopping = threading.Event()
        self.token = token if token is not None else os.environ.get("SCREENLAB_TUI_TOKEN") or None
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._want_port = port

    # ---- running on the app thread
    def _on_app(self, fn, *a):
        """Run `fn(*a)` on the app thread and return its result (raises what `fn` raises)."""
        return self.app.call_from_thread(fn, *a)

    def health(self) -> dict:
        s = self.session
        return {"ok": True, "version": _version(), "protocol": PROTOCOL, "lab": s.lab,
                "dataset": self._on_app(lambda: s.card().line()), "store": s.store_path(),
                "control_port": self.port, "seq": s.seq, "home_url": self.home_url(),
                "serve": bool(self.serve_url), "headless": bool(getattr(self.app, "headless", False))}

    def charts_url(self) -> str:
        return f"http://{self.host}:{self.port}/charts"

    def home_url(self) -> str:
        """Where to point a browser: the split page in --serve mode, else the chart page."""
        return f"http://{self.host}:{self.port}/" if self.serve_url else self.charts_url()

    def theme(self) -> dict:
        """Active Textual theme: colours read on the app thread (headless: the session's stored theme name)."""
        def read():
            try:
                t = self.app.current_theme
                return t.name, bool(t.dark), self.app.get_css_variables()
            except Exception:                                    # noqa: BLE001  (headless: what an app process pushed)
                return self.session.theme, "light" not in str(self.session.theme), self.session.theme_vars
        name, dark, css = self._on_app(read)
        return web.theme_payload(name, dark, css)

    def run_command(self, cmd: str, args: dict) -> dict:
        s = self.session
        args = dict(args or {})
        wait = args.pop("wait", None)
        timeout = float(args.get("timeout", 120) or 120)
        if cmd == "picks" and wait is None:
            wait = True
        if wait:
            args.pop("timeout", None)
            res = self._on_app(s.command, cmd, args)           # submit on the app thread, no waiting there
            res = s.await_jobs(res, timeout)                   # wait here; the UI keeps running
            return res
        return self._on_app(s.command, cmd, args)

    # ---- http
    def _handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            server_version = "screenlab-tui"

            def log_message(self, *a):                           # quiet
                pass

            def _send(self, code: int, obj) -> None:
                body = json.dumps(obj).encode()
                self._raw(code, body, "application/json")

            def _raw(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _html(self, name: str, **subst) -> None:
                self._raw(200, web.page(name, **subst), "text/html; charset=utf-8")

            def _events(self) -> None:
                """Server-sent events: one `change` event now and one on every Session.seq change."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                s, last, idle = outer.session, None, 0.0
                try:
                    while not outer._stopping.is_set():
                        seq = s.seq
                        if seq != last:
                            msg = {"seq": seq, "tab": s.active_tab}
                            self.wfile.write(f"event: change\ndata: {json.dumps(msg)}\n\n".encode())
                            self.wfile.flush()
                            last, idle = seq, 0.0
                        elif idle >= 15:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            idle = 0.0
                        time.sleep(0.2)
                        idle += 0.2
                except OSError:                                  # the browser went away
                    pass

            def _authorized(self) -> bool:
                if not outer.token:
                    return True
                got = self.headers.get("X-Screenlab-Token") or (self._query().get("token") or [""])[0]
                auth = self.headers.get("Authorization") or ""
                if auth.lower().startswith("bearer "):
                    got = auth[7:].strip()
                return hmac.compare_digest(got.encode(), outer.token.encode())

            def _guard(self, fn):
                try:
                    self._send(200, fn())
                except SessionError as e:
                    self._send(e.status, {"error": e.message})
                except Exception as e:                           # noqa: BLE001
                    self._send(500, {"error": f"internal error: {type(e).__name__}: {e}"})

            def _query(self) -> dict:
                return parse_qs(urlsplit(self.path).query)

            def do_GET(self):
                path = self.path.split("?")[0].rstrip("/") or "/"
                if path == "/health":
                    return self._guard(outer.health)
                if path == "/charts":                            # static pages carry no data: no token needed
                    return self._html("charts.html")
                if path == "/" and outer.serve_url:
                    return self._html("index.html", SERVE_URL=outer.serve_url)
                if path == "/plotly.js":
                    f = web.plotly_local_file()
                    if f is None:
                        return self._send(404, {"error": "SCREENLAB_PLOTLY_JS is not a local file"})
                    return self._raw(200, f.read_bytes(), "application/javascript")
                if not self._authorized():
                    return self._send(401, {"error": "unauthorized"})
                if path == "/charts/data":
                    return self._guard(lambda: web.chart_data(outer.session, (self._query().get("tab") or [""])[0]))
                if path == "/theme":
                    return self._guard(outer.theme)
                if path == "/events":
                    return self._events()
                if path == "/state":
                    return self._guard(lambda: outer._on_app(outer.session.snapshot))
                if path == "/view":
                    tab = (self._query().get("tab") or [None])[0]
                    return self._guard(lambda: outer._on_app(outer.session.cmd_view, tab))
                if path == "/seq":                               # long-poll: answers when Session.seq != after
                    q = self._query()
                    try:
                        after = int((q.get("after") or ["-1"])[0])
                        wait = min(max(float((q.get("timeout") or ["0"])[0]), 0.0), 30.0)
                    except ValueError:
                        return self._send(400, {"error": "after must be an integer and timeout a number"})
                    return self._guard(lambda: {"seq": outer.session.wait_seq(after, wait)})
                self._send(404, {"error": f"unknown path {path}; use /health /state /view /seq /command /await /charts /charts/data /theme /events"})

            def do_POST(self):
                path = self.path.split("?")[0].rstrip("/")
                if not self._authorized():
                    return self._send(401, {"error": "unauthorized"})
                if path not in ("/command", "/await"):
                    return self._send(404, {"error": f"unknown path {path}; POST /command or /await"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                    if path == "/command" and not isinstance(body.get("cmd"), str):
                        raise ValueError
                    if path == "/await" and not isinstance(body.get("result"), dict):
                        raise ValueError
                except ValueError:
                    return self._send(400, {"error": 'body must be {"cmd": "<name>", "args": {...}}' if path == "/command"
                                            else 'body must be {"result": {"jobs": [...]}, "timeout": 120}'})
                if path == "/await":                              # wait here (HTTP thread) for the jobs of a result
                    try:
                        timeout = float(body.get("timeout") or 120)
                    except (TypeError, ValueError):
                        return self._send(400, {"error": "`timeout` must be a number"})
                    return self._guard(lambda: outer.session.await_jobs(body["result"], timeout))
                args = body.get("args") or {}
                if not isinstance(args, dict):
                    return self._send(400, {"error": "`args` must be an object"})
                self._guard(lambda: outer.run_command(body["cmd"], args))

        return H

    def start(self) -> "ControlServer":
        self._httpd = ThreadingHTTPServer((self.host, self._want_port), self._handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.session.control_port = self.port
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="screenlab-tui-control", daemon=True)
        self._thread.start()
        if getattr(self.app, "headless", False):
            self.app.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if getattr(self.app, "headless", False):
            self.app.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
