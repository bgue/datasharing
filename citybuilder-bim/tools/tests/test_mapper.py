import unittest

from helpers import doc, elem, library, rule, rules, step  # noqa: F401  (sets sys.path)
from bimseq.mapper import MappingError, map_elements, match_ok, rule_matches
from bimseq.model import Element, Match, mapping_rules_from_dict


def make_element(**kw) -> Element:
    d = dict(guid="g1", ifc_class="IfcWall", name="Wall A", storey_id="G", zone_id="G-Z1", cells=[(0, 0)],
             quantities={"area_m2": 10.0}, predefined_type=None, system_id=None, material="Concrete C30",
             properties={"IsExternal": True, "Count": 3})
    d.update(kw)
    return Element(**d)


def M(**kw) -> Match:
    return mapping_rules_from_dict({
        "schema_version": "1.0", "sector": "civil",
        "rules": [{"id": "R-x", "priority": 1, "match": kw, "steps": [{"step": "AB-CD", "quantity": "count"}]}],
        "default": {"steps": [{"step": "AB-CD", "quantity": "count"}]}}).rules[0].match


def tasks_by_name(sm, step_id=None):
    out = {}
    for t in sm.tasks:
        if step_id is None or t.step_id == step_id:
            out.setdefault(t.element_name, []).append(t)
    return out


class RuleMatching(unittest.TestCase):
    def test_all_keys_are_anded(self):
        el = make_element()
        self.assertTrue(match_ok(M(ifc_class=["IfcWall"], properties={"IsExternal": True}), el, 0, []))
        self.assertFalse(match_ok(M(ifc_class=["IfcWall"], properties={"IsExternal": False}), el, 0, []))
        self.assertFalse(match_ok(M(ifc_class=["IfcSlab"], properties={"IsExternal": True}), el, 0, []))
        self.assertTrue(match_ok(M(), el, 0, []))

    def test_regexes_use_search(self):
        el = make_element()
        self.assertTrue(match_ok(M(name_regex="Wall"), el, 0, []))
        self.assertTrue(match_ok(M(name_regex="(?i)wall a$"), el, 0, []))
        self.assertFalse(match_ok(M(name_regex="^A"), el, 0, []))
        self.assertTrue(match_ok(M(material_regex="(?i)concrete"), el, 0, []))
        self.assertFalse(match_ok(M(material_regex="steel"), el, 0, []))
        self.assertFalse(match_ok(M(material_regex="x"), make_element(material=None), 0, []))

    def test_predefined_type_null(self):
        none_el, floor_el = make_element(), make_element(predefined_type="FLOOR")
        self.assertFalse(match_ok(M(predefined_type=["FLOOR"]), none_el, 0, []))
        self.assertTrue(match_ok(M(predefined_type=["FLOOR"]), floor_el, 0, []))
        self.assertTrue(match_ok(M(predefined_type=["FLOOR", None]), none_el, 0, []))
        self.assertTrue(match_ok(M(predefined_type=[None]), none_el, 0, []))
        self.assertFalse(match_ok(M(predefined_type=[None]), floor_el, 0, []))

    def test_properties_exact_and_present(self):
        el = make_element()
        self.assertTrue(match_ok(M(properties={"Count": 3}), el, 0, []))
        self.assertFalse(match_ok(M(properties={"Count": 4}), el, 0, []))
        self.assertFalse(match_ok(M(properties={"Missing": True}), el, 0, []))
        self.assertFalse(match_ok(M(properties={"IsExternal": 1}), el, 0, []), "bool must not equal int")
        self.assertTrue(match_ok(M(properties_present=["Count", "IsExternal"]), el, 0, []))
        self.assertFalse(match_ok(M(properties_present=["Count", "Nope"]), el, 0, []))

    def test_storey_zone_quantity(self):
        el = make_element()
        self.assertTrue(match_ok(M(storey_index_min=0, storey_index_max=0), el, 0, []))
        self.assertFalse(match_ok(M(storey_index_min=1), el, 0, []))
        self.assertFalse(match_ok(M(storey_index_max=-1), el, 0, []))
        self.assertTrue(match_ok(M(zone_tags_any=["a", "b"]), el, 0, ["b"]))
        self.assertFalse(match_ok(M(zone_tags_any=["a"]), el, 0, ["c"]))
        self.assertTrue(match_ok(M(quantity_min={"area_m2": 10}), el, 0, []))
        self.assertFalse(match_ok(M(quantity_min={"area_m2": 11}), el, 0, []))
        self.assertFalse(match_ok(M(quantity_min={"volume_m3": 1}), el, 0, []))

    def test_system_prefix_on_rule(self):
        r = mapping_rules_from_dict({"schema_version": "1.0", "sector": "civil", "rules": [
            {"id": "R-a", "priority": 1, "match": {}, "system_prefix": "MG-",
             "steps": [{"step": "AB-CD", "quantity": "count"}]}],
            "default": {"steps": [{"step": "AB-CD", "quantity": "count"}]}}).rules[0]
        self.assertTrue(rule_matches(r, make_element(system_id="MG-O2"), 0, []))
        self.assertFalse(rule_matches(r, make_element(system_id="CHW"), 0, []))
        self.assertFalse(rule_matches(r, make_element(system_id=None), 0, []))


class RuleEngine(unittest.TestCase):
    def setUp(self):
        self.lib = library([step("TST-A-DO"), step("TST-B-DO"), step("TST-C-DO"), step("TST-DEFAULT-DO")])

    def map(self, rule_list, elements, lib=None):
        return map_elements(doc(elements), lib or self.lib, rules(rule_list), generated_at="t")

    def test_priority_first_match_wins(self):
        rl = [rule("R-low", 10, {"ifc_class": ["IfcWall"]}, ["TST-A-DO"]),
              rule("R-high", 50, {"ifc_class": ["IfcWall"]}, ["TST-B-DO"])]
        sm = self.map(rl, [elem("w", "W", "G", "G-Z1", [(0, 0)], ifc_class="IfcWall")])
        self.assertEqual([(t.step_id, t.rule_id) for t in sm.tasks], [("TST-B-DO", "R-high")])

    def test_continue_accumulates_then_stops(self):
        rl = [rule("R-1", 90, {}, ["TST-A-DO"], **{"continue": True}),
              rule("R-2", 80, {}, ["TST-B-DO"]),
              rule("R-3", 70, {}, ["TST-C-DO"])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)])])
        self.assertEqual([t.step_id for t in sm.tasks], ["TST-A-DO", "TST-B-DO"])
        self.assertEqual(sm.unmapped_elements, [])

    def test_default_rule_reports_unmapped(self):
        rl = [rule("R-w", 10, {"ifc_class": ["IfcWall"]}, ["TST-A-DO"])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)])])
        self.assertEqual([t.step_id for t in sm.tasks], ["TST-DEFAULT-DO"])
        self.assertEqual(sm.unmapped_elements, [{"guid": "e", "ifc_class": "IfcBuildingElementProxy", "reason": "default_rule"}])

    def test_null_predefined_type_in_engine(self):
        rl = [rule("R-null", 10, {"predefined_type": ["FLOOR", None]}, ["TST-A-DO"])]
        els = [elem("a", "A", "G", "G-Z1", [(0, 0)]),
               elem("b", "B", "G", "G-Z1", [(1, 0)], predefined_type="FLOOR"),
               elem("c", "C", "G", "G-Z1", [(1, 0)], predefined_type="ROOF")]
        got = {t.element_guid: t.step_id for t in self.map(rl, els).tasks}
        self.assertEqual(got, {"a": "TST-A-DO", "b": "TST-A-DO", "c": "TST-DEFAULT-DO"})

    def test_quantities_units_cost(self):
        lib = library([step("TST-A-DO", basis="volume_m3", rate=4.0, cost=50.0), step("TST-DEFAULT-DO")])
        rl = [rule("R-q", 10, {}, [{"step": "TST-A-DO", "quantity": "area_m2", "quantity_factor": 0.5,
                                    "unit_override": "t"}])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"area_m2": 20.0})], lib)
        t = sm.tasks[0]
        self.assertEqual((t.quantity, t.unit, t.estimated_crew_days, t.cost), (10.0, "t", 2.5, 500.0))
        rl = [rule("R-q", 10, {}, [{"step": "TST-A-DO", "quantity": "volume_m3"}])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"volume_m3": 8.0})], lib)
        self.assertEqual(sm.tasks[0].unit, "m3")

    def test_zero_quantity_gets_minimum_and_no_quantity_reason(self):
        lib = library([step("TST-A-DO", basis="area_m2"), step("TST-DEFAULT-DO")])
        rl = [rule("R-q", 10, {}, [{"step": "TST-A-DO", "quantity": "area_m2", "min_quantity": 2.0}])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"count": 1})], lib)
        self.assertEqual(sm.tasks[0].quantity, 2.0)
        self.assertEqual([u["reason"] for u in sm.unmapped_elements], ["no_quantity"])
        rl = [rule("R-q", 10, {}, [{"step": "TST-A-DO", "quantity": "area_m2"}])]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"count": 1})], lib)
        self.assertEqual(sm.tasks[0].quantity, 0.01)

    def test_rule_visual_override(self):
        rl = [rule("R-v", 10, {}, ["TST-A-DO"], visual="tank")]
        sm = self.map(rl, [elem("e", "E", "G", "G-Z1", [(0, 0)])])
        self.assertEqual(sm.element_visuals, {"e": "tank"})

    def test_unknown_step_is_an_error(self):
        with self.assertRaises(MappingError):
            self.map([rule("R-x", 1, {}, ["TST-NOPE-DO"])], [elem("e", "E", "G", "G-Z1", [(0, 0)])])

    def test_deterministic_ids_independent_of_input_order(self):
        rl = [rule("R-x", 1, {}, ["TST-A-DO", "TST-B-DO"])]
        els = [elem(f"g{i}", f"E{i}", s, z, [(i % 2, 0)]) for i, (s, z) in
               enumerate([("U", "U-Z1"), ("G", "G-Z2"), ("B", "B-Z1"), ("G", "G-Z1"), ("G", "G-Z1")])]
        a = self.map(rl, els)
        b = self.map(rl, list(reversed(els)))
        self.assertEqual(a.to_dict(), b.to_dict())
        order = [(t.storey_id, t.zone_id, t.element_guid, t.step_id) for t in a.tasks]
        self.assertEqual([t.task_id for t in a.tasks], [f"T{i:06d}" for i in range(1, len(order) + 1)])
        self.assertEqual(order[0][0], "B")                       # storey index order: -1, 0, 1
        self.assertEqual(order[-1][0], "U")
        g = [o for o in order if o[0] == "G"]
        self.assertEqual(g, sorted(g, key=lambda o: (o[1], o[2])))   # zone, guid; chain order within element


class ChainLinks(unittest.TestCase):
    def setUp(self):
        self.lib = library([step("TST-A-DO"), step("TST-B-DO"), step("TST-C-DO"), step("TST-DEFAULT-DO")])

    def test_chain_links_with_lags(self):
        rl = [rule("R-c", 1, {}, ["TST-A-DO", {"step": "TST-B-DO", "quantity": "count", "lag_days": 7},
                                  {"step": "TST-C-DO", "quantity": "count", "lag_days": 2}])]
        sm = map_elements(doc([elem("e", "E", "G", "G-Z1", [(0, 0)])]), self.lib, rules(rl), generated_at="t")
        a, b, c = sm.tasks
        self.assertEqual(a.predecessors, [])
        self.assertEqual([(p.task_id, p.type, p.lag_days, p.reason) for p in b.predecessors],
                         [(a.task_id, "FS", 0 + 7, "chain:TST-A-DO")])
        self.assertEqual([(p.task_id, p.lag_days) for p in c.predecessors], [(b.task_id, 2)])

    def test_chain_false_has_no_links(self):
        rl = [rule("R-c", 1, {}, ["TST-A-DO", "TST-B-DO"], chain=False)]
        sm = map_elements(doc([elem("e", "E", "G", "G-Z1", [(0, 0)])]), self.lib, rules(rl), generated_at="t")
        self.assertTrue(all(not t.predecessors for t in sm.tasks))


class ScopeResolution(unittest.TestCase):
    """Every scope on a tiny hand graph: storeys B(-1), G(0), U(1); zones B-Z1, G-Z1, G-Z2, U-Z1."""

    SCOPES = ["same_element", "host", "same_cell", "same_cell_below", "same_cell_above", "same_zone",
              "same_zone_below", "same_storey", "same_storey_below", "same_system", "project"]

    def build(self, scopes):
        steps = [step("TST-PROV-DO"), step("TST-SELF-DO"), step("TST-DEFAULT-DO")]
        rl = [rule("R-prov", 100, {"name_regex": "^prov"}, ["TST-PROV-DO"])]
        els = [
            elem("pB", "prov-B", "B", "B-Z1", [(0, 0)], system="SYS-B"),
            elem("pG1", "prov-G1", "G", "G-Z1", [(0, 0)], system="SYS-A"),
            elem("pG2", "prov-G2", "G", "G-Z2", [(2, 0)], system="SYS-A"),
            elem("pG3", "prov-G3", "G", "G-Z1", [(1, 0), (2, 0)]),
            elem("pU", "prov-U", "U", "U-Z1", [(0, 0)], system="SYS-A"),
            elem("pU2", "prov-U2", "U", "U-Z1", [(3, 0)]),
        ]
        for i, s in enumerate(scopes):
            sid = f"TST-C{i:02d}-DO"
            steps.append(step(sid, preds=[("TST-SELF-DO" if s == "same_element" else "TST-PROV-DO", s)]))
            emits = [sid] if s != "same_element" else ["TST-SELF-DO", sid]
            rl.append(rule(f"R-{s.replace('_', '-')}", 50, {"name_regex": f"^cons-{s}$"}, emits, chain=False))
            els.append(elem(f"c-{s}", f"cons-{s}", "G", "G-Z1", [(0, 0), (1, 0)], system="SYS-A",
                            host="pG1" if s == "host" else None))
        return map_elements(doc(els), library(steps), rules(rl), generated_at="t")

    def preds_of(self, sm, scope):
        by_id = {t.task_id: t for t in sm.tasks}
        cons = next(t for t in sm.tasks if t.element_name == f"cons-{scope}" and t.step_id != "TST-SELF-DO")
        return sorted(by_id[p.task_id].element_name for p in cons.predecessors), cons

    def test_each_scope(self):
        sm = self.build(self.SCOPES)
        expected = {
            "same_element": ["cons-same_element"],
            "host": ["prov-G1"],
            "same_cell": ["prov-G1", "prov-G3"],
            "same_cell_below": ["prov-B"],
            "same_cell_above": ["prov-U"],
            "same_zone": ["prov-G1", "prov-G3"],
            "same_zone_below": ["prov-B"],
            "same_storey": ["prov-G1", "prov-G2", "prov-G3"],
            "same_storey_below": ["prov-B"],
            "same_system": ["prov-G1", "prov-G2", "prov-U"],
            "project": ["prov-B", "prov-G1", "prov-G2", "prov-G3", "prov-U", "prov-U2"],
        }
        for scope, names in expected.items():
            with self.subTest(scope=scope):
                got, cons = self.preds_of(sm, scope)
                self.assertEqual(got, names)
                self.assertTrue(all(p.reason.startswith(f"rule:{scope}:") for p in cons.predecessors))
                self.assertTrue(all(p.type == "FS" for p in cons.predecessors))

    def test_link_type_and_lag_come_from_rule(self):
        steps = [step("TST-PROV-DO"), step("TST-CONS-DO", preds=[("TST-PROV-DO", "same_cell", "SS", -2)]),
                 step("TST-DEFAULT-DO")]
        rl = [rule("R-p", 20, {"name_regex": "^p"}, ["TST-PROV-DO"]),
              rule("R-c", 10, {"name_regex": "^c"}, ["TST-CONS-DO"])]
        sm = map_elements(doc([elem("p", "p", "G", "G-Z1", [(0, 0)]), elem("c", "c", "G", "G-Z1", [(0, 0)])]),
                          library(steps), rules(rl), generated_at="t")
        cons = next(t for t in sm.tasks if t.step_id == "TST-CONS-DO")
        self.assertEqual([(p.type, p.lag_days) for p in cons.predecessors], [("SS", -2)])

    def test_missing_below_storey_and_host_are_silent(self):
        # a consumer on storey B (lowest) has no storey below; no host_guid -> no links, no gaps
        steps = [step("TST-PROV-DO"), step("TST-DEFAULT-DO"),
                 step("TST-CONS-DO", preds=[("TST-PROV-DO", "same_cell_below"), ("TST-PROV-DO", "host"),
                                            ("TST-PROV-DO", "same_system")])]
        rl = [rule("R-p", 20, {"name_regex": "^p"}, ["TST-PROV-DO"]),
              rule("R-c", 10, {"name_regex": "^c"}, ["TST-CONS-DO"])]
        sm = map_elements(doc([elem("p", "p", "G", "G-Z1", [(0, 0)]), elem("c", "c", "B", "B-Z1", [(0, 0)])]),
                          library(steps), rules(rl), generated_at="t")
        self.assertEqual(sm.sequencing_gaps, [])
        self.assertTrue(all(not t.predecessors for t in sm.tasks))

    def test_required_gap_recorded_only_when_required(self):
        steps = [step("TST-PROV-DO"), step("TST-DEFAULT-DO"),
                 step("TST-CONS-DO", preds=[("TST-PROV-DO", "same_cell", "FS", 0, True),
                                            ("TST-PROV-DO", "same_zone")])]
        rl = [rule("R-c", 10, {"name_regex": "^c"}, ["TST-CONS-DO"])]
        sm = map_elements(doc([elem("c", "c", "G", "G-Z1", [(0, 0)])]), library(steps), rules(rl), generated_at="t")
        self.assertEqual(sm.sequencing_gaps, [{"task_id": "T000001", "step": "TST-PROV-DO", "scope": "same_cell",
                                               "note": "required predecessor not found"}])

    def test_no_self_links_and_no_duplicates(self):
        steps = [step("TST-A-DO", preds=[("TST-A-DO", "same_cell"), ("TST-A-DO", "same_zone"), ("TST-A-DO", "same_storey")]),
                 step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 10, {}, ["TST-A-DO"])]
        sm = map_elements(doc([elem("e1", "e1", "G", "G-Z1", [(0, 0)]), elem("e2", "e2", "G", "G-Z2", [(2, 0)])]),
                          library(steps), rules(rl), generated_at="t")
        t1, t2 = sm.tasks
        self.assertEqual([p.task_id for p in t1.predecessors], [])           # t2 -> t1 cycle member dropped
        self.assertEqual([p.task_id for p in t2.predecessors], [t1.task_id])  # exactly one link, not self
        for t in sm.tasks:
            self.assertEqual(len({(p.task_id, p.type) for p in t.predecessors}), len(t.predecessors))


class CycleHandling(unittest.TestCase):
    def test_cycle_link_dropped_and_recorded(self):
        steps = [step("TST-A-DO", preds=[("TST-A-DO", "same_cell")]), step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 10, {}, ["TST-A-DO"])]
        els = [elem("e1", "e1", "G", "G-Z1", [(0, 0)]), elem("e2", "e2", "G", "G-Z1", [(0, 0)])]
        sm = map_elements(doc(els), library(steps), rules(rl), generated_at="t")
        t1, t2 = sm.tasks
        self.assertEqual([p.task_id for p in t1.predecessors], [])
        self.assertEqual([p.task_id for p in t2.predecessors], [t1.task_id])
        self.assertEqual(sm.sequencing_gaps, [{"task_id": t1.task_id, "step": "TST-A-DO", "scope": "same_cell",
                                               "note": "cycle"}])

    def test_longer_cycle_across_steps(self):
        # A -> B (same_element), B -> C (same_cell other element), C -> A (same_cell): cycle through 3 tasks
        steps = [step("TST-A-DO", preds=[("TST-C-DO", "same_cell")]),
                 step("TST-B-DO", preds=[("TST-A-DO", "same_element")]),
                 step("TST-C-DO", preds=[("TST-B-DO", "same_cell")]), step("TST-DEFAULT-DO")]
        rl = [rule("R-ab", 20, {"name_regex": "^x"}, ["TST-A-DO", "TST-B-DO"], chain=False),
              rule("R-c", 10, {"name_regex": "^y"}, ["TST-C-DO"])]
        sm = map_elements(doc([elem("x", "x", "G", "G-Z1", [(0, 0)]), elem("y", "y", "G", "G-Z1", [(0, 0)])]),
                          library(steps), rules(rl), generated_at="t")
        ids = {t.task_id: t for t in sm.tasks}
        # the output graph must be acyclic
        order, seen = [], set()

        def visit(tid, stack=()):
            self.assertNotIn(tid, stack, "cycle remains")
            if tid in seen:
                return
            seen.add(tid)
            for p in ids[tid].predecessors:
                visit(p.task_id, stack + (tid,))
            order.append(tid)
        for tid in ids:
            visit(tid)
        self.assertTrue(any(g["note"] == "cycle" for g in sm.sequencing_gaps))


if __name__ == "__main__":
    unittest.main()
