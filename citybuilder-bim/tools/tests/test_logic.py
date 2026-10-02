import json
import tempfile
import unittest
from pathlib import Path

from helpers import doc, elem, library, rule, rules, scenario, step, REPO  # noqa: F401  (sets sys.path)
from bimseq import logic
from bimseq.mapper import map_elements
from bimseq.model import elements_from_dict, step_library_from_dict, step_map_from_dict
from bimseq.scheduler import build_sequence
from bimseq.validate import schema_errors, validate_file

STEPS = [
    step("TST-SURVEY-DO", phase="build"), step("TST-CUT-DO", phase="build"), step("TST-DEWATER-DO", phase="build"),
    step("TST-FORM-DO", phase="build"), step("TST-POUR-DO", phase="build"), step("TST-TEST-DO", phase="fit"),
    step("TST-WINDOW-DO", phase="fit"), step("TST-PIPE-DO", phase="build"), step("TST-DEFAULT-DO"),
]


def recipe(rid, steps, **kw):
    d = {"schema_version": "1.0", "id": rid, "name": rid, "sector": "all", "applies_to": {"ifc_class": ["IfcTank"]},
         "steps": steps}
    d.update(kw)
    return d


TANK = recipe("rec_tank", [
    {"ref": "TST-SURVEY-DO", "virtual": True, "duration_days": 2, "marker": "survey"},
    {"ref": "TST-CUT-DO", "from_element": "foundation"},
    {"ref": "TST-DEWATER-DO", "virtual": True, "duration_days": 10, "parallel_with": "TST-CUT-DO", "marker": "dewatering"},
    {"ref": "TST-FORM-DO", "lag_days": 3},
    {"ref": "TST-POUR-DO", "hold_point": "structural"},
    {"ref": "TST-TEST-DO", "virtual": True, "optional": True},
], logic=[{"after": "TST-DEWATER-DO", "before": "TST-POUR-DO", "lag_days": 1, "reason": "dry pit"}])

BASE_ELEMENTS = lambda: [
    elem("foot", "Footing F1", "G", "G-Z1", [(0, 0)], ifc_class="IfcFooting"),
    elem("tank", "Tank 1", "G", "G-Z1", [(0, 0)], ifc_class="IfcTank", quantities={"count": 1, "volume_m3": 5.0}),
]
BASE_RULES = [rule("R-foot", 20, {"ifc_class": ["IfcFooting"]}, ["TST-CUT-DO"]),
              rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_tank")]


def expand(recipes, elements=None, rule_list=None, steps=None, manual=None):
    lib = step_library_from_dict(library(steps or STEPS).raw)
    d = doc(elements or BASE_ELEMENTS())
    sm = map_elements(d, lib, rules(rule_list or BASE_RULES), generated_at="t",
                      recipes={r["id"]: r for r in recipes}, manual=manual)
    return sm, lib, d


def by_step(sm, sid):
    return [t for t in sm.tasks if t.step_id == sid]


def pred_steps(sm, task):
    tid = {t.task_id: t for t in sm.tasks}
    return sorted((tid[p.task_id].step_id, p.type, p.lag_days) for p in task.predecessors)


class Matcher(unittest.TestCase):
    def el(self, **kw):
        return elements_from_dict(doc([elem("e", "Big Storage Tank", "G", "G-Z1", [(0, 0)], ifc_class="IfcTank",
                                           predefined_type="STORAGE", properties={"Foundation": "ring"},
                                           visual_kit="tank")]).to_dict()).elements[0]

    def test_any_key_matches(self):
        e = self.el()
        for applies in ({"ifc_class": ["IfcTank"]}, {"predefined_type": ["STORAGE"]}, {"name_regex": "Storage"},
                        {"properties": {"Foundation": "ring"}}, {"visual_kit": ["tank"]},
                        {"zone_tags_any": ["x"]}, {"keywords": ["TANK"]}, {"ifc_class": ["IfcWall"], "keywords": ["big"]}):
            tags = ["x"] if "zone_tags_any" in applies else []
            self.assertTrue(logic.applies_to_matches(applies, e, tags), applies)
        for applies in ({"ifc_class": ["IfcWall"]}, {"properties": {"Foundation": "pad"}},
                        {"properties": {"Foundation": "ring", "Other": 1}}, {"keywords": ["pump"]}, {}):
            self.assertFalse(logic.applies_to_matches(applies, e, []), applies)

    def test_sector_gate(self):
        e = self.el()
        r = {"sector": "civil", "applies_to": {"ifc_class": ["IfcTank"]}}
        self.assertFalse(logic.recipe_matches(r, e, [], "industrial"))
        self.assertTrue(logic.recipe_matches(r, e, [], "civil"))
        self.assertTrue(logic.recipe_matches({**r, "sector": "all"}, e, [], "industrial"))

    def test_null_predefined_type_in_applies_to(self):
        e = elements_from_dict(doc([elem("e", "x", "G", "G-Z1", [(0, 0)])]).to_dict()).elements[0]
        self.assertTrue(logic.applies_to_matches({"predefined_type": [None]}, e, []))


class IndexAndLoading(unittest.TestCase):
    def test_load_validate_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "sub").mkdir()
            (tmp / "sub" / "rec_tank.json").write_text(json.dumps(TANK))
            (tmp / "bad.json").write_text(json.dumps({"id": "rec_bad"}))
            (tmp / "dup.json").write_text(json.dumps(TANK))
            recipes, errors = logic.load_recipes([tmp], include_default=False)
            self.assertEqual(list(recipes), ["rec_tank"])
            self.assertTrue(any("bad.json" in e for e in errors))
            self.assertTrue(any("duplicate recipe id" in e for e in errors))
            idx = logic.build_index(recipes)
            row = idx["recipes"][0]
            self.assertEqual((row["id"], row["step_count"], row["virtual_count"]), ("rec_tank", 6, 3))
            self.assertEqual(row["steps"][:2], ["TST-SURVEY-DO", "TST-CUT-DO"])
            out = tmp / "index.json"
            logic.write_index(recipes, out)
            self.assertEqual(json.loads(out.read_text())["recipe_count"], 1)

    def test_shipped_recipes_valid_and_resolve_against_libraries(self):
        recipes, errors = logic.load_recipes()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(recipes), 25)
        libs = {s: json.loads((REPO / "data" / "sectors" / s / "step_library.json").read_text()) for s in
                ("industrial", "civil", "healthcare")}
        for rid, r in recipes.items():
            for sector, lib in libs.items():
                if r["sector"] not in ("all", sector):
                    continue
                ids = {s["id"] for s in lib["steps"]}
                for entry in r["steps"]:
                    ref = logic.step_ref(entry)
                    if ref and "step" not in entry:
                        self.assertIn(ref, ids, f"{rid} in {sector}")
                    if "recipe" in entry:
                        self.assertIn(entry["recipe"], recipes)


class Expansion(unittest.TestCase):
    def test_recipe_rule_expands_virtual_and_bound_steps(self):
        sm, _, d = expand([TANK])
        virt = [t for t in sm.tasks if t.virtual]
        self.assertEqual(sorted(t.step_id for t in virt), ["TST-DEWATER-DO", "TST-SURVEY-DO"])    # optional test skipped
        zone_cells = [tuple(c) for c in d.zone_by_id()["G-Z1"].cells]
        for t in virt:
            self.assertIsNone(t.element_guid)
            self.assertEqual((t.origin, t.recipe_id, t.zone_id, t.storey_id), ("recipe", "rec_tank", "G-Z1", "G"))
            self.assertEqual(t.cells, zone_cells)
            self.assertEqual(t.quantity, 1.0)
        dewater = by_step(sm, "TST-DEWATER-DO")[0]
        self.assertEqual((dewater.duration_days, dewater.marker, dewater.estimated_crew_days), (10, "dewatering", 10.0))
        self.assertEqual(by_step(sm, "TST-SURVEY-DO")[0].marker, "survey")
        # foundation-bound step reuses the rule-generated task on the footing: exactly one, no duplicate
        cut = by_step(sm, "TST-CUT-DO")
        self.assertEqual([t.element_guid for t in cut], ["foot"])
        self.assertEqual(cut[0].origin, None)
        self.assertEqual(sm.sequencing_gaps, [])

    def test_chain_parallel_logic_hold_point(self):
        sm, _, _ = expand([TANK])
        survey, cut, dewater, form, pour = (by_step(sm, s)[0] for s in
                                            ("TST-SURVEY-DO", "TST-CUT-DO", "TST-DEWATER-DO", "TST-FORM-DO", "TST-POUR-DO"))
        self.assertEqual(pred_steps(sm, cut), [("TST-SURVEY-DO", "FS", 0)])
        self.assertEqual(pred_steps(sm, dewater), [("TST-CUT-DO", "SS", 0)])                # start-together branch
        self.assertEqual(pred_steps(sm, form), [("TST-CUT-DO", "FS", 3)])                  # follows the step BEFORE the branch
        self.assertEqual(pred_steps(sm, pour), [("TST-DEWATER-DO", "FS", 1), ("TST-FORM-DO", "FS", 0)])
        self.assertEqual(pour.element_guid, "tank")
        self.assertEqual(pour.origin, "recipe")
        self.assertTrue(pour.flags["inspection"])
        self.assertEqual(pour.flags["inspection_type"], "structural")
        reasons = {p.reason for p in pour.predecessors}
        self.assertTrue(any(r.startswith("logic:rec_tank:") for r in reasons))
        self.assertTrue(any(r.startswith("recipe:rec_tank:") for r in reasons))

    def test_include_optional_via_manual_applied_recipe(self):
        man = {"schema_version": "1.0", "project": "p",
               "applied_recipes": [{"recipe": "rec_tank", "zone_id": "G-Z1", "element_guid": "tank", "include_optional": True}]}
        sm, _, _ = expand([TANK], rule_list=[BASE_RULES[0]], manual=man)
        self.assertEqual(sorted(t.step_id for t in sm.tasks if t.virtual),
                         ["TST-DEWATER-DO", "TST-SURVEY-DO", "TST-TEST-DO"])

    def test_dedupe_against_rule_tasks_and_ids_deterministic(self):
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, ["TST-POUR-DO"], recipe="rec_tank"),
              BASE_RULES[0]]
        sm, _, _ = expand([TANK], rule_list=rl)
        self.assertEqual(len(by_step(sm, "TST-POUR-DO")), 1)
        self.assertEqual(by_step(sm, "TST-POUR-DO")[0].origin, None)           # rule task reused, not duplicated
        sm2, _, _ = expand([TANK], elements=list(reversed(BASE_ELEMENTS())), rule_list=rl)
        self.assertEqual(sm.to_dict(), sm2.to_dict())
        self.assertEqual([t.task_id for t in sm.tasks], [f"T{i:06d}" for i in range(1, len(sm.tasks) + 1)])

    def test_foundation_prefers_same_storey_then_lower_and_falls_back_to_self(self):
        els = [elem("slab", "Ground slab", "B", "B-Z1", [(0, 0), (1, 0)], ifc_class="IfcSlab", predefined_type="BASESLAB"),
               elem("tank", "Tank", "G", "G-Z1", [(0, 0)], ifc_class="IfcTank"),
               elem("tank2", "Tank 2", "U", "U-Z1", [(3, 0)], ifc_class="IfcTank")]
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_tank")]
        sm, _, _ = expand([recipe("rec_tank", [{"ref": "TST-CUT-DO", "from_element": "foundation"}])], els, rl)
        got = sorted((t.element_guid) for t in by_step(sm, "TST-CUT-DO"))
        self.assertEqual(got, ["slab", "tank2"])        # tank -> slab on lower storey; tank2 has none -> itself

    def test_host_system_and_zone_binding(self):
        els = [elem("wall", "Wall", "G", "G-Z1", [(0, 0)], ifc_class="IfcWall"),
               elem("win", "Window", "G", "G-Z1", [(0, 0)], ifc_class="IfcWindow", host="wall"),
               elem("p1", "Pipe 1", "G", "G-Z1", [(0, 0)], ifc_class="IfcPipeSegment", system="S1"),
               elem("p2", "Pipe 2", "G", "G-Z2", [(2, 0)], ifc_class="IfcPipeSegment", system="S1"),
               elem("p3", "Pipe 3", "G", "G-Z2", [(3, 0)], ifc_class="IfcPipeSegment", system="S2")]
        rl = [rule("R-win", 20, {"ifc_class": ["IfcWindow"]}, [], recipe="rec_w"),
              rule("R-pipe", 10, {"name_regex": "^Pipe 1"}, ["TST-PIPE-DO"], recipe="rec_p")]
        rec_w = recipe("rec_w", [{"ref": "TST-WINDOW-DO", "from_element": "host"}])
        rec_p = recipe("rec_p", [{"ref": "TST-PIPE-DO", "from_element": "system"},
                                 {"ref": "TST-TEST-DO", "from_element": "zone"}])
        sm, _, _ = expand([rec_w, rec_p], els, rl)
        self.assertEqual([t.element_guid for t in by_step(sm, "TST-WINDOW-DO")], ["wall"])
        self.assertEqual(sorted(t.element_guid for t in by_step(sm, "TST-PIPE-DO")), ["p1", "p2"])    # S1 only, p1 deduped
        zone_task = by_step(sm, "TST-TEST-DO")[0]
        self.assertTrue(zone_task.virtual and zone_task.element_guid is None and zone_task.zone_id == "G-Z1")
        # system-bound pipe tasks are all linked before the zone test
        self.assertEqual(sorted(s for s, _, _ in pred_steps(sm, zone_task)), ["TST-PIPE-DO", "TST-PIPE-DO"])

    def test_nested_recipes_and_cycle_guard(self):
        inner = recipe("rec_inner", [{"ref": "TST-FORM-DO"}, {"ref": "TST-POUR-DO"}])
        outer = recipe("rec_outer", [{"ref": "TST-SURVEY-DO", "virtual": True}, {"recipe": "rec_inner"},
                                     {"ref": "TST-TEST-DO", "virtual": True}])
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_outer")]
        sm, _, _ = expand([outer, inner], rule_list=rl)
        survey, form, pour, test = (by_step(sm, s)[0] for s in ("TST-SURVEY-DO", "TST-FORM-DO", "TST-POUR-DO", "TST-TEST-DO"))
        self.assertEqual(pred_steps(sm, form), [("TST-SURVEY-DO", "FS", 0)])
        self.assertEqual(pred_steps(sm, pour), [("TST-FORM-DO", "FS", 0)])
        self.assertEqual(pred_steps(sm, test), [("TST-POUR-DO", "FS", 0)])
        a = recipe("rec_a", [{"ref": "TST-FORM-DO"}, {"recipe": "rec_b"}])
        b = recipe("rec_b", [{"ref": "TST-POUR-DO"}, {"recipe": "rec_a"}])
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_a")]
        sm, _, _ = expand([a, b], rule_list=rl)
        self.assertIn("recipe_cycle", [g["note"] for g in sm.sequencing_gaps])
        self.assertEqual(len(by_step(sm, "TST-FORM-DO")), 1)

    def test_inline_step_registered_and_survives_map_round_trip(self):
        inline = {"id": "TST-INLINE-DO", "name": "Inline thing", "phase": "build", "trade": "crew", "discipline": "general",
                  "quantity_basis": "count", "rate_per_crew_day": 2, "unit_cost": 50}
        rec = recipe("rec_i", [{"step": inline, "virtual": True}, {"step": {**inline, "id": "bad id"}, "virtual": True}])
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_i")]
        sm, lib, d = expand([rec], rule_list=rl)
        self.assertIn("TST-INLINE-DO", lib.steps)
        self.assertEqual([s["id"] for s in sm.inline_steps], ["TST-INLINE-DO"])
        self.assertIn("recipe_ref", [g["note"] for g in sm.sequencing_gaps])
        t = by_step(sm, "TST-INLINE-DO")[0]
        self.assertEqual(t.estimated_crew_days, 0.5)
        fresh = step_library_from_dict(library(STEPS).raw)                     # as the schedule CLI sees it
        sm2 = step_map_from_dict(json.loads(json.dumps(sm.to_dict())))
        bundle, _ = build_sequence(sm2, fresh, scenario(), d, generated_at="t", recipes=[rec])
        self.assertIn("TST-INLINE-DO", {s["id"] for s in bundle["step_library"]["steps"]})
        self.assertEqual(schema_errors("sequence", bundle), [])

    def test_unknown_references_become_gaps(self):
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, [], recipe="rec_missing"),
              rule("R-foot", 5, {"ifc_class": ["IfcFooting"]}, [], recipe="rec_bad")]
        sm, _, _ = expand([recipe("rec_bad", [{"ref": "TST-NOPE-DO"}, {"ref": "TST-FORM-DO"}])], rule_list=rl)
        self.assertEqual(sorted((g["step"], g["note"]) for g in sm.sequencing_gaps),
                         [("TST-NOPE-DO", "recipe_ref"), ("rec_missing", "recipe_ref")])

    def test_rule_visual_kit_recorded(self):
        rl = [rule("R-tank", 10, {"ifc_class": ["IfcTank"]}, ["TST-POUR-DO"], visual_kit="tank")]
        sm, _, d = expand([], rule_list=rl)
        self.assertEqual(sm.element_visual_kits, {"tank": "tank"})
        bundle, _ = build_sequence(sm, step_library_from_dict(library(STEPS).raw), scenario(), d, generated_at="t")
        self.assertEqual({e["guid"]: e.get("visual_kit") for e in bundle["elements"]}["tank"], "tank")

    def test_bundle_with_virtual_tasks_validates_and_uses_duration(self):
        sm, lib, d = expand([TANK])
        bundle, _ = build_sequence(sm, lib, scenario({"crew": 20, "other": 20}), d, generated_at="t", recipes=[TANK])
        self.assertEqual(schema_errors("sequence", bundle), [])
        tasks = {t["task_id"]: t for t in bundle["tasks"]}
        dew = next(t for t in bundle["tasks"] if t["step_id"] == "TST-DEWATER-DO")
        self.assertEqual(dew["planned_finish_day"] - dew["planned_start_day"], 10)
        self.assertEqual(bundle["recipes"][0]["id"], "rec_tank")
        self.assertTrue(all(t["package_id"] for t in tasks.values()))
        self.assertTrue(all(t["cells"] for t in bundle["tasks"] if t["element_guid"] is None))


class Explain(unittest.TestCase):
    def test_element_explanation_statuses(self):
        recs = {"rec_tank": TANK}
        d = doc(BASE_ELEMENTS())
        index = logic.ElementIndex(d)
        none = logic.explain_element(recs, index, "tank")
        self.assertEqual({s["status"] for s in none[0]["steps"]}, {"virtual", "unchecked"})
        sm, _, _ = expand([TANK], rule_list=[BASE_RULES[0]])           # rule tasks only: footing cut exists
        items = logic.explain_element(recs, index, "tank", sm)
        status = {s["ref"]: s["status"] for s in items[0]["steps"]}
        self.assertEqual(status["TST-CUT-DO"], "covered")             # via the foundation element
        self.assertEqual(status["TST-FORM-DO"], "missing")             # BIM-bound but no task on the tank
        self.assertEqual(status["TST-SURVEY-DO"], "virtual")           # would be created
        sm2, _, _ = expand([TANK])                                     # recipe applied
        status = {s["ref"]: s["status"] for s in logic.explain_element(recs, index, "tank", sm2)[0]["steps"]}
        self.assertEqual((status["TST-SURVEY-DO"], status["TST-FORM-DO"], status["TST-POUR-DO"]), ("covered",) * 3)
        text = logic.format_explanation(items)
        self.assertIn("rec_tank", text)
        self.assertIn("[missing", text)

    def test_zone_explanation_aggregates_and_no_match(self):
        els = BASE_ELEMENTS() + [elem("tank2", "Tank 2", "G", "G-Z1", [(1, 0)], ifc_class="IfcTank")]
        d = doc(els)
        index = logic.ElementIndex(d)
        sm, _, _ = expand([TANK], els, rule_list=[BASE_RULES[0]])
        out = logic.explain_zone({"rec_tank": TANK}, index, "G-Z1", sm)
        self.assertEqual(len(out[0]["elements"]), 2)
        form = next(s for s in out[0]["steps"] if s["ref"] == "TST-FORM-DO")
        self.assertEqual(form["counts"], {"missing": 2})
        self.assertEqual(logic.explain_zone({"rec_tank": TANK}, index, "G-Z2", sm), [])
        self.assertEqual(logic.format_explanation([]), "no recipe applies")

    def test_every_synthetic_element_explains_without_error(self):
        from bimseq.synth import GENERATORS
        recipes, _ = logic.load_recipes()
        for sector, gen in GENERATORS.items():
            d = elements_from_dict(gen())
            index = logic.ElementIndex(d)
            matched = 0
            for e in d.elements:
                items = logic.explain_element(recipes, index, e.guid)
                matched += bool(items)
            self.assertGreater(matched, 0, sector)

    def test_cli_logic_commands(self):
        import io
        from contextlib import redirect_stdout
        from bimseq import __main__ as cli
        from bimseq.model import write_json
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "recipes").mkdir()
            (tmp / "recipes" / "rec_tank.json").write_text(json.dumps(TANK))
            write_json(tmp / "elements.json", doc(BASE_ELEMENTS()).to_dict())
            base = ["--recipes-dir", str(tmp / "recipes")]

            def run(*argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main(list(argv))
                return rc, buf.getvalue()
            rc, out = run("logic", "get", "rec_tank", *base)
            self.assertEqual((rc, json.loads(out)["id"]), (0, "rec_tank"))
            rc, out = run("logic", "list", "--sector", "industrial", *base)
            self.assertIn("rec_tank", out)
            rc, out = run("logic", "explain", "--elements", str(tmp / "elements.json"), "--guid", "tank", *base)
            self.assertEqual(rc, 0)
            self.assertIn("rec_tank", out)
            rc, out = run("logic", "explain", "--elements", str(tmp / "elements.json"), "--zone", "G-Z1", "--json", *base)
            self.assertEqual(json.loads(out)[0]["recipe"], "rec_tank")
            rc, out = run("logic", "index", "--out", str(tmp / "idx.json"), *base)
            self.assertTrue((tmp / "idx.json").exists())


if __name__ == "__main__":
    unittest.main()
