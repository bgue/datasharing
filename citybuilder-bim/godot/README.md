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
| T | Show / hide the timeline (Gantt) panel (also the `T` button in the top bar) |

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

## Packages, faces, takt cards, second shift (docs/05)

* Players release, hold and prioritise **packages** (a trade's tasks in one zone and work face), not tasks. Bundles
  without `packages[]` get them synthesised (`SequenceBundle`). Crew demand per package: below `min` crews no progress,
  crews beyond `ideal` count 0.6, beyond `max` they spill to the next package of the trade in the zone.
* Work faces cap crews per face and an exclusive face (default `floor`) pushes the other faces to 0.35.
* **Sequence cards** (library + scenario, by id) release a zone station by station; trains stagger zones.
* **Second shift** per zone (zone inspector button): x1.8 output, x2.2 crew cost, x1.5 risk, +5% inspection fail,
  at most `shift.max_zones` zones, never in `occupied_adjacent` zones.

## Timeline (Gantt)

A hideable panel docked at the bottom (30 % of the screen height, `T` or the top bar `T` button; hidden at start when the
scenario has `gantt_visible_default: false`). Hiding frees the area: the bottom-anchored panels and the 3D camera reflow.
Drag the thin handle on its top edge to resize it (15 % to 70 %).

* **Rows**: zones grouped by storey (click a storey header to collapse / expand it). Overlapping packages of a zone are
  stacked in lanes. Each package bar has a thin grey baseline (planned start to finish) and a bar in the discipline colour
  filled to its progress; **hatched** = held, **red outline** = understaffed, **amber fill** = behind takt, dimmed = done.
  Station brackets (name, planned span; the current one in white, amber when behind takt) show above zones with a card,
  cyan diamonds are procurement deliveries, red ticks are incidents, the white vertical line is today, the red one the
  contract finish.
* **Zoom / scroll**: header buttons 12 / 26 / 52 weeks, starting at `max(0, week - 2)` and following the game until you
  scroll. Mouse wheel scrolls the rows, **Shift + wheel** (or the scrollbar) scrolls time.
* **Filters**: storey, discipline, and **My crews only** (packages whose trade has a crew assigned to that zone).
* **Hover** a bar: state, crews now / ideal / max, remaining crew-days, blocked reason, station, behind takt.
* **Click** a bar or row: selects the zone (zone inspector pins it and the storey focus follows).
  **Double-click** a row: expands that zone to task level (one sub-row per task, one zone at a time); double-click again
  collapses it.
* **Right-click** a bar: Hold / Release package, Set priority (spin box), Apply card (submenu of the cards), Clear card.
  These call the same `SimState` / `Cards` methods as the API and refresh the panel.
* The zone inspector shows the same renderer as a one-zone **lane view** above its package list.

Code: `scripts/ui/gantt_model.gd` (rows and bars from `ApiViews.gantt`, lanes, stations, markers; pure, unit-tested),
`scripts/ui/gantt_renderer.gd` (`_draw` only, culled, `style_for`, hit testing, context menu actions),
`scripts/ui/gantt_panel.gd` (header, filters, splitter, debounced refresh on `week_advanced`, `task_state_changed`,
`crews_changed`, `package_state_changed`).

## Control API (JSON-RPC 2.0 over WebSocket)

```
godot --headless --path godot --api=8765 [--api-token=T] -- --scenario=minimal
```

`--api[=port]` (also `sitebuilder/api/enabled` project setting) starts `ApiServer` on 127.0.0.1; every method of
docs/05 section 6.2 is implemented (`ApiServer.method_names()` lists them), batches are supported, notifications are
`week.advanced`, `event.fired`, `level.finished`, `package.state`. The Python client and MCP server live in
`tools/sitebuilder_client` and `tools/sitebuilder_mcp`. High-level planning (`site.auto_layout`, `zone.staff`,
`sim.autopilot`, `procure.order_all_due`, analysis) is in `scripts/api/planner.gd` and is usable from the UI.

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
| `scripts/ui/*.gd`, `scenes/ui/*.tscn` | HUD panels (theme built in code, Lilita One font); `gantt_*.gd` is the timeline (built in code, no scene) |
| `scripts/export/plan_export.gd`, `scripts/save_game.gd` | Plan export (element_step_map shape) and save/load |
| `scripts/main.gd`, `scenes/main.tscn`, `scenes/menu.tscn` | Scene wiring; Kenney View/Camera/GridMap/Sun/CanvasLayer nodes are kept |

### Simulation rules in short

* `advance_week()`: deliveries land, events draw, release pass (full readiness refresh), then five
  working days (progress + inspections at day resolution), then economy, safety, score snapshot,
  week + 1. Each working day: readiness is re-evaluated incrementally (successors of tasks that
  started or finished, tasks held by a gate that just opened), every assigned crew spends one
  crew-day (rework first, then earliest planned start; tiny tasks chain within the day), and
  inspections due that day resolve. `SimState.before_work_day` is an optional hook (planners, tests).
* Dates are true working days (`week * 5 + day`). An inspection falls due two working days after the
  work finishes (`work_done_day + 2`, resolved at the end of that day) with the seeded 10% fail chance.
* Gates are cumulative (docs/02 section 3.2): within a zone / storey / project instance, a task whose
  phase order is >= `before_phase` waits until every task at or below `after_phase` is finished.
* Access BFS passes through the cells of all zones (building interior) once a road touches them.
* The site extent is the model grid plus all declared gate / occupied / blocked / tile cells (+2 cells);
  a laydown tile provides 4 laydown cells; civil haul roads may cross the works footprint.
* Task states: NOT_STARTED, READY, BLOCKED, ACTIVE, AWAITING_INSPECTION, REWORK, DONE, INSPECTED.
  Predecessors count as finished when DONE (no inspection) or INSPECTED. FS/SS/FF with lag in days.
  Gates, procurement and predecessors make a task BLOCKED. Access, crane reach, laydown space and
  zone pauses are *impediments*: the task stays READY but crews will not start it.
* Each crew has 1 crew-day per working day and works READY / ACTIVE / REWORK tasks of its trade in its
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
