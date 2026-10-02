"""Mapping rule engine: BIM elements -> tasks with resolved predecessor links.

Implements docs/02-sequencing-model.md sections 3.1 (predecessor scopes) and 4 (rules).
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass
import copy
import re
from typing import Any, Iterable, Mapping

from . import GENERATOR
from .graph import cyclic_components
from .logic import ElementIndex, recipe_matches, step_ref
from .model import (
    BASIS_UNIT, Cell, Element, ElementsDoc, JSON, MappingRules, Match, Predecessor, Rule, Step,
    StepLibrary, StepMap, Task, step_from_dict,
)

MIN_FALLBACK_QUANTITY = 0.01
STEP_ID_RE = re.compile(r"^[A-Z]{2,5}(-[A-Z0-9]{2,12}){1,3}$")
VIRTUAL_CLASS = "Virtual"


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


@dataclass
class _Subject:
    """What a task is bound to, for predecessor-scope resolution."""

    guids: list[str]
    storey_id: str
    storey_index: int
    zone_id: str
    cells: list[Cell]
    system_id: str | None = None
    host_guid: str | None = None

    @property
    def guid(self) -> str | None:
        return self.guids[0] if self.guids else None


def _subject_of(el: Element, si: int) -> _Subject:
    return _Subject([el.guid], el.storey_id, si, el.zone_id, list(el.cells), el.system_id, el.host_guid)


class _Index:
    """Per-step lookup tables over tasks, one per predecessor scope."""

    def __init__(self) -> None:
        self.by_step: dict[str, list[int]] = defaultdict(list)
        self.by_elem: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_cell: dict[tuple[str, int, Cell], list[int]] = defaultdict(list)
        self.by_zone: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_storey: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_system: dict[tuple[str, str], list[int]] = defaultdict(list)

    def add(self, idx: int, step: str, sub: _Subject) -> None:
        self.by_step[step].append(idx)
        for g in sub.guids:
            self.by_elem[(step, g)].append(idx)
        for c in sub.cells:
            self.by_cell[(step, sub.storey_index, c)].append(idx)
        self.by_zone[(step, sub.zone_id)].append(idx)
        self.by_storey[(step, sub.storey_id)].append(idx)
        if sub.system_id:
            self.by_system[(step, sub.system_id)].append(idx)


def _resolve_scope(scope: str, step: str, sub: _Subject, ix: _Index,
                   storey_id_by_index: dict[int, str],
                   zones_at: dict[tuple[int, Cell], list[str]]) -> list[int]:
    """Task indices of ``step`` within ``scope`` of a task subject (unsorted, may repeat)."""
    if scope == "same_element":
        out: set[int] = set()
        for g in sub.guids:
            out.update(ix.by_elem.get((step, g), ()))
        return sorted(out)
    if scope == "host":
        return ix.by_elem.get((step, sub.host_guid), []) if sub.host_guid else []
    if scope in ("same_cell", "same_cell_below", "same_cell_above"):
        si = sub.storey_index + {"same_cell": 0, "same_cell_below": -1, "same_cell_above": 1}[scope]
        out = set()
        for c in sub.cells:
            out.update(ix.by_cell.get((step, si, c), ()))
        return sorted(out)
    if scope == "same_zone":
        return ix.by_zone.get((step, sub.zone_id), [])
    if scope == "same_zone_below":
        zones: set[str] = set()
        for c in sub.cells:
            zones.update(zones_at.get((sub.storey_index - 1, c), ()))
        out = set()
        for z in zones:
            out.update(ix.by_zone.get((step, z), ()))
        return sorted(out)
    if scope == "same_storey":
        return ix.by_storey.get((step, sub.storey_id), [])
    if scope == "same_storey_below":
        sid = storey_id_by_index.get(sub.storey_index - 1)
        return ix.by_storey.get((step, sid), []) if sid else []
    if scope == "same_system":
        return ix.by_system.get((step, sub.system_id), []) if sub.system_id else []
    if scope == "project":
        return ix.by_step.get(step, [])
    raise MappingError(f"unknown scope {scope!r}")


# --------------------------------------------------------------------------- main entry
def _unit(emit_unit: str | None, basis: str) -> str:
    return emit_unit or BASIS_UNIT[basis]


def map_elements(doc: ElementsDoc, library: StepLibrary, rules: MappingRules, *,
                 generated_at: str | None = None, step_library_ref: str | None = None,
                 mapping_rules_ref: str | None = None, recipes: Mapping[str, JSON] | None = None,
                 manual: Mapping[str, Any] | None = None) -> StepMap:
    """Run the rule engine and predecessor resolution; returns an undated :class:`StepMap`.

    ``recipes`` (id -> recipe) enable rules that emit a recipe and manual ``applied_recipes``;
    ``manual`` is a parsed manual_sequence.json. Steps defined inline by recipes are registered into
    ``library`` (and listed in ``StepMap.inline_steps``), so the same library object must be passed
    on to the scheduler.
    """
    problems = check_rules(rules, library)
    if problems:
        raise MappingError("; ".join(problems[:20]) + (" ..." if len(problems) > 20 else ""))
    return _Engine(doc, library, rules, recipes or {}, manual).run(
        generated_at, step_library_ref, mapping_rules_ref)


class _Engine:
    """One mapping run: tasks, links and the recipe / manual machinery."""

    def __init__(self, doc: ElementsDoc, library: StepLibrary, rules: MappingRules,
                 recipes: Mapping[str, JSON], manual: Mapping[str, Any] | None) -> None:
        self.doc, self.library, self.rules, self.recipes = doc, library, rules, recipes
        self.manual = manual or {}
        self.sector = doc.project.get("sector")
        self.storey_index = {s.id: s.index for s in doc.storeys}
        self.storey_id_by_index = {s.index: s.id for s in doc.storeys}
        self.zones = doc.zone_by_id()
        self.zone_tags = {z.id: set(z.tags) for z in doc.zones}
        self.zones_at: dict[tuple[int, Cell], list[str]] = defaultdict(list)
        for z in doc.zones:
            zi = self.storey_index.get(z.storey_id)
            if zi is not None:
                for c in z.cells:
                    self.zones_at[(zi, c)].append(z.id)
        self.eindex = ElementIndex(doc)
        self.manual_zones = set(self.manual.get("zones_in_manual_mode", []))
        self.suppress_all: set[str] = set()
        self.suppress: set[tuple[str, str]] = set()
        for ov in self.manual.get("overrides", []):
            if ov.get("suppress_all"):
                self.suppress_all.add(ov["element_guid"])
            for st in ov.get("suppress_steps", []):
                self.suppress.add((ov["element_guid"], st))
        # task state (internal indices; ids are assigned at the end)
        self.tasks: list[Task] = []
        self.subjects: list[_Subject] = []
        self.sortkeys: list[tuple] = []
        self.links: list[_Link] = []
        self.ix = _Index()
        self.by_elem_step: dict[tuple[str, str], int] = {}
        self.unmapped: list[dict[str, str]] = []
        self.gaps: list[tuple[int | None, str, str, str]] = []     # (task idx, step, scope, note)
        self.element_visuals: dict[str, str] = {}
        self.element_kits: dict[str, str] = {}
        self.inline_steps: list[JSON] = []
        self.seq = 0
        self.manual_idx: dict[str, int] = {}
        self.pending_after: list[tuple[int, list[str], str, int]] = []

    # ------------------------------------------------------------------ task creation
    def _register(self, task: Task, sub: _Subject, owner: str, step_id: str) -> int:
        idx = len(self.tasks)
        self.tasks.append(task)
        self.subjects.append(sub)
        self.sortkeys.append((sub.storey_index, sub.zone_id, owner, self.seq))
        self.seq += 1
        self.ix.add(idx, step_id, sub)
        for g in sub.guids:
            self.by_elem_step.setdefault((g, step_id), idx)
        return idx

    def _blank(self, step: Step, rule_id: str, **kw: Any) -> Task:
        return Task(task_id="", element_guid=None, ifc_class=VIRTUAL_CLASS, element_name="", storey_id="",
                    zone_id="", system_id=None, step_id=step.id, phase=step.phase, trade=step.trade,
                    quantity=0.0, unit="ea", estimated_crew_days=0.0, cost=0.0, cells=[],
                    flags=step.flags(), predecessors=[], rule_id=rule_id, work_face=step.work_face, **kw)

    def _quantity(self, raw: float, minimum: float, el_guid: str | None, ifc_class: str) -> float:
        if raw <= 0:
            if el_guid is not None:
                self.unmapped.append({"guid": el_guid, "ifc_class": ifc_class, "reason": "no_quantity"})
            return max(minimum, MIN_FALLBACK_QUANTITY)
        return max(raw, minimum)

    def _element_task(self, el: Element, si: int, rule_id: str, step: Step, basis: str, factor: float,
                      unit: str | None, minimum: float, *, origin: str | None = None,
                      recipe_id: str | None = None, fixed_value: float | None = None,
                      duration_days: int | None = None) -> int:
        raw = fixed_value if fixed_value is not None else el.quantities.get(basis, 0.0) * factor
        q = self._quantity(raw, minimum, el.guid, el.ifc_class)
        t = self._blank(step, rule_id, origin=origin, recipe_id=recipe_id, duration_days=duration_days)
        t.element_guid, t.ifc_class, t.element_name = el.guid, el.ifc_class, el.name
        t.storey_id, t.zone_id, t.system_id, t.cells = el.storey_id, el.zone_id, el.system_id, list(el.cells)
        t.quantity, t.unit = round(q, 4), _unit(unit, basis)
        t.estimated_crew_days = round(q / step.rate_per_crew_day, 4)
        t.cost = round(q * step.unit_cost, 2)
        return self._register(t, _subject_of(el, si), el.guid, step.id)

    def _virtual_task(self, step: Step, rule_id: str, zone_id: str, owner: str, name: str, *,
                      origin: str, recipe_id: str | None = None, quantity: float | None = None,
                      basis: str | None = None, factor: float = 1.0, duration_days: int | None = None,
                      marker: str | None = None, manual_id: str | None = None,
                      unit: str | None = None) -> int:
        zone = self.zones[zone_id]
        basis = basis or ("count" if quantity is None else step.quantity_basis)
        q = (quantity if quantity is not None else 1.0) * factor
        q = max(q, MIN_FALLBACK_QUANTITY) if q <= 0 else q
        t = self._blank(step, rule_id, virtual=True, origin=origin, recipe_id=recipe_id,
                        duration_days=duration_days, marker=marker, manual_id=manual_id)
        t.element_name, t.storey_id, t.zone_id, t.cells = name, zone.storey_id, zone_id, list(zone.cells)
        t.quantity, t.unit = round(q, 4), _unit(unit, basis)
        t.estimated_crew_days = round(float(duration_days) if duration_days else q / step.rate_per_crew_day, 4)
        t.cost = round(q * step.unit_cost, 2)
        sub = _Subject([], zone.storey_id, self.storey_index[zone.storey_id], zone_id, list(zone.cells))
        return self._register(t, sub, owner, step.id)

    def _link(self, pred: int, succ: int, type_: str = "FS", lag: int = 0, reason: str = "") -> None:
        if pred != succ:
            self.links.append(_Link(pred, succ, type_, lag, reason))

    def _gap(self, idx: int | None, step: str, scope: str, note: str) -> None:
        self.gaps.append((idx, step, scope, note))

    # ------------------------------------------------------------------ main run
    def run(self, generated_at: str | None, step_library_ref: str | None,
            mapping_rules_ref: str | None) -> StepMap:
        doc, rules = self.doc, self.rules
        self._manual_tasks()
        ordered_rules = rules.ordered()
        elements = sorted(doc.elements, key=lambda e: (self.storey_index[e.storey_id], e.zone_id, e.guid))
        pending: list[tuple[Element, Rule]] = []
        for el in elements:
            si = self.storey_index[el.storey_id]
            tags = self.zone_tags.get(el.zone_id, set())
            matched: list[Rule] = []
            for rule in ordered_rules:
                if rule_matches(rule, el, si, tags):
                    matched.append(rule)
                    if not rule.continue_:
                        break
            used_default = not matched
            if used_default:
                matched = [rules.default]
            skip = el.zone_id in self.manual_zones or el.guid in self.suppress_all
            if used_default and not skip:
                self.unmapped.append({"guid": el.guid, "ifc_class": el.ifc_class, "reason": "default_rule"})
            for rule in matched:
                if rule.visual and el.guid not in self.element_visuals:
                    self.element_visuals[el.guid] = rule.visual
                if rule.visual_kit and el.guid not in self.element_kits:
                    self.element_kits[el.guid] = rule.visual_kit
                if skip:
                    continue
                self._emit_rule(el, si, rule)
                if rule.recipe:
                    pending.append((el, rule))
        for el, rule in pending:
            if rule.recipe not in self.recipes:
                self._gap(None, rule.recipe, f"rule:{rule.id}", "recipe_ref")
                continue
            self._expand(rule.recipe, el, el.zone_id, rule.id, False, (), False)
        self._applied_recipes()
        self._resolve_predecessors()
        return self._finish(generated_at, step_library_ref, mapping_rules_ref)

    def _emit_rule(self, el: Element, si: int, rule: Rule) -> None:
        prev: int | None = None
        for emit in rule.steps:
            if (el.guid, emit.step) in self.suppress:
                continue
            existing = self.by_elem_step.get((el.guid, emit.step))
            if existing is not None and self.tasks[existing].origin in ("manual", "recipe"):
                idx = existing                      # manual / recipe task already covers this element+step
            else:
                idx = self._element_task(el, si, rule.id, self.library.steps[emit.step], emit.quantity,
                                         emit.quantity_factor, emit.unit_override, emit.min_quantity)
            if prev is not None and rule.chain:
                self._link(prev, idx, "FS", emit.lag_days, f"chain:{self.tasks[prev].step_id}")
            prev = idx

    # ------------------------------------------------------------------ recipes
    def _resolve_step(self, entry: Mapping[str, Any], rid: str) -> Step | None:
        if "step" in entry:
            sd = dict(entry["step"])
            sid = sd.get("id", "")
            if sid in self.library.steps:
                return self.library.steps[sid]
            sd.setdefault("name", sid)
            if not STEP_ID_RE.match(str(sid)):
                self._gap(None, str(sid), f"recipe:{rid}", "recipe_ref")
                return None
            try:
                step = step_from_dict(sd)
            except KeyError:
                self._gap(None, sid, f"recipe:{rid}", "recipe_ref")
                return None
            if step.trade not in self.library.trades or step.phase not in {p.id for p in self.library.phases}:
                self._gap(None, sid, f"recipe:{rid}", "recipe_ref")
                return None
            self.library.register_step(sd)
            self.inline_steps.append(copy.deepcopy(sd))
            return self.library.steps[sid]
        step = self.library.steps.get(entry["ref"])
        if step is None:
            self._gap(None, entry["ref"], f"recipe:{rid}", "recipe_ref")
        return step

    def _expand(self, rid: str, el: Element | None, zone_id: str, rule_id: str, include_optional: bool,
                stack: tuple[str, ...], force: bool) -> tuple[list[int], list[int], list[int]]:
        """Create the tasks of a recipe for one anchor; returns (heads, tails, all task indices)."""
        recipe = self.recipes[rid]
        groups: dict[str, list[int]] = {}
        prev_tails: list[int] = []
        heads: list[int] | None = None
        all_idx: list[int] = []
        for entry in recipe["steps"]:
            if entry.get("optional") and not include_optional:
                continue
            lag = int(entry.get("lag_days", 0))
            if "recipe" in entry:
                nested = entry["recipe"]
                if nested in stack or nested == rid or nested not in self.recipes:
                    self._gap(None, nested, f"recipe:{rid}", "recipe_cycle" if nested in stack or nested == rid else "recipe_ref")
                    continue
                h, t, a = self._expand(nested, el, zone_id, rule_id, include_optional, stack + (rid,), force)
                if not a:
                    continue
                for p in prev_tails:
                    for i in h:
                        self._link(p, i, "FS", lag, f"recipe:{rid}:{nested}")
                heads = heads if heads is not None else h
                prev_tails = t
                groups[entry.get("key") or nested] = a
                all_idx.extend(a)
                continue
            step = self._resolve_step(entry, rid)
            if step is None:
                continue
            idxs = self._bind(entry, step, recipe, el, zone_id, rule_id, force)
            if not idxs:
                continue
            key = entry.get("key") or step.id
            reason = f"recipe:{rid}:{key}"
            pw = entry.get("parallel_with")
            if pw and pw in groups:
                for p in groups[pw]:
                    for i in idxs:
                        self._link(p, i, "SS", lag, reason)
                # a start-together side branch: not a chain member, the next step follows the one before it
            else:
                for p in prev_tails:
                    for i in idxs:
                        self._link(p, i, "FS", lag, reason)
                prev_tails = idxs
            if heads is None:
                heads = idxs
            groups[key] = idxs
            groups.setdefault(step.id, idxs)
            all_idx.extend(idxs)
            hold = entry.get("hold_point")
            if hold:
                for i in idxs:
                    self.tasks[i].flags = {**self.tasks[i].flags, "inspection": True, "inspection_type": hold}
        for lg in recipe.get("logic", []):
            a, b = groups.get(lg["after"]), groups.get(lg["before"])
            if a and b:
                for i in a:
                    for j in b:
                        self._link(i, j, lg.get("type", "FS"), int(lg.get("lag_days", 0)),
                                   f"logic:{rid}:{lg.get('reason') or lg['after'] + '>' + lg['before']}")
        return heads or [], prev_tails, all_idx

    def _bind(self, entry: Mapping[str, Any], step: Step, recipe: Mapping[str, Any], el: Element | None,
              zone_id: str, rule_id: str, force: bool) -> list[int]:
        """Task indices for a recipe step (new, or reused when element+step already has a task)."""
        rid = recipe["id"]
        qspec = entry.get("quantity") or {}
        duration = entry.get("duration_days")
        mode = entry.get("from_element", "self")
        marker = entry.get("marker") or recipe.get("virtual_visual")
        if entry.get("virtual") or mode == "zone":
            zone = self.zones[zone_id]
            owner = el.guid if el is not None else "~zone"
            label = el.name if el is not None else zone.name
            idx = self._virtual_task(
                step, rule_id, zone_id, owner, f"{step.name} · {label}", origin="recipe", recipe_id=rid,
                quantity=qspec.get("value"), basis=qspec.get("basis"), factor=float(qspec.get("factor", 1.0)),
                duration_days=duration, marker=marker)
            return [idx]
        if el is None:
            self._gap(None, step.id, f"recipe:{rid}", "recipe_unbound")
            return []
        out: list[int] = []
        basis = qspec.get("basis") or step.quantity_basis
        for target in self.eindex.targets(el, mode):
            if (target.guid, step.id) in self.suppress or target.guid in self.suppress_all:
                continue
            if target.zone_id in self.manual_zones and not force:
                continue
            existing = self.by_elem_step.get((target.guid, step.id))
            if existing is not None:
                out.append(existing)
                continue
            out.append(self._element_task(
                target, self.storey_index[target.storey_id], rule_id, step, basis, float(qspec.get("factor", 1.0)),
                None, 0.0, origin="recipe", recipe_id=rid, fixed_value=qspec.get("value"), duration_days=duration))
        return out

    def _applied_recipes(self) -> None:
        for ar in self.manual.get("applied_recipes", []):
            rid, zone_id = ar["recipe"], ar["zone_id"]
            if rid not in self.recipes or zone_id not in self.zones:
                self._gap(None, rid, f"manual:{zone_id}", "manual_ref")
                continue
            opt = bool(ar.get("include_optional", False))
            guid = ar.get("element_guid")
            if guid:
                el = self.eindex.by_guid.get(guid)
                anchors: list[Element | None] = [el] if el else []
                if el is None:
                    self._gap(None, rid, f"manual:{guid}", "manual_ref")
            else:
                zone = self.zones[zone_id]
                anchors = [e for e in sorted(self.doc.elements, key=lambda e: e.guid)
                           if e.zone_id == zone_id and recipe_matches(self.recipes[rid], e, zone.tags, self.sector)]
                anchors = anchors or [None]
            for el in anchors:
                self._expand(rid, el, zone_id, "manual", opt, (), True)

    # ------------------------------------------------------------------ manual tasks
    def _manual_tasks(self) -> None:
        for mt in self.manual.get("tasks", []):
            step = self.library.steps.get(mt["step"])
            zone = self.zones.get(mt["zone_id"])
            if step is None or zone is None:
                self._gap(None, mt["step"], f"manual:{mt['id']}", "manual_ref")
                continue
            els = []
            for g in mt.get("elements", []):
                e = self.eindex.by_guid.get(g)
                if e is None:
                    self._gap(None, mt["step"], f"manual:{mt['id']}:{g}", "manual_ref")
                else:
                    els.append(e)
            virtual = bool(mt.get("virtual")) or not els
            basis = step.quantity_basis
            if "quantity" in mt:
                q_raw = float(mt["quantity"])
            elif els:
                q_raw = sum(e.quantities.get(basis, 0.0) for e in els)
            else:
                q_raw, basis = 1.0, "count"
            guid0 = els[0].guid if els else None
            q = self._quantity(q_raw, 0.0, guid0, els[0].ifc_class if els else VIRTUAL_CLASS)
            t = self._blank(step, "manual", virtual=True if virtual else None, origin="manual",
                            duration_days=mt.get("duration_days"), marker=mt.get("marker"), manual_id=mt["id"],
                            recipe_id=mt.get("recipe_id"))
            cells = sorted({tuple(c) for c in mt["cells"]}) if mt.get("cells") else \
                sorted({c for e in els for c in e.cells}) or list(zone.cells)
            t.element_guid = guid0
            t.ifc_class = els[0].ifc_class if els else VIRTUAL_CLASS
            t.element_name = mt.get("name") or (
                (els[0].name + (f" +{len(els) - 1}" if len(els) > 1 else "")) if els else f"{step.name} · {zone.name}")
            t.storey_id, t.zone_id, t.cells = zone.storey_id, zone.id, [tuple(c) for c in cells]
            t.system_id = els[0].system_id if els else None
            t.quantity, t.unit = round(q, 4), _unit(mt.get("unit"), basis)
            dd = mt.get("duration_days")
            t.estimated_crew_days = round(float(dd) if (virtual and dd) else q / step.rate_per_crew_day, 4)
            t.cost = round(q * step.unit_cost, 2)
            sub = _Subject([e.guid for e in els], zone.storey_id, self.storey_index[zone.storey_id], zone.id,
                           list(t.cells), t.system_id, els[0].host_guid if els else None)
            idx = self._register(t, sub, guid0 or "~zone", step.id)
            for e in els:
                self.by_elem_step[(e.guid, step.id)] = idx
            self.manual_idx[mt["id"]] = idx
            if mt.get("after"):
                self.pending_after.append((idx, list(mt["after"]), mt.get("link_type", "FS"), int(mt.get("lag_days", 0))))

    # ------------------------------------------------------------------ predecessor rules
    def _resolve_predecessors(self) -> None:
        inherit = bool(self.manual.get("inherit_logic", True))
        for idx, task in enumerate(self.tasks):
            if task.origin == "manual" and not inherit:
                continue
            sub = self.subjects[idx]
            for pr in self.library.steps[task.step_id].predecessors:
                found = _resolve_scope(pr.scope, pr.step, sub, self.ix, self.storey_id_by_index, self.zones_at)
                cands = [c for c in found if c != idx]
                if not cands:
                    if pr.required:
                        self._gap(idx, pr.step, pr.scope, "required predecessor not found")
                    continue
                for c in cands:
                    self.links.append(_Link(c, idx, pr.type, pr.lag_days, f"rule:{pr.scope}:{pr.step}",
                                            step=pr.step, scope=pr.scope))

    # ------------------------------------------------------------------ finish
    def _finish(self, generated_at: str | None, step_library_ref: str | None,
                mapping_rules_ref: str | None) -> StepMap:
        n = len(self.tasks)
        order = sorted(range(n), key=lambda i: self.sortkeys[i])
        new_of = {old: new for new, old in enumerate(order)}
        tasks = [self.tasks[old] for old in order]
        for new, t in enumerate(tasks):
            t.task_id = f"T{new + 1:06d}"
        id_to_idx = {t.task_id: i for i, t in enumerate(tasks)}
        links = [_Link(new_of[lk.pred], new_of[lk.succ], lk.type, lk.lag, lk.reason, lk.step, lk.scope)
                 for lk in self.links]
        gaps: list[dict[str, str]] = []
        for idx, step, scope, note in self.gaps:
            gaps.append({"task_id": tasks[new_of[idx]].task_id if idx is not None else "", "step": step,
                         "scope": scope, "note": note})
        # manual 'after' references: M ids, or generated T ids (final numbering)
        for idx, refs, ltype, lag in self.pending_after:
            for ref in refs:
                src = new_of[self.manual_idx[ref]] if ref in self.manual_idx else id_to_idx.get(ref)
                if src is None:
                    gaps.append({"task_id": tasks[new_of[idx]].task_id, "step": ref, "scope": "manual:after",
                                 "note": "manual_ref"})
                else:
                    links.append(_Link(src, new_of[idx], ltype, lag, f"manual:{ref}"))
        links = _merge_links(links)
        links, dropped = _drop_cycles(n, links)
        for lk in dropped:
            gaps.append({"task_id": tasks[lk.succ].task_id, "step": lk.step or tasks[lk.pred].step_id,
                         "scope": lk.scope, "note": "cycle"})
        for lk in sorted(links, key=lambda k: (k.succ, k.pred)):
            tasks[lk.succ].predecessors.append(Predecessor(tasks[lk.pred].task_id, lk.type, lk.lag, lk.reason))
        return StepMap(
            project=dict(self.doc.project), sector=self.rules.sector, generated_at=generated_at or utc_now(),
            tasks=tasks, generator=GENERATOR, step_library_ref=step_library_ref or self.rules.step_library,
            mapping_rules_ref=mapping_rules_ref, unmapped_elements=_dedupe_dicts(self.unmapped),
            sequencing_gaps=_dedupe_dicts(gaps), element_visuals=self.element_visuals,
            element_visual_kits=self.element_kits, inline_steps=self.inline_steps)


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
            lk.reason = cur.reason
            best[key] = lk
    return list(best.values())


def _path_links(links: list[_Link], comp: set[int], src: int, dst: int, skip: _Link) -> list[_Link]:
    """Shortest link path src -> dst inside a component (BFS), ignoring link ``skip``."""
    out: dict[int, list[_Link]] = defaultdict(list)
    for lk in links:
        if lk is not skip and lk.pred in comp and lk.succ in comp:
            out[lk.pred].append(lk)
    prev: dict[int, _Link] = {}
    seen = {src}
    queue = [src]
    for v in queue:
        if v == dst:
            break
        for lk in out[v]:
            if lk.succ not in seen:
                seen.add(lk.succ)
                prev[lk.succ] = lk
                queue.append(lk.succ)
    path: list[_Link] = []
    v = dst
    while v != src and v in prev:
        path.append(prev[v])
        v = prev[v].pred
    return list(reversed(path))


def _drop_cycles(n: int, links: list[_Link]) -> tuple[list[_Link], list[_Link]]:
    """Remove links that close a cycle (indices are final task order); returns (kept, dropped).

    Library predecessor-rule links (reason ``rule:...``) give way first: rule links that run against
    task order inside a cyclic component are dropped. If a cycle remains it passes through a
    chain/recipe/manual link running backwards; one library link on a path back around that cycle is
    dropped (that link itself only if the cycle has none). Authored order therefore wins over
    generic library logic.
    """
    dropped: list[_Link] = []
    while True:
        succ: list[list[int]] = [[] for _ in range(n)]
        for lk in links:
            succ[lk.pred].append(lk.succ)
        comps = cyclic_components(n, succ)
        if not comps:
            return links, dropped
        comp_of = {v: ci for ci, comp in enumerate(comps) for v in comp}
        inside = [lk for lk in links if lk.pred in comp_of and comp_of.get(lk.succ) == comp_of[lk.pred]]
        back_rules = [lk for lk in inside if lk.pred > lk.succ and lk.reason.startswith("rule:")]
        if back_rules:
            victims = back_rules
        else:
            e = min((lk for lk in inside if lk.pred > lk.succ), key=lambda lk: (lk.succ, lk.pred))
            path = _path_links(links, set(comps[comp_of[e.pred]]), e.succ, e.pred, e)
            rules_on_path = [lk for lk in path if lk.reason.startswith("rule:")]
            victims = [rules_on_path[-1] if rules_on_path else e]
        dropped.extend(victims)
        gone = {id(v) for v in victims}
        links = [lk for lk in links if id(lk) not in gone]
