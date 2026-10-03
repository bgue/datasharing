"""Element aggregation (docs/06 B.5, C.2): fold dense cells into aggregate elements with member GUIDs."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence as Seq

from .model import Element, ElementsDoc, JSON, read_json

PRESETS_PATH = Path(__file__).with_name("aggregation_presets.json")
DEFAULT_THRESHOLD = 25
DEFAULT_MAX_MEMBERS = 200
AGG_PREFIX = "AGG-"


def load_presets(path: str | Path = PRESETS_PATH) -> dict[str, JSON]:
    return read_json(path)["presets"]


def resolve_preset(preset: str | Mapping[str, Any] | None) -> JSON:
    """Preset by name (from ``aggregation_presets.json``) or an inline dict."""
    if preset is None:
        preset = "generic"
    if isinstance(preset, Mapping):
        return {"threshold_per_cell": DEFAULT_THRESHOLD, "max_members": DEFAULT_MAX_MEMBERS,
                "group_by": ["storey", "cell", "kit_or_class", "system"], **preset}
    presets = load_presets()
    if preset not in presets:
        raise ValueError(f"unknown aggregation preset {preset!r}; known: {', '.join(sorted(presets))}")
    return presets[preset]


def _group_key(e: Element, group_by: Seq[str]) -> tuple:
    parts: dict[str, Any] = {
        "storey": e.storey_id, "cell": tuple(e.cells[0]), "kit_or_class": e.visual_kit or e.ifc_class,
        "system": e.system_id or "", "zone": e.zone_id,
    }
    return tuple(parts[k] for k in group_by)


def _most_common(values: Seq[Any]) -> Any:
    counts = Counter(values)
    return sorted(counts, key=lambda v: (-counts[v], str(v)))[0]


def _sample(cells: list, limit: int) -> list:
    if not limit or len(cells) <= limit:
        return cells
    step = (len(cells) - 1) / (limit - 1) if limit > 1 else 0
    return [cells[round(i * step)] for i in range(limit)]


def make_aggregate(members: Seq[Element], max_cells: int = 0) -> Element:
    """One aggregate element standing in for ``members`` (same storey and group key)."""
    members = sorted(members, key=lambda e: e.guid)
    guids = [m.guid for m in members]
    guid = AGG_PREFIX + hashlib.sha1("|".join(guids).encode()).hexdigest()[:10]
    first = members[0]
    cls = _most_common([m.ifc_class for m in members])
    cell = first.cells[0]
    quantities: dict[str, float] = defaultdict(float)
    for m in members:
        for k, v in m.quantities.items():
            quantities[k] += v
    props = {k: v for k, v in first.properties.items()
             if all(k in m.properties and m.properties[k] == v and isinstance(m.properties[k], bool) == isinstance(v, bool)
                    for m in members)}
    cells = _sample(sorted({tuple(c) for m in members for c in m.cells}), max_cells)
    bbox = None
    boxes = [m.bbox for m in members if m.bbox]
    if boxes:
        bbox = {"min": [min(b["min"][i] for b in boxes) for i in range(3)],
                "max": [max(b["max"][i] for b in boxes) for i in range(3)]}
    ptypes = {m.predefined_type for m in members}
    return Element(
        guid=guid, ifc_class=cls, name=f"{len(members)} x {cls} in [{cell[0]}, {cell[1]}]",
        storey_id=first.storey_id, zone_id=first.zone_id, cells=cells,
        quantities={k: round(v, 4) for k, v in quantities.items()},
        predefined_type=next(iter(ptypes)) if len(ptypes) == 1 else None, system_id=first.system_id,
        host_guid=first.host_guid, material=_most_common([m.material for m in members]),
        properties=props, bbox=bbox, visual=_most_common([m.visual for m in members]),
        visual_kit=first.visual_kit, member_guids=guids)


def aggregate(doc: ElementsDoc, preset: str | Mapping[str, Any] | None = "generic") -> ElementsDoc:
    """Return a new document in which dense groups are replaced by aggregate elements.

    A group (``group_by`` key) is folded when it holds more than ``threshold_per_cell`` elements, in
    chunks of at most ``max_members`` (sorted by GUID). ``host_guid`` references to folded members are
    re-pointed to their aggregate. The input is not modified.
    """
    cfg = resolve_preset(preset)
    if "threshold_per_cell" not in cfg and "max_members" in cfg and "legacy" not in cfg:
        cfg = {**cfg, "threshold_per_cell": cfg["max_members"], "max_members": DEFAULT_MAX_MEMBERS}   # old-style preset
    limit = int(cfg.get("threshold_per_cell", DEFAULT_THRESHOLD))
    cap = max(2, int(cfg.get("max_members", DEFAULT_MAX_MEMBERS)))
    max_cells = int(cfg.get("max_cells", 0))
    groups: dict[tuple, list[Element]] = defaultdict(list)
    for e in doc.elements:
        groups[_group_key(e, cfg["group_by"])].append(e)
    folded: dict[str, str] = {}
    aggregates: list[Element] = []
    for key in sorted(groups, key=str):
        members = sorted(groups[key], key=lambda e: e.guid)
        if len(members) > limit:
            n_chunks = -(-len(members) // cap)
            size = -(-len(members) // n_chunks)                        # balanced chunks of <= cap
            for lo in range(0, len(members), size):
                agg = make_aggregate(members[lo:lo + size], max_cells)
                aggregates.append(agg)
                for m in members[lo:lo + size]:
                    folded[m.guid] = agg.guid
    if not folded:
        return doc
    elements = [e for e in doc.elements if e.guid not in folded] + aggregates
    for e in elements:
        if e.host_guid in folded:
            e.host_guid = folded[e.host_guid]
    return ElementsDoc(doc.project, doc.storeys, doc.zones, doc.systems, elements)


def aggregate_map(doc: ElementsDoc) -> dict[str, list[str]]:
    """aggregate guid -> member guids for the aggregate elements of ``doc``."""
    return {e.guid: list(e.member_guids) for e in doc.elements if e.member_guids}


def apply_aggregates(doc: ElementsDoc, aggregates: Mapping[str, Seq[str]],
                     aggregate_elements: Seq[Mapping[str, Any]]) -> ElementsDoc:
    """Rebuild the aggregated document from the stored map (used when scheduling a mapped file)."""
    folded = {m: agg for agg, members in aggregates.items() for m in members}
    elements = [e for e in doc.elements if e.guid not in folded] + [Element.from_dict(d) for d in aggregate_elements]
    for e in elements:
        if e.host_guid in folded:
            e.host_guid = folded[e.host_guid]
    return ElementsDoc(doc.project, doc.storeys, doc.zones, doc.systems, elements)
