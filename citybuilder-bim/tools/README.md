# bimseq: BIM to construction-sequence pipeline

Python 3.11, standard library plus `jsonschema` and `referencing`. `ifcopenshell` is optional and only
needed for `ifc-to-elements`.

```
IFC or synthetic generator -> elements.json -> map -> element_step_map.json -> schedule -> sequence.json
```

All commands run from this directory (`python3 -m bimseq ...`). JSON is written with `indent=1`, UTF-8,
`\n` newlines. Outputs are deterministic (seed 42, no wall-clock timestamps in `build-samples`).

## Commands

```bash
# Generate, map and schedule the three synthetic sector projects, then validate them
python3 -m bimseq build-samples ../data/samples          # [--sectors industrial civil healthcare]
python3 -m bimseq validate ../data/samples               # walks the tree, prints OK/FAIL, exit 1 on failure
python3 -m bimseq sync-godot ../data/samples ../godot    # -> godot/scenarios/<sector>_<scenario_id>/sequence.json

# Step by step with your own files
python3 -m bimseq ifc-to-elements model.ifc elements.json [--sector civil] [--cell-size 6]   # exit 3 without ifcopenshell
python3 -m bimseq map elements.json --rules mapping_rules.json --library step_library.json --out element_step_map.json
python3 -m bimseq schedule element_step_map.json --library step_library.json \
        --scenario scenario_standard.json --elements elements.json --out sequence.json
python3 -m bimseq export-csv sequence.json sequence.csv   # also accepts element_step_map.json
```

`build-samples` writes `<out>/<sector>/{elements,element_step_map,sequence}.json` and `sequence.csv`.
Inputs come from `data/sectors/<sector>/{step_library,mapping_rules,scenario_standard}.json`. If one of
the three is missing or fails schema validation it falls back to the minimal libraries in
`bimseq/fallback/` (clearly marked `FALLBACK` in file comments, scenario description and the bundle's
`generator`) and prints a warning. `--require-real` turns that into an error. `--sectors-dir` points
elsewhere.

`sync-godot` skips sample folders whose name is not a sector (the hand-authored `minimal`), never deletes
anything, and does not repeat the sector prefix when the scenario id already starts with it
(`civil_standard` stays `civil_standard`).

## Construction logic, virtual tasks and manual sequencing

```bash
python3 -m bimseq logic list [--sector healthcare]        # recipes from data/logic/recipes/**/*.json (+ --recipes-dir DIR)
python3 -m bimseq logic get rec_tank_ring_foundation
python3 -m bimseq logic index                             # writes data/logic/index.json (build-samples does too)
python3 -m bimseq logic explain --elements elements.json --guid GUID [--map element_step_map.json]
python3 -m bimseq logic explain --elements elements.json --zone L01-Z1 [--map ...] [--json]
python3 -m bimseq manual template --zone L00-Z3 --elements elements.json --library step_library.json [--rules mapping_rules.json] --out manual.json
python3 -m bimseq map elements.json --rules R --library L --manual manual.json --out map.json
python3 -m bimseq build-samples ../data/samples --manual-demo   # + healthcare/manual_demo.json and healthcare_manual_demo bundle
```

* A mapping rule with `recipe` expands the recipe for each matched element (after its own `steps`).
  `ref` steps bind per `from_element` (`self`; `foundation` = footing/base slab/pile overlapping the cells on
  the same else a lower storey, else self; `host`; `system` = one task per element of the system; `zone` =
  virtual task). `virtual` steps become tasks with `element_guid: null`, `virtual: true`, `origin: "recipe"`,
  `recipe_id`, `duration_days`, `marker` and the zone's cells. Inline `step` definitions are registered into the
  embedded library (and kept in the map as `inline_steps`). Nested recipes expand recursively (cycles become
  gaps `recipe_cycle`; unknown ids/steps become `recipe_ref`). An existing task for the same element+step is
  reused (linked, not duplicated).
* Order: steps chain FS with their `lag_days`; a `parallel_with` step is an SS side branch and not a chain
  member (the next step follows the one before it); `logic` entries add links by key or ref; `hold_point` sets
  `flags.inspection`. `optional` steps only come with `applied_recipes[].include_optional`.
* Manual sequences (`manual_sequence.json`): `zones_in_manual_mode` suppress generated tasks; manual tasks get
  `origin: "manual"` and `manual_id`; `element_guid` is the first element, or null (virtual) without elements;
  the same element+step replaces the generated task; `after` takes M ids or generated T ids (final numbering of
  the merged map; unknown ids give gap `manual_ref`); `overrides` suppress steps; `applied_recipes` expand
  recipes; `inherit_logic` (default true) applies library predecessor rules to manual tasks. When authored order
  conflicts with a library rule the library link is dropped (gap `cycle`). `schedule --manual` embeds the file
  as `sequence.manual` (merging happens in `map`).
* `sequence.json` embeds `recipes` (those matching any element or used by a task) and `manual`.

## Aggregation, grid detection

* `map --aggregate PRESET` (presets in `bimseq/aggregation_presets.json`, default off) folds groups of more than
  `max_members` elements sharing (storey, cell, visual_kit or class, system) into one aggregate element
  (`AGG-<hash>`, `member_guids`, summed quantities). The map keeps `aggregates` and `aggregated_elements`;
  `export-csv` expands every aggregate task to one row per member GUID with the aggregate's dates.
* `grid-detect elements.json|model.ifc` prints the estimated `cell_size_m` (median column nearest-neighbour
  spacing, 0.5 m snap, clamp 3..12, default 6), `rotation_deg` (dominant wall/beam direction modulo 90, 5 degree
  snap) and `origin` (site bbox minimum in the rotated frame). `ifc-to-elements` uses it by default
  (`--grid-mode auto`, `--project-config`); `--grid-mode fixed` keeps 6 m, no rotation.

## IFC, project config and scale (Phase 5)

`ifcopenshell` 0.9 is used when installed (without it `ifc-to-elements` exits 3).

```bash
python3 -m bimseq synth-ifc --sector healthcare --out model.ifc            # real IFC4 (civil: IFC4X3); --scale N = N-wing campus
python3 -m bimseq ifc-to-elements model.ifc elements.json --sector healthcare [--project-config project_config.json] [--compress]
python3 -m bimseq map elements.json --rules R --library L --out map.json [--project-config project_config.json]
python3 -m bimseq schedule map.json --library L --scenario S --elements elements.json --out sequence.json [--project-config ...]
python3 -m bimseq grid-detect model.ifc                                      # cell size / rotation / origin diagnostics
python3 -m bimseq rebuild-zone map.json --zone L01-S3 --manual manual.json   # patch one zone of the bundle beside the map
python3 -m bimseq stress --wings 6 6 --sync-godot ../godot                   # synthetic campus bundle (~30k elements)
python3 -m bimseq bench --wings 15 12 --from-ifc                             # ~150k elements through a real IFC; writes bench/last_bench.json
```

**project_config.json** (`schema/project_config.schema.json`, loader `bimseq/config.py`; all keys optional):

* `grid.mode`: `auto` (cell size = median `IfcColumn` nearest-neighbour spacing snapped to `snap_m`, clamped
  `min_cell_m`..`max_cell_m`; rotation = dominant wall/beam direction mod 90 degrees, 5 degree snap; origin = site bbox
  minimum in the rotated frame), `fixed` (`cell_size_m`, default 6, `rotation_deg`, `origin`) or `chainage` (cells
  along the alignment axis of `cell_length_m`, `lanes` lane cells of `lane_width_m` across; `axis` auto = the longer
  bbox axis; `cell_size_m` of the project becomes the cell length, `alignment_guid` is only recorded). The result and
  diagnostics are written to `project.grid` (`mode`, `rotation_deg`, `detected`).
* `zones.source`: `auto` (spaces when the model has any, else blocks), `ifc_space` (one zone per `IfcSpace`), `ifc_zone`
  (spaces of one `IfcZone` merge), `file` (rectangles from a CSV `zone_id,name,storey_id,x0,z0,x1,z1[,tags][,max_crews]`).
  Space names/long names/object types/properties go through `data/zoning/space_tags_<sector>.json` (first matching rule
  wins: tags, `max_crews`, `faces`, `shift_allowed`); spaces smaller than `merge_small_spaces_below_cells` merge into
  the neighbour they touch most; a cell claimed by two spaces belongs to the smaller; cells outside every space get
  `auto_block` zones (`<storey>-A<n>`).
* `filters` (exclude/include classes, include storeys by name or GlobalId, bbox, `min_bbox_m`), `scope` (buildings by
  name/GlobalId, systems), `aggregation.preset`, `output` (`compress`, `tasks_per_part`, `lazy_zone_detail`,
  `elements_per_part`), `areas`. With more than one `IfcBuilding`, `project.areas` lists each building (cells, storeys,
  camera bookmark).

**Extraction** reads relationships once (material, `IfcSystem`, voids/fills, property and quantity sets), runs the
geometry iterator on all CPUs for world-space bboxes (placement fallback), computes grid, zones and cells, then streams
elements in chunks. Above `elements_per_part` elements it writes `elements.part-N.json[.gz]` (each a complete elements
document) plus `elements.index.json`; `map` accepts the index. Quantities come from `IfcElementQuantity` when present,
else from the bbox.

**Aggregation presets** (`bimseq/aggregation_presets.json`: `generic`, `industrial_dense`, `healthcare_mep`,
`civil_linear`): a group (`group_by` of storey, cell, zone, kit_or_class, system) with more than `threshold_per_cell`
members folds into aggregates of at most `max_members`; `max_cells` bounds the cells kept on an aggregate. Aggregate
tasks last about as long as one member (members work concurrently) and in the fractional crew model occupy
`crew_days / duration` crews. `map` with an aggregation preset caps predecessor fan-in at 8 (an even sample of the
candidates, last one always kept), which keeps link counts linear. Packaging for aggregated builds groups by zone,
phase and trade.

**Recipe `anchor`** (mapping rule): `element` (default), `system`, `zone`, `cell_group` (4-neighbour contiguous cells,
split when storey, system or zone changes), `storey`, `project`. Virtual steps become one task per anchor group (cells =
zone cells, or the union of the group's cells); element-bound steps bind to all matched elements of the group by
`from_element`; consecutive element groups link per shared element instead of M x N.

**Split bundle** (`output.compress` / `lazy_zone_detail`; `bimseq/bundle.py`): `sequence.json[.gz]` holds everything
except `tasks` (empty) and `bundle_format {compressed, task_parts[], zone_detail_dir}`; `tasks.part-N.json[.gz]` is
`{schema_version, part, tasks[]}`; `zones/<zone_id>.json[.gz]` is `{zone_id, task_ids, package_ids, members}` where
`members` maps aggregate GUIDs to member GUIDs (moved out of the main file, which keeps `member_count`).
`load_bundle` merges everything back; `validate` and `export-csv` accept split bundles.

**rebuild-zone** re-maps with the manual sequence, keeps task and package ids outside the zone, gives the zone's tasks
and packages fresh ids, recomputes links both ways and the schedule, and rewrites sequence, map in place (or `--out-dir`).

## Crew model for levelling

`--crew-model whole` (default for `schedule`): every active task occupies one crew, so at most
`crews_available[trade]` tasks of a trade run at once (a missing trade, or 0, is treated as 1).
`--crew-model fractional` (default for `build-samples`): a task occupies `estimated_crew_days / duration`
crews, so tasks smaller than a crew-day share a crew. This matches the game, which tracks progress in
crew-days. With element-level tasks the `whole` model inflates baselines enormously (each of ~2000 tiny
tasks costs a whole crew-day), which would make the derived `contract_weeks` unplayable.

## Package layout

| Module | Purpose |
| --- | --- |
| `model.py` | dataclass loaders (`load_elements`, `load_step_library`, `load_mapping_rules`, `load_scenario`, `load_step_map`, `load_sequence`) with schema defaults applied |
| `validate.py` | schema validation via `validate_schemas.py`, plus task-graph cross checks |
| `ifc_extract.py` | IFC to `elements.json` (storeys, bbox cells, quantities, materials, systems, hosts, auto zones) |
| `mapper.py` | rule engine and predecessor scopes (docs/02 sections 3.1 and 4) |
| `scheduler.py` | CPM (FS/SS/FF + lag), gate milestones, greedy levelling, baseline, `sequence.json` |
| `synth/` | seeded synthetic BIM generators: `industrial`, `civil`, `healthcare` |
| `export.py` | CSV export |
| `pipeline.py`, `__main__.py` | orchestration and CLI |

### Mapper notes

* Rules sort by priority descending (file order breaks ties); first match wins unless `continue`.
  The default rule applies only when no rule matched (reason `default_rule`).
* A quantity of 0 becomes `max(min_quantity, 0.01)` and the element is listed with reason `no_quantity`.
* Rule-level `visual` overrides are stored in the optional top-level `element_visuals` map of
  `element_step_map.json` and applied when `sequence.json` is built.
* Links are deduplicated per (predecessor, successor, type) keeping the larger lag. A link that closes a
  cycle is dropped and recorded in `sequencing_gaps` with note `cycle`; unmatched `required` rules are
  recorded with note `required predecessor not found`.

### Scheduler notes

* `duration = max(min_duration_days, ceil(estimated_crew_days))`; a task occupies `[start, finish)`.
* Gates are cumulative (docs/02 section 3.2) and become one synthetic milestone per (gate, scope
  instance): every task with phase order <= `after_phase` in the instance feeds it, and every task with
  order >= `before_phase` waits for it. An instance with no task at or below `after_phase` is vacuous.
  Milestones are not emitted. A gate instance that would create a cycle with task links is disabled with a
  warning and recorded in `sequencing_gaps` with note `gate_cycle` (in `element_step_map.json` written by
  `build-samples`).
* `total_float_days` and `is_critical` come from the unlevelled CPM; `planned_*` and `baseline.finish_*`
  from the levelled schedule. `weekly_planned_cost[w]` is the cumulative cost at the end of week `w`,
  `w = 0..finish_week`. `contract_weeks = ceil(finish_week * contract_factor)` and
  `budget = round(total_cost * budget_factor)` fill in when the scenario has `null` / `0`.
* Money rules (applied to the scenario copy embedded in `sequence.json`). Labour is
  `estimated_crew_days * trade.weekly_cost / 5` per task (8000/week for a trade missing from the library),
  reported as `baseline.total_labour_cost`; `baseline.total_cost` stays material cost only, and
  `weekly_planned_cost` accrues materials plus labour. With `budget == 0`:
  `budget = round((total_cost + total_labour_cost) * budget_factor * 1.05)` (5% mobilisation allowance). Labour is crew-days × weekly cost / 5 divided by an expected utilisation of 0.65 (`LABOUR_UTILISATION`), because crews are paid for whole weeks including time waiting on gates, inspections and deliveries. A step library may override it with `labour_utilisation` (civil uses 0.4 because staged traffic work idles crews).
  With `W = (total_cost + total_labour_cost) / finish_week`, `start_cash` is raised to at least `8 * W`
  and `overdraft_limit` to at least `4 * W`; each raise prints a `note:` line. Explicit budgets and larger
  cash values are left alone.
* Levelling releases a task only after all its predecessors are placed, so FF successors never start
  before their predecessor starts.

## Tests

```bash
cd tools && python3 -m unittest discover -s tests -v
```
