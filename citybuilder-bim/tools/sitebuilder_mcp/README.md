# SiteBuilder MCP server and Python client

Lets an LLM (or a script) play SiteBuilder through the game's JSON-RPC control API
(`docs/05-complex-areas-and-control-api.md` sections 6 and 7).

```
LLM client <--stdio MCP--> sitebuilder_mcp.server --GameClient (websocket JSON-RPC)--> Godot game (--api)
```

Requirements: Python 3.11, `mcp` (official SDK, FastMCP) and `websockets` 13+.

## Run the game with the API

```bash
cd tools
python3 -m sitebuilder_mcp.launch_game --scenario minimal --port 8765      # uses $GODOT_BIN, default `godot`
# equivalent: $GODOT_BIN --headless --path ../godot --api=8765 -- --scenario=minimal
```

`launch_game` waits up to 60 s for the port. Add `--api-token=X` on the game side (`--token X` here) to require
a token; the server and client then need `SITEBUILDER_TOKEN=X`. From Python: `launch_game.launch_game("minimal", 8765)`
returns the `Popen`; stop it with `launch_game.stop_game(proc)`.

## Register the MCP server

`sitebuilder_mcp/mcp_config.example.json`:

```json
{
  "mcpServers": {
    "sitebuilder": {
      "command": "python3",
      "args": ["-m", "sitebuilder_mcp"],
      "cwd": "/path/to/citybuilder-bim/tools",
      "env": {"SITEBUILDER_URL": "ws://127.0.0.1:8765", "SITEBUILDER_TOKEN": ""}
    }
  }
}
```

Claude Code: `claude mcp add sitebuilder -e SITEBUILDER_URL=ws://127.0.0.1:8765 -- sh -c "cd /path/to/citybuilder-bim/tools && python3 -m sitebuilder_mcp"`.
The server connects to the game lazily on the first tool call and reconnects if the connection drops.
`SITEBUILDER_TIMEOUT` (seconds, default 120) bounds a single call.

## Tools

| Tool | What it does |
| --- | --- |
| `load_scenario(id="")` | load a level (no id lists scenarios) |
| `get_summary()` | week, cash, package counts, score, crews, pending event |
| `list_zones(storey_id?)` | zone table: crews, shift, card, station, progress |
| `list_packages(zone_id?, state?, limit=60)` | package table with crews vs min/ideal/max, blocked reasons |
| `list_bottlenecks()` | idle zones, understaffed/waiting packages, late orders |
| `staff_zone(zone_id, level="ideal", hire=False)` | move idle crews so released packages meet min/ideal/max |
| `assign_crew(crew_id, zone_id)` | assign one crew (`""` unassigns) |
| `hire_crews(trade, count=1)` | hire crews |
| `release_package(package_id)` / `hold_package(package_id)` | let crews work on / stop work on a package |
| `apply_card(card_id, zone_id, auto_staff?)` | apply a takt sequence card to a zone |
| `apply_train(card_id, zone_ids, stagger_weeks=1)` | apply a card to several zones, staggered |
| `set_shift(zone_id, mode)` | `single` or `double` shift |
| `order_due_procurement(horizon_weeks=8)` | order long-lead items due within the horizon |
| `auto_layout_site(laydown=4)` | haul roads, laydown, crane pads |
| `advance_weeks(weeks=1, stop_on_event=True)` | simulate; stops at events and reports their choices |
| `run_until(week?, package_done?, cash_below?, state_count?)` | advance until a condition or event |
| `autopilot(weeks, staff_level="ideal")` | built-in planner plays N weeks |
| `resolve_event(choice)` | answer the pending event |
| `export_plan(path?)` | write the played plan to files |
| `gantt_text(zone_ids?, from_week?, to_week?, width=100, group_by="package")` | ASCII Gantt |

### Construction logic and manual sequencing (docs/06 track A)

| Tool | What it does |
| --- | --- |
| `explain_installation(zone_id="", element_guid="")` | "What is needed?": applicable recipes with a step table, `[x]` covered by a BIM task, `[v]` virtual task in place, `[ ]` missing |
| `list_recipes(sector="")` | recipe index: id, name, sector, typical weeks, steps, tags |
| `get_recipe(id)` | summary, prerequisites, steps (virtual/optional/hold points), ordering logic, checks, references |
| `apply_recipe(recipe_id, zone_id="", element_guid="", include_optional=False)` | expand a recipe into tasks (reuses existing ones, creates virtual steps, chains them) |
| `set_manual_mode(zone_id, on=True)` | freeze the zone's generated packages and run only authored tasks (or restore them) |
| `list_manual_chain(zone_id)` | ordered authored chain: id, step, bound elements or `virtual`, predecessors, lag, state, days |
| `add_manual_task(step, zone_id, elements?, virtual?, duration_days?, after?, link_type="FS", lag_days=0, name?, note?, marker?, hold_point?, quantity?)` | add one task; virtual when no elements |
| `link_manual_tasks(from_id, to_id, type="FS", lag_days=0)` | `to_id` follows `from_id` (FS, SS or FF) |
| `remove_manual_task(task_id, bridge=True)` | remove an unstarted authored task, reconnecting its neighbours |
| `export_manual_sequence(path?)` | the `manual_sequence.json` document (summary and JSON) |
| `author_manual_chain(zone_id, steps, manual_mode=True, auto_link=True)` | headline tool: switch the zone to manual mode, add the steps in order, link them FS, return the chain |

`author_manual_chain` takes `steps` as a list of `{step, elements?, virtual?, duration_days?, lag_days?, hold_point?}`
(extras: `name`, `note`, `marker`, `quantity`, `unit`, `link_type`). A step without `elements` is virtual; `lag_days`
delays the step after its predecessor (a 7 day cure). The call is all-or-nothing: if the game rejects a step the tasks
already added are removed, the zone's previous mode is restored and the error names the step (`step 3 (NOPE-X)
rejected: ... unknown step: NOPE-X`). The API has no `library.steps` call, so step ids are validated by the game
(valid ids appear in `explain_installation`, `get_recipe` and `list_manual_chain`).

Resources: `sitebuilder://summary`, `sitebuilder://zones`, `sitebuilder://zone/{zone_id}`,
`sitebuilder://packages/{zone_id}`, `sitebuilder://gantt`, `sitebuilder://logic` (recipe index),
`sitebuilder://logic/{recipe_id}`, `sitebuilder://manual/{zone_id}` (the zone's authored chain).
Prompts: `plan_next_week(focus="")`, `plan_installation(zone_or_element)`.

Game errors (JSON-RPC `-32000`, message = the game's `last_error`) surface as MCP tool errors with that message.
A pending event is rendered like

```
PENDING EVENT rain: Heavy rain forecast
  [wait] Wait it out
  [pump] Hire pumps
Answer with resolve_event(choice=<id above>).
```

## Gantt legend

One row per package (or zone with `group_by="zone"`); a week is 5 working days.
`=` planned and not started, `#` done share, `-` remaining share of a started bar, `|` current week,
flag column `H` held / `!` understaffed.

```
                         w0         w4         w8
L00-Z1                 ######-----
L01-Z1               H         ==========================
```

## Example conversation

```
You:    Load the minimal scenario and play the first week.
Claude: [load_scenario id=minimal] -> Week 0/20, cash 250,000 ...
        [auto_layout_site]         -> Site auto-layout done.
        [get_summary]              -> Counts: READY 4, BLOCKED 30 ... Crews: (none)
        [list_bottlenecks]         -> Zones with ready work and no crews (2): L00-Z1, L01-Z1
        [gantt_text group_by=zone] -> (rows per zone)
        Plan: hire one civil and one finishes crew, staff the ground zone first (foundations are on the
        critical path), order long-lead AHU now (lead time 6 weeks).
        [hire_crews trade=civil] [staff_zone zone_id=L00-Z1 level=ideal hire=true]
        [order_due_procurement horizon_weeks=8]
        [advance_weeks weeks=1]    -> Week 1/20 ... PENDING EVENT rain: ...  [wait] ... [pump] ...
        Rain costs a week of outdoor work; pumps are cheap -> [resolve_event choice=pump]
        [advance_weeks weeks=1]    -> Week 2/20 ...
```

## Example conversation: authoring a sequence

```
You:    In zone L00-Z5 excavate, pile, survey, form and pour the slab, with a 7 day cure before the as-built survey.
Claude: [explain_installation zone_id=L00-Z5]  -> rec_slab_on_grade: 2 of 8 required steps in place ...
        [get_recipe id=rec_slab_on_grade]      -> steps, hold point structural after the pour, check: cube tests
        The recipe has no piling, so I will write the chain myself.
        [author_manual_chain zone_id=L00-Z5 steps=[
           {step: CIV-EARTH-CUT,  elements: [<guid>]},
           {step: CIV-PILE-DRIVE, elements: [<guid>]},
           {step: GEN-SURVEY-SETOUT},                       # virtual
           {step: STR-SLAB-FORM,  elements: [<guid>]},
           {step: STR-SLAB-POUR,  elements: [<guid>], hold_point: structural},
           {step: GEN-SURVEY-ASBUILT, lag_days: 7}]]
        -> Authored 6 tasks in L00-Z5: M000008 ... (linked FS in order).
           Zone L00-Z5: manual mode ON / Manual chain for L00-Z5 (6 tasks): (table)
        [staff_zone zone_id=L00-Z5 level=ideal hire=true]
        [advance_weeks weeks=2]
```

Standard jobs go through `apply_recipe` instead: `explain_installation` first, read `get_recipe` for the optional steps,
then `apply_recipe(recipe_id, zone_id, include_optional=true)`; `plan_installation` is a prompt that walks the model through this.

## Python client

```python
from sitebuilder_client import GameClient
with GameClient("ws://127.0.0.1:8765") as c:
    c.scenario_load("minimal")
    c.site_auto_layout()
    c.zone_staff("L00-Z1", "ideal", hire=True)
    s = c.sim_advance(2)          # notifications land in c.events
    print(c.analysis_bottlenecks())
```

CLI: `python3 -m sitebuilder_client state.summary --url ws://127.0.0.1:8765`,
`python3 -m sitebuilder_client sim_advance '{"weeks": 2}' --events`. Method names accept `sim.advance` or `sim_advance`.
Exit codes: 0 ok, 1 game error (JSON on stdout), 2 bad params, 3 connection failure.
`batch([("crew.hire", {"trade": "civil"}), "state.summary"])` sends one frame; failed items come back as `GameApiError` instances.

## Tests

```bash
cd tools
python3 -m unittest discover -s tests -p 'test_client_*.py' -v     # client + textviews + CLI against a fake server
python3 -m unittest discover -s tests -p 'test_mcp_*.py' -v        # MCP tools/resources/prompt, launch_game
# integration (real game): needs a Godot 4 binary and the implemented godot/scripts/api/
GODOT_BIN=/path/to/godot python3 -m unittest tests.test_mcp_integration -v
```

The integration test is skipped, with a message explaining why, unless `GODOT_BIN` is set and `godot/scripts/api/` exists.
`RealGameManualTests` (needs the same environment) loads `healthcare_manual_demo` (else `minimal`), explains a zone,
authors a 3-step chain (one virtual) in a free zone, plays 10 autopilot weeks and asserts the chain tasks appear in
`state.tasks` with `origin: manual`. Example run:
`GODOT_BIN=/path/to/Godot_v4.6-stable_linux.x86_64 python3 -m unittest tests.test_mcp_integration -v`.
`RealGameTests` launches `minimal`, runs `site_auto_layout`, hires and staffs crews, plays `sim_autopilot(30)` (resolving
events with the first choice), asserts the level finished and exports the plan.

## Assumptions about the game API (for the game side)

* Weeks are 5 working days; `week = day // 5`. `state.gantt` returns a list of bars, or `{bars: [...]}`.
* `state.summary` has `week`, `day`, `cash`, `budget`, `contract_weeks`, `counts` (state -> number), `score`,
  `pending_event: {event_id, text, choices: [{id, text}]}` (or strings), and `finished`/`result` when the level is over.
* Zone entries carry `zone_id` (or `id`), `name`, `max_crews`, `shift_mode`, `card`, `station`; `state.crews` returns a list
  or `{crews: [{id, trade, zone_id, package_id}], ...caps}`.
* `package.release`/`package.hold` return the package view (or `{package: view}`).
* `state_count` for `sim.run_until` is passed through as an object; `card.save` params are the card object itself;
  `card.get` takes an optional `card_id`; `zone.staff` takes `hire` (bool); `save.*` take an optional `slot`.
* Manual/logic API as implemented in `godot/scripts/api/`: `manual.add_task` returns `{ok, task_id, task}`;
  `logic.explain` returns `{scope, recipes: [{recipe_id, name, matched_by, steps: [{ref, name, status, virtual, optional,
  hold_point, task_id, task_ids}], coverage}], none}`; `logic.get` returns the raw recipe JSON; `logic.list` rows carry
  `typical_duration_weeks` and a numeric `steps` count; `manual.tasks` rows are task views (`predecessors[{task_id,type,lag_days}]`,
  `element_guids`, `virtual`, `origin`, `manual_id`). Runtime tasks get ids `M000001...`; pipeline-authored ones keep a `T...`
  task id plus `manual_id` `M0001` (the chain view shows both).
* Errors use `-32000` with the game's `last_error`; a missing or wrong token should be an error response (any code).
