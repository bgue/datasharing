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
