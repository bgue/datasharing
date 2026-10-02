"""Baseline scheduler: CPM (FS/SS/FF + lag), gate milestones, greedy crew levelling, sequence.json.

Conventions: days are working days from day 0; a task occupies ``[start, finish)`` so a
one-day task starting on day 0 finishes on day 1; weeks are 5 days.
"""
from __future__ import annotations

import copy
import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Sequence as Seq

from . import GENERATOR
from .graph import cyclic_components, topological_order
from .mapper import utc_now
from .model import (
    ElementsDoc, Gate, JSON, Scenario, StepLibrary, StepMap, Task,
)
from .visuals import VISUALS, size_hint, visual_for

DAYS_PER_WEEK = 5


class SchedulingError(ValueError):
    """The task graph cannot be scheduled (cycle, missing nodes)."""


@dataclass(frozen=True)
class Edge:
    """Precedence ``pred -> succ`` of type FS/SS/FF with ``lag`` working days."""

    pred: int
    succ: int
    type: str = "FS"
    lag: int = 0


@dataclass
class CpmResult:
    es: list[int]
    ef: list[int]
    ls: list[int]
    lf: list[int]
    total_float: list[int]
    finish: int


def _adjacency(n: int, edges: Seq[Edge]) -> tuple[list[list[Edge]], list[list[Edge]]]:
    preds: list[list[Edge]] = [[] for _ in range(n)]
    succs: list[list[Edge]] = [[] for _ in range(n)]
    for e in edges:
        preds[e.succ].append(e)
        succs[e.pred].append(e)
    return preds, succs


def cpm(durations: Seq[int], edges: Seq[Edge], finish_nodes: Seq[int] | None = None) -> CpmResult:
    """Unlevelled critical path method.

    Forward pass gives early dates; backward pass (anchored on the latest early finish of
    ``finish_nodes``, default all nodes) gives late dates and total float.
    """
    n = len(durations)
    preds, succs = _adjacency(n, edges)
    order = topological_order(n, [[e.succ for e in succs[v]] for v in range(n)])
    if order is None:
        raise SchedulingError("precedence graph has a cycle")
    es = [0] * n
    ef = [0] * n
    for v in order:
        start = 0
        for e in preds[v]:
            if e.type == "FS":
                start = max(start, ef[e.pred] + e.lag)
            elif e.type == "SS":
                start = max(start, es[e.pred] + e.lag)
            elif e.type == "FF":
                start = max(start, ef[e.pred] + e.lag - durations[v])
            else:
                raise SchedulingError(f"unknown link type {e.type!r}")
        es[v] = start
        ef[v] = start + durations[v]
    nodes = range(n) if finish_nodes is None else finish_nodes
    finish = max((ef[v] for v in nodes), default=0)
    lf = [finish] * n
    ls = [0] * n
    for v in reversed(order):
        limit = finish
        for e in succs[v]:
            if e.type == "FS":
                limit = min(limit, ls[e.succ] - e.lag)
            elif e.type == "SS":
                limit = min(limit, ls[e.succ] - e.lag + durations[v])
            else:  # FF
                limit = min(limit, lf[e.succ] - e.lag)
        lf[v] = limit
        ls[v] = limit - durations[v]
    return CpmResult(es, ef, ls, lf, [ls[v] - es[v] for v in range(n)], finish)


def level(durations: Seq[int], edges: Seq[Edge], resources: Seq[str | None],
          capacity: Mapping[str, int], priority: Seq[float] | None = None,
          default_capacity: int = 1, max_days: int = 200_000,
          loads: Seq[float] | None = None) -> list[int]:
    """Greedy day-by-day resource levelling; returns start days.

    Nodes with duration 0 are milestones: scheduled as soon as their predecessors are.
    Other nodes start on the first day all predecessor constraints hold and fewer than
    ``capacity[resource]`` nodes of the same resource are active.  Candidates are ordered by
    ``priority`` (low first, e.g. CPM late start) then node index.  A node whose resource is
    ``None`` is not capacity limited.  By default every active node occupies one crew; with
    ``loads`` a node occupies ``loads[i]`` crews (e.g. crew-days / duration), so several small
    tasks can share one crew.

    Limitation: a node is considered only after *all* its predecessors have been placed, so a
    successor linked by FF (or SS with negative lag) never starts before its predecessor starts.
    """
    n = len(durations)
    prio = list(priority) if priority is not None else [0] * n
    preds, succs = _adjacency(n, edges)
    remaining = [len(preds[v]) for v in range(n)]
    ready_at = [0] * n
    start = [-1] * n
    pending: list[tuple[int, int]] = []                  # (ready_day, node)
    ready: dict[str | None, list[tuple[float, int]]] = defaultdict(list)
    active: dict[str | None, list[tuple[int, float]]] = defaultdict(list)   # heap of (finish day, load)
    load_of = list(loads) if loads is not None else [1.0] * n
    cap_of = {r: max(1, int(capacity.get(r, default_capacity))) for r in set(resources) if r is not None}
    done = 0
    day = 0

    def place(v: int, s: int) -> None:
        """Fix node v at start day s and release its successors."""
        nonlocal done
        stack = [(v, s)]
        while stack:
            u, su = stack.pop()
            start[u] = su
            done += 1
            fu = su + durations[u]
            for e in succs[u]:
                w = e.succ
                if e.type == "FS":
                    c = fu + e.lag
                elif e.type == "SS":
                    c = su + e.lag
                else:
                    c = fu + e.lag - durations[w]
                ready_at[w] = max(ready_at[w], c)
                remaining[w] -= 1
                if remaining[w] == 0:
                    rd = max(ready_at[w], day)
                    if durations[w] == 0:
                        stack.append((w, rd))
                    else:
                        heapq.heappush(pending, (rd, w))

    for v in range(n):
        if remaining[v] == 0:
            if durations[v] == 0:
                place(v, 0)
            else:
                heapq.heappush(pending, (0, v))

    while done < n:
        if day > max_days:
            raise SchedulingError("levelling did not converge")
        progressed = True
        while progressed:
            progressed = False
            while pending and pending[0][0] <= day:
                _, v = heapq.heappop(pending)
                heapq.heappush(ready[resources[v]], (prio[v], v))
            for res in list(ready):
                heap = ready[res]
                if not heap:
                    continue
                act = active[res]
                while act and act[0][0] <= day:
                    heapq.heappop(act)
                limit = cap_of.get(res) if res is not None else None
                used = sum(ld for _, ld in act)
                while heap and (limit is None or used + load_of[heap[0][1]] <= limit + 1e-9):
                    _, v = heapq.heappop(heap)
                    heapq.heappush(act, (day + durations[v], load_of[v]))
                    used += load_of[v]
                    place(v, day)
                    progressed = True
        day += 1
    return start


# --------------------------------------------------------------------------- gates
@dataclass
class _GateInstance:
    gate: Gate
    key: str
    prereq: list[int] = field(default_factory=list)   # tasks of gate.after_phase: finish first
    held: list[int] = field(default_factory=list)     # tasks of gate.before_phase: wait
    enabled: bool = True


def _gate_instances(gates: Seq[Gate], tasks: Seq[Task], phase_order: Mapping[str, int]) -> list[_GateInstance]:
    """One instance per (gate, scope instance) with tasks on both sides (cumulative phase gates).

    ``prereq`` holds every task whose phase order is <= order(after_phase), ``held`` every task
    whose phase order is >= order(before_phase), both within the scope instance. An instance
    with no prerequisite tasks is vacuous and omitted.
    """
    out: list[_GateInstance] = []
    for g in gates:
        if g.after_phase not in phase_order or g.before_phase not in phase_order:
            continue
        lo, hi = phase_order[g.after_phase], phase_order[g.before_phase]
        if lo >= hi:
            continue
        if g.scope not in ("zone", "storey", "project"):
            raise SchedulingError(f"gate {g.id}: unknown scope {g.scope!r}")
        groups: dict[str, _GateInstance] = {}
        for i, t in enumerate(tasks):
            order = phase_order.get(t.phase)
            if order is None or lo < order < hi:
                continue
            key = {"zone": t.zone_id, "storey": t.storey_id, "project": "*"}[g.scope]
            inst = groups.setdefault(key, _GateInstance(g, key))
            (inst.prereq if order <= lo else inst.held).append(i)
        out.extend(inst for inst in groups.values() if inst.prereq and inst.held)
    return out


def _task_edges(tasks: Seq[Task]) -> list[Edge]:
    idx = {t.task_id: i for i, t in enumerate(tasks)}
    edges = []
    for i, t in enumerate(tasks):
        for p in t.predecessors:
            if p.task_id not in idx:
                raise SchedulingError(f"{t.task_id}: unknown predecessor {p.task_id}")
            edges.append(Edge(idx[p.task_id], i, p.type, p.lag_days))
    return edges


def _build_graph(tasks: Seq[Task], instances: Seq[_GateInstance]) -> tuple[list[Edge], int]:
    """All edges including synthetic gate milestones (nodes numbered after the tasks)."""
    edges = _task_edges(tasks)
    n = len(tasks)
    for inst in instances:
        if not inst.enabled:
            continue
        m = n
        n += 1
        edges.extend(Edge(p, m) for p in inst.prereq)
        edges.extend(Edge(m, s) for s in inst.held)
    return edges, n


def _resolve_gate_cycles(tasks: Seq[Task], instances: list[_GateInstance]) -> tuple[list[Edge], int, list[str]]:
    """Build the graph; disable gate instances that sit on a cycle (with a warning each)."""
    warnings: list[str] = []
    n_tasks = len(tasks)
    while True:
        edges, n = _build_graph(tasks, instances)
        _, succs = _adjacency(n, edges)
        comps = cyclic_components(n, [[e.succ for e in succs[v]] for v in range(n)])
        if not comps:
            return edges, n, warnings
        enabled = [inst for inst in instances if inst.enabled]
        milestone_of = {n_tasks + k: inst for k, inst in enumerate(enabled)}
        disabled = 0
        for comp in comps:
            for v in comp:
                inst = milestone_of.get(v)
                if inst is not None and inst.enabled:
                    inst.enabled = False
                    disabled += 1
                    warnings.append(
                        f"gate {inst.gate.id} ({inst.key}) disabled: it conflicts with task links (gate_cycle)")
        if not disabled:
            raise SchedulingError("task precedence graph has a cycle")


# --------------------------------------------------------------------------- scheduling
@dataclass
class ScheduleResult:
    starts: list[int]
    finishes: list[int]
    floats: list[int]
    warnings: list[str]
    gate_gaps: list[JSON] = field(default_factory=list)   # sequencing_gaps entries, note "gate_cycle"


def duration_days(task: Task, library: StepLibrary) -> int:
    """``max(min_duration_days, ceil(estimated_crew_days))`` for one crew."""
    step = library.steps[task.step_id]
    return max(step.min_duration_days, math.ceil(round(task.estimated_crew_days, 6) - 1e-9), 1)


def schedule_tasks(tasks: Seq[Task], library: StepLibrary, crews: Mapping[str, int],
                   fractional_crews: bool = False) -> ScheduleResult:
    """CPM + gates + levelling for a task list; returns dates in task order.

    With ``fractional_crews`` a task occupies ``estimated_crew_days / duration`` crews instead of
    a whole crew, so tasks shorter than a crew-day can share a crew (shorter baselines).
    """
    n_tasks = len(tasks)
    durs = [duration_days(t, library) for t in tasks]
    phase_order = {p.id: p.order for p in library.phases}
    instances = _gate_instances(library.gates, tasks, phase_order)
    edges, n, warnings = _resolve_gate_cycles(tasks, instances)
    all_durs = durs + [0] * (n - n_tasks)
    res = cpm(all_durs, edges, finish_nodes=range(n_tasks))
    resources: list[str | None] = [t.trade for t in tasks] + [None] * (n - n_tasks)
    loads = None
    if fractional_crews:
        loads = [min(1.0, max(t.estimated_crew_days, 0.01) / d) for t, d in zip(tasks, durs)] + [0.0] * (n - n_tasks)
    starts = level(all_durs, edges, resources, crews, priority=res.ls, loads=loads)[:n_tasks]
    finishes = [s + d for s, d in zip(starts, durs)]
    gaps: list[JSON] = []
    for inst in instances:
        if not inst.enabled:
            t = tasks[inst.held[0]]
            gaps.append({"task_id": t.task_id, "step": t.step_id, "scope": inst.gate.scope, "note": "gate_cycle"})
    return ScheduleResult(starts, finishes, res.total_float[:n_tasks], warnings, gaps)


def weekly_cumulative_cost(tasks: Seq[Task]) -> list[float]:
    """Cumulative planned cost at the end of each week 0..finish_week (accrued linearly)."""
    finish_day = max((t.planned_finish_day or 0 for t in tasks), default=0)
    weeks = math.ceil(finish_day / DAYS_PER_WEEK)
    daily = [0.0] * (weeks * DAYS_PER_WEEK + 1)
    for t in tasks:
        s, f = t.planned_start_day or 0, t.planned_finish_day or 0
        if f <= s:
            continue
        per_day = t.cost / (f - s)
        for d in range(s, f):
            daily[d] += per_day
    out, run = [], 0.0
    for w in range(weeks + 1):
        lo, hi = w * DAYS_PER_WEEK, (w + 1) * DAYS_PER_WEEK
        run += sum(daily[lo:hi])
        out.append(round(run, 2))
    return out


def _round_up(x: float) -> int:
    return int(math.ceil(round(x, 6)))


def _light_elements(doc: ElementsDoc, visuals: Mapping[str, str]) -> list[JSON]:
    grid = doc.project["grid"]
    cs, sh = float(grid["cell_size_m"]), float(grid["storey_height_m"])
    out = []
    for e in doc.elements:
        visual = visuals.get(e.guid) or e.visual or visual_for(
            e.ifc_class, e.properties, e.predefined_type, e.name)
        if visual not in VISUALS:
            visual = "generic"
        out.append({
            "guid": e.guid, "ifc_class": e.ifc_class, "name": e.name, "storey_id": e.storey_id,
            "zone_id": e.zone_id, "system_id": e.system_id,
            "cells": [[c[0], c[1]] for c in e.cells], "visual": visual,
            "size_hint": size_hint({"bbox": e.bbox, "cells": e.cells}, visual, cs, sh),
        })
    return out


def build_sequence(step_map: StepMap, library: StepLibrary, scenario: Scenario,
                   elements: ElementsDoc, *, generated_at: str | None = None,
                   generator: str | None = None,
                   fractional_crews: bool = False) -> tuple[JSON, list[str]]:
    """Schedule ``step_map`` and assemble the sequence.json bundle; returns (bundle, warnings).

    Gate instances disabled because they conflict with task links are also appended to
    ``step_map.sequencing_gaps`` with note ``gate_cycle`` (the map is the only mutated input).
    """
    tasks = [Task.from_dict(t.to_dict()) for t in step_map.tasks]   # do not mutate the input map
    if not tasks:
        raise SchedulingError("no tasks to schedule")
    res = schedule_tasks(tasks, library, scenario.crews_available, fractional_crews)
    for gap in res.gate_gaps:
        if gap not in step_map.sequencing_gaps:
            step_map.sequencing_gaps.append(gap)
    critical: list[str] = []
    for t, s, f, fl in zip(tasks, res.starts, res.finishes, res.floats):
        t.planned_start_day, t.planned_finish_day = s, f
        t.total_float_days = fl
        t.is_critical = fl == 0
        if fl == 0:
            critical.append(t.task_id)

    finish_day = max(t.planned_finish_day for t in tasks)          # type: ignore[type-var]
    finish_week = _round_up(finish_day / DAYS_PER_WEEK)
    total_cost = round(sum(t.cost for t in tasks), 2)
    baseline = {
        "finish_day": finish_day, "finish_week": finish_week, "total_cost": total_cost,
        "total_crew_days": round(sum(t.estimated_crew_days for t in tasks), 2),
        "critical_task_ids": critical, "weekly_planned_cost": weekly_cumulative_cost(tasks),
    }
    scn = scenario.to_dict()
    if scn.get("contract_weeks") is None:
        scn["contract_weeks"] = _round_up(finish_week * float(scn.get("contract_factor", 1.1)))
    if not scn.get("budget"):
        scn["budget"] = round(total_cost * float(scn.get("budget_factor", 1.15)))

    bundle: JSON = {
        "schema_version": "1.0",
        "project": copy.deepcopy(elements.project),
        "storeys": [s.to_dict() for s in elements.storeys],
        "zones": [z.to_dict() for z in elements.zones],
        "systems": [s.to_dict() for s in elements.systems],
        "elements": _light_elements(elements, step_map.element_visuals),
        "step_library": library.to_dict(),
        "tasks": [t.to_dict() for t in tasks],
        "baseline": baseline,
        "scenario": scn,
        "generated_at": generated_at or utc_now(),
        "generator": generator or GENERATOR,
    }
    return bundle, res.warnings
