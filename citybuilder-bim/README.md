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

## Status (v4, Phase 4)

| Check | Result |
| --- | --- |
| Pipeline, client and MCP tests (`cd tools && GODOT_BIN=... python3 -m unittest discover -s tests`) | 200 tests pass |
| Schema validation (`python3 -m bimseq validate ../data`) | all files valid |
| Godot headless tests (`godot --headless --path godot --script res://tests/run_tests.gd`) | 270 tests pass |
| Reference screenshots (`GODOT=... bash godot/tools/shots.sh`, rendered under Xvfb) | 32 images in `docs/img/`, reviewed in `docs/07-visual-qa.md` |

v4 (visuals) adds progress-scaled geometry for ordinary elements (slabs grow
across the bay, walls and piers grow in height, ducts and pipes grow along
their run), thin outline ghosts instead of filled volumes, a per-cell heat
overlay (H), element highlighting, an Installations panel (I) with hover
tooltips showing per-discipline layer fills, Kenney-sampled kit palette with
edge darkening and extra detail, a kit catalogue sheet
(`docs/img/kits_catalogue.png`), a dock-based UI layout with site-fitting
camera framing, and a screenshot harness so every later change can be
checked against real renders.

Earlier status: v3 added the construction logic library (36 recipes),
virtual non-BIM tasks, manual sequencing with an in-game editor (N), and
MCP tools; v2 added work packages, faces, takt cards, second shift, the
Gantt (T) and the JSON-RPC control API. Sample bundle sizes and baselines
are unchanged from v3: industrial 2189 tasks / 39 weeks, civil 1755 / 24,
healthcare 1526 / 35.

Known gaps: no real IFC file has been run through the extractor; the
screenshots are software-rendered under Xvfb, not on a GPU; civil goes
bankrupt unfunded; close-up framing of a selected installation is slightly
off-centre; recipe anchoring for linear runs needs an "anchor once per
system or zone" rule option before real models. Phase 5 (configurable grid,
aggregation at scale, chunked rendering) and Phase 6 (schedule import and
replay, true geometry) follow the roadmap in
`docs/06-roadmap-scale-visuals-manual-logic.md`.
