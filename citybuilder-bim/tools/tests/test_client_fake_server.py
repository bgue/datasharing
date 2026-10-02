"""Tests for sitebuilder_client against an in-process fake JSON-RPC WebSocket game server."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import sys
import threading
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from websockets.asyncio.server import serve  # noqa: E402

from sitebuilder_client import GameApiError, GameClient, method_to_wire  # noqa: E402
from sitebuilder_client import __main__ as cli  # noqa: E402
from sitebuilder_client import textviews as tv  # noqa: E402


_QUIET = logging.getLogger("sitebuilder.fakeserver")
_QUIET.addHandler(logging.NullHandler())
_QUIET.propagate = False


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class FakeGame:
    """Tiny in-memory game: just enough state to exercise the protocol."""

    def __init__(self, token=None, event_week=None):
        self.token = token
        self.event_week = event_week
        self.calls = []
        self.reset()

    def reset(self):
        self.week, self.cash, self.loaded, self.pending = 0, 100000, None, None
        self.crews = []
        self.packages = {
            "P00001": {"package_id": "P00001", "name": "L00-Z1 · Foundations · civil", "zone_id": "L00-Z1",
                       "state": "ready", "crews_now": 0, "crew_profile": {"min": 1, "ideal": 2, "max": 3},
                       "total_crew_days": 10, "crew_days_done": 0, "remaining_crew_days": 10,
                       "planned_start_day": 0, "planned_finish_day": 10},
            "P00002": {"package_id": "P00002", "name": "L01-Z1 · Frame", "zone_id": "L01-Z1", "state": "held",
                       "crews_now": 0, "crew_profile": {"min": 1, "ideal": 1, "max": 2}, "total_crew_days": 5,
                       "crew_days_done": 0, "remaining_crew_days": 5, "planned_start_day": 10,
                       "planned_finish_day": 15},
        }

    def summary(self):
        s = {"ok": True, "week": self.week, "day": self.week * 5, "cash": self.cash, "budget": 100000,
             "contract_weeks": 10, "counts": {"ready": 1, "held": 1}, "score": 0}
        if self.pending:
            s["pending_event"] = self.pending
        return s

    def handle(self, method, params, notify):
        """Return the result or raise (code, message)."""
        self.calls.append((method, dict(params)))
        if method == "scenario.list":
            return [{"id": "minimal", "name": "Minimal block", "sector": "healthcare", "difficulty": 1, "tasks": 12}]
        if method == "scenario.load":
            if params.get("id") != "minimal":
                raise RpcError(-32000, f"unknown scenario {params.get('id')}")
            self.reset()
            self.loaded = "minimal"
            return self.summary()
        if method == "state.summary":
            return self.summary()
        if method == "state.crews":
            return {"ok": True, "crews": self.crews, "caps": {"civil": 3}}
        if method == "state.zones":
            return [{"zone_id": "L00-Z1", "name": "Ground zone", "max_crews": 2, "shift_mode": "single"},
                    {"zone_id": "L01-Z1", "name": "Level 1 zone", "max_crews": 2, "shift_mode": "single"}]
        if method == "state.packages":
            return [p for p in self.packages.values() if params.get("zone_id") in (None, p["zone_id"])]
        if method == "state.gantt":
            return [{"package_id": p["package_id"], "zone_id": p["zone_id"], "name": p["name"],
                     "planned_start_day": p["planned_start_day"], "planned_finish_day": p["planned_finish_day"],
                     "progress": 0.0, "state": p["state"]} for p in self.packages.values()]
        if method == "sim.advance":
            n = int(params.get("weeks", 1))
            if self.pending:
                raise RpcError(-32000, "resolve the pending event first")
            for _ in range(n):
                self.week += 1
                self.cash -= 1000 * len(self.crews)
                notify("week.advanced", {"week": self.week})
                if self.event_week == self.week and params.get("stop_on_event", True):
                    self.pending = {"event_id": "rain", "text": "Heavy rain forecast",
                                    "choices": [{"id": "wait", "text": "Wait it out"},
                                                {"id": "pump", "text": "Hire pumps"}]}
                    notify("event.fired", self.pending)
                    break
            return self.summary()
        if method == "sim.resolve_event":
            if not self.pending:
                raise RpcError(-32000, "no pending event")
            self.pending = None
            return self.summary()
        if method == "sim.autopilot":
            res = self.handle("sim.advance", {"weeks": params["weeks"]}, notify)
            self.calls.pop()  # keep the log to the call the client actually made
            return res
        if method == "crew.hire":
            for _ in range(int(params.get("count", 1))):
                self.crews.append({"id": len(self.crews) + 1, "trade": params["trade"], "zone_id": "", "package_id": ""})
            return {"ok": True, "crews": self.crews}
        if method == "crew.assign":
            for c in self.crews:
                if c["id"] == params["crew_id"]:
                    c["zone_id"] = params["zone_id"]
                    return {"ok": True}
            raise RpcError(-32000, f"no such crew {params['crew_id']}")
        if method in ("package.hold", "package.release"):
            p = self.packages.get(params.get("package_id"))
            if not p:
                raise RpcError(-32000, "unknown package")
            p["state"] = "held" if method.endswith("hold") else "ready"
            return p
        if method == "analysis.bottlenecks":
            return {"ok": True, "idle_zones": ["L00-Z1"], "understaffed": [{"package_id": "P00001", "crews_now": 0}]}
        if method == "zone.staff":
            return {"ok": True}
        if method == "plan.export":
            return {"ok": True, "paths": ["/tmp/plan.json"]}
        raise RpcError(-32601, f"Method not found: {method}")


class FakeServer:
    """Runs FakeGame behind a websocket on 127.0.0.1:<free port> in a background thread."""

    def __init__(self, token=None, event_week=None):
        self.game = FakeGame(token, event_week)
        self.url = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _handler(self, ws):
        async def notify(method, params):
            await ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

        async for raw in ws:
            msg = json.loads(raw)
            batch = isinstance(msg, list)
            out = []
            for req in msg if batch else [msg]:
                pending = []
                rid = req.get("id")
                params = dict(req.get("params") or {})
                if self.game.token is not None and params.pop("token", None) != self.game.token:
                    out.append({"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "unauthorized"}})
                    continue
                params.pop("token", None)
                try:
                    res = self.game.handle(req["method"], params, lambda m, p: pending.append((m, p)))
                    resp = {"jsonrpc": "2.0", "id": rid, "result": res}
                except RpcError as e:
                    resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}}
                for m, p in pending:
                    await notify(m, p)
                out.append(resp)
            await ws.send(json.dumps(out if batch else out[0]))

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self):
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", 0, logger=_QUIET) as server:
            port = server.sockets[0].getsockname()[1]
            self.url = f"ws://127.0.0.1:{port}"
            self._ready.set()
            await self._stop.wait()

    def start(self):
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("fake server did not start")
        return self

    def stop(self):
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)

    def client(self, **kw):
        c = GameClient(self.server.url, timeout=5, **kw)
        self.addCleanup(c.close)
        return c

    def test_ids_and_results(self):
        c = self.client()
        s1 = c.scenario_load("minimal")
        s2 = c.state_summary()
        self.assertEqual(s1["week"], 0)
        self.assertEqual(s2["cash"], 100000)
        self.assertEqual(c.scenario_list()[0]["id"], "minimal")
        self.assertEqual([m for m, _ in self.server.game.calls], ["scenario.load", "state.summary", "scenario.list"])

    def test_context_manager_and_wire_names(self):
        with GameClient(self.server.url, timeout=5) as c:
            self.assertTrue(c.connected)
            c.call("state_summary")
            c.call("state.summary")
        self.assertFalse(c.connected)
        self.assertEqual(method_to_wire("procure_order_all_due"), "procure.order_all_due")
        self.assertEqual(method_to_wire("zone_set_shift"), "zone.set_shift")
        self.assertEqual(method_to_wire("sim.advance"), "sim.advance")

    def test_none_params_are_omitted(self):
        c = self.client()
        c.state_packages(zone_id=None)
        self.assertEqual(self.server.game.calls[-1], ("state.packages", {}))
        c.state_packages(zone_id="L00-Z1")
        self.assertEqual(self.server.game.calls[-1], ("state.packages", {"zone_id": "L00-Z1"}))

    def test_request_ids_increment(self):
        c = self.client()
        seen = []
        orig = c._request
        c._request = lambda m, p: seen.append(orig(m, p)["id"]) or {"jsonrpc": "2.0", "id": seen[-1], "method": m, "params": p}
        c.state_summary()
        c.state_summary()
        c.batch(["state.summary", "state.summary"])
        self.assertEqual(seen, [1, 2, 3, 4])

    def test_notifications_captured(self):
        c = self.client()
        c.scenario_load("minimal")
        res = c.sim_advance(weeks=3)
        self.assertEqual(res["week"], 3)
        weeks = [e["params"]["week"] for e in c.events if e["method"] == "week.advanced"]
        self.assertEqual(weeks, [1, 2, 3])
        self.assertEqual(len(c.take_events("week.advanced")), 3)
        self.assertEqual(c.events, [])

    def test_error_mapping(self):
        c = self.client()
        with self.assertRaises(GameApiError) as cm:
            c.call("nope.nothing")
        self.assertEqual(cm.exception.code, -32601)
        self.assertIn("Method not found", cm.exception.message)
        with self.assertRaises(GameApiError) as cm:
            c.scenario_load("zzz")
        self.assertEqual(cm.exception.code, -32000)
        self.assertEqual(cm.exception.message, "unknown scenario zzz")

    def test_crews_and_packages(self):
        c = self.client()
        c.scenario_load("minimal")
        c.crew_hire("civil", 2)
        c.crew_assign(1, "L00-Z1")
        crews = c.state_crews()["crews"]
        self.assertEqual(crews[0]["zone_id"], "L00-Z1")
        self.assertEqual(c.package_hold("P00001")["state"], "held")
        self.assertEqual(c.package_release("P00001")["state"], "ready")
        with self.assertRaises(GameApiError):
            c.package_hold("P99999")

    def test_batch(self):
        c = self.client()
        res = c.batch([("scenario.load", {"id": "minimal"}), ("crew.hire", {"trade": "civil"}), "state.summary",
                       ("bad.method", {}), {"method": "sim.advance", "params": {"weeks": 2}}])
        self.assertEqual(res[0]["week"], 0)
        self.assertEqual(res[1]["crews"][0]["id"], 1)
        self.assertEqual(res[2]["week"], 0)
        self.assertIsInstance(res[3], GameApiError)
        self.assertEqual(res[3].code, -32601)
        self.assertEqual(res[4]["week"], 2)
        self.assertEqual(c.batch([]), [])

    def test_pending_event_flow(self):
        server = FakeServer(event_week=2).start()
        self.addCleanup(server.stop)
        with GameClient(server.url, timeout=5) as c:
            res = c.sim_advance(weeks=5)
            self.assertEqual(res["week"], 2)
            self.assertEqual(res["pending_event"]["event_id"], "rain")
            self.assertIn("event.fired", [e["method"] for e in c.events])
            with self.assertRaises(GameApiError):
                c.sim_advance(1)
            self.assertNotIn("pending_event", c.sim_resolve_event("wait"))

    def test_connection_refused(self):
        c = GameClient("ws://127.0.0.1:1", timeout=1)
        with self.assertRaises(OSError):
            c.state_summary()

    def test_all_api_methods_exist(self):
        from sitebuilder_client.client import API_METHODS
        spec = ("scenario_list scenario_load state_summary state_zones state_packages state_tasks state_crews "
                "state_tiles state_procurement state_gantt sim_advance sim_resolve_event sim_set_speed crew_hire "
                "crew_fire crew_assign tile_place tile_remove equipment_place equipment_remove procure_order "
                "package_release package_hold package_priority zone_set_shift card_list card_get card_apply "
                "card_clear card_save train_apply plan_export save_write save_read zone_staff zone_clear_crews "
                "site_auto_layout procure_order_all_due sim_run_until sim_autopilot analysis_bottlenecks "
                "analysis_critical analysis_s_curve analysis_what_if_shift").split()
        self.assertEqual(sorted(API_METHODS), sorted(spec))
        for name in spec:
            self.assertTrue(callable(getattr(GameClient, name)), name)

    def test_high_level_param_shapes(self):
        c = self.client()
        for fn, args, expect in [
            (c.zone_staff, ("L00-Z1", "max", True), ("zone.staff", {"zone_id": "L00-Z1", "level": "max", "hire": True})),
            (c.sim_autopilot, (3,), ("sim.autopilot", {"weeks": 3, "staff_level": "ideal"})),
        ]:
            with contextlib.suppress(GameApiError):
                fn(*args)
            self.assertEqual(self.server.game.calls[-1], expect)


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer(token="s3cret").start()
        self.addCleanup(self.server.stop)

    def test_token_injected(self):
        with GameClient(self.server.url, token="s3cret", timeout=5) as c:
            self.assertEqual(c.state_summary()["week"], 0)
            self.assertEqual(c.batch(["state.summary"])[0]["week"], 0)

    def test_token_rejected(self):
        for tok in (None, "wrong"):
            with GameClient(self.server.url, token=tok, timeout=5) as c:
                with self.assertRaises(GameApiError) as cm:
                    c.state_summary()
                self.assertEqual(cm.exception.code, -32001)


class TextViewTests(unittest.TestCase):
    PACKAGES = [
        {"package_id": "P00042", "name": "L02-Z3 · MEP rough-in · mechanical", "zone_id": "L02-Z3", "state": "active",
         "crews_now": 2, "crew_profile": {"min": 1, "ideal": 3, "max": 4}, "crew_days_done": 5.5,
         "total_crew_days": 23.5, "remaining_crew_days": 18, "planned_start_day": 140, "planned_finish_day": 152,
         "blocked_reason": "", "behind_takt": True},
        {"package_id": "P00043", "state": "blocked", "blocked_reason": "waiting crane"},
    ]

    def test_summary(self):
        t = tv.summary_text({"week": 4, "day": 20, "cash": 123456.7, "budget": 200000, "contract_weeks": 30,
                             "counts": {"ready": 3, "done": 1}, "score": {"total": 61.5},
                             "pending_event": {"event_id": "e1", "text": "Strike", "choices": [{"id": 0, "text": "Pay"}]}})
        self.assertIn("Week 4/30", t)
        self.assertIn("123,457", t)
        self.assertIn("ready 3", t)
        self.assertIn("PENDING EVENT e1: Strike", t)
        self.assertIn("[0] Pay", t)
        self.assertEqual(tv.summary_text({}).split()[0], "Week")  # tolerant of an empty dict
        self.assertIsInstance(tv.summary_text(None), str)

    def test_tables(self):
        t = tv.packages_table(self.PACKAGES)
        self.assertIn("P00042", t)
        self.assertIn("2 (1/3/4)", t)
        self.assertIn("behind-takt", t)
        self.assertIn("waiting crane", t)
        self.assertEqual(tv.packages_table([]), "(no packages)")
        z = tv.zones_table([{"zone_id": "L00-Z1", "name": "Ground", "crews": 1, "max_crews": 2, "shift_mode": "double",
                             "card": "card_or_room", "station": "Frame"}, {"id": "L01-Z1"}])
        self.assertIn("1/2", z)
        self.assertIn("double", z)
        self.assertIn("L01-Z1", z)

    def test_bottlenecks(self):
        t = tv.bottlenecks_text({"idle_zones": ["L00-Z1"], "understaffed": [{"package_id": "P1", "crews_now": 0}],
                                 "late_orders": []})
        self.assertIn("no crews", t)
        self.assertIn("L00-Z1", t)
        self.assertIn("P1 crews_now=0", t)
        self.assertNotIn("Late", t)
        self.assertEqual(tv.bottlenecks_text({}), "No bottlenecks.")

    BARS = [
        {"package_id": "P1", "zone_id": "Z1", "name": "Z1 · Dig", "planned_start_day": 0, "planned_finish_day": 25,
         "progress": 0.5, "state": "active"},
        {"package_id": "P2", "zone_id": "Z1", "name": "Z1 · Pour", "planned_start_day": 25, "planned_finish_day": 50,
         "progress": 0, "state": "held"},
        {"package_id": "P3", "zone_id": "Z2", "name": "Z2 · Frame", "planned_start_day": 10, "planned_finish_day": 40,
         "progress": 0, "state": "understaffed"},
    ]

    def test_gantt_markers_and_width(self):
        t = tv.gantt_text(self.BARS, 0, 9, width=80, current_week=3)
        lines = t.split("\n")
        self.assertTrue(all(len(l) <= 80 for l in lines), max(len(l) for l in lines))
        rows = {l.split()[0]: l for l in lines if l.startswith("P")}
        self.assertIn("#", rows["P1"])
        self.assertIn("-", rows["P1"])
        self.assertIn("=", rows["P2"])
        self.assertIn(" H ", rows["P2"])
        self.assertIn(" ! ", rows["P3"])
        self.assertIn("|", t)
        self.assertNotIn("#", rows["P2"])
        # P1 spans 5 weeks of a 10-week window: half of its cells are done
        bar1 = rows["P1"][rows["P1"].index("#"):]
        self.assertAlmostEqual(bar1.count("#"), bar1.count("-"), delta=1)
        self.assertIn("planned", lines[-1])

    def test_gantt_width_narrow_and_wide(self):
        for w in (40, 120):
            t = tv.gantt_text(self.BARS, 0, 9, width=w)
            self.assertTrue(all(len(l) <= max(w, 80) for l in t.split("\n")[:-1]), w)
        # a wider chart gives longer bar rows
        short = [l for l in tv.gantt_text(self.BARS, 0, 9, width=50).split("\n") if l.startswith("P1")][0]
        long = [l for l in tv.gantt_text(self.BARS, 0, 9, width=120).split("\n") if l.startswith("P1")][0]
        self.assertGreater(len(long), len(short))

    def test_gantt_zone_rows_defaults_and_tolerance(self):
        t = tv.gantt_text(self.BARS, group="zone")
        rows = [l for l in t.split("\n") if l.startswith("Z")]
        self.assertEqual(len(rows), 2)
        self.assertEqual(tv.gantt_text([]), "(no gantt bars)")
        self.assertIsInstance(tv.gantt_text([{"package_id": "X"}, {"name": "n", "planned_start_day": 3}]), str)
        done = tv.gantt_text([{"package_id": "D", "planned_start_day": 0, "planned_finish_day": 10, "state": "done",
                               "progress": 1}], 0, 3)
        self.assertNotIn("=", done.split("\n")[1])
        self.assertIn("#", done)


class CliTests(unittest.TestCase):
    def test_parse_args(self):
        a = cli.build_parser().parse_args(["sim.advance", '{"weeks": 2}', "--url", "ws://h:1", "--token", "t"])
        self.assertEqual((a.method, a.url, a.token), ("sim.advance", "ws://h:1", "t"))
        self.assertEqual(cli.parse_params(a.params), {"weeks": 2})
        a = cli.build_parser().parse_args(["state_summary"])
        self.assertEqual(cli.parse_params(a.params), {})
        with self.assertRaises(ValueError):
            cli.parse_params("[1]")

    def test_cli_runs_against_fake(self):
        server = FakeServer(token="tk").start()
        self.addCleanup(server.stop)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["sim_advance", '{"weeks": 2}', "--url", server.url, "--token", "tk"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["week"], 2)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["state.summary", "--url", server.url, "--token", "bad"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(buf.getvalue())["error"]["code"], -32001)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["x", "{bad"]), 2)
            self.assertEqual(cli.main(["state.summary", "--url", "ws://127.0.0.1:1", "--timeout", "1"]), 3)


if __name__ == "__main__":
    unittest.main()
