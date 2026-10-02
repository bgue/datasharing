"""Command line interface: ``python3 -m bimseq <command> ...`` (see tools/README.md)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import pipeline
from .export import export_csv
from .validate import validate_tree

EXIT_NO_IFCOPENSHELL = 3
CREW_MODELS = ("whole", "fractional")
CREW_HELP = ("levelling crew model: 'whole' = each active task occupies one crew; 'fractional' = a task "
             "occupies estimated_crew_days/duration crews so sub-day tasks share a crew")


def _cmd_ifc(args: argparse.Namespace) -> int:
    from . import ifc_extract
    if not ifc_extract.available():
        print("error: ifcopenshell is not installed; install it (pip install ifcopenshell) to read IFC "
              "files. Synthetic samples do not need it: use 'build-samples'.", file=sys.stderr)
        return EXIT_NO_IFCOPENSHELL
    doc = ifc_extract.extract(args.ifc, sector=args.sector, cell_size_m=args.cell_size,
                              zone_block=args.zone_block, max_crews=args.max_crews)
    from .model import write_json
    write_json(args.out, doc)
    print(f"wrote {args.out}: {len(doc['elements'])} elements, {len(doc['zones'])} zones, "
          f"{len(doc['storeys'])} storeys")
    return 0


def _cmd_map(args: argparse.Namespace) -> int:
    pipeline.run_map(args.elements, args.rules, args.library, args.out, args.generated_at)
    from .model import load_step_map
    m = load_step_map(args.out)
    print(f"wrote {args.out}: {len(m.tasks)} tasks, {len(m.unmapped_elements)} unmapped entries, "
          f"{len(m.sequencing_gaps)} sequencing gaps")
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    for w in pipeline.run_schedule(args.map, args.library, args.scenario, args.elements, args.out,
                                   args.generated_at, args.crew_model == "fractional"):
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
    for sector in args.sectors:
        try:
            s = pipeline.build_sector(sector, args.out_dir, args.sectors_dir, args.generated_at,
                                      args.require_real, args.seed, args.crew_model == "fractional")
        except FileNotFoundError as exc:
            print(f"error: {sector}: sector data unavailable ({exc})", file=sys.stderr)
            rc = 1
            continue
        for note in s.notes:
            print(f"WARNING {sector}: using FALLBACK data ({note})", file=sys.stderr)
        for w in s.warnings:
            print(f"{sector}: {w}" if w.startswith("note:") else f"warning {sector}: {w}", file=sys.stderr)
        print(s.line())
    return rc


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
    p.add_argument("--cell-size", type=float, default=6.0, help="grid cell size in metres")
    p.add_argument("--zone-block", type=int, nargs=2, default=(3, 3), metavar=("W", "D"))
    p.add_argument("--max-crews", type=int, default=2)
    p.set_defaults(fn=_cmd_ifc)

    p = sub.add_parser("map", help="elements.json + rules + library -> element_step_map.json")
    p.add_argument("elements", type=Path)
    p.add_argument("--rules", type=Path, required=True)
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--generated-at", default=None)
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
    p.set_defaults(fn=_cmd_build)

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
