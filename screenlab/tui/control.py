"""Control endpoint of the running TUI (DESIGN.md L3, contract in PROTOCOL.md).

A stdlib `http.server` in a daemon thread. Every command is marshalled into the app's event loop with
`app.call_from_thread`, so the UI and the session are only touched from the app thread. Commands with
`wait` submit on the app thread and then wait for the jobs in the HTTP thread (the UI keeps running).
Bound to 127.0.0.1; if env `SCREENLAB_TUI_TOKEN` is set every request except `GET /health` must carry it
(`Authorization: Bearer <token>` or `X-Screenlab-Token`)."""
from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .session import PROTOCOL, SessionError


def _version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:
        return "0.1.0"


class ControlServer:
    """`ControlServer(app, session, port).start()`; `.port` is the bound port (use 0 for an ephemeral one in
    tests); `.stop()` shuts it down."""

    def __init__(self, app, session, port: int = 8765, host: str = "127.0.0.1", token: str | None = None):
        self.app, self.session, self.host = app, session, host
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
                "control_port": self.port}

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
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if not outer.token:
                    return True
                got = self.headers.get("X-Screenlab-Token") or ""
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

            def do_GET(self):
                path = self.path.split("?")[0].rstrip("/") or "/"
                if path == "/health":
                    return self._guard(outer.health)
                if not self._authorized():
                    return self._send(401, {"error": "unauthorized"})
                if path == "/state":
                    return self._guard(lambda: outer._on_app(outer.session.snapshot))
                if path == "/view":
                    return self._guard(lambda: outer._on_app(outer.session.cmd_view))
                self._send(404, {"error": f"unknown path {path}; use /health /state /view /command"})

            def do_POST(self):
                path = self.path.split("?")[0].rstrip("/")
                if not self._authorized():
                    return self._send(401, {"error": "unauthorized"})
                if path != "/command":
                    return self._send(404, {"error": f"unknown path {path}; POST /command"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict) or not isinstance(body.get("cmd"), str):
                        raise ValueError
                except ValueError:
                    return self._send(400, {"error": 'body must be {"cmd": "<name>", "args": {...}}'})
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
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
