"""CSV export of a sequence.json / element_step_map.json for scheduling tools."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping

from .model import read_json

COLUMNS = [
    "element_guid", "task_id", "step_id", "element_name", "storey_id", "zone_id", "trade", "phase",
    "quantity", "unit", "planned_start_day", "planned_finish_day", "actual_start_day",
    "actual_finish_day", "predecessor_task_ids",
]


def _cell(v: Any) -> Any:
    return "" if v is None else v


def aggregate_members(data: Mapping[str, Any]) -> dict[str, list[str]]:
    """aggregate guid -> member guids, from ``aggregates`` (map) or ``elements[].member_guids`` (bundle)."""
    out = {k: list(v) for k, v in data.get("aggregates", {}).items()}
    for e in data.get("elements", []):
        if e.get("member_guids"):
            out.setdefault(e["guid"], list(e["member_guids"]))
    return out


def task_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One CSV row per task and element; missing dates become empty cells.

    A task on an aggregate element expands to one row per member GUID with the aggregate's dates;
    virtual tasks (no element) have an empty ``element_guid``.
    """
    members = aggregate_members(data)
    rows = []
    for t in data["tasks"]:
        row = {c: _cell(t.get(c)) for c in COLUMNS if c != "predecessor_task_ids"}
        row["predecessor_task_ids"] = ";".join(p["task_id"] for p in t.get("predecessors", []))
        for guid in members.get(t.get("element_guid"), [t.get("element_guid")]):
            rows.append({c: (_cell(guid) if c == "element_guid" else row[c]) for c in COLUMNS})
    return rows


def export_csv(source: str | Path | Mapping[str, Any], out_path: str | Path) -> int:
    """Write the CSV; ``source`` is a path or already-parsed document. Returns the row count."""
    data = read_json(source) if isinstance(source, (str, Path)) else source
    rows = task_rows(data)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def read_csv(path: str | Path) -> list[dict[str, str]]:
    """Read an exported CSV back as string rows (used for round-trip checks)."""
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
