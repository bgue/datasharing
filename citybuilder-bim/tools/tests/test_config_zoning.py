import json
import tempfile
import unittest
from pathlib import Path

from helpers import REPO  # noqa: F401  (sets sys.path)
from bimseq import config as cfgmod, zoning
from bimseq.validate import validate_file


class ProjectConfig(unittest.TestCase):
    def test_defaults_from_schema(self):
        c = cfgmod.from_dict({"schema_version": "1.0"})
        self.assertEqual((c.grid.mode, c.grid.cell_size_m, c.grid.snap_m, c.grid.min_cell_m, c.grid.max_cell_m),
                         ("auto", None, 0.5, 3.0, 12.0))
        self.assertEqual((c.grid.chainage.axis, c.grid.chainage.cell_length_m, c.grid.chainage.lanes,
                          c.grid.chainage.lane_width_m), ("auto", 25.0, 6, 3.5))
        self.assertEqual((c.zones.source, c.zones.auto_block, c.zones.max_crews_default,
                          c.zones.merge_small_spaces_below_cells), ("auto", (3, 3), 2, 2))
        self.assertEqual((c.aggregation.preset, c.aggregation.max_members, c.aggregation.threshold_per_cell), (None, 200, 25))
        self.assertIn("IfcSpace", c.filters.exclude_ifc_classes)
        self.assertEqual((c.filters.min_bbox_m, c.scope.buildings, c.scope.systems), (0.05, ["*"], ["*"]))
        self.assertEqual((c.output.compress, c.output.tasks_per_part, c.output.lazy_zone_detail), (False, 5000, False))
        self.assertIsNone(c.aggregation_preset())

    def test_aggregation_preset_with_overrides(self):
        c = cfgmod.from_dict({"schema_version": "1.0", "aggregation": {"preset": "healthcare_mep"}})
        self.assertEqual(c.aggregation_preset()["group_by"], ["storey", "zone", "kit_or_class", "system"])
        self.assertEqual(c.aggregation_preset()["max_members"], 120)                 # preset value, not the schema default
        c = cfgmod.from_dict({"schema_version": "1.0", "aggregation": {"preset": "generic", "max_members": 50,
                                                                       "threshold_per_cell": 10}})
        p = c.aggregation_preset()
        self.assertEqual((p["max_members"], p["threshold_per_cell"]), (50, 10))

    def test_load_validates_and_resolves_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "project_config.json").write_text(json.dumps({"schema_version": "1.0", "grid": {"mode": "chainage"},
                                                                 "zones": {"space_tags": "tags.json"}}))
            c = cfgmod.load_project_config(tmp / "project_config.json")
            self.assertEqual(c.grid.mode, "chainage")
            self.assertEqual(c.resolve(c.zones.space_tags), tmp / "tags.json")
            (tmp / "bad.json").write_text(json.dumps({"schema_version": "1.0", "grid": {"mode": "spiral"}}))
            with self.assertRaises(ValueError):
                cfgmod.load_project_config(tmp / "bad.json")
            self.assertEqual(cfgmod.load_project_config(None).grid.mode, "auto")


def rec(guid, name, sid, cells, **kw):
    return zoning.SpaceRec(guid, name, sid, [tuple(c) for c in cells], **kw)


class SpaceTagRules(unittest.TestCase):
    RULES = [
        {"id": "or", "match": {"name_regex": "(?i)theat"}, "tags": ["or_room"], "max_crews": 3, "faces": {"walls": 2}, "shift_allowed": False},
        {"id": "ward-live", "match": {"name_regex": "(?i)ward", "long_name_regex": "(?i)live"}, "tags": ["ward", "occupied_adjacent"]},
        {"id": "ward", "match": {"name_regex": "(?i)ward"}, "tags": ["ward"], "max_crews": 2},
        {"id": "prop", "match": {"properties": {"Kind": "plant"}}, "tags": ["plant_room"], "max_crews": 4},
        {"id": "fallback", "match": {"name_regex": ".*"}, "tags": ["other"], "max_crews": 2},
    ]

    def test_first_match_wins_and_match_keys_are_anded(self):
        m = lambda r: zoning.match_rule(self.RULES, r)["id"]
        self.assertEqual(m(rec("1", "Operating Theatre 1", "L00", [(0, 0)])), "or")
        self.assertEqual(m(rec("2", "Ward A", "L00", [(0, 0)], long_name="Live wing")), "ward-live")
        self.assertEqual(m(rec("3", "Ward A", "L00", [(0, 0)], long_name="Quiet")), "ward")
        self.assertEqual(m(rec("4", "Room 9", "L00", [(0, 0)], props={"Kind": "plant"})), "prop")
        self.assertEqual(m(rec("5", "Store", "L00", [(0, 0)])), "fallback")
        self.assertIsNone(zoning.match_rule(self.RULES[:1], rec("6", "Store", "L00", [(0, 0)])))

    def test_shipped_rule_files_validate_and_match_names(self):
        for sector, names in {"healthcare": {"Operating Theatre 2": "or_room", "MRI 1": "imaging", "Plant Room": "plant_room",
                                             "Ward A": "ward", "Corridor": "corridor"},
                              "industrial": {"Switchroom": "switchroom", "Control Room": "control_room",
                                             "Pump House": "pump_house", "Pipe Rack 1": "pipe_rack",
                                             "Process Unit 3": "process_unit"},
                              "civil": {"Live Lane 1": "live_traffic", "Bridge Span 2": "bridge", "Culvert": "culvert"}}.items():
            path = REPO / "data" / "zoning" / f"space_tags_{sector}.json"
            ok, msgs = validate_file(path)
            self.assertTrue(ok, msgs)
            rules = zoning.load_space_tag_rules(path)
            for name, tag in names.items():
                rule = zoning.match_rule(rules, rec("x", name, "L00", [(0, 0)]))
                self.assertIn(tag, rule["tags"], f"{sector}: {name}")


class BuildZones(unittest.TestCase):
    RULES = SpaceTagRules.RULES

    def build(self, spaces, occupied, **kw):
        return zoning.build_zones(spaces, self.RULES, occupied, storey_order=["L00", "L01"], **kw)

    def test_spaces_become_tagged_zones_with_caps_and_faces(self):
        sp = [rec("a", "Operating Theatre 1", "L00", [(0, 0), (1, 0), (0, 1), (1, 1)]),
              rec("b", "Ward A", "L00", [(2, 0), (3, 0), (2, 1)])]
        zones, zone_of = self.build(sp, {"L00": {(x, z) for x in range(4) for z in range(2)} - {(3, 1)}})
        by_name = {z["name"]: z for z in zones}
        theatre = by_name["Operating Theatre 1"]
        self.assertEqual((theatre["tags"], theatre["max_crews"], theatre["faces"], theatre["shift_allowed"]),
                         (["or_room"], 3, {"walls": 2}, False))
        self.assertEqual(by_name["Ward A"]["max_crews"], 2)
        self.assertEqual(zone_of[("L00", (1, 1))], theatre["id"])
        self.assertTrue(all(len(z["cells"]) >= 1 for z in zones))
        ids = [z["id"] for z in zones]
        self.assertEqual(len(ids), len(set(ids)))

    def test_leftover_cells_become_auto_block_zones(self):
        sp = [rec("a", "Ward A", "L00", [(0, 0), (1, 0)])]
        occupied = {"L00": {(x, 0) for x in range(8)}, "L01": {(0, 0), (1, 0), (2, 0), (3, 0)}}
        zones, zone_of = self.build(sp, occupied, auto_block=(3, 3))
        self.assertEqual({z["storey_id"] for z in zones}, {"L00", "L01"})
        auto = [z for z in zones if z["name"].endswith(tuple("123456789")) and "area" in z["name"]]
        self.assertEqual(sum(len(z["cells"]) for z in auto if z["storey_id"] == "L00"), 6)      # cells 2..7 outside the ward
        self.assertTrue(all((sid, c) in zone_of for sid, cs in occupied.items() for c in cs))
        self.assertTrue(all(z["max_crews"] == 2 for z in auto))

    def test_small_spaces_merge_into_the_neighbour_they_touch(self):
        sp = [rec("a", "Ward A", "L00", [(0, 0), (1, 0), (2, 0), (3, 0)]),
              rec("b", "Riser", "L00", [(4, 0)]),                          # 1 cell, touches ward A
              rec("c", "Operating Theatre 1", "L00", [(8, 0), (9, 0)])]
        zones, zone_of = self.build(sp, {"L00": {(x, 0) for x in (0, 1, 2, 3, 4, 8, 9)}}, merge_below=2)
        self.assertEqual(sorted(z["name"] for z in zones), ["Operating Theatre 1", "Ward A"])
        ward = next(z for z in zones if z["name"] == "Ward A")
        self.assertIn([4, 0], ward["cells"])
        self.assertIn("other", ward["tags"])                                 # merged space keeps its tags (union)
        alone, _ = self.build([rec("b", "Riser", "L00", [(4, 0)])], {"L00": {(4, 0)}}, merge_below=2)
        self.assertEqual(len(alone), 1)                                      # nothing to merge into

    def test_overlapping_spaces_smaller_one_owns_the_cell(self):
        sp = [rec("a", "Ward A", "L00", [(0, 0), (1, 0), (2, 0)]), rec("b", "Operating Theatre 1", "L00", [(1, 0), (2, 0)])]
        zones, zone_of = self.build(sp, {"L00": {(0, 0), (1, 0), (2, 0)}})
        theatre = next(z for z in zones if z["name"].startswith("Operating"))
        self.assertEqual(zone_of[("L00", (1, 0))], theatre["id"])
        self.assertEqual(zone_of[("L00", (0, 0))], next(z["id"] for z in zones if z["name"] == "Ward A"))

    def test_ifc_zone_groups_merge_spaces(self):
        sp = [rec("a", "Room 1", "L00", [(0, 0), (1, 0)], group="Ward A"), rec("b", "Room 2", "L00", [(2, 0), (3, 0)], group="Ward A"),
              rec("c", "Theatre", "L00", [(8, 0), (9, 0)])]
        zones, _ = self.build(sp, {"L00": {(x, 0) for x in (0, 1, 2, 3, 8, 9)}}, source="ifc_zone")
        ward = next(z for z in zones if z["name"] == "Ward A")
        self.assertEqual(len(ward["cells"]), 4)
        self.assertEqual(ward["tags"], ["ward"])

    def test_csv_zones(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "zones.csv"
            p.write_text("zone_id,name,storey_id,x0,z0,x1,z1,tags,max_crews\nZ1,Block A,L00,0,0,1,1,ward;live,3\nZ2,Gone,L09,0,0,0,0,,\n")
            zones = zoning.zones_from_csv(p, ["L00"])
            self.assertEqual(len(zones), 1)
            self.assertEqual((zones[0]["tags"], zones[0]["max_crews"], len(zones[0]["cells"])), (["ward", "live"], 3, 4))


if __name__ == "__main__":
    unittest.main()
