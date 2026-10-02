import unittest
from collections import Counter

from helpers import TOOLS  # noqa: F401  (sets sys.path)
from bimseq.synth import GENERATORS
from bimseq.validate import schema_errors
from bimseq.visuals import VISUALS


class Generators(unittest.TestCase):
    docs = {name: gen() for name, gen in GENERATORS.items()}

    def test_validate_against_elements_schema(self):
        for name, d in self.docs.items():
            with self.subTest(sector=name):
                self.assertEqual(schema_errors("elements", d), [])

    def test_size_and_determinism(self):
        for name, gen in GENERATORS.items():
            with self.subTest(sector=name):
                d = self.docs[name]
                self.assertTrue(200 <= len(d["elements"]) <= 900, len(d["elements"]))
                self.assertEqual(d, gen(42))
                self.assertNotEqual(d["elements"][0]["guid"], gen(7)["elements"][0]["guid"])
                self.assertEqual(d["project"]["sector"], name)
                self.assertEqual(d["project"]["grid"]["cell_size_m"], 6)
                self.assertEqual(d["project"]["grid"]["storey_height_m"], 4)

    def test_integrity(self):
        for name, d in self.docs.items():
            with self.subTest(sector=name):
                guids = [e["guid"] for e in d["elements"]]
                self.assertEqual(len(guids), len(set(guids)))
                zones = {z["id"]: z for z in d["zones"]}
                storeys = {s["id"] for s in d["storeys"]}
                systems = {s["id"] for s in d["systems"]}
                by_guid = {e["guid"]: e for e in d["elements"]}
                w, dd = d["project"]["grid"]["width_cells"], d["project"]["grid"]["depth_cells"]
                for z in d["zones"]:
                    self.assertTrue(4 <= len(z["cells"]) <= 12, z["id"])
                    self.assertGreaterEqual(z["max_crews"], 1)
                    self.assertIn(z["storey_id"], storeys)
                for e in d["elements"]:
                    self.assertIn(e["storey_id"], storeys)
                    z = zones[e["zone_id"]]
                    self.assertEqual(z["storey_id"], e["storey_id"])
                    self.assertIn(e["cells"][0], z["cells"])
                    if e["system_id"]:
                        self.assertIn(e["system_id"], systems)
                    if e["host_guid"]:
                        self.assertEqual(by_guid[e["host_guid"]]["storey_id"], e["storey_id"])
                    self.assertIn(e["visual"], VISUALS)
                    for c in e["cells"]:
                        self.assertTrue(0 <= c[0] < w and 0 <= c[1] < dd, (e["name"], c))
                    self.assertTrue(all(a <= b for a, b in zip(e["bbox"]["min"], e["bbox"]["max"])))
                    self.assertGreater(e["quantities"].get("count", 0), 0)

    def test_industrial_content(self):
        d = self.docs["industrial"]
        self.assertEqual(len(d["storeys"]), 1)
        cls = Counter(e["ifc_class"] for e in d["elements"])
        for c in ("IfcMember", "IfcPipeSegment", "IfcTank", "IfcCableCarrierSegment", "IfcEnergyConversionDevice"):
            self.assertGreater(cls[c], 0, c)
        modules = [e for e in d["elements"] if e["properties"].get("Module") is True]
        self.assertTrue(8 <= len(modules) <= 12)
        self.assertTrue(all(20 <= m["quantities"]["weight_t"] <= 120 for m in modules))
        self.assertTrue(any(s["id"].startswith("PR-") for s in d["systems"]))
        self.assertEqual(d["project"]["grid"]["width_cells"], 12)
        self.assertTrue(any("heavy_lift_area" in z["tags"] for z in d["zones"]))

    def test_civil_content(self):
        d = self.docs["civil"]
        self.assertEqual(sorted(s["index"] for s in d["storeys"]), [-1, 0])
        self.assertEqual(d["project"]["grid"]["width_cells"], 30)
        cls = Counter(e["ifc_class"] for e in d["elements"])
        for c in ("IfcEarthworksCut", "IfcEarthworksFill", "IfcPavement", "IfcCourse", "IfcKerb", "IfcPile",
                  "IfcDeepFoundation", "IfcColumn", "IfcBeam", "IfcSlab", "IfcBearing", "IfcSign", "IfcRailing",
                  "IfcPipeSegment"):
            self.assertGreater(cls[c], 0, c)
        parts = {e["properties"].get("BridgePart") for e in d["elements"]}
        self.assertTrue({"pier", "girder", "deck"} <= parts)
        self.assertTrue(any(e["ifc_class"] == "IfcBuildingElementProxy" and "Culvert" in e["name"] for e in d["elements"]))
        self.assertTrue(any("live_traffic" in z["tags"] for z in d["zones"]))
        self.assertTrue(any("live_traffic" not in z["tags"] for z in d["zones"] if z["storey_id"] == "L00"))
        under = [e for e in d["elements"] if e["storey_id"] == "UG1"]
        self.assertTrue(under and all(e["ifc_class"] in ("IfcPipeSegment", "IfcCableCarrierSegment",
                                                         "IfcDistributionChamberElement") for e in under))

    def test_healthcare_content(self):
        d = self.docs["healthcare"]
        self.assertEqual(len(d["storeys"]), 3)
        self.assertEqual(d["project"]["grid"]["width_cells"], 8)
        cls = Counter(e["ifc_class"] for e in d["elements"])
        for c in ("IfcFooting", "IfcColumn", "IfcSlab", "IfcWall", "IfcCurtainWall", "IfcDoor", "IfcWindow",
                  "IfcCovering", "IfcDuctSegment", "IfcPipeSegment", "IfcCableCarrierSegment", "IfcLightFixture",
                  "IfcAirTerminal", "IfcSanitaryTerminal", "IfcMedicalDevice"):
            self.assertGreater(cls[c], 0, c)
        self.assertTrue(any(e["properties"].get("IsExternal") for e in d["elements"] if e["ifc_class"] == "IfcWall"))
        self.assertTrue(any(e["properties"].get("Shielding") for e in d["elements"] if e["ifc_class"] == "IfcWall"))
        self.assertTrue(any(e["properties"].get("Hygienic") for e in d["elements"] if e["ifc_class"] == "IfcCovering"))
        self.assertTrue(any(e["properties"].get("Hygienic") is False for e in d["elements"] if e["ifc_class"] == "IfcCovering"))
        sys_ids = {s["id"] for s in d["systems"]}
        for sid in ("AHU-1", "MG-O2", "MG-MA", "CHW", "HHW", "DW"):
            self.assertIn(sid, sys_ids)
        for e in d["elements"]:
            if e["ifc_class"] in ("IfcDoor", "IfcWindow"):
                self.assertIsNotNone(e["host_guid"])
        tags = {t for z in d["zones"] for t in z["tags"]}
        self.assertTrue({"occupied_adjacent", "pressure_room"} <= tags)
        self.assertTrue(all(e["quantities"]["weight_t"] > 0 for e in d["elements"] if e["ifc_class"] == "IfcMedicalDevice"))


if __name__ == "__main__":
    unittest.main()
