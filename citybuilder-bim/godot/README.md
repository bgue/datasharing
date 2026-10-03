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
| H | Toggle the per-cell progress heat overlay (see "Element progress visuals") |
| B | Show / hide the **Areas** panel: camera bookmarks (whole site and every `project.areas` entry), also the `B` button in the top bar (see "Large models") |
| F1 / F2 | Save / load `user://save_<scenario>.json` |
| F5 | Export `user://plan_export_<scenario>.json` + `.csv` |
| T | Show / hide the timeline (Gantt) panel (also the `T` button in the top bar) |
| N | Show / hide the sequence editor for the selected zone (also the `N` button in the top bar and "Sequence editor (N)" in the zone inspector) |

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

## Sequence editor and "What's needed?" (docs/06 track A.2 / A.3)

**Sequence editor** (`scripts/ui/sequence_editor.gd`, `N`): a panel docked at the right, left of the zone inspector, opened
for the zone selected in the inspector (click a zone in Assign mode, in the timeline, or press the inspector's
"Sequence editor (N)" button; it follows the pinned zone while open). Every action calls the same `Manual` functions as the
API (`manual.*`), so the panel and the API server share one code path.

* **Header**: zone name, **Manual mode** switch (`Manual.set_mode`: the generated packages of the zone freeze), **Apply recipe**
  (recipes that apply to the zone, plus an "All recipes" submenu; `Manual.apply_recipe`), **What's needed?**, **Export**
  (`Manual.export_doc` to `user://manual_<scenario>.json`, toast with the path), **Legend** (marker glyphs and colours).
* **Left, step palette**: library steps grouped by phase (name, trade, face letter F / W / C / A) with a search box; recipes
  below as expandable groups listing their steps. Double-click or **Add** adds the highlighted step; highlighting a recipe and
  pressing Add applies the whole recipe.
* **Middle, chain**: the zone's manual tasks in order (`#`, marker glyph or face letter, step, binding `virtual` / `N el.`,
  quantity or duration, link type and lag, predecessors as row numbers, state). **Add** binds the step to the elements
  highlighted in the right column (a small menu asks "bound or virtual"), or adds a virtual task when none are highlighted.
  **Up / Down** or dragging a row re-orders the chain: the order is the dependency order, so moving a row re-links the rows
  around it FS in the new order (each row keeps the lag of its previous link). **Remove** (successors inherit the row's
  predecessors), **Auto-link** (every row FS to the previous one, existing links untouched), **Link to...** (follow another row,
  FS / SS / FF with lag, cycles refused), **Lag** (days on the row's links), **Duration** (virtual tasks), **Hold point**
  (makes the task an inspection of the chosen type). Rows that already started cannot be changed.
* **Right, elements** of the zone (name, class, state, chain rows that bind them) with multi-select (Ctrl / Shift click) and a
  filter. **Bind selected** adds them to the highlighted row (a virtual row becomes an element task). **Select in 3D**
  calls `BimView.highlight_elements(guids)` when the 3D view has it (it does not yet: the button is disabled with a tooltip).
  **What's needed?** opens the dialog for the first selected element.
* **Bottom**: a one-lane timeline (the Gantt renderer in a single zone row) with one bar per chain task, and the legend.
* Virtual tasks show their marker glyph and colour (survey S, dewatering D, scaffold C, lift plan L, permit P, test T,
  shoring R, crane K, other V) in the editor rows, the lane, the zone inspector task list and the legend; the colours are the
  ones `BimView` draws (placeholder cylinders, or the marker kit colours when kits are active).

**What's needed?** (`scripts/ui/whats_needed_dialog.gd`): a dialog opened from the zone inspector button, the sequence editor
(zone, or the selected element), for a zone or one element. Left: the recipes that apply, with `steps in place / total`.
Right: summary, typical duration (weeks), prerequisites (equipment, site, permits, information) and the table of steps with
status chips: **covered** (green, the task id), **virtual** (blue, the virtual task id), **missing** (grey); optional steps
are in italics; zone scope counts any generated task of the step in the zone (tasks frozen by manual mode show "frozen" and do
not cover). **Add missing** runs `Manual.apply_recipe` for the zone or element (the **Include optional steps** box adds the
optional steps too; existing tasks are reused, never duplicated), **Open rationale** shows the recipe's summary, ordering
logic, checks and references. Bundles without recipes show "No recipes available"; recipes that do not match show
"No recipe applies here." Without a listener on `ZoneInspector.whats_needed_requested` the inspector falls back to printing
the rows inline.

Code: `sequence_editor.gd` (panel, built in code), `whats_needed_dialog.gd`, `marker_legend.gd` (glyphs, colours, legend).
Tests: `tests/test_seq_editor.gd`, `tests/test_whats_needed.gd`.

## Element progress visuals and heat overlay (docs/06 B.3, WP-Q)

Elements that no kit draws (`scripts/bim_view.gd`) show partial completion. `fill` is crew-days done over estimated
crew-days of the element's tasks (virtual tasks excluded; finished tasks count in full):

| Group (`visual`) | Partial look |
| --- | --- |
| grow height: wall, curtain_wall, column, pile, pier, footing, earthwork, barrier, culvert, tank, stair, generic | height scales with `fill` from its base (minimum 6 % once started) |
| grow length: duct, pipe, cable_tray, kerb, beam | extends from the first cell along the dominant axis of its cell run |
| flat: slab, roof, deck, pavement, floor_finish, ceiling | the pour front runs across the longer cell axis (a 0.3 m slab would not read as half-built by thickness) |
| count: window, door, terminal, equipment, sign | ghost below `fill` 0.5, solid from there |

Not started: ghost at full extent. In progress: the scaled part at 50 % alpha plus a thin outline box of the full
extent. Complete: solid. Inspected: green tint (lerp 0.22, as in the kits). Rework: red tint, pulsing. Instances are
rewritten only when `fill` moved by more than 2 % or the visual state changed (`BimView.refresh_progress()`, run every
week and on `task_state_changed`; `transform_writes` counts the writes).

* **Heat overlay** (`scripts/cell_heat_overlay.gd`, key H, `BimView.set_heat_visible(bool)`): one flat quad per cell,
  coloured by the done share of every task whose cells include the cell: grey 0 %, amber 50 %, green 100 %, red tint
  when any of them is in rework. Zone cells no task touches are hatched. The focused storey is drawn fully, lower
  storeys faintly, upper ones not at all.
* **Highlight**: `BimView.highlight_elements(guids, color)` draws a pulsing outline box around each element (kit
  elements: around the kit instance footprint); `clear_highlight()` removes it. The sequence editor's "Select in 3D" uses it.
* **API**: `view.highlight {guids[]}`, `view.clear_highlight`, `view.set_heat {on}`, `view.heat {storey_id?}` (per-cell
  `share`, `tasks`, `rework`, plus `empty_cells`). The first three need the 3D view (they error when the game runs
  without a scene); `view.heat` works headless.

## Control API (JSON-RPC 2.0 over WebSocket)

```
godot --headless --path godot --api=8765 [--api-token=T] -- --scenario=minimal
```

`--api[=port]` (also `sitebuilder/api/enabled` project setting) starts `ApiServer` on 127.0.0.1; every method of
docs/05 section 6.2 is implemented (`ApiServer.method_names()` lists them), batches are supported, notifications are
`week.advanced`, `event.fired`, `level.finished`, `package.state`. The Python client and MCP server live in
`tools/sitebuilder_client` and `tools/sitebuilder_mcp`. High-level planning (`site.auto_layout`, `zone.staff`,
`sim.autopilot`, `procure.order_all_due`, analysis) is in `scripts/api/planner.gd` and is usable from the UI.

## Manual sequencing, virtual tasks and the logic library (docs/06 track A)

* **Virtual tasks** (`virtual: true`, `element_guid: null`, `origin`, `recipe_id`, `duration_days`, `marker`, `manual_id`)
  are ordinary tasks of their trade: they count for predecessors, gates, packages and completion, but have no element, so
  they never touch element visuals. A task with `duration_days` is time driven: its package needs exactly one crew, it
  advances one day per working day whatever the quantity, crew weights, shift and learning curve (access / crane flags and
  events still apply). `SimState.virtual_markers()` lists `{task_id, marker, zone_id, cell (zone centre), storey_id, state,
  index}`; `BimView` draws a placeholder cylinder per marker (survey yellow, dewatering blue, scaffold grey, lift_plan and
  crane orange, permit white, test green, shoring brown) and a kit can replace the mesh by assigning
  `BimView.marker_mesh_provider = func(marker: String) -> Mesh` (the visual kits do).
* **Manual mode** (`Manual.set_mode`, zone inspector check button): the generated packages of the zone are frozen (held, no
  work, tasks BLOCKED, ignored by gates and by level completion) and only authored tasks (`TaskData.is_authored()`: origin
  `manual`, or created at runtime) run; switching it off restores the packages. Bundles list their manual zones in the
  `manual` block; the pipeline already drops the generated tasks there.
* **Authoring** (`scripts/sim/manual.gd`): `add_task` (runtime ids `M000001`..; `SimState.register_task` updates indices,
  successors, gate caches, packages and runtime; one authored package per zone / phase / trade / face), `update_task`,
  `remove_task` (successors inherit its predecessors), `link` / `unlink` (cycle check, FS / SS / FF + lag),
  `apply_recipe` (library steps bind to the element by `from_element` self / foundation / host / system / zone, existing
  generated tasks are reused and linked, virtual steps are created or reused per zone, steps chain FS + lag,
  `parallel_with` is SS, recipe `logic` links raise lags, `hold_point` makes an inspection, nested recipes are expanded),
  `export_doc` (manual_sequence.schema.json). Plan export lists authored tasks with T-ids beyond the maximum, `manual_id`,
  `element_guid` null for virtual ones, plus the `manual` document. Saves carry zones, runtime tasks, links and steps; a
  restart re-parses the pristine bundle.
* **Logic library** (`scripts/sim/logic_lib.gd`): recipes come from the bundle's `recipes[]` (else
  `res://logic/recipes/**.json`); a recipe applies to an element when ANY `applies_to` key matches (ifc_class, visual_kit,
  name_regex, keywords against name / class; zone_tags_any against the zone); `explain` rows are `covered` (task id),
  `virtual_present` or `missing`.
* API: `manual.set_mode`, `manual.add_task`, `manual.update_task`, `manual.remove_task`, `manual.link`, `manual.unlink`,
  `manual.apply_recipe`, `manual.export`, `manual.tasks`, `logic.list`, `logic.get`, `logic.explain`, `logic.apply`;
  `state.tasks` carries `virtual` / `origin` / `marker` / `recipe_id` / `duration_days` / `manual_id`, `state.summary`
  `manual_zones`, `manual_tasks`, `virtual_tasks`.

## Screenshots and visual QA (docs/07)

`godot/tools/screenshot.gd` loads a scenario in the real game scene, optionally plays it for N weeks (`Planner.auto_layout` +
`Planner.autopilot`, events resolved with choice 0), opens panels, frames the camera and saves the viewport as a PNG. It needs a
rendering display: under Xvfb with the OpenGL 3 driver (software llvmpipe is fine; ALSA and V-Sync warnings are harmless).

```
xvfb-run -a -s "-screen 0 1280x720x24" godot --path godot --rendering-driver opengl3 \
    --script res://tools/screenshot.gd -- --scenario=healthcare_standard --weeks=15 --view=overview \
    --panels=gantt --size=1280x720 --out=/tmp/shot.png

GODOT=/path/to/godot godot/tools/shots.sh            # the standard set into docs/img/ (about 6 minutes)
GODOT=/path/to/godot godot/tools/shots.sh --quick    # only minimal_overview_w0 (CI smoke)
GODOT=/path/to/godot godot/tools/shots.sh --only=gantt   # images whose name contains the text
```

| Argument | Meaning |
| --- | --- |
| `--scenario=<id>` | Bundle under `scenarios/<id>/` (default `minimal`) |
| `--weeks=<n>` | Auto layout plus autopilot until week n; the weekly report and toast are hidden unless asked for |
| `--view=` | `overview` (whole site), `zone:<id>`, `storey:<index>` (focus plane), `installation:<n>` (kit instance, see `Installations` panel) |
| `--panels=` | Comma list: `gantt`, `editor`, `whats_needed`, `procurement`, `report` (weekly), `final` (score), `crews`, `heat`, `legend`, `charts`, `inspector`, `toast` |
| `--hide=` | Comma list to close first: `crews`, `procurement`, `charts`, `inspector`, `gantt`, `editor` |
| `--zone=<id>` | Zone for the inspector, editor and What's needed? (default: first manual zone, else the busiest zone) |
| `--size=WxH` | Window size, default `1280x720`; `1920x1080` is the second supported layout |
| `--scale=<f>` | Downscale the saved image (0.05..1) |
| `--out=<path.png>` | Output file (required) |

Bad arguments print the problem plus a usage line and exit with code 2; a render failure (unknown scenario or zone, no display)
exits 1. `parse_args` of `screenshot.gd` (static) is unit-tested in `tests/test_screenshots.gd`; the test also renders one minimal
overview when a display exists and prints `SKIP` under `--headless`. `shots.sh` writes `docs/img/<scenario>_<view>_w<weeks>[_<panels>].png`
(`SHOTS_OUT` overrides the folder) and shrinks any PNG above 400 KB to a 256-colour palette. The images are inspected and the
findings written up in `../docs/07-visual-qa.md`.

HUD layout rules (main.gd `_reflow_bottom`): the top bar, the left dock (crews, charts) and the right dock (zone inspector,
procurement) are containers, so panels never overlap; the timeline takes the bottom 30 % and everything bottom-anchored sits above
it; the sequence editor fills the free middle and, on screens narrower than about 1500 px, hides the left dock while it is open
(on screens shorter than 900 px it also folds the timeline away and restores both on close); the hint bar sits at the bottom of
the free middle, the event toast under the storey badge, the weekly report above the hint bar. `view.gd` frames the camera into the
free area (`set_insets`, `frame_site`, `frame_cells(cells, height, pad, snap)`) and picks the yaw (default or turned by 45 / 90 degrees)
at which a long thin site fits best; rotating with the mouse keeps your yaw.

## Large models (docs/06 C.3, WP-U)

Built for a 150k-element / 20k-task / 2k-package model: the pipeline's stress bundle plays at about 20 to 70 ms per simulated week
and refreshes its views in a few milliseconds (numbers below; `tests/test_scale_big.gd` asserts them).

**Bundle formats.** `SequenceBundle.load_from_path` reads `sequence.json` or `sequence.json.gz` (gzip is detected by the magic bytes and
unpacked with `PackedByteArray.decompress_dynamic(-1, FileAccess.COMPRESSION_GZIP)`; `FileAccess.open_compressed` only reads files
written by Godot itself). `Scenarios` finds `res://scenarios/<folder>/sequence.json` or `sequence.json.gz`; the scenario is identified by
`scenario.id` inside the bundle, not by the folder (`Scenarios.path_for` accepts either; the menu list is cached in
`user://scenario_index.json` by file size and modification time, so the first start parses big bundles once). Exports need `*.gz` in the
export filters. `bundle_format` (schema `sequence.schema.json`) selects the layout:

| Key | Meaning |
| --- | --- |
| `compressed` | informational; the gzip magic decides |
| `task_parts` | task files relative to the bundle folder, read in order; each is a JSON array of tasks or `{"schema_version", "part", "tasks": [...]}`. `tasks[]` of the main file may be empty |
| `zone_detail_dir` | folder of `<zone_id>.json` / `.json.gz` read the first time a zone is looked at (`SimState.ensure_zone_detail`, called by the zone inspector, the timeline's task rows and the API). Content: `members` (aggregate element guid -> member guids, merged into `ElementData.member_guids`) and optionally `tasks` rows with the descriptive fields a light task row leaves out (`ifc_class`, `element_name`, `system_id`, `quantity`, `unit`, `rule_id`, `note`, predecessor `reason`s). `task_ids` / `package_ids` in the file are informational |
| `task_count` | optional, the menu shows it without reading the parts |

A task, element or zone row may give `cell_rects: [[x, z, width, depth], ...]` instead of `cells` (a zone-wide slab is one rectangle instead
of 200 pairs; the JSON parse is the dominant load cost). Task cells are decoded on first use. Bundles with more than 4000 tasks drop the
per-task JSON dictionaries and the source dictionary after parsing (exports are rebuilt from the typed fields, `pristine_copy()` re-reads
the file). `bundle.load_stats` reports `read_ms`, `parse_ms`, `part_ms`, `part_json_ms`, `build_ms`, `total_ms`; the pipeline's 12k-task
stress bundle loads in about 2.7 s, a 20k-task synthetic one parses from a dictionary in about 1.3 s.

**Runtime state.** `SimState.runtime` is a `TaskStore` (`scripts/data/task_store.gd`): the state of every task lives in PackedArrays
indexed by task slot (state, progress, required, start / finish days, inspection due day, delivery week, flags, blocked reason) with an
id -> slot Dictionary. `runtime[task_id]` still returns a `TaskRuntime` view with the old field names (cached, so identity is stable),
`runtime.has / size / erase / get_rt` and `for id in runtime` work; hot code reads the arrays through `runtime.index[id]`. The store keeps
what the day loop needs without scanning tasks: state counts and per-zone / per-(storey, phase) counters, the sets of ACTIVE /
AWAITING_INSPECTION / READY / ordered / unpaid tasks, and a change log (`changes_since`) that BimView, KitLayer, the heat overlay and the
zone overlay read with their own cursor. Writing `rt.state` directly behaves like `set_task_state` (the store calls back into `SimState`).

**Weekly loop.** The weekly loop has no pass over all tasks: readiness is incremental (`Readiness.refresh_incremental`: tasks queued by
`note_changed`, the watch list of time / delivery blockers, impediments of the READY set; `blocker` slots skip successors held by another
predecessor, tasks held by a gate wait for the gate to open and read its count live), packages recompute only when one of their tasks, crew
count, `released` or `frozen` changed, `has_work` and the priority-sorted (zone, trade) lists are cached, crews per zone are counted once
per day, inspections, deliveries, payments and the safety roll walk the store's sets. `refresh_states()` (full) stays the default for tests
and tools; `refresh_states(false)` is the incremental one used by `advance_week`, tile / equipment / order actions and event choices.
`SimState.debug_full_refresh` turns the incremental pass into a full one (`tests/test_incremental.gd` compares both over 18 weeks).
`SimState.last_week_profile_us` splits the last week into events / release / days / economy / end. One difference from the old full pass:
a tile or crane change in the middle of a week no longer sends tasks released that week back to BLOCKED (the full pass re-evaluated them at
the start-of-week day).

**Rendering.** `BimView` draws the non-kit elements with one MultiMesh per (storey, 8 x 8 cell chunk, visual kind) plus an outline
MultiMesh beside it; per-element state lives in PackedArrays (`_slots` maps guid -> element index). `update_culling(camera)` (from
`_process` every 0.1 s, only when the camera, focus or ghost toggle changed) hides chunks outside the frustum (two-level test: tiles of
4 x 4 chunks first), hides the storeys above the focus on models with more than 30000 elements (`cull_above_focus`), and swaps chunks
farther than `lod_far_distance` (100 cells) for **cell cubes** tinted by the mean done share of the tasked elements on the cell through the
heat palette (hysteresis 0.9, at most 96 cube sets built per pass). The weekly `refresh_progress()` touches only the elements of tasks that
changed (change log), the first colouring of a big model is spread over frames, and a fresh model (nothing started) is written to the
MultiMeshes in bulk. `KitLayer` keeps one node per installation: frustum culling with bounding spheres (out-of-view full meshes are not
rebuilt), LOD collapse beyond `lod_collapse_distance`, fills refreshed only for installations whose tasks changed, at most
`FRAME_BUDGET_MS` of mesh builds per frame, and models with more than 300 installations build their first meshes from `_process`.
The heat overlay (H) is built the first time it is shown, keeps per-cell sums current from the change log, and creates quads per
(storey, chunk) only for chunks BimView draws in full. The zone overlay recolours only the focused storey and, between weeks, only the
zones whose tasks or crews changed (per-zone counters in the store make `zone_status` O(1)).

**Areas and bookmarks.** `project.areas[]` (`id`, `name`, `cells`, `storey_ids`, `camera_bookmark`) is parsed into `bundle.areas`
(`zone_ids` = zones whose centre cell is in the area and on its storeys; an area without cells is its storeys). Key **B** (or the top bar
`B` button) shows the Areas panel in the left dock: "Whole site" and every area with its zone count and done share; a click sets the storey
focus and calls `view.frame_cells` on the area's cells (`camera_bookmark` may carry `yaw` and `zoom`). API: `state.areas {with_cells?}` ->
list of `{id, name, storey_ids, zone_ids, zones, cell_count, bounds, tasks, tasks_done, active, done_share, crews[, cells]}`;
`view.jump_to_area {id}` (`""` / `"site"` = whole site) -> `{id, name, framed, storey_index}` (`framed` is false without a 3D view);
`state.zones` and `state.gantt` accept `area_id`; the timeline header shows an **Area** filter when the bundle has areas.

**Timeline.** `GanttRenderer` only draws the rows in view. Above 150 zones or 1500 packages `GanttModel.build` is lazy: zone rows carry
just the layout data (lane count from the planned spans, cached in `SimState.gantt_lanes`; stations) and `GanttModel.fill_row` adds the
bars and markers of a row when it is drawn or hit-tested (`GanttRenderer.ensure_row`, `built_row_count`). 600 zones: model in about 5 ms
instead of 200 ms, about 5 rows built per screen.

**Measured** (headless CPU, this build machine; `test_scale_big` prints them): 19.3k tasks / 4.4k packages / 234 zones / 10.8k elements
synthetic campus: advance_week mean 63 ms (worst 91 ms, planner excluded), BimView + KitLayer + zone overlay refresh 3 to 5 ms per
week, culling pass 0.2 ms, Gantt model 28 ms cold. Pipeline stress bundle (12k tasks, 1.5k packages, 5.9k elements, 720 kit
installations, 684 chunks): load 2.7 s, advance_week mean 54 ms, refresh 5 to 15 ms. `StressGen` (`tests/stress_gen.gd`) builds the synthetic
bundles (`generate(wings)` replicates healthcare_standard, `zone_heavy(n)` many small zones) and writes `.json.gz` / split / detail variants.

## Run the tests (headless)

```
godot --headless --path godot --import                      # once, and after adding class_name scripts
godot --headless --path godot --script res://tests/run_tests.gd
godot --headless --path godot --script res://tests/run_tests.gd -- readiness   # only files containing "readiness"
```

Prints `PASS`/`FAIL` per test and a `SUMMARY:` line; exit code 1 if anything failed.
Tests mostly use `scenarios/minimal`; the scale tests also play the shipped bundles and the stress bundles (`test_scale_big` takes about a minute). Always run `--import` first when adding new `class_name`
files, otherwise the global class cache is stale and scripts fail to parse.
`--check-only` cannot resolve autoload names (`Audio`, `GameState`, `Scenarios`); the test
`test_scene_smoke::test_scripts_compile` loads every script with the real compiler instead.

## Adding scenarios

1. Produce a `sequence.json` (see `../schema/sequence.schema.json`; the Python pipeline's
   `sync-godot` command does this).
2. Copy it to `godot/scenarios/<scenario id>/sequence.json` (or `sequence.json.gz`, plus its `tasks.part-N.json.gz` and zone detail folder when it is split).
3. Run `--import` (not required for plain JSON, but harmless). The menu lists every
   `res://scenarios/*/sequence.json`; discovery uses `DirAccess`, so exported builds must
   include the `scenarios/` folder via export filters (`*.json`).

## Code map

| Path | Role |
| --- | --- |
| `scripts/game_state.gd` (autoload `GameState`, class `SimState`) | The simulation: bundle, task states, week loop, crews, equipment, tiles, procurement, events, score, `snapshot()`, save data |
| `scripts/scenarios.gd` (autoload `Scenarios`) | Bundle discovery and selection |
| `scripts/sequence_bundle.gd`, `scripts/data/*.gd` | Typed parse of `sequence.json` / `.json.gz` (split parts, lazy zone detail, areas) plus indices; `task_store.gd` is the PackedArray task runtime |
| `scripts/sim/*.gd` | `readiness`, `productivity`, `logistics`, `economy`, `events`, `inspections`, `safety`, `scoring`: static helpers over `SimState`, unit-testable; `manual` (manual mode, authored tasks, recipe expansion, manual export) and `logic_lib` (recipe matcher, explain) |
| `scripts/site_builder.gd`, `site_tile*.gd`, `road_autotile.gd` | Kenney builder adapted: logistics tiles on the GridMap, road auto-tiling |
| `scripts/bim_view.gd` | Chunked MultiMesh stand-ins (storey, 8 x 8 chunk, kind), state tint, storey filter, frustum culling and far LOD cell cubes |
| `scripts/zone_overlay.gd` | Zone quads, hover/click picking |
| `scripts/sim/areas.gd`, `scripts/ui/areas_panel.gd` | `project.areas` summaries, camera targets; the Areas panel (key B) |
| `scripts/ui/*.gd`, `scenes/ui/*.tscn` | HUD panels (theme built in code, Lilita One font); `gantt_*.gd` is the timeline (built in code, no scene); `sequence_editor.gd`, `whats_needed_dialog.gd`, `marker_legend.gd` author manual chains and explain recipes (built in code) |
| `scripts/export/plan_export.gd`, `scripts/save_game.gd` | Plan export (element_step_map shape) and save/load |
| `scripts/main.gd`, `scenes/main.tscn`, `scenes/menu.tscn` | Scene wiring and HUD layout (docks, `_reflow_bottom`); Kenney View/Camera/GridMap/Sun/CanvasLayer nodes are kept |
| `tools/screenshot.gd`, `tools/shots.sh` | Visual QA harness (see above) |

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
