import math
import random
import tempfile
import unittest
from pathlib import Path

from helpers import doc, elem, library, rule, rules, scenario, step, REPO  # noqa: F401  (sets sys.path)
from bimseq import aggregation, export, grid_detect, ifc_extract, pipeline
from bimseq.mapper import map_elements
from bimseq.model import elements_from_dict, read_json, step_library_from_dict, write_json
from bimseq.scheduler import build_sequence
from bimseq.validate import schema_errors, validate_file


def dense_doc(n_pipes=60, n_cables=10):
    els = [elem(f"p{i:03d}", f"Pipe {i}", "G", "G-Z1", [(0, 0)], ifc_class="IfcPipeSegment", system="S1",
                quantities={"length_m": 2.0, "count": 1}, properties={"Insulated": True, "Dia": i % 3}) for i in range(n_pipes)]
    els += [elem(f"c{i:03d}", f"Cable {i}", "G", "G-Z1", [(0, 0)], ifc_class="IfcCableSegment", system="S2",
                 quantities={"length_m": 3.0, "count": 1}) for i in range(n_cables)]
    els.append(elem("w1", "Window", "G", "G-Z1", [(0, 0)], ifc_class="IfcWindow", host="p000", quantities={"count": 1}))
    els.append(elem("lone", "Lone pipe", "G", "G-Z2", [(2, 0)], ifc_class="IfcPipeSegment", system="S1",
                    quantities={"length_m": 5.0, "count": 1}))
    return doc(els)


STEPS = [step("TST-PIPE-DO", basis="length_m", rate=10.0), step("TST-CABLE-DO", basis="length_m", rate=20.0),
         step("TST-WIN-DO", preds=[("TST-PIPE-DO", "host", "FS", 0, True)]), step("TST-DEFAULT-DO")]
RULES = [rule("R-p", 30, {"ifc_class": ["IfcPipeSegment"]}, [{"step": "TST-PIPE-DO", "quantity": "length_m"}]),
         rule("R-c", 20, {"ifc_class": ["IfcCableSegment"]}, [{"step": "TST-CABLE-DO", "quantity": "length_m"}]),
         rule("R-w", 10, {"ifc_class": ["IfcWindow"]}, ["TST-WIN-DO"])]


class Aggregation(unittest.TestCase):
    def test_dense_cell_aggregates_and_small_groups_stay(self):
        agg = aggregation.aggregate(dense_doc(), "default")
        by_class = {}
        for e in agg.elements:
            by_class.setdefault(e.ifc_class, []).append(e)
        pipes = by_class["IfcPipeSegment"]
        self.assertEqual(len(pipes), 2)                                      # 1 aggregate + the lone pipe in another cell
        a = next(e for e in pipes if e.member_guids)
        self.assertTrue(a.guid.startswith("AGG-"))
        self.assertEqual(a.member_guids, sorted(f"p{i:03d}" for i in range(60)))
        self.assertEqual((a.quantities["length_m"], a.quantities["count"]), (120.0, 60.0))
        self.assertEqual(a.name, "60 x IfcPipeSegment in [0, 0]")
        self.assertEqual(a.properties, {"Insulated": True})                  # only properties common to all members
        self.assertEqual(a.system_id, "S1")
        self.assertEqual(len(by_class["IfcCableSegment"]), 10)               # 10 <= 25: untouched
        win = by_class["IfcWindow"][0]
        self.assertEqual(win.host_guid, a.guid)                              # host re-pointed to the aggregate
        self.assertEqual(aggregation.aggregate(dense_doc(), "default").elements[0].guid,
                         aggregation.aggregate(dense_doc(), "default").elements[0].guid)

    def test_presets_and_threshold(self):
        self.assertIn("industrial_dense", aggregation.load_presets())
        d = dense_doc()
        self.assertEqual(len(aggregation.aggregate(d, {"max_members": 100}).elements), len(d.elements))
        self.assertEqual(len(aggregation.aggregate(d, {"max_members": 5}).elements),
                         len(d.elements) - 60 + 1 - 10 + 1)                  # cables now aggregate too
        with self.assertRaises(ValueError):
            aggregation.aggregate(d, "nope")

    def test_round_trip_map_schedule_export_expands_members(self):
        d = dense_doc()
        lib = step_library_from_dict(library(STEPS).raw)
        agg = aggregation.aggregate(d, "default")
        sm = map_elements(agg, lib, rules(RULES), generated_at="t")
        plain = map_elements(d, step_library_from_dict(library(STEPS).raw), rules(RULES), generated_at="t")
        self.assertLess(len(sm.tasks), len(plain.tasks))
        a_guid = next(e.guid for e in agg.elements if e.member_guids)
        self.assertEqual(len([t for t in sm.tasks if t.element_guid == a_guid]), 1)       # one task per step on the aggregate
        bundle, _ = build_sequence(sm, lib, scenario({"crew": 5}), agg, generated_at="t")
        self.assertEqual(schema_errors("sequence", bundle), [])
        agg_light = next(e for e in bundle["elements"] if e["guid"] == a_guid)
        self.assertEqual(len(agg_light["member_guids"]), 60)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.csv"
            n = export.export_csv(bundle, out)
            rows = export.read_csv(out)
            self.assertEqual(n, len(rows))
            pipe_rows = [r for r in rows if r["step_id"] == "TST-PIPE-DO" and r["element_guid"].startswith("p")]
            self.assertEqual(sorted(r["element_guid"] for r in pipe_rows), sorted(f"p{i:03d}" for i in range(60)))
            task = next(t for t in bundle["tasks"] if t["element_guid"] == a_guid and t["step_id"] == "TST-PIPE-DO")
            self.assertEqual({(r["planned_start_day"], r["planned_finish_day"], r["task_id"]) for r in pipe_rows},
                             {(str(task["planned_start_day"]), str(task["planned_finish_day"]), task["task_id"])})
            # unaggregated rows are untouched, and the row count equals the un-aggregated element-step count
            self.assertEqual(len(rows), len(plain.tasks))
            self.assertEqual(sorted({r["element_guid"] for r in rows}), sorted({t.element_guid for t in plain.tasks}))
            # the map file alone also expands (aggregates key)
            sm.aggregates = aggregation.aggregate_map(agg)
            sm_dict = sm.to_dict()
            self.assertEqual(len(export.task_rows(sm_dict)), len(plain.tasks))

    def test_cli_map_aggregate_and_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp / "elements.json", dense_doc().to_dict())
            write_json(tmp / "lib.json", library(STEPS).raw)
            rules_dict = {"schema_version": "1.0", "sector": "industrial", "rules": [
                {"id": r.id, "priority": r.priority, "match": {"ifc_class": r.match.ifc_class},
                 "steps": [{"step": e.step, "quantity": e.quantity} for e in r.steps]} for r in rules(RULES).rules],
                "default": {"steps": [{"step": "TST-DEFAULT-DO", "quantity": "count"}]}}
            write_json(tmp / "rules.json", rules_dict)
            write_json(tmp / "scn.json", scenario({"crew": 5}).to_dict())
            pipeline.run_map(tmp / "elements.json", tmp / "rules.json", tmp / "lib.json", tmp / "map.json", "t",
                             aggregate="default")
            m = read_json(tmp / "map.json")
            self.assertEqual(len(m["aggregates"]), 1)
            self.assertEqual(len(m["aggregated_elements"]), 1)
            pipeline.run_schedule(tmp / "map.json", tmp / "lib.json", tmp / "scn.json", tmp / "elements.json",
                                  tmp / "seq.json", "t")
            ok, msgs = validate_file(tmp / "seq.json", "sequence")
            self.assertTrue(ok, msgs)
            ok, msgs = validate_file(tmp / "map.json", "element_step_map")
            self.assertTrue(ok, msgs)

    def test_sector_bundle_aggregation_stays_valid_and_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            off = pipeline.build_sector("industrial", Path(tmp) / "off", Path("/nonexistent"))
            on = pipeline.build_sector("industrial", Path(tmp) / "on", Path("/nonexistent"), aggregate="default")
            self.assertLessEqual(on.tasks, off.tasks)
            seq = read_json(Path(tmp) / "on" / "industrial" / "sequence.json")
            self.assertEqual(schema_errors("sequence", seq), [])
            rows = export.read_csv(Path(tmp) / "on" / "industrial" / "sequence.csv")
            plain = read_json(Path(tmp) / "off" / "industrial" / "sequence.json")
            self.assertEqual(len(rows), len(plain["tasks"]))


class GridDetect(unittest.TestCase):
    @staticmethod
    def lattice(spacing, rot_deg, n=5, m=4, origin=(100.0, 50.0)):
        items = []
        for i in range(n):
            for j in range(m):
                x, y = grid_detect.rotate(i * spacing, j * spacing, rot_deg)
                cx, cy = origin[0] + x, origin[1] + y
                items.append(grid_detect.GridItem("IfcColumn", (cx - 0.2, cy - 0.2, 0.0), (cx + 0.2, cy + 0.2, 4.0)))
        return items

    def walls_and_beams(self, rot_deg, origin=(100.0, 50.0)):
        out = []
        for k in range(6):
            x, y = grid_detect.rotate(k * 7.5, 0, rot_deg)
            cx, cy = origin[0] + x, origin[1] + y
            cls = "IfcWall" if k % 2 else "IfcBeam"
            out.append(grid_detect.GridItem(cls, (cx - 3, cy - 3, 0), (cx + 3, cy + 3, 3), axis_deg=rot_deg + (90 if k % 3 == 0 else 0)))
        return out

    def test_columns_7_5_m_rotated_30_degrees(self):
        items = self.lattice(7.5, 30) + self.walls_and_beams(30)
        res = grid_detect.detect_grid(items)
        self.assertEqual(res["cell_size_m"], 7.5)
        self.assertEqual(res["rotation_deg"], 30.0)
        self.assertEqual(res["columns"], 20)
        # origin = min corner of everything in the rotated frame, mapped back to model space
        u, v = res["origin_rotated"]
        x, y = grid_detect.rotate(u, v, 30.0)
        self.assertAlmostEqual(res["origin"][0], x, 2)
        self.assertAlmostEqual(res["origin"][1], y, 2)
        for it in items:
            for cx in (it.lo[0], it.hi[0]):
                for cy in (it.lo[1], it.hi[1]):
                    ru, rv = grid_detect.rotate(cx, cy, -30.0)
                    self.assertGreaterEqual(ru, u - 1e-6)
                    self.assertGreaterEqual(rv, v - 1e-6)

    def test_rotation_from_columns_only_and_unrotated(self):
        self.assertEqual(grid_detect.detect_grid(self.lattice(7.5, 30))["rotation_deg"], 30.0)
        res = grid_detect.detect_grid(self.lattice(9.0, 0))
        self.assertEqual((res["cell_size_m"], res["rotation_deg"]), (9.0, 0.0))
        self.assertEqual(grid_detect.detect_grid(self.lattice(6.0, -20))["rotation_deg"], -20.0)

    def test_snapping_clamping_and_defaults(self):
        self.assertEqual(grid_detect.detect_grid(self.lattice(7.3, 0))["cell_size_m"], 7.5)
        self.assertEqual(grid_detect.detect_grid(self.lattice(1.5, 0))["cell_size_m"], 3.0)
        self.assertEqual(grid_detect.detect_grid(self.lattice(30, 0))["cell_size_m"], 12.0)
        none = grid_detect.detect_grid([grid_detect.GridItem("IfcWall", (0, 0, 0), (5, 0.3, 3))])
        self.assertEqual((none["cell_size_m"], none["rotation_deg"]), (6.0, 0.0))
        self.assertEqual(grid_detect.detect_grid([])["cell_size_m"], 6.0)
        # bbox long axis only (no axis angle): east-west walls -> 0 degrees; angles fold modulo 90
        e = [grid_detect.GridItem("IfcWall", (0, 0, 0), (8, 0.3, 3)), grid_detect.GridItem("IfcBeam", (0, 5, 0), (0.3, 12, 3))]
        self.assertEqual(grid_detect.estimate_rotation(e), 0.0)
        self.assertEqual(grid_detect.estimate_rotation([grid_detect.GridItem("IfcWall", (0, 0, 0), (5, 5, 3), axis_deg=120)]), 30.0)

    def test_nearest_neighbour_large_set_matches_spacing(self):
        rng = random.Random(1)
        pts = [(i * 8.0 + rng.uniform(-0.05, 0.05), j * 8.0) for i in range(60) for j in range(60)]
        d = grid_detect.nearest_neighbour_distances(pts)
        self.assertEqual(len(d), len(pts))
        self.assertAlmostEqual(sorted(d)[len(d) // 2], 8.0, 1)

    def test_cells_in_rotated_grid_and_helpers(self):
        res = grid_detect.detect_grid(self.lattice(7.5, 30) + self.walls_and_beams(30))
        cells = set()
        for it in self.lattice(7.5, 30):
            cells.update(grid_detect.cells_for_bbox_rotated(it.lo, it.hi, res["origin"], 7.5, 30.0))
        self.assertTrue(all(c[0] >= 0 and c[1] >= 0 for c in cells), cells)
        self.assertEqual(max(c[0] for c in cells), 4)
        self.assertEqual(max(c[1] for c in cells), 3)
        self.assertEqual(ifc_extract.cells_for_bbox((0, 0, 0), (12, 6, 1), (0, 0, 0), 6),
                         grid_detect.cells_for_bbox_rotated((0, 0, 0), (12, 6, 1), (0, 0, 0), 6, 0.0))
        self.assertAlmostEqual(ifc_extract.plan_axis_deg([0, 4, 8], [0, 2.3094, 4.6188]), 30.0, 1)
        self.assertIsNone(ifc_extract.plan_axis_deg([0, 1], [0, 1]))

    def test_cli_grid_detect_on_elements_json(self):
        import io
        import json
        from contextlib import redirect_stdout
        from bimseq import __main__ as cli
        d = elements_from_dict(__import__("bimseq.synth", fromlist=["x"]).GENERATORS["healthcare"]())
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / "e.json", d.to_dict())
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["grid-detect", str(Path(tmp) / "e.json")])
        res = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual((res["cell_size_m"], res["rotation_deg"]), (6.0, 0.0))        # synthetic hospital: 6 m bays
        self.assertGreater(res["columns"], 100)


if __name__ == "__main__":
    unittest.main()
