"""Tests for the sitebuilder_mcp server tools, resources and prompt against the fake game server."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
for p in (TOOLS, TOOLS / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from sitebuilder_client import GameClient  # noqa: E402
from sitebuilder_mcp import launch_game, server  # noqa: E402
from test_client_fake_server import FakeServer  # noqa: E402

SPEC_TOOLS = ["load_scenario", "get_summary", "list_zones", "list_packages", "list_bottlenecks", "staff_zone",
              "assign_crew", "hire_crews", "release_package", "hold_package", "apply_card", "apply_train",
              "set_shift", "order_due_procurement", "auto_layout_site", "advance_weeks", "run_until", "autopilot",
              "resolve_event", "export_plan", "gantt_text",
              # docs/06 track A
              "explain_installation", "list_recipes", "get_recipe", "apply_recipe", "set_manual_mode",
              "list_manual_chain", "add_manual_task", "link_manual_tasks", "remove_manual_task",
              "export_manual_sequence", "author_manual_chain",
              # 3D view, installations, areas, IFC
              "show_heat", "heat_map", "highlight_elements", "list_installations", "jump_to_installation",
              "list_areas", "jump_to_area", "ifc_to_bundle"]


def run(coro):
    return asyncio.run(coro)


class RegistryTests(unittest.TestCase):
    def test_tool_names_match_spec(self):
        names = [t.name for t in run(server.mcp.list_tools())]
        self.assertEqual(sorted(names), sorted(SPEC_TOOLS))

    def test_tools_have_docstrings_and_typed_schemas(self):
        for t in run(server.mcp.list_tools()):
            self.assertGreater(len(t.description or ""), 40, t.name)
            self.assertEqual(t.inputSchema["type"], "object")
        tools = {t.name: t for t in run(server.mcp.list_tools())}
        self.assertEqual(tools["advance_weeks"].inputSchema["properties"]["weeks"]["type"], "integer")
        self.assertEqual(tools["staff_zone"].inputSchema["required"], ["zone_id"])

    def test_resources_and_prompt(self):
        uris = {str(r.uri) for r in run(server.mcp.list_resources())}
        self.assertEqual(uris, {"sitebuilder://summary", "sitebuilder://zones", "sitebuilder://gantt", "sitebuilder://logic"})
        tmpl = {r.uriTemplate for r in run(server.mcp.list_resource_templates())}
        self.assertEqual(tmpl, {"sitebuilder://zone/{zone_id}", "sitebuilder://packages/{zone_id}",
                                "sitebuilder://logic/{recipe_id}", "sitebuilder://manual/{zone_id}"})
        prompts = run(server.mcp.list_prompts())
        self.assertEqual(sorted(p.name for p in prompts), ["plan_installation", "plan_next_week"])
        pi = run(server.mcp.get_prompt("plan_installation", {"zone_or_element": "L00-Z1"})).messages[0].content.text
        for needle in ("explain_installation", "apply_recipe", "author_manual_chain", "staff_zone", "L00-Z1"):
            self.assertIn(needle, pi)
        text = run(server.mcp.get_prompt("plan_next_week", {"focus": "cash"}))
        body = text.messages[0].content.text
        for needle in ("get_summary", "list_bottlenecks", "gantt_text", "Focus: cash"):
            self.assertIn(needle, body)


class FakeBackedTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer(event_week=3).start()
        self.addCleanup(self.fake.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)
        patcher = mock.patch.object(server, "_new_client", lambda: GameClient(self.fake.url, timeout=5))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lazy_connection(self):
        self.assertEqual(self.fake.game.calls, [])
        self.assertIsNone(server._client)
        server.get_summary()
        self.assertIsNotNone(server._client)

    def test_get_summary(self):
        server.load_scenario("minimal")
        text = server.get_summary()
        self.assertIn("Week 0/10", text)
        self.assertIn("cash 100,000", text)
        self.assertIn("Raw JSON", text)

    def test_load_scenario_list_and_error(self):
        self.assertIn("minimal: Minimal block", server.load_scenario())
        with self.assertRaises(ToolError) as cm:
            server.load_scenario("nonexistent")
        self.assertIn("unknown scenario", str(cm.exception))

    def test_advance_weeks_and_pending_event(self):
        server.load_scenario("minimal")
        t1 = server.advance_weeks(2)
        self.assertIn("Week 2/10", t1)
        self.assertIn("week.advanced x2", t1)
        t2 = server.advance_weeks(5)  # event fires in week 3
        self.assertIn("PENDING EVENT rain: Heavy rain forecast", t2)
        self.assertIn("[wait] Wait it out", t2)
        self.assertIn("[pump] Hire pumps", t2)
        self.assertIn("resolve_event", t2)
        self.assertIn("Week 3", t2)
        with self.assertRaises(ToolError):
            server.advance_weeks(1)
        t3 = server.resolve_event("wait")
        self.assertNotIn("PENDING EVENT", t3)

    def test_autopilot_reports_event(self):
        server.load_scenario("minimal")
        self.assertIn("PENDING EVENT", server.autopilot(10))

    def test_gantt_text(self):
        server.load_scenario("minimal")
        t = server.gantt_text()
        self.assertIn("P00001", t)
        self.assertIn("P00002", t)
        self.assertIn(" H ", t)  # P00002 is held
        self.assertIn("now (w0)", t)
        z = server.gantt_text(from_week=0, to_week=6, width=60, group_by="zone")
        self.assertIn("L00-Z1", z)
        self.assertIn("L01-Z1", z)
        self.assertIn("sitebuilder://gantt", [str(r.uri) for r in run(server.mcp.list_resources())])
        self.assertIn("L00-Z1", server.gantt_resource())

    def test_crew_package_and_listing_tools(self):
        server.load_scenario("minimal")
        self.assertIn("Hired 2 x civil", server.hire_crews("civil", 2))
        self.assertIn("Crew 1 -> L00-Z1", server.assign_crew("1", "L00-Z1"))
        self.assertIn("1=civil@L00-Z1", server.get_summary())
        self.assertIn("held", server.hold_package("P00001"))
        self.assertIn("ready", server.release_package("P00001"))
        self.assertIn("P00001", server.list_packages(zone_id="L00-Z1"))
        self.assertNotIn("P00002", server.list_packages(zone_id="L00-Z1"))
        self.assertIn("L01-Z1", server.list_zones())
        self.assertIn("L00-Z1", server.list_bottlenecks())
        self.assertIn("Staffed L00-Z1 to max", server.staff_zone("L00-Z1", "max"))
        self.assertIn("Plan exported", server.export_plan())
        with self.assertRaises(ToolError):
            server.assign_crew("99", "L00-Z1")
        with self.assertRaises(ToolError):
            server.run_until()  # no condition

    def test_unknown_methods_surface_as_tool_errors(self):
        # the fake has no card.apply: the game error must reach the model as a ToolError, not crash
        with self.assertRaises(ToolError) as cm:
            server.apply_card("c", "L00-Z1")
        self.assertIn("Method not found", str(cm.exception))

    def test_call_tool_through_registry(self):
        server.load_scenario("minimal")
        content, _ = run(server.mcp.call_tool("advance_weeks", {"weeks": 1}))
        self.assertIn("Week 1/10", content[0].text)

    def test_resources(self):
        server.load_scenario("minimal")
        self.assertIn("Week 0", run(server.mcp.read_resource("sitebuilder://summary"))[0].content)
        self.assertIn("L00-Z1", run(server.mcp.read_resource("sitebuilder://zones"))[0].content)
        self.assertIn("Ground zone", run(server.mcp.read_resource("sitebuilder://zone/L00-Z1"))[0].content)
        self.assertIn("P00002", run(server.mcp.read_resource("sitebuilder://packages/L01-Z1"))[0].content)

    def test_reconnects_after_drop(self):
        server.load_scenario("minimal")
        server.get_client()._ws.close()  # simulate a dead connection
        self.assertIn("Week 0", server.get_summary())


class ManualToolTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer().start()
        self.addCleanup(self.fake.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)
        patcher = mock.patch.object(server, "_new_client", lambda: GameClient(self.fake.url, timeout=5))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = self.fake.game

    def test_explain_installation(self):
        t = server.explain_installation(zone_id="L00-Z1")
        self.assertIn("rec_slab_on_grade", t)
        self.assertIn("[ ] missing", t)
        self.assertIn("Legend", t)
        self.assertIn("element E1", server.explain_installation(element_guid="E1"))
        with self.assertRaises(ToolError):
            server.explain_installation()
        with self.assertRaises(ToolError) as cm:
            server.explain_installation(element_guid="nope")
        self.assertIn("unknown element", str(cm.exception))

    def test_recipes(self):
        t = server.list_recipes()
        self.assertIn("rec_slab_on_grade", t)
        self.assertIn("rec_pump_on_plinth", t)
        self.assertNotIn("rec_slab_on_grade", server.list_recipes("civil"))
        g = server.get_recipe("rec_slab_on_grade")
        self.assertIn("Prerequisites:", g)
        self.assertIn("Cube tests", g)
        with self.assertRaises(ToolError):
            server.get_recipe("rec_missing")

    def test_apply_recipe(self):
        t = server.apply_recipe("rec_slab_on_grade", zone_id="L00-Z1")
        self.assertIn("3 tasks created", t)
        self.assertIn("2 links", t)
        self.assertIn("STR-SLAB-POUR", t)
        t2 = server.apply_recipe("rec_slab_on_grade", zone_id="L01-Z1", include_optional=True)
        self.assertIn("4 tasks created", t2)
        self.assertIn(("manual.apply_recipe", {"recipe_id": "rec_slab_on_grade", "include_optional": True,
                                               "zone_id": "L01-Z1"}), self.game.calls)
        with self.assertRaises(ToolError):
            server.apply_recipe("rec_slab_on_grade")
        with self.assertRaises(ToolError):
            server.apply_recipe("rec_nope", zone_id="L00-Z1")
        self.assertIn("[v] virtual in place", server.explain_installation(zone_id="L00-Z1"))

    def test_manual_mode_add_link_remove_export(self):
        self.assertIn("manual mode ON", server.set_manual_mode("L00-Z1", True))
        a = server.add_manual_task("GEN-SURVEY-SETOUT", "L00-Z1")  # no elements -> virtual by default
        self.assertIn("Added M000001 (GEN-SURVEY-SETOUT, virtual)", a)
        self.assertTrue(self.game.tasks["M000001"]["virtual"])
        b = server.add_manual_task("STR-SLAB-FORM", "L00-Z1", elements=["E1"], after=["M000001"], lag_days=2)
        self.assertIn("1 elements", b)
        self.assertFalse(self.game.tasks["M000002"]["virtual"])
        self.assertEqual(self.game.tasks["M000002"]["predecessors"][0]["lag_days"], 2)
        c = server.add_manual_task("STR-SLAB-POUR", "L00-Z1", elements=["E1"], hold_point="structural")
        self.assertIn("M000003", c)
        self.assertIn("Linked M000002 -FS+7-> M000003", server.link_manual_tasks("M000002", "M000003", "FS", 7))
        self.assertIn("-SS->", server.link_manual_tasks("M000001", "M000003", "SS"))
        chain = server.list_manual_chain("L00-Z1")
        self.assertIn("manual mode ON", chain)
        self.assertIn("3 tasks", chain)
        self.assertIn("hold:structural", chain)
        self.assertIn("chain bridged", server.remove_manual_task("M000002"))
        self.assertNotIn("M000002", server.list_manual_chain("L00-Z1").split("\n", 1)[1])
        self.assertEqual(len(self.game.tasks), 2)
        ex = server.export_manual_sequence("/tmp/x.json")
        self.assertIn("2 tasks", ex)
        self.assertIn("L00-Z1", ex)
        self.assertIn("/tmp/x.json", ex)
        with self.assertRaises(ToolError):
            server.add_manual_task("BAD-STEP", "L00-Z1")
        with self.assertRaises(ToolError):
            server.link_manual_tasks("M000001", "M999999")
        self.assertIn("OFF", server.set_manual_mode("L00-Z1", False))
        self.assertIn("(no manual tasks", server.list_manual_chain("L01-Z1"))

    def test_author_manual_chain(self):
        t = server.author_manual_chain("L01-Z1", [
            {"step": "CIV-EARTH-CUT", "elements": ["E1"], "duration_days": 3},
            {"step": "CIV-PILE-DRIVE", "elements": ["E2", "E3"]},
            {"step": "GEN-SURVEY-SETOUT"},  # virtual by default
            {"step": "STR-SLAB-FORM", "elements": ["E1"]},
            {"step": "STR-SLAB-POUR", "elements": ["E1"], "lag_days": 7, "hold_point": "structural"},
        ])
        self.assertIn("Authored 5 tasks in L01-Z1: M000001, M000002, M000003, M000004, M000005", t)
        self.assertIn("manual mode ON", t)
        self.assertIn("staff_zone", t)
        self.assertIn("virtual", t)
        self.assertIn("hold:structural", t)
        g = self.game.tasks
        self.assertIn("L01-Z1", self.game.manual_zones)
        self.assertEqual([g[f"M00000{i}"]["predecessors"] for i in (1, 2)],
                         [[], [{"task_id": "M000001", "type": "FS", "lag_days": 0}]])
        self.assertEqual(g["M000005"]["predecessors"], [{"task_id": "M000004", "type": "FS", "lag_days": 7}])
        self.assertTrue(g["M000003"]["virtual"])
        self.assertFalse(g["M000001"]["virtual"])
        # the pour waits 7 days after the form: planned start reflects the lag
        self.assertEqual(g["M000005"]["planned_start_day"], g["M000004"]["planned_finish_day"] + 7)
        self.assertEqual(g["M000001"]["duration_days"], 3)

    def test_author_manual_chain_no_autolink_and_existing_mode(self):
        server.set_manual_mode("L00-Z1", True)
        t = server.author_manual_chain("L00-Z1", [{"step": "GEN-PERMIT-WORK"}, {"step": "GEN-LIFT-PLAN"}],
                                       manual_mode=False, auto_link=False)
        self.assertIn("not linked", t)
        self.assertTrue(all(not x["predecessors"] for x in self.game.tasks.values()))

    def test_author_manual_chain_rolls_back_on_error(self):
        with self.assertRaises(ToolError) as cm:
            server.author_manual_chain("L00-Z1", [
                {"step": "GEN-SURVEY-SETOUT"}, {"step": "STR-SLAB-FORM", "elements": ["E1"]},
                {"step": "MADE-UP-STEP", "elements": ["E1"]}])
        msg = str(cm.exception)
        self.assertIn("step 3 (MADE-UP-STEP)", msg)
        self.assertIn("unknown step: MADE-UP-STEP", msg)
        self.assertIn("2 earlier step(s) rolled back", msg)
        self.assertEqual(self.game.tasks, {})
        self.assertNotIn("L00-Z1", self.game.manual_zones)  # mode restored

    def test_author_manual_chain_validation(self):
        for steps, needle in (([], "empty"), ([{"elements": ["E1"]}], "needs a 'step'"),
                              ([{"step": "A", "bogus": 1}], "unknown keys"),
                              ([{"step": "A", "elements": "E1"}], "list of GUIDs")):
            with self.assertRaises(ToolError) as cm:
                server.author_manual_chain("L00-Z1", steps)
            self.assertIn(needle, str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            server.author_manual_chain("NOPE", [{"step": "GEN-PERMIT-WORK"}])
        self.assertIn("unknown zone", str(cm.exception))
        self.assertEqual(self.game.tasks, {})

    def test_resources(self):
        server.author_manual_chain("L00-Z1", [{"step": "GEN-SURVEY-SETOUT"}, {"step": "STR-SLAB-FORM", "elements": ["E1"]}])
        read = lambda uri: run(server.mcp.read_resource(uri))[0].content
        self.assertIn("rec_slab_on_grade", read("sitebuilder://logic"))
        self.assertIn("Slab on grade", read("sitebuilder://logic/rec_slab_on_grade"))
        self.assertIn("STR-SLAB-FORM", read("sitebuilder://manual/L00-Z1"))

    def test_call_through_registry(self):
        content, _ = run(server.mcp.call_tool("author_manual_chain", {
            "zone_id": "L00-Z1", "steps": [{"step": "GEN-SURVEY-SETOUT", "duration_days": 1}]}))
        self.assertIn("Authored 1 tasks", content[0].text)
        tools = {t.name: t for t in run(server.mcp.list_tools())}
        props = tools["author_manual_chain"].inputSchema["properties"]
        self.assertEqual(props["manual_mode"]["default"], True)
        self.assertEqual(props["auto_link"]["default"], True)
        self.assertEqual(tools["link_manual_tasks"].inputSchema["properties"]["type"]["enum"], ["FS", "SS", "FF"])
        self.assertGreater(len(tools["author_manual_chain"].description), 600)


class ViewToolTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer().start()
        self.addCleanup(self.fake.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)
        patcher = mock.patch.object(server, "_new_client", lambda: GameClient(self.fake.url, timeout=5))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = self.fake.game

    def test_heat(self):
        self.assertIn("ON", server.show_heat(True))
        self.assertTrue(self.game.heat_on)
        self.assertIn("OFF", server.show_heat(False))
        self.assertFalse(self.game.heat_on)
        t = server.heat_map("L01")
        self.assertIn("Heat L01", t)
        self.assertIn(" 04#", t)
        self.assertIn(" 0R#", t)
        self.assertEqual(self.game.calls[-1], ("view.heat", {"storey_id": "L01"}))
        server.heat_map()
        self.assertEqual(self.game.calls[-1], ("view.heat", {}))
        with self.assertRaises(ToolError) as cm:
            server.heat_map("L99")
        self.assertIn("no such storey", str(cm.exception))

    def test_highlight(self):
        self.assertIn("Highlighted 1 elements", server.highlight_elements(["E1", "zzz"]))
        self.assertIn("Unknown GUIDs: zzz", server.highlight_elements(["E1", "zzz"]))
        self.assertEqual(self.game.highlighted, ["E1"])
        self.assertIn("cleared", server.highlight_elements([]))
        self.assertEqual(self.game.highlighted, [])

    def test_installations_and_jumps(self):
        t = server.list_installations()
        self.assertIn("tank", t)
        self.assertIn("2 installations, 1 complete", t)
        self.assertIn("(showing 1)", server.list_installations(limit=1))
        j = server.jump_to_installation(1)
        self.assertIn("installation #1", j)
        self.assertIn("100% done", j)
        with self.assertRaises(ToolError):
            server.jump_to_installation(7)
        self.assertIn("North wing", server.list_areas())
        self.assertIn("area A2", server.jump_to_area("A2"))
        with self.assertRaises(ToolError) as cm:
            server.jump_to_area("nope")
        self.assertIn("no such area", str(cm.exception))

    def test_older_game_without_areas(self):
        self.game.no_areas = True
        with self.assertRaises(ToolError) as cm:
            server.list_areas()
        self.assertIn("Method not found", str(cm.exception))


class FakePipeline:
    """Stands in for server._run_cmd: records commands and writes the files the real pipeline would."""
    HELP = {"ifc-to-elements": "--sector --cell-size --project-config", "map": "--rules --aggregate",
            "schedule": "--library --crew-model", "validate": ""}

    def __init__(self, fail=None, help_text=None):
        self.cmds, self.fail = [], fail or {}
        self.help = dict(self.HELP, **(help_text or {}))

    @staticmethod
    def sub(cmd):
        return cmd[cmd.index("bimseq") + 1]

    def __call__(self, cmd, cwd, timeout=0):
        self.cmds.append(cmd)
        sub = self.sub(cmd)
        if "--help" in cmd:
            return 0, f"usage: bimseq {sub} {self.help[sub]}", ""
        if sub in self.fail:
            rc, err = self.fail[sub]
            return rc, "", err
        out = {"ifc-to-elements": lambda: cmd[cmd.index(sub) + 2], "map": lambda: cmd[cmd.index("--out") + 1],
               "schedule": lambda: cmd[cmd.index("--out") + 1]}.get(sub)
        if out:
            Path(out()).write_text(json.dumps({"sub": sub}))
        if sub == "schedule":
            Path(out()).with_name("sequence_part2.json").write_text("{}")
        return 0, f"wrote {sub} output\nOK {sub}\n", ""

    def named(self, sub):
        return [c for c in self.cmds if self.sub(c) == sub and "--help" not in c]


class IfcToBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.ifc = self.tmp / "Plant 12.ifc"
        self.ifc.write_text("ISO-10303-21;")
        self.scen_dir, self.work = self.tmp / "scenarios", self.tmp / "work"
        env = mock.patch.dict(os.environ, {"SITEBUILDER_SCENARIOS_DIR": str(self.scen_dir),
                                           "SITEBUILDER_WORK_DIR": str(self.work)})
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeServer().start()
        self.addCleanup(self.fake.stop)
        server.reset_client()
        self.addCleanup(server.reset_client)
        p = mock.patch.object(server, "_new_client", lambda: GameClient(self.fake.url, timeout=5))
        p.start()
        self.addCleanup(p.stop)

    def run_tool(self, runner, **kw):
        with mock.patch.object(server, "_run_cmd", runner):
            return server.ifc_to_bundle(str(self.ifc), **kw)

    def test_success_and_load(self):
        self.fake.game.scenario_ids.add("my_plant")
        r = FakePipeline()
        text = self.run_tool(r, sector="industrial", scenario_id="my_plant")
        for n, name in enumerate(("ifc-to-elements", "map", "schedule", "validate", "copy"), 1):
            self.assertIn(f"[{n}/5] {name}: ok", text)
        self.assertIn("Loaded in the game", text)
        self.assertIn(("scenario.load", {"id": "my_plant"}), self.fake.game.calls)
        self.assertEqual(json.loads((self.scen_dir / "my_plant" / "sequence.json").read_text()), {"sub": "schedule"})
        self.assertTrue((self.scen_dir / "my_plant" / "sequence_part2.json").exists())  # split bundles are copied whole
        self.assertFalse((self.scen_dir / "my_plant" / "elements.json").exists())
        ifc_cmd, map_cmd, sch_cmd = (r.named(s)[0] for s in ("ifc-to-elements", "map", "schedule"))
        self.assertIn("--sector", ifc_cmd)
        self.assertEqual(ifc_cmd[ifc_cmd.index("--sector") + 1], "industrial")
        self.assertTrue(map_cmd[map_cmd.index("--rules") + 1].endswith("data/sectors/industrial/mapping_rules.json"))
        self.assertTrue(sch_cmd[sch_cmd.index("--library") + 1].endswith("data/sectors/industrial/step_library.json"))
        self.assertEqual(sch_cmd[sch_cmd.index("--crew-model") + 1], "fractional")
        self.assertNotIn("--aggregate", map_cmd)
        self.assertNotIn("--project-config", ifc_cmd)
        self.assertEqual(r.named("validate")[0][-1], str(self.work / "my_plant"))
        # the scenario handed to the scheduler carries the new id
        sc = json.loads(Path(sch_cmd[sch_cmd.index("--scenario") + 1]).read_text())
        self.assertEqual(sc["id"], "my_plant")
        self.assertIn("Plant 12", sc["name"])
        self.assertEqual(sch_cmd[0], sys.executable)

    def test_default_scenario_id_and_other_sector(self):
        text = self.run_tool(FakePipeline(), sector="healthcare")
        self.assertIn("Scenario id: healthcare_plant_12", text)
        self.assertTrue((self.scen_dir / "healthcare_plant_12" / "sequence.json").exists())

    def test_optional_flags(self):
        cfg = self.tmp / "project_config.json"
        cfg.write_text("{}")
        r = FakePipeline()
        self.run_tool(r, scenario_id="x1", project_config_path=str(cfg), aggregate_preset="area_6", cell_size_m=3.0)
        ifc_cmd, map_cmd = r.named("ifc-to-elements")[0], r.named("map")[0]
        self.assertEqual(ifc_cmd[ifc_cmd.index("--project-config") + 1], str(cfg))
        self.assertEqual(ifc_cmd[ifc_cmd.index("--cell-size") + 1], "3.0")
        self.assertEqual(map_cmd[map_cmd.index("--aggregate") + 1], "area_6")

    def test_unsupported_requested_flag_is_an_error(self):
        cfg = self.tmp / "pc.json"
        cfg.write_text("{}")
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(help_text={"ifc-to-elements": "--sector"}), scenario_id="x2", project_config_path=str(cfg))
        self.assertIn("no --project-config", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(help_text={"map": "--rules"}), scenario_id="x3", aggregate_preset="p")
        self.assertIn("no --aggregate", str(cm.exception))
        # optional --crew-model is silently left out when unsupported
        r = FakePipeline(help_text={"schedule": "--library"})
        self.run_tool(r, scenario_id="x4")
        self.assertNotIn("--crew-model", r.named("schedule")[0])

    def test_step_failure_reports_step_and_does_not_copy(self):
        r = FakePipeline(fail={"map": (2, "error: bad rules\nTraceback...\nValueError: boom")})
        with self.assertRaises(ToolError) as cm:
            self.run_tool(r, scenario_id="bad")
        msg = str(cm.exception)
        self.assertIn("map failed (exit 2)", msg)
        self.assertIn("ValueError: boom", msg)
        self.assertIn("[1/5] ifc-to-elements: ok", msg)
        self.assertIn("[2/5] map: FAILED", msg)
        self.assertEqual(r.named("schedule"), [])
        self.assertFalse((self.scen_dir / "bad").exists())

    def test_no_ifcopenshell_hint(self):
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(fail={"ifc-to-elements": (3, "error: ifcopenshell is not installed")}), scenario_id="n")
        self.assertIn("pip install ifcopenshell", str(cm.exception))

    def test_validate_failure(self):
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(fail={"validate": (1, "FAIL sequence.json: tasks/3: required")}), scenario_id="v")
        self.assertIn("validate failed", str(cm.exception))
        self.assertFalse((self.scen_dir / "v").exists())

    def test_input_validation_and_overwrite_protection(self):
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(), sector="space")
        self.assertIn("sector must be one of", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            with mock.patch.object(server, "_run_cmd", FakePipeline()):
                server.ifc_to_bundle(str(self.tmp / "missing.ifc"))
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(), scenario_id="p", project_config_path=str(self.tmp / "none.json"))
        self.assertIn("project config not found", str(cm.exception))
        self.run_tool(FakePipeline(), scenario_id="once")
        with self.assertRaises(ToolError) as cm:
            self.run_tool(FakePipeline(), scenario_id="once")
        self.assertIn("already exists", str(cm.exception))
        self.assertIn("[5/5] copy: ok", self.run_tool(FakePipeline(), scenario_id="once", overwrite=True))

    def test_game_not_connected_and_refusing(self):
        with mock.patch.object(server, "_new_client", lambda: GameClient("ws://127.0.0.1:1", timeout=1)):
            server.reset_client()
            text = self.run_tool(FakePipeline(), scenario_id="offline")
        self.assertIn("Game not connected", text)
        self.assertIn("load_scenario(id='offline')", text)
        self.assertTrue((self.scen_dir / "offline" / "sequence.json").exists())
        server.reset_client()
        text = self.run_tool(FakePipeline(), scenario_id="refused")  # the fake game does not know this id
        self.assertIn("game refused scenario.load", text)
        server.reset_client()
        text = self.run_tool(FakePipeline(), scenario_id="noload", load=False)
        self.assertNotIn("Loaded", text)
        self.assertNotIn("not connected", text)

    def test_real_cli_flags_still_exist(self):
        """The flags this tool passes must exist in the current bimseq CLI (guards against pipeline renames)."""
        import subprocess
        for sub, flags in (("ifc-to-elements", ["--sector"]), ("map", ["--rules", "--library", "--out"]),
                           ("schedule", ["--library", "--scenario", "--elements", "--out"]), ("validate", [])):
            out = subprocess.run([sys.executable, "-m", "bimseq", sub, "--help"], cwd=TOOLS, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            for f in flags:
                self.assertIn(f, out.stdout, f"{sub} lost {f}")


class ConnectionFailureTests(unittest.TestCase):
    def test_unreachable_game_gives_actionable_error(self):
        server.reset_client()
        self.addCleanup(server.reset_client)
        with mock.patch.dict(os.environ, {"SITEBUILDER_URL": "ws://127.0.0.1:1"}):
            with self.assertRaises(ToolError) as cm:
                server.get_summary()
        self.assertIn("launch_game", str(cm.exception))

    def test_env_configuration(self):
        server.reset_client()
        self.addCleanup(server.reset_client)
        with mock.patch.dict(os.environ, {"SITEBUILDER_URL": "ws://example:9", "SITEBUILDER_TOKEN": "tk"}):
            c = server._new_client()
        self.assertEqual((c.url, c.token), ("ws://example:9", "tk"))


class LaunchGameTests(unittest.TestCase):
    def test_build_command(self):
        cmd = launch_game.build_command("minimal", 9000, godot_bin="/bin/godot")
        self.assertEqual(cmd[0], "/bin/godot")
        self.assertIn("--headless", cmd)
        self.assertIn("--api=9000", cmd)
        self.assertEqual(cmd[-2:], ["--", "--scenario=minimal"])
        self.assertLess(cmd.index("--api=9000"), cmd.index("--"))
        self.assertTrue(cmd[cmd.index("--path") + 1].endswith("godot"))
        self.assertIn("--api-token=t", launch_game.build_command("x", 1, token="t", godot_bin="g"))

    def test_launch_fails_fast_when_process_exits(self):
        with mock.patch.object(launch_game, "build_command", lambda *a, **k: [sys.executable, "-c", "raise SystemExit(3)"]):
            with self.assertRaises(RuntimeError) as cm:
                launch_game.launch_game(port=59123, wait=10)
        self.assertIn("exited early", str(cm.exception))

    def test_launch_waits_for_port(self):
        code = ("import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
                "time.sleep(0.6);s.bind(('127.0.0.1',59124));s.listen();time.sleep(30)")
        with mock.patch.object(launch_game, "build_command", lambda *a, **k: [sys.executable, "-c", code]):
            proc = launch_game.launch_game(port=59124, wait=20)
        self.addCleanup(launch_game.stop_game, proc)
        self.assertTrue(launch_game.port_open(59124))


if __name__ == "__main__":
    unittest.main()
