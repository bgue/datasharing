"""Shared builders for the bimseq tests (tiny hand-made libraries, rules and element lists)."""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from bimseq.model import (  # noqa: E402
    elements_from_dict, mapping_rules_from_dict, scenario_from_dict, step_library_from_dict,
)

REPO = TOOLS.parent


def step(sid, phase="build", trade="crew", basis="count", rate=1.0, cost=10.0, preds=(), **kw):
    """Compact step dict. ``preds`` are ``(step, scope[, type, lag, required])`` tuples."""
    plist = []
    for p in preds:
        d = {"step": p[0], "scope": p[1]}
        if len(p) > 2:
            d["type"] = p[2]
        if len(p) > 3:
            d["lag_days"] = p[3]
        if len(p) > 4:
            d["required"] = p[4]
        plist.append(d)
    d = {"id": sid, "name": sid, "phase": phase, "trade": trade, "discipline": "general",
         "quantity_basis": basis, "rate_per_crew_day": rate, "unit_cost": cost, "predecessors": plist}
    d.update(kw)
    return d


def library(steps, gates=(), phases=("build", "fit"), trades=("crew", "other")):
    return step_library_from_dict({
        "schema_version": "1.0", "sector": "industrial",
        "phases": [{"id": p, "name": p, "order": i} for i, p in enumerate(phases)],
        "trades": [{"id": t, "name": t, "weekly_cost": 1000} for t in trades],
        "steps": list(steps), "gates": list(gates)})


def rules(rule_list, default_step="TST-DEFAULT-DO", default_quantity="count"):
    return mapping_rules_from_dict({
        "schema_version": "1.0", "sector": "industrial", "rules": list(rule_list),
        "default": {"steps": [{"step": default_step, "quantity": default_quantity, "min_quantity": 1}]}})


def rule(rid, prio, match, steps, **kw):
    d = {"id": rid, "priority": prio, "match": match,
         "steps": [s if isinstance(s, dict) else {"step": s, "quantity": "count"} for s in steps]}
    d.update(kw)
    return d


def elem(guid, name, storey, zone, cells, ifc_class="IfcBuildingElementProxy", system=None, host=None,
         quantities=None, **kw):
    d = {"guid": guid, "ifc_class": ifc_class, "name": name, "storey_id": storey, "zone_id": zone,
         "cells": [list(c) for c in cells], "quantities": quantities or {"count": 1, "length_m": 6.0},
         "system_id": system, "host_guid": host}
    d.update(kw)
    return d


def doc(elements, storeys=None, zones=None):
    storeys = storeys or [("B", -1), ("G", 0), ("U", 1)]
    zones = zones or [
        ("B-Z1", "B", [(0, 0), (1, 0), (2, 0), (3, 0)], []),
        ("G-Z1", "G", [(0, 0), (1, 0)], []),
        ("G-Z2", "G", [(2, 0), (3, 0)], []),
        ("U-Z1", "U", [(0, 0), (1, 0), (2, 0), (3, 0)], []),
    ]
    return elements_from_dict({
        "schema_version": "1.0",
        "project": {"name": "hand", "sector": "industrial",
                    "grid": {"cell_size_m": 6, "storey_height_m": 4, "width_cells": 4, "depth_cells": 1}},
        "storeys": [{"id": s, "name": s, "index": i} for s, i in storeys],
        "zones": [{"id": z, "name": z, "storey_id": s, "cells": [list(c) for c in cells], "max_crews": 2,
                   "tags": tags} for z, s, cells, tags in zones],
        "systems": [], "elements": elements})


def scenario(crews=None, **kw):
    d = {"schema_version": "1.0", "id": "test", "name": "test", "sector": "industrial", "difficulty": "standard",
         "contract_weeks": None, "budget": 0, "start_cash": 0, "crews_available": crews or {"crew": 1, "other": 1},
         "equipment": [], "site": {"gates": [[0, 0]]}, "events": [],
         "scoring": {"weights": {"time": 1, "cost": 1, "safety": 1, "quality": 1, "stability": 1}}}
    d.update(kw)
    return scenario_from_dict(d)
