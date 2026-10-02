"""High level orchestration: map, schedule and build-samples for the sector projects."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import GENERATOR
from . import aggregation, logic, manual as manual_mod
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
    virtual_tasks: int = 0
    recipe_elements: int = 0
    recipe_errors: list[str] = field(default_factory=list)
    name: str = ""

    def line(self) -> str:
        src = "FALLBACK data" if self.fallback else "sector data"
        return (f"{(self.name or self.sector):10s} [{src}] elements={self.elements} tasks={self.tasks} links={self.links} "
                f"finish=day {self.finish_day} (week {self.finish_week}) contract={self.contract_weeks}w "
                f"cost={self.total_cost:,.0f} packages={self.packages} virtual={self.virtual_tasks} "
                f"recipe_elements={self.recipe_elements} unmapped={self.unmapped} gaps={self.gaps}")


def run_map(elements_path: Path, rules_path: Path, library_path: Path, out_path: Path,
            generated_at: str | None = None, manual_path: Path | None = None,
            aggregate: str | None = None, recipe_dirs: tuple = ()) -> None:
    """``map`` subcommand body."""
    doc = load_elements(elements_path)
    if aggregate:
        doc = aggregation.aggregate(doc, aggregate)
    recipes, _ = logic.load_recipes(recipe_dirs)
    step_map = map_elements(doc, load_step_library(library_path), load_mapping_rules(rules_path),
                            generated_at=generated_at, step_library_ref=Path(library_path).name,
                            mapping_rules_ref=Path(rules_path).name, recipes=recipes,
                            manual=manual_mod.load_manual(manual_path) if manual_path else None)
    _attach_aggregates(step_map, doc)
    write_json(out_path, step_map.to_dict())


def _attach_aggregates(step_map, doc) -> None:
    agg = aggregation.aggregate_map(doc)
    if agg:
        step_map.aggregates = agg
        step_map.aggregated_elements = [e.to_dict() for e in doc.elements if e.member_guids]


def run_schedule(map_path: Path, library_path: Path, scenario_path: Path, elements_path: Path,
                 out_path: Path, generated_at: str | None = None,
                 fractional_crews: bool = False, manual_path: Path | None = None,
                 recipe_dirs: tuple = ()) -> list[str]:
    """``schedule`` subcommand body; returns scheduler warnings.

    ``manual_path`` embeds the manual sequence in the bundle (it is merged into tasks by ``map``).
    """
    step_map = load_step_map(map_path)
    doc = load_elements(elements_path)
    if step_map.aggregates:
        doc = aggregation.apply_aggregates(doc, step_map.aggregates, step_map.aggregated_elements)
    recipes, _ = logic.load_recipes(recipe_dirs)
    used = {t.recipe_id for t in step_map.tasks if t.recipe_id}
    bundle, warnings = build_sequence(
        step_map, load_step_library(library_path), load_scenario(scenario_path), doc,
        generated_at=generated_at, fractional_crews=fractional_crews,
        generator=_generator(False, fractional_crews),
        recipes=logic.applicable_recipes(recipes, doc, used),
        manual=manual_mod.load_manual(manual_path) if manual_path else None)
    write_json(out_path, bundle)
    return warnings


def _generator(fallback: bool, fractional: bool) -> str:
    extras = (["fallback sector data"] if fallback else []) + (["fractional crews"] if fractional else [])
    return GENERATOR + (f" [{', '.join(extras)}]" if extras else "")


def build_sector(sector: str, out_dir: Path, sectors_dir: Path = DEFAULT_SECTORS_DIR,
                 generated_at: str = DEFAULT_TIMESTAMP, require_real: bool = False,
                 seed: int = 42, fractional_crews: bool = False, recipe_dirs: tuple = (),
                 manual: dict | None = None, aggregate: str | None = None, out_name: str | None = None,
                 scenario_patch: dict | None = None, elements_dict: dict | None = None) -> BuildSummary:
    """Generate, map and schedule one sector; writes elements/element_step_map/sequence/csv.

    ``manual`` (a parsed manual sequence) is merged into the tasks; ``out_name`` changes the output
    folder (e.g. ``healthcare_manual_demo``) and ``scenario_patch`` overrides scenario keys (id, name).
    ``elements_dict`` reuses an already generated elements document.
    """
    inputs = resolve_sector_inputs(sector, sectors_dir, require_real)
    target = Path(out_dir) / (out_name or sector)
    elements_dict = elements_dict or GENERATORS[sector](seed)
    write_json(target / "elements.json", elements_dict)
    doc: ElementsDoc = elements_from_dict(elements_dict)
    if aggregate:
        doc = aggregation.aggregate(doc, aggregate)
    library = load_step_library(inputs.library)
    rules = load_mapping_rules(inputs.rules)
    scenario = load_scenario(inputs.scenario)
    if scenario_patch:
        scenario.raw.update(scenario_patch)
    recipes, recipe_errors = logic.load_recipes(recipe_dirs)
    tag = "fallback" if inputs.fallback else "sector"
    step_map = map_elements(doc, library, rules, generated_at=generated_at,
                            step_library_ref=f"{tag}:{inputs.library.name}",
                            mapping_rules_ref=f"{tag}:{inputs.rules.name}", recipes=recipes, manual=manual)
    _attach_aggregates(step_map, doc)
    used = {t.recipe_id for t in step_map.tasks if t.recipe_id}
    bundle, warnings = build_sequence(step_map, library, scenario, doc, generated_at=generated_at,
                                      generator=_generator(inputs.fallback, fractional_crews),
                                      fractional_crews=fractional_crews,
                                      recipes=logic.applicable_recipes(recipes, doc, used), manual=manual)
    write_json(target / "element_step_map.json", step_map.to_dict())   # after scheduling: gate_cycle gaps
    write_json(target / "sequence.json", bundle)
    export_csv(bundle, target / "sequence.csv")
    zone_tags = {z.id: z.tags for z in doc.zones}
    matched = sum(1 for e in doc.elements
                  if logic.matching_recipes(recipes, e, zone_tags.get(e.zone_id, ()), sector))
    return BuildSummary(
        sector=sector, fallback=inputs.fallback, elements=len(doc.elements), tasks=len(bundle["tasks"]),
        links=sum(len(t["predecessors"]) for t in bundle["tasks"]),
        finish_day=bundle["baseline"]["finish_day"], finish_week=bundle["baseline"]["finish_week"],
        contract_weeks=bundle["scenario"]["contract_weeks"], total_cost=bundle["baseline"]["total_cost"],
        unmapped=len(step_map.unmapped_elements), gaps=len(step_map.sequencing_gaps),
        warnings=warnings, notes=inputs.notes, packages=len(bundle["packages"]),
        virtual_tasks=sum(1 for t in bundle["tasks"] if t.get("virtual")), recipe_elements=matched,
        recipe_errors=recipe_errors, name=out_name or "")


def build_manual_demo(out_dir: Path, samples_dir: Path | None = None, sectors_dir: Path = DEFAULT_SECTORS_DIR,
                      generated_at: str = DEFAULT_TIMESTAMP, fractional_crews: bool = False,
                      recipe_dirs: tuple = (), seed: int = 42) -> BuildSummary:
    """Write ``<out_dir>/healthcare/manual_demo.json`` and build the ``healthcare_manual_demo`` bundle."""
    sector = "healthcare"
    inputs = resolve_sector_inputs(sector, sectors_dir)
    elements_dict = GENERATORS[sector](seed)
    manual = manual_mod.demo(elements_from_dict(elements_dict), load_step_library(inputs.library))
    write_json(Path(out_dir) / sector / "manual_demo.json", manual)
    return build_sector(sector, out_dir, sectors_dir, generated_at, False, seed, fractional_crews, recipe_dirs,
                        manual=manual, out_name="healthcare_manual_demo", elements_dict=elements_dict,
                        scenario_patch={"id": "healthcare_manual_demo", "name": "Hospital wing (manual sequencing demo)",
                                        "description": "Standard healthcare scenario with one ground-floor zone in "
                                                       "manual mode (excavate, pile, survey, form, slab)."})


def godot_scenario_dir_name(sector: str, scenario_id: str) -> str:
    """``<sector>_<scenario_id>``; the sector prefix is not repeated if the id already has it."""
    return scenario_id if scenario_id.startswith(sector) else f"{sector}_{scenario_id}"


def sync_godot(samples_dir: Path, godot_dir: Path) -> list[Path]:
    """Copy each sector sample's sequence.json to godot/scenarios/<sector>_<scenario_id>/."""
    written = []
    for seq_path in sorted(Path(samples_dir).glob("*/sequence.json")):
        data = read_json(seq_path)
        sector = data["project"]["sector"]
        if seq_path.parent.name != sector and not seq_path.parent.name.startswith(sector + "_"):
            continue            # e.g. the hand-authored 'minimal' sample is already in godot/scenarios
        name = godot_scenario_dir_name(sector, data["scenario"]["id"])
        dest = Path(godot_dir) / "scenarios" / name / "sequence.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seq_path, dest)
        written.append(dest)
    return written
