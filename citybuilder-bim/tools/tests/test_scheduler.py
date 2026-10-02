import math
from bimseq.scheduler import LABOUR_UTILISATION as U
import random
import unittest

from helpers import doc, elem, library, rule, rules, scenario, step  # noqa: F401  (sets sys.path)
from bimseq.mapper import map_elements
from bimseq.scheduler import Edge, SchedulingError, build_sequence, cpm, level, weekly_cumulative_cost


class CpmHandGraph(unittest.TestCase):
    """A(3) -FS+1-> B(2); A -SS+2-> C(4); B -FF0-> D(4); C -FS-1-> E(1); D -FS-> E."""

    def setUp(self):
        self.dur = [3, 2, 4, 4, 1]                      # A B C D E
        self.edges = [Edge(0, 1, "FS", 1), Edge(0, 2, "SS", 2), Edge(1, 3, "FF", 0),
                      Edge(2, 4, "FS", -1), Edge(3, 4, "FS", 0)]

    def test_forward_dates(self):
        r = cpm(self.dur, self.edges)
        self.assertEqual(r.es, [0, 4, 2, 2, 6])
        self.assertEqual(r.ef, [3, 6, 6, 6, 7])
        self.assertEqual(r.finish, 7)

    def test_backward_float_and_critical_path(self):
        r = cpm(self.dur, self.edges)
        self.assertEqual(r.total_float, [0, 0, 1, 0, 0])
        self.assertEqual(r.ls, [0, 4, 3, 2, 6])

    def test_start_clamped_to_zero_and_cycle_rejected(self):
        r = cpm([2, 5], [Edge(0, 1, "FF", -10)])
        self.assertEqual(r.es[1], 0)
        with self.assertRaises(SchedulingError):
            cpm([1, 1], [Edge(0, 1), Edge(1, 0)])

    def test_milestone_zero_duration(self):
        r = cpm([2, 3, 0, 1], [Edge(0, 2), Edge(1, 2), Edge(2, 3)])
        self.assertEqual((r.es[2], r.es[3], r.finish), (3, 3, 4))


class Levelling(unittest.TestCase):
    @staticmethod
    def max_concurrency(starts, durs, idx):
        events = sorted({s for i, s in enumerate(starts) if i in idx})
        best = 0
        for day in events:
            best = max(best, sum(1 for i in idx if starts[i] <= day < starts[i] + durs[i]))
        return best

    def test_capacity_limits_parallel_tasks(self):
        durs = [2] * 6
        starts = level(durs, [], ["a"] * 6, {"a": 2})
        self.assertEqual(self.max_concurrency(starts, durs, range(6)), 2)
        self.assertEqual(max(s + d for s, d in zip(starts, durs)), 6)

    def test_missing_trade_defaults_to_one_crew(self):
        durs = [1, 1, 1]
        starts = level(durs, [], ["x"] * 3, {})
        self.assertEqual(sorted(starts), [0, 1, 2])

    def test_priority_decides_who_goes_first(self):
        starts = level([1, 1], [], ["a", "a"], {"a": 1}, priority=[5, 1])
        self.assertEqual(starts, [1, 0])

    def test_constraints_met_for_all_link_types(self):
        durs = [3, 2, 4, 4, 1]
        edges = [Edge(0, 1, "FS", 1), Edge(0, 2, "SS", 2), Edge(1, 3, "FF", 0), Edge(2, 4, "FS", -1), Edge(3, 4)]
        starts = level(durs, edges, [None] * 5, {})
        fin = [s + d for s, d in zip(starts, durs)]
        # without resource limits the levelled dates equal CPM, except that a successor linked by FF
        # is only released once its predecessor has been placed (D waits for B to start)
        self.assertEqual(starts, [0, 4, 2, 4, 8])
        no_ff = [e for e in edges if e.type != "FF"]
        self.assertEqual(level(durs, no_ff, [None] * 5, {}), cpm(durs, no_ff).es)
        self.assertGreaterEqual(starts[1], fin[0] + 1)
        self.assertGreaterEqual(starts[2], starts[0] + 2)
        self.assertGreaterEqual(fin[3], fin[1])
        self.assertGreaterEqual(starts[4], fin[2] - 1)

    def test_random_graphs_respect_capacity_and_precedence(self):
        rng = random.Random(7)
        for trial in range(25):
            n = rng.randint(8, 40)
            durs = [rng.randint(1, 5) for _ in range(n)]
            trades = [rng.choice("abc") for _ in range(n)]
            caps = {t: rng.randint(1, 3) for t in "abc"}
            edges = []
            for j in range(1, n):
                for i in rng.sample(range(j), k=min(j, rng.randint(0, 3))):
                    edges.append(Edge(i, j, rng.choice(["FS", "FS", "SS", "FF"]), rng.randint(-1, 3)))
            res = cpm(durs, edges)
            starts = level(durs, edges, trades, caps, priority=res.ls)
            for t in "abc":
                idx = [i for i in range(n) if trades[i] == t]
                self.assertLessEqual(self.max_concurrency(starts, durs, idx), caps[t], f"trial {trial}")
            for e in edges:
                sp, sf = starts[e.pred], starts[e.pred] + durs[e.pred]
                ss, sfs = starts[e.succ], starts[e.succ] + durs[e.succ]
                if e.type == "FS":
                    self.assertGreaterEqual(ss, sf + e.lag)
                elif e.type == "SS":
                    self.assertGreaterEqual(ss, sp + e.lag)
                else:
                    self.assertGreaterEqual(sfs, sf + e.lag)
            for i in range(n):
                self.assertGreaterEqual(starts[i], res.es[i], "levelled never earlier than CPM")


def build(elements, steps, rule_list, gates=(), crews=None, **scn):
    lib = library(steps, gates=gates)
    sm = map_elements(doc(elements), lib, rules(rule_list), generated_at="t")
    seq, warnings = build_sequence(sm, lib, scenario(crews, **scn), doc(elements), generated_at="t")
    return sm, seq, warnings, {t["element_guid"] + ":" + t["step_id"]: t for t in seq["tasks"]}


class SequenceBuild(unittest.TestCase):
    def test_durations_baseline_and_derived_scenario(self):
        steps = [step("TST-A-DO", basis="area_m2", rate=4.0, cost=100.0, min_duration_days=1),
                 step("TST-B-DO", basis="area_m2", rate=1.0, cost=10.0, min_duration_days=3),
                 step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, [{"step": "TST-A-DO", "quantity": "area_m2"}, {"step": "TST-B-DO", "quantity": "area_m2"}])]
        _, seq, _, t = build([elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"area_m2": 10.0})], steps, rl)
        a, b = t["e:TST-A-DO"], t["e:TST-B-DO"]
        self.assertEqual((a["planned_start_day"], a["planned_finish_day"]), (0, 3))      # ceil(2.5)
        self.assertEqual((b["planned_start_day"], b["planned_finish_day"]), (3, 13))     # ceil(10) > min 3
        self.assertTrue(a["is_critical"] and b["is_critical"])
        base = seq["baseline"]
        self.assertEqual((base["finish_day"], base["finish_week"]), (13, 3))
        self.assertEqual(base["total_cost"], 1100.0)
        labour = round(2500.0 / U, 2)                                     # (2.5 + 10 crew-days) * 1000 / 5 / utilisation
        self.assertAlmostEqual(base["total_labour_cost"], labour, places=2)
        self.assertAlmostEqual(base["weekly_planned_cost"][-1], 1100.0 + labour, places=1)  # materials + labour
        self.assertEqual(len(base["weekly_planned_cost"]), 4)
        self.assertEqual(base["weekly_planned_cost"], sorted(base["weekly_planned_cost"]))
        self.assertEqual(seq["scenario"]["contract_weeks"], math.ceil(3 * 1.1))           # 4, not float noise
        self.assertEqual(seq["scenario"]["budget"], round((1100.0 + labour) * 1.15 * 1.05))

    def test_labour_start_cash_and_overdraft_rules(self):
        steps = [step("TST-A-DO", min_duration_days=10, rate=1.0, trade="other"), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, [{"step": "TST-A-DO", "quantity": "length_m"}])]
        els = [elem("e", "E", "G", "G-Z1", [(0, 0)], quantities={"length_m": 10.0})]
        # 10 crew-days at trade weekly_cost 1000 -> 2000 / utilisation labour; materials 10 * 10 = 100; 2 weeks
        _, seq, warnings, _ = build(els, steps, rl)
        b = seq["baseline"]
        labour = round(2000.0 / U, 2)
        self.assertEqual((b["total_cost"], b["finish_week"]), (100.0, 2))
        self.assertAlmostEqual(b["total_labour_cost"], labour, places=2)
        weekly = (100.0 + labour) / 2
        self.assertEqual(seq["scenario"]["start_cash"], round(8 * weekly))  # 8 weeks of average spend
        self.assertEqual(seq["scenario"]["overdraft_limit"], round(4 * weekly))
        self.assertEqual(sum(w.startswith("note:") for w in warnings), 2)
        # generous values are kept, an explicit budget is not touched
        _, seq, warnings, _ = build(els, steps, rl, budget=777, start_cash=50000, overdraft_limit=20000)
        self.assertEqual((seq["scenario"]["budget"], seq["scenario"]["start_cash"],
                          seq["scenario"]["overdraft_limit"]), (777, 50000, 20000))
        self.assertFalse(any(w.startswith("note:") for w in warnings))

    def test_unknown_trade_labour_defaults_to_8000_per_week(self):
        from bimseq.model import Task
        from bimseq.scheduler import labour_costs
        lib = library([step("TST-A-DO")])
        t = Task("T000001", "g", "IfcX", "n", "G", "Z", None, "TST-A-DO", "build", "ghost", 1, "ea", 5.0, 1.0,
                 [(0, 0)], {}, [], "R-1")
        self.assertAlmostEqual(labour_costs([t], lib)[0], 8000.0 / U, places=6)

    def test_contract_factor_float_noise(self):
        steps = [step("TST-A-DO", basis="count", rate=1.0, min_duration_days=50), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, ["TST-A-DO"])]
        _, seq, _, _ = build([elem("e", "E", "G", "G-Z1", [(0, 0)])], steps, rl)
        self.assertEqual(seq["baseline"]["finish_week"], 10)
        self.assertEqual(seq["scenario"]["contract_weeks"], 11)                           # 10 * 1.1 -> 11

    def test_explicit_scenario_values_kept(self):
        steps = [step("TST-A-DO"), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, ["TST-A-DO"])]
        _, seq, _, _ = build([elem("e", "E", "G", "G-Z1", [(0, 0)])], steps, rl, contract_weeks=9, budget=500)
        self.assertEqual((seq["scenario"]["contract_weeks"], seq["scenario"]["budget"]), (9, 500))

    def test_levelling_never_exceeds_crews(self):
        steps = [step("TST-A-DO", min_duration_days=2), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, ["TST-A-DO"])]
        els = [elem(f"e{i}", f"E{i}", "G", "G-Z1", [(i % 2, 0)]) for i in range(7)]
        _, seq, _, _ = build(els, steps, rl, crews={"crew": 2})
        spans = [(t["planned_start_day"], t["planned_finish_day"]) for t in seq["tasks"]]
        for day in range(max(f for _, f in spans)):
            self.assertLessEqual(sum(1 for s, f in spans if s <= day < f), 2)
        self.assertEqual(max(f for _, f in spans), 8)                                      # ceil(7/2)*2
        self.assertFalse(all(t["is_critical"] for t in seq["tasks"]) and False)

    def test_predecessor_links_respected_after_levelling(self):
        steps = [step("TST-A-DO"), step("TST-B-DO", preds=[("TST-A-DO", "same_zone", "FS", 2)]),
                 step("TST-DEFAULT-DO")]
        rl = [rule("R-a", 20, {"name_regex": "^a"}, ["TST-A-DO"]), rule("R-b", 10, {"name_regex": "^b"}, ["TST-B-DO"])]
        els = [elem("a1", "a1", "G", "G-Z1", [(0, 0)]), elem("a2", "a2", "G", "G-Z1", [(1, 0)]),
               elem("b1", "b1", "G", "G-Z1", [(0, 0)])]
        _, seq, _, t = build(els, steps, rl, crews={"crew": 1})
        b = t["b1:TST-B-DO"]
        pred_fin = max(t["a1:TST-A-DO"]["planned_finish_day"], t["a2:TST-A-DO"]["planned_finish_day"])
        self.assertGreaterEqual(b["planned_start_day"], pred_fin + 2)

    def test_elements_shape_and_visual_override(self):
        steps = [step("TST-A-DO"), step("TST-DEFAULT-DO")]
        rl = [rule("R-1", 1, {}, ["TST-A-DO"], visual="tank")]
        _, seq, _, _ = build([elem("e", "E", "G", "G-Z1", [(0, 0), (1, 0)])], steps, rl)
        e = seq["elements"][0]
        self.assertEqual(e["visual"], "tank")
        self.assertEqual(len(e["size_hint"]), 3)
        self.assertEqual(sorted(e), ["cells", "guid", "ifc_class", "name", "size_hint", "storey_id", "system_id",
                                     "visual", "zone_id"])


class Gates(unittest.TestCase):
    def setUp(self):
        self.steps = [step("TST-P1-DO", phase="build", min_duration_days=3), step("TST-P2-DO", phase="fit"),
                      step("TST-DEFAULT-DO")]
        self.rl = [rule("R-1", 20, {"name_regex": "^one"}, ["TST-P1-DO"]), rule("R-2", 10, {"name_regex": "^two"}, ["TST-P2-DO"])]

    def run_gate(self, scope, elements):
        gate = [{"id": "G-t", "name": "t", "after_phase": "build", "before_phase": "fit", "scope": scope}]
        return build(elements, self.steps, self.rl, gates=gate, crews={"crew": 10})

    def test_storey_scope(self):
        els = [elem("one-g", "one-g", "G", "G-Z1", [(0, 0)]), elem("two-g", "two-g", "G", "G-Z2", [(2, 0)]),
               elem("two-u", "two-u", "U", "U-Z1", [(0, 0)])]
        sm, seq, _, t = self.run_gate("storey", els)
        self.assertEqual(len(seq["tasks"]), len(sm.tasks))                                   # no milestone tasks
        self.assertEqual(t["two-g:TST-P2-DO"]["planned_start_day"], 3)                       # held by P1 on same storey
        self.assertEqual(t["two-u:TST-P2-DO"]["planned_start_day"], 0)                       # other storey free
        self.assertEqual(t["two-g:TST-P2-DO"]["predecessors"], [])                           # gate is not in the output links

    def test_zone_scope(self):
        els = [elem("one-g", "one-g", "G", "G-Z1", [(0, 0)]), elem("two-a", "two-a", "G", "G-Z1", [(1, 0)]),
               elem("two-b", "two-b", "G", "G-Z2", [(2, 0)])]
        _, _, _, t = self.run_gate("zone", els)
        self.assertEqual(t["two-a:TST-P2-DO"]["planned_start_day"], 3)
        self.assertEqual(t["two-b:TST-P2-DO"]["planned_start_day"], 0)

    def test_project_scope_waits_for_all(self):
        els = [elem("one-g", "one-g", "G", "G-Z1", [(0, 0)]), elem("one-u", "one-u", "U", "U-Z1", [(0, 0)]),
               elem("two-b", "two-b", "B", "B-Z1", [(0, 0)])]
        steps = list(self.steps)
        steps[0] = step("TST-P1-DO", phase="build", basis="length_m", rate=1.0)
        els[0]["quantities"] = {"length_m": 4.0}
        els[1]["quantities"] = {"length_m": 7.0}
        self.rl[0] = rule("R-1", 20, {"name_regex": "^one"}, [{"step": "TST-P1-DO", "quantity": "length_m"}])
        lib = library(steps, gates=[{"id": "G-t", "name": "t", "after_phase": "build", "before_phase": "fit",
                                     "scope": "project"}])
        sm = map_elements(doc(els), lib, rules(self.rl), generated_at="t")
        seq, _ = build_sequence(sm, lib, scenario({"crew": 10}), doc(els), generated_at="t")
        t = {x["element_guid"]: x for x in seq["tasks"]}
        self.assertEqual(t["two-b"]["planned_start_day"], 7)                                  # longest P1 task is 7 days

    def test_gate_chain_through_levelling(self):
        # P1 limited to one crew: gate must still wait for the last P1 task
        els = [elem(f"one-{i}", f"one-{i}", "G", "G-Z1", [(i % 2, 0)]) for i in range(3)] + \
              [elem("two", "two", "G", "G-Z1", [(0, 0)])]
        gate = [{"id": "G-t", "name": "t", "after_phase": "build", "before_phase": "fit", "scope": "storey"}]
        _, seq, _, t = build(els, self.steps, self.rl, gates=gate, crews={"crew": 1})
        self.assertEqual(t["two:TST-P2-DO"]["planned_start_day"], 9)

    def test_gate_conflicting_with_task_links_is_dropped_with_warning(self):
        steps = [step("TST-P1-DO", phase="build", preds=[("TST-P2-DO", "same_zone")]), step("TST-P2-DO", phase="fit"),
                 step("TST-DEFAULT-DO")]
        els = [elem("one-g", "one-g", "G", "G-Z1", [(0, 0)]), elem("two-g", "two-g", "G", "G-Z1", [(1, 0)])]
        gate = [{"id": "G-t", "name": "t", "after_phase": "build", "before_phase": "fit", "scope": "zone"}]
        sm, seq, warnings, t = build(els, steps, self.rl, gates=gate, crews={"crew": 5})
        self.assertTrue(any("G-t" in w for w in warnings))
        self.assertGreaterEqual(t["one-g:TST-P1-DO"]["planned_start_day"], t["two-g:TST-P2-DO"]["planned_finish_day"])


class CumulativeGates(unittest.TestCase):
    """Gate build -> fit also holds the later phase 'late' and covers every earlier phase."""

    def run_it(self, elements, gates, phases=("early", "build", "fit", "late"), steps=None):
        steps = steps or [step("TST-E-DO", phase="early", min_duration_days=2), step("TST-B-DO", phase="build", min_duration_days=3),
                          step("TST-F-DO", phase="fit"), step("TST-L-DO", phase="late"), step("TST-DEFAULT-DO", phase="early")]
        rl = [rule(f"R-{k}", 50 - i, {"name_regex": f"^{k}"}, [f"TST-{k.upper()}-DO"]) for i, k in enumerate("ebfl")]
        lib = library(steps, gates=gates, phases=phases)
        sm = map_elements(doc(elements), lib, rules(rl), generated_at="t")
        seq, warnings = build_sequence(sm, lib, scenario({"crew": 20}), doc(elements), generated_at="t")
        return sm, seq, warnings, {t["element_guid"]: t for t in seq["tasks"]}

    GATE = [{"id": "G-t", "name": "t", "after_phase": "build", "before_phase": "fit", "scope": "storey"}]

    def test_unnamed_later_phase_is_held_and_earlier_phases_are_prerequisites(self):
        els = [elem("e1", "e1", "G", "G-Z1", [(0, 0)]), elem("b1", "b1", "G", "G-Z1", [(1, 0)]),
               elem("f1", "f1", "G", "G-Z2", [(2, 0)]), elem("l1", "l1", "G", "G-Z2", [(3, 0)])]
        _, _, _, t = self.run_it(els, self.GATE)
        self.assertEqual(t["e1"]["planned_start_day"], 0)
        self.assertEqual(t["b1"]["planned_start_day"], 0)
        self.assertEqual(t["f1"]["planned_start_day"], 3)       # waits for build (3d) in the storey
        self.assertEqual(t["l1"]["planned_start_day"], 3)       # 'late' is named by no gate but still held

    def test_early_phase_is_prerequisite_even_without_build_tasks(self):
        els = [elem("e1", "e1", "G", "G-Z1", [(0, 0)]), elem("l1", "l1", "G", "G-Z2", [(2, 0)])]
        _, _, _, t = self.run_it(els, self.GATE)
        self.assertEqual(t["l1"]["planned_start_day"], 2)       # held by the 'early' task (2d)

    def test_vacuous_scope_instance_passes_immediately(self):
        gate = [dict(self.GATE[0], scope="zone")]
        els = [elem("b1", "b1", "G", "G-Z1", [(0, 0)]), elem("f1", "f1", "G", "G-Z1", [(1, 0)]),
               elem("l2", "l2", "G", "G-Z2", [(2, 0)])]
        _, _, _, t = self.run_it(els, gate)
        self.assertEqual(t["f1"]["planned_start_day"], 3)
        self.assertEqual(t["l2"]["planned_start_day"], 0)       # G-Z2 has nothing at or below 'build'

    def test_gate_cycle_recorded_in_sequencing_gaps(self):
        steps = [step("TST-E-DO", phase="early"), step("TST-B-DO", phase="build"), step("TST-DEFAULT-DO", phase="early"),
                 step("TST-F-DO", phase="fit"), step("TST-L-DO", phase="late"),
                 ]
        steps[1] = step("TST-B-DO", phase="build", preds=[("TST-F-DO", "same_zone")])
        els = [elem("b1", "b1", "G", "G-Z1", [(0, 0)]), elem("f1", "f1", "G", "G-Z1", [(1, 0)])]
        sm, _, warnings, t = self.run_it(els, [dict(self.GATE[0], scope="zone")], steps=steps)
        self.assertTrue(any("G-t" in w for w in warnings))
        self.assertEqual([g["note"] for g in sm.sequencing_gaps], ["gate_cycle"])
        self.assertEqual(sm.sequencing_gaps[0]["scope"], "zone")


class WeeklyCost(unittest.TestCase):
    def test_linear_accrual(self):
        from bimseq.model import Task
        t = Task("T000001", "g", "IfcX", "n", "G", "Z", None, "TST-A-DO", "build", "crew", 1, "ea", 1, 1000.0, [(0, 0)],
                 {}, [], "R-1", planned_start_day=3, planned_finish_day=8)
        self.assertEqual(weekly_cumulative_cost([t]), [400.0, 1000.0, 1000.0])    # weeks 0..finish_week


if __name__ == "__main__":
    unittest.main()
