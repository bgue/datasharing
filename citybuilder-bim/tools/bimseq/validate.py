"""Schema validation built on tools/validate_schemas.py, plus light cross-reference checks."""
from __future__ import annotations

import functools
import os
import sys
from pathlib import Path
from typing import Any, Iterator

_TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import validate_schemas as vs  # noqa: E402  (tools/validate_schemas.py)

from .model import read_json  # noqa: E402

MAX_ERRORS = 25


@functools.lru_cache(maxsize=None)
def _validator(name: str):
    return vs.validator_for(name)


def guess_schema(path: str | Path) -> str | None:
    """Schema name for a file (delegates to validate_schemas.guess_schema; ``manual*.json`` too)."""
    name = vs.guess_schema(str(path))
    if name is None and Path(path).name.startswith("manual") and Path(path).suffix == ".json":
        return "manual_sequence"
    return name


def schema_errors(name: str, data: Any) -> list[str]:
    """Human-readable schema violations for ``data`` against schema ``name`` (empty if valid)."""
    errors = sorted(_validator(name).iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    lines = []
    for err in errors[:MAX_ERRORS]:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        lines.append(f"{loc}: {err.message[:300]}")
    if len(errors) > MAX_ERRORS:
        lines.append(f"... {len(errors) - MAX_ERRORS} more errors")
    return lines


def cross_checks(name: str, data: dict[str, Any]) -> list[str]:
    """Reference-integrity checks the JSON Schemas cannot express (task graph, library refs)."""
    problems: list[str] = []
    if name not in ("sequence", "element_step_map"):
        return problems
    tasks = data.get("tasks", [])
    ids = [t["task_id"] for t in tasks]
    idset = set(ids)
    if len(idset) != len(ids):
        problems.append("duplicate task_id values")
    for t in tasks:
        for p in t.get("predecessors", []):
            if p["task_id"] not in idset:
                problems.append(f"{t['task_id']}: unknown predecessor {p['task_id']}")
            elif p["task_id"] == t["task_id"]:
                problems.append(f"{t['task_id']}: self predecessor")
    if name == "sequence" and data.get("packages"):
        pkgs = data["packages"]
        pids = [p["package_id"] for p in pkgs]
        if len(set(pids)) != len(pids):
            problems.append("duplicate package_id values")
        by_task = {}
        for p in pkgs:
            for tid in p["task_ids"]:
                if tid not in idset:
                    problems.append(f"{p['package_id']}: unknown task {tid}")
                by_task[tid] = p["package_id"]
        for t in tasks:
            if by_task.get(t["task_id"]) != t.get("package_id"):
                problems.append(f"{t['task_id']}: package_id {t.get('package_id')} disagrees with packages[]")
    if name == "sequence":
        steps = {s["id"] for s in data["step_library"]["steps"]}
        zones = {z["id"] for z in data["zones"]}
        guids = {e["guid"] for e in data["elements"]}
        for t in tasks:
            if t["step_id"] not in steps:
                problems.append(f"{t['task_id']}: step {t['step_id']} not in embedded library")
            if t["zone_id"] not in zones:
                problems.append(f"{t['task_id']}: unknown zone {t['zone_id']}")
            if t["element_guid"] is not None and t["element_guid"] not in guids:
                problems.append(f"{t['task_id']}: unknown element {t['element_guid']}")
            if t["planned_finish_day"] < t["planned_start_day"]:
                problems.append(f"{t['task_id']}: finish before start")
    return problems[:MAX_ERRORS]


def validate_file(path: str | Path, schema: str | None = None) -> tuple[bool, list[str]]:
    """Validate one file; returns (ok, messages)."""
    name = schema or guess_schema(path)
    if name is None:
        return False, ["cannot guess schema from file name"]
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        return False, [f"cannot read JSON: {exc}"]
    msgs = schema_errors(name, data)
    if not msgs:
        msgs = cross_checks(name, data)
    return not msgs, msgs


def iter_recognised(root: str | Path) -> Iterator[tuple[Path, str]]:
    """Yield (path, schema) for every ``*.json`` under root whose name maps to a schema."""
    root = Path(root)
    candidates = [root] if root.is_file() else sorted(root.rglob("*.json"))
    for p in candidates:
        name = guess_schema(p)
        if name:
            yield p, name


def validate_tree(root: str | Path, out=None) -> bool:
    """Validate a directory (or single file). Prints OK/FAIL lines; returns True if all pass."""
    out = out or sys.stdout
    ok = True
    count = 0
    for path, name in iter_recognised(root):
        good, msgs = validate_file(path, name)
        count += 1
        print(f"{'OK  ' if good else 'FAIL'} {name:17s} {path}", file=out)
        for m in msgs:
            print(f"     {m}", file=out)
        ok &= good
    if count == 0:
        print(f"no recognised JSON files under {root}", file=out)
        return False
    return ok
