"""High level orchestration: map, schedule and build-samples for the sector projects."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import GENERATOR
from .export import export_csv
from .mapper import map_elements
from .model import (
    ElementsDoc, elements_from_dict, load_elements, load_mapping_rules, load_scenario,
    load_step_library, load_step_map, read_json, write_json,
)
from .scheduler import build_sequence
from .synth import GENERATORS
from .validate import validate_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECTORS_DIR = REPO_ROOT / "data" / "sectors"
FALLBACK_DIR = Path(__file__).resolve().parent / "fallback"
DEFAULT_TIMESTAMP = "2026-10-02T00:00:00Z"
SECTORS = ("industrial", "civil", "healthcare")


@dataclass
class SectorInputs:
    """Resolved file locations of one sector's library, rules and scenario."""

    library: Path
    rules: Path
    scenario: Path
    fallback: bool
    notes: list[str] = field(default_factory=list)


def resolve_sector_inputs(sector: str, sectors_dir: Path = DEFAULT_SECTORS_DIR,
                          require_real: bool = False) -> SectorInputs:
    """Prefer ``data/sectors/<sector>`` files; fall back to the bundled minimal data.

    The sector data is used only when all three files exist and pass schema validation.
    """
    base = Path(sectors_dir) / sector
    real = (base / "step_library.json", base / "mapping_rules.json", base / "scenario_standard.json")
    problems = [f"missing {p}" for p in real if not p.is_file()]
    if not problems:
        for p in real:
            ok, msgs = validate_file(p)
            if not ok:
                problems.append(f"invalid {p}: {msgs[0] if msgs else '?'}")
    if not problems:
        return SectorInputs(*real, fallback=False)
    if require_real:
        raise FileNotFoundError("; ".join(problems))
    return SectorInputs(FALLBACK_DIR / f"{sector}_step_library.json",
                        FALLBACK_DIR / f"{sector}_mapping_rules.json",
                        FALLBACK_DIR / f"{sector}_scenario.json", True, problems)


@dataclass
class BuildSummary:
    sector: str
    fallback: bool
    elements: int
    tasks: int
    links: int
    finish_day: int
    finish_week: int
    contract_weeks: int
    total_cost: float
    unmapped: int
    gaps: int
    warnings: list[str]
    notes: list[str]
    packages: int = 0

    def line(self) -> str:
        src = "FALLBACK data" if self.fallback else "sector data"
        return (f"{self.sector:10s} [{src}] elements={self.elements} tasks={self.tasks} links={self.links} "
                f"finish=day {self.finish_day} (week {self.finish_week}) contract={self.contract_weeks}w "
                f"cost={self.total_cost:,.0f} packages={self.packages} unmapped={self.unmapped} gaps={self.gaps}")


def run_map(elements_path: Path, rules_path: Path, library_path: Path, out_path: Path,
            generated_at: str | None = None) -> None:
    """``map`` subcommand body."""
    step_map = map_elements(load_elements(elements_path), load_step_library(library_path),
                            load_mapping_rules(rules_path), generated_at=generated_at,
                            step_library_ref=Path(library_path).name,
                            mapping_rules_ref=Path(rules_path).name)
    write_json(out_path, step_map.to_dict())


def run_schedule(map_path: Path, library_path: Path, scenario_path: Path, elements_path: Path,
                 out_path: Path, generated_at: str | None = None,
                 fractional_crews: bool = False) -> list[str]:
    """``schedule`` subcommand body; returns scheduler warnings."""
    bundle, warnings = build_sequence(load_step_map(map_path), load_step_library(library_path),
                                      load_scenario(scenario_path), load_elements(elements_path),
                                      generated_at=generated_at, fractional_crews=fractional_crews,
                                      generator=_generator(False, fractional_crews))
    write_json(out_path, bundle)
    return warnings


def _generator(fallback: bool, fractional: bool) -> str:
    extras = (["fallback sector data"] if fallback else []) + (["fractional crews"] if fractional else [])
    return GENERATOR + (f" [{', '.join(extras)}]" if extras else "")


def build_sector(sector: str, out_dir: Path, sectors_dir: Path = DEFAULT_SECTORS_DIR,
                 generated_at: str = DEFAULT_TIMESTAMP, require_real: bool = False,
                 seed: int = 42, fractional_crews: bool = False) -> BuildSummary:
    """Generate, map and schedule one sector; writes elements/element_step_map/sequence/csv."""
    inputs = resolve_sector_inputs(sector, sectors_dir, require_real)
    target = Path(out_dir) / sector
    elements_dict = GENERATORS[sector](seed)
    write_json(target / "elements.json", elements_dict)
    doc: ElementsDoc = elements_from_dict(elements_dict)
    library = load_step_library(inputs.library)
    rules = load_mapping_rules(inputs.rules)
    scenario = load_scenario(inputs.scenario)
    tag = "fallback" if inputs.fallback else "sector"
    step_map = map_elements(doc, library, rules, generated_at=generated_at,
                            step_library_ref=f"{tag}:{inputs.library.name}",
                            mapping_rules_ref=f"{tag}:{inputs.rules.name}")
    bundle, warnings = build_sequence(step_map, library, scenario, doc, generated_at=generated_at,
                                      generator=_generator(inputs.fallback, fractional_crews),
                                      fractional_crews=fractional_crews)
    write_json(target / "element_step_map.json", step_map.to_dict())   # after scheduling: gate_cycle gaps
    write_json(target / "sequence.json", bundle)
    export_csv(bundle, target / "sequence.csv")
    return BuildSummary(
        sector=sector, fallback=inputs.fallback, elements=len(doc.elements), tasks=len(bundle["tasks"]),
        links=sum(len(t["predecessors"]) for t in bundle["tasks"]),
        finish_day=bundle["baseline"]["finish_day"], finish_week=bundle["baseline"]["finish_week"],
        contract_weeks=bundle["scenario"]["contract_weeks"], total_cost=bundle["baseline"]["total_cost"],
        unmapped=len(step_map.unmapped_elements), gaps=len(step_map.sequencing_gaps),
        packages=len(bundle["packages"]),
        warnings=warnings, notes=inputs.notes)


def godot_scenario_dir_name(sector: str, scenario_id: str) -> str:
    """``<sector>_<scenario_id>``; the sector prefix is not repeated if the id already has it."""
    return scenario_id if scenario_id.startswith(sector) else f"{sector}_{scenario_id}"


def sync_godot(samples_dir: Path, godot_dir: Path) -> list[Path]:
    """Copy each sector sample's sequence.json to godot/scenarios/<sector>_<scenario_id>/."""
    written = []
    for seq_path in sorted(Path(samples_dir).glob("*/sequence.json")):
        data = read_json(seq_path)
        sector = data["project"]["sector"]
        if seq_path.parent.name != sector:
            continue            # e.g. the hand-authored 'minimal' sample is already in godot/scenarios
        name = godot_scenario_dir_name(sector, data["scenario"]["id"])
        dest = Path(godot_dir) / "scenarios" / name / "sequence.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seq_path, dest)
        written.append(dest)
    return written
