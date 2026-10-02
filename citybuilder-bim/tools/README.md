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
* Levelling releases a task only after all its predecessors are placed, so FF successors never start
  before their predecessor starts.

## Tests

```bash
cd tools && python3 -m unittest discover -s tests -v
```
