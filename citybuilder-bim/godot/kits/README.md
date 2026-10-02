# Visual kits

Procedural, flat-shaded low-poly stand-ins for whole installations (docs/06 Track B). A kit replaces the
generic box / cylinder MultiMesh stand-ins of the elements that belong to it and shows **partial completion per
discipline layer**: unbuilt parts are drawn as 15 % alpha ghosts, built parts solid.

* Manifest: `kits/kit_manifest.json` (schema `schema/visual_kit.schema.json`; check with
  `python3 tools/validate_schemas.py visual_kit godot/kits/kit_manifest.json`).
* Code: `scripts/kits/` (`KitBuilder` base, one builder per kit, `KitRegistry`, `KitInstances`, `KitLayer`, `MarkerKit`).
* Tests: `tests/test_kits.gd` (builders, registry, instances, markers) and `tests/test_kits_layer.gd` (KitLayer, BimView hook):
  `godot --headless --path godot --script res://tests/run_tests.gd -- kits`.

## Kits, layers and disciplines

Each layer binds to disciplines (the discipline of a task's step in the step library). `grow` says how a partial fill
shows: `count` (the first f of the repeated parts are solid), `height` / `length` (the part is solid up to f of its
height / length), `none` (solid only at fill 1).

| Kit | Builder | Layers (id: disciplines, grow) |
| --- | --- | --- |
| `rack` | `PipeRackKit` | `steel` (structure, count); `piping` (process, count); `ei` (electrical/instrumentation, length); `mech` (mechanical, count); `insul` (architecture, length) |
| `tank` | `TankKit` | `ring_foundation` (civil/structure, none); `shell` (process, height); `roof` (process, none); `stair` (architecture, none); `nozzles` (process, count); `insulation` (architecture, height) |
| `turbine` | `TurbineKit` | `pedestal` (civil/structure, height); `skid` (structure, none); `casing` (process/mechanical, count); `generator` (electrical, count); `auxiliaries` (mechanical, count); `exhaust_stack` (process/mechanical, height); `enclosure` (architecture, count) |
| `vessel_v` | `VesselKit` | `skirt` (structure/civil, none); `shell` (process, height); `heads` (process, count); `nozzles` (process, count); `platforms` (structure, count) |
| `vessel_h` | `VesselKit` | `skirt` (structure/civil, none); `shell` (process, height); `heads` (process, count); `nozzles` (process, count); `platforms` (structure, count) |
| `pump_plinth` | `PumpKit` | `plinth` (civil/structure, height); `pump` (process, count); `motor` (electrical/mechanical, count); `piping` (process, count) |
| `exchanger` | `ExchangerKit` | `saddles` (structure/civil, none); `shell` (process, length); `channel_heads` (process, count); `nozzles` (process, count) |
| `compressor` | `CompressorKit` | `skid` (structure, none); `casing` (process/mechanical, count); `motor` (electrical, count); `coolers` (process/mechanical, count); `piping` (process/mechanical, count) |
| `transformer` | `TransformerKit` | `plinth` (civil/structure, height); `tank` (electrical, count); `radiators` (electrical, count); `bushings` (electrical, count); `firewall` (architecture/fire, length) |
| `switchroom` | `BoxBuildingKit` | `slab` (civil/structure, none); `walls` (structure/architecture/electrical, height); `roof` (architecture, none); `doors` (architecture, count); `louvres` (mechanical, count); `trench` (electrical, length) |
| `cooling_tower` | `CoolingTowerKit` | `basin` (civil/structure, height); `cells` (process/mechanical, count); `fan_decks` (process/mechanical, count); `fans` (process/mechanical, count) |
| `stack` | `StackKit` | `base` (civil/structure, none); `shaft` (process/structure, height); `ladder` (architecture, none); `platforms` (structure, count) |
| `module` | `ModuleKit` | `frame` (structure, count); `equipment` (process, count); `piping` (process, count) |
| `ahu` | `AhuKit` | `base` (structure/civil, none); `sections` (mechanical, count); `ducts` (mechanical, count); `controls` (electrical/instrumentation, none) |
| `chiller` | `ChillerKit` | `skid` (structure, none); `condenser` (mechanical, length); `evaporator` (mechanical, length); `compressor` (mechanical, count); `piping` (mechanical/plumbing, count); `controls` (electrical/instrumentation, none) |
| `mri` | `MriKit` | `room` (structure/architecture, height); `shield` (architecture, height); `magnet` (medical, count); `table` (medical, count) |
| `bridge_pier` | `PierKit` | `pile_cap` (structure/civil, none); `stems` (structure/civil, height); `pier_head` (structure/civil, length); `bearings` (structure/civil, count) |
| `culvert` | `CulvertKit` | `bedding` (civil, length); `segments` (structure, count); `headwalls` (structure, count) |
`rack` has variants `rack` (steel, piping, insul), `rack_ei` (+ ei) and `rack_mpei` (+ mech). The variant is chosen from
the disciplines that have tasks in the instance (`KitRegistry.variant_for`): electrical / instrumentation -> `rack_ei`,
mechanical -> `rack_mpei`. A `visual_kit` hint that names a variant (`"rack_ei"`) is a lower bound and also splits the
rack into sections of equal hint (the sample rack becomes three sections).
`vessel_v` and `vessel_h` share `VesselKit` (`params.orientation`).

Markers for virtual tasks (`markers` in the manifest): `survey`, `dewatering`, `scaffold`, `lift_plan`, `permit`,
`test`, `shoring`, `crane` (all `MarkerKit`; colour and anchor per manifest). `KitRegistry.marker_mesh(id)` is the
provider for `BimView.marker_mesh_provider` (mesh centred, about 0.4 x 0.3 grid cells).

## How fills map to progress

For every kit instance (`KitInstances`) the tasks of all its elements (plus the tasks of `member_guids` aggregates) are
collected. `KitRegistry.analyze` turns task progress into layer fills:

* **fill** of a layer = crew-days done / total crew-days over the instance's tasks whose step discipline is in the
  layer's `disciplines` (0 when there are none). Done = `estimated_crew_days` for DONE / INSPECTED / awaiting-inspection
  tasks, else `min(progress, estimated_crew_days)`.
* `sequence_group` + `weight`: layers of a group split the group's combined progress in layer order (a tank's shell takes
  the first 4/6 of the process work, then the roof, then the nozzles). Disciplines of a group are the union of its layers'.
* `follows`: a layer without tasks of its own copies the fill of the layer it names (a tank without stair tasks gets its
  stair with the roof). A layer that has no tasks and follows nothing is **not drawn** (neither solid nor ghost), so an
  instance only shows what the plan builds. If nothing at all has tasks the whole kit is drawn as a ghost.
* **states**: a layer is `rework` when a task of its disciplines is in REWORK (red tint on that layer only) and
  `inspected` when all of them are finished and at least one is INSPECTED (green tint).
* **overall** fill (all tasks) tints the collapsed LOD box.

Fills are quantised to 1/50 before meshing; meshes are cached by (kit, footprint, height, variant, seed, quantised
fills, present layers, states). `KitLayer` rebuilds an instance only when a layer fill moved by more than 0.02, a
layer reached 0 or 1, the present layers / states changed, or the ghost toggle flipped, and spends at most 12 ms
per frame on rebuilds when it runs from `_process`.

## Instances

`KitInstances` groups elements per (storey, kit). Kits with `params.instancing = "group"` (rack, culvert, bridge pier,
cooling tower) join elements on touching cells (a pipe-rack row is one instance of N cells); `"element"` kits (tank,
pump, ...) only merge elements that share a cell, so neighbouring tanks stay separate. Instance footprint = bounding
rectangle of the cells; height = tallest `size_hint` of the members (`height_from: "element"`, floored by
`min_height_m`) or `default_height_m`.

## Assignment (`kit_for_element`)

1. `element.visual_kit` when it names a kit (or a variant, which maps to its kit).
2. Otherwise by `ifc_class` / name / `visual` / zone tag: `IfcTank` -> tank; name ~ turbine / transformer / vessel,
   column, reactor, heater / exchanger / pump / compressor / chiller / AHU / MRI / cooling tower / stack / switchroom /
   module / culvert / pier; `visual == "culvert"`; pipe-rack members (beams, members, pipes, fittings, trays, coverings)
   in zones tagged `pipe_rack` -> rack. Structural classes never match equipment names by accident.

## Rendering

Meshes are built in metres, origin at the footprint centre on the storey floor, and always fit inside
`footprint * cell_size_m` x `height_m`. `KitLayer` scales each `MeshInstance3D` by `1 / cell_size_m` (1 world unit =
1 grid cell) and places it at the centre of the cell rectangle at `storey_y`. Two surfaces: `solid` (opaque) and `ghost`
(alpha), both using vertex colour as albedo. Instances above the focus storey get `transparency = 0.93`. Beyond
`lod_collapse_distance` (world units = grid cells, measured from the camera) an instance collapses to a tinted box.
`BimView` (`use_kits`, default true) skips the elements a kit handles in the MultiMesh pass. `KitInstances.element_layers(guid)` gives the per-element layer view (kit, variant, layer fills, states) for the API.

## Adding a kit

1. Write `scripts/kits/<name>_kit.gd`: `class_name <Name>Kit extends KitBuilder`, override `_build()`. For each layer call
   `if begin_layer("id"):` and draw with the primitives (`box_mm`, `box_grow`, `cyl`, `cyl_grow`, `dome`, `ring`, `tube`,
   `pipe`, `ibeam`, `bar`, `ladder`, `rail`, `platform`, `spiral_stair`, ...). Use `set_part(i, n)` for repeated parts
   (solid once the layer fill covers them), `box_grow` / `cyl_grow` for partial height / length, `orient_long()` /
   `end_orient()` to build along the longer footprint side. Stay inside `width_m` x `depth_m` x `height_m`, stay under
   6000 triangles, and use only `rng` for variation (determinism).
2. Add the kit to `kit_manifest.json` (builder class name -> file name is snake-cased: `PipeRackKit` ->
   `pipe_rack_kit.gd`), with its layers, `params` (`instancing`, `height_from`, `default_height_m`, `min_height_m`) and
   `lod_collapse_distance`.
3. Add a fallback rule to `KitRegistry._fallback_kit` if the kit should be picked without a `visual_kit` hint.
4. Run `godot --headless --path godot --import` (new `class_name`) and the kits tests: every manifest kit is built
   at 1x1, 2x1, 2x3 and fills {0, 0.5, 1} and checked for bounds, triangle count and determinism.
