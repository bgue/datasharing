# 02 — Sequencing Model and BIM Element-to-Step Mapping

This document defines the data model that turns a BIM model into a playable,
schedulable construction sequence. Everything here is machine-readable; the
JSON Schemas in `../schema/` are normative, this document explains them.

## 1. Pipeline

```
IFC file ──(ifc_to_elements)──> elements.json
                                    │
      step_library.json ─┐          │
      mapping_rules.json ─┴──(map)──┴──> element_step_map.json   (tasks, no dates)
                                              │
      scenario.json ──────────(schedule)──────┴──> sequence.json  (game bundle, planned dates)
                                                        │
                                              Godot game plays it
                                                        │
                                              plan_export.json  (element_step_map + actual dates)
```

| File | Schema | Produced by | Consumed by |
| --- | --- | --- | --- |
| `elements.json` | `elements.schema.json` | IFC extractor or synthetic generator | mapper |
| `step_library.json` | `step_library.schema.json` | sector author (one per sector) | mapper, scheduler, game |
| `mapping_rules.json` | `mapping_rules.schema.json` | sector author | mapper |
| `scenario.json` | `scenario.schema.json` | level designer | scheduler, game |
| `element_step_map.json` | `element_step_map.schema.json` | mapper | scheduler, external 4D tools |
| `sequence.json` | `sequence.schema.json` | scheduler | game |
| `plan_export.json` | `element_step_map.schema.json` | game | external 4D tools, scoring |

## 2. Spatial model: the grid

The game world is a GridMap. The pipeline places every element on it.

* **Cell**: `[x, z]` integer pair. One cell is one structural bay,
  `project.grid.cell_size_m` wide (default 6 m) on each side.
* **Storey**: `storey_id` with `index` (0 = ground, negative = below grade).
  One storey is one vertical GridMap layer. Civil projects use a single
  storey and model chainage along `x`.
* **Zone**: a named set of cells on one storey (`zones[]`). Zones are the unit
  of work release and congestion. A good zone is 4–12 cells. Zones carry
  `max_crews` and `tags` (e.g. `occupied_adjacent`, `live_traffic`,
  `heavy_lift_area`).
* **Element footprint**: `elements[].cells` on the element's storey. Vertical
  elements (columns, risers) that span storeys are split per storey by the
  extractor, one element record each, linked by `properties.ParentGuid`.

Mapping from model coordinates: `cell_x = floor((x - origin.x) / cell_size_m)`,
`cell_z = floor((y_model_north - origin.z) / cell_size_m)`, storey by elevation.

## 3. Step library

A step is a *type* of construction activity. Steps are sector-specific but
share one vocabulary. Step IDs are `DISCIPLINE-OBJECT-ACTION`, upper case,
hyphenated, e.g. `STR-SLAB-POUR`, `MEP-DUCT-INSTALL`, `CIV-EARTH-CUT`.

Key fields (see schema for all):

| Field | Meaning |
| --- | --- |
| `phase` | Macro-sequence bucket; gates are per phase |
| `trade` | Crew type that performs it |
| `quantity_basis` | Which element quantity drives duration: `volume_m3`, `area_m2`, `length_m`, `count`, `weight_t` |
| `rate_per_crew_day` | Units of `quantity_basis` one crew completes in a day |
| `unit_cost` | Material + subcontract cost per unit |
| `requires_crane`, `requires_access`, `laydown_cells` | Logistics demands |
| `lead_time_weeks` | Procurement lead; 0 for stock materials |
| `inspection`, `inspection_type` | Hold point after completion |
| `risk`, `weather_sensitive`, `noisy`, `dusty` | Event and safety hooks |
| `predecessors[]` | Dependency *rules*, resolved per element by the mapper |

### 3.1 Predecessor rules and scopes

A predecessor rule says "before step `A` on element `E` can start, all tasks of
step `B` within `scope` of `E` must be finished (plus `lag_days`)". The mapper
resolves rules into concrete task-to-task links. If no task matches, the rule
is silently skipped unless `required: true`, in which case the mapper reports
a sequencing gap.

| Scope | Tasks of step B considered |
| --- | --- |
| `same_element` | on the same element GUID (used to chain form → rebar → pour) |
| `host` | on the element's `host_guid` (window → wall) |
| `same_cell` | on elements overlapping any of E's cells, same storey |
| `same_cell_below` | overlapping cells, storey index − 1 |
| `same_cell_above` | overlapping cells, storey index + 1 (e.g. remove formwork after slab above is self-supporting; demolition top-down) |
| `same_zone` | in E's zone |
| `same_zone_below` | in the zone(s) covering E's cells on storey − 1 |
| `same_storey` | on E's storey |
| `same_storey_below` | on storey − 1 |
| `same_system` | with the same `system_id` (MEP systems, process units) |
| `project` | anywhere in the project |

Link types are `FS` (default), `SS`, `FF`. `lag_days` may be negative for
overlap (e.g. MEP rough-in may start SS+5 after framing).

### 3.2 Gates

A gate is a cumulative phase hold point. `after_phase` is the last phase that
must be finished; `before_phase` is the first phase that is held. Within each
scope instance (one zone, one storey, or the project):

* every task whose phase order is **≤ order(after_phase)** must be finished
  (and inspected, where the step has an inspection) before
* any task whose phase order is **≥ order(before_phase)** may start.

Because gates are cumulative, a phase that is not named directly by any gate
is still held by every gate whose `before_phase` is at or below it. A scope
instance with no tasks at or below `after_phase` has a vacuous gate (it
passes immediately). Gates are evaluated at run time by the game and used by
the scheduler as additional FS links through one synthetic milestone per
(gate, scope instance). Scope: `zone`, `storey`, `project`.

## 4. Mapping rules

A rule matches elements and emits one or more steps per matched element.

```json
{
  "id": "R-slab-suspended",
  "priority": 100,
  "match": {
    "ifc_class": ["IfcSlab"],
    "predefined_type": ["FLOOR"],
    "storey_index_min": 1,
    "material_regex": "(?i)concrete"
  },
  "steps": [
    {"step": "STR-SLAB-FORM",  "quantity": "area_m2"},
    {"step": "STR-SLAB-REBAR", "quantity": "volume_m3", "quantity_factor": 0.11, "unit_override": "t"},
    {"step": "STR-SLAB-POUR",  "quantity": "volume_m3"},
    {"step": "STR-SLAB-STRIKE","quantity": "area_m2", "lag_days": 7}
  ]
}
```

Rules are evaluated in descending `priority`; the first match wins unless
`continue: true`. Steps listed in one rule are chained FS in order on the
same element (`chain: true`, default) so form → rebar → pour → strike is
implicit. The `default` rule catches anything else (so every element is
always buildable), and such elements are listed in `unmapped_elements` with
reason `default_rule` for authoring feedback.

### 4.1 IFC coverage expected per sector

| Sector | Must handle |
| --- | --- |
| All | IfcFooting, IfcPile, IfcSlab, IfcColumn, IfcBeam, IfcMember, IfcWall / IfcWallStandardCase, IfcCurtainWall, IfcRoof, IfcStair, IfcRamp, IfcDoor, IfcWindow, IfcCovering, IfcRailing, IfcPlate, IfcBuildingElementProxy |
| All MEP | IfcDuctSegment, IfcDuctFitting, IfcPipeSegment, IfcPipeFitting, IfcCableCarrierSegment, IfcCableSegment, IfcFlowTerminal (+ subtypes), IfcFlowController, IfcFlowMovingDevice, IfcEnergyConversionDevice, IfcFlowStorageDevice, IfcDistributionChamberElement, IfcElectricDistributionBoard |
| Industrial | IfcTank, IfcChimney, process equipment as IfcBuildingElementProxy/IfcDistributionElement with `properties.Module=true`, pipe racks (IfcMember + IfcPipeSegment with `system_id`), IfcTransformer |
| Civil (IFC 4.3) | IfcEarthworksCut, IfcEarthworksFill, IfcPavement, IfcCourse, IfcKerb, IfcDeepFoundation, IfcBearing, IfcBridgePart via IfcSlab/IfcBeam with `properties.BridgePart`, IfcSign, IfcRail-ish proxies, utility IfcPipeSegment underground (`storey index -1`) |
| Healthcare | IfcMedicalDevice, IfcSanitaryTerminal, IfcAirTerminal, medical gas IfcPipeSegment (`system_id` prefixed `MG-`), lead-lined IfcWall (`properties.Shielding=true`), IfcCovering with `properties.Hygienic=true`, pressure-controlled rooms via zone tag `pressure_room` |

## 5. Element-to-step map (the deliverable)

`element_step_map.json` is a flat list of **tasks**. One task = one step
applied to one element. A task carries everything a 4D tool or scheduler
needs, so it can be consumed without the other files:

```json
{
  "task_id": "T000142",
  "element_guid": "2O2Fr$t4X7Zf8NOew3FLKI",
  "ifc_class": "IfcSlab",
  "element_name": "Slab L02 bay C4",
  "storey_id": "L02", "zone_id": "L02-Z1", "system_id": null,
  "step_id": "STR-SLAB-POUR", "phase": "superstructure", "trade": "concrete",
  "quantity": 43.2, "unit": "m3",
  "estimated_crew_days": 3.6, "cost": 15120,
  "cells": [[4, 2], [5, 2]],
  "flags": {"requires_crane": false, "requires_access": true, "inspection": true,
            "lead_time_weeks": 0, "laydown_cells": 1},
  "predecessors": [
    {"task_id": "T000141", "type": "FS", "lag_days": 0, "reason": "chain:STR-SLAB-REBAR"},
    {"task_id": "T000097", "type": "FS", "lag_days": 0, "reason": "rule:same_cell_below:STR-COL-POUR"}
  ],
  "rule_id": "R-slab-suspended",
  "planned_start_day": 112, "planned_finish_day": 116,
  "actual_start_day": null, "actual_finish_day": null
}
```

Days are working days from project day 0. `sequence.json` wraps the same
tasks with the project, zones, embedded step library, gates and scenario so
the game loads one file. The game writes `plan_export.json` in the
`element_step_map` shape with `actual_*` filled in; a companion CSV
(`element_guid, task_id, step_id, planned_start, planned_finish, actual_start,
actual_finish`) is produced for scheduling tools.

## 6. Scheduler (baseline)

The pipeline computes a baseline with a critical path method: duration =
`ceil(estimated_crew_days)` clamped to `min_duration_days`, forward pass over
predecessors (FS/SS/FF with lag) and cumulative gate milestones, backward pass
for float and the critical path, then a greedy day-by-day resource levelling
pass against the scenario's `crews_available`. Two crew models exist:
`whole` (one task occupies one crew) and `fractional` (a task occupies
`estimated_crew_days / duration` crews, so many small element tasks share a
crew). Shipped samples use `fractional`, which matches how the game tracks
progress in crew-days; `whole` gives a much longer, more conservative
baseline. The baseline finish week sets the level's contract date
(`scenario.contract_weeks` overrides; otherwise `contract_factor` applies).

## 7. Sector step libraries (summary)

Full libraries live in `../data/sectors/<sector>/step_library.json`.

**Industrial** phases: mobilise → earthworks → foundations → steel →
pipe_rack → equipment → piping → electrical_instrumentation → insulation →
precommissioning → commissioning → handover.

**Civil** phases: mobilise → traffic_stage_1 → utilities → earthworks →
drainage → structures → traffic_stage_2 → pavement → finishing → handover.

**Healthcare** phases: mobilise → substructure → superstructure → envelope →
mep_roughin → interiors → mep_finish → medical_equipment → commissioning →
icra_closeout → handover.

Each library defines trades, steps with rates and costs, predecessor rules,
and gates, and each sector's `mapping_rules.json` covers the IFC classes in
section 4.1.
