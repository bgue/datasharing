"""Bundle I/O: plain JSON, gzip, and the split format (task parts + per-zone lazy detail).

Split layout written next to ``sequence.json[.gz]``::

    sequence.json.gz            everything except tasks[] (empty); ``bundle_format`` lists the parts
    tasks.part-0.json.gz ...    {"schema_version", "part": N, "tasks": [full task objects]}
    zones/<zone_id>.json.gz     {"zone_id", "task_ids", "package_ids", "members": {aggregate guid: [member records]}}

Task objects in parts are exactly the schema's task shape, so a loader can concatenate the parts into
``tasks``. With ``lazy_zone_detail`` the aggregate elements' ``member_guids`` move out of the main file
into the zone detail files.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .model import JSON

GZ = ".gz"


def _dump(data: Any, path: Path, compress: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=None if compress else 1, ensure_ascii=False, separators=(",", ":") if compress else None)
    if compress:
        path = path.with_name(path.name + GZ)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=6, newline="\n") as fh:
            fh.write(text)
            fh.write("\n")
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.write("\n")
    return path


def read_any_json(path: str | Path) -> Any:
    """Read ``.json`` or ``.json.gz``."""
    path = Path(path)
    if path.suffix == GZ:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def safe_name(zone_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", zone_id)


def write_bundle(bundle: Mapping[str, Any], out_dir: str | Path, *, compress: bool = False,
                 tasks_per_part: int = 5000, lazy_zone_detail: bool = False,
                 name: str = "sequence") -> Path:
    """Write ``bundle`` as ``<out_dir>/<name>.json[.gz]``; split into parts when ``compress`` or lazy detail.

    Returns the main file path. Without compression and lazy detail this is the classic single file.
    """
    out_dir = Path(out_dir)
    if not compress and not lazy_zone_detail:
        return _dump(dict(bundle), out_dir / f"{name}.json", False)
    main = {k: v for k, v in bundle.items() if k != "tasks"}
    tasks = list(bundle["tasks"])
    parts: list[str] = []
    for n, lo in enumerate(range(0, len(tasks), tasks_per_part)):
        p = _dump({"schema_version": "1.0", "part": n, "tasks": tasks[lo:lo + tasks_per_part]},
                  out_dir / f"tasks.part-{n}.json", compress)
        parts.append(p.name)
    fmt: JSON = {"compressed": compress, "task_parts": parts}
    if lazy_zone_detail:
        by_zone: dict[str, JSON] = {}
        for z in bundle["zones"]:
            by_zone[z["id"]] = {"zone_id": z["id"], "task_ids": [], "package_ids": [], "members": {}}
        for t in tasks:
            by_zone[t["zone_id"]]["task_ids"].append(t["task_id"])
        for p in bundle.get("packages", []):
            by_zone[p["zone_id"]]["package_ids"].append(p["package_id"])
        light = []
        for e in bundle["elements"]:
            if e.get("member_guids"):
                by_zone[e["zone_id"]]["members"][e["guid"]] = e["member_guids"]
                e = {k: v for k, v in e.items() if k != "member_guids"}
                e["member_count"] = len(by_zone[e["zone_id"]]["members"][e["guid"]])
            light.append(e)
        main["elements"] = light
        for zid, detail in by_zone.items():
            _dump(detail, out_dir / "zones" / f"{safe_name(zid)}.json", compress)
        fmt["zone_detail_dir"] = "zones"
    main["tasks"] = []
    main["bundle_format"] = fmt
    return _dump(main, out_dir / f"{name}.json", compress)


def load_bundle(path: str | Path, *, with_zone_detail: bool = True) -> JSON:
    """Load a bundle (single file or split) into the classic in-memory shape (tasks merged)."""
    path = Path(path)
    data = read_any_json(path)
    fmt = data.get("bundle_format") or {}
    parts = fmt.get("task_parts") or []
    if parts:
        tasks: list[JSON] = []
        for rel in parts:
            tasks.extend(read_any_json(path.parent / rel)["tasks"])
        data["tasks"] = tasks
    detail_dir = fmt.get("zone_detail_dir")
    if detail_dir and with_zone_detail:
        members: dict[str, list[str]] = {}
        for z in data["zones"]:
            f = path.parent / detail_dir / (safe_name(z["id"]) + (".json.gz" if fmt.get("compressed") else ".json"))
            if f.exists():
                members.update(read_any_json(f)["members"])
        for e in data["elements"]:
            if e["guid"] in members:
                e["member_guids"] = members[e["guid"]]
                e.pop("member_count", None)
    return data
