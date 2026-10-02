# SiteBuilder — a BIM-driven construction-planning city builder

SiteBuilder is a SimCity-style builder built on the
[Kenney Starter Kit City Builder](https://github.com/KenneyNL/Starter-Kit-City-Builder)
(Godot 4.6, MIT). Instead of growing a city, the player plans and sequences a
real construction project. The **BIM model (IFC) is both the source and the
target state**: every element in the model must be built, in a valid order,
by crews with limited space, equipment, cash and time.

The game's output is not a save file. It is a **machine-readable 4D plan**:
every BIM element GUID mapped to a construction step with planned dates,
crew, zone and predecessors. That file round-trips into real 4D/scheduling
tools (Synchro, Navisworks TimeLiner, P6/MS Project via CSV).

Sectors covered by the step libraries and scenarios:

| Sector | Example scenario | What makes it different |
| --- | --- | --- |
| Industrial | Process building with pipe rack and equipment modules | Heavy lifts, long-lead equipment, module installation, crane logistics |
| Civil | Road widening with a bridge and culvert | Earthworks balance, traffic-management phases, utility diversions, linear sequencing |
| Complex commercial (healthcare) | Three-storey hospital wing with imaging suite | Live-hospital phasing, infection control (ICRA) barriers, MEP density, commissioning gates |

## Layout

```
citybuilder-bim/
  docs/            Game design, sequencing model, architecture, implementation plan
  schema/          JSON Schemas: the contract between the data pipeline and the game
  data/sectors/    Per-sector step libraries, mapping rules, scenario definitions
  data/samples/    Generated sample projects (elements -> element_step_map -> sequence)
  tools/           Python pipeline: IFC/synthetic BIM -> element-to-step mapping -> schedule
  godot/           Godot 4.6 project (Kenney kit + gameplay systems)
```

## Quick start

```bash
# 1. Build the sample data (no IFC toolchain needed; uses synthetic BIM generators)
cd citybuilder-bim/tools
python3 -m bimseq build-samples ../data/samples

# 2. Validate everything against the schemas
python3 -m bimseq validate ../data

# 3. Open citybuilder-bim/godot in Godot 4.6 and run. Pick a scenario in the menu.
```

Real IFC files are supported when `ifcopenshell` is installed:

```bash
python3 -m bimseq ifc-to-elements model.ifc elements.json
python3 -m bimseq map elements.json --sector healthcare --out element_step_map.json
python3 -m bimseq schedule element_step_map.json --sector healthcare --out sequence.json
```

Start with `docs/01-game-design.md`.

## Status (v2)

| Check | Result |
| --- | --- |
| Pipeline tests (`cd tools && python3 -m unittest discover -s tests`) | 140 tests pass (1 skipped without `GODOT_BIN`) |
| Schema validation (`python3 -m bimseq validate ../data`) | all files valid |
| Godot headless tests (`godot --headless --path godot --script res://tests/run_tests.gd`) | 158 tests pass |
| Real-game MCP integration test (`GODOT_BIN=... python3 -m unittest tests.test_mcp_integration`) | passes |

v2 adds work packages with a crew demand curve, work faces, takt sequence
cards and trains, second shift, a hideable Gantt timeline, a JSON-RPC
WebSocket control API (`godot --headless --path godot --api=8765 -- --scenario=<id>`)
and an MCP server (`cd tools && python3 -m sitebuilder_mcp`). See
`docs/05-complex-areas-and-control-api.md` and `tools/sitebuilder_mcp/README.md`.

Generated sample projects (synthetic BIM, seed 42, fractional crew model):

| Sector | Elements | Tasks | Packages | Baseline | Contract |
| --- | --- | --- | --- | --- | --- |
| industrial | 849 | 1910 | 258 | 36 weeks | 40 weeks |
| civil | 723 | 1588 | 192 | 20 weeks | 22 weeks |
| healthcare | 834 | 1370 | 285 | 37 weeks | 41 weeks |

Unfunded autopilot at week 40 (standard levels): healthcare 88% finished,
industrial 62%, civil 44%; none bankrupt. Civil utilisation is low (33%)
because staged traffic work gates crews, so its library budgets labour at
40% utilisation; its autopilot throughput is a known gap.

Known gaps: no real IFC file has been run through `ifc-to-elements`; the
Godot UI and Gantt have only been exercised headless; economy balance has
had one tuning pass; industrial throughput is bounded by 30 to 40 week
procurement lead times. The roadmap for large models, visual kits, manual
sequencing and the construction logic library is in
`docs/06-roadmap-scale-visuals-manual-logic.md`.
