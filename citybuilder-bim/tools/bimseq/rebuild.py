"""Incremental zone rebuild: re-map one zone (e.g. with a manual sequence) and patch an existing bundle.

Task and package ids outside the zone stay stable; the zone's tasks and packages get fresh ids above the
existing maxima; predecessor links are recomputed in both directions (into and out of the zone); the
schedule is recomputed for the whole project (levelling is global).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from . import aggregation, logic
from .bundle import load_bundle
from .mapper import map_elements
from .model import (
    ElementsDoc, JSON, MappingRules, Predecessor, Scenario, StepLibrary, StepMap, Task, step_map_from_dict,
)
from .scheduler import build_sequence

ID_RE = re.compile(r"^[TP](\d+)$")


def _key(t: Task) -> tuple:
    return (t.element_guid, t.step_id, t.origin, t.recipe_id, t.manual_id,
            t.element_name if t.element_guid is None else None, t.zone_id if t.element_guid is None else None)


def _max_id(ids: list[str]) -> int:
    return max((int(ID_RE.match(i).group(1)) for i in ids if ID_RE.match(i)), default=0)       # type: ignore[union-attr]


def rebuild_zone(old_bundle: Mapping[str, Any], old_map: StepMap, zone_id: str, doc: ElementsDoc,
                 library: StepLibrary, rules: MappingRules, scenario: Scenario, *,
                 manual: Mapping[str, Any] | None = None, recipes: Mapping[str, JSON] | None = None,
                 fractional_crews: bool = False, max_fanin: int | None = None,
                 generated_at: str | None = None) -> tuple[JSON, StepMap, dict[str, int]]:
    """Return ``(new bundle, new step map, stats)`` for ``zone_id`` rebuilt with ``manual``."""
    if zone_id not in {z.id for z in doc.zones}:
        raise ValueError(f"unknown zone {zone_id!r}")
    if old_map.aggregates:
        doc = aggregation.apply_aggregates(doc, old_map.aggregates, old_map.aggregated_elements)
    for sd in old_map.inline_steps:
        library.register_step(sd)
    fresh = map_elements(doc, library, rules, generated_at=generated_at or old_map.generated_at,
                         recipes=recipes or {}, manual=manual, max_fanin=max_fanin)
    old_tasks = [Task.from_dict(t) for t in old_bundle["tasks"]]
    old_by_key: dict[tuple, str] = {}
    for t in old_tasks:
        if t.zone_id != zone_id:
            old_by_key.setdefault(_key(t), t.task_id)
    next_id = _max_id([t.task_id for t in old_tasks]) + 1
    new_id: dict[str, str] = {}
    kept = replaced = 0
    for t in fresh.tasks:
        stable = old_by_key.get(_key(t)) if t.zone_id != zone_id else None
        if stable is not None:
            new_id[t.task_id] = stable
            kept += 1
        else:
            new_id[t.task_id] = f"T{next_id:06d}"
            next_id += 1
            replaced += t.zone_id == zone_id
    tasks: list[Task] = []
    for t in fresh.tasks:
        t.predecessors = [Predecessor(new_id[p.task_id], p.type, p.lag_days, p.reason) for p in t.predecessors]
        t.task_id = new_id[t.task_id]
        tasks.append(t)
    tasks.sort(key=lambda t: t.task_id)
    fresh.tasks = tasks
    fresh.sequencing_gaps = [{**g, "task_id": new_id.get(g["task_id"], g["task_id"])} for g in fresh.sequencing_gaps]
    fresh.aggregates, fresh.aggregated_elements = old_map.aggregates, old_map.aggregated_elements
    bundle, _warnings = build_sequence(
        fresh, library, scenario, doc, generated_at=generated_at or old_bundle.get("generated_at"),
        generator=old_bundle.get("generator"), fractional_crews=fractional_crews,
        recipes=logic.applicable_recipes(recipes or {}, doc, {t.recipe_id for t in tasks if t.recipe_id}),
        manual=manual)
    # keep package ids stable outside the zone
    old_pkg_by_tasks: dict[frozenset, str] = {}
    for p in old_bundle.get("packages", []):
        if p["zone_id"] != zone_id:
            old_pkg_by_tasks[frozenset(p["task_ids"])] = p["package_id"]
    next_pkg = _max_id([p["package_id"] for p in old_bundle.get("packages", [])]) + 1
    remap: dict[str, str] = {}
    for p in bundle["packages"]:
        stable = old_pkg_by_tasks.get(frozenset(p["task_ids"]))
        if stable is None:
            stable = f"P{next_pkg:05d}"
            next_pkg += 1
        remap[p["package_id"]] = stable
    for p in bundle["packages"]:
        p["package_id"] = remap[p["package_id"]]
    bundle["packages"].sort(key=lambda p: p["package_id"])
    for t in bundle["tasks"]:
        t["package_id"] = remap[t["package_id"]]
    for t in fresh.tasks:
        t.package_id = remap.get(t.package_id or "", t.package_id)
    stats = {"tasks_kept": kept, "zone_tasks": sum(1 for t in tasks if t.zone_id == zone_id),
             "zone_tasks_before": sum(1 for t in old_tasks if t.zone_id == zone_id), "tasks": len(tasks)}
    return bundle, fresh, stats
