"""Mapping rule engine: BIM elements -> tasks with resolved predecessor links.

Implements docs/02-sequencing-model.md sections 3.1 (predecessor scopes) and 4 (rules).
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from . import GENERATOR
from .graph import cyclic_components
from .model import (
    BASIS_UNIT, Cell, Element, ElementsDoc, MappingRules, Match, Predecessor, Rule, Step,
    StepLibrary, StepMap, Task,
)

MIN_FALLBACK_QUANTITY = 0.01


class MappingError(ValueError):
    """Rules and step library are inconsistent (e.g. a rule emits an unknown step)."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- matching
def _prop_equal(actual: Any, expected: Any) -> bool:
    """Exact equality that does not conflate booleans with numbers."""
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return actual == expected


def match_ok(m: Match, el: Element, storey_index: int, zone_tags: Iterable[str]) -> bool:
    """True when every key in ``m`` holds for the element (keys are AND-ed)."""
    if m.ifc_class is not None and el.ifc_class not in m.ifc_class:
        return False
    if m.predefined_type is not None and el.predefined_type not in m.predefined_type:
        return False
    if m.name_regex is not None and not m.name_regex.search(el.name or ""):
        return False
    if m.material_regex is not None:
        if el.material is None or not m.material_regex.search(el.material):
            return False
    if m.storey_index_min is not None and storey_index < m.storey_index_min:
        return False
    if m.storey_index_max is not None and storey_index > m.storey_index_max:
        return False
    if m.zone_tags_any is not None and not set(m.zone_tags_any) & set(zone_tags):
        return False
    if m.properties:
        for key, expected in m.properties.items():
            if not _prop_equal(el.properties.get(key), expected):
                return False
    if m.properties_present and any(k not in el.properties for k in m.properties_present):
        return False
    if m.quantity_min:
        for basis, minimum in m.quantity_min.items():
            if el.quantities.get(basis, 0.0) < minimum:
                return False
    return True


def rule_matches(rule: Rule, el: Element, storey_index: int, zone_tags: Iterable[str]) -> bool:
    """Rule-level match: ``system_prefix`` plus the ``match`` block."""
    if rule.system_prefix is not None:
        if el.system_id is None or not el.system_id.startswith(rule.system_prefix):
            return False
    return match_ok(rule.match, el, storey_index, zone_tags)


def check_rules(rules: MappingRules, library: StepLibrary) -> list[str]:
    """Consistency problems between rules and library (unknown steps, bad basis/unit)."""
    problems = []
    for rule in [*rules.rules, rules.default]:
        for emit in rule.steps:
            if emit.step not in library.steps:
                problems.append(f"rule {rule.id}: unknown step {emit.step}")
    for step in library.steps.values():
        for pr in step.predecessors:
            if pr.step not in library.steps:
                problems.append(f"step {step.id}: predecessor rule references unknown step {pr.step}")
        if step.trade not in library.trades:
            problems.append(f"step {step.id}: unknown trade {step.trade}")
    return problems


# --------------------------------------------------------------------------- spatial lookups
@dataclass
class _Link:
    pred: int
    succ: int
    type: str
    lag: int
    reason: str
    step: str = ""
    scope: str = ""
    chain: bool = False


class _Index:
    """Per-step lookup tables over tasks, one per predecessor scope."""

    def __init__(self) -> None:
        self.by_step: dict[str, list[int]] = defaultdict(list)
        self.by_elem: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_cell: dict[tuple[str, int, Cell], list[int]] = defaultdict(list)
        self.by_zone: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_storey: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_system: dict[tuple[str, str], list[int]] = defaultdict(list)

    def add(self, idx: int, step: str, el: Element, storey_index: int) -> None:
        self.by_step[step].append(idx)
        self.by_elem[(step, el.guid)].append(idx)
        for c in el.cells:
            self.by_cell[(step, storey_index, c)].append(idx)
        self.by_zone[(step, el.zone_id)].append(idx)
        self.by_storey[(step, el.storey_id)].append(idx)
        if el.system_id:
            self.by_system[(step, el.system_id)].append(idx)


def _resolve_scope(scope: str, step: str, el: Element, storey_index: int, ix: _Index,
                   storey_id_by_index: dict[int, str],
                   zones_at: dict[tuple[int, Cell], list[str]]) -> list[int]:
    """Task indices of ``step`` within ``scope`` of element ``el`` (unsorted, may repeat)."""
    if scope == "same_element":
        return ix.by_elem.get((step, el.guid), [])
    if scope == "host":
        return ix.by_elem.get((step, el.host_guid), []) if el.host_guid else []
    if scope in ("same_cell", "same_cell_below", "same_cell_above"):
        si = storey_index + {"same_cell": 0, "same_cell_below": -1, "same_cell_above": 1}[scope]
        out: set[int] = set()
        for c in el.cells:
            out.update(ix.by_cell.get((step, si, c), ()))
        return sorted(out)
    if scope == "same_zone":
        return ix.by_zone.get((step, el.zone_id), [])
    if scope == "same_zone_below":
        zones: set[str] = set()
        for c in el.cells:
            zones.update(zones_at.get((storey_index - 1, c), ()))
        out = set()
        for z in zones:
            out.update(ix.by_zone.get((step, z), ()))
        return sorted(out)
    if scope == "same_storey":
        return ix.by_storey.get((step, el.storey_id), [])
    if scope == "same_storey_below":
        sid = storey_id_by_index.get(storey_index - 1)
        return ix.by_storey.get((step, sid), []) if sid else []
    if scope == "same_system":
        return ix.by_system.get((step, el.system_id), []) if el.system_id else []
    if scope == "project":
        return ix.by_step.get(step, [])
    raise MappingError(f"unknown scope {scope!r}")


# --------------------------------------------------------------------------- main entry
def _unit(emit_unit: str | None, basis: str) -> str:
    return emit_unit or BASIS_UNIT[basis]


def map_elements(doc: ElementsDoc, library: StepLibrary, rules: MappingRules, *,
                 generated_at: str | None = None, step_library_ref: str | None = None,
                 mapping_rules_ref: str | None = None) -> StepMap:
    """Run the rule engine and predecessor resolution; returns an undated :class:`StepMap`."""
    problems = check_rules(rules, library)
    if problems:
        raise MappingError("; ".join(problems[:20]) + (" ..." if len(problems) > 20 else ""))

    storey_index = {s.id: s.index for s in doc.storeys}
    storey_id_by_index = {s.index: s.id for s in doc.storeys}
    zone_tags = {z.id: set(z.tags) for z in doc.zones}
    zones_at: dict[tuple[int, Cell], list[str]] = defaultdict(list)
    for z in doc.zones:
        zi = storey_index.get(z.storey_id)
        if zi is None:
            continue
        for c in z.cells:
            zones_at[(zi, c)].append(z.id)

    ordered_rules = rules.ordered()
    elements = sorted(doc.elements, key=lambda e: (storey_index[e.storey_id], e.zone_id, e.guid))

    tasks: list[Task] = []
    task_el: list[Element] = []
    task_si: list[int] = []
    links: list[_Link] = []
    unmapped: list[dict[str, str]] = []
    element_visuals: dict[str, str] = {}
    ix = _Index()

    def add_task(el: Element, si: int, rule: Rule, step: Step, emit_i: int) -> int:
        emit = rule.steps[emit_i]
        raw = el.quantities.get(emit.quantity, 0.0) * emit.quantity_factor
        if raw <= 0:
            q = max(emit.min_quantity, MIN_FALLBACK_QUANTITY)
            unmapped.append({"guid": el.guid, "ifc_class": el.ifc_class, "reason": "no_quantity"})
        else:
            q = max(raw, emit.min_quantity)
        crew_days = q / step.rate_per_crew_day
        idx = len(tasks)
        tasks.append(Task(
            task_id=f"T{idx + 1:06d}", element_guid=el.guid, ifc_class=el.ifc_class,
            element_name=el.name, storey_id=el.storey_id, zone_id=el.zone_id,
            system_id=el.system_id, step_id=step.id, phase=step.phase, trade=step.trade,
            quantity=round(q, 4), unit=_unit(emit.unit_override, emit.quantity),
            estimated_crew_days=round(crew_days, 4), cost=round(q * step.unit_cost, 2),
            cells=list(el.cells), flags=step.flags(), predecessors=[], rule_id=rule.id,
        ))
        task_el.append(el)
        task_si.append(si)
        ix.add(idx, step.id, el, si)
        return idx

    for el in elements:
        si = storey_index[el.storey_id]
        tags = zone_tags.get(el.zone_id, set())
        matched: list[Rule] = []
        for rule in ordered_rules:
            if rule_matches(rule, el, si, tags):
                matched.append(rule)
                if not rule.continue_:
                    break
        used_default = not matched
        if used_default:
            matched = [rules.default]
            unmapped.append({"guid": el.guid, "ifc_class": el.ifc_class, "reason": "default_rule"})
        for rule in matched:
            if rule.visual and el.guid not in element_visuals:
                element_visuals[el.guid] = rule.visual
            prev: int | None = None
            for i, emit in enumerate(rule.steps):
                idx = add_task(el, si, rule, library.steps[emit.step], i)
                if prev is not None and rule.chain:
                    links.append(_Link(prev, idx, "FS", emit.lag_days,
                                       f"chain:{tasks[prev].step_id}", chain=True))
                prev = idx

    unmapped = _dedupe_dicts(unmapped)
    gaps: list[dict[str, str]] = []

    # --- predecessor rules -> links
    for idx, task in enumerate(tasks):
        el, si = task_el[idx], task_si[idx]
        for pr in library.steps[task.step_id].predecessors:
            found = _resolve_scope(pr.scope, pr.step, el, si, ix, storey_id_by_index, zones_at)
            cands = [c for c in found if c != idx]
            if not cands:
                if pr.required:
                    gaps.append({"task_id": task.task_id, "step": pr.step, "scope": pr.scope,
                                 "note": "required predecessor not found"})
                continue
            for c in cands:
                links.append(_Link(c, idx, pr.type, pr.lag_days, f"rule:{pr.scope}:{pr.step}",
                                   step=pr.step, scope=pr.scope))

    links = _merge_links(links)
    links, dropped = _drop_cycles(len(tasks), links)
    for lk in dropped:
        gaps.append({"task_id": tasks[lk.succ].task_id, "step": lk.step or tasks[lk.pred].step_id,
                     "scope": lk.scope, "note": "cycle"})

    for lk in sorted(links, key=lambda k: (k.succ, k.pred)):
        tasks[lk.succ].predecessors.append(
            Predecessor(tasks[lk.pred].task_id, lk.type, lk.lag, lk.reason))

    return StepMap(
        project=dict(doc.project), sector=rules.sector, generated_at=generated_at or utc_now(),
        tasks=tasks, generator=GENERATOR, step_library_ref=step_library_ref or rules.step_library,
        mapping_rules_ref=mapping_rules_ref, unmapped_elements=unmapped,
        sequencing_gaps=_dedupe_dicts(gaps), element_visuals=element_visuals,
    )


def _dedupe_dicts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple] = set()
    out = []
    for d in items:
        key = tuple(sorted(d.items()))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _merge_links(links: list[_Link]) -> list[_Link]:
    """Drop self links and duplicates; for the same (pred, succ, type) keep the larger lag."""
    best: dict[tuple[int, int, str], _Link] = {}
    for lk in links:
        if lk.pred == lk.succ:
            continue
        key = (lk.pred, lk.succ, lk.type)
        cur = best.get(key)
        if cur is None:
            best[key] = lk
        elif lk.lag > cur.lag:
            lk.reason = lk.reason if not cur.chain else cur.reason
            lk.chain = cur.chain
            best[key] = lk
    return list(best.values())


def _drop_cycles(n: int, links: list[_Link]) -> tuple[list[_Link], list[_Link]]:
    """Remove links that close a cycle.

    Within every cyclic strongly connected component the non-chain links that run against
    task-id order are dropped.  Chain links always run forward in id order, so the remaining
    links inside the component all increase task id and cannot form a cycle.
    """
    succ: list[list[int]] = [[] for _ in range(n)]
    for lk in links:
        succ[lk.pred].append(lk.succ)
    comp_of: dict[int, int] = {}
    for ci, comp in enumerate(cyclic_components(n, succ)):
        for v in comp:
            comp_of[v] = ci
    if not comp_of:
        return links, []
    keep, dropped = [], []
    for lk in links:
        same = lk.pred in comp_of and comp_of.get(lk.succ) == comp_of[lk.pred]
        if same and not lk.chain and lk.pred > lk.succ:
            dropped.append(lk)
        else:
            keep.append(lk)
    return keep, dropped
