"""Command line interface: ``python3 -m bimseq <command> ...`` (see tools/README.md)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import logic, pipeline
from .export import export_csv
from .validate import validate_tree

EXIT_NO_IFCOPENSHELL = 3
CREW_MODELS = ("whole", "fractional")
CREW_HELP = ("levelling crew model: 'whole' = each active task occupies one crew; 'fractional' = a task "
             "occupies estimated_crew_days/duration crews so sub-day tasks share a crew")


def _recipe_dirs(args: argparse.Namespace) -> tuple:
    return tuple(getattr(args, "recipes_dir", None) or ())


def _cmd_ifc(args: argparse.Namespace) -> int:
    from . import ifc_extract
    if not ifc_extract.available():
        print("error: ifcopenshell is not installed; install it (pip install ifcopenshell) to read IFC "
              "files. Synthetic samples do not need it: use 'build-samples'.", file=sys.stderr)
        return EXIT_NO_IFCOPENSHELL
    doc = ifc_extract.extract(args.ifc, sector=args.sector, cell_size_m=args.cell_size,
                              zone_block=args.zone_block, max_crews=args.max_crews, grid_mode=args.grid_mode,
                              project_config=ifc_extract.load_project_config(args.project_config))
    from .model import write_json
    write_json(args.out, doc)
    print(f"wrote {args.out}: {len(doc['elements'])} elements, {len(doc['zones'])} zones, "
          f"{len(doc['storeys'])} storeys")
    return 0


def _cmd_map(args: argparse.Namespace) -> int:
    pipeline.run_map(args.elements, args.rules, args.library, args.out, args.generated_at, args.manual,
                     args.aggregate, _recipe_dirs(args))
    from .model import load_step_map
    m = load_step_map(args.out)
    print(f"wrote {args.out}: {len(m.tasks)} tasks ({sum(1 for t in m.tasks if t.virtual)} virtual), "
          f"{len(m.unmapped_elements)} unmapped entries, {len(m.sequencing_gaps)} sequencing gaps"
          + (f", {len(m.aggregates)} aggregates" if m.aggregates else ""))
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    for w in pipeline.run_schedule(args.map, args.library, args.scenario, args.elements, args.out,
                                   args.generated_at, args.crew_model == "fractional", args.manual,
                                   _recipe_dirs(args)):
        print(w if w.startswith("note:") else f"warning: {w}", file=sys.stderr)
    from .model import load_sequence
    b = load_sequence(args.out).baseline
    print(f"wrote {args.out}: finish day {b['finish_day']} (week {b['finish_week']}), "
          f"cost {b['total_cost']:,.0f}, {len(b['critical_task_ids'])} critical tasks")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    n = export_csv(args.source, args.out)
    print(f"wrote {args.out}: {n} rows")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    rc = 0
    frac = args.crew_model == "fractional"
    for sector in args.sectors:
        try:
            s = pipeline.build_sector(sector, args.out_dir, args.sectors_dir, args.generated_at,
                                      args.require_real, args.seed, frac, _recipe_dirs(args),
                                      aggregate=args.aggregate)
        except FileNotFoundError as exc:
            print(f"error: {sector}: sector data unavailable ({exc})", file=sys.stderr)
            rc = 1
            continue
        for note in s.notes:
            print(f"WARNING {sector}: using FALLBACK data ({note})", file=sys.stderr)
        for w in s.warnings:
            print(f"{sector}: {w}" if w.startswith("note:") else f"warning {sector}: {w}", file=sys.stderr)
        for e in s.recipe_errors[:5]:
            print(f"warning recipes: {e}", file=sys.stderr)
        print(s.line())
    if args.manual_demo:
        s = pipeline.build_manual_demo(args.out_dir, None, args.sectors_dir, args.generated_at, frac,
                                       _recipe_dirs(args), args.seed)
        print(s.line())
    if not args.no_logic_index:
        recipes, errors = logic.load_recipes(_recipe_dirs(args))
        for e in errors[:5]:
            print(f"warning recipes: {e}", file=sys.stderr)
        idx = logic.write_index(recipes, args.logic_index)
        print(f"logic index: {idx['recipe_count']} recipes -> {args.logic_index}")
    return rc


def _cmd_logic(args: argparse.Namespace) -> int:
    import json
    from .model import load_elements, load_step_map
    recipes, errors = logic.load_recipes(_recipe_dirs(args))
    for e in errors[:10]:
        print(f"warning: {e}", file=sys.stderr)
    if args.logic_cmd == "index":
        idx = logic.write_index(recipes, args.out)
        print(f"wrote {args.out}: {idx['recipe_count']} recipes")
        return 1 if errors else 0
    if args.logic_cmd == "list":
        rows = [r for r in recipes.values() if not args.sector or r["sector"] in (args.sector, "all")]
        for r in sorted(rows, key=lambda r: r["id"]):
            virt = sum(1 for s in r["steps"] if s.get("virtual"))
            print(f"{r['id']:38s} {r['sector']:10s} {len(r['steps']):2d} steps ({virt} virtual)  {r['name']}")
        print(f"{len(rows)} recipes")
        return 0
    if args.logic_cmd == "get":
        if args.id not in recipes:
            print(f"error: unknown recipe {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(recipes[args.id], indent=1))
        return 0
    doc = load_elements(args.elements)
    index = logic.ElementIndex(doc)
    step_map = load_step_map(args.map) if args.map else None
    if bool(args.guid) == bool(args.zone):
        print("error: give exactly one of --guid or --zone", file=sys.stderr)
        return 2
    if args.guid:
        if args.guid not in index.by_guid:
            print(f"error: unknown element {args.guid}", file=sys.stderr)
            return 1
        items = logic.explain_element(recipes, index, args.guid, step_map)
    else:
        if args.zone not in doc.zone_by_id():
            print(f"error: unknown zone {args.zone}", file=sys.stderr)
            return 1
        items = logic.explain_zone(recipes, index, args.zone, step_map)
    print(json.dumps(items, indent=1) if args.json else logic.format_explanation(items))
    return 0


def _cmd_manual(args: argparse.Namespace) -> int:
    from . import manual as manual_mod
    from .model import load_elements, load_mapping_rules, load_step_library, write_json
    doc = load_elements(args.elements)
    lib = load_step_library(args.library)
    recipes, _ = logic.load_recipes(_recipe_dirs(args))
    data = manual_mod.template(doc, lib, args.zone, load_mapping_rules(args.rules) if args.rules else None, recipes)
    write_json(args.out, data)
    print(f"wrote {args.out}: zone {args.zone}, {len(data['tasks'])} suggested tasks")
    return 0


def _cmd_grid(args: argparse.Namespace) -> int:
    import json
    from . import grid_detect
    path = str(args.source)
    if path.lower().endswith(".ifc"):
        from . import ifc_extract
        if not ifc_extract.available():
            print("error: ifcopenshell is not installed; pass an elements.json instead", file=sys.stderr)
            return EXIT_NO_IFCOPENSHELL
        doc = ifc_extract.extract(path, grid_mode="auto")
        res = dict(doc["project"]["grid"])
    else:
        from .model import load_elements
        res = grid_detect.detect_grid(grid_detect.items_from_elements(load_elements(path)))
    print(json.dumps(res, indent=1))
    return 0


def _cmd_packages(args: argparse.Namespace) -> int:
    from .model import read_json
    data = read_json(args.seq)
    pkgs = [p for p in data.get("packages", []) if not args.zone or p["zone_id"] == args.zone]
    if not pkgs:
        print("no packages", file=sys.stderr)
        return 1
    print(f"{'package':8s} {'zone':9s} {'phase':18s} {'trade':14s} {'face':13s} {'tasks':>5s} {'crew-d':>7s} "
          f"{'min/id/max':>10s} {'start':>5s} {'end':>5s}")
    for p in pkgs[:args.limit] if args.limit else pkgs:
        cp = p["crew_profile"]
        print(f"{p['package_id']:8s} {p['zone_id']:9s} {p['phase'][:18]:18s} {p['trade'][:14]:14s} "
              f"{p['work_face']:13s} {len(p['task_ids']):5d} {p['total_crew_days']:7.1f} "
              f"{str(cp['min']) + '/' + str(cp['ideal']) + '/' + str(cp['max']):>10s} {p.get('planned_start_day', 0):5d} {p.get('planned_finish_day', 0):5d}")
    total = len(data.get("packages", []))
    print(f"{len(pkgs)} packages shown ({total} total, {len(data['tasks'])} tasks)")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    return 0 if validate_tree(args.path) else 1


def _cmd_sync(args: argparse.Namespace) -> int:
    paths = pipeline.sync_godot(args.samples_dir, args.godot_dir)
    for p in paths:
        print(f"copied -> {p}")
    if not paths:
        print("nothing to sync", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bimseq", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ifc-to-elements", help="extract elements.json from an IFC file (needs ifcopenshell)")
    p.add_argument("ifc", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--sector", default="industrial", choices=pipeline.SECTORS)
    p.add_argument("--cell-size", type=float, default=None, help="grid cell size in metres (default: detected)")
    p.add_argument("--zone-block", type=int, nargs=2, default=(3, 3), metavar=("W", "D"))
    p.add_argument("--max-crews", type=int, default=2)
    p.add_argument("--grid-mode", choices=("auto", "fixed"), default="auto",
                   help="auto: detect cell size, rotation and origin; fixed: --cell-size (6 m), no rotation")
    p.add_argument("--project-config", default=None, help="project_config.json (grid block)")
    p.set_defaults(fn=_cmd_ifc)

    p = sub.add_parser("map", help="elements.json + rules + library -> element_step_map.json")
    p.add_argument("elements", type=Path)
    p.add_argument("--rules", type=Path, required=True)
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--generated-at", default=None)
    p.add_argument("--manual", type=Path, default=None, help="manual_sequence.json to merge")
    p.add_argument("--aggregate", default=None, help="aggregation preset name (default: off)")
    p.add_argument("--recipes-dir", type=Path, action="append", help="extra recipe directory (repeatable)")
    p.set_defaults(fn=_cmd_map)

    p = sub.add_parser("schedule", help="element_step_map.json -> sequence.json (CPM + levelling)")
    p.add_argument("map", type=Path)
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--scenario", type=Path, required=True)
    p.add_argument("--elements", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--generated-at", default=None)
    p.add_argument("--crew-model", choices=CREW_MODELS, default="whole",
                   help=CREW_HELP + " (default: whole)")
    p.add_argument("--manual", type=Path, default=None, help="manual_sequence.json to embed in the bundle")
    p.add_argument("--recipes-dir", type=Path, action="append", help="extra recipe directory (repeatable)")
    p.set_defaults(fn=_cmd_schedule)

    p = sub.add_parser("export-csv", help="sequence.json or element_step_map.json -> CSV")
    p.add_argument("source", type=Path)
    p.add_argument("out", type=Path)
    p.set_defaults(fn=_cmd_export)

    p = sub.add_parser("build-samples", help="generate, map and schedule the synthetic sector projects")
    p.add_argument("out_dir", type=Path)
    p.add_argument("--sectors", nargs="+", choices=pipeline.SECTORS, default=list(pipeline.SECTORS))
    p.add_argument("--sectors-dir", type=Path, default=pipeline.DEFAULT_SECTORS_DIR)
    p.add_argument("--require-real", action="store_true",
                   help="fail instead of falling back to the bundled minimal sector data")
    p.add_argument("--generated-at", default=pipeline.DEFAULT_TIMESTAMP)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--crew-model", choices=CREW_MODELS, default="fractional",
                   help=CREW_HELP + " (default: fractional, matching the game's crew-day progress model)")
    p.add_argument("--manual-demo", action="store_true",
                   help="also write healthcare/manual_demo.json and build the healthcare_manual_demo bundle")
    p.add_argument("--aggregate", default=None, help="aggregation preset (default: off)")
    p.add_argument("--recipes-dir", type=Path, action="append", help="extra recipe directory (repeatable)")
    p.add_argument("--no-logic-index", action="store_true", help="do not (re)write data/logic/index.json")
    p.add_argument("--logic-index", type=Path, default=logic.DEFAULT_INDEX_PATH)
    p.set_defaults(fn=_cmd_build)

    p = sub.add_parser("logic", help="construction logic library: index, list, get, explain")
    lsub = p.add_subparsers(dest="logic_cmd", required=True)
    lp = lsub.add_parser("index", help="write the recipe index (default data/logic/index.json)")
    lp.add_argument("--out", type=Path, default=logic.DEFAULT_INDEX_PATH)
    lp = lsub.add_parser("list", help="list recipes")
    lp.add_argument("--sector", choices=pipeline.SECTORS, default=None)
    lp = lsub.add_parser("get", help="print one recipe")
    lp.add_argument("id")
    lp = lsub.add_parser("explain", help="which recipes apply to an element or zone, and what is covered")
    lp.add_argument("--elements", type=Path, required=True)
    lp.add_argument("--guid", default=None)
    lp.add_argument("--zone", default=None)
    lp.add_argument("--map", type=Path, default=None, help="element_step_map.json for coverage")
    lp.add_argument("--json", action="store_true")
    for lp in lsub.choices.values():
        lp.add_argument("--recipes-dir", type=Path, action="append", help="extra recipe directory (repeatable)")
    p.set_defaults(fn=_cmd_logic)

    p = sub.add_parser("manual", help="manual sequencing helpers")
    msub = p.add_subparsers(dest="manual_cmd", required=True)
    mp = msub.add_parser("template", help="write a starter manual_sequence.json for a zone")
    mp.add_argument("--zone", required=True)
    mp.add_argument("--elements", type=Path, required=True)
    mp.add_argument("--library", type=Path, required=True)
    mp.add_argument("--rules", type=Path, default=None, help="mapping rules: suggest the generated chain")
    mp.add_argument("--out", type=Path, required=True)
    mp.add_argument("--recipes-dir", type=Path, action="append")
    p.set_defaults(fn=_cmd_manual)

    p = sub.add_parser("grid-detect", help="estimate cell size, rotation and origin (elements.json or .ifc)")
    p.add_argument("source", type=Path)
    p.set_defaults(fn=_cmd_grid)

    p = sub.add_parser("packages", help="print the package table of a sequence.json")
    p.add_argument("seq", type=Path)
    p.add_argument("--zone", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=_cmd_packages)

    p = sub.add_parser("validate", help="validate every recognised *.json under a directory")
    p.add_argument("path", type=Path)
    p.set_defaults(fn=_cmd_validate)

    p = sub.add_parser("sync-godot", help="copy sample sequence.json files to godot/scenarios/")
    p.add_argument("samples_dir", type=Path)
    p.add_argument("godot_dir", type=Path)
    p.set_defaults(fn=_cmd_sync)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as exc:     # mapping/scheduling/model errors
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
