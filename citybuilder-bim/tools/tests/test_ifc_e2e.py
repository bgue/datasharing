import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path

from helpers import REPO  # noqa: F401  (sets sys.path)
from bimseq import config as cfgmod, ifc_extract, pipeline, synth_ifc
from bimseq.__main__ import main as cli_main
from bimseq.bundle import load_bundle
from bimseq.model import read_json, write_json
from bimseq.validate import schema_errors, validate_file

HAVE = ifc_extract.available()
SECTORS = REPO / "data" / "sectors"


@unittest.skipUnless(HAVE, "ifcopenshell not installed")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.res = {}
        for sector in ("healthcare", "industrial", "civil"):
            ifc = cls.dir / f"{sector}.ifc"
            cls.info = synth_ifc.synth_ifc(sector, ifc)
            cfg = cfgmod.from_dict({"schema_version": "1.0", "sector": sector,
                                    **({"grid": {"mode": "chainage", "chainage": {"axis": "x", "cell_length_m": 6, "lanes": 6,
                                                                                  "lane_width_m": 6}}} if sector == "civil" else {})})
            cls.res[sector] = ifc_extract.extract_to(str(ifc), cls.dir / f"{sector}_elements.json", sector=sector, config=cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def doc(self, sector):
        return read_json(self.dir / f"{sector}_elements.json")

    def run_pipeline(self, sector):
        d = self.dir
        S = SECTORS / sector
        self.assertEqual(cli_main(["map", str(d / f"{sector}_elements.json"), "--rules", str(S / "mapping_rules.json"),
                                   "--library", str(S / "step_library.json"), "--out", str(d / f"{sector}_map.json")]), 0)
        self.assertEqual(cli_main(["schedule", str(d / f"{sector}_map.json"), "--library", str(S / "step_library.json"),
                                   "--scenario", str(S / "scenario_standard.json"), "--elements", str(d / f"{sector}_elements.json"),
                                   "--out", str(d / f"{sector}_seq.json"), "--crew-model", "fractional"]), 0)
        return read_json(d / f"{sector}_map.json"), read_json(d / f"{sector}_seq.json")

    def test_hospital_end_to_end(self):
        doc = self.doc("healthcare")
        self.assertEqual(schema_errors("elements", doc), [])
        self.assertTrue(800 <= len(doc["elements"]) <= 900, len(doc["elements"]))
        g = doc["project"]["grid"]
        self.assertEqual((g["cell_size_m"], g["rotation_deg"], g["mode"]), (6.0, 0.0, "auto"))
        self.assertEqual(g["detected"]["cell_size_m"], 6.0)
        self.assertGreater(g["detected"]["columns"], 100)
        tags = {t for z in doc["zones"] for t in z["tags"]}
        self.assertTrue({"or_room", "imaging", "plant_room", "ward", "corridor", "pressure_room"} <= tags, tags)
        theatre = [z for z in doc["zones"] if "or_room" in z["tags"]]
        self.assertEqual(len(theatre), 4)
        self.assertTrue(all(z["max_crews"] == 3 and z["faces"]["ceiling_void"] <= 3 for z in theatre))
        self.assertTrue(all(z.get("shift_allowed") is False for z in theatre))
        self.assertEqual({s["id"] for s in doc["storeys"]}, {"L00", "L01", "L02"})
        els = doc["elements"]
        self.assertTrue(any(e["properties"].get("IsExternal") for e in els))
        self.assertTrue(any(e["properties"].get("Shielding") for e in els))
        self.assertTrue(any(e["material"] for e in els))
        self.assertGreater(len({e["system_id"] for e in els if e["system_id"]}), 8)
        hosted = [e for e in els if e["host_guid"]]
        self.assertTrue(len(hosted) >= 70 and all(e["ifc_class"] in ("IfcDoor", "IfcWindow") for e in hosted))
        guids = {e["guid"] for e in els}
        self.assertTrue(all(e["host_guid"] in guids for e in hosted))
        mri = next(e for e in els if e["ifc_class"] == "IfcMedicalDevice" and e["name"] == "MRI scanner")
        self.assertAlmostEqual(mri["quantities"]["weight_t"], 6.8, 2)            # from IfcElementQuantity, not bbox
        zone_ids = {z["id"] for z in doc["zones"]}
        self.assertTrue(all(e["zone_id"] in zone_ids for e in els))
        m, seq = self.run_pipeline("healthcare")
        self.assertTrue(1300 <= len(seq["tasks"]) <= 1900, len(seq["tasks"]))
        self.assertEqual(m["sequencing_gaps"], [])
        self.assertTrue(len(seq["packages"]) > 100)
        self.assertTrue(20 <= seq["baseline"]["finish_week"] <= 60, seq["baseline"]["finish_week"])
        ok, msgs = validate_file(self.dir / "healthcare_seq.json", "sequence")
        self.assertTrue(ok, msgs)

    def test_industrial_end_to_end(self):
        doc = self.doc("industrial")
        self.assertEqual(schema_errors("elements", doc), [])
        self.assertEqual(len(doc["elements"]), 855)
        self.assertEqual(doc["project"]["grid"]["cell_size_m"], 6.0)
        tags = {t for z in doc["zones"] for t in z["tags"]}
        self.assertTrue({"process_unit", "pipe_rack", "control_room", "switchroom", "pump_house"} <= tags, tags)
        self.assertTrue(any(e["properties"].get("Module") is True for e in doc["elements"]))
        m, seq = self.run_pipeline("industrial")
        self.assertEqual(m["sequencing_gaps"], [])
        self.assertTrue(1800 <= len(seq["tasks"]) <= 2800, len(seq["tasks"]))
        ok, msgs = validate_file(self.dir / "industrial_seq.json", "sequence")
        self.assertTrue(ok, msgs)

    def test_civil_chainage_end_to_end(self):
        doc = self.doc("civil")
        g = doc["project"]["grid"]
        self.assertEqual((g["mode"], g["cell_size_m"]), ("chainage", 6.0))
        self.assertEqual((g["width_cells"], g["depth_cells"]), (30, 6))          # 30 cells along x, 6 lanes
        self.assertEqual(g["detected"]["axis"], "x")
        self.assertTrue(all(0 <= c[0] < 30 and 0 <= c[1] < 6 for e in doc["elements"] for c in e["cells"]))
        tags = {t for z in doc["zones"] for t in z["tags"]}
        self.assertTrue({"live_traffic", "bridge", "culvert"} <= tags, tags)
        self.assertEqual(sorted(s["index"] for s in doc["storeys"]), [-1, 0])
        m, seq = self.run_pipeline("civil")
        self.assertEqual(m["sequencing_gaps"], [])
        self.assertTrue(1500 <= len(seq["tasks"]) <= 2300, len(seq["tasks"]))
        ok, msgs = validate_file(self.dir / "civil_seq.json", "sequence")
        self.assertTrue(ok, msgs)

    def test_chainage_axis_auto_picks_the_long_axis(self):
        cfg = cfgmod.from_dict({"schema_version": "1.0", "grid": {"mode": "chainage", "chainage": {"cell_length_m": 6}}})
        res = ifc_extract.extract_to(str(self.dir / "civil.ifc"), None, sector="civil", config=cfg)
        self.assertEqual(res.head["project"]["grid"]["detected"]["axis"], "x")

    def test_space_zoning_off_gives_auto_blocks(self):
        cfg = cfgmod.from_dict({"schema_version": "1.0", "zones": {"source": "auto", "auto_block": [4, 2]}})
        cfg.zones.source = "file"           # no file: falls back to auto blocks only
        res = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfgmod.from_dict(
            {"schema_version": "1.0", "zones": {"source": "file"}}))
        self.assertTrue(res.head["zones"])
        self.assertTrue(all(z["id"].split("-")[1].startswith("A") for z in res.head["zones"]))

    def test_filters_and_scope(self):
        base = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare")
        n = base.count
        cfg = cfgmod.from_dict({"schema_version": "1.0", "filters": {"include_ifc_classes": ["IfcWall", "IfcColumn"]}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertTrue(0 < r.count < n)
        self.assertEqual({e["ifc_class"] for e in r.elements}, {"IfcWall", "IfcColumn"})
        cfg = cfgmod.from_dict({"schema_version": "1.0", "filters": {"exclude_ifc_classes": ["IfcLightFixture", "IfcAirTerminal"]}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertEqual(r.count, n - 72)
        cfg = cfgmod.from_dict({"schema_version": "1.0", "filters": {"include_storeys": ["Level 1"]}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertEqual({s["id"] for s in r.head["storeys"]}, {"L00"})            # re-indexed: the only storey is ground
        self.assertTrue(0 < r.count < n // 2 + 100)
        cfg = cfgmod.from_dict({"schema_version": "1.0", "filters": {"min_bbox_m": 1.0}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertLess(r.count, n)
        cfg = cfgmod.from_dict({"schema_version": "1.0", "filters": {"bbox": {"min": [0, 0, -10], "max": [20, 40, 50]}}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertTrue(0 < r.count < n)
        cfg = cfgmod.from_dict({"schema_version": "1.0", "scope": {"systems": ["CHW"]}})
        r = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), None, sector="healthcare", config=cfg)
        self.assertEqual({s["id"] for s in r.head["systems"]}, {"CHW"})
        self.assertTrue(all(e["system_id"] in (None, "CHW") for e in r.elements))

    def test_streaming_parts_and_gzip(self):
        cfg = cfgmod.from_dict({"schema_version": "1.0", "output": {"compress": True, "elements_per_part": 1000}})
        out = self.dir / "streamed" / "elements.json"
        res = ifc_extract.extract_to(str(self.dir / "industrial.ifc"), out, sector="industrial", config=cfg)
        self.assertEqual([f.name for f in res.files], ["elements.json.gz"])        # 855 < 1000: one compressed document
        cfg = cfgmod.from_dict({"schema_version": "1.0", "output": {"compress": True, "elements_per_part": 1000}})
        cfg.output.elements_per_part = 300
        res = ifc_extract.extract_to(str(self.dir / "healthcare.ifc"), self.dir / "streamed2" / "elements.json",
                                     sector="healthcare", config=cfg)
        parts = [f for f in res.files if ".part-" in f.name]
        self.assertGreater(len(parts), 0)
        index = next(f for f in res.files if f.name.endswith(".index.json"))
        self.assertEqual(json.loads(index.read_text())["element_count"], res.count)
        with gzip.open(parts[0], "rt") as fh:
            part = json.load(fh)
        self.assertEqual(schema_errors("elements", part), [])           # every part is a complete elements document
        from bimseq.model import load_elements
        merged = load_elements(index)
        self.assertEqual(len(merged.elements), res.count)
        self.assertEqual(len({e.guid for e in merged.elements}), res.count)
        self.assertTrue(validate_file(parts[0], "elements")[0])

    def test_cli_synth_and_extract_and_grid_detect(self):
        ifc = self.dir / "cli.ifc"
        self.assertEqual(cli_main(["synth-ifc", "--sector", "industrial", "--out", str(ifc)]), 0)
        out = self.dir / "cli_elements.json"
        self.assertEqual(cli_main(["ifc-to-elements", str(ifc), str(out), "--sector", "industrial"]), 0)
        self.assertEqual(len(read_json(out)["elements"]), 855)
        self.assertEqual(cli_main(["grid-detect", str(ifc)]), 0)


class MissingIfcopenshell(unittest.TestCase):
    def test_exit_code_3_when_unavailable(self):
        from unittest import mock
        import io
        from contextlib import redirect_stderr
        with mock.patch.object(ifc_extract, "available", return_value=False), redirect_stderr(io.StringIO()) as err:
            rc = cli_main(["ifc-to-elements", "model.ifc", "out.json"])
        self.assertEqual(rc, 3)
        self.assertIn("ifcopenshell", err.getvalue())


@unittest.skipUnless(HAVE, "ifcopenshell not installed")
class RotatedGrid(unittest.TestCase):
    """Columns on a 7.5 m lattice rotated 30 degrees, walls and beams along the same axes."""

    @staticmethod
    def build(path, spacing=7.5, rot=30.0):
        import ifcopenshell.guid as guid
        w = synth_ifc._Writer("IFC4", "Rotated")
        f = w.f
        bld = f.create_entity("IfcBuilding", GlobalId=guid.new(), Name="B")
        f.createIfcRelAggregates(guid.new(), None, None, None, w.site, [bld])
        st = f.create_entity("IfcBuildingStorey", GlobalId=guid.new(), Name="Ground", Elevation=0.0)
        f.createIfcRelAggregates(guid.new(), None, None, None, bld, [st])
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

        def box(cls, name, ox, oy, dx, dy, dz):
            x, y = ox * c - oy * s + 100.0, ox * s + oy * c + 50.0
            ax = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((x, y, 0.0)), f.createIfcDirection((0.0, 0.0, 1.0)),
                                             f.createIfcDirection((c, s, 0.0)))
            pl = f.createIfcLocalPlacement(None, ax)
            prof = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(
                f.createIfcCartesianPoint((dx / 2, dy / 2))), dx, dy)
            solid = f.createIfcExtrudedAreaSolid(prof, w._origin, w._dir, dz)
            shape = f.createIfcProductDefinitionShape(None, None, [f.createIfcShapeRepresentation(w.body, "Body", "SweptSolid", [solid])])
            return f.create_entity(cls, GlobalId=guid.new(), Name=name, ObjectPlacement=pl, Representation=shape)
        ents = []
        for i in range(5):
            for j in range(4):      # columns sit away from cell boundaries so the cell assertion is robust
                ents.append(box("IfcColumn", f"C{i}{j}", i * spacing + 3.0, j * spacing + 3.0, 0.4, 0.4, 4.0))
        for i in range(4):
            ents.append(box("IfcWall", f"W{i}", i * spacing, 0.0, spacing, 0.3, 3.0))
            ents.append(box("IfcBeam", f"B{i}", i * spacing, 0.0, spacing, 0.3, 0.5))
        f.createIfcRelContainedInSpatialStructure(guid.new(), None, None, None, ents, st)
        f.write(str(path))

    def test_auto_grid_on_rotated_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            ifc = Path(tmp) / "rot.ifc"
            self.build(ifc)
            res = ifc_extract.extract_to(str(ifc), None, sector="industrial")
            g = res.head["project"]["grid"]
            self.assertEqual((g["cell_size_m"], g["rotation_deg"]), (7.5, 30.0))
            doc = res.document()
            self.assertEqual(schema_errors("elements", doc), [])
            cols = [e for e in doc["elements"] if e["ifc_class"] == "IfcColumn"]
            cells = {tuple(e["cells"][0]) for e in cols}
            self.assertEqual(len(cells), 20)                       # every column in its own cell of the rotated grid
            self.assertEqual((max(c[0] for c in cells), max(c[1] for c in cells)), (4, 3))
            # a forced rotation of 0 and a fixed 6 m cell reproduce the legacy behaviour
            cfg = cfgmod.from_dict({"schema_version": "1.0", "grid": {"mode": "fixed", "cell_size_m": 6}})
            fixed = ifc_extract.extract_to(str(ifc), None, sector="industrial", config=cfg)
            self.assertEqual((fixed.head["project"]["grid"]["cell_size_m"], fixed.head["project"]["grid"]["rotation_deg"]), (6.0, 0.0))


@unittest.skipUnless(HAVE, "ifcopenshell not installed")
class IfcZonesAndAreas(unittest.TestCase):
    def test_two_buildings_become_areas_and_ifc_zone_groups_merge(self):
        import ifcopenshell.guid as guid
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "two.ifc"
            w = synth_ifc._Writer("IFC4", "Campus")
            f = w.f
            ents, spaces = [], []
            for b, x0 in (("North wing", 0.0), ("South wing", 60.0)):
                bld = f.create_entity("IfcBuilding", GlobalId=guid.new(), Name=b)
                f.createIfcRelAggregates(guid.new(), None, None, None, w.site, [bld])
                st = f.create_entity("IfcBuildingStorey", GlobalId=guid.new(), Name=f"{b} L0", Elevation=0.0)
                f.createIfcRelAggregates(guid.new(), None, None, None, bld, [st])
                members = []
                for k in range(4):
                    pl, sh = w.geometry((x0 + k * 12, 0, 0), (x0 + k * 12 + 11.9, 5.9, 3))
                    members.append(f.create_entity("IfcWall", GlobalId=guid.new(), Name=f"{b} wall {k}", ObjectPlacement=pl, Representation=sh))
                f.createIfcRelContainedInSpatialStructure(guid.new(), None, None, None, members, st)
                rooms = []
                for k in range(4):
                    pl, sh = w.geometry((x0 + k * 12 + 0.1, 0.1, 0), (x0 + k * 12 + 11.8, 5.8, 3))
                    rooms.append(f.create_entity("IfcSpace", GlobalId=guid.new(), Name=f"Room {k}", ObjectPlacement=pl, Representation=sh))
                f.createIfcRelAggregates(guid.new(), None, None, None, st, rooms)
                if b == "North wing":
                    z = f.create_entity("IfcZone", GlobalId=guid.new(), Name="Ward A")
                    f.createIfcRelAssignsToGroup(guid.new(), None, None, None, rooms[:3], None, z)
            f.write(str(path))
            res = ifc_extract.extract_to(str(path), None, sector="healthcare")
            areas = res.head["project"]["areas"]
            self.assertEqual([a["name"] for a in areas], ["North wing", "South wing"])
            self.assertTrue(all(a["cells"] and a["camera_bookmark"]["cells"] for a in areas))
            self.assertEqual(schema_errors("elements", res.document()), [])
            cfg = cfgmod.from_dict({"schema_version": "1.0", "zones": {"source": "ifc_zone"}})
            res2 = ifc_extract.extract_to(str(path), None, sector="healthcare", config=cfg)
            ward = [z for z in res2.head["zones"] if z["name"] == "Ward A"]
            self.assertEqual(len(ward), 1)
            self.assertGreaterEqual(len(ward[0]["cells"]), 6)             # three 2-cell rooms merged into one zone
            self.assertIn("ward", ward[0]["tags"])
            cfg3 = cfgmod.from_dict({"schema_version": "1.0", "scope": {"buildings": ["South wing"]}})
            res3 = ifc_extract.extract_to(str(path), None, sector="healthcare", config=cfg3)
            self.assertEqual(res3.count, 4)


if __name__ == "__main__":
    unittest.main()
