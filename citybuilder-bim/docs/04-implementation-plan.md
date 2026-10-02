# 04 — Implementation Plan and Work Packages

Three work packages run in parallel against the schemas in `../schema/`
(the contract) and the hand-authored `data/samples/minimal/` bundle. A fourth
integration pass runs the full pipeline and the headless game tests.

## WP-A Python pipeline (`tools/bimseq/`)

Deliverables:
1. `bimseq/model.py` typed loaders for all six schemas (dataclasses), `bimseq/validate.py`
   wrapping `tools/validate_schemas.py`.
2. `bimseq/ifc_extract.py`: optional `ifcopenshell` import; extracts storeys, elements,
   quantities (from `IfcElementQuantity` or bbox fallback), materials, systems
   (`IfcRelAssignsToGroup`/`IfcSystem`), host (`IfcRelFillsElement`/`IfcRelVoidsElement`),
   grid placement from bbox. Zones auto-generated per storey as N×M blocks of cells.
3. `bimseq/mapper.py`: rule engine per `02-sequencing-model.md` §3.1, §4. Deterministic
   task IDs (sorted by storey index, zone, element guid, chain order). Emits
   `element_step_map.json` with `unmapped_elements` and `sequencing_gaps`.
4. `bimseq/scheduler.py`: CPM forward/backward pass with FS/SS/FF + lag, gate links,
   greedy weekly crew levelling against `scenario.crews_available`, critical path,
   weekly cumulative cost; emits `sequence.json`.
5. `bimseq/synth/{industrial,civil,healthcare}.py`: synthetic BIM generators producing
   `elements.json` with realistic class mix and quantities (200–900 elements each),
   zones with tags, systems, long-lead equipment.
6. `bimseq/export.py`: CSV for scheduling tools; `bimseq/__main__.py` CLI:
   `ifc-to-elements`, `map`, `schedule`, `build-samples`, `validate`, `sync-godot`
   (copies `sequence.json` into `godot/scenarios/<id>/`).
7. `tools/tests/` unittest suite: rule matching, scope resolution per scope type, chain
   links, CPM on a hand graph, levelling never exceeds availability, every generated
   sample validates, round trip export/import.

## WP-B Sector content (`data/sectors/<sector>/`)

Deliverables per sector: `step_library.json` (25–60 steps, trades, phases, gates),
`mapping_rules.json` covering §4.1 classes, three `scenario_*.json`
(tutorial/standard/hard) with sector events, equipment, site, hints. All validate.
Rates and costs should be plausible order-of-magnitude (cite source assumptions in
`data/sectors/README.md`).

## WP-C Godot game (`godot/`)

Deliverables per `03-architecture.md`: all scripts, scenes, UI, tests. Loads any bundle
in `res://scenarios/`. Playable end to end on `minimal` with keyboard/mouse. Exports
`plan_export.json`. Headless test runner passes.

## WP-D Integration (after A–C)

Run `build-samples`, `validate`, `sync-godot`, headless Godot import + tests on every
scenario; fix cross-package mismatches; update README with real numbers.

## Conventions

* Python 3.11, stdlib + `jsonschema` only (`ifcopenshell` optional). `unittest`.
* GDScript 4.6, static typing, `snake_case`, one class per file, no plugins.
* All JSON written with `indent=1`, sorted keys off, UTF-8, `\n` newlines.
* Days are working days from day 0; weeks are 5 days; `week = day // 5`.
* Deterministic outputs: seeded RNG (`seed=42`) in generators and sim tests.
