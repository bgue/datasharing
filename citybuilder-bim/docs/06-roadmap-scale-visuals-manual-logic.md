# 06 — Roadmap: Large Models, Visual Kits, Manual Sequencing, Construction Logic Library

This roadmap takes SiteBuilder from "synthetic projects on a 6 m grid" to
"plan an actual area of a real plant or hospital from its model". Four
tracks, each with a data-model change, a pipeline change, a game change and
an API/MCP surface. Phases are ordered so that each one is useful alone.

| Phase | Track | Why first |
| --- | --- | --- |
| 3 | Construction logic library + manual sequencing + virtual tasks | Everything else needs tasks that are not BIM elements |
| 4 | Visual kits (rack, rack+EI, rack+MPEI, tank, turbine, ...) | Makes industrial areas legible enough to plan |
| 5 | Scale: configurable grid, aggregation, chunked rendering | Opens real models (100k to 1M elements) |
| 6 | Schedule import and replay, true-geometry layer | Validates real plans against the constraints |

---

## Track A — Construction logic library (Phase 3)

### A.1 What it is

Today: the **step library** holds atoms (`STR-SLAB-POUR`), and **mapping
rules** turn BIM elements into chains of atoms. Missing: the knowledge of
what a particular *installation* needs, including work that has no BIM
element (survey, utility locate, dewatering, shoring, scaffold, lifting
study, temporary works, pre-commissioning, permits, road closure).

The logic library adds **recipes**: molecules of steps with ordering logic,
prerequisites and hold points, indexed by installation type.

```
data/logic/
  recipes/<sector>/<recipe_id>.json      machine-readable recipe
  recipes/<sector>/<recipe_id>.md        rationale, references, pictures (optional)
  index.json                             generated: recipe id -> applies_to, tags, steps
schema/recipe.schema.json
```

Recipe (schema sketch):

```json
{
  "id": "rec_tank_ring_foundation",
  "name": "Storage tank on ring-beam foundation",
  "sector": "industrial",
  "applies_to": {"ifc_class": ["IfcTank"], "visual_kit": ["tank"], "properties": {"Foundation": "ring"}},
  "prerequisites": {"equipment": ["crawler_crane"], "site": ["access_road", "laydown>=3"], "permits": ["hot_work"]},
  "steps": [
    {"ref": "GEN-SURVEY-SETOUT",  "virtual": true, "quantity": {"basis": "count", "value": 1}},
    {"ref": "CIV-EARTH-CUT",      "from_element": "foundation|self", "note": "to formation level"},
    {"ref": "GEN-DEWATER-RUN",    "virtual": true, "duration_days": 10, "parallel_with": "CIV-EARTH-CUT"},
    {"ref": "STR-RING-FORM"}, {"ref": "STR-RING-REBAR"}, {"ref": "STR-RING-POUR", "hold_point": "structural"},
    {"ref": "GEN-SURVEY-ASBUILT", "virtual": true},
    {"ref": "PRC-TANK-SHELL",     "progress_visual": "grow_height"},
    {"ref": "PRC-TANK-ROOF"}, {"ref": "GEN-HYDROTEST", "virtual": true, "hold_point": "pressure_test", "duration_days": 5},
    {"ref": "PIP-NOZZLE-TIEIN", "from_system": true}, {"ref": "ARC-INSUL-APPLY"}
  ],
  "logic": [{"after": "STR-RING-POUR", "before": "PRC-TANK-SHELL", "lag_days": 7, "reason": "cure"}],
  "checks": ["settlement survey before hydrotest", "crane lift plan above 20 t"],
  "references": ["API 650 §7", "company procedure XYZ"],
  "typical_duration_weeks": [8, 14]
}
```

Rules:

* `ref` is a step id; `virtual: true` means no element produces it, so the
  pipeline creates a **virtual task** (see A.3). Inline step definitions are
  allowed for one-off activities (`"step": {...}`), validated like library
  steps.
* `applies_to` uses the same matcher vocabulary as mapping rules, so a recipe
  can be attached automatically (rule `recipe: "rec_tank_ring_foundation"`)
  or manually from the UI/API.
* Recipes may **nest**: a step entry may be `{"recipe": "rec_deep_excavation"}`.

### A.2 Where it is used

| Surface | Capability |
| --- | --- |
| Pipeline | `bimseq logic list`, `logic explain --element GUID` (which recipe applies, which steps are covered by BIM tasks, which are virtual and missing), `logic apply --recipe ID --zone Z` |
| Mapping rules | a rule may emit a recipe instead of a flat step list |
| Game | zone inspector and element tooltip gain **What's needed?** which opens the recipe, marks covered / virtual / missing steps, and offers "Add missing" (creates virtual tasks and packages) |
| API | `logic.list`, `logic.get`, `logic.explain`, `logic.apply` |
| MCP | `explain_installation(element_or_zone)`, `apply_recipe(recipe_id, zone_id)`, resource `sitebuilder://logic/{recipe_id}` |

### A.3 Virtual tasks and manual sequencing

Schema changes (`element_step_map`, `sequence`):

* `task.element_guid` may be `null`; new `task.virtual: true`,
  `task.origin: "rule" | "recipe" | "manual"`, `task.recipe_id`,
  `task.duration_days` (for time-driven virtual steps such as dewatering).
* New file `manual_sequence.json` (`schema/manual_sequence.schema.json`):

```json
{
  "schema_version": "1.0",
  "project": "plant-area-12",
  "zones_in_manual_mode": ["A12-Z3"],
  "tasks": [
    {"id": "M0001", "step": "GEN-SURVEY-SETOUT", "zone_id": "A12-Z3", "virtual": true, "duration_days": 2},
    {"id": "M0002", "step": "CIV-EARTH-CUT", "zone_id": "A12-Z3", "elements": ["2O2Fr$t4X7Zf8NOew3FLKI"], "after": ["M0001"]},
    {"id": "M0003", "step": "CIV-PILE-DRIVE", "zone_id": "A12-Z3", "elements": ["..."], "after": ["M0002"], "lag_days": 0},
    {"id": "M0004", "step": "GEN-SURVEY-ASBUILT", "zone_id": "A12-Z3", "virtual": true, "after": ["M0003"]},
    {"id": "M0005", "step": "STR-SLAB-FORM", "zone_id": "A12-Z3", "elements": ["..."], "after": ["M0004"]},
    {"id": "M0006", "step": "STR-SLAB-POUR", "zone_id": "A12-Z3", "elements": ["..."], "after": ["M0005"]}
  ],
  "overrides": [{"element_guid": "...", "suppress_steps": ["STR-SLAB-STRIKE"]}]
}
```

* Pipeline: `bimseq map --manual manual_sequence.json`. In a zone in
  manual mode, generated tasks are suppressed and the manual chain is used;
  elsewhere manual tasks are merged (same element+step replaces the generated
  task; new ones are added). Predecessor rules from the library still apply
  unless `"inherit_logic": false`.
* Game: **Sequence editor** panel per zone. Left: step palette (library,
  grouped by phase, with recipes as expandable groups). Middle: ordered chain
  with drag reordering, lag fields, "auto-link from library logic" button.
  Right: elements in the zone to bind to a step (multi-select on the 3D view
  or list). Saves to `user://manual_<scenario>.json` and exports with the
  plan. Switching a zone to manual mode freezes its generated packages and
  replaces them with the authored chain.
* API: `manual.set_mode(zone_id, on)`, `manual.add_task`, `manual.update_task`,
  `manual.remove_task`, `manual.link`, `manual.apply_recipe`,
  `manual.export`. MCP mirrors these so a model can author a chain from a
  description ("excavate, pile, survey, form, slab in zone A12-Z3").
* Virtual tasks get a visual: a marker on the zone (survey tripod, pump,
  scaffold cage around bound elements, crane icon) from a small marker kit.

### A.4 Deliverables and acceptance

* 25 to 40 recipes across the three sectors (deep excavation, piled
  foundation, slab on grade, pipe-rack module, tank, vertical vessel,
  pump on plinth, turbine on pedestal, transformer and switchgear,
  bridge pier, culvert, utility diversion, OR room, imaging suite, plant room,
  medical gas system, AHU replacement in live hospital).
* `logic explain` on every synthetic element returns a recipe or "none".
* Manual chain authored via API reproduces the baseline order for one zone
  and the game plays it to completion; virtual tasks appear in the export
  with `element_guid: null`.

---

## Track B — Visual kits (Phase 4)

### B.1 Principle

Keep the low-poly, flat-shaded Kenney look, but make installations
recognisable and let **partial completion show per discipline layer**.
A kit is a procedural composite mesh (GDScript builders) or an authored
`.glb` in the same style, parameterised by footprint cells, height and
layer presence. Kits are driven by data, not hard-coded per element.

### B.2 Kit manifest

`godot/kits/kit_manifest.json` (schema `visual_kit.schema.json`):

```json
{
  "rack": {
    "builder": "PipeRackKit",
    "params": {"tiers": 2, "bent_spacing_m": 6},
    "layers": [
      {"id": "steel",   "disciplines": ["structure"],       "parts": ["columns", "beams", "struts"]},
      {"id": "piping",  "disciplines": ["process"],         "parts": ["pipes_tier1", "pipes_tier2"], "count_from": "length_m"},
      {"id": "ei",      "disciplines": ["electrical", "instrumentation"], "parts": ["tray", "conduit", "jbox"]},
      {"id": "mech",    "disciplines": ["mechanical"],      "parts": ["aircooler", "platform"]},
      {"id": "insul",   "disciplines": ["architecture"],    "parts": ["cladding"]}
    ],
    "variants": {"rack": ["steel", "piping"], "rack_ei": ["steel", "piping", "ei"], "rack_mpei": ["steel", "piping", "ei", "mech"]}
  },
  "tank":    {"builder": "TankKit", "layers": ["ring_foundation", "shell(grow_height)", "roof", "stair", "nozzles", "insulation"]},
  "turbine": {"builder": "TurbineKit", "layers": ["pedestal", "skid", "casing", "generator", "auxiliaries", "exhaust_stack", "enclosure"]},
  "vessel_v": {"builder": "VesselKit", "params": {"orientation": "vertical"}},
  "vessel_h": {"builder": "VesselKit", "params": {"orientation": "horizontal"}},
  "pump_plinth": {"builder": "PumpKit"}, "exchanger": {"builder": "ExchangerKit"},
  "compressor": {"builder": "CompressorKit"}, "transformer": {"builder": "TransformerKit"},
  "switchroom": {"builder": "BoxBuildingKit"}, "cooling_tower": {"builder": "CoolingTowerKit"},
  "stack": {"builder": "StackKit"}, "module": {"builder": "ModuleKit"},
  "ahu": {"builder": "AhuKit"}, "chiller": {"builder": "ChillerKit"}, "mri": {"builder": "MriKit"},
  "bridge_pier": {"builder": "PierKit"}, "culvert": {"builder": "CulvertKit"}
}
```

### B.3 Layered progress

* Each layer binds to disciplines. Layer fill = crew-days done / total for
  the tasks of those disciplines on the elements in that kit instance.
* Layer rendering by fill: `0` ghost; `0 < f < 1` the first `f` of the
  layer's repeated parts solid, the rest ghost (pipes appear one by one, tank
  shell grows by `grow_height`); `1` solid; inspected adds the green
  emission; rework red pulse on the layer only.
* Slabs, walls, earthworks and pavements also get `grow_height` or
  `grow_length` so a half-poured slab looks half poured (answers the
  "partially complete spots" gap).
* Zoom LOD: beyond a distance, a kit collapses to its bounding box tinted by
  overall fill, so large sites stay readable.

### B.4 Assignment

* `element.visual_kit` set by mapping rules (`visual_kit: "rack"`) or by
  aggregation (B.5). Variant chosen automatically from which disciplines have
  tasks in the kit instance (`rack` → `rack_ei` → `rack_mpei`).
* A **kit instance** spans a cell group: all elements in a cell (or a
  system) that map to the same kit are rendered as one instance.

### B.5 Aggregation (shared with Track C)

Real racks are hundreds of members, pipes, fittings and supports per cell.
Pipeline aggregation rule set (`aggregation.json`): group elements by
(cell, kit, discipline, system) into **aggregate elements** carrying
`member_guids[]` and summed quantities. Tasks and packages are built on
aggregates; the export expands back to member GUIDs so 4D tools still get
every element.

### B.6 Deliverables and acceptance

* 18 kits listed above, each with a headless test that builds the mesh for
  1×1, 2×1 and 2×3 cell footprints and all layer fills in {0, 0.5, 1}.
* An industrial sample area (pipe rack with EI and MPEI variants, two tanks,
  a turbine, pumps, transformer) is recognisable in a screenshot taken in the
  editor; screenshot checked in under `docs/img/`.
* Per-layer progress visible in the headless state (`element_visual_layers`
  in the API).

---

## Track C — Scale and configurable grid (Phase 5)

### C.1 Grid defaults and configuration

`project_config.json` (new, optional, schema `project_config.schema.json`):

```json
{
  "grid": {
    "mode": "auto | fixed | chainage",
    "cell_size_m": null,
    "storey_height_m": null,
    "origin": null, "rotation_deg": null,
    "chainage": {"alignment": "IfcAlignment GUID or null", "cell_length_m": 25, "lanes": 6}
  },
  "zones": {"source": "auto | ifc_space | ifc_zone | file", "file": "zones.csv", "auto_block": [3, 3], "max_crews_default": 2},
  "aggregation": {"preset": "industrial_dense", "max_members": 200},
  "filters": {"exclude_ifc_classes": ["IfcAnnotation", "IfcGrid"], "include_storeys": null, "bbox": null},
  "scope": {"buildings": ["*"], "systems": ["*"]}
}
```

Sensible defaults when nothing is given:

| Setting | Default rule |
| --- | --- |
| cell size | median column-to-column spacing from `IfcColumn` placements, snapped to 0.5 m, clamped to 3–12 m; else 6 m |
| storey height | from `IfcBuildingStorey` elevations; single-storey sites 4 m |
| rotation | dominant wall or beam direction (PCA of long axes), snapped to 5° |
| origin | site bbox minimum after rotation |
| civil | chainage mode when `IfcAlignment` exists or a `Chainage` property is found; 25 m cells |
| zones | `IfcSpace` grouped by `IfcZone` when present (rooms become zones with tags from space names: "OR", "Imaging", "Plant"), else auto 3×3 blocks |

### C.2 Pipeline at scale

* Streaming extraction with the ifcopenshell geometry iterator
  (multi-threaded), writing `elements.part-N.json.gz`; memory bounded.
* Aggregation (B.5) with presets per sector; target ≤ 20k tasks and ≤ 2k
  packages for the game regardless of model size. The aggregate → member GUID
  map is kept in `members.json.gz` for export expansion.
* Scheduler already O(E log V); add incremental rebuild for a changed zone.
* Bundle format: gzip, split (`sequence.json.gz` + `tasks.part-N.json.gz`),
  lazy per-zone task detail; `elements` light list only.
* Targets: 300k-element hospital model through the pipeline in under 10
  minutes on a laptop; 1M-element plant model under 40 minutes with
  aggregation.

### C.3 Game at scale

* Chunked rendering: one MultiMesh per (storey, 8×8 cell chunk, kit);
  frustum and storey culling; far LOD collapses to cell cubes.
* Sim already incremental; store task runtime in PackedArrays; packages are
  the UI unit everywhere.
* Gantt virtualised (rows outside the viewport are not drawn), filters by
  building, storey, system, discipline, card; search box.
* Multiple buildings or areas: `project.areas[]` with their own grid origin
  and rotation on a shared world; camera bookmarks per area.
* Targets: 20k tasks / 2k packages at < 100 ms per week and 60 fps in the
  editor on integrated graphics.

### C.4 Deliverables and acceptance

* Auto grid detection unit-tested on synthetic models with known spacing and
  rotation; `project_config.json` overrides honoured.
* A generated 150k-element stress model runs the full pipeline and loads in
  the game within the targets above.

---

## Track D — Schedule import, replay and true geometry (Phase 6)

* Import IFC4 `IfcTask`/`IfcWorkSchedule`/`IfcRelSequence`, P6 XER,
  MS Project XML or CSV; map to tasks/packages by GUID or activity code;
  labour histogram per trade becomes time-varying `crews_available`.
* **Replay mode**: releases and staffing follow the imported dates; the
  simulation records every constraint violation (face/zone congestion,
  access, crane, gates, lead times, cash) as a findings list and Gantt
  markers. API `replay.load`, `replay.run`, `replay.findings`.
* **True geometry layer**: ifcopenshell → glTF per aggregate, decimated;
  toggle at close zoom; kits remain the planning view.

---

## Work packages for the next fan-out (Phase 3 + first half of Phase 4)

| WP | Owner | Scope |
| --- | --- | --- |
| WP-J schemas and docs | orchestrator | `recipe`, `manual_sequence`, `visual_kit`, `project_config` schemas; task `virtual/origin/recipe_id`; `element_guid` nullable |
| WP-K logic library content | content agent | 25–40 recipes with `.md` rationale, index generation, cross-checks against step libraries; new virtual steps added to libraries (survey, dewatering, shoring, scaffold, lifting study, hydrotest, permits) |
| WP-L pipeline | pipeline agent | recipe matcher, virtual tasks, `--manual` merge, `logic` CLI, aggregation preset v0, auto grid detection v0 |
| WP-M game sim + API | game agent | virtual task runtime, manual mode per zone, `manual.*` and `logic.*` methods, kit instance registry and layer fill data |
| WP-N game UI | UI agent | sequence editor panel, "What's needed?" dialog, marker kit for virtual tasks |
| WP-O visual kits | new agent | 18 kit builders, manifest, layered progress, LOD, headless mesh tests, editor screenshot |
| WP-P MCP | MCP agent | `explain_installation`, `apply_recipe`, `author_manual_chain`, resources |

Order: J → (K, L, M, O in parallel) → (N, P) → integration.

---

## Phase 4 plan (visuals, after Phase 3 delivered the 18 kits)

Rendering under `xvfb-run` with the OpenGL 3 driver works in the build
environment, so this phase adds screenshot-based visual QA to the headless
tests.

| WP | Scope |
| --- | --- |
| WP-Q element progress visuals | Ordinary (non-kit) elements show partial completion: slabs, walls, footings, piers, earthworks and pavements grow in height with crew-days done; linear elements (pipes, ducts, trays, roads, kerbs, drains) grow in length along their cell run; in-progress scaffold box stays; inspected and rework tints match the kits. Per-cell progress heat overlay (toggle H) colouring each cell by done share. `BimView.highlight_elements(guids)` selection API for the sequence editor and API (`view.highlight`). |
| WP-R visual QA and screenshots | `godot/tools/screenshot.gd`: loads a scenario, optionally runs the autopilot N weeks, frames a camera bookmark, toggles panels, saves PNGs. Captures for every bundle at weeks 0, 15 and 30 plus each panel (Gantt, sequence editor, What's needed, procurement, report) are checked into `docs/img/`. The agent inspects the images and fixes layout, overlap, contrast, framing and reflow defects in the UI and camera. A headless test runs the harness once under Xvfb when available. |
| WP-S kit polish and installations | Palette aligned with the Kenney colormap; edge darkening for readability; cheap detail (ladders, handrails, nozzles, doors); hover picking of kit instances with a tooltip (installation name, variant, layer fills); an Installations panel listing kit instances with "jump to" camera bookmarks; `element_visual_layers` in the API; a kit catalogue sheet (all kits at fills 0, 0.5, 1) rendered to `docs/img/kits_catalogue.png`. |

Acceptance: screenshots exist for all bundles and panels; a reviewer can
name every kit in the catalogue sheet; half-poured slabs read as half; the
suite stays green; no panel overlaps another at 1280×720 or 1920×1080.
