import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from helpers import REPO, TOOLS  # noqa: F401  (sets sys.path)
from bimseq import __main__ as cli
from bimseq import export, ifc_extract, pipeline
from bimseq.model import read_json
from bimseq.validate import validate_file, validate_tree

NO_SECTORS = Path("/nonexistent-sector-dir")


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


def check_schedule_constraints(tc, seq):
    """Every planned date respects every predecessor link and level-independent invariants."""
    tasks = {t["task_id"]: t for t in seq["tasks"]}
    for t in seq["tasks"]:
        s, f = t["planned_start_day"], t["planned_finish_day"]
        tc.assertGreaterEqual(s, 0)
        tc.assertGreater(f, s)
        for p in t["predecessors"]:
            q = tasks[p["task_id"]]
            if p["type"] == "FS":
                tc.assertGreaterEqual(s, q["planned_finish_day"] + p["lag_days"], t["task_id"])
            elif p["type"] == "SS":
                tc.assertGreaterEqual(s, q["planned_start_day"] + p["lag_days"], t["task_id"])
            else:
                tc.assertGreaterEqual(f, q["planned_finish_day"] + p["lag_days"], t["task_id"])
    cap = seq["scenario"]["crews_available"]
    events = {}
    for t in seq["tasks"]:
        ev = events.setdefault(t["trade"], {})
        ev[t["planned_start_day"]] = ev.get(t["planned_start_day"], 0) + 1
        ev[t["planned_finish_day"]] = ev.get(t["planned_finish_day"], 0) - 1
    for trade, ev in events.items():
        active = 0
        for day in sorted(ev):
            active += ev[day]
            tc.assertLessEqual(active, max(1, cap.get(trade, 1)), f"{trade} day {day}")


class BuildSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.fallback_dir = cls.tmp / "fallback"
        cls.default_dir = cls.tmp / "default"
        cls.fb = {s: pipeline.build_sector(s, cls.fallback_dir, NO_SECTORS) for s in pipeline.SECTORS}
        cls.df = {s: pipeline.build_sector(s, cls.default_dir) for s in pipeline.SECTORS}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fallback_used_when_sector_data_missing(self):
        for s, summary in self.fb.items():
            self.assertTrue(summary.fallback)
            self.assertTrue(summary.notes)
        with self.assertRaises(FileNotFoundError):
            pipeline.build_sector("civil", self.tmp / "x", NO_SECTORS, require_real=True)

    def test_every_output_validates_against_every_schema(self):
        trees = [self.fallback_dir]
        if not all(x.fallback for x in self.df.values()):      # real sector data in use: check that build too
            trees.append(self.default_dir)
        for d in trees:
            buf = io.StringIO()
            self.assertTrue(validate_tree(d, out=buf), buf.getvalue())
            for sector in pipeline.SECTORS:
                for name in ("elements", "element_step_map", "sequence"):
                    self.assertIn(f"{name:17s} {d / sector / (name + '.json')}", buf.getvalue())
        for sector in pipeline.SECTORS:                       # fallback inputs validate too
            for kind in ("step_library", "mapping_rules", "scenario"):
                ok, msgs = validate_file(pipeline.FALLBACK_DIR / f"{sector}_{kind}.json", kind)
                self.assertTrue(ok, msgs)

    def test_schedules_respect_links_and_crews(self):
        for d in (self.fallback_dir, self.default_dir):
            for sector in pipeline.SECTORS:
                with self.subTest(dir=d.name, sector=sector):
                    check_schedule_constraints(self, read_json(d / sector / "sequence.json"))

    def test_sequence_content(self):
        for sector, s in self.fb.items():
            seq = read_json(self.fallback_dir / sector / "sequence.json")
            m = read_json(self.fallback_dir / sector / "element_step_map.json")
            self.assertEqual(len(seq["tasks"]), len(m["tasks"]))
            self.assertEqual(seq["baseline"]["finish_week"], -(-seq["baseline"]["finish_day"] // 5))
            self.assertEqual(seq["baseline"]["finish_day"], max(t["planned_finish_day"] for t in seq["tasks"]))
            self.assertAlmostEqual(seq["baseline"]["total_cost"], sum(t["cost"] for t in seq["tasks"]), 1)
            self.assertEqual(len(seq["baseline"]["weekly_planned_cost"]), seq["baseline"]["finish_week"] + 1)
            self.assertIsInstance(seq["scenario"]["contract_weeks"], int)
            self.assertGreater(seq["scenario"]["budget"], 0)
            self.assertTrue(set(seq["baseline"]["critical_task_ids"]) <=
                            {t["task_id"] for t in seq["tasks"] if t["is_critical"]})
            self.assertEqual(len(seq["elements"]), s.elements)
            self.assertTrue(any(t["is_critical"] for t in seq["tasks"]))
            self.assertIn("fallback", seq["generator"])
            self.assertEqual(m["sequencing_gaps"], [], "fallback sector data must map without gaps")

    def test_fractional_crew_model(self):
        out = self.tmp / "fractional"
        for sector in pipeline.SECTORS:
            pipeline.build_sector(sector, out, NO_SECTORS, fractional_crews=True)
            seq = read_json(out / sector / "sequence.json")
            whole = read_json(self.fallback_dir / sector / "sequence.json")
            self.assertLessEqual(seq["baseline"]["finish_day"], whole["baseline"]["finish_day"])
            self.assertIn("fractional crews", seq["generator"])
            tasks = {t["task_id"]: t for t in seq["tasks"]}
            for t in seq["tasks"]:
                for p in t["predecessors"]:
                    if p["type"] == "FS":
                        self.assertGreaterEqual(t["planned_start_day"], tasks[p["task_id"]]["planned_finish_day"] + p["lag_days"])
            cap = seq["scenario"]["crews_available"]
            load = {}
            for t in seq["tasks"]:
                dur = t["planned_finish_day"] - t["planned_start_day"]
                share = min(1.0, max(t["estimated_crew_days"], 0.01) / dur)
                for d in range(t["planned_start_day"], t["planned_finish_day"]):
                    load[(t["trade"], d)] = load.get((t["trade"], d), 0.0) + share
            for (trade, d), v in load.items():
                self.assertLessEqual(v, max(1, cap.get(trade, 1)) + 1e-6, f"{sector} {trade} day {d}")

    def test_deterministic(self):
        again = self.tmp / "again"
        pipeline.build_sector("healthcare", again, NO_SECTORS)
        for name in ("elements.json", "element_step_map.json", "sequence.json", "sequence.csv"):
            self.assertEqual((again / "healthcare" / name).read_bytes(),
                             (self.fallback_dir / "healthcare" / name).read_bytes())

    def test_csv_matches_sequence(self):
        seq = read_json(self.fallback_dir / "civil" / "sequence.json")
        rows = export.read_csv(self.fallback_dir / "civil" / "sequence.csv")
        self.assertEqual(len(rows), len(seq["tasks"]))
        self.assertEqual(list(rows[0]), export.COLUMNS)
        self.assertEqual(rows[5]["task_id"], seq["tasks"][5]["task_id"])

    def test_sync_godot(self):
        godot = self.tmp / "godot"
        (godot / "scenarios" / "minimal").mkdir(parents=True)
        (godot / "scenarios" / "minimal" / "sequence.json").write_text("{}")
        samples = self.tmp / "samples"
        shutil.copytree(self.fallback_dir, samples)
        shutil.copytree(REPO / "data" / "samples" / "minimal", samples / "minimal")
        paths = pipeline.sync_godot(samples, godot)
        names = sorted(p.parent.name for p in paths)
        self.assertEqual(names, ["civil_standard", "healthcare_standard", "industrial_standard"])
        self.assertTrue((godot / "scenarios" / "minimal" / "sequence.json").exists())
        self.assertEqual((godot / "scenarios" / "civil_standard" / "sequence.json").read_bytes(),
                         (samples / "civil" / "sequence.json").read_bytes())
        self.assertEqual(pipeline.godot_scenario_dir_name("civil", "civil-hard"), "civil-hard")


class MinimalRoundTrip(unittest.TestCase):
    def test_export_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = REPO / "data" / "samples" / "minimal" / "sequence.json"
            out = Path(tmp) / "min.csv"
            rc, stdout, _ = run_cli("export-csv", src, out)
            self.assertEqual(rc, 0)
            seq = read_json(src)
            rows = export.read_csv(out)
            self.assertEqual(len(rows), len(seq["tasks"]))
            for row, t in zip(rows, seq["tasks"]):
                self.assertEqual(row["task_id"], t["task_id"])
                self.assertEqual(row["element_guid"], t["element_guid"])
                self.assertEqual(row["step_id"], t["step_id"])
                self.assertEqual(float(row["quantity"]), t["quantity"])
                self.assertEqual(int(row["planned_start_day"]), t["planned_start_day"])
                self.assertEqual(int(row["planned_finish_day"]), t["planned_finish_day"])
                self.assertEqual(row["actual_start_day"], "")
                preds = [p for p in row["predecessor_task_ids"].split(";") if p]
                self.assertEqual(preds, [p["task_id"] for p in t["predecessors"]])
            self.assertEqual([r["predecessor_task_ids"] for r in rows][8], "T000005;T000006;T000007;T000008")
            # element_step_map (no dates in some tools) exports too
            rc, _, _ = run_cli("export-csv", REPO / "data" / "samples" / "minimal" / "element_step_map.json",
                               Path(tmp) / "map.csv")
            self.assertEqual(rc, 0)

    def test_map_and_schedule_cli_on_synthetic_elements(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            elements = tmp / "elements.json"
            from bimseq.model import write_json
            from bimseq.synth import GENERATORS
            write_json(elements, GENERATORS["healthcare"]())
            lib = pipeline.FALLBACK_DIR / "healthcare_step_library.json"
            rc, out, err = run_cli("map", elements, "--rules", pipeline.FALLBACK_DIR / "healthcare_mapping_rules.json",
                                   "--library", lib, "--out", tmp / "map.json")
            self.assertEqual(rc, 0, err)
            rc, out, err = run_cli("schedule", tmp / "map.json", "--library", lib, "--scenario",
                                   pipeline.FALLBACK_DIR / "healthcare_scenario.json", "--elements", elements,
                                   "--out", tmp / "seq.json")
            self.assertEqual(rc, 0, err)
            self.assertTrue(validate_file(tmp / "seq.json", "sequence")[0])
            self.assertTrue(validate_file(tmp / "map.json", "element_step_map")[0])
            self.assertEqual(run_cli("validate", tmp)[0], 0)


class ValidateCommand(unittest.TestCase):
    def test_validate_reports_failures_and_skips_unknown_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copy(REPO / "data" / "samples" / "minimal" / "scenario.json", tmp / "scenario.json")
            (tmp / "notes.json").write_text("{}")             # unrecognised name: ignored
            rc, out, _ = run_cli("validate", tmp)
            self.assertEqual(rc, 0)
            self.assertIn("OK", out)
            self.assertNotIn("notes.json", out)
            bad = read_json(tmp / "scenario.json")
            del bad["budget"]
            (tmp / "scenario_bad.json").write_text(json.dumps(bad))
            rc, out, _ = run_cli("validate", tmp)
            self.assertEqual(rc, 1)
            self.assertIn("FAIL", out)
            self.assertIn("budget", out)

    def test_cross_check_catches_dangling_predecessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            seq = read_json(REPO / "data" / "samples" / "minimal" / "sequence.json")
            seq["tasks"][1]["predecessors"].append({"task_id": "T999999", "type": "FS", "lag_days": 0})
            p = Path(tmp) / "sequence.json"
            p.write_text(json.dumps(seq))
            ok, msgs = validate_file(p)
            self.assertFalse(ok)
            self.assertIn("T999999", msgs[0])

    def test_empty_directory_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_cli("validate", tmp)[0], 1)


class IfcExtract(unittest.TestCase):
    @unittest.skipIf(ifc_extract.available(), "ifcopenshell installed")
    def test_missing_ifcopenshell_exits_3(self):
        rc, _, err = run_cli("ifc-to-elements", "model.ifc", "out.json")
        self.assertEqual(rc, 3)
        self.assertIn("ifcopenshell", err)

    def test_storey_indexing(self):
        self.assertEqual(ifc_extract.index_storeys([("b", 3.5), ("a", -0.0), ("c", -3.2)]), {"c": -1, "a": 0, "b": 1})
        self.assertEqual(ifc_extract.index_storeys([("x", -6), ("y", -3)]), {"x": -1, "y": 0})
        self.assertEqual(ifc_extract.index_storeys([]), {})

    def test_cells_for_bbox(self):
        self.assertEqual(ifc_extract.cells_for_bbox((0.5, 0.5, 0), (5.5, 5.5, 1), (0, 0, 0), 6), [(0, 0)])
        self.assertEqual(ifc_extract.cells_for_bbox((0, 0, 0), (12, 6, 1), (0, 0, 0), 6), [(0, 0), (1, 0)])
        self.assertEqual(ifc_extract.cells_for_bbox((6, 6, 0), (6, 6, 0), (0, 0, 0), 6), [(1, 1)])   # point on a corner
        self.assertEqual(ifc_extract.cells_for_bbox((13, 1, 0), (14, 2, 0), (1, 0, 0), 6), [(2, 0)])

    def test_tile_zones_blocks_of_three(self):
        cells = {(x, z) for x in range(7) for z in range(4)}
        zones, zone_of = ifc_extract.tile_zones({"L00": cells, "L01": {(0, 0)}}, (3, 3), 2)
        l0 = [z for z in zones if z["storey_id"] == "L00"]
        self.assertEqual(len(l0), 6)                                         # ceil(7/3) x ceil(4/3)
        self.assertTrue(all(1 <= len(z["cells"]) <= 9 and z["max_crews"] == 2 for z in zones))
        self.assertEqual(sum(len(z["cells"]) for z in l0), len(cells))
        self.assertEqual(zone_of[("L00", (0, 0))], "L00-Z1")
        self.assertEqual(zone_of[("L01", (0, 0))], "L01-Z1")

    def test_storeys_overlapping_splits_columns(self):
        bands = [("a", 0, 4), ("b", 4, 8), ("c", 8, 12)]
        res = ifc_extract.storeys_overlapping(0.0, 8.0, bands)
        self.assertEqual([k for k, _ in res], ["a", "b"])
        self.assertAlmostEqual(sum(f for _, f in res), 1.0)
        self.assertEqual([k for k, _ in ifc_extract.storeys_overlapping(0.1, 3.9, bands)], ["a"])

    def test_quantity_mapping(self):
        q = ifc_extract.map_quantity_sets({"Qto_WallBaseQuantities": {"NetVolume": 2.0, "GrossVolume": 3.0,
                                                                       "NetSideArea": 10.0, "Length": 5000.0, "id": 9},
                                           "Weights": {"GrossWeight": 1500.0}}, unit_scale=0.001)
        self.assertEqual(q["length_m"], 5.0)
        self.assertLess(q["volume_m3"], 1e-6)                                # mm3 -> m3
        self.assertEqual(q["weight_t"], 1.5)
        q = ifc_extract.map_quantity_sets({"q": {"NetVolume": 2.0, "NetSideArea": 10.0}}, 1.0)
        self.assertEqual((q["volume_m3"], q["area_m2"]), (2.0, 10.0))
        fb = ifc_extract.quantities_from_bbox("IfcSlab", (0, 0, 0), (6, 6, 0.3))
        self.assertEqual((fb["area_m2"], fb["volume_m3"], fb["length_m"]), (36.0, 10.8, 6.0))
        fb = ifc_extract.quantities_from_bbox("IfcWall", (0, 0, 0), (6, 0.3, 3))
        self.assertEqual(fb["area_m2"], 18.0)

    def test_system_discipline(self):
        self.assertEqual(ifc_extract.guess_system_discipline("x", "VENTILATION"), "mechanical")
        self.assertEqual(ifc_extract.guess_system_discipline("MG-O2"), "medical")
        self.assertEqual(ifc_extract.guess_system_discipline("zzz"), "general")


if __name__ == "__main__":
    unittest.main()
