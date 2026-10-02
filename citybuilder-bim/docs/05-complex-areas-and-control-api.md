# 05 — Complex Areas, Takt Sequencing, Second Shift, Gantt and Control API

Scope of this iteration (v2):

1. **Work packages** with a crew demand curve (labour-intensive areas).
2. **Work faces** so several disciplines can share a zone at different times.
3. **Takt sequence cards** and trains for repetitive complex rooms.
4. **Second shift** per zone.
5. A **hideable Gantt** (timeline) panel, package level, with per-zone lanes.
6. A **control API** (JSON-RPC 2.0 over WebSocket) exposed by the game and an
   **MCP server** plus Python client offering higher-level controls.

Schemas in `../schema/` are normative; this document explains them. All
additions are backward compatible: an old bundle still loads, the game
synthesises packages and treats missing fields as defaults.

---

## 1. Work packages

### 1.1 Definition

A package is a group of tasks in one zone that one trade would reasonably
plan as a unit. The pipeline creates packages; the game and API operate on
them. Players release, hold, prioritise and staff packages, never tasks.

Grouping key (pipeline, `step_library.packaging.group_by`, default
`["zone_id", "phase", "trade", "work_face"]`). Packages are split when they
exceed `packaging.max_crew_days_per_package` (default 60) so one package is
never a multi-month blob.

`sequence.json` gains `packages[]` and each task gains `package_id`
(`P` + 5 digits, deterministic ordering: storey index, zone id, phase order,
trade, face, first task id).

```json
{
  "package_id": "P00042",
  "name": "L02-Z3 · MEP rough-in · mechanical · ceiling void",
  "zone_id": "L02-Z3", "storey_id": "L02",
  "phase": "mep_roughin", "trade": "mechanical", "discipline": "mechanical",
  "work_face": "ceiling_void",
  "task_ids": ["T000311", "T000312", "..."],
  "total_crew_days": 23.5,
  "crew_profile": {"min": 1, "ideal": 3, "max": 4},
  "planned_start_day": 140, "planned_finish_day": 152,
  "requires_crane": false, "lead_time_weeks": 0, "laydown_cells": 2
}
```

### 1.2 Crew demand curve

`crew_profile` comes from the step library (`steps[].crew_profile`, maximum of
the member steps' `min`, and `ideal`/`max` from the package size) and is
clamped to the zone's `max_crews`:

```
ideal = clamp(ceil(total_crew_days / packaging.target_duration_days), max(min,1), zone.max_crews)
max   = clamp(ideal + packaging.max_over_ideal, ideal, zone.max_crews)
```

Game rule, per working day, for the crews of the package's trade working in
the zone on that package (`n`):

| Condition | Effect |
| --- | --- |
| `n < min` | no progress; blocked reason `needs N crews` |
| `min ≤ n ≤ ideal` | each crew contributes 1.0 crew-day × multipliers |
| `ideal < n ≤ max` | crews beyond `ideal` contribute `packaging.over_ideal_factor` (default 0.6) |
| `n > max` | extra crews idle in this package and spill to the next released package of that trade in the zone; if none, they idle (reason `package full`) |

Zone congestion (`max_crews`, 1.0 / 0.6 / 0.35) still applies on top.

### 1.3 Release and priority

* Each package has `released: bool` (default true) and `priority: int`
  (default = planned start). Crews pick the released package of their trade
  in their zone with the lowest priority value that has ready tasks.
* Holding a package stops new tasks starting in it; active tasks finish.
* A package is `done` when all its tasks are DONE/INSPECTED.
* States shown to the player: `held`, `waiting` (released, nothing ready,
  with reason), `ready` (ready tasks, no crews), `understaffed` (n < min),
  `active`, `over_ideal`, `done`.

---

## 2. Work faces

A face is where in the zone the work physically happens.

`work_face` enum (common schema): `structure`, `below_ground`, `external`,
`roof`, `ceiling_void`, `walls`, `floor`, `plant_pad`, `any`. Steps carry
`work_face` (default `any`). Zones may carry `faces: {face: max_crews}`
(default: no per-face cap). Step library `exclusive_faces` (default
`["floor"]`) lists faces that, while any crew works on them in a zone, push
crews on every other face in that zone to the 0.35 congestion factor.

Rules per working day in a zone:

1. Count crews by face across all active packages.
2. A face over its cap gets congestion 0.6 at +1 and 0.35 at +2 or more,
   exactly like `max_crews`, in addition to the zone-level count.
3. If an exclusive face is active, all other faces use 0.35.
4. `any` never counts toward a face cap and is never excluded.

The zone overlay shows a stacked bar per zone: one segment per active face,
coloured by discipline, red outline when a face is over cap or excluded.

---

## 3. Takt sequence cards and trains

### 3.1 Card

A card is an ordered list of stations. Each station selects packages by
`phase`, `trade`, `discipline` and/or `work_face` (any subset, AND-ed) and
has a `takt_weeks` target.

```json
{
  "id": "card_or_room",
  "name": "Operating room recipe",
  "applies_to_zone_tags": ["or_room"],
  "stations": [
    {"name": "Frame",          "select": {"phase": "interiors", "trade": "finishes"}, "takt_weeks": 1},
    {"name": "MEP rough-in",   "select": {"phase": "mep_roughin"},                     "takt_weeks": 2},
    {"name": "Lead lining",    "select": {"phase": "interiors", "work_face": "walls"}, "takt_weeks": 1},
    {"name": "Ceilings",       "select": {"work_face": "ceiling_void", "phase": "interiors"}, "takt_weeks": 1},
    {"name": "Finishes",       "select": {"phase": "interiors", "work_face": "floor"},  "takt_weeks": 1},
    {"name": "Equipment",      "select": {"phase": "medical_equipment"},                "takt_weeks": 2},
    {"name": "Test",           "select": {"phase": "commissioning"},                    "takt_weeks": 1}
  ],
  "auto_staff": "ideal"
}
```

Cards live in `step_library.sequence_cards[]` (sector defaults) and may be
added or overridden in `scenario.sequence_cards[]`. The player may edit a
card in the UI (reorder stations, change takt) and save it to
`user://cards_<sector>.json`.

### 3.2 Applying a card to a zone

* Packages in the zone are assigned to the first station whose selector
  matches; unmatched packages go to an implicit trailing station `Other`.
* Only the current station's packages are released. Earlier stations stay
  released until done; later stations are held.
* The station advances when all its packages are done. If
  `takt_weeks` is exceeded the zone is flagged `behind takt` (Gantt and
  overlay) but nothing is forced.
* `auto_staff` (`"off"`, `"min"`, `"ideal"`): at the start of each working
  day the game moves idle crews of the needed trades into the zone up to
  that level, and releases crews that have nothing to do. Hiring is never
  automatic.
* Clearing a card releases all packages and leaves crews where they are.

### 3.3 Trains

`train.apply(card_id, zone_ids[], stagger_weeks)` applies the card to each
zone and holds zone `k`'s first station until week
`current_week + k × stagger_weeks`. The Gantt shows the train as a diagonal
of station bars. A zone leaving its takt delays nothing automatically; the
player sees the diagonal bend.

---

## 4. Second shift

Per zone `shift_mode`: `single` (default) or `double`. Scenario
`shift` block sets the economics (defaults in parentheses):

| Field | Effect |
| --- | --- |
| `productivity_factor` (1.8) | crew-days per crew per day in that zone |
| `cost_factor` (2.2) | weekly cost multiplier for crews assigned to that zone |
| `risk_factor` (1.5) | incident probability multiplier for tasks in that zone |
| `inspection_fail_add` (0.05) | added to the base fail chance for tasks worked under double shift |
| `max_zones` (2) | how many zones may run double shift at once |
| `forbidden_zone_tags` (`["occupied_adjacent"]`) | zones where double shift is refused (quiet hours) |

The score's `stability` component is not affected; `safety` and `quality`
are, through their normal channels. Score snapshot records
`double_shift_zone_weeks`.

---

## 5. Gantt (timeline) panel

* Toggle with `T` or the top bar button; hidden by default on tutorial
  levels, shown on others. Docked bottom, 30 % height, draggable splitter.
* Rows: zones grouped by storey (collapsible). Each row shows its packages
  as bars; a zone with a card shows station brackets above the bars.
* Per package: thin grey baseline bar (`planned_start_day`..`planned_finish_day`),
  a coloured progress bar (discipline colour, fill = crew-days done / total),
  hatched when held, red outline when understaffed, amber when behind takt.
* Vertical line at the current day; procurement deliveries as diamonds on
  the row; incidents as red ticks.
* Zoom: 12 / 26 / 52 weeks. Filters: storey, discipline, "my crews only".
* Hover: package tooltip (state, n/ideal/max crews, remaining crew-days,
  blocked reason). Click: selects the zone (zone inspector follows).
  Right-click: hold/release, set priority, apply card.
* Expand a row to task level for one zone only.
* The zone inspector embeds the same renderer for a single row ("lane view").
* Rendering: one `Control` with `_draw`, culled to the visible window;
  target under 5 ms per frame for 1 900 tasks / ~200 packages.

---

## 6. Control API (game side)

### 6.1 Transport

JSON-RPC 2.0 over WebSocket, text frames, one request or batch per frame.
Enabled by `--api[=port]` (default 8765) or project setting
`sitebuilder/api/enabled`. Binds `127.0.0.1` only. Optional `--api-token=X`
requires `{"token": "X"}` in `params` of every call. Implemented with Godot's
`TCPServer`, `WebSocketPeer.accept_stream` and the built-in `JSONRPC` class.
Requests are processed on the main thread between frames; a request that
advances time runs synchronously and returns the new snapshot summary.

Notifications (server → client, no id): `week.advanced {week}`,
`event.fired {event_id, text, choices[]}`, `level.finished {result}`,
`package.state {package_id, state}`.

### 6.2 Methods

Low level (mirror of game actions). All return `{ok: true, ...}` or a
JSON-RPC error `-32000` with `message` = the game's `last_error`.

| Method | Params | Returns |
| --- | --- | --- |
| `scenario.list` | | `[{id, name, sector, difficulty, tasks}]` |
| `scenario.load` | `id` | summary |
| `state.summary` | | week, day, cash, budget, contract_weeks, counts by state, score so far, pending_event |
| `state.zones` | `storey_id?` | `[zone_status + faces + shift_mode + card + station]` |
| `state.packages` | `zone_id?`, `state?` | `[package view]` |
| `state.tasks` | `zone_id?` or `package_id?` | `[task view]` |
| `state.crews` | | `[{id, trade, zone_id, package_id}]`, caps, hire-left |
| `state.tiles` | | tiles, equipment, access per zone |
| `state.procurement` | | long-lead tasks with order/delivery weeks and lateness |
| `state.gantt` | `zone_ids?`, `from_week?`, `to_week?` | bars in the shape the Gantt panel draws |
| `sim.advance` | `weeks=1`, `stop_on_event=true` | summary; stops early at an event with choices |
| `sim.resolve_event` | `choice` | summary |
| `sim.set_speed` | `speed` | |
| `crew.hire` / `crew.fire` | `trade, count=1` / `crew_id` | crews |
| `crew.assign` | `crew_id, zone_id` (`""` unassigns) | |
| `tile.place` / `tile.remove` | `cell[x,z], tile, orientation=0` / `cell` | |
| `equipment.place` / `equipment.remove` | `equipment_id, cell` / `index` | |
| `procure.order` | `task_id` or `package_id` (orders all long-lead tasks in it) | |
| `package.release` / `package.hold` | `package_id` | package view |
| `package.priority` | `package_id, priority` | |
| `zone.set_shift` | `zone_id, mode` | zone status |
| `card.list` / `card.get` | | cards available |
| `card.apply` / `card.clear` | `card_id, zone_id, auto_staff?` / `zone_id` | zone status |
| `card.save` | card object | stores to user cards |
| `train.apply` | `card_id, zone_ids[], stagger_weeks` | zones |
| `plan.export` | `path?` | paths written |
| `save.write` / `save.read` | `slot?` | |

High level (planner conveniences, implemented in `scripts/api/planner.gd`,
also used by the UI):

| Method | Params | What it does |
| --- | --- | --- |
| `zone.staff` | `zone_id, level ("min"\|"ideal"\|"max")` | moves idle crews (hiring if `hire=true` and caps allow) so every released package meets the level |
| `zone.clear_crews` | `zone_id` | unassigns all crews in the zone |
| `site.auto_layout` | `laydown=4` | places haul roads from gates to every zone, laydown tiles and crane pads with cranes where needed (the scale-test routine) |
| `procure.order_all_due` | `horizon_weeks=8` | orders every long-lead task whose planned start minus lead time is within the horizon |
| `sim.run_until` | `{week?, package_done?, cash_below?, state_count?}` | advances until a condition or an event with choices |
| `sim.autopilot` | `weeks, staff_level="ideal"` | per week: order due items, staff zones with ready work round-robin, advance |
| `analysis.bottlenecks` | | zones with ready work and no crews; understaffed packages; packages waiting on gate/access/crane/laydown/procurement; late orders |
| `analysis.critical` | `top=20` | packages on the baseline critical path not yet done, with slip vs plan |
| `analysis.s_curve` | | planned vs actual cumulative by week |
| `analysis.what_if_shift` | `zone_id` | projected weeks saved and cost added if double shift from now |

### 6.3 Views

`package view`: the package record plus `state`, `crews_now`, `crew_days_done`,
`remaining_crew_days`, `blocked_reason`, `station`, `behind_takt`.
`task view`: task record plus `state`, `progress`, `blocked_reason`,
`actual_start_day`, `actual_finish_day`.

---

## 7. MCP server and Python client (`tools/sitebuilder_mcp/`)

* `sitebuilder_client/`: synchronous WebSocket JSON-RPC client
  (`websockets`), `GameClient(url, token=None)` with one method per API
  method, notifications collected in `client.events`. CLI
  `python3 -m sitebuilder_client <method> [json-params]`.
* `sitebuilder_mcp/server.py`: `FastMCP("sitebuilder")` over stdio
  (`mcp` SDK). Env `SITEBUILDER_URL` (default `ws://127.0.0.1:8765`),
  `SITEBUILDER_TOKEN`.
  * Tools (one per high-level method plus the essential low-level ones):
    `load_scenario`, `get_summary`, `list_zones`, `list_packages`,
    `list_bottlenecks`, `staff_zone`, `assign_crew`, `hire_crews`,
    `release_package`, `hold_package`, `apply_card`, `apply_train`,
    `set_shift`, `order_due_procurement`, `auto_layout_site`,
    `advance_weeks`, `run_until`, `autopilot`, `resolve_event`,
    `export_plan`, `gantt_text` (ASCII Gantt of selected zones for the model).
  * Resources: `sitebuilder://summary`, `sitebuilder://zones`,
    `sitebuilder://zone/{id}`, `sitebuilder://packages/{zone_id}`,
    `sitebuilder://gantt`.
  * Prompt: `plan_next_week` (summarise bottlenecks and propose actions).
* `tools/sitebuilder_mcp/launch_game.py`: starts Godot headless with
  `--api --scenario=<id>` (uses `GODOT_BIN`), waits for the port.
* Tests: unit tests against a fake JSON-RPC WebSocket server; an
  integration test that launches the real game when `GODOT_BIN` is set and
  plays `minimal` to completion through the client.

---

## 8. Gameplay guardrails (unchanged intent)

Decisions stay at zone, package and card level. Every block has a one-line
reason. One headline capacity per zone, faces on hover. Co-required crews and
clash events are out of scope for this iteration.
