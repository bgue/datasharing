import unittest

from helpers import doc, elem, library, rule, rules, step  # noqa: F401  (sets sys.path)
from bimseq.mapper import map_elements
from bimseq.model import step_library_from_dict

STEPS = [step("TST-SURVEY-DO"), step("TST-ITEM-DO"), step("TST-TEST-DO"), step("TST-DEFAULT-DO")]
RECIPE = {"schema_version": "1.0", "id": "rec_anchor", "name": "anchor", "sector": "all",
          "applies_to": {"keywords": ["pipe"]},
          "steps": [{"ref": "TST-SURVEY-DO", "virtual": True, "duration_days": 2, "marker": "survey"},
                    {"ref": "TST-ITEM-DO"},
                    {"ref": "TST-TEST-DO", "virtual": True, "marker": "test"}]}


def elements():
    return [
        elem("e1", "pipe 1", "G", "G-Z1", [(0, 0)], system="A"),
        elem("e2", "pipe 2", "G", "G-Z1", [(1, 0)], system="A"),
        elem("e3", "pipe 3", "G", "G-Z2", [(2, 0)], system="A"),      # touches e2 but another zone
        elem("e4", "pipe 4", "G", "G-Z2", [(3, 0)], system="B"),      # touches e3 but another system
        elem("e5", "pipe 5", "U", "U-Z1", [(0, 0)], system="B"),
        elem("e6", "pipe 6", "U", "U-Z1", [(3, 0)], system="B"),      # same zone/system as e5, not contiguous
        elem("w", "wall", "G", "G-Z1", [(0, 0)]),                      # not matched
    ]


def run(anchor):
    lib = step_library_from_dict(library(STEPS).raw)
    kw = {} if anchor is None else {"anchor": anchor}
    rl = [rule("R-pipe", 20, {"name_regex": "^pipe"}, [], recipe="rec_anchor", **kw)]
    return map_elements(doc(elements()), lib, rules(rl), generated_at="t", recipes={"rec_anchor": RECIPE})


def virtual(sm, step_id):
    return [t for t in sm.tasks if t.virtual and t.step_id == step_id]


def bound(sm):
    return sorted(t.element_guid for t in sm.tasks if t.step_id == "TST-ITEM-DO")


class AnchorTypes(unittest.TestCase):
    EXPECTED = {None: 6, "element": 6, "system": 2, "zone": 3, "cell_group": 5, "storey": 2, "project": 1}

    def test_virtual_task_counts_per_anchor(self):
        for anchor, n in self.EXPECTED.items():
            with self.subTest(anchor=anchor):
                sm = run(anchor)
                self.assertEqual(len(virtual(sm, "TST-SURVEY-DO")), n)
                self.assertEqual(len(virtual(sm, "TST-TEST-DO")), n)
                self.assertEqual(sm.sequencing_gaps, [])

    def test_element_bound_steps_cover_every_matched_element_once(self):
        for anchor in self.EXPECTED:
            with self.subTest(anchor=anchor):
                self.assertEqual(bound(run(anchor)), ["e1", "e2", "e3", "e4", "e5", "e6"])

    def test_unmatched_elements_get_nothing(self):
        for anchor in self.EXPECTED:
            sm = run(anchor)
            self.assertEqual([t for t in sm.tasks if t.element_guid == "w" and t.origin == "recipe"], [])

    def test_cell_group_splits_on_zone_and_system_changes(self):
        sm = run("cell_group")
        groups = {}
        for t in sm.tasks:
            if t.step_id == "TST-ITEM-DO":
                groups[t.element_guid] = t.task_id
        # the survey task preceding each element task identifies its group
        by_id = {t.task_id: t for t in sm.tasks}
        survey_of = {g: next(by_id[p.task_id].task_id for p in by_id[tid].predecessors
                             if by_id[p.task_id].step_id == "TST-SURVEY-DO") for g, tid in groups.items()}
        self.assertEqual(survey_of["e1"], survey_of["e2"])
        self.assertEqual(len({survey_of[g] for g in ("e1", "e2", "e3", "e4", "e5", "e6")}), 5)

    def test_virtual_task_binding_scope(self):
        d = doc(elements())
        zone_cells = {z.id: sorted(map(tuple, z.cells)) for z in d.zones}
        sm = run("zone")
        for t in virtual(sm, "TST-SURVEY-DO"):
            self.assertEqual(sorted(t.cells), zone_cells[t.zone_id])
        sm = run("cell_group")
        by_zone = {}
        for t in virtual(sm, "TST-SURVEY-DO"):
            by_zone.setdefault(t.zone_id, []).append(sorted(t.cells))
        self.assertIn([(0, 0), (1, 0)], by_zone["G-Z1"])                  # union of e1 and e2 only
        self.assertIn([(2, 0)], by_zone["G-Z2"])
        sm = run("system")
        sysA = [t for t in virtual(sm, "TST-SURVEY-DO") if "system A" in t.element_name][0]
        self.assertEqual(sorted(sysA.cells), [(0, 0), (1, 0), (2, 0)])
        sm = run("project")
        (only,) = virtual(sm, "TST-SURVEY-DO")
        self.assertEqual(len(only.cells), 4)                                   # distinct cells over both storeys
        self.assertEqual((only.origin, only.recipe_id, only.virtual, only.element_guid), ("recipe", "rec_anchor", True, None))

    def test_links_group_to_members_and_back(self):
        sm = run("system")
        by_id = {t.task_id: t for t in sm.tasks}
        items = [t for t in sm.tasks if t.step_id == "TST-ITEM-DO"]
        for t in items:
            self.assertEqual([by_id[p.task_id].step_id for p in t.predecessors], ["TST-SURVEY-DO"])
        tests = virtual(sm, "TST-TEST-DO")
        self.assertEqual(sorted(len(t.predecessors) for t in tests), [3, 3])        # each test follows its 3 element tasks

    def test_large_group_links_are_paired_not_cross_multiplied(self):
        els = [elem(f"p{i:02d}", f"pipe {i}", "G", "G-Z1", [(i % 2, 0)], system="A") for i in range(24)]
        recipe = {**RECIPE, "steps": [{"ref": "TST-ITEM-DO"}, {"ref": "TST-TEST-DO", "from_element": "self"}]}
        lib = step_library_from_dict(library(STEPS).raw)
        rl = [rule("R-pipe", 20, {"name_regex": "^pipe"}, [], recipe="rec_anchor", anchor="project")]
        sm = map_elements(doc(els), lib, rules(rl), generated_at="t", recipes={"rec_anchor": recipe})
        tests = [t for t in sm.tasks if t.step_id == "TST-TEST-DO"]
        self.assertEqual(len(tests), 24)
        self.assertTrue(all(len(t.predecessors) == 1 for t in tests))               # same-element pairing


if __name__ == "__main__":
    unittest.main()
