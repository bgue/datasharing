# 03 — Architecture (Godot 4.6 on the Kenney Starter Kit)

## 1. What we keep from the Kenney kit

| Kenney file | Kept as | Notes |
| --- | --- | --- |
| `scripts/view.gd` | camera controller | unchanged except storey focus (`PgUp`/`PgDn` moves the ground plane up one storey) |
| `scripts/builder.gd` | split into `site_builder.gd` | logistics tile placement only; BIM elements are never hand-placed |
| `scripts/structure.gd`, `structures/*.tres` | `SiteTile` resources | haul road, laydown, crane pad, welfare, hoarding, ICRA barrier, gate, cones |
| `GridMap` + dynamic `MeshLibrary` | logistics layer (y = 0) | one GridMap for site tiles |
| `models/*.glb`, sounds, sprites, font | as is | roads → haul roads, pavement → laydown, garage → welfare, buildings → existing structures |
| `data_map.gd`, `data_structure.gd` | replaced by `save_game.gd` | save = scenario id + week + tile layer + task states + crews |

## 2. Scene tree

```
Main (Node3D)
├── View (view.gd)                       Kenney camera
│   └── Camera
├── SiteGrid (GridMap)                   logistics tiles, Kenney MeshLibrary
├── SiteBuilder (site_builder.gd)        cursor, placement, demolish, rotate
│   └── Selector/Container
├── BimView (bim_view.gd)                MultiMeshInstance3D per visual kind + state
├── ZoneOverlay (zone_overlay.gd)        translucent quads per zone, colour = congestion/readiness
├── Sun, Environment
└── UI (CanvasLayer)
    ├── TopBar        week, cash, speed, phase tracker
    ├── CrewPanel     hire/fire per trade, drag crew onto zone
    ├── ZoneInspector ready/blocked/active/done tasks for hovered zone
    ├── Procurement   long-lead items, order buttons, delivery weeks
    ├── Charts        S-curve (planned vs actual cost), crew histogram
    ├── EventToast    weekly events, choices
    └── Report        end-of-week and end-of-level
```

Autoloads: `GameState` (sim), `Audio` (Kenney), `Scenarios` (bundle discovery in `res://scenarios/`).

## 3. Scripts (all GDScript, typed)

| Script | Responsibility |
| --- | --- |
| `game_state.gd` (autoload) | owns `SequenceBundle`, task states, week counter, cash, crews, equipment, score; `advance_week()`; signals `week_advanced`, `task_state_changed`, `event_fired`, `level_finished` |
| `sequence_bundle.gd` | parses `sequence.json` into typed objects (`TaskData`, `ElementData`, `ZoneData`, `StepDef`); builds indices: tasks by zone, by element, successors |
| `sim/readiness.gd` | computes ready/blocked per task from predecessors (FS/SS/FF + lag in days), gates, procurement, crane reach, access (BFS on tile layer), laydown |
| `sim/productivity.gd` | weekly progress per active task; congestion, weather, learning, event modifiers |
| `sim/logistics.gd` | tile graph: gate → haul road connectivity, crane reach circles, laydown capacity |
| `sim/economy.gd` | weekly outflow (crews, hire, tile rent), task start material cost, progress payments, overdraft |
| `sim/events.gd` | weighted draw from scenario events, trigger evaluation, apply effects/choices |
| `sim/inspections.gd` | hold points, fail/rework, gate release |
| `sim/safety.gd` | incident probability, consequences |
| `sim/scoring.gd` | score and grade |
| `bim_view.gd` | element stand-in meshes and state tints; ghost/framed/solid/finished; storey filter; system filter |
| `site_builder.gd` | Kenney builder adapted: place/demolish `SiteTile`s with costs, blocked/occupied cells |
| `ui/*.gd` | panels listed above |
| `export/plan_export.gd` | writes `user://plan_export.json` (element_step_map shape + actual days) and CSV |
| `save_game.gd` | save/load mid-level |

## 4. Time model

* 1 week = 5 working days. Tasks have `estimated_crew_days`; progress is tracked in
  **crew-days done** against `estimated_crew_days` with multipliers (see game design 5.1).
* Day resolution within a week is not simulated; a task finishing mid-week frees its crew
  for the remainder (remainder carried into the next task in the same zone, same trade).
* `advance_week()` order: deliveries land → events draw → release pass (auto-start ready
  tasks for crews assigned to a zone) → progress → inspections → economy → safety → score snapshot.

## 5. Crew assignment model (the core interaction)

A crew is `{trade, zone_id or null}`. Assigning a crew to a zone lets it work any ready
task of its trade in that zone, highest planned-start first (follows the baseline unless
the player pins a task). Multiple crews of the same trade in one zone split tasks.
Congestion counts all crews in the zone against `zone.max_crews`.

## 6. Rendering BIM elements without art

`bim_view.gd` builds one `MultiMeshInstance3D` per `visual` kind using primitive meshes
(`BoxMesh`, `CylinderMesh`, `PrismMesh`) scaled from `size_hint` and positioned by
`cells` + storey index. Per-instance colour encodes state:

| State | Appearance |
| --- | --- |
| not started | ghost: 15% alpha, category colour |
| in progress | wireframe-ish: 50% alpha + scaffold box |
| complete | solid category colour |
| inspected | solid + green emission |
| rework | solid + red pulse |

Category colours: structure grey, architecture beige, mechanical blue, electrical yellow,
plumbing teal, process orange, civil brown, medical magenta.

## 7. Data flow at runtime

```
res://scenarios/<id>/sequence.json ─> SequenceBundle ─> GameState
GameState.advance_week() ─> signals ─> BimView / UI / ZoneOverlay
GameState.finish_level() ─> PlanExport ─> user://plan_export_<id>.json + .csv
```

## 8. Testing without the editor

`godot --headless --path godot --script res://tests/run_tests.gd` runs GDScript unit tests
(`tests/test_*.gd`) covering readiness, productivity, logistics BFS, economy and a 20-week
scripted playthrough of the `minimal` scenario. `godot --headless --path godot --import`
followed by `--check-only` is used in CI-like validation.
