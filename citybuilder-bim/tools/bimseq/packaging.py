"""Work packages (docs/05 sections 1.1 and 1.2): group scheduled tasks, split, derive crew profiles."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Mapping, Sequence as Seq

from .model import ElementsDoc, JSON, StepLibrary, Task

DEFAULT_ZONE_MAX_CREWS = 2


def _clamp(x: int, lo: int, hi: int) -> int:
    return min(max(x, lo), hi)


def crew_profile(tasks: Seq[Task], library: StepLibrary, total_crew_days: float, zone_max_crews: int) -> JSON:
    """Package crew profile: min = max of member mins; ideal/max from size unless steps give them.

    ``min`` is also capped at the zone's ``max_crews`` so a package can always be staffed.
    """
    pk = library.packaging
    cap = max(1, zone_max_crews)
    profiles = [library.steps[t.step_id].crew_profile for t in tasks]
    lo = min(max([p.min for p in profiles] + [1]), cap)
    explicit_ideal = max((p.ideal for p in profiles if p.ideal), default=None)
    explicit_max = max((p.max for p in profiles if p.max), default=None)
    ideal_raw = explicit_ideal if explicit_ideal is not None else math.ceil(
        round(total_crew_days, 6) / pk.target_duration_days - 1e-9)
    ideal = _clamp(ideal_raw, lo, cap)
    max_raw = explicit_max if explicit_max is not None else ideal + pk.max_over_ideal
    return {"min": lo, "ideal": ideal, "max": _clamp(max_raw, ideal, cap)}


def _split(tasks: list[Task], limit: float) -> list[list[Task]]:
    """Chunks in planned-start order, each at most ``limit`` crew-days (a chunk keeps >= 1 task)."""
    ordered = sorted(tasks, key=lambda t: (t.planned_start_day or 0, t.planned_finish_day or 0, t.task_id))
    chunks: list[list[Task]] = []
    cur: list[Task] = []
    total = 0.0
    for t in ordered:
        if cur and total + t.estimated_crew_days > limit:
            chunks.append(cur)
            cur, total = [], 0.0
        cur.append(t)
        total += t.estimated_crew_days
    if cur:
        chunks.append(cur)
    return chunks


def _most_common(values: Seq[str]) -> str:
    counts = Counter(values)
    return sorted(counts, key=lambda v: (-counts[v], v))[0]


def build_packages(tasks: Seq[Task], library: StepLibrary, doc: ElementsDoc) -> list[JSON]:
    """Create packages from *scheduled* tasks and set ``package_id`` on every task (in place)."""
    pk = library.packaging
    zones = doc.zone_by_id()
    storey_index = {s.id: s.index for s in doc.storeys}
    phase_order = {p.id: p.order for p in library.phases}
    phase_name = {p.id: p.name for p in library.phases}
    disc = {t.task_id: library.steps[t.step_id].discipline for t in tasks}

    field_of = {
        "zone_id": lambda t: t.zone_id, "phase": lambda t: t.phase, "trade": lambda t: t.trade,
        "work_face": lambda t: t.work_face or "any", "discipline": lambda t: disc[t.task_id],
    }
    groups: dict[tuple, list[Task]] = defaultdict(list)
    for t in tasks:
        groups[tuple(field_of[k](t) for k in pk.group_by)].append(t)

    raw: list[tuple[tuple, list[Task], int, int]] = []     # sort key, tasks, chunk no, chunk count
    for members in groups.values():
        chunks = _split(members, pk.max_crew_days_per_package)
        for n, chunk in enumerate(chunks, start=1):
            first = min(t.task_id for t in chunk)
            ref = chunk[0]
            key = (storey_index.get(ref.storey_id, 0), ref.zone_id, phase_order.get(ref.phase, 999),
                   ref.trade, ref.work_face or "any", first)
            raw.append((key, chunk, n, len(chunks)))
    raw.sort(key=lambda r: r[0])

    packages: list[JSON] = []
    for i, (_, chunk, n, count) in enumerate(raw, start=1):
        pid = f"P{i:05d}"
        zone_id = _most_common([t.zone_id for t in chunk])
        phase = _most_common([t.phase for t in chunk])
        trade = _most_common([t.trade for t in chunk])
        face = _most_common([t.work_face or "any" for t in chunk])
        zone = zones.get(zone_id)
        cap = zone.max_crews if zone else DEFAULT_ZONE_MAX_CREWS
        crew_days = sum(t.estimated_crew_days for t in chunk)
        name = f"{zone_id} · {phase_name.get(phase, phase)} · {trade} · {face.replace('_', ' ')}"
        if count > 1:
            name += f" ({n}/{count})"
        for t in sorted(chunk, key=lambda t: t.task_id):
            t.package_id = pid
        flags = [t.flags for t in chunk]
        packages.append({
            "package_id": pid, "name": name, "zone_id": zone_id,
            "storey_id": next(t.storey_id for t in chunk if t.zone_id == zone_id),
            "phase": phase, "trade": trade, "discipline": _most_common([disc[t.task_id] for t in chunk]),
            "work_face": face, "task_ids": sorted(t.task_id for t in chunk),
            "total_crew_days": round(crew_days, 3),
            "crew_profile": crew_profile(chunk, library, crew_days, cap),
            "planned_start_day": min(t.planned_start_day for t in chunk if t.planned_start_day is not None)
            if any(t.planned_start_day is not None for t in chunk) else None,
            "planned_finish_day": max(t.planned_finish_day for t in chunk if t.planned_finish_day is not None)
            if any(t.planned_finish_day is not None for t in chunk) else None,
            "requires_crane": any(f.get("requires_crane") for f in flags),
            "lead_time_weeks": max(int(f.get("lead_time_weeks", 0)) for f in flags),
            "laydown_cells": max(int(f.get("laydown_cells", 0)) for f in flags),
            "cost": round(sum(t.cost for t in chunk), 2),
        })
    return packages


def check_cards(library: StepLibrary, scenario_cards: Seq[Mapping[str, object]]) -> list[JSON]:
    """sequencing_gaps entries (note ``card_ref``) for stations selecting unknown phase/trade/discipline.

    Library cards are overridden by scenario cards with the same id, as the game merges them.
    """
    merged: dict[str, Mapping[str, object]] = {c["id"]: c for c in library.sequence_cards}
    for c in scenario_cards:
        merged[c["id"]] = c
    phases = {p.id for p in library.phases}
    trades = set(library.trades)
    disciplines = {s.discipline for s in library.steps.values()}
    gaps: list[JSON] = []
    for cid, card in merged.items():
        for st in card.get("stations", []):                       # type: ignore[union-attr]
            sel = st.get("select", {})
            bad = [k for k, known in (("phase", phases), ("trade", trades), ("discipline", disciplines))
                   if k in sel and sel[k] not in known]
            for k in bad:
                gaps.append({"task_id": "", "step": cid, "scope": f"station:{st.get('name', '?')}:{k}={sel[k]}",
                             "note": "card_ref"})
    return gaps
