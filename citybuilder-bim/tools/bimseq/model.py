"""Typed dataclass loaders for the six SiteBuilder JSON documents.

Schema defaults (requires_access=true, chain=true, link type FS, ...) are applied at load
time so the rest of the pipeline never has to repeat them.  Every loader accepts either a
parsed ``dict`` (``*_from_dict``) or a path (``load_*``).
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

JSON = dict[str, Any]
Cell = tuple[int, int]

BASIS_UNIT: dict[str, str] = {
    "volume_m3": "m3", "area_m2": "m2", "length_m": "m", "count": "ea", "weight_t": "t",
}


class ModelError(ValueError):
    """Raised when a document is structurally unusable (beyond what the schema checks)."""


# --------------------------------------------------------------------------- JSON io
def read_json(path: str | Path) -> JSON:
    """Read a UTF-8 JSON file."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, data: Any) -> None:
    """Write JSON per project conventions: indent=1, keys unsorted, UTF-8, ``\\n`` newlines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, sort_keys=False, ensure_ascii=False)
        fh.write("\n")


def _cells(raw: Iterable[Iterable[int]]) -> list[Cell]:
    return [(int(c[0]), int(c[1])) for c in raw]


def _cells_json(cells: Iterable[Cell]) -> list[list[int]]:
    return [[c[0], c[1]] for c in cells]


# --------------------------------------------------------------------------- elements
@dataclass
class Storey:
    id: str
    name: str
    index: int
    elevation_m: float | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Storey":
        return cls(d["id"], d["name"], int(d["index"]), d.get("elevation_m"))

    def to_dict(self) -> JSON:
        out: JSON = {"id": self.id, "name": self.name, "index": self.index}
        if self.elevation_m is not None:
            out["elevation_m"] = self.elevation_m
        return out


@dataclass
class Zone:
    id: str
    name: str
    storey_id: str
    cells: list[Cell]
    max_crews: int = 2
    tags: list[str] = field(default_factory=list)
    faces: dict[str, int] = field(default_factory=dict)      # per work-face crew cap; missing = no cap
    shift_allowed: bool = True

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Zone":
        return cls(d["id"], d["name"], d["storey_id"], _cells(d["cells"]), int(d["max_crews"]),
                   list(d.get("tags", [])), {k: int(v) for k, v in d.get("faces", {}).items()},
                   bool(d.get("shift_allowed", True)))

    def to_dict(self) -> JSON:
        out: JSON = {"id": self.id, "name": self.name, "storey_id": self.storey_id,
                     "cells": _cells_json(self.cells), "max_crews": self.max_crews, "tags": list(self.tags)}
        if self.faces:
            out["faces"] = dict(self.faces)
        if not self.shift_allowed:
            out["shift_allowed"] = False
        return out


@dataclass
class System:
    id: str
    name: str
    discipline: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "System":
        return cls(d["id"], d["name"], d["discipline"])

    def to_dict(self) -> JSON:
        return {"id": self.id, "name": self.name, "discipline": self.discipline}


@dataclass
class Element:
    guid: str
    ifc_class: str
    name: str
    storey_id: str
    zone_id: str
    cells: list[Cell]
    quantities: dict[str, float]
    predefined_type: str | None = None
    system_id: str | None = None
    host_guid: str | None = None
    material: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    bbox: dict[str, list[float]] | None = None
    visual: str | None = None
    visual_kit: str | None = None
    member_guids: list[str] = field(default_factory=list)       # aggregate elements only

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Element":
        return cls(
            guid=d["guid"], ifc_class=d["ifc_class"], name=d["name"], storey_id=d["storey_id"],
            zone_id=d["zone_id"], cells=_cells(d["cells"]),
            quantities={k: float(v) for k, v in d["quantities"].items()},
            predefined_type=d.get("predefined_type"), system_id=d.get("system_id"),
            host_guid=d.get("host_guid"), material=d.get("material"),
            properties=dict(d.get("properties", {})), bbox=copy.deepcopy(d.get("bbox")),
            visual=d.get("visual"), visual_kit=d.get("visual_kit"),
            member_guids=list(d.get("member_guids", [])),
        )

    def to_dict(self) -> JSON:
        out: JSON = {
            "guid": self.guid, "ifc_class": self.ifc_class, "predefined_type": self.predefined_type,
            "name": self.name, "storey_id": self.storey_id, "zone_id": self.zone_id,
            "system_id": self.system_id, "host_guid": self.host_guid,
            "cells": _cells_json(self.cells), "quantities": dict(self.quantities),
            "material": self.material, "properties": dict(self.properties),
        }
        if self.bbox is not None:
            out["bbox"] = self.bbox
        if self.visual is not None:
            out["visual"] = self.visual
        if self.visual_kit is not None:
            out["visual_kit"] = self.visual_kit
        if self.member_guids:
            out["member_guids"] = list(self.member_guids)
        return out


@dataclass
class ElementsDoc:
    project: JSON
    storeys: list[Storey]
    zones: list[Zone]
    systems: list[System]
    elements: list[Element]

    def storey_by_id(self) -> dict[str, Storey]:
        return {s.id: s for s in self.storeys}

    def zone_by_id(self) -> dict[str, Zone]:
        return {z.id: z for z in self.zones}

    def to_dict(self) -> JSON:
        return {
            "schema_version": "1.0", "project": copy.deepcopy(self.project),
            "storeys": [s.to_dict() for s in self.storeys], "zones": [z.to_dict() for z in self.zones],
            "systems": [s.to_dict() for s in self.systems],
            "elements": [e.to_dict() for e in self.elements],
        }


def elements_from_dict(d: Mapping[str, Any]) -> ElementsDoc:
    doc = ElementsDoc(
        project=copy.deepcopy(d["project"]),
        storeys=[Storey.from_dict(s) for s in d["storeys"]],
        zones=[Zone.from_dict(z) for z in d["zones"]],
        systems=[System.from_dict(s) for s in d.get("systems", [])],
        elements=[Element.from_dict(e) for e in d["elements"]],
    )
    ids = {s.id for s in doc.storeys}
    for e in doc.elements:
        if e.storey_id not in ids:
            raise ModelError(f"element {e.guid}: unknown storey_id {e.storey_id!r}")
    return doc


def load_elements(path: str | Path) -> ElementsDoc:
    """Load an elements document: ``.json``, ``.json.gz`` or an ``elements.index.json`` listing part files."""
    from .bundle import read_any_json
    path = Path(path)
    data = read_any_json(path)
    if "parts" in data and "elements" not in data:
        head: dict[str, Any] | None = None
        elements: list[Any] = []
        for rel in data["parts"]:
            part = read_any_json(path.parent / rel)
            head = head or {k: v for k, v in part.items() if k != "elements"}
            elements.extend(part["elements"])
        data = {**(head or {}), "elements": elements}
    return elements_from_dict(data)


# --------------------------------------------------------------------------- step library
@dataclass
class Phase:
    id: str
    name: str
    order: int


@dataclass
class Trade:
    id: str
    name: str
    weekly_cost: float
    crew_size: int = 4
    max_hire_per_week: int = 2


@dataclass
class PredRule:
    step: str
    scope: str
    type: str = "FS"
    lag_days: int = 0
    required: bool = False


@dataclass
class CrewProfile:
    """Per-step crew demand; ``ideal``/``max`` are None unless the step states them."""

    min: int = 1
    ideal: int | None = None
    max: int | None = None


@dataclass
class Packaging:
    """step_library.packaging with schema defaults."""

    group_by: list[str] = field(default_factory=lambda: ["zone_id", "phase", "trade", "work_face"])
    target_duration_days: int = 10
    max_crew_days_per_package: float = 60.0
    max_over_ideal: int = 1
    over_ideal_factor: float = 0.6


@dataclass
class Step:
    id: str
    name: str
    phase: str
    trade: str
    discipline: str
    quantity_basis: str
    rate_per_crew_day: float
    unit_cost: float
    min_duration_days: int = 1
    requires_crane: bool = False
    requires_access: bool = True
    laydown_cells: int = 0
    lead_time_weeks: int = 0
    inspection: bool = False
    inspection_type: str | None = None
    risk: float = 0.1
    weather_sensitive: bool = False
    noisy: bool = False
    dusty: bool = False
    progress_visual: str = "solid"
    predecessors: list[PredRule] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    work_face: str = "any"
    crew_profile: CrewProfile = field(default_factory=CrewProfile)

    @property
    def default_unit(self) -> str:
        return BASIS_UNIT[self.quantity_basis]

    def flags(self) -> JSON:
        """The task ``flags`` object derived from this step."""
        return {
            "requires_crane": self.requires_crane, "requires_access": self.requires_access,
            "inspection": self.inspection, "inspection_type": self.inspection_type,
            "lead_time_weeks": self.lead_time_weeks, "laydown_cells": self.laydown_cells,
            "risk": self.risk, "weather_sensitive": self.weather_sensitive,
            "noisy": self.noisy, "dusty": self.dusty,
        }


@dataclass
class Gate:
    id: str
    name: str
    after_phase: str
    before_phase: str
    scope: str
    requires_inspection_types: list[str] = field(default_factory=list)


@dataclass
class StepLibrary:
    sector: str
    phases: list[Phase]
    trades: dict[str, Trade]
    steps: dict[str, Step]
    gates: list[Gate]
    raw: JSON = field(default_factory=dict, repr=False)
    packaging: Packaging = field(default_factory=Packaging)
    exclusive_faces: list[str] = field(default_factory=lambda: ["floor"])
    sequence_cards: list[JSON] = field(default_factory=list)
    labour_utilisation: float = 0.65

    def to_dict(self) -> JSON:
        return copy.deepcopy(self.raw)

    def register_step(self, step_dict: Mapping[str, Any]) -> Step:
        """Add an inline step definition (idempotent by id); updates both the typed and raw library."""
        sid = step_dict["id"]
        if sid not in self.steps:
            self.steps[sid] = step_from_dict(step_dict)
            self.raw.setdefault("steps", []).append(copy.deepcopy(dict(step_dict)))
        return self.steps[sid]


def _crew_profile(d: Mapping[str, Any] | None) -> CrewProfile:
    d = d or {}
    return CrewProfile(int(d.get("min", 1)), d.get("ideal"), d.get("max"))


def step_from_dict(s: Mapping[str, Any]) -> Step:
    """Build a :class:`Step` (schema defaults applied) from a step_library ``steps[]`` entry."""
    preds = [PredRule(p["step"], p["scope"], p.get("type", "FS"), int(p.get("lag_days", 0)),
                      bool(p.get("required", False))) for p in s.get("predecessors", [])]
    return Step(
        id=s["id"], name=s["name"], phase=s["phase"], trade=s["trade"], discipline=s["discipline"],
        quantity_basis=s["quantity_basis"], rate_per_crew_day=float(s["rate_per_crew_day"]),
        unit_cost=float(s["unit_cost"]), min_duration_days=int(s.get("min_duration_days", 1)),
        requires_crane=bool(s.get("requires_crane", False)),
        requires_access=bool(s.get("requires_access", True)),
        laydown_cells=int(s.get("laydown_cells", 0)), lead_time_weeks=int(s.get("lead_time_weeks", 0)),
        inspection=bool(s.get("inspection", False)), inspection_type=s.get("inspection_type"),
        risk=float(s.get("risk", 0.1)), weather_sensitive=bool(s.get("weather_sensitive", False)),
        noisy=bool(s.get("noisy", False)), dusty=bool(s.get("dusty", False)),
        progress_visual=s.get("progress_visual", "solid"), predecessors=preds,
        tags=list(s.get("tags", [])), work_face=s.get("work_face", "any"),
        crew_profile=_crew_profile(s.get("crew_profile")),
    )


def step_library_from_dict(d: Mapping[str, Any]) -> StepLibrary:
    phases = [Phase(p["id"], p["name"], int(p["order"])) for p in d["phases"]]
    trades = {
        t["id"]: Trade(t["id"], t["name"], float(t["weekly_cost"]), int(t.get("crew_size", 4)),
                       int(t.get("max_hire_per_week", 2)))
        for t in d["trades"]
    }
    steps: dict[str, Step] = {}
    for sd in d["steps"]:
        steps[sd["id"]] = step_from_dict(sd)
    gates = [Gate(g["id"], g["name"], g["after_phase"], g["before_phase"], g["scope"],
                  list(g.get("requires_inspection_types", []))) for g in d.get("gates", [])]
    pk = d.get("packaging", {})
    packaging = Packaging(
        group_by=list(pk.get("group_by", ["zone_id", "phase", "trade", "work_face"])),
        target_duration_days=int(pk.get("target_duration_days", 10)),
        max_crew_days_per_package=float(pk.get("max_crew_days_per_package", 60)),
        max_over_ideal=int(pk.get("max_over_ideal", 1)),
        over_ideal_factor=float(pk.get("over_ideal_factor", 0.6)))
    return StepLibrary(d["sector"], phases, trades, steps, gates, copy.deepcopy(dict(d)), packaging,
                       list(d.get("exclusive_faces", ["floor"])), copy.deepcopy(list(d.get("sequence_cards", []))),
                       float(d.get("labour_utilisation", 0.65)))


def load_step_library(path: str | Path) -> StepLibrary:
    return step_library_from_dict(read_json(path))


# --------------------------------------------------------------------------- mapping rules
@dataclass
class Match:
    ifc_class: list[str] | None = None
    predefined_type: list[str | None] | None = None
    name_regex: re.Pattern[str] | None = None
    material_regex: re.Pattern[str] | None = None
    storey_index_min: int | None = None
    storey_index_max: int | None = None
    zone_tags_any: list[str] | None = None
    properties: dict[str, Any] | None = None
    properties_present: list[str] | None = None
    quantity_min: dict[str, float] | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Match":
        return cls(
            ifc_class=d.get("ifc_class"), predefined_type=d.get("predefined_type"),
            name_regex=re.compile(d["name_regex"]) if "name_regex" in d else None,
            material_regex=re.compile(d["material_regex"]) if "material_regex" in d else None,
            storey_index_min=d.get("storey_index_min"), storey_index_max=d.get("storey_index_max"),
            zone_tags_any=d.get("zone_tags_any"), properties=d.get("properties"),
            properties_present=d.get("properties_present"), quantity_min=d.get("quantity_min"),
        )


@dataclass
class Emit:
    step: str
    quantity: str
    quantity_factor: float = 1.0
    unit_override: str | None = None
    lag_days: int = 0
    min_quantity: float = 0.0

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Emit":
        return cls(d["step"], d["quantity"], float(d.get("quantity_factor", 1.0)),
                   d.get("unit_override"), int(d.get("lag_days", 0)), float(d.get("min_quantity", 0)))


@dataclass
class Rule:
    id: str
    priority: int
    match: Match
    steps: list[Emit]
    continue_: bool = False
    chain: bool = True
    visual: str | None = None
    system_prefix: str | None = None
    description: str = ""
    recipe: str | None = None
    visual_kit: str | None = None
    anchor: str = "element"


@dataclass
class MappingRules:
    sector: str
    rules: list[Rule]
    default: Rule
    step_library: str | None = None

    def ordered(self) -> list[Rule]:
        """Rules by descending priority; file order breaks ties (stable sort)."""
        return sorted(self.rules, key=lambda r: -r.priority)


DEFAULT_RULE_ID = "R-default"


def mapping_rules_from_dict(d: Mapping[str, Any]) -> MappingRules:
    rules = [
        Rule(id=r["id"], priority=int(r["priority"]), match=Match.from_dict(r["match"]),
             steps=[Emit.from_dict(e) for e in r.get("steps", [])], continue_=bool(r.get("continue", False)),
             chain=bool(r.get("chain", True)), visual=r.get("visual"),
             system_prefix=r.get("system_prefix"), description=r.get("description", ""),
             recipe=r.get("recipe"), visual_kit=r.get("visual_kit"), anchor=r.get("anchor", "element"))
        for r in d["rules"]
    ]
    dflt = d["default"]
    default = Rule(id=DEFAULT_RULE_ID, priority=-(10 ** 9), match=Match(),
                   steps=[Emit.from_dict(e) for e in dflt["steps"]], visual=dflt.get("visual"))
    return MappingRules(d["sector"], rules, default, d.get("step_library"))


def load_mapping_rules(path: str | Path) -> MappingRules:
    return mapping_rules_from_dict(read_json(path))


# --------------------------------------------------------------------------- scenario
@dataclass
class Scenario:
    """Scenario document; ``raw`` is authoritative, typed accessors cover what the pipeline needs."""

    raw: JSON

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def sector(self) -> str:
        return self.raw["sector"]

    @property
    def crews_available(self) -> dict[str, int]:
        return dict(self.raw.get("crews_available", {}))

    @property
    def contract_weeks(self) -> int | None:
        return self.raw.get("contract_weeks")

    @property
    def contract_factor(self) -> float:
        return float(self.raw.get("contract_factor", 1.1))

    @property
    def sequence_cards(self) -> list[JSON]:
        return copy.deepcopy(list(self.raw.get("sequence_cards", [])))

    @property
    def budget(self) -> float:
        return float(self.raw.get("budget", 0))

    @property
    def budget_factor(self) -> float:
        return float(self.raw.get("budget_factor", 1.15))

    def to_dict(self) -> JSON:
        return copy.deepcopy(self.raw)


def scenario_from_dict(d: Mapping[str, Any]) -> Scenario:
    return Scenario(copy.deepcopy(dict(d)))


def load_scenario(path: str | Path) -> Scenario:
    return scenario_from_dict(read_json(path))


# --------------------------------------------------------------------------- tasks / map / sequence
@dataclass
class Predecessor:
    task_id: str
    type: str = "FS"
    lag_days: int = 0
    reason: str = ""

    def to_dict(self) -> JSON:
        out: JSON = {"task_id": self.task_id, "type": self.type, "lag_days": self.lag_days}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class Task:
    task_id: str
    element_guid: str | None
    ifc_class: str
    element_name: str
    storey_id: str
    zone_id: str
    system_id: str | None
    step_id: str
    phase: str
    trade: str
    quantity: float
    unit: str
    estimated_crew_days: float
    cost: float
    cells: list[Cell]
    flags: JSON
    predecessors: list[Predecessor]
    rule_id: str
    planned_start_day: int | None = None
    planned_finish_day: int | None = None
    actual_start_day: int | None = None
    actual_finish_day: int | None = None
    is_critical: bool | None = None
    total_float_days: int | None = None
    package_id: str | None = None
    work_face: str | None = None
    virtual: bool | None = None
    origin: str | None = None
    recipe_id: str | None = None
    duration_days: int | None = None
    marker: str | None = None
    manual_id: str | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Task":
        return cls(
            task_id=d["task_id"], element_guid=d["element_guid"], ifc_class=d["ifc_class"],
            element_name=d["element_name"], storey_id=d["storey_id"], zone_id=d["zone_id"],
            system_id=d.get("system_id"), step_id=d["step_id"], phase=d["phase"], trade=d["trade"],
            quantity=float(d["quantity"]), unit=d["unit"],
            estimated_crew_days=float(d["estimated_crew_days"]), cost=float(d["cost"]),
            cells=_cells(d["cells"]), flags=dict(d["flags"]),
            predecessors=[Predecessor(p["task_id"], p.get("type", "FS"), int(p.get("lag_days", 0)),
                                      p.get("reason", "")) for p in d.get("predecessors", [])],
            rule_id=d["rule_id"], planned_start_day=d.get("planned_start_day"),
            planned_finish_day=d.get("planned_finish_day"),
            actual_start_day=d.get("actual_start_day"), actual_finish_day=d.get("actual_finish_day"),
            is_critical=d.get("is_critical"), total_float_days=d.get("total_float_days"),
            package_id=d.get("package_id"), work_face=d.get("work_face"),
            virtual=d.get("virtual"), origin=d.get("origin"), recipe_id=d.get("recipe_id"),
            duration_days=d.get("duration_days"), marker=d.get("marker"), manual_id=d.get("manual_id"),
        )

    def to_dict(self) -> JSON:
        out: JSON = {
            "task_id": self.task_id, "element_guid": self.element_guid, "ifc_class": self.ifc_class,
            "element_name": self.element_name, "storey_id": self.storey_id, "zone_id": self.zone_id,
            "system_id": self.system_id, "step_id": self.step_id, "phase": self.phase,
            "trade": self.trade, "quantity": self.quantity, "unit": self.unit,
            "estimated_crew_days": self.estimated_crew_days, "cost": self.cost,
            "cells": _cells_json(self.cells), "flags": dict(self.flags),
            "predecessors": [p.to_dict() for p in self.predecessors], "rule_id": self.rule_id,
            "planned_start_day": self.planned_start_day, "planned_finish_day": self.planned_finish_day,
            "actual_start_day": self.actual_start_day, "actual_finish_day": self.actual_finish_day,
        }
        if self.work_face is not None:
            out["work_face"] = self.work_face
        for key in ("virtual", "origin", "recipe_id", "duration_days", "marker", "manual_id"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.package_id is not None:
            out["package_id"] = self.package_id
        if self.is_critical is not None:
            out["is_critical"] = self.is_critical
        if self.total_float_days is not None or self.is_critical is not None:
            out["total_float_days"] = self.total_float_days
        return out


@dataclass
class StepMap:
    """element_step_map.json: tasks without dates, plus authoring feedback."""

    project: JSON
    sector: str
    generated_at: str
    tasks: list[Task]
    generator: str = ""
    step_library_ref: str | None = None
    mapping_rules_ref: str | None = None
    unmapped_elements: list[JSON] = field(default_factory=list)
    sequencing_gaps: list[JSON] = field(default_factory=list)
    element_visuals: dict[str, str] = field(default_factory=dict)
    element_visual_kits: dict[str, str] = field(default_factory=dict)
    inline_steps: list[JSON] = field(default_factory=list)          # steps defined inline by recipes
    aggregates: dict[str, list[str]] = field(default_factory=dict)  # aggregate guid -> member guids
    aggregated_elements: list[JSON] = field(default_factory=list)   # the aggregate element records

    def to_dict(self) -> JSON:
        out: JSON = {
            "schema_version": "1.0", "project": copy.deepcopy(self.project), "sector": self.sector,
            "generated_at": self.generated_at, "generator": self.generator,
        }
        if self.step_library_ref:
            out["step_library_ref"] = self.step_library_ref
        if self.mapping_rules_ref:
            out["mapping_rules_ref"] = self.mapping_rules_ref
        out["tasks"] = [t.to_dict() for t in self.tasks]
        out["unmapped_elements"] = list(self.unmapped_elements)
        out["sequencing_gaps"] = list(self.sequencing_gaps)
        # Extension keys (the schema allows extra properties):
        if self.element_visuals:
            out["element_visuals"] = dict(self.element_visuals)       # rule-level visual overrides
        if self.element_visual_kits:
            out["element_visual_kits"] = dict(self.element_visual_kits)
        if self.inline_steps:
            out["inline_steps"] = copy.deepcopy(self.inline_steps)
        if self.aggregates:
            out["aggregates"] = {k: list(v) for k, v in self.aggregates.items()}
            out["aggregated_elements"] = copy.deepcopy(self.aggregated_elements)
        return out


def step_map_from_dict(d: Mapping[str, Any]) -> StepMap:
    return StepMap(
        project=copy.deepcopy(d["project"]), sector=d["sector"], generated_at=d["generated_at"],
        tasks=[Task.from_dict(t) for t in d["tasks"]], generator=d.get("generator", ""),
        step_library_ref=d.get("step_library_ref"), mapping_rules_ref=d.get("mapping_rules_ref"),
        unmapped_elements=list(d.get("unmapped_elements", [])),
        sequencing_gaps=list(d.get("sequencing_gaps", [])),
        element_visuals=dict(d.get("element_visuals", {})),
        element_visual_kits=dict(d.get("element_visual_kits", {})),
        inline_steps=copy.deepcopy(list(d.get("inline_steps", []))),
        aggregates={k: list(v) for k, v in d.get("aggregates", {}).items()},
        aggregated_elements=copy.deepcopy(list(d.get("aggregated_elements", []))),
    )


def load_step_map(path: str | Path) -> StepMap:
    from .bundle import read_any_json
    return step_map_from_dict(read_any_json(path))


@dataclass
class Sequence:
    """sequence.json game bundle; tasks are typed, the remaining sections stay as JSON."""

    data: JSON
    tasks: list[Task]

    @property
    def baseline(self) -> JSON:
        return self.data["baseline"]

    @property
    def scenario(self) -> Scenario:
        return scenario_from_dict(self.data["scenario"])

    @property
    def step_library(self) -> StepLibrary:
        return step_library_from_dict(self.data["step_library"])

    def to_dict(self) -> JSON:
        out = dict(self.data)
        out["tasks"] = [t.to_dict() for t in self.tasks]
        return out


def sequence_from_dict(d: Mapping[str, Any]) -> Sequence:
    return Sequence(dict(d), [Task.from_dict(t) for t in d["tasks"]])


def load_sequence(path: str | Path) -> Sequence:
    return sequence_from_dict(read_json(path))
