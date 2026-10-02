import copy
import tempfile
import unittest
from pathlib import Path

from helpers import doc, elem, library, rule, rules, scenario, step, REPO  # noqa: F401  (sets sys.path)
from bimseq import pipeline
from bimseq.mapper import map_elements
from bimseq.model import read_json, step_library_from_dict
from bimseq.packaging import check_cards, crew_profile
from bimseq.scheduler import build_sequence
from bimseq.validate import validate_file

ZONES = [
    ("B-Z1", "B", [(0, 0), (1, 0), (2, 0), (3, 0)], []),
    ("G-Z1", "G", [(0, 0), (1, 0)], []),
    ("G-Z2", "G", [(2, 0), (3, 0)], []),
    ("U-Z1", "U", [(0, 0), (1, 0), (2, 0), (3, 0)], []),
]


def run(elements, steps, rule_list, packaging=None, scn=None, phases=("build", "fit"), zones=None, **libkw):
    lib_dict = library(steps, phases=phases, **libkw).raw
    if packaging is not None:
        lib_dict["packaging"] = packaging
    lib = step_library_from_dict(lib_dict)
    d = doc(elements, zones=zones)
    sm = map_elements(d, lib, rules(rule_list), generated_at="t")
    seq, warnings = build_sequence(sm, lib, scn or scenario({"crew": 50, "other": 50}), d, generated_at="t")
    return sm, seq, warnings


def one_rule(step_id, **kw):
    return [rule("R-1", 1, {}, [{"step": step_id, "quantity": "count"}], **kw)]


class Grouping(unittest.TestCase):
    def test_default_grouping_by_zone_phase_trade_face(self):
        steps = [step("TST-A-DO", phase="build", trade="crew", work_face="walls"),
                 step("TST-B-DO", phase="build", trade="other", work_face="walls"),
                 step("TST-C-DO", phase="fit", trade="crew", work_face="floor"),
                 step("TST-D-DO", phase="build", trade="crew"),
                 step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 40, {"name_regex": "^a"}, ["TST-A-DO", "TST-C-DO"], chain=False),
              rule("R-b", 30, {"name_regex": "^b"}, ["TST-B-DO"]), rule("R-d", 20, {"name_regex": "^d"}, ["TST-D-DO"])]
        els = [elem("a1", "a1", "G", "G-Z1", [(0, 0)]), elem("a2", "a2", "G", "G-Z1", [(1, 0)]),
               elem("a3", "a3", "G", "G-Z2", [(2, 0)]), elem("b1", "b1", "G", "G-Z1", [(0, 0)]),
               elem("d1", "d1", "G", "G-Z1", [(1, 0)])]
        _, seq, _ = run(els, steps, rl)
        keys = [(p["zone_id"], p["phase"], p["trade"], p["work_face"], len(p["task_ids"])) for p in seq["packages"]]
        self.assertEqual(sorted(keys), sorted([
            ("G-Z1", "build", "crew", "walls", 2), ("G-Z1", "fit", "crew", "floor", 2), ("G-Z2", "build", "crew", "walls", 1),
            ("G-Z2", "fit", "crew", "floor", 1), ("G-Z1", "build", "other", "walls", 1), ("G-Z1", "build", "crew", "any", 1)]))
        # work_face is copied onto tasks; package ids are P + 5 digits
        by_id = {t["task_id"]: t for t in seq["tasks"]}
        self.assertEqual({t["work_face"] for t in seq["tasks"]}, {"walls", "floor", "any"})
        for p in seq["packages"]:
            self.assertRegex(p["package_id"], r"^P\d{5}$")
            for tid in p["task_ids"]:
                self.assertEqual(by_id[tid]["package_id"], p["package_id"])

    def test_group_by_override(self):
        steps = [step("TST-A-DO", phase="build", trade="crew"), step("TST-B-DO", phase="fit", trade="other"), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, ["TST-A-DO", "TST-B-DO"], chain=False)]
        els = [elem("e1", "e1", "G", "G-Z1", [(0, 0)]), elem("e2", "e2", "G", "G-Z2", [(2, 0)])]
        _, seq, _ = run(els, steps, rl, packaging={"group_by": ["zone_id"]})
        self.assertEqual(sorted(len(p["task_ids"]) for p in seq["packages"]), [2, 2])

    def test_ids_ordered_by_storey_zone_phase_trade_face_first_task(self):
        steps = [step("TST-A-DO", phase="fit", trade="crew"), step("TST-B-DO", phase="build", trade="other"),
                 step("TST-C-DO", phase="build", trade="crew"), step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 30, {"name_regex": "^a"}, ["TST-A-DO"]), rule("R-b", 20, {"name_regex": "^b"}, ["TST-B-DO"]),
              rule("R-c", 10, {"name_regex": "^c"}, ["TST-C-DO"])]
        els = [elem("c-u", "c", "U", "U-Z1", [(0, 0)]), elem("a-g", "a", "G", "G-Z2", [(2, 0)]),
               elem("b-g", "b", "G", "G-Z1", [(0, 0)]), elem("a-g1", "a", "G", "G-Z1", [(0, 0)]),
               elem("c-g1", "c", "G", "G-Z1", [(1, 0)]), elem("c-b", "c", "B", "B-Z1", [(0, 0)])]
        _, seq, _ = run(els, steps, rl)
        order = [(p["storey_id"], p["zone_id"], p["phase"], p["trade"]) for p in seq["packages"]]
        self.assertEqual(order, [("B", "B-Z1", "build", "crew"), ("G", "G-Z1", "build", "crew"),
                                 ("G", "G-Z1", "build", "other"), ("G", "G-Z1", "fit", "crew"),
                                 ("G", "G-Z2", "fit", "crew"), ("U", "U-Z1", "build", "crew")])
        self.assertEqual([p["package_id"] for p in seq["packages"]], [f"P{i:05d}" for i in range(1, 7)])
        _, seq2, _ = run(list(reversed(els)), steps, rl)
        self.assertEqual(seq["packages"], seq2["packages"])

    def test_package_aggregates(self):
        steps = [step("TST-A-DO", cost=10.0, requires_crane=True, lead_time_weeks=3, laydown_cells=1, min_duration_days=2),
                 step("TST-B-DO", cost=10.0, lead_time_weeks=8, laydown_cells=4, min_duration_days=3), step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 20, {"name_regex": "^a"}, ["TST-A-DO"]), rule("R-b", 10, {"name_regex": "^b"}, ["TST-B-DO"])]
        # same phase/trade/face so both land in one package
        els = [elem("a", "a", "G", "G-Z1", [(0, 0)]), elem("b", "b", "G", "G-Z1", [(1, 0)])]
        _, seq, _ = run(els, steps, rl, scn=scenario({"crew": 1}))
        (p,) = seq["packages"]
        tasks = {t["task_id"]: t for t in seq["tasks"]}
        mine = [tasks[i] for i in p["task_ids"]]
        self.assertEqual(p["planned_start_day"], min(t["planned_start_day"] for t in mine))
        self.assertEqual(p["planned_finish_day"], max(t["planned_finish_day"] for t in mine))
        self.assertEqual((p["requires_crane"], p["lead_time_weeks"], p["laydown_cells"], p["cost"]), (True, 8, 4, 20.0))
        self.assertEqual(p["total_crew_days"], 2.0)
        self.assertIn("G-Z1 · build · crew", p["name"])
        self.assertEqual(p["discipline"], "general")
        self.assertEqual(p["storey_id"], "G")


class Splitting(unittest.TestCase):
    def test_split_in_planned_start_order_with_suffixes(self):
        steps = [step("TST-A-DO", basis="count", rate=0.1), step("TST-DEFAULT-DO")]       # 10 crew-days per task
        rl = one_rule("TST-A-DO")
        els = [elem(f"e{i}", f"e{i}", "G", "G-Z1", [(i % 2, 0)]) for i in range(7)]
        _, seq, _ = run(els, steps, rl, packaging={"max_crew_days_per_package": 25}, scn=scenario({"crew": 1}))
        pkgs = seq["packages"]
        self.assertEqual([len(p["task_ids"]) for p in pkgs], [2, 2, 2, 1])
        self.assertTrue(all(p["total_crew_days"] <= 25 for p in pkgs))
        self.assertEqual([p["name"][-6:] for p in pkgs], [" (1/4)", " (2/4)", " (3/4)", " (4/4)"])
        self.assertTrue(all(p["zone_id"] == "G-Z1" and p["trade"] == "crew" for p in pkgs))
        tasks = {t["task_id"]: t for t in seq["tasks"]}
        starts = [[tasks[i]["planned_start_day"] for i in p["task_ids"]] for p in pkgs]
        flat = [s for chunk in starts for s in chunk]
        self.assertEqual(flat, sorted(flat), "chunks follow planned-start order")
        self.assertEqual([p["package_id"] for p in pkgs], [f"P{i:05d}" for i in range(1, 5)])

    def test_oversize_single_task_stays_alone(self):
        steps = [step("TST-A-DO", rate=0.01), step("TST-DEFAULT-DO")]                    # 100 crew-days
        _, seq, _ = run([elem("e", "e", "G", "G-Z1", [(0, 0)])], steps, one_rule("TST-A-DO"))
        self.assertEqual(len(seq["packages"]), 1)
        self.assertNotIn("(1/1)", seq["packages"][0]["name"])


class CrewProfile(unittest.TestCase):
    def lib(self, **pk):
        return step_library_from_dict(library([step("TST-A-DO", crew_profile={"min": 2}),
                                               step("TST-B-DO", crew_profile={"min": 1, "ideal": 6, "max": 9}),
                                               step("TST-C-DO")]).raw | {"packaging": pk})

    def tasks(self, lib, *ids):
        from bimseq.model import Task
        return [Task(f"T{i:06d}", "g", "IfcX", "n", "G", "Z", None, sid, "build", "crew", 1, "ea", 1, 1.0, [(0, 0)],
                     lib.steps[sid].flags(), [], "R-1") for i, sid in enumerate(ids, start=1)]

    def test_derived_from_size_and_clamped_to_zone(self):
        lib = self.lib(target_duration_days=10, max_over_ideal=1)
        t = self.tasks(lib, "TST-C-DO")
        self.assertEqual(crew_profile(t, lib, 5.0, 4), {"min": 1, "ideal": 1, "max": 2})        # ceil(0.5) = 1
        self.assertEqual(crew_profile(t, lib, 23.5, 4), {"min": 1, "ideal": 3, "max": 4})       # docs example
        self.assertEqual(crew_profile(t, lib, 80.0, 4), {"min": 1, "ideal": 4, "max": 4})       # ideal clamped to max_crews
        self.assertEqual(crew_profile(t, lib, 80.0, 2), {"min": 1, "ideal": 2, "max": 2})
        self.assertEqual(crew_profile(t, lib, 0.0, 3), {"min": 1, "ideal": 1, "max": 2})

    def test_min_is_max_of_members_and_lifts_ideal(self):
        lib = self.lib()
        t = self.tasks(lib, "TST-A-DO", "TST-C-DO")
        self.assertEqual(crew_profile(t, lib, 5.0, 4), {"min": 2, "ideal": 2, "max": 3})
        self.assertEqual(crew_profile(t, lib, 5.0, 1), {"min": 1, "ideal": 1, "max": 1}, "min capped at zone max_crews")

    def test_explicit_member_values_win_then_clamp(self):
        lib = self.lib()
        t = self.tasks(lib, "TST-B-DO", "TST-C-DO")
        self.assertEqual(crew_profile(t, lib, 5.0, 10), {"min": 1, "ideal": 6, "max": 9})
        self.assertEqual(crew_profile(t, lib, 5.0, 4), {"min": 1, "ideal": 4, "max": 4})
        self.assertEqual(crew_profile(t, lib, 5.0, 8), {"min": 1, "ideal": 6, "max": 8})

    def test_package_profile_uses_zone_max_crews(self):
        steps = [step("TST-A-DO", rate=0.1, crew_profile={"min": 2}), step("TST-DEFAULT-DO")]       # 10 crew-days/task
        els = [elem(f"e{i}", f"e{i}", "G", "G-Z1", [(0, 0)]) for i in range(3)]
        _, seq, _ = run(els, steps, one_rule("TST-A-DO"), packaging={"max_crew_days_per_package": 1000})
        self.assertEqual(seq["packages"][0]["crew_profile"], {"min": 2, "ideal": 2, "max": 2})  # zones have max_crews 2


class Cards(unittest.TestCase):
    def test_card_ref_gap_for_unknown_selects(self):
        lib = step_library_from_dict(library([step("TST-A-DO")]).raw | {"sequence_cards": [
            {"id": "card_ok", "name": "ok", "stations": [{"name": "s", "select": {"phase": "build", "trade": "crew"}}]},
            {"id": "card_bad", "name": "bad", "stations": [{"name": "s1", "select": {"phase": "nope"}},
                                                          {"name": "s2", "select": {"trade": "ghost", "work_face": "walls"}}]}]})
        gaps = check_cards(lib, [])
        self.assertEqual([(g["step"], g["note"]) for g in gaps], [("card_bad", "card_ref")] * 2)
        # a scenario card with the same id overrides the library card
        fixed = {"id": "card_bad", "name": "fixed", "stations": [{"name": "s", "select": {"phase": "fit"}}]}
        self.assertEqual(check_cards(lib, [fixed]), [])
        # and a bad scenario card is reported
        extra = {"id": "card_new", "name": "n", "stations": [{"name": "s", "select": {"discipline": "fire"}}]}
        self.assertEqual(len(check_cards(lib, [fixed, extra])), 1)

    def test_gap_lands_in_step_map_and_cards_untouched(self):
        steps = [step("TST-A-DO"), step("TST-DEFAULT-DO")]
        raw = library(steps).raw | {"sequence_cards": [{"id": "card_x", "name": "x",
                                                         "stations": [{"name": "s", "select": {"phase": "zzz"}}]}]}
        lib = step_library_from_dict(raw)
        d = doc([elem("e", "e", "G", "G-Z1", [(0, 0)])])
        sm = map_elements(d, lib, rules(one_rule("TST-A-DO")), generated_at="t")
        scn = scenario(sequence_cards=[{"id": "card_y", "name": "y", "stations": [{"name": "s", "select": {"phase": "build"}}]}])
        seq, warnings = build_sequence(sm, lib, scn, d, generated_at="t")
        self.assertEqual([g["note"] for g in sm.sequencing_gaps], ["card_ref"])
        self.assertTrue(any("card_ref" in w for w in warnings))
        self.assertEqual(seq["step_library"]["sequence_cards"][0]["id"], "card_x")
        self.assertEqual(seq["scenario"]["sequence_cards"][0]["id"], "card_y")


class SectorBundles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        for s in pipeline.SECTORS:
            pipeline.build_sector(s, cls.out, Path("/nonexistent"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_task_has_package_and_every_package_task_exists(self):
        for s in pipeline.SECTORS:
            with self.subTest(sector=s):
                seq = read_json(self.out / s / "sequence.json")
                m = read_json(self.out / s / "element_step_map.json")
                ids = {t["task_id"] for t in seq["tasks"]}
                pk = {p["package_id"]: p for p in seq["packages"]}
                self.assertTrue(seq["packages"])
                self.assertTrue(all(t.get("package_id") in pk for t in seq["tasks"]))
                self.assertTrue(all(t.get("work_face") for t in seq["tasks"]))
                self.assertEqual({tid for p in pk.values() for tid in p["task_ids"]}, ids)
                self.assertEqual(sum(len(p["task_ids"]) for p in pk.values()), len(ids))
                self.assertEqual({t["task_id"]: t["package_id"] for t in seq["tasks"]},
                                 {t["task_id"]: t["package_id"] for t in m["tasks"]})
                limit = seq["step_library"].get("packaging", {}).get("max_crew_days_per_package", 60)
                zones = {z["id"]: z for z in seq["zones"]}
                for p in pk.values():
                    cp = p["crew_profile"]
                    self.assertTrue(1 <= cp["min"] <= cp["ideal"] <= cp["max"] <= zones[p["zone_id"]]["max_crews"])
                    if len(p["task_ids"]) > 1:
                        self.assertLessEqual(p["total_crew_days"], limit + 1e-6)
                ok, msgs = validate_file(self.out / s / "sequence.json")
                self.assertTrue(ok, msgs)
                ok, msgs = validate_file(self.out / s / "element_step_map.json")
                self.assertTrue(ok, msgs)

    def test_synthetic_zone_faces_and_tags(self):
        z = {s: read_json(self.out / s / "elements.json")["zones"] for s in pipeline.SECTORS}
        tagged = lambda sector, tag: [x for x in z[sector] if tag in x["tags"]]
        self.assertEqual(len(tagged("healthcare", "or_room")), 4)
        self.assertEqual(len([x for x in tagged("healthcare", "or_room") if x["storey_id"] == "L01"]), 2)
        self.assertEqual([x["storey_id"] for x in tagged("healthcare", "imaging")], ["L00"])
        self.assertEqual(tagged("healthcare", "plant_room")[0]["faces"], {"plant_pad": 2, "ceiling_void": 1})
        self.assertEqual(tagged("healthcare", "or_room")[0]["faces"], {"ceiling_void": 2, "walls": 2, "floor": 1})
        for tag in ("pipe_rack", "process_unit"):
            self.assertTrue(all(x["faces"] == {"structure": 2, "plant_pad": 2, "ceiling_void": 1} for x in tagged("industrial", tag)))
            self.assertTrue(tagged("industrial", tag))
        self.assertTrue(tagged("civil", "bridge") and tagged("civil", "segment"))
        civil = read_json(self.out / "civil" / "elements.json")
        zone_of = {e["guid"]: e["zone_id"] for e in civil["elements"] if "ulvert" in e["name"]}
        culvert_zones = {x["id"] for x in tagged("civil", "culvert")}
        self.assertTrue(culvert_zones and set(zone_of.values()) <= culvert_zones)
        bridge_zones = {x["id"] for x in tagged("civil", "bridge")}
        bridge_elems = {e["zone_id"] for e in civil["elements"] if e["properties"].get("BridgePart")}
        self.assertTrue(bridge_elems <= bridge_zones)
        self.assertTrue(all(x["faces"] == {"below_ground": 2, "structure": 2, "floor": 2}
                            for x in tagged("civil", "bridge") + tagged("civil", "segment")))
        crews = lambda sector, tag: {x["max_crews"] for x in tagged(sector, tag)}
        self.assertEqual((crews("healthcare", "or_room"), crews("healthcare", "imaging"), crews("healthcare", "plant_room")),
                         ({3}, {3}, {4}))
        self.assertEqual({x["max_crews"] for x in z["healthcare"] if x["storey_id"] == "L01" and not set(x["tags"]) & {"or_room"}}, {2})
        self.assertEqual({x["max_crews"] for x in z["healthcare"] if x["storey_id"] == "L00" and "imaging" not in x["tags"]}, {4})
        self.assertEqual((crews("industrial", "process_unit"), crews("industrial", "equipment_yard")), ({5}, {4}))
        self.assertEqual({x["max_crews"] for x in z["industrial"] if "process_unit" not in x["tags"]
                          and "equipment_yard" not in x["tags"]}, {3})
        self.assertEqual((crews("civil", "bridge"), crews("civil", "culvert"), crews("civil", "segment") - {4}), ({4}, {3}, {3}))
        self.assertEqual({x["max_crews"] for x in z["civil"] if x["storey_id"] == "UG1"}, {2})
        for sec in z.values():
            for x in sec:
                self.assertTrue(all(v <= x["max_crews"] for v in x.get("faces", {}).values()), x["id"])

    def test_packages_cli(self):
        import io
        from contextlib import redirect_stdout
        from bimseq import __main__ as cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["packages", str(self.out / "healthcare" / "sequence.json"), "--limit", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("P00001", buf.getvalue())
        self.assertIn("packages shown", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
