"""Tests for the sitebuilder_mcp server tools, resources and prompt against the fake game server."""
from __future__ import annotations

import asyncio
import os
import sys
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
              "resolve_event", "export_plan", "gantt_text"]


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
        self.assertEqual(uris, {"sitebuilder://summary", "sitebuilder://zones", "sitebuilder://gantt"})
        tmpl = {r.uriTemplate for r in run(server.mcp.list_resource_templates())}
        self.assertEqual(tmpl, {"sitebuilder://zone/{zone_id}", "sitebuilder://packages/{zone_id}"})
        prompts = run(server.mcp.list_prompts())
        self.assertEqual([p.name for p in prompts], ["plan_next_week"])
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
