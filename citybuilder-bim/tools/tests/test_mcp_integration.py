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


if __name__ == "__main__":
    unittest.main()
