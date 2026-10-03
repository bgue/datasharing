"""End-to-end test against the real game (headless Godot). Skipped unless GODOT_BIN is set and the game's
control API exists (godot/scripts/api/)."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from sitebuilder_client import GameClient  # noqa: E402
from sitebuilder_client import textviews as tv  # noqa: E402
from sitebuilder_mcp import launch_game  # noqa: E402

REPO = TOOLS.parent
API_DIR = REPO / "godot" / "scripts" / "api"
SKIP = ("integration test needs the real game: set GODOT_BIN to a Godot 4 binary and implement godot/scripts/api/ "
        "(GODOT_BIN=%r, api dir exists: %s)" % (os.environ.get("GODOT_BIN"), API_DIR.is_dir()))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


try:
    import ifcopenshell  # noqa: F401
    HAVE_IFC = True
except ImportError:
    HAVE_IFC = False


@unittest.skipUnless(HAVE_IFC, "ifc_to_bundle smoke test needs ifcopenshell (pip install ifcopenshell)")
class RealPipelineTests(unittest.TestCase):
    """ifc_to_bundle against the real bimseq pipeline (no game needed) on a tiny generated IFC."""

    def test_tiny_ifc_to_bundle(self):
        import json
        from unittest import mock
        import ifcopenshell.api as api
        import numpy
        from sitebuilder_mcp import server
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        f = api.run("project.create_file")
        proj = api.run("root.create_entity", f, ifc_class="IfcProject", name="Tiny")
        api.run("unit.assign_unit", f)
        site, bld, st = (api.run("root.create_entity", f, ifc_class=c, name=c) for c in
                         ("IfcSite", "IfcBuilding", "IfcBuildingStorey"))
        api.run("aggregate.assign_object", f, products=[site], relating_object=proj)
        api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
        api.run("aggregate.assign_object", f, products=[st], relating_object=bld)
        ctx = api.run("context.add_context", f, context_type="Model")
        body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                       target_view="MODEL_VIEW", parent=ctx)
        for i in range(6):
            e = api.run("root.create_entity", f, ifc_class="IfcSlab" if i % 2 else "IfcColumn", name=f"E{i}")
            api.run("spatial.assign_container", f, products=[e], relating_structure=st)
            api.run("geometry.edit_object_placement", f, product=e,
                    matrix=numpy.array([[1, 0, 0, 6.0 * i], [0, 1, 0, 3], [0, 0, 1, 0], [0, 0, 0, 1.0]]))
            rep = api.run("geometry.add_wall_representation", f, context=body, length=3, height=3, thickness=0.3)
            api.run("geometry.assign_representation", f, product=e, representation=rep)
        ifc = tmp / "tiny.ifc"
        f.write(str(ifc))
        env = {"SITEBUILDER_SCENARIOS_DIR": str(tmp / "scenarios"), "SITEBUILDER_WORK_DIR": str(tmp / "work")}
        with mock.patch.dict(os.environ, env):
            text = server.ifc_to_bundle(str(ifc), sector="industrial", scenario_id="tiny_smoke", load=False)
        self.assertIn("[5/5] copy: ok", text)
        seq = json.loads((tmp / "scenarios" / "tiny_smoke" / "sequence.json").read_text())
        self.assertEqual(seq["scenario"]["id"], "tiny_smoke")
        self.assertGreater(len(seq["tasks"]), 0)


@unittest.skipUnless(os.environ.get("GODOT_BIN") and API_DIR.is_dir(), SKIP)
class RealGameTests(unittest.TestCase):
    def test_play_minimal_to_completion(self):
        port = free_port()
        proc = launch_game.launch_game("minimal", port, wait=60)
        self.addCleanup(launch_game.stop_game, proc)
        with GameClient(f"ws://127.0.0.1:{port}", timeout=120) as c:
            ids = [s["id"] for s in c.scenario_list()]
            self.assertIn("minimal", ids)
            summary = c.scenario_load("minimal")
            self.assertEqual(summary.get("week", 0), 0)
            c.site_auto_layout()
            for zone in c.state_zones():
                for trade in sorted({p["trade"] for p in c.state_packages(zone_id=zone.get("zone_id", zone.get("id")))}):
                    try:
                        c.crew_hire(trade, 1)
                    except Exception:  # hire caps are fine to hit
                        pass
            for zone in c.state_zones():
                c.zone_staff(zone.get("zone_id", zone.get("id")), "ideal")
            summary = c.sim_autopilot(30)
            for _ in range(20):  # resolve events until the level is over
                ev = summary.get("pending_event")
                if not ev:
                    break
                summary = c.sim_resolve_event(ev["choices"][0].get("id", 0) if ev.get("choices") else 0)
            if not summary.get("finished") and not any(e["method"] == "level.finished" for e in c.events):
                summary = c.sim_autopilot(60)
            finished = summary.get("finished") or any(e["method"] == "level.finished" for e in c.events)
            self.assertTrue(finished, "level did not finish: " + tv.summary_text(summary))
            with tempfile.TemporaryDirectory() as d:
                res = c.plan_export(os.path.join(d, "plan.json"))
                self.assertTrue(res.get("ok", True), res)


@unittest.skipUnless(os.environ.get("GODOT_BIN") and API_DIR.is_dir(), SKIP)
class RealGameManualTests(unittest.TestCase):
    """Logic library and manual sequencing through the MCP tool functions (docs/06 track A)."""

    def setUp(self):
        from unittest import mock
        from sitebuilder_mcp import server
        self.server = server
        demo = REPO / "godot" / "scenarios" / "healthcare_manual_demo"
        self.scenario = "healthcare_manual_demo" if demo.is_dir() else "minimal"
        self.port = free_port()
        proc = launch_game.launch_game(self.scenario, self.port, wait=90)
        self.addCleanup(launch_game.stop_game, proc)
        env = mock.patch.dict(os.environ, {"SITEBUILDER_URL": f"ws://127.0.0.1:{self.port}"})
        env.start()
        self.addCleanup(env.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)
        self.c = server.get_client()

    @staticmethod
    def _zid(z):
        return z.get("zone_id", z.get("id"))

    def test_explain_author_and_play(self):
        srv, c = self.server, self.c
        zones = c.state_zones()
        tasks = c.state_tasks()
        # explain_installation on a zone
        explained = None
        for z in zones:
            res = c.logic_explain(zone_id=self._zid(z))
            if not res.get("none"):
                explained = self._zid(z)
                break
        if explained is None:
            self.skipTest("no zone of %s has an applicable recipe" % self.scenario)
        text = srv.explain_installation(zone_id=explained)
        self.assertIn("What is needed for zone " + explained, text)
        self.assertRegex(text, r"\[(x| |v)\]")
        self.assertIn("rec_", text)
        self.assertIn("rec_", srv.list_recipes())
        rid = c.logic_explain(zone_id=explained)["recipes"][0]["recipe_id"]
        self.assertIn("Steps:", srv.get_recipe(rid))

        # a free zone (not in manual mode) with at least two distinct element-bound steps
        by_zone: dict = {}
        for t in tasks:
            if not t.get("virtual") and t.get("element_guid") and not t.get("manual_zone"):
                by_zone.setdefault(t["zone_id"], {}).setdefault(t["step_id"], t["element_guid"])
        free = [self._zid(z) for z in zones if not z.get("manual_mode") and len(by_zone.get(self._zid(z), {})) >= 2]
        self.assertTrue(free, "no free zone with two element-bound steps")
        zone = free[0]
        (s1, g1), (s2, g2) = list(by_zone[zone].items())[:2]
        virtual_steps = [t["step_id"] for t in tasks if t.get("virtual")] or ["GEN-SURVEY-SETOUT"]
        vstep = "GEN-SURVEY-SETOUT" if "GEN-SURVEY-SETOUT" in virtual_steps else virtual_steps[0]
        out = srv.author_manual_chain(zone, [
            {"step": vstep, "duration_days": 1},                   # virtual: no elements
            {"step": s1, "elements": [g1]},
            {"step": s2, "elements": [g2], "lag_days": 1},
        ])
        self.assertIn("Authored 3 tasks in " + zone, out)
        chain = c.manual_tasks(zone)
        ours = [t for t in chain if t.get("origin") == "manual" and t.get("runtime_added")]
        self.assertEqual(len(ours), 3, out)
        ids = {t["task_id"] for t in ours}
        self.assertEqual([t["virtual"] for t in sorted(ours, key=lambda t: t["task_id"])], [True, False, False])
        self.assertTrue(next(z for z in c.state_zones() if self._zid(z) == zone)["manual_mode"])
        self.assertIn("manual mode ON", srv.list_manual_chain(zone))

        for trade in sorted({t["trade"] for t in ours}):
            try:
                c.crew_hire(trade, 1)
            except Exception:  # caps are fine to hit
                pass
        srv.staff_zone(zone, "ideal", hire=True)
        srv.autopilot(10)
        for _ in range(10):  # an event with choices stops the autopilot: take the first choice
            ev = (c.state_summary() or {}).get("pending_event")
            if not ev:
                break
            srv.resolve_event(str((ev.get("choices") or [{"id": 0}])[0].get("id", 0)))
            srv.autopilot(10)

        live = {t["task_id"]: t for t in c.state_tasks(zone_id=zone)}
        self.assertTrue(ids <= set(live), "authored tasks missing from state.tasks")
        for tid in ids:
            self.assertEqual(live[tid]["origin"], "manual")
            self.assertTrue(live[tid]["manual_zone"])
        self.assertTrue(any(live[t]["state"] != "BLOCKED" or live[t].get("progress") for t in ids))
        summary = c.state_summary()
        self.assertGreaterEqual(summary.get("manual_zones", 0), 1)
        self.assertGreaterEqual(summary.get("manual_tasks", 0), 3)
        self.assertIn("Manual sequence:", srv.export_manual_sequence())


@unittest.skipUnless(os.environ.get("GODOT_BIN") and API_DIR.is_dir(), SKIP)
class RealGameViewTests(unittest.TestCase):
    """Kit installations and the heat view of industrial_standard through the MCP tool functions."""

    def setUp(self):
        from unittest import mock
        from sitebuilder_mcp import server
        self.server = server
        self.port = free_port()
        proc = launch_game.launch_game("industrial_standard", self.port, wait=120)
        self.addCleanup(launch_game.stop_game, proc)
        env = mock.patch.dict(os.environ, {"SITEBUILDER_URL": f"ws://127.0.0.1:{self.port}"})
        env.start()
        self.addCleanup(env.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)

    def test_installations_and_heat(self):
        srv = self.server
        text = srv.list_installations(limit=5)
        self.assertRegex(text, r"\d+ installations, \d+ complete")
        self.assertIn("kit", text.split("\n")[0])
        raw = srv.get_client().view_installations()
        self.assertGreater(raw["count"], 0)
        self.assertIn("installation #0", srv.jump_to_installation(0))
        self.assertIn("ON", srv.show_heat(True))
        before = srv.heat_map()
        self.assertTrue(before.startswith("Heat "), before)
        self.assertIn("rework", before)
        srv.highlight_elements([])
        c = srv.get_client()
        c.site_auto_layout()
        for zone in c.state_zones():
            srv.staff_zone(zone.get("zone_id", zone.get("id")), "ideal", hire=True)
        srv.autopilot(8)
        after = srv.heat_map()
        grid = "".join(l.split(None, 1)[1] for l in after.split("\n")[1:-1] if l.strip() and l.split(None, 1)[1:])
        self.assertRegex(grid, r"[1-9#]", "no progress shows in the heat grid after 8 autopilot weeks:\n" + after)


if __name__ == "__main__":
    unittest.main()
