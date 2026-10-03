"""project_config.json: loader with schema defaults (docs/06 C.1)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .model import JSON, read_json
from .validate import schema_errors

DEFAULT_EXCLUDE = ["IfcAnnotation", "IfcGrid", "IfcOpeningElement", "IfcSpace", "IfcSite", "IfcBuilding",
                   "IfcBuildingStorey"]


@dataclass
class ChainageConfig:
    alignment_guid: str | None = None
    axis: str = "auto"
    cell_length_m: float = 25.0
    lanes: int = 6
    lane_width_m: float = 3.5


@dataclass
class GridConfig:
    mode: str = "auto"
    cell_size_m: float | None = None
    storey_height_m: float | None = None
    origin: list[float] | None = None
    rotation_deg: float | None = None
    snap_m: float = 0.5
    min_cell_m: float = 3.0
    max_cell_m: float = 12.0
    chainage: ChainageConfig = field(default_factory=ChainageConfig)


@dataclass
class ZonesConfig:
    source: str = "auto"
    file: str | None = None
    auto_block: tuple[int, int] = (3, 3)
    max_crews_default: int = 2
    space_tags: str | None = None
    merge_small_spaces_below_cells: int = 2


@dataclass
class AggregationConfig:
    preset: str | None = None
    max_members: int = 200
    threshold_per_cell: int = 25
    explicit: set[str] = field(default_factory=set)     # keys the file set explicitly (override the preset)


@dataclass
class FiltersConfig:
    exclude_ifc_classes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    include_ifc_classes: list[str] | None = None
    include_storeys: list[str] | None = None
    bbox: dict[str, list[float]] | None = None
    min_bbox_m: float = 0.05


@dataclass
class ScopeConfig:
    buildings: list[str] = field(default_factory=lambda: ["*"])
    systems: list[str] = field(default_factory=lambda: ["*"])


@dataclass
class OutputConfig:
    compress: bool = False
    tasks_per_part: int = 5000
    lazy_zone_detail: bool = False
    elements_per_part: int = 50000


@dataclass
class ProjectConfig:
    name: str | None = None
    sector: str | None = None
    grid: GridConfig = field(default_factory=GridConfig)
    zones: ZonesConfig = field(default_factory=ZonesConfig)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    areas: list[JSON] = field(default_factory=list)
    base_dir: Path = field(default_factory=Path)

    def aggregation_preset(self) -> str | dict[str, Any] | None:
        """Preset name, or the preset with explicit overrides from the file; None when aggregation is off."""
        a = self.aggregation
        if not a.preset:
            return None
        from .aggregation import resolve_preset
        cfg = dict(resolve_preset(a.preset))
        if "max_members" in a.explicit:
            cfg["max_members"] = a.max_members
        if "threshold_per_cell" in a.explicit:
            cfg["threshold_per_cell"] = a.threshold_per_cell
        return cfg

    def resolve(self, rel: str | None) -> Path | None:
        """A path from the config, relative to the config file's directory."""
        if not rel:
            return None
        p = Path(rel)
        return p if p.is_absolute() else self.base_dir / p


def from_dict(d: Mapping[str, Any], base_dir: Path | str = ".") -> ProjectConfig:
    g = d.get("grid", {})
    ch = g.get("chainage", {})
    z = d.get("zones", {})
    a = d.get("aggregation", {})
    f = d.get("filters", {})
    s = d.get("scope", {})
    o = d.get("output", {})
    return ProjectConfig(
        name=d.get("name"), sector=d.get("sector"),
        grid=GridConfig(
            mode=g.get("mode", "auto"), cell_size_m=g.get("cell_size_m"), storey_height_m=g.get("storey_height_m"),
            origin=list(g["origin"]) if g.get("origin") else None, rotation_deg=g.get("rotation_deg"),
            snap_m=float(g.get("snap_m", 0.5)), min_cell_m=float(g.get("min_cell_m", 3)),
            max_cell_m=float(g.get("max_cell_m", 12)),
            chainage=ChainageConfig(ch.get("alignment_guid"), ch.get("axis", "auto"), float(ch.get("cell_length_m", 25)),
                                    int(ch.get("lanes", 6)), float(ch.get("lane_width_m", 3.5)))),
        zones=ZonesConfig(source=z.get("source", "auto"), file=z.get("file"),
                          auto_block=tuple(z.get("auto_block", (3, 3))),            # type: ignore[arg-type]
                          max_crews_default=int(z.get("max_crews_default", 2)), space_tags=z.get("space_tags"),
                          merge_small_spaces_below_cells=int(z.get("merge_small_spaces_below_cells", 2))),
        aggregation=AggregationConfig(a.get("preset"), int(a.get("max_members", 200)),
                                      int(a.get("threshold_per_cell", 25)),
                                      {k for k in ("max_members", "threshold_per_cell") if k in a}),
        filters=FiltersConfig(list(f.get("exclude_ifc_classes", DEFAULT_EXCLUDE)), f.get("include_ifc_classes"),
                              f.get("include_storeys"), copy.deepcopy(f.get("bbox")), float(f.get("min_bbox_m", 0.05))),
        scope=ScopeConfig(list(s.get("buildings", ["*"])), list(s.get("systems", ["*"]))),
        output=OutputConfig(bool(o.get("compress", False)), int(o.get("tasks_per_part", 5000)),
                            bool(o.get("lazy_zone_detail", False)), int(o.get("elements_per_part", 50000))),
        areas=copy.deepcopy(list(d.get("areas", []))), base_dir=Path(base_dir))


def load_project_config(path: str | Path | None) -> ProjectConfig:
    """Load and schema-validate a project_config.json (defaults when ``path`` is None)."""
    if path is None:
        return ProjectConfig()
    data = read_json(path)
    msgs = schema_errors("project_config", data)
    if msgs:
        raise ValueError(f"{path}: invalid project config: {msgs[0]}")
    return from_dict(data, Path(path).parent)
