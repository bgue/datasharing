"""Zones from IfcSpace / IfcZone records with space-tag rules, small-space merging and auto-block fallback."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence as Seq

from .model import Cell, JSON, read_json
from .validate import schema_errors


@dataclass
class SpaceRec:
    """One IfcSpace (or IfcZone member) reduced to what zoning needs."""

    guid: str
    name: str
    storey_id: str
    cells: list[Cell]
    long_name: str = ""
    object_type: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    group: str | None = None            # IfcZone name when the space belongs to one


def load_space_tag_rules(path: str | Path) -> list[JSON]:
    """Read and validate a space_tags file; returns its rules in file order."""
    data = read_json(path)
    msgs = schema_errors("space_tags", data)
    if msgs:
        raise ValueError(f"{path}: invalid space tags: {msgs[0]}")
    return data["rules"]


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text or "") is not None


def match_rule(rules: Seq[Mapping[str, Any]], rec: SpaceRec) -> Mapping[str, Any] | None:
    """First rule (file order) whose match block fits the space; None if no rule matches."""
    for rule in rules:
        m = rule["match"]
        if "name_regex" in m and not _search(m["name_regex"], rec.name):
            continue
        if "long_name_regex" in m and not _search(m["long_name_regex"], rec.long_name):
            continue
        if "object_type_regex" in m and not _search(m["object_type_regex"], rec.object_type):
            continue
        if any(rec.props.get(k) != v for k, v in m.get("properties", {}).items()):
            continue
        return rule
    return None


def _neighbours(cells: set[Cell]) -> set[Cell]:
    out: set[Cell] = set()
    for x, z in cells:
        out.update(((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))
    return out - cells


def tile_cells(cells: Iterable[Cell], block: tuple[int, int]) -> list[list[Cell]]:
    """Blocks of up to ``block`` cells over a cell set (only occupied cells), ordered by (row, column)."""
    bw, bd = max(1, block[0]), max(1, block[1])
    blocks: dict[tuple[int, int], list[Cell]] = defaultdict(list)
    for c in sorted(cells, key=lambda c: (c[1], c[0])):
        blocks[(c[1] // bd, c[0] // bw)].append(c)
    return [blocks[k] for k in sorted(blocks)]


def build_zones(spaces: Seq[SpaceRec], rules: Seq[Mapping[str, Any]], occupied: Mapping[str, set[Cell]], *,
                storey_order: Seq[str], source: str = "ifc_space", auto_block: tuple[int, int] = (3, 3),
                max_crews_default: int = 2, merge_below: int = 2, auto_prefix: str = "A") -> tuple[list[JSON], dict[tuple[str, Cell], str]]:
    """Zones for a model.

    Spaces become zones (tags, crew caps, faces from the first matching rule; ``source="ifc_zone"`` first
    merges the spaces of one IfcZone). A space smaller than ``merge_below`` cells merges into the adjacent
    space it touches most (else stays alone). A cell claimed by several spaces belongs to the smaller one.
    Occupied cells outside every space are tiled into auto-block zones. Returns ``(zones, zone_of)`` with
    ``zone_of[(storey_id, cell)] = zone_id``.
    """
    # 1. units: spaces (or merged IfcZone groups) per storey
    units: dict[str, list[dict[str, Any]]] = {sid: [] for sid in storey_order}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for sp in spaces:
        if sp.storey_id not in units:
            continue
        if source == "ifc_zone" and sp.group:
            u = grouped.get((sp.storey_id, sp.group))
            if u is None:
                rule = match_rule(rules, SpaceRec(sp.guid, sp.group, sp.storey_id, sp.cells, sp.long_name,
                                                  sp.object_type, sp.props))
                u = grouped[(sp.storey_id, sp.group)] = {"name": sp.group, "cells": set(), "rule": rule}
                units[sp.storey_id].append(u)
            u["cells"].update(sp.cells)
        else:
            units[sp.storey_id].append({"name": sp.name, "cells": set(sp.cells), "rule": match_rule(rules, sp)})
    zones: list[JSON] = []
    zone_of: dict[tuple[str, Cell], str] = {}
    for sid in storey_order:
        us = [u for u in units[sid] if u["cells"]]
        # 2. merge small units into the neighbour they touch most
        for u in sorted(us, key=lambda u: len(u["cells"])):
            if len(u["cells"]) >= merge_below or u.get("merged"):
                continue
            ring = _neighbours(u["cells"])
            best, best_n = None, 0
            for v in us:
                if v is u or v.get("merged"):
                    continue
                n = len(ring & v["cells"])
                if n > best_n:
                    best, best_n = v, n
            if best is not None:
                best["cells"] |= u["cells"]
                best["tags_extra"] = best.get("tags_extra", []) + ((u["rule"] or {}).get("tags", []))
                u["merged"] = True
        us = [u for u in us if not u.get("merged")]
        # 3. ownership: smaller space wins a contested cell
        owner: dict[Cell, dict[str, Any]] = {}
        for u in sorted(us, key=lambda u: (len(u["cells"]), u["name"])):
            for c in sorted(u["cells"]):
                owner.setdefault(c, u)
        for n, u in enumerate(sorted(us, key=lambda u: (min((c[1], c[0]) for c in u["cells"]), u["name"])), start=1):
            cells = sorted(c for c, o in owner.items() if o is u)
            if not cells:
                continue
            zid = f"{sid}-S{n}"
            rule = u["rule"] or {}
            tags = list(dict.fromkeys(list(rule.get("tags", [])) + list(u.get("tags_extra", []))))
            z: JSON = {"id": zid, "name": u["name"], "storey_id": sid, "cells": [[c[0], c[1]] for c in cells],
                       "max_crews": int(rule.get("max_crews", max_crews_default)), "tags": tags}
            if rule.get("faces"):
                z["faces"] = {k: min(v, z["max_crews"]) for k, v in rule["faces"].items()}
            if rule.get("shift_allowed") is False:
                z["shift_allowed"] = False
            zones.append(z)
            for c in cells:
                zone_of[(sid, c)] = zid
        # 4. leftover occupied cells -> auto blocks
        left = {c for c in occupied.get(sid, set()) if (sid, c) not in zone_of}
        for k, block in enumerate(tile_cells(left, auto_block), start=1):
            zid = f"{sid}-{auto_prefix}{k}"
            zones.append({"id": zid, "name": f"{sid} area {k}", "storey_id": sid,
                          "cells": [[c[0], c[1]] for c in block], "max_crews": max_crews_default, "tags": []})
            for c in block:
                zone_of[(sid, c)] = zid
    return zones, zone_of


def zones_from_csv(path: str | Path, storey_ids: Seq[str], max_crews_default: int = 2) -> list[JSON]:
    """Rectangular zones from a CSV: ``zone_id,name,storey_id,x0,z0,x1,z1[,tags][,max_crews]`` (tags ``;`` separated)."""
    zones: list[JSON] = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row["storey_id"]
            if sid not in storey_ids:
                continue
            x0, x1 = sorted((int(row["x0"]), int(row["x1"])))
            z0, z1 = sorted((int(row["z0"]), int(row["z1"])))
            zones.append({"id": row["zone_id"], "name": row.get("name") or row["zone_id"], "storey_id": sid,
                          "cells": [[x, z] for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)],
                          "max_crews": int(row.get("max_crews") or max_crews_default),
                          "tags": [t for t in (row.get("tags") or "").split(";") if t]})
    return zones
