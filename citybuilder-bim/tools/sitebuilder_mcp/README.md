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

Resources: `sitebuilder://summary`, `sitebuilder://zones`, `sitebuilder://zone/{zone_id}`,
`sitebuilder://packages/{zone_id}`, `sitebuilder://gantt`. Prompt: `plan_next_week(focus="")`.

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
        [get_summary]              -> Packages: ready 4, held 0 ... Crews: (none)
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
It launches `minimal`, runs `site_auto_layout`, hires and staffs crews, plays `sim_autopilot(30)` (resolving
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
* Errors use `-32000` with the game's `last_error`; a missing or wrong token should be an error response (any code).
