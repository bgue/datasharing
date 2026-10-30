"""Shared-session stress test: one parent Session behind a ControlServer, two RemoteSession clients and four
threads interleaving add_scenario / set_params / run / compare for about ten seconds on the small synthetic lab.

Checks: no exception escapes, the final snapshot is consistent (every scenario label once, ids unique, versions
contiguous per family, seq never goes backwards), and the control endpoint keeps answering while it runs.

    python -m pytest screenlab/tests/test_tui_stress.py -q
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import Counter

import pytest

from screenlab import Lab
from screenlab.server.registry import LabRegistry
from screenlab.tui.control import ControlServer, HeadlessHost
from screenlab.tui.remote import RemoteSession
from screenlab.tui.session import Session, SessionError

pytestmark = pytest.mark.slow

DURATION = 10.0
SCREENS = ("dogs", "momentum_6m")


def _health(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
        return json.loads(r.read())


def test_shared_session_survives_interleaved_clients():
    reg = LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)},
                      default="synthetic", mode="thread")
    session = Session(reg)
    srv = ControlServer(HeadlessHost(session), session, port=0, serve_url="http://localhost:8000").start()
    url = f"http://127.0.0.1:{srv.port}"
    clients = [RemoteSession(url), RemoteSession(url)]
    errors: list[BaseException] = []
    counts: Counter = Counter()
    seqs: list[int] = []                      # seq as seen by the monitor, in time order
    latencies: list[float] = []
    stop = threading.Event()
    deadline = time.monotonic() + DURATION

    def worker(k: int) -> None:
        c = clients[k % 2]                    # two threads share each client
        try:
            cur = c.command("add_scenario", {"screen": SCREENS[k % 2], "name": f"w{k}", "run": True})["scenario"]["id"]
            counts["add_scenario"] += 1
            n = 0
            while time.monotonic() < deadline:
                n += 1
                step = n % 4
                if step == 1:
                    out = c.command("set_params", {"scenario": cur, "params": {"cost_bps": 10 + k * 1000 + n},
                                                   "run": True})
                    cur = out["scenario"]["id"]
                    counts["set_params"] += 1
                elif step == 2:
                    c.command("run", {"scenario": cur})
                    counts["run"] += 1
                elif step == 3:
                    rows = c.command("compare")["rows"]
                    assert rows and rows[0]["label"] == session.passive_label()
                    counts["compare"] += 1
                else:
                    c.command("add_scenario", {"screen": SCREENS[(k + n) % 2], "name": f"w{k}x", "run": True})
                    counts["add_scenario"] += 1
                time.sleep(0.02)
        except BaseException as e:            # noqa: BLE001  (collected, asserted below)
            errors.append(e)

    def monitor() -> None:
        try:
            while not stop.is_set():
                t0 = time.monotonic()
                h = _health(srv.port)
                latencies.append(time.monotonic() - t0)
                assert h.get("ok", True) is not False
                seqs.append(session.seq)
                time.sleep(0.1)
        except BaseException as e:            # noqa: BLE001
            errors.append(e)

    mon = threading.Thread(target=monitor, daemon=True)
    workers = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(4)]
    try:
        mon.start()
        for t in workers:
            t.start()
        for t in workers:
            t.join(DURATION + 60)
        assert not any(t.is_alive() for t in workers), "a worker thread hung"
        stop.set()
        mon.join(15)

        assert not errors, errors
        assert all(counts[k] > 0 for k in ("add_scenario", "set_params", "run", "compare")), dict(counts)

        # the endpoint still answers after the storm, and the jobs drain
        assert _health(srv.port)
        for c in clients:
            for _ in range(600):
                if not c.active_jobs():
                    break
                time.sleep(0.1)
                c.snapshot()
        snap = clients[0].snapshot()
        assert snap == json.loads(json.dumps(snap))

        labels = [s["label"] for s in snap["scenarios"]]
        assert len(labels) == len(set(labels)), [k for k, v in Counter(labels).items() if v > 1]
        ids = [s["id"] for s in snap["scenarios"]]
        assert len(ids) == len(set(ids))
        fams: dict[str, list[int]] = {}
        for s in snap["scenarios"]:
            fams.setdefault(s["name"], []).append(s["version"])
        for name, vs in fams.items():
            assert sorted(vs) == list(range(1, len(vs) + 1)), (name, vs)       # every version present once
        assert snap["seq"] == session.seq == clients[1].snapshot()["seq"]
        assert seqs == sorted(seqs) and seqs[-1] <= snap["seq"]               # seq is monotonic
        assert [s["label"] for s in snap["scenarios"]] == [x.label for x in session.scenarios]
        assert len(snap["scenarios"]) >= counts["add_scenario"] + counts["set_params"]
        assert max(latencies) < 5.0, max(latencies)                            # control endpoint stayed responsive
        c = clients[0].command("compare")
        assert c["rows"][0]["label"] == session.passive_label()
    finally:
        stop.set()
        for c in clients:
            c.close()
        srv.stop()
        if reg._jm is not None:
            reg._jm.shutdown(wait=False)
