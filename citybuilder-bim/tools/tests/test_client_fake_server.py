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
        self.tasks, self.manual_zones, self.next_id = {}, set(), 0
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
            return [{"zone_id": z, "name": n, "max_crews": 2, "shift_mode": "single", "manual_mode": z in self.manual_zones}
                    for z, n in (("L00-Z1", "Ground zone"), ("L01-Z1", "Level 1 zone"))]
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
        if method == "state.tasks":
            return [t for t in self.tasks.values() if params.get("zone_id") in (None, t["zone_id"])]
        if method.startswith(("manual.", "logic.")):
            return self.handle_manual(method, params)
        raise RpcError(-32601, f"Method not found: {method}")

    # ---- manual sequencing and logic library (tiny in-memory version of docs/06 track A) ----------------
    KNOWN_STEPS = {"CIV-EARTH-CUT", "CIV-PILE-DRIVE", "GEN-SURVEY-SETOUT", "GEN-SURVEY-ASBUILT", "STR-SLAB-FORM",
                   "STR-SLAB-POUR", "GEN-PERMIT-WORK", "GEN-LIFT-PLAN"}
    KNOWN_ELEMENTS = {"E1", "E2", "E3"}
    ZONES = {"L00-Z1", "L01-Z1"}
    RECIPES = {
        "rec_slab_on_grade": {
            "id": "rec_slab_on_grade", "name": "Slab on grade", "sector": "industrial", "tags": ["slab"],
            "summary": "Set out, form, pour and cure a ground slab.", "typical_duration_weeks": [2, 6],
            "prerequisites": {"equipment": ["concrete_pump"], "site": ["access_road"], "permits": []},
            "steps": [{"ref": "GEN-SURVEY-SETOUT", "virtual": True}, {"ref": "STR-SLAB-FORM"},
                      {"ref": "STR-SLAB-POUR", "hold_point": "structural"},
                      {"ref": "GEN-SURVEY-ASBUILT", "virtual": True, "optional": True}],
            "logic": [{"after": "STR-SLAB-POUR", "before": "GEN-SURVEY-ASBUILT", "reason": "cure first", "lag_days": 7}],
            "checks": ["Cube tests"], "references": ["Concrete practice guidance"]},
        "rec_pump_on_plinth": {
            "id": "rec_pump_on_plinth", "name": "Pump set on grouted plinth", "sector": "civil", "tags": ["pump"],
            "summary": "Pump.", "typical_duration_weeks": [3, 3], "steps": [{"ref": "GEN-LIFT-PLAN", "virtual": True}]},
    }

    def _task_view(self, t):
        return dict(t)

    def _new_task(self, params):
        step = params.get("step")
        if not step:
            raise RpcError(-32000, "missing parameter: step")
        if step not in self.KNOWN_STEPS:
            raise RpcError(-32000, f"unknown step: {step}")
        zone = params.get("zone_id", "")
        if zone not in self.ZONES:
            raise RpcError(-32000, f"no such zone: {zone}")
        els = list(params.get("elements") or [])
        for g in els:
            if g not in self.KNOWN_ELEMENTS:
                raise RpcError(-32000, f"unknown element: {g}")
        if not els and not params.get("virtual"):
            raise RpcError(-32000, "a task needs elements, or virtual=true")
        preds = []
        for a in params.get("after") or []:
            if a not in self.tasks:
                raise RpcError(-32000, f"unknown predecessor task: {a}")
            preds.append({"task_id": a, "type": params.get("link_type", "FS"), "lag_days": int(params.get("lag_days", 0))})
        self.next_id += 1
        tid = "M%06d" % self.next_id
        start = max([self.tasks[p["task_id"]]["planned_finish_day"] + p["lag_days"] for p in preds] or [0])
        dur = int(params.get("duration_days") or 2)
        t = {"task_id": tid, "manual_id": tid, "step_id": step, "zone_id": zone, "virtual": bool(params.get("virtual")),
             "origin": "manual", "element_guids": els, "element_guid": els[0] if els else None, "predecessors": preds,
             "state": "ready", "planned_start_day": start, "planned_finish_day": start + dur,
             "duration_days": params.get("duration_days"), "marker": params.get("marker"), "recipe_id": None,
             "frozen": False, "manual_zone": zone in self.manual_zones,
             "flags": {"inspection_type": params.get("hold_point")}}
        self.tasks[tid] = t
        return t

    def handle_manual(self, method, params):
        if method == "manual.set_mode":
            z = params.get("zone_id")
            if z not in self.ZONES:
                raise RpcError(-32000, f"no such zone: {z}")
            (self.manual_zones.add if params.get("on", True) else self.manual_zones.discard)(z)
            return {"ok": True, "zone_id": z, "manual_mode": z in self.manual_zones,
                    "manual_zones": len(self.manual_zones)}
        if method == "manual.add_task":
            t = self._new_task(params)
            return {"ok": True, "task_id": t["task_id"], "task": self._task_view(t)}
        if method == "manual.remove_task":
            tid = params.get("task_id")
            t = self.tasks.pop(tid, None)
            if t is None:
                raise RpcError(-32000, f"no such task: {tid}")
            for o in self.tasks.values():
                for p in list(o["predecessors"]):
                    if p["task_id"] == tid:
                        o["predecessors"].remove(p)
                        if params.get("bridge", True):
                            o["predecessors"] += [dict(q) for q in t["predecessors"]]
            return {"ok": True, "removed": tid}
        if method == "manual.link":
            f, to = params.get("from_id"), params.get("to_id")
            if f not in self.tasks or to not in self.tasks:
                raise RpcError(-32000, f"no such task: {f if f not in self.tasks else to}")
            self.tasks[to]["predecessors"].append({"task_id": f, "type": params.get("type", "FS"),
                                                   "lag_days": int(params.get("lag_days", 0))})
            return {"ok": True, "task": self.tasks[to]}
        if method == "manual.unlink":
            to = params.get("to_id")
            self.tasks[to]["predecessors"] = [p for p in self.tasks[to]["predecessors"] if p["task_id"] != params.get("from_id")]
            return {"ok": True, "task": self.tasks[to]}
        if method == "manual.update_task":
            t = self.tasks.get(params.get("task_id"))
            if t is None:
                raise RpcError(-32000, f"no such task: {params.get('task_id')}")
            t.update(params.get("fields") or {})
            return {"ok": True, "task_id": t["task_id"], "task": t}
        if method == "manual.tasks":
            return [t for t in self.tasks.values() if params.get("zone_id") in (None, t["zone_id"])]
        if method == "manual.export":
            doc = {"schema_version": "1.0", "zones_in_manual_mode": sorted(self.manual_zones),
                   "tasks": [{"id": t["task_id"], "step": t["step_id"], "zone_id": t["zone_id"],
                              "after": [p["task_id"] for p in t["predecessors"]]} for t in self.tasks.values()]}
            if params.get("path"):
                doc["path"] = params["path"]
            return doc
        if method in ("manual.apply_recipe", "logic.apply"):
            r = self.RECIPES.get(params.get("recipe_id"))
            if r is None:
                raise RpcError(-32000, f"unknown recipe: {params.get('recipe_id')}")
            zone = params.get("zone_id") or "L00-Z1"
            if zone not in self.ZONES:
                raise RpcError(-32000, f"no such zone: {zone}")
            created, rows, prev = [], [], None
            for st in r["steps"]:
                if st.get("optional") and not params.get("include_optional"):
                    continue
                t = self._new_task({"step": st["ref"], "zone_id": zone, "virtual": True,
                                    "after": [prev] if prev else []})
                t["origin"], t["recipe_id"] = "recipe", r["id"]
                created.append(t["task_id"])
                rows.append({"key": st["ref"], "ref": st["ref"], "virtual": True, "status": "created", "task_ids": [t["task_id"]]})
                prev = t["task_id"]
            return {"ok": True, "recipe_id": r["id"], "zone_id": zone, "element_guid": params.get("element_guid", ""),
                    "created": created, "reused": [], "links": max(len(created) - 1, 0), "skipped_links": [], "steps": rows}
        if method == "logic.list":
            sec = params.get("sector")
            return [{"id": r["id"], "name": r["name"], "summary": r["summary"], "sector": r["sector"], "tags": r["tags"],
                     "steps": len(r["steps"]), "typical_duration_weeks": r["typical_duration_weeks"]}
                    for r in self.RECIPES.values() if not sec or r["sector"] in (sec, "all")]
        if method == "logic.get":
            r = self.RECIPES.get(params.get("id"))
            if r is None:
                raise RpcError(-32000, f"unknown recipe: {params.get('id')}")
            return r
        if method == "logic.explain":
            if params.get("element_guid"):
                if params["element_guid"] not in self.KNOWN_ELEMENTS:
                    raise RpcError(-32000, f"unknown element: {params['element_guid']}")
                scope, zone = "element", "L00-Z1"
            elif params.get("zone_id") in self.ZONES:
                scope, zone = "zone", params["zone_id"]
            else:
                raise RpcError(-32602, "missing parameter: element_guid or zone_id")
            have = {t["step_id"]: t["task_id"] for t in self.tasks.values() if t["zone_id"] == zone}
            rows = []
            for st in self.RECIPES["rec_slab_on_grade"]["steps"]:
                tid = have.get(st["ref"])
                rows.append({"key": st["ref"], "ref": st["ref"], "name": st["ref"].title(), "virtual": bool(st.get("virtual")),
                             "optional": bool(st.get("optional")), "hold_point": st.get("hold_point"),
                             "status": "missing" if tid is None else ("virtual_present" if st.get("virtual") else "covered"),
                             "task_id": tid, "task_ids": [tid] if tid else []})
            req = [r for r in rows if not (r["optional"] and r["status"] == "missing")]
            cov = {"covered": sum(r["status"] == "covered" for r in req),
                   "virtual_present": sum(r["status"] == "virtual_present" for r in req),
                   "missing": sum(r["status"] == "missing" for r in req), "total": len(req)}
            return {"scope": scope, "zone_id": zone, "element_guid": params.get("element_guid"), "name": "Fake",
                    "none": False, "manual_mode": zone in self.manual_zones,
                    "recipes": [{"recipe_id": "rec_slab_on_grade", "name": "Slab on grade", "matched_by": ["ifc_class"],
                                 "steps": rows, "coverage": cov}]}
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
                "analysis_critical analysis_s_curve analysis_what_if_shift manual_set_mode manual_add_task "
                "manual_update_task manual_remove_task manual_link manual_unlink manual_apply_recipe manual_export "
                "manual_tasks logic_list logic_get logic_explain logic_apply").split()
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


class ManualAndLogicClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)
        self.c = GameClient(self.server.url, timeout=5)
        self.addCleanup(self.c.close)

    def last(self):
        return self.server.game.calls[-1]

    def test_manual_chain_roundtrip(self):
        c = self.c
        self.assertTrue(c.manual_set_mode("L00-Z1", True)["manual_mode"])
        self.assertEqual(self.last(), ("manual.set_mode", {"zone_id": "L00-Z1", "on": True}))
        a = c.manual_add_task("GEN-SURVEY-SETOUT", zone_id="L00-Z1", virtual=True, duration_days=2)
        self.assertEqual(a["task_id"], "M000001")
        self.assertEqual(self.last()[1], {"step": "GEN-SURVEY-SETOUT", "zone_id": "L00-Z1", "virtual": True, "duration_days": 2})
        b = c.manual_add_task("STR-SLAB-FORM", zone_id="L00-Z1", elements=["E1", "E2"], after=["M000001"], lag_days=3)
        self.assertEqual(b["task"]["predecessors"], [{"task_id": "M000001", "type": "FS", "lag_days": 3}])
        self.assertEqual(b["task"]["planned_start_day"], 5)
        self.assertEqual(len(c.manual_tasks("L00-Z1")), 2)
        self.assertEqual(c.manual_tasks("L01-Z1"), [])
        self.assertEqual(len(c.manual_tasks()), 2)
        c.manual_link("M000001", "M000002", "SS", 1)
        self.assertEqual(self.last(), ("manual.link", {"from_id": "M000001", "to_id": "M000002", "type": "SS", "lag_days": 1}))
        c.manual_unlink("M000001", "M000002")
        self.assertEqual(self.last()[0], "manual.unlink")
        c.manual_update_task("M000002", {"note": "x"})
        self.assertEqual(self.last(), ("manual.update_task", {"task_id": "M000002", "fields": {"note": "x"}}))
        self.assertEqual(c.manual_export("/tmp/m.json")["path"], "/tmp/m.json")
        self.assertEqual(c.manual_remove_task("M000001")["removed"], "M000001")
        self.assertEqual(self.last(), ("manual.remove_task", {"task_id": "M000001", "bridge": True}))
        self.assertEqual(c.state_zones()[0]["manual_mode"], True)

    def test_manual_errors(self):
        with self.assertRaises(GameApiError) as cm:
            self.c.manual_add_task("NOPE-STEP", zone_id="L00-Z1", virtual=True)
        self.assertEqual(cm.exception.message, "unknown step: NOPE-STEP")
        with self.assertRaises(GameApiError):
            self.c.manual_add_task("STR-SLAB-POUR", zone_id="L00-Z1")  # neither elements nor virtual
        with self.assertRaises(GameApiError):
            self.c.manual_set_mode("ZZ", True)

    def test_logic_methods(self):
        c = self.c
        rows = c.logic_list()
        self.assertEqual({r["id"] for r in rows}, {"rec_slab_on_grade", "rec_pump_on_plinth"})
        self.assertEqual([r["id"] for r in c.logic_list(sector="civil")], ["rec_pump_on_plinth"])
        self.assertEqual(self.last(), ("logic.list", {"sector": "civil"}))
        self.assertEqual(c.logic_get("rec_slab_on_grade")["steps"][0]["ref"], "GEN-SURVEY-SETOUT")
        ex = c.logic_explain(zone_id="L00-Z1")
        self.assertEqual(ex["recipes"][0]["coverage"]["missing"], 3)
        self.assertEqual(self.last(), ("logic.explain", {"zone_id": "L00-Z1"}))
        self.assertEqual(c.logic_explain(element_guid="E1")["scope"], "element")
        res = c.logic_apply("rec_slab_on_grade", zone_id="L00-Z1")
        self.assertEqual(self.last()[0], "logic.apply")
        self.assertEqual(len(res["created"]), 3)
        res = c.manual_apply_recipe("rec_slab_on_grade", "L01-Z1", include_optional=True)
        self.assertEqual(self.last()[0], "manual.apply_recipe")
        self.assertEqual(len(res["created"]), 4)
        with self.assertRaises(GameApiError):
            c.logic_get("rec_nothing")
        self.assertEqual(c.logic_explain(zone_id="L00-Z1")["recipes"][0]["coverage"]["virtual_present"], 1)


class LogicTextViewTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.game.manual_zones.add("L00-Z1")

    def test_recipes_table(self):
        t = tv.recipes_table(self.game.handle("logic.list", {}, None))
        self.assertIn("rec_slab_on_grade", t)
        self.assertIn("2-6", t)  # typical weeks
        self.assertIn("slab", t)
        self.assertEqual(tv.recipes_table([]), "(no recipes)")

    def test_explain_text(self):
        t = tv.explain_text(self.game.handle("logic.explain", {"zone_id": "L00-Z1"}, None))
        self.assertIn("rec_slab_on_grade", t)
        self.assertIn("0 of 3 required steps", t)
        self.assertIn("[ ] missing", t)
        self.assertIn("hold:structural", t)
        self.assertIn("optional", t)
        self.assertIn("[manual mode]", t)
        self.game.handle("manual.add_task", {"step": "GEN-SURVEY-SETOUT", "zone_id": "L00-Z1", "virtual": True}, None)
        t = tv.explain_text(self.game.handle("logic.explain", {"zone_id": "L00-Z1"}, None))
        self.assertIn("[v] virtual in place", t)
        self.assertIn("M000001", t)
        self.assertIn("no recipe applies", tv.explain_text({"scope": "zone", "zone_id": "Z9", "none": True, "recipes": []}))
        self.assertIsInstance(tv.explain_text(None), str)

    def test_recipe_text(self):
        t = tv.recipe_text(self.game.handle("logic.get", {"id": "rec_slab_on_grade"}, None))
        for needle in ("Slab on grade", "Prerequisites:", "equipment: concrete_pump", "1. GEN-SURVEY-SETOUT (virtual)",
                       "hold point: structural", "optional", "STR-SLAB-POUR -> GEN-SURVEY-ASBUILT", "lag 7 d",
                       "Checks:", "Cube tests", "References:", "2-6 weeks"):
            self.assertIn(needle, t)

    def test_manual_chain_text_order_and_columns(self):
        g = self.game
        g.handle("manual.add_task", {"step": "GEN-SURVEY-SETOUT", "zone_id": "L00-Z1", "virtual": True, "duration_days": 2}, None)
        g.handle("manual.add_task", {"step": "STR-SLAB-FORM", "zone_id": "L00-Z1", "elements": ["E1", "E2"],
                                     "after": ["M000001"], "lag_days": 3, "hold_point": "structural"}, None)
        tasks = g.handle("manual.tasks", {"zone_id": "L00-Z1"}, None)
        t = tv.manual_chain_text(list(reversed(tasks)), "L00-Z1")  # order must not depend on input order
        lines = t.split("\n")
        self.assertIn("2 tasks", lines[0])
        rows = [l for l in lines if l.startswith("M0")]
        self.assertTrue(rows[0].startswith("M000001"))
        self.assertIn("virtual", rows[0])
        self.assertIn("2 el", rows[1])
        self.assertIn("M000001", rows[1])
        self.assertIn("hold:structural", rows[1])
        self.assertRegex(rows[1], r"\b3\b")  # lag
        self.assertIn("(no manual tasks in Z", tv.manual_chain_text([], "Z"))


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
