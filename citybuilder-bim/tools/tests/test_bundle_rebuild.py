import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import REPO  # noqa: F401  (sets sys.path)
from bimseq import bundle, pipeline
from bimseq.__main__ import main as cli_main
from bimseq.model import read_json, write_json
from bimseq.validate import validate_file, validate_tree

NO_SECTORS = Path("/nonexistent")


class SplitBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        pipeline.build_sector("healthcare", cls.dir / "plain", NO_SECTORS)
        cls.plain = read_json(cls.dir / "plain" / "healthcare" / "sequence.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_split_round_trip_and_validation(self):
        out = self.dir / "split"
        main = bundle.write_bundle(self.plain, out, compress=True, tasks_per_part=500, lazy_zone_detail=True)
        self.assertEqual(main.name, "sequence.json.gz")
        parts = sorted(p.name for p in out.glob("tasks.part-*.json.gz"))
        self.assertEqual(len(parts), -(-len(self.plain["tasks"]) // 500))
        head = bundle.read_any_json(main)
        self.assertEqual(head["tasks"], [])
        self.assertEqual(head["bundle_format"], {"compressed": True, "task_parts": parts, "zone_detail_dir": "zones"})
        zones = sorted((out / "zones").glob("*.json.gz"))
        self.assertEqual(len(zones), len(self.plain["zones"]))
        detail = bundle.read_any_json(zones[0])
        self.assertEqual(set(detail), {"zone_id", "task_ids", "package_ids", "members"})
        full = bundle.load_bundle(main)
        self.assertEqual(full["tasks"], self.plain["tasks"])
        self.assertEqual(full["packages"], self.plain["packages"])
        self.assertEqual([e.get("member_guids") for e in full["elements"]], [e.get("member_guids") for e in self.plain["elements"]])
        ok, msgs = validate_file(main)
        self.assertTrue(ok, msgs)
        buf = __import__("io").StringIO()
        self.assertTrue(validate_tree(out, out=buf), buf.getvalue())
        self.assertEqual(buf.getvalue().count("OK"), 1)                  # only sequence.json.gz is a schema file

    def test_member_guids_move_to_zone_detail_only_when_lazy(self):
        agg = pipeline.build_sector("industrial", self.dir / "agg", NO_SECTORS, aggregate={"threshold_per_cell": 3, "group_by": ["storey", "zone", "kit_or_class"]})
        seq = read_json(self.dir / "agg" / "industrial" / "sequence.json")
        if not any(e.get("member_guids") for e in seq["elements"]):
            self.skipTest("no aggregates at this size")
        out = self.dir / "lazy"
        bundle.write_bundle(seq, out, compress=False, lazy_zone_detail=True)
        head = read_json(out / "sequence.json")
        self.assertFalse(any("member_guids" in e for e in head["elements"]))
        self.assertTrue(any("member_count" in e for e in head["elements"]))
        self.assertEqual(bundle.load_bundle(out / "sequence.json")["elements"], seq["elements"])

    def test_plain_bundle_unchanged(self):
        out = self.dir / "plainout"
        p = bundle.write_bundle(self.plain, out)
        self.assertEqual(read_json(p), self.plain)
        self.assertEqual(bundle.load_bundle(p), self.plain)

    def test_schedule_cli_writes_split_bundle_from_project_config(self):
        d = self.dir / "cli"
        d.mkdir()
        sec = pipeline.FALLBACK_DIR
        cfg = d / "project_config.json"
        write_json(cfg, {"schema_version": "1.0", "output": {"compress": True, "tasks_per_part": 400, "lazy_zone_detail": True}})
        src = self.dir / "plain" / "healthcare"
        self.assertEqual(cli_main(["map", str(src / "elements.json"), "--rules", str(sec / "healthcare_mapping_rules.json"),
                                   "--library", str(sec / "healthcare_step_library.json"), "--out", str(d / "map.json"),
                                   "--project-config", str(cfg)]), 0)
        self.assertEqual(cli_main(["schedule", str(d / "map.json.gz") if (d / "map.json.gz").exists() else str(d / "map.json"),
                                   "--library", str(sec / "healthcare_step_library.json"), "--scenario",
                                   str(sec / "healthcare_scenario.json"), "--elements", str(src / "elements.json"),
                                   "--out", str(d / "sequence.json"), "--project-config", str(cfg)]), 0)
        self.assertTrue((d / "sequence.json.gz").exists())
        self.assertTrue(list(d.glob("tasks.part-*.json.gz")))
        full = bundle.load_bundle(d / "sequence.json.gz")
        self.assertGreater(len(full["tasks"]), 400)

    def test_project_config_aggregation_preset_drives_map(self):
        d = self.dir / "agg_cli"
        d.mkdir()
        sec = pipeline.FALLBACK_DIR
        cfg = d / "project_config.json"
        write_json(cfg, {"schema_version": "1.0", "aggregation": {"preset": "healthcare_mep", "threshold_per_cell": 5}})
        src = self.dir / "plain" / "healthcare"
        args = ["map", str(src / "elements.json"), "--rules", str(sec / "healthcare_mapping_rules.json"),
                "--library", str(sec / "healthcare_step_library.json")]
        self.assertEqual(cli_main(args + ["--out", str(d / "a.json"), "--project-config", str(cfg)]), 0)
        self.assertEqual(cli_main(args + ["--out", str(d / "b.json")]), 0)
        a, b = read_json(d / "a.json"), read_json(d / "b.json")
        self.assertTrue(a["aggregates"])
        self.assertNotIn("aggregates", b)
        self.assertLess(len(a["tasks"]), len(b["tasks"]))


class Rebuild(unittest.TestCase):
    def test_rebuild_zone_keeps_other_ids_and_applies_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pipeline.build_sector("healthcare", d, NO_SECTORS)
            S = d / "healthcare"
            old = read_json(S / "sequence.json")
            zone = "L00-Z3"
            man = {"schema_version": "1.0", "project": "x", "zones_in_manual_mode": [zone],
                   "tasks": [{"id": "M0001", "step": "GEN-SURVEY-SETOUT", "zone_id": zone, "virtual": True, "duration_days": 2}]}
            write_json(S / "m.json", man)
            lib = pipeline.FALLBACK_DIR
            rc = cli_main(["rebuild-zone", str(S / "element_step_map.json"), "--zone", zone, "--manual", str(S / "m.json"),
                           "--rules", str(lib / "healthcare_mapping_rules.json"), "--library", str(lib / "healthcare_step_library.json")])
            self.assertEqual(rc, 0)
            new = read_json(S / "sequence.json")
            ok, msgs = validate_file(S / "sequence.json")
            self.assertTrue(ok, msgs)
            zone_old = [t for t in old["tasks"] if t["zone_id"] == zone]
            zone_new = [t for t in new["tasks"] if t["zone_id"] == zone]
            self.assertGreater(len(zone_old), 1)
            self.assertEqual([t["origin"] for t in zone_new], ["manual"])
            self.assertEqual(zone_new[0]["manual_id"], "M0001")
            key = lambda t: (t["element_guid"], t["step_id"])
            old_ids = {key(t): t["task_id"] for t in old["tasks"] if t["zone_id"] != zone}
            outside = [t for t in new["tasks"] if t["zone_id"] != zone]
            self.assertEqual(len(outside), len(old_ids))
            self.assertTrue(all(old_ids[key(t)] == t["task_id"] for t in outside))          # ids stable outside the zone
            self.assertGreater(int(zone_new[0]["task_id"][1:]), max(int(t["task_id"][1:]) for t in old["tasks"]))
            self.assertEqual(len({t["task_id"] for t in new["tasks"]}), len(new["tasks"]))
            old_pk = {p["package_id"]: frozenset(p["task_ids"]) for p in old["packages"] if p["zone_id"] != zone}
            new_pk = {p["package_id"]: frozenset(p["task_ids"]) for p in new["packages"] if p["zone_id"] != zone}
            self.assertEqual(old_pk, new_pk)                                                 # package ids stable too
            self.assertEqual(new["manual"]["zones_in_manual_mode"], [zone])
            self.assertEqual(sum(1 for t in new["tasks"] if t["zone_id"] == zone and t.get("virtual")), 1)

    def test_rebuild_zone_unknown_zone_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pipeline.build_sector("civil", d, NO_SECTORS)
            lib = pipeline.FALLBACK_DIR
            rc = cli_main(["rebuild-zone", str(d / "civil" / "element_step_map.json"), "--zone", "NOPE",
                           "--rules", str(lib / "civil_mapping_rules.json"), "--library", str(lib / "civil_step_library.json")])
            self.assertEqual(rc, 2)


class StressBuild(unittest.TestCase):
    def test_small_stress_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, timings = pipeline.build_stress(2, 2, Path(tmp), "stress_test")
            self.assertEqual(summary.elements, 4 * 834)
            self.assertLess(summary.tasks, 4 * 1600)
            ok, msgs = validate_file(Path(tmp) / "stress_test" / "sequence.json.gz")
            self.assertTrue(ok, msgs)
            full = bundle.load_bundle(Path(tmp) / "stress_test" / "sequence.json.gz")
            self.assertTrue(full["packages"])
            self.assertEqual(full["scenario"]["id"], "stress_test")
            godot = Path(tmp) / "godot"
            written = pipeline.sync_split_bundle(Path(tmp) / "stress_test", godot, "stress_test")
            self.assertTrue((godot / "scenarios" / "stress_test" / "sequence.json.gz").exists())
            self.assertTrue(any(p.parent.name == "zones" for p in written))

    @unittest.skipUnless(__import__("bimseq.ifc_extract", fromlist=["x"]).available(), "ifcopenshell not installed")
    def test_small_stress_through_real_ifc(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, timings = pipeline.build_stress_ifc(2, 1, Path(tmp), "stress_ifc")
            self.assertEqual(summary.elements, 2 * 834)
            self.assertTrue({"synth_ifc", "extract", "map", "schedule"} <= set(timings))
            ok, msgs = validate_file(Path(tmp) / "stress_ifc" / "sequence.json.gz")
            self.assertTrue(ok, msgs)


if __name__ == "__main__":
    unittest.main()
