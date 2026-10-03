"""High level orchestration: map, schedule and build-samples for the sector projects."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import GENERATOR
from . import aggregation, bundle as bundle_io, config as config_mod, logic, manual as manual_mod
from .export import export_csv
from .mapper import map_elements
from .model import (
    ElementsDoc, elements_from_dict, load_elements, load_mapping_rules, load_scenario,
    load_step_library, load_step_map, read_json, scenario_from_dict, step_library_from_dict, write_json,
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


MAX_FANIN_AGGREGATED = 8


def run_map(elements_path: Path, rules_path: Path, library_path: Path, out_path: Path,
            generated_at: str | None = None, manual_path: Path | None = None,
            aggregate: str | None = None, recipe_dirs: tuple = (), project_config: Path | None = None) -> None:
    """``map`` subcommand body. Aggregation: ``aggregate`` preset, else the project config's preset (off if none)."""
    cfg = config_mod.load_project_config(project_config)
    doc = load_elements(elements_path)
    preset = aggregate or cfg.aggregation_preset()
    if preset:
        doc = aggregation.aggregate(doc, preset)
    recipes, _ = logic.load_recipes(recipe_dirs)
    step_map = map_elements(doc, load_step_library(library_path), load_mapping_rules(rules_path),
                            generated_at=generated_at, step_library_ref=Path(library_path).name,
                            mapping_rules_ref=Path(rules_path).name, recipes=recipes,
                            manual=manual_mod.load_manual(manual_path) if manual_path else None,
                            max_fanin=MAX_FANIN_AGGREGATED if preset else None)
    _attach_aggregates(step_map, doc)
    if cfg.output.compress:
        bundle_io._dump(step_map.to_dict(), Path(out_path), True)
    else:
        write_json(out_path, step_map.to_dict())


def _attach_aggregates(step_map, doc) -> None:
    agg = aggregation.aggregate_map(doc)
    if agg:
        step_map.aggregates = agg
        step_map.aggregated_elements = [e.to_dict() for e in doc.elements if e.member_guids]


def run_schedule(map_path: Path, library_path: Path, scenario_path: Path, elements_path: Path,
                 out_path: Path, generated_at: str | None = None,
                 fractional_crews: bool = False, manual_path: Path | None = None,
                 recipe_dirs: tuple = (), project_config: Path | None = None) -> list[str]:
    """``schedule`` subcommand body; returns scheduler warnings.

    ``manual_path`` embeds the manual sequence in the bundle (it is merged into tasks by ``map``).
    """
    cfg = config_mod.load_project_config(project_config)
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
    out_path = Path(out_path)
    if cfg.output.compress or cfg.output.lazy_zone_detail:
        name = out_path.name[:-len(".json")] if out_path.name.endswith(".json") else out_path.stem
        bundle_io.write_bundle(bundle, out_path.parent, compress=cfg.output.compress,
                               tasks_per_part=cfg.output.tasks_per_part, lazy_zone_detail=cfg.output.lazy_zone_detail,
                               name=name)
    else:
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


def build_large(sector: str, elements_dict: dict | None, out_dir: Path, name: str, *, preset: str | dict | None,
                compress: bool = True, tasks_per_part: int = 5000, lazy_zone_detail: bool = True,
                max_fanin: int | None = 8, crew_scale: float = 1.0, scenario_patch: dict | None = None,
                sectors_dir: Path = DEFAULT_SECTORS_DIR, generated_at: str = DEFAULT_TIMESTAMP,
                recipe_dirs: tuple = (), write_elements: bool = True, fractional_crews: bool = True,
                log=None, doc: ElementsDoc | None = None) -> tuple[BuildSummary, dict[str, float]]:
    """Map and schedule a large (typically aggregated) project and write a compressed split bundle.

    Returns the summary and wall-clock seconds per stage (load, aggregate, map, schedule, write).
    ``crew_scale`` multiplies ``crews_available`` so the baseline stays sensible for big models.
    """
    def tick(label: str, t0: float, timings: dict[str, float]) -> float:
        now = time.perf_counter()
        timings[label] = round(now - t0, 2)
        if log:
            print(f"  {label}: {timings[label]}s", file=log)
        return now

    timings: dict[str, float] = {}
    t = time.perf_counter()
    inputs = resolve_sector_inputs(sector, sectors_dir)
    doc = doc if doc is not None else elements_from_dict(elements_dict)          # type: ignore[arg-type]
    library = load_step_library(inputs.library)
    rules = load_mapping_rules(inputs.rules)
    scenario = load_scenario(inputs.scenario)
    scenario.raw["crews_available"] = {k: max(1, round(v * crew_scale)) for k, v in scenario.crews_available.items()}
    if preset:        # aggregated tasks stand for whole groups: package by zone, phase and trade, split only when huge
        library.raw["packaging"] = {**library.raw.get("packaging", {}), "group_by": ["zone_id", "phase", "trade"],
                                    "max_crew_days_per_package": 100000, "target_duration_days": 20}
        library.packaging = step_library_from_dict(library.raw).packaging
    if scenario_patch:
        scenario.raw.update(scenario_patch)
    recipes, recipe_errors = logic.load_recipes(recipe_dirs)
    t = tick("load", t, timings)
    n_elements = len(doc.elements)
    if preset:
        doc = aggregation.aggregate(doc, preset)
    t = tick("aggregate", t, timings)
    step_map = map_elements(doc, library, rules, generated_at=generated_at, step_library_ref=f"sector:{inputs.library.name}",
                            mapping_rules_ref=f"sector:{inputs.rules.name}", recipes=recipes, max_fanin=max_fanin)
    _attach_aggregates(step_map, doc)
    t = tick("map", t, timings)
    used = {x.recipe_id for x in step_map.tasks if x.recipe_id}
    bundle, warnings = build_sequence(step_map, library, scenario, doc, generated_at=generated_at,
                                      generator=_generator(inputs.fallback, fractional_crews) + (" [aggregated]" if preset else ""),
                                      fractional_crews=fractional_crews,
                                      recipes=logic.applicable_recipes(recipes, doc, used))
    t = tick("schedule", t, timings)
    target = Path(out_dir) / name
    bundle_io.write_bundle(bundle, target, compress=compress, tasks_per_part=tasks_per_part,
                           lazy_zone_detail=lazy_zone_detail)
    sm_path = target / "element_step_map.json"
    bundle_io._dump(step_map.to_dict(), sm_path, compress)
    if write_elements and elements_dict is not None:
        bundle_io._dump(elements_dict, target / "elements.json", compress)
    tick("write", t, timings)
    summary = BuildSummary(
        sector=sector, fallback=inputs.fallback, elements=n_elements, tasks=len(bundle["tasks"]),
        links=sum(len(x["predecessors"]) for x in bundle["tasks"]), finish_day=bundle["baseline"]["finish_day"],
        finish_week=bundle["baseline"]["finish_week"], contract_weeks=bundle["scenario"]["contract_weeks"],
        total_cost=bundle["baseline"]["total_cost"], unmapped=len(step_map.unmapped_elements),
        gaps=len(step_map.sequencing_gaps), warnings=warnings, notes=inputs.notes, packages=len(bundle["packages"]),
        virtual_tasks=sum(1 for x in bundle["tasks"] if x.get("virtual")), recipe_errors=recipe_errors, name=name)
    summary.recipe_elements = len(doc.elements)       # elements after aggregation
    return summary, timings


BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"


def sync_split_bundle(bundle_dir: Path, godot_dir: Path, name: str = "stress_hospital") -> list[Path]:
    """Copy a split bundle (sequence.json[.gz], task parts, zone detail) to godot/scenarios/<name>/ (replacing it)."""
    dest = Path(godot_dir) / "scenarios" / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    written = []
    for f in sorted(Path(bundle_dir).iterdir()):
        if f.name.startswith(("sequence.json", "tasks.part-")):
            shutil.copyfile(f, dest / f.name)
            written.append(dest / f.name)
    zd = Path(bundle_dir) / "zones"
    if zd.is_dir():
        shutil.copytree(zd, dest / "zones")
        written.extend(sorted((dest / "zones").iterdir()))
    return written


def build_stress(wings_x: int, wings_z: int, out_dir: Path = BENCH_DIR / "work", name: str = "stress_hospital",
                 preset: str = "healthcare_mep", log=None, tasks_per_part: int = 5000
                 ) -> tuple[BuildSummary, dict[str, float]]:
    """Generate a ``wings_x x wings_z`` hospital campus (elements generator) and build the compressed split bundle."""
    from .synth import stress
    t = time.perf_counter()
    d = stress.generate(wings_x, wings_z)
    gen = round(time.perf_counter() - t, 2)
    n = wings_x * wings_z
    summary, timings = build_large(
        "healthcare", d, out_dir, name, preset=preset, crew_scale=n, tasks_per_part=tasks_per_part,
        scenario_patch={"id": name, "name": f"Hospital campus ({n} wings, stress)",
                        "description": f"Synthetic stress model: {n} hospital wings, aggregated ('{preset}')."},
        log=log, write_elements=False)
    return summary, {"generate": gen, **timings}


def build_stress_ifc(wings_x: int, wings_z: int, work_dir: Path = BENCH_DIR / "work", name: str = "stress_hospital",
                     log=None, reuse_ifc: bool = True) -> tuple[BuildSummary, dict[str, float]]:
    """End to end on a real IFC: synth-ifc -> ifc-to-elements (auto grid, space zoning, parts) -> aggregated map
    -> schedule -> compressed split bundle. Returns the summary and per-stage seconds."""
    from . import ifc_extract, synth_ifc
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    n = wings_x * wings_z
    ifc_path = work_dir / f"campus_{wings_x}x{wings_z}.ifc"
    timings: dict[str, float] = {}
    t = time.perf_counter()
    if not (reuse_ifc and ifc_path.exists()):
        doc = stress_doc = synth_ifc.stress.generate(wings_x, wings_z)
        info = synth_ifc.doc_to_ifc(doc, ifc_path, "healthcare", quantities="weight")
        del doc, stress_doc
        if log:
            print(f"  wrote {ifc_path} ({ifc_path.stat().st_size / 1e6:.0f} MB): {info}", file=log)
    timings["synth_ifc"] = round(time.perf_counter() - t, 2)
    cfg = config_mod.from_dict({"schema_version": "1.0", "sector": "healthcare",
                                "aggregation": {"preset": "healthcare_mep"},
                                "output": {"compress": True, "tasks_per_part": 5000, "lazy_zone_detail": True,
                                           "elements_per_part": 50000}})
    t = time.perf_counter()
    res = ifc_extract.extract_to(str(ifc_path), work_dir / "elements.json", sector="healthcare", config=cfg, log=log)
    timings["extract"] = round(time.perf_counter() - t, 2)
    if log:
        print(f"  extract: {res.count} elements, parts={len(res.files)}, {res.timings}", file=log)
    index = next(f for f in res.files if f.name.endswith(".index.json")) if len(res.files) > 1 else res.files[0]
    t = time.perf_counter()
    doc = load_elements(index)
    timings["load_elements"] = round(time.perf_counter() - t, 2)
    summary, tm = build_large("healthcare", None, work_dir, name, preset=cfg.aggregation_preset(), crew_scale=n,
                              tasks_per_part=cfg.output.tasks_per_part, lazy_zone_detail=cfg.output.lazy_zone_detail,
                              scenario_patch={"id": name, "name": f"Hospital campus ({n} wings, IFC stress)"},
                              write_elements=False, log=log, doc=doc)
    summary.elements = res.count
    return summary, {**timings, **{k: v for k, v in tm.items()}}


def run_rebuild_zone(map_path: Path, zone_id: str, manual_path: Path | None, *, bundle_path: Path | None = None,
                     elements_path: Path | None = None, rules_path: Path | None = None, library_path: Path | None = None,
                     scenario_path: Path | None = None, out_dir: Path | None = None, recipe_dirs: tuple = (),
                     fractional_crews: bool = False) -> dict[str, int]:
    """``rebuild-zone`` body: patch the bundle next to ``map_path`` (or ``bundle_path``) for one zone.

    Defaults: ``sequence.json[.gz]`` and ``elements.json[.gz]`` beside the map; rules, library and scenario from
    ``data/sectors/<sector>/`` (``scenario_standard.json`` or the bundle's own scenario id).
    """
    from .rebuild import rebuild_zone
    map_path = Path(map_path)
    base = map_path.parent
    step_map = load_step_map(map_path)
    bundle_path = bundle_path or next(p for p in (base / "sequence.json", base / "sequence.json.gz") if p.exists())
    old_bundle = bundle_io.load_bundle(bundle_path)
    elements_path = elements_path or next(p for p in (base / "elements.json", base / "elements.json.gz",
                                                       base / "elements.index.json") if p.exists())
    sector = step_map.sector
    sec = DEFAULT_SECTORS_DIR / sector
    library = step_library_from_dict_safe(library_path or sec / "step_library.json")
    rules = load_mapping_rules(rules_path or sec / "mapping_rules.json")
    scenario = scenario_from_dict(old_bundle["scenario"]) if scenario_path is None else load_scenario(scenario_path)
    recipes, _ = logic.load_recipes(recipe_dirs)
    manual = manual_mod.load_manual(manual_path) if manual_path else None
    bundle, new_map, stats = rebuild_zone(
        old_bundle, step_map, zone_id, load_elements(elements_path), library, rules, scenario, manual=manual,
        recipes=recipes, fractional_crews=fractional_crews or "fractional crews" in old_bundle.get("generator", ""),
        max_fanin=MAX_FANIN_AGGREGATED if step_map.aggregates else None)
    out_dir = Path(out_dir) if out_dir else base
    fmt = old_bundle.get("bundle_format") or {}
    name = bundle_path.name.split(".json")[0]
    if fmt.get("task_parts") or fmt.get("compressed"):
        bundle_io.write_bundle(bundle, out_dir, compress=bool(fmt.get("compressed")),
                               tasks_per_part=max(100, len(fmt.get("task_parts", [])) and
                                                  -(-len(bundle["tasks"]) // len(fmt["task_parts"]))),
                               lazy_zone_detail=bool(fmt.get("zone_detail_dir")), name=name)
    else:
        write_json(out_dir / bundle_path.name, bundle)
    bundle_io._dump(new_map.to_dict(), out_dir / (map_path.name.removesuffix(".gz").removesuffix(".json") + ".json"),
                    map_path.name.endswith(".gz"))
    return stats


def step_library_from_dict_safe(path: Path) -> StepLibrary:
    return load_step_library(path)


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
