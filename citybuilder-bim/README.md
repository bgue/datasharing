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

## Status (v3, Phase 3)

| Check | Result |
| --- | --- |
| Pipeline, client and MCP tests (`cd tools && GODOT_BIN=... python3 -m unittest discover -s tests`) | 200 tests pass, including two real-game integration tests |
| Schema validation (`python3 -m bimseq validate ../data`) | all files valid |
| Godot headless tests (`godot --headless --path godot --script res://tests/run_tests.gd`) | 234 tests pass |

Visual QA with real screenshots (`godot/tools/shots.sh`, images in `docs/img/`): `docs/07-visual-qa.md`.

v3 adds the construction logic library (36 installation recipes in
`data/logic/`), virtual non-BIM tasks (survey, dewatering, shoring, scaffold,
lift plans, permits, tests) expanded from recipes attached to mapping rules,
manual sequencing (per-zone manual mode, hand-authored chains, in-game
sequence editor on N, "What's needed?" dialog), 18 procedural visual kits
with per-discipline progress layers (racks with EI and MPEI variants, tanks,
turbines, vessels, pumps, transformers, MRI, bridge piers, culverts, ...),
aggregation v0 and auto grid detection v0 in the pipeline, and MCP tools to
explain an installation or author a chain from a sentence. See
`docs/06-roadmap-scale-visuals-manual-logic.md`, `data/logic/README.md`,
`godot/kits/README.md` and `tools/sitebuilder_mcp/README.md`.

Generated sample projects (synthetic BIM, seed 42, fractional crew model):

| Sector | Elements | Tasks | Virtual | Packages | Baseline | Contract |
| --- | --- | --- | --- | --- | --- | --- |
| industrial | 855 | 2189 | 257 | 325 | 39 weeks | 43 weeks |
| civil | 723 | 1755 | 167 | 255 | 24 weeks | 27 weeks |
| healthcare | 834 | 1526 | 161 | 342 | 35 weeks | 39 weeks |
| healthcare_manual_demo | 834 | 1413 | 157 | 324 | 35 weeks | 39 weeks |

Funded autopilot at week 40: healthcare 93%, civil 84%, industrial 66%.
Unfunded: healthcare and industrial survive; civil still goes bankrupt
(low utilisation under staged traffic gating, known gap).

Known gaps: no real IFC file has been run through the extractor; the UI,
Gantt, sequence editor and kits have only been exercised headless (the kits
agent rendered the industrial sample once under Xvfb and found the kits
recognisable); economy balance has had one tuning pass; one civil chamber
sits outside every crane pad's reach; recipe anchoring for linear runs uses
synthetic-name regexes that need re-narrowing on real models (an "anchor
once per system or zone" rule option is the planned fix).
