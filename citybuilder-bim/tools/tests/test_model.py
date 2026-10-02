import unittest

from helpers import REPO, library, step  # noqa: F401  (sets sys.path)
from bimseq import model


class Defaults(unittest.TestCase):
    def test_step_defaults(self):
        lib = model.step_library_from_dict({
            "schema_version": "1.0", "sector": "civil",
            "phases": [{"id": "a", "name": "A", "order": 0}, {"id": "b", "name": "B", "order": 1}],
            "trades": [{"id": "t", "name": "T", "weekly_cost": 1}],
            "steps": [{"id": "AB-CD", "name": "x", "phase": "a", "trade": "t", "discipline": "civil",
                       "quantity_basis": "volume_m3", "rate_per_crew_day": 2, "unit_cost": 3,
                       "predecessors": [{"step": "AB-CD", "scope": "project"}]}]})
        s = lib.steps["AB-CD"]
        self.assertTrue(s.requires_access)
        self.assertFalse(s.requires_crane)
        self.assertEqual((s.min_duration_days, s.laydown_cells, s.lead_time_weeks, s.risk, s.progress_visual),
                         (1, 0, 0, 0.1, "solid"))
        self.assertEqual((s.predecessors[0].type, s.predecessors[0].lag_days, s.predecessors[0].required),
                         ("FS", 0, False))
        self.assertEqual(lib.trades["t"].crew_size, 4)
        self.assertEqual(s.default_unit, "m3")
        self.assertEqual(s.flags()["requires_access"], True)

    def test_rule_and_emit_defaults(self):
        mr = model.mapping_rules_from_dict({
            "schema_version": "1.0", "sector": "civil",
            "rules": [{"id": "R-a", "priority": 5, "match": {"name_regex": "x", "predefined_type": [None, "A"]},
                       "steps": [{"step": "AB-CD", "quantity": "count"}]},
                      {"id": "R-b", "priority": 9, "match": {}, "steps": [{"step": "AB-CD", "quantity": "count"}]}],
            "default": {"steps": [{"step": "AB-CD", "quantity": "count"}]}})
        r = mr.rules[0]
        self.assertTrue(r.chain)
        self.assertFalse(r.continue_)
        e = r.steps[0]
        self.assertEqual((e.quantity_factor, e.lag_days, e.min_quantity, e.unit_override), (1.0, 0, 0.0, None))
        self.assertEqual([x.id for x in mr.ordered()], ["R-b", "R-a"])
        self.assertTrue(r.match.name_regex.search("axb"))
        self.assertEqual(r.match.predefined_type, [None, "A"])

    def test_scenario_defaults(self):
        sc = model.scenario_from_dict({"id": "s", "sector": "civil", "contract_weeks": None, "budget": 0})
        self.assertEqual((sc.contract_factor, sc.budget_factor, sc.crews_available), (1.1, 1.15, {}))


class Loaders(unittest.TestCase):
    def test_minimal_sample_loads(self):
        base = REPO / "data" / "samples" / "minimal"
        seq = model.load_sequence(base / "sequence.json")
        self.assertEqual(len(seq.tasks), 12)
        self.assertEqual(seq.tasks[8].predecessors[0].type, "FS")
        self.assertEqual(seq.scenario.id, "minimal")
        self.assertIn("STR-SLAB-POUR", seq.step_library.steps)
        sm = model.load_step_map(base / "element_step_map.json")
        self.assertEqual(sm.sector, "healthcare")
        lib = model.load_step_library(base / "step_library.json")
        self.assertEqual(lib.gates[0].after_phase, "superstructure")

    def test_task_round_trip(self):
        seq = model.load_sequence(REPO / "data" / "samples" / "minimal" / "sequence.json")
        raw = model.read_json(REPO / "data" / "samples" / "minimal" / "sequence.json")
        for t, d in zip(seq.tasks, raw["tasks"]):
            self.assertEqual(t.to_dict(), d)

    def test_unknown_storey_rejected(self):
        with self.assertRaises(model.ModelError):
            model.elements_from_dict({
                "project": {}, "storeys": [{"id": "G", "name": "G", "index": 0}], "zones": [],
                "elements": [{"guid": "g", "ifc_class": "IfcX", "name": "n", "storey_id": "Z", "zone_id": "z",
                              "cells": [[0, 0]], "quantities": {}}]})


if __name__ == "__main__":
    unittest.main()
