"""RemoteSession: the Session interface over HTTP, for the app processes of `screenlab-tui --serve` (DESIGN.md L2/L3).

    s = RemoteSession("http://127.0.0.1:8765")        # token from env SCREENLAB_TUI_TOKEN, or token=...
    s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True})
    s.snapshot(); s.view(); s.seq; s.poll(); s.await_jobs(result)

The parent process owns the one `Session` and the control endpoint (control.py); every browser tab runs an app over a
RemoteSession, so all tabs, the chart panel and MCP/curl control show the same scenarios. No Textual import here.

Cheap change detection: a watcher thread long-polls `GET /seq?after=<seq>` and fetches `GET /state` only when the
parent's seq moved; `poll()` just reports whether that happened. After a command the state is refreshed at once, so
the caller reads its own writes. The attributes the app reads (`scenarios`, `selected`, `active_tab`, `analyses`, `mc`,
`theme`, `card()`, ...) come from the cached snapshot, so reading them never touches the network.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

from .session import FINAL, SessionError


class _Card:
    """The two things the app needs from a data card."""

    def __init__(self, line: str, warnings: list, data: dict):
        self._line, self._warnings, self.data = line, list(warnings), data

    def line(self) -> str:
        return self._line

    def warnings(self) -> list:
        return list(self._warnings)

    def to_dict(self) -> dict:
        return self.data


class RemoteSession:
    remote = True

    def __init__(self, url: str, token: str | None = None, watch: bool = True, connect_timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("SCREENLAB_TUI_TOKEN") or None
        self._cv = threading.Condition()
        self._state: dict | None = None
        self._seq = -1
        self._polled = -1                              # the seq last reported by poll()
        self._closed = threading.Event()
        self._local_log: list[str] = []
        self._screens: dict = {}
        self.connected = True
        self.health = self._connect(connect_timeout)
        self.serve = bool(self.health.get("serve"))
        self.home_url = self.health.get("home_url")
        self.lab = self.health.get("lab")
        self._thread = None
        if watch:
            self._thread = threading.Thread(target=self._watch, name="screenlab-remote-watch", daemon=True)
            self._thread.start()

    # ---------------------------------------------------------------- transport
    def _request(self, method: str, path: str, body=None, timeout: float = 30.0):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except Exception:                                    # noqa: BLE001
                msg = str(e.reason)
            raise SessionError(e.code, msg) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.connected = False
            raise SessionError(503, f"cannot reach the screenlab-tui control endpoint {self.url}: {e}") from None

    def _connect(self, timeout: float) -> dict:
        t0, err = time.time(), None
        while time.time() - t0 < timeout:
            try:
                h = self._request("GET", "/health", timeout=5)
                self.connected = True
                return h
            except SessionError as e:
                err = e
                time.sleep(0.3)
        raise err or SessionError(503, f"cannot reach {self.url}")

    # ---------------------------------------------------------------- state cache
    def _refresh(self, timeout: float = 600.0) -> dict:
        """Fetch /state (the parent may be building the lab: the first call can take a while)."""
        st = self._request("GET", "/state", timeout=timeout)
        with self._cv:
            if st.get("seq", 0) >= self._seq or self._state is None:
                self._state, self._seq = st, st.get("seq", 0)
                self._cv.notify_all()
        return st

    def _ensure(self) -> dict:
        if self._state is None:
            self._refresh()
        return self._state

    def _watch(self) -> None:
        while not self._closed.is_set():
            try:
                if self._state is None:
                    self._refresh()
                    continue
                got = self._request("GET", f"/seq?after={self._seq}&timeout=15", timeout=30)
                self.connected = True
                if got.get("seq") != self._seq:
                    self._refresh()
            except SessionError:
                self._closed.wait(1.0)

    @property
    def seq(self) -> int:
        return self._seq

    def poll(self, wait: float = 0.0) -> bool:
        """True once per change of the parent's state. `wait` blocks up to that many seconds for one. Without the
        watcher thread (`watch=False`) this does a single `GET /seq?after=` and refreshes."""
        if self._thread is None:
            try:
                got = self._request("GET", f"/seq?after={self._seq}&timeout={max(wait, 0)}", timeout=wait + 10)
                if got.get("seq") != self._seq or self._state is None:
                    self._refresh()
            except SessionError:
                return False
        else:
            with self._cv:
                if wait > 0 and self._seq == self._polled:
                    self._cv.wait_for(lambda: self._seq != self._polled, wait)
        changed = self._seq != self._polled
        self._polled = self._seq
        return changed

    # ---------------------------------------------------------------- the Session interface
    def command(self, cmd: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        timeout = (float(args.get("timeout") or 120) + 30) if args.get("wait") or cmd == "picks" else 60.0
        res = self._request("POST", "/command", {"cmd": cmd, "args": args}, timeout=timeout)
        if cmd not in ("compare", "view", "snapshot", "history", "jobs", "curves", "screens", "datasets"):
            self._refresh()                                      # read your own writes
        return res

    def snapshot(self) -> dict:
        st = self._refresh()
        return json.loads(json.dumps(st))

    def view(self, tab: str | None = None) -> str:
        q = f"?tab={tab}" if tab else ""
        return self._request("GET", "/view" + q)["view"]

    def await_jobs(self, result: dict, timeout: float = 120) -> dict:
        res = self._request("POST", "/await", {"result": result, "timeout": timeout}, timeout=timeout + 30)
        self._refresh()
        return res

    def close(self) -> None:
        self._closed.set()

    # ---------------------------------------------------------------- what the app reads (from the cached snapshot)
    @property
    def scenarios(self) -> list:
        out = []
        for d in self._ensure()["scenarios"]:
            out.append(SimpleNamespace(id=d["id"], name=d["name"], version=d["version"], label=d["label"],
                                       color=d["color"], params=d["params"], status=d["status"], job=d.get("job"),
                                       diff_line=d.get("diff_from_previous"), error=d.get("error"),
                                       metrics=d.get("metrics") or {}, frozen=bool(d.get("frozen")),
                                       candidate_of=d.get("candidate_of"), hypothesis=d.get("hypothesis"),
                                       trials=d.get("trials"), dsr=d.get("dsr")))
        return out

    @property
    def selected(self):
        return self._ensure()["selected"]

    @property
    def active_tab(self) -> str:
        return self._ensure()["active_tab"]

    @property
    def theme(self) -> str:
        return self._ensure()["theme"]

    @property
    def analyses(self) -> dict:
        return self._ensure()["analyses"]

    @property
    def mc(self) -> dict:
        return self._ensure()["mc"]

    def trials_total(self) -> int:
        return int((self._ensure().get("trials") or {}).get("total") or 0)

    def card(self) -> _Card:
        st = self._ensure()
        return _Card(st["data_line"], st["warnings"], st["data_card"])

    def passive_label(self) -> str:
        return self._ensure()["passive"]["label"]

    def store_path(self):
        return self._ensure()["store"]

    def lab_names(self) -> list:
        return list(self._ensure()["labs"])

    def screen_names(self, lab=None) -> list:
        st = self._ensure()
        if lab in (None, st["lab"]):
            return list(st["screens"])
        if lab not in self._screens:
            self._screens[lab] = self.command("screens", {"lab": lab})["screens"]
        return list(self._screens[lab])

    def active_jobs(self) -> list:
        return [j for j in self._ensure()["jobs"] if j["status"] not in FINAL]

    def data_state(self) -> dict:
        return self._ensure()["data"]

    def curves_data(self) -> dict:
        return self.command("curves")

    def log(self, msg: str) -> None:
        self._local_log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self._local_log[:-100]

    def log_lines(self, n: int = 200) -> list:
        return (list(self._ensure()["log"]) + self._local_log)[-n:]

    def set_theme(self, theme: str, variables: dict | None = None) -> None:
        self.command("set_theme", {"theme": theme, **({"variables": variables} if variables else {})})
