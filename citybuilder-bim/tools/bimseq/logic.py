"""Construction logic library: recipe loading, matching, indexing and explanation (docs/06 Track A)."""
from __future__ import annotations

import copy
import functools
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence as Seq

from .model import Cell, Element, ElementsDoc, JSON, StepMap, read_json, write_json
from .validate import schema_errors

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGIC_DIR = REPO_ROOT / "data" / "logic"
DEFAULT_RECIPES_DIR = DEFAULT_LOGIC_DIR / "recipes"
DEFAULT_INDEX_PATH = DEFAULT_LOGIC_DIR / "index.json"

FOUNDATION_CLASSES = ("IfcFooting", "IfcPile")


# --------------------------------------------------------------------------- loading
def load_recipes(extra_dirs: Iterable[str | Path] = (), include_default: bool = True
                 ) -> tuple[dict[str, JSON], list[str]]:
    """Load and validate every recipe ``**/*.json`` under the default and extra directories.

    Returns ``(recipes by id, error messages)``; invalid or duplicate recipes are skipped.
    """
    dirs = ([DEFAULT_RECIPES_DIR] if include_default else []) + [Path(d) for d in extra_dirs]
    recipes: dict[str, JSON] = {}
    errors: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.json")):
            try:
                data = read_json(path)
            except (OSError, ValueError) as exc:
                errors.append(f"{path}: cannot read: {exc}")
                continue
            msgs = schema_errors("recipe", data)
            if msgs:
                errors.extend(f"{path}: {m}" for m in msgs[:5])
                continue
            if data["id"] in recipes:
                errors.append(f"{path}: duplicate recipe id {data['id']}")
                continue
            recipes[data["id"]] = data
    return recipes, errors


def clean(recipe: Mapping[str, Any]) -> JSON:
    """A copy of the recipe, safe to embed."""
    return copy.deepcopy(dict(recipe))


# --------------------------------------------------------------------------- matching
@functools.lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return actual == expected


def applies_to_matches(applies: Mapping[str, Any], el: Element, zone_tags: Iterable[str] = ()) -> bool:
    """True when ANY applies_to key matches the element (OR across keys)."""
    tags = set(zone_tags)
    if "ifc_class" in applies and el.ifc_class in applies["ifc_class"]:
        return True
    if "predefined_type" in applies and el.predefined_type in applies["predefined_type"]:
        return True
    if "name_regex" in applies and _regex(applies["name_regex"]).search(el.name or ""):
        return True
    props = applies.get("properties")
    if props and all(_same(el.properties.get(k), v) for k, v in props.items()):
        return True
    if "visual_kit" in applies and el.visual_kit is not None and el.visual_kit in applies["visual_kit"]:
        return True
    if "zone_tags_any" in applies and tags & set(applies["zone_tags_any"]):
        return True
    kws = applies.get("keywords")
    if kws:
        hay = f"{el.name} {el.ifc_class}".lower()
        if any(k.lower() in hay for k in kws):
            return True
    return False


def recipe_matches(recipe: Mapping[str, Any], el: Element, zone_tags: Iterable[str] = (),
                   sector: str | None = None) -> bool:
    """Sector gate (recipe sector equals project sector or ``all``) plus :func:`applies_to_matches`."""
    if sector is not None and recipe["sector"] not in ("all", sector):
        return False
    return applies_to_matches(recipe.get("applies_to", {}), el, zone_tags)


def matching_recipes(recipes: Mapping[str, JSON], el: Element, zone_tags: Iterable[str],
                     sector: str | None) -> list[JSON]:
    """Recipes applicable to one element, ordered by id."""
    return [r for rid, r in sorted(recipes.items()) if recipe_matches(r, el, zone_tags, sector)]


def applicable_recipes(recipes: Mapping[str, JSON], doc: ElementsDoc, extra_ids: Iterable[str] = ()) -> list[JSON]:
    """Recipes that match at least one element of the project (plus ``extra_ids``), ordered by id."""
    sector = doc.project.get("sector")
    tags = {z.id: z.tags for z in doc.zones}
    keep = set(extra_ids)
    for rid, r in recipes.items():
        if rid in keep:
            continue
        if any(recipe_matches(r, e, tags.get(e.zone_id, ()), sector) for e in doc.elements):
            keep.add(rid)
    return [clean(recipes[rid]) for rid in sorted(keep) if rid in recipes]


# --------------------------------------------------------------------------- spatial helpers
class ElementIndex:
    """Lookups over an elements document used for recipe binding and explanation."""

    def __init__(self, doc: ElementsDoc) -> None:
        self.doc = doc
        self.by_guid = {e.guid: e for e in doc.elements}
        self.storey_index = {s.id: s.index for s in doc.storeys}
        self.by_system: dict[str, list[Element]] = {}
        self.foundations: dict[int, list[Element]] = {}
        for e in sorted(doc.elements, key=lambda e: e.guid):
            if e.system_id:
                self.by_system.setdefault(e.system_id, []).append(e)
            if e.ifc_class in FOUNDATION_CLASSES or (e.ifc_class == "IfcSlab" and e.predefined_type == "BASESLAB"):
                self.foundations.setdefault(self.storey_index[e.storey_id], []).append(e)

    def foundation_for(self, el: Element) -> Element:
        """Foundation element under ``el``: most overlapping cells on the same, else a lower storey."""
        mine = set(el.cells)
        si = self.storey_index[el.storey_id]
        levels = sorted((i for i in self.foundations if i <= si), reverse=True)
        for level in levels:
            best: tuple[int, str, Element] | None = None
            for f in self.foundations[level]:
                n = len(mine.intersection(f.cells))
                if n and (best is None or (-n, f.guid) < (-best[0], best[1])):
                    best = (n, f.guid, f)
            if best is not None:
                return best[2]
        return el

    def targets(self, el: Element, from_element: str) -> list[Element]:
        """Elements a non-virtual recipe step binds to for a given ``from_element`` mode."""
        if from_element == "foundation":
            return [self.foundation_for(el)]
        if from_element == "host":
            host = self.by_guid.get(el.host_guid) if el.host_guid else None
            return [host or el]
        if from_element == "system":
            return list(self.by_system.get(el.system_id, [])) if el.system_id else [el]
        return [el]


def step_ref(entry: Mapping[str, Any]) -> str | None:
    """Step id of a recipe step entry (``ref`` or inline ``step.id``); None for nested recipes."""
    if "ref" in entry:
        return entry["ref"]
    if "step" in entry:
        return entry["step"].get("id")
    return None


# --------------------------------------------------------------------------- index
def build_index(recipes: Mapping[str, JSON]) -> JSON:
    """The generated ``index.json``: id, name, sector, tags, applies_to summary, step refs, virtual count."""
    rows = []
    for rid, r in sorted(recipes.items()):
        steps = r["steps"]
        refs = [step_ref(s) for s in steps if step_ref(s)]
        applies = r.get("applies_to", {})
        rows.append({
            "id": rid, "name": r["name"], "sector": r["sector"], "tags": list(r.get("tags", [])),
            "applies_to": {k: v for k, v in applies.items()},
            "applies_to_summary": ", ".join(f"{k}={v}" for k, v in applies.items() if k != "properties")
                                  + (", properties=" + ",".join(f"{k}={v}" for k, v in applies["properties"].items())
                                     if applies.get("properties") else ""),
            "steps": refs, "nested_recipes": [s["recipe"] for s in steps if "recipe" in s],
            "step_count": len(steps), "virtual_count": sum(1 for s in steps if s.get("virtual")),
            "summary": r.get("summary", ""), "typical_duration_weeks": r.get("typical_duration_weeks"),
        })
    return {"schema_version": "1.0", "recipe_count": len(rows), "recipes": rows}


def write_index(recipes: Mapping[str, JSON], path: str | Path = DEFAULT_INDEX_PATH) -> JSON:
    idx = build_index(recipes)
    write_json(path, idx)
    return idx


# --------------------------------------------------------------------------- explain
def _coverage(entry: Mapping[str, Any], recipe: Mapping[str, Any], el: Element, index: ElementIndex,
              step_map: StepMap | None, zone_id: str) -> JSON:
    ref = step_ref(entry)
    row: JSON = {"key": entry.get("key") or ref or entry.get("recipe"), "ref": ref or entry.get("recipe"),
                 "virtual": bool(entry.get("virtual")), "from_element": entry.get("from_element", "self"),
                 "optional": bool(entry.get("optional")), "status": "unknown", "task_ids": []}
    if "recipe" in entry:
        row["status"] = "nested"
        return row
    if step_map is None:
        row["status"] = "virtual" if row["virtual"] else "unchecked"
        return row
    if row["virtual"] or entry.get("from_element") == "zone":
        tasks = [t for t in step_map.tasks if t.virtual and t.step_id == ref and t.zone_id == zone_id]
        tasks = [t for t in tasks if t.recipe_id in (None, recipe["id"])] or []
        row["task_ids"] = [t.task_id for t in tasks]
        row["status"] = "covered" if tasks else "virtual"
        return row
    targets = {t.guid for t in index.targets(el, entry.get("from_element", "self"))}
    tasks = [t for t in step_map.tasks if t.step_id == ref and t.element_guid in targets]
    row["task_ids"] = [t.task_id for t in tasks]
    row["status"] = "covered" if tasks else "missing"
    return row


def explain_element(recipes: Mapping[str, JSON], index: ElementIndex, guid: str,
                    step_map: StepMap | None = None) -> list[JSON]:
    """Matching recipes for one element with per-step coverage (covered / virtual / missing)."""
    el = index.by_guid[guid]
    zones = index.doc.zone_by_id()
    tags = zones[el.zone_id].tags if el.zone_id in zones else []
    out = []
    for r in matching_recipes(recipes, el, tags, index.doc.project.get("sector")):
        out.append({"recipe": r["id"], "name": r["name"], "element": guid,
                    "steps": [_coverage(s, r, el, index, step_map, el.zone_id) for s in r["steps"]]})
    return out


def explain_zone(recipes: Mapping[str, JSON], index: ElementIndex, zone_id: str,
                 step_map: StepMap | None = None) -> list[JSON]:
    """Per matching recipe: the zone's matched elements and aggregated step status counts."""
    doc = index.doc
    zone = doc.zone_by_id()[zone_id]
    sector = doc.project.get("sector")
    members = sorted((e for e in doc.elements if e.zone_id == zone_id), key=lambda e: e.guid)
    out = []
    for rid, r in sorted(recipes.items()):
        matched = [e for e in members if recipe_matches(r, e, zone.tags, sector)]
        if not matched:
            continue
        agg: dict[str, JSON] = {}
        for e in matched:
            for s in r["steps"]:
                row = _coverage(s, r, e, index, step_map, zone_id)
                a = agg.setdefault(row["key"], {**row, "task_ids": [], "counts": {}})
                a["counts"][row["status"]] = a["counts"].get(row["status"], 0) + 1
                a["task_ids"] = sorted(set(a["task_ids"]) | set(row["task_ids"]))
        for a in agg.values():
            a["status"] = sorted(a["counts"], key=lambda k: -a["counts"][k])[0]
        out.append({"recipe": rid, "name": r["name"], "zone": zone_id,
                    "elements": [e.guid for e in matched], "steps": list(agg.values())})
    return out


def format_explanation(items: Seq[JSON]) -> str:
    """Human-readable text for :func:`explain_element` / :func:`explain_zone` results."""
    if not items:
        return "no recipe applies"
    lines: list[str] = []
    for it in items:
        head = f"{it['recipe']}  {it['name']}"
        if "elements" in it:
            head += f"  ({len(it['elements'])} matching elements)"
        lines.append(head)
        for s in it["steps"]:
            extra = ""
            if s.get("counts"):
                extra = "  " + ", ".join(f"{k}={v}" for k, v in sorted(s["counts"].items()))
            tasks = f"  tasks: {', '.join(s['task_ids'][:6])}{' ...' if len(s['task_ids']) > 6 else ''}" if s["task_ids"] else ""
            flags = ("virtual " if s["virtual"] else "") + ("optional " if s["optional"] else "")
            lines.append(f"  [{s['status']:9s}] {s['ref']:22s} {flags}{extra}{tasks}")
    return "\n".join(lines)
