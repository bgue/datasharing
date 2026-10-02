import json
import tempfile
import unittest
from pathlib import Path

from helpers import doc, elem, library, rule, rules, scenario, step, REPO  # noqa: F401  (sets sys.path)
from bimseq import manual as manual_mod, pipeline
from bimseq.mapper import map_elements
from bimseq.model import elements_from_dict, read_json, step_library_from_dict, write_json
from bimseq.scheduler import build_sequence
from bimseq.validate import schema_errors, validate_file

STEPS = [step("TST-SURVEY-DO"), step("TST-CUT-DO", preds=[("TST-SURVEY-DO", "same_zone")]), step("TST-FORM-DO"),
         step("TST-POUR-DO", preds=[("TST-FORM-DO", "same_element")]), step("TST-STRIKE-DO"),
         step("TST-DEFAULT-DO", phase="fit")]


def els():
    return [elem("a", "A", "G", "G-Z1", [(0, 0)], quantities={"count": 1, "volume_m3": 4.0}),
            elem("b", "B", "G", "G-Z1", [(1, 0)], quantities={"count": 1, "volume_m3": 6.0}),
            elem("c", "C", "G", "G-Z2", [(2, 0)])]


RULES = [rule("R-all", 10, {}, ["TST-FORM-DO", "TST-POUR-DO", "TST-STRIKE-DO"])]


def manual(**kw):
    d = {"schema_version": "1.0", "project": "p"}
    d.update(kw)
    return d


def run(man, steps=None, elements=None, rule_list=None, recipes=None):
    lib = step_library_from_dict(library(steps or STEPS).raw)
    d = doc(elements or els())
    sm = map_elements(d, lib, rules(rule_list or RULES), generated_at="t", manual=man, recipes=recipes)
    return sm, lib, d


def of(sm, **kw):
    return [t for t in sm.tasks if all(getattr(t, k) == v for k, v in kw.items())]


def preds(sm, t):
    byid = {x.task_id: x for x in sm.tasks}
    return sorted((byid[p.task_id].manual_id or byid[p.task_id].step_id, p.type, p.lag_days) for p in t.predecessors)


class ManualMerge(unittest.TestCase):
    def test_schema_valid_fixture(self):
        man = manual(zones_in_manual_mode=["G-Z1"], tasks=[{"id": "M0001", "step": "TST-SURVEY-DO", "zone_id": "G-Z1",
                                                         "virtual": True, "duration_days": 2}])
        self.assertEqual(schema_errors("manual_sequence", man), [])

    def test_manual_zone_suppresses_generated_tasks(self):
        man = manual(zones_in_manual_mode=["G-Z1"], tasks=[
            {"id": "M0001", "step": "TST-SURVEY-DO", "zone_id": "G-Z1", "virtual": True, "duration_days": 2}])
        sm, _, _ = run(man)
        self.assertEqual({t.zone_id for t in sm.tasks if t.origin != "manual"}, {"G-Z2"})
        self.assertEqual(len(of(sm, zone_id="G-Z1")), 1)
        self.assertEqual(sm.unmapped_elements, [])

    def test_manual_tasks_fields_links_and_virtual(self):
        man = manual(zones_in_manual_mode=["G-Z1"], tasks=[
            {"id": "M0001", "step": "TST-SURVEY-DO", "zone_id": "G-Z1", "virtual": True, "duration_days": 2, "marker": "survey"},
            {"id": "M0002", "step": "TST-CUT-DO", "zone_id": "G-Z1", "elements": ["a", "b"], "after": ["M0001"]},
            {"id": "M0003", "step": "TST-FORM-DO", "zone_id": "G-Z1", "elements": ["a"], "after": ["M0002"],
             "link_type": "SS", "lag_days": 3, "quantity": 7, "unit": "m2"},
            {"id": "M0004", "step": "TST-POUR-DO", "zone_id": "G-Z1", "after": ["M0003"]}])
        sm, _, d = run(man)
        m1, m2, m3, m4 = (of(sm, manual_id=f"M000{i}")[0] for i in range(1, 5))
        self.assertTrue(m1.virtual and m1.element_guid is None and m1.origin == "manual")
        self.assertEqual((m1.duration_days, m1.marker, m1.estimated_crew_days), (2, "survey", 2.0))
        self.assertEqual(m1.cells, [tuple(c) for c in d.zone_by_id()["G-Z1"].cells])
        self.assertEqual((m2.element_guid, m2.virtual, m2.element_name, m2.cells), ("a", None, "A +1", [(0, 0), (1, 0)]))
        self.assertEqual((m3.quantity, m3.unit), (7.0, "m2"))
        self.assertTrue(m4.virtual and m4.element_guid is None)              # no elements -> virtual
        self.assertIn(("M0001", "FS", 0), preds(sm, m2))
        self.assertIn(("M0002", "FS", 0), preds(sm, m3) + [("M0002", "FS", 0)]) if False else None
        self.assertEqual([p for p in preds(sm, m3) if p[0] == "M0002"], [("M0002", "SS", 3)])
        self.assertEqual([p for p in preds(sm, m4) if p[0] == "M0003"], [("M0003", "FS", 0)])
        # library predecessor rules apply to manual tasks (cut after survey in zone) by default
        self.assertTrue(any(p[0] == "M0001" for p in preds(sm, m2)))

    def test_inherit_logic_false_skips_library_rules(self):
        man = manual(inherit_logic=False, zones_in_manual_mode=["G-Z1"], tasks=[
            {"id": "M0001", "step": "TST-SURVEY-DO", "zone_id": "G-Z1", "virtual": True},
            {"id": "M0002", "step": "TST-CUT-DO", "zone_id": "G-Z1", "elements": ["a"]}])
        sm, _, _ = run(man)
        self.assertEqual(of(sm, manual_id="M0002")[0].predecessors, [])
        man["inherit_logic"] = True
        sm, _, _ = run(man)
        self.assertEqual(len(of(sm, manual_id="M0002")[0].predecessors), 1)

    def test_manual_task_replaces_generated_for_same_element_and_step(self):
        man = manual(tasks=[{"id": "M0001", "step": "TST-POUR-DO", "zone_id": "G-Z1", "elements": ["a"], "quantity": 99}])
        sm, _, _ = run(man)
        pours = [t for t in of(sm, step_id="TST-POUR-DO") if t.element_guid == "a"]
        self.assertEqual(len(pours), 1)
        self.assertEqual((pours[0].origin, pours[0].quantity), ("manual", 99.0))
        # the generated chain around it points at the manual task (form -> pour -> strike)
        form = [t for t in of(sm, step_id="TST-FORM-DO") if t.element_guid == "a"][0]
        strike = [t for t in of(sm, step_id="TST-STRIKE-DO") if t.element_guid == "a"][0]
        self.assertIn((pours[0].task_id), [p.task_id for p in strike.predecessors])
        self.assertIn(form.task_id, [p.task_id for p in pours[0].predecessors])
        self.assertEqual(len(of(sm, step_id="TST-POUR-DO")), 3)             # b and c keep their generated tasks

    def test_overrides_suppress_steps(self):
        man = manual(overrides=[{"element_guid": "a", "suppress_steps": ["TST-STRIKE-DO"]},
                                {"element_guid": "c", "suppress_all": True}])
        sm, _, _ = run(man)
        self.assertEqual([t.step_id for t in of(sm, element_guid="a")], ["TST-FORM-DO", "TST-POUR-DO"])
        self.assertEqual(of(sm, element_guid="c"), [])
        self.assertEqual([p.reason for t in of(sm, element_guid="a", step_id="TST-POUR-DO") for p in t.predecessors],
                         ["chain:TST-FORM-DO"])

    def test_after_resolves_generated_task_ids_and_reports_unknown(self):
        man = manual(tasks=[{"id": "M0001", "step": "TST-SURVEY-DO", "zone_id": "G-Z2", "virtual": True,
                             "after": ["T000001", "M0009", "T999999"]}])
        sm, _, _ = run(man)
        t = of(sm, manual_id="M0001")[0]
        self.assertIn("T000001", [p.task_id for p in t.predecessors])
        self.assertEqual(sorted(g["note"] for g in sm.sequencing_gaps), ["manual_ref", "manual_ref"])
        bad = manual(tasks=[{"id": "M0001", "step": "TST-NOPE-DO", "zone_id": "G-Z1"},
                            {"id": "M0002", "step": "TST-CUT-DO", "zone_id": "NOPE"},
                            {"id": "M0003", "step": "TST-CUT-DO", "zone_id": "G-Z1", "elements": ["ghost"]}])
        sm, _, _ = run(bad)
        self.assertEqual([g["note"] for g in sm.sequencing_gaps], ["manual_ref"] * 3)

    def test_manual_order_beats_library_rule_when_they_conflict(self):
        # library: CUT after SURVEY (same zone); manual chain says survey AFTER cut -> manual wins
        man = manual(zones_in_manual_mode=["G-Z1"], tasks=[
            {"id": "M0001", "step": "TST-CUT-DO", "zone_id": "G-Z1", "elements": ["a"]},
            {"id": "M0002", "step": "TST-SURVEY-DO", "zone_id": "G-Z1", "virtual": True, "after": ["M0001"]}])
        sm, _, _ = run(man)
        cut, survey = of(sm, manual_id="M0001")[0], of(sm, manual_id="M0002")[0]
        self.assertIn(cut.task_id, [p.task_id for p in survey.predecessors])
        self.assertNotIn(survey.task_id, [p.task_id for p in cut.predecessors])
        self.assertIn("cycle", [g["note"] for g in sm.sequencing_gaps])
        bundle, _ = build_sequence(sm, step_library_from_dict(library(STEPS).raw), scenario(), doc(els()), generated_at="t")
        d = {t["manual_id"]: t for t in bundle["tasks"] if t.get("manual_id")}
        self.assertLess(d["M0001"]["planned_start_day"], d["M0002"]["planned_start_day"])

    def test_applied_recipe_creates_recipe_tasks(self):
        rec = {"schema_version": "1.0", "id": "rec_x", "name": "x", "sector": "all", "applies_to": {"ifc_class": ["IfcBuildingElementProxy"]},
               "steps": [{"ref": "TST-SURVEY-DO", "virtual": True, "duration_days": 3}, {"ref": "TST-CUT-DO", "optional": True},
                         {"ref": "TST-FORM-DO"}]}
        man = manual(zones_in_manual_mode=["G-Z1"], applied_recipes=[{"recipe": "rec_x", "zone_id": "G-Z1", "element_guid": "a"},
                                                                    {"recipe": "rec_x", "zone_id": "G-Z2", "include_optional": True}])
        sm, _, _ = run(man, recipes={"rec_x": rec})
        in_z1 = of(sm, zone_id="G-Z1")
        self.assertEqual(sorted(t.step_id for t in in_z1), ["TST-FORM-DO", "TST-SURVEY-DO"])       # optional skipped; forced in manual zone
        self.assertTrue(all(t.origin == "recipe" and t.recipe_id == "rec_x" for t in in_z1))
        z2 = [t for t in of(sm, zone_id="G-Z2") if t.recipe_id == "rec_x"]
        self.assertIn("TST-CUT-DO", [t.step_id for t in z2])

    def test_template(self):
        d = doc(els())
        lib = step_library_from_dict(library(STEPS).raw)
        t = manual_mod.template(d, lib, "G-Z1", rules(RULES))
        self.assertEqual(schema_errors("manual_sequence", t), [])
        self.assertEqual([x["step"] for x in t["tasks"]], ["TST-FORM-DO", "TST-POUR-DO", "TST-STRIKE-DO"])
        self.assertEqual(t["tasks"][0]["elements"], ["a", "b"])
        self.assertEqual(t["tasks"][1]["after"], ["M0001"])
        self.assertEqual(t["zones_in_manual_mode"], ["G-Z1"])
        sm, _, _ = run(t)                      # the template itself maps
        self.assertTrue(all(x.origin == "manual" for x in sm.tasks if x.zone_id == "G-Z1"))
        t2 = manual_mod.template(d, lib, "G-Z2")
        self.assertEqual(schema_errors("manual_sequence", t2), [])
        self.assertTrue(t2["tasks"] and all("elements" not in x for x in t2["tasks"]))
        with self.assertRaises(ValueError):
            manual_mod.template(d, lib, "NOPE")


class DemoBundle(unittest.TestCase):
    def test_manual_demo_builds_validates_and_syncs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            summary = pipeline.build_manual_demo(tmp, sectors_dir=Path("/nonexistent"))
            self.assertEqual(summary.name, "healthcare_manual_demo")
            self.assertGreaterEqual(summary.virtual_tasks, 2)
            ok, msgs = validate_file(tmp / "healthcare" / "manual_demo.json")
            self.assertTrue(ok, msgs)
            for n in ("elements", "element_step_map", "sequence"):
                ok, msgs = validate_file(tmp / "healthcare_manual_demo" / f"{n}.json", n)
                self.assertTrue(ok, msgs)
            seq = read_json(tmp / "healthcare_manual_demo" / "sequence.json")
            man = read_json(tmp / "healthcare" / "manual_demo.json")
            self.assertEqual(seq["manual"]["zones_in_manual_mode"], man["zones_in_manual_mode"])
            self.assertEqual(seq["scenario"]["id"], "healthcare_manual_demo")
            chain = sorted((t for t in seq["tasks"] if t.get("origin") == "manual"), key=lambda t: t["manual_id"])
            self.assertEqual(len(chain), len(man["tasks"]))
            starts = [t["planned_start_day"] for t in chain]
            self.assertEqual(starts, sorted(starts))                          # authored order is the schedule order
            self.assertTrue(all(t["element_guid"] is None for t in chain if t.get("virtual")))
            zone = man["zones_in_manual_mode"][0]
            self.assertTrue(all(t["origin"] == "manual" for t in seq["tasks"] if t["zone_id"] == zone))
            godot = tmp / "godot"
            paths = pipeline.sync_godot(tmp, godot)
            self.assertEqual([p.parent.name for p in paths], ["healthcare_manual_demo"])


if __name__ == "__main__":
    unittest.main()
