# SiteBuilder (Godot 4.6)

Construction-planning game built on the Kenney City Builder starter kit. The BIM model is the
level (`sequence.json`); you lay out the site, hire crews, release work zone by zone and hand
over on time. Design: `../docs/01-game-design.md`. **Architecture: `../docs/03-architecture.md`**
(this folder implements it; see "Code map" below). Data contracts: `../schema/`.

## Run

Open `godot/` in Godot 4.6 and press Play, or:

```
godot --path godot                      # menu: pick a scenario
godot --path godot -- --scenario=minimal   # skip the menu
```

## Controls

| Key / mouse | Action |
| --- | --- |
| W A S D, F, middle mouse, wheel | Camera move, re-centre, rotate, zoom (Kenney) |
| Left mouse | Build mode: place tile / place armed equipment. Assign mode: click a zone |
| Right mouse | Rotate the tile cursor |
| Q / E | Previous / next site tile (haul road, laydown, crane pad, welfare, hoarding, ICRA barrier, cones) |
| Delete | Demolish the tile under the cursor |
| Tab | Toggle Build / Assign mode (selecting a crew switches to Assign) |
| Esc | Cancel crew selection / equipment placement |
| Space | Pause / resume |
| 1 / 2 / 3 | Speed 1x / 2x / 4x (3 s of real time per week at 1x) |
| Page Up / Page Down | Focus storey up / down (overlay, ghost fading and camera plane follow) |
| G | Toggle ghost (not-started) elements |
| F1 / F2 | Save / load `user://save_<scenario>.json` |
| F5 | Export `user://plan_export_<scenario>.json` + `.csv` |

Top bar: week / contract week, cash / budget, speed, panel toggles, phase tracker per storey
(grey = not started, blue = in progress, green = complete). Panels: Crews (hire/fire, select a
crew, equipment), Zone inspector, Procurement (order long-lead items), Charts (S-curve,
crew histogram). Zone colours: green ready but no crew, blue active, red congested, grey done,
yellow blocked (gate / delivery / no access / out of crane reach / paused).

### Core loop

1. Place a **haul road** from the site gate to a cell next to a zone (access), a **laydown** tile
   (steps with `laydown_cells`), and a **crane pad** + crane (steps with `requires_crane`).
2. Hire crews, select one, switch to Assign mode and click a zone: it works any READY task of
   its trade there. More crews than `max_crews` in a zone slows everyone (x0.6, x0.35).
3. Order long-lead items early in the Procurement panel.
4. Run the weeks. Inspection steps wait a week and fail 10% of the time (25% rework).
5. When every task is done (or you go bankrupt / fail on hard) the score report appears; export
   the executed plan from there or with F5.

## Run the tests (headless)

```
godot --headless --path godot --import                      # once, and after adding class_name scripts
godot --headless --path godot --script res://tests/run_tests.gd
godot --headless --path godot --script res://tests/run_tests.gd -- readiness   # only files containing "readiness"
```

Prints `PASS`/`FAIL` per test and a `SUMMARY:` line; exit code 1 if anything failed.
Tests use only `scenarios/minimal`. Always run `--import` first when adding new `class_name`
files, otherwise the global class cache is stale and scripts fail to parse.
`--check-only` cannot resolve autoload names (`Audio`, `GameState`, `Scenarios`); the test
`test_scene_smoke::test_scripts_compile` loads every script with the real compiler instead.

## Adding scenarios

1. Produce a `sequence.json` (see `../schema/sequence.schema.json`; the Python pipeline's
   `sync-godot` command does this).
2. Copy it to `godot/scenarios/<scenario id>/sequence.json`.
3. Run `--import` (not required for plain JSON, but harmless). The menu lists every
   `res://scenarios/*/sequence.json`; discovery uses `DirAccess`, so exported builds must
   include the `scenarios/` folder via export filters (`*.json`).

## Code map

| Path | Role |
| --- | --- |
| `scripts/game_state.gd` (autoload `GameState`, class `SimState`) | The simulation: bundle, task states, week loop, crews, equipment, tiles, procurement, events, score, `snapshot()`, save data |
| `scripts/scenarios.gd` (autoload `Scenarios`) | Bundle discovery and selection |
| `scripts/sequence_bundle.gd`, `scripts/data/*.gd` | Typed parse of `sequence.json` plus indices |
| `scripts/sim/*.gd` | `readiness`, `productivity`, `logistics`, `economy`, `events`, `inspections`, `safety`, `scoring`: static helpers over `SimState`, unit-testable |
| `scripts/site_builder.gd`, `site_tile*.gd`, `road_autotile.gd` | Kenney builder adapted: logistics tiles on the GridMap, road auto-tiling |
| `scripts/bim_view.gd` | MultiMesh stand-ins per `visual` kind, state tint and storey filter |
| `scripts/zone_overlay.gd` | Zone quads, hover/click picking |
| `scripts/ui/*.gd`, `scenes/ui/*.tscn` | HUD panels (theme built in code, Lilita One font) |
| `scripts/export/plan_export.gd`, `scripts/save_game.gd` | Plan export (element_step_map shape) and save/load |
| `scripts/main.gd`, `scenes/main.tscn`, `scenes/menu.tscn` | Scene wiring; Kenney View/Camera/GridMap/Sun/CanvasLayer nodes are kept |

### Simulation rules in short

* `advance_week()`: deliveries land, events draw, release pass (readiness refresh), progress,
  inspections, economy, safety, score snapshot, week + 1.
* Task states: NOT_STARTED, READY, BLOCKED, ACTIVE, AWAITING_INSPECTION, REWORK, DONE, INSPECTED.
  Predecessors count as finished when DONE (no inspection) or INSPECTED. FS/SS/FF with lag in days.
  Gates, procurement and predecessors make a task BLOCKED. Access, crane reach, laydown space and
  zone pauses are *impediments*: the task stays READY but crews will not start it.
* Each crew has 5 crew-days a week and works READY / ACTIVE / REWORK tasks of its trade in its
  zone (rework first, then by planned start). Progress is in crew-days against
  `estimated_crew_days`, multiplied by congestion, weather, access, learning and event factors.
* Cash: crews, equipment hire and tile rent weekly; task material cost (`cost`) when a task starts;
  tile and mobilisation cost when placed. Progress payments every `progress_payment_every_weeks`
  for finished tasks at `cost * budget / sum(cost)` (earned value on the contract value) less
  retention; retention is released on handover. Overdraft fee 2%/week.
* The RNG is seeded from the scenario id hash, so runs and tests are deterministic.
* Game over: cash below `-overdraft_limit` for 4 consecutive weeks, 3 incidents on `hard`,
  or `max(52, 3 x contract weeks)` weeks elapsed.

Kenney assets: see `KENNEY_README.md` and `LICENSE-kenney.md` (CC0 / MIT).
