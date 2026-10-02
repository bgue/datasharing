"""Manual sequences (docs/06 A.3): loading, starter templates and the healthcare demo chain."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence as Seq

from .model import ElementsDoc, JSON, MappingRules, StepLibrary, read_json
from .validate import schema_errors


def load_manual(path: str | Path) -> JSON:
    """Read and schema-validate a manual_sequence.json; raises ``ValueError`` when invalid."""
    data = read_json(path)
    msgs = schema_errors("manual_sequence", data)
    if msgs:
        raise ValueError(f"{path}: invalid manual sequence: {msgs[0]}")
    return data


def _first_existing(library: StepLibrary, ids: Seq[str]) -> str | None:
    return next((i for i in ids if i in library.steps), None)


def template(doc: ElementsDoc, library: StepLibrary, zone_id: str, rules: MappingRules | None = None,
             recipes: Mapping[str, JSON] | None = None) -> JSON:
    """Starter manual_sequence for one zone: its elements bound to a suggested step chain.

    With ``rules`` the chain is what the mapper would generate for the zone (one manual task per
    step, ordered by phase, elements bound); without rules it lists one placeholder per library
    phase (first step of the phase, no elements; edit before use).
    """
    zone = doc.zone_by_id().get(zone_id)
    if zone is None:
        raise ValueError(f"unknown zone {zone_id!r}")
    phase_order = {p.id: p.order for p in library.phases}
    tasks: list[JSON] = []
    if rules is not None:
        from .mapper import map_elements       # local import: mapper imports logic only
        sm = map_elements(doc, library, rules, generated_at="template", recipes=recipes)
        by_step: dict[str, list] = {}
        for t in sm.tasks:
            if t.zone_id == zone_id and not t.virtual:
                by_step.setdefault(t.step_id, []).append(t)
        ordered = sorted(by_step, key=lambda s: (phase_order.get(library.steps[s].phase, 999), by_step[s][0].task_id))
        for i, sid in enumerate(ordered, start=1):
            guids = sorted({t.element_guid for t in by_step[sid] if t.element_guid})
            tasks.append({"id": f"M{i:04d}", "step": sid, "zone_id": zone_id, "elements": guids,
                          "note": f"{library.steps[sid].name} ({len(guids)} elements)"})
    else:
        for i, ph in enumerate(sorted(library.phases, key=lambda p: p.order), start=1):
            step = next((s for s in library.steps.values() if s.phase == ph.id), None)
            if step is not None:
                tasks.append({"id": f"M{len(tasks) + 1:04d}", "step": step.id, "zone_id": zone_id,
                              "note": f"TODO bind elements: {ph.name} / {step.name}"})
    for prev, cur in zip(tasks, tasks[1:]):
        cur["after"] = [prev["id"]]
    return {"schema_version": "1.0", "project": doc.project.get("name", ""),
            "author": "bimseq manual template", "zones_in_manual_mode": [zone_id],
            "inherit_logic": True, "tasks": tasks, "overrides": []}


def demo(doc: ElementsDoc, library: StepLibrary, zone_id: str | None = None) -> JSON:
    """Manual chain excavate -> pile -> survey -> form -> slab for one ground-floor zone.

    The zone defaults to the first untagged ground-storey zone that holds footings and a slab. Survey
    steps are virtual; GEN-SURVEY-* steps are used when the library has them, else GEN-SETOUT.
    """
    ground = min(doc.storeys, key=lambda s: abs(s.index)).id
    footings: dict[str, list] = {}
    slabs: dict[str, list] = {}
    for e in doc.elements:
        if e.storey_id != ground:
            continue
        if e.ifc_class == "IfcFooting":
            footings.setdefault(e.zone_id, []).append(e)
        elif e.ifc_class == "IfcSlab":
            slabs.setdefault(e.zone_id, []).append(e)
    if zone_id is None:
        tags = {z.id: z.tags for z in doc.zones}
        cands = [z.id for z in doc.zones if z.id in footings and z.id in slabs and not tags[z.id]]
        zone_id = cands[0] if cands else next(iter(footings))
    foot = sorted(e.guid for e in footings.get(zone_id, []))
    slab = sorted(e.guid for e in slabs.get(zone_id, []))
    survey = _first_existing(library, ["GEN-SURVEY-SETOUT", "GEN-SETOUT"])
    asbuilt = _first_existing(library, ["GEN-SURVEY-ASBUILT", "GEN-SETOUT"])
    form = _first_existing(library, ["STR-SLAB-FORM", "STR-GSLAB-FORM"])
    pour = _first_existing(library, ["STR-SLAB-POUR", "STR-GSLAB-POUR"])
    chain = [
        (survey, [], True, 2, "Survey set-out before digging"),
        (_first_existing(library, ["CIV-EARTH-CUT"]), foot, False, None, "Excavate to formation"),
        (_first_existing(library, ["CIV-PILE-DRIVE"]), foot, False, None, "Drive piles"),
        (asbuilt, [], True, 1, "As-built survey of piles"),
        (form, slab, False, None, "Form slab"),
        (pour, slab, False, None, "Pour slab"),
    ]
    tasks: list[JSON] = []
    for step, guids, virtual, days, note in chain:
        if step is None:
            continue
        t: JSON = {"id": f"M{len(tasks) + 1:04d}", "step": step, "zone_id": zone_id, "note": note}
        if virtual:
            t["virtual"] = True
            t["duration_days"] = days
            t["marker"] = "survey"
        else:
            t["elements"] = guids
        if tasks:
            t["after"] = [tasks[-1]["id"]]
        tasks.append(t)
    return {"schema_version": "1.0", "project": doc.project.get("name", ""),
            "author": "bimseq build-samples --manual-demo", "zones_in_manual_mode": [zone_id],
            "inherit_logic": True, "tasks": tasks, "overrides": []}
