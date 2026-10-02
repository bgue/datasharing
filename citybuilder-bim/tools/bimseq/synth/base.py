"""Shared builder for the synthetic BIM generators (seeded, deterministic)."""
from __future__ import annotations

import random
from typing import Any, Callable, Iterable, Sequence

from ..visuals import visual_for

Cell = tuple[int, int]
GUID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
TagFn = Callable[[int, int, int, int], list[str]]


def q(volume: float | None = None, area: float | None = None, length: float | None = None,
      weight: float | None = None, count: float = 1) -> dict[str, float]:
    """Quantities dict (rounded); omitted measures are left out, ``count`` defaults to 1."""
    out: dict[str, float] = {}
    for key, val in (("volume_m3", volume), ("area_m2", area), ("length_m", length),
                     ("count", count), ("weight_t", weight)):
        if val is not None:
            out[key] = round(float(val), 3)
    return out


class SynthBuilder:
    """Accumulates storeys, zones, systems and elements and emits an elements.json document."""

    def __init__(self, sector: str, name: str, description: str, width: int, depth: int,
                 seed: int = 42, cell_size: float = 6.0, storey_height: float = 4.0) -> None:
        self.sector, self.name, self.description = sector, name, description
        self.width, self.depth = width, depth
        self.cs, self.sh = cell_size, storey_height
        self.rng = random.Random(seed)
        self.storeys: list[dict[str, Any]] = []
        self.zones: list[dict[str, Any]] = []
        self.systems: dict[str, dict[str, str]] = {}
        self.elements: list[dict[str, Any]] = []
        self._zone_at: dict[tuple[str, Cell], str] = {}
        self._guids: set[str] = set()
        self._elev: dict[str, float] = {}
        self._counter: dict[str, int] = {}

    # ------------------------------------------------------------- structure
    def guid(self) -> str:
        """Unique IFC-style 22 character GUID drawn from the seeded RNG."""
        while True:
            g = self.rng.choice(GUID_CHARS[:4]) + "".join(self.rng.choice(GUID_CHARS) for _ in range(21))
            if g not in self._guids:
                self._guids.add(g)
                return g

    def add_storey(self, sid: str, name: str, index: int) -> None:
        elev = round(index * self.sh, 3)
        self._elev[sid] = elev
        self.storeys.append({"id": sid, "name": name, "index": index, "elevation_m": elev})

    def tile_zones(self, storey_id: str, x0: int, z0: int, w: int, d: int, bw: int, bd: int,
                   tags: TagFn | None = None, max_crews: int = 2) -> None:
        """Tile the rectangle with ``bw x bd`` blocks (edge blocks may be smaller) as zones."""
        n = len([z for z in self.zones if z["storey_id"] == storey_id])
        for bz in range(z0, z0 + d, bd):
            for bx in range(x0, x0 + w, bw):
                cells = [[x, z] for z in range(bz, min(bz + bd, z0 + d)) for x in range(bx, min(bx + bw, x0 + w))]
                n += 1
                zid = f"{storey_id}-Z{n}"
                ztags = tags(bx, bz, min(bx + bw, x0 + w) - 1, min(bz + bd, z0 + d) - 1) if tags else []
                self.zones.append({"id": zid, "name": f"{storey_id} zone {n}", "storey_id": storey_id,
                                   "cells": cells, "max_crews": max_crews, "tags": ztags})
                for c in cells:
                    self._zone_at[(storey_id, (c[0], c[1]))] = zid

    def add_system(self, sid: str, name: str, discipline: str) -> str:
        self.systems.setdefault(sid, {"id": sid, "name": name, "discipline": discipline})
        return sid

    def elevation(self, storey_id: str) -> float:
        return self._elev[storey_id]

    # ------------------------------------------------------------- geometry helpers
    def box(self, cells: Sequence[Cell], storey_id: str, z0: float, z1: float,
            inset: float = 0.0) -> dict[str, list[float]]:
        """Bbox (model x, y-north, z-up) covering ``cells`` between heights z0..z1 above the storey."""
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        e = self._elev[storey_id]
        return self.box_xy((min(xs) * self.cs + inset, min(ys) * self.cs + inset,
                            (max(xs) + 1) * self.cs - inset, (max(ys) + 1) * self.cs - inset),
                           e + z0, e + z1)

    @staticmethod
    def box_xy(xy: tuple[float, float, float, float], z0: float, z1: float) -> dict[str, list[float]]:
        x0, y0, x1, y1 = xy
        return {"min": [round(x0, 3), round(y0, 3), round(z0, 3)],
                "max": [round(x1, 3), round(y1, 3), round(z1, 3)]}

    def centred(self, cell: Cell, storey_id: str, sx: float, sy: float, z0: float, z1: float,
                dx: float = 0.0, dy: float = 0.0) -> dict[str, list[float]]:
        """Bbox of size sx x sy centred in ``cell`` (offset dx, dy metres)."""
        cx = (cell[0] + 0.5) * self.cs + dx
        cy = (cell[1] + 0.5) * self.cs + dy
        e = self._elev[storey_id]
        return self.box_xy((cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2), e + z0, e + z1)

    def next_no(self, key: str) -> int:
        self._counter[key] = self._counter.get(key, 0) + 1
        return self._counter[key]

    # ------------------------------------------------------------- elements
    def add(self, ifc_class: str, name: str, storey_id: str, cells: Iterable[Cell], *,
            qty: dict[str, float], predefined: str | None = None, material: str | None = None,
            system: str | None = None, host: str | None = None, props: dict[str, Any] | None = None,
            bbox: dict[str, list[float]] | None = None, visual: str | None = None) -> str:
        """Append an element; its zone is the zone of the first cell. Returns the new GUID."""
        cells = [(int(c[0]), int(c[1])) for c in cells]
        zone = self._zone_at.get((storey_id, cells[0]))
        if zone is None:
            raise ValueError(f"cell {cells[0]} on {storey_id} is not covered by a zone ({name})")
        props = dict(props or {})
        g = self.guid()
        self.elements.append({
            "guid": g, "ifc_class": ifc_class, "predefined_type": predefined, "name": name,
            "storey_id": storey_id, "zone_id": zone, "system_id": system, "host_guid": host,
            "cells": [[c[0], c[1]] for c in cells], "quantities": qty, "material": material,
            "properties": props, "bbox": bbox,
            "visual": visual or visual_for(ifc_class, props, predefined, name),
        })
        return g

    def document(self) -> dict[str, Any]:
        """The elements.json document."""
        used = {e["system_id"] for e in self.elements if e["system_id"]}
        systems = [s for sid, s in self.systems.items() if sid in used]
        for e in self.elements:
            if e["bbox"] is None:
                del e["bbox"]
        return {
            "schema_version": "1.0",
            "project": {
                "name": self.name, "sector": self.sector, "description": self.description,
                "source": "synthetic",
                "grid": {"cell_size_m": self.cs, "storey_height_m": self.sh, "origin": [0, 0, 0],
                         "width_cells": self.width, "depth_cells": self.depth},
            },
            "storeys": self.storeys, "zones": self.zones, "systems": systems,
            "elements": self.elements,
        }
