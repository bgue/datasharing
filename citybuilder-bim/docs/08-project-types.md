# 08 — Project Types

A **project type** is chosen once, at the start of a plan or build, and
scopes everything after it. Nothing from another type is offered: no ICRA
barrier tiles on a tank farm, no bridge piers in a hospital, no MRI recipe
on a substation. Sector stays the top level (industrial, civil, healthcare);
project type sits under it.

## 1. Catalogue

| Sector | Project type | What it is | Signature content |
| --- | --- | --- | --- |
| industrial | `process_unit` | Process building / unit with equipment, racks, utilities | vessels, exchangers, pumps, compressor, turbine, racks, modules |
| industrial | `pipe_rack` | Stand-alone rack corridor between units | rack bents, pipe spools, trays, insulation, EI, MPEI variants |
| industrial | `tank_farm` | Storage tanks in bunds with manifolds | tank ring foundations, shells, roofs, bund walls, manifolds, pumps |
| industrial | `piling_foundations` | Piling and foundations package | piling rig, test piles, pile caps, ground beams, anchor bolts, grout |
| industrial | `ug_civil` | Underground civil: trenches, duct banks, UG piping, drainage | trenches, bedding, duct banks, manholes/chambers, UG pipes, backfill, surveys |
| industrial | `building` | Control building, warehouse, workshop | steel frame, cladding, slab, roofing, doors, HVAC, lighting |
| industrial | `ehouse` | Prefabricated electrical house set on foundations | e-house set, cable entries, terminations, HVAC, fire suppression, SAT |
| industrial | `substation` | Transformer yard and switchgear | transformers, bus gantry, breakers, grounding grid, cable trenches, fence, energisation |
| healthcare | `new_wing` | New hospital wing | existing content |
| healthcare | `live_fitout` | Fit-out inside an occupied hospital | ICRA containment, quiet hours, phased tie-ins |
| healthcare | `imaging_suite` | Imaging suite (MRI/CT) in an existing building | shielding, magnet delivery route, quench pipe, rigging |
| healthcare | `plant_replacement` | Plant room replacement in a live building | temporary plant, change-over, commissioning gates |
| civil | `road_widening` | Road widening with traffic staging | existing content |
| civil | `bridge` | Bridge structure | piles, piers, girders, deck, bearings, parapets |
| civil | `culvert_drainage` | Culvert and drainage scheme | culvert units, headwalls, drainage runs, chambers |
| civil | `utility_diversion` | Utility diversion ahead of works | locate, trench, divert, test, backfill, reinstate |

The industrial list is the priority. Types can be combined on one site
later (`project.areas[]` each carry a type); v1 is one type per project.

## 2. Definition file

`data/sectors/<sector>/project_types/<type>.json` (schema
`project_type.schema.json`):

```json
{
  "schema_version": "1.0",
  "id": "substation", "sector": "industrial", "name": "Substation and transformer yard",
  "description": "...", "typical_duration_weeks": [20, 40],
  "phases": ["mobilise", "earthworks", "foundations", "steel", "equipment", "electrical_instrumentation", "precommissioning", "commissioning", "handover"],
  "trades": ["civil", "concrete", "erection", "electrical", "instrumentation", "commissioning"],
  "steps": {"include_tags": ["electrical", "foundations", "earthworks", "virtual"], "exclude": ["PRC-MOD-SET"]},
  "recipes": ["rec_transformer_and_switchgear", "rec_cable_tray_and_cabling", "rec_slab_on_grade", "rec_crane_lift_heavy", "rec_grounding_grid", "rec_energisation"],
  "kits": ["transformer", "switchroom", "substation_bay", "bus_gantry", "duct_bank", "chamber"],
  "tiles": ["haul_road", "laydown", "crane_pad", "welfare", "hoarding", "gate"],
  "zone_tags": ["switchyard", "transformer_bay", "control_room", "cable_trench"],
  "equipment": ["mobile_crane", "crawler_crane", "excavator"],
  "events": {"include_tags": ["weather", "delivery", "electrical", "safety"], "exclude": ["icra_audit", "quiet_hours"]},
  "scenario_defaults": {"contract_factor": 1.1, "budget_factor": 1.15, "crews_available": {"electrical": 4, "concrete": 2, "erection": 2, "civil": 2, "instrumentation": 2, "commissioning": 1}},
  "generator": {"id": "industrial_substation", "params": {"transformers": 2, "bays": 6}},
  "ifc_signature": {"classes_any": ["IfcTransformer", "IfcSwitchingDevice", "IfcElectricDistributionBoard"], "min_share": 0.1}
}
```

Rules:

* `phases`, `trades`, `steps`, `recipes`, `kits`, `tiles`, `zone_tags`,
  `equipment` and `events` are **allowlists**: content outside them is not
  offered in that project. Steps listed explicitly in a recipe used by the
  type are always allowed.
* `scenario_defaults` seed a scenario when the player creates a project in
  the setup screen; authored `scenario_*.json` files declare their
  `project_type` and must stay inside the allowlists (validated).
* `generator` names the synthetic generator used for sample bundles and the
  in-game "generate a sample project" option.
* `ifc_signature` lets `ifc-to-elements` propose a type for a real model;
  `project_config.project_type` overrides.

## 3. Where the type applies

| Layer | Behaviour |
| --- | --- |
| Pipeline | `project.project_type` on every bundle; mapping rules may declare `project_types`; recipes `applies_to.project_types`; `build-samples --types` builds one bundle per type; type detection on IFC import |
| Content | one definition per type; recipes, zone tags, kits and events tagged for the types that use them |
| Game setup | **New project** screen: sector → project type card (description, kits preview, typical weeks) → difficulty → start (generated sample or an imported bundle of that type). Existing bundles list grouped by sector and type |
| Game runtime | tile palette, kit fallback, recipe lists (no "All recipes" across types), step palette, events, equipment all filtered by the bundle's type; the HUD shows "Sector · Type" |
| API / MCP | `scenario.list` grouped by type; `project.types` lists definitions; `ifc_to_bundle` accepts or detects a type |

## 4. New kits for the industrial types

`ehouse` (prefab box with cable entries, HVAC units, doors, skid),
`substation_bay` (breaker + disconnects + CTs on steel), `bus_gantry`
(lattice portal with insulators and bus), `duct_bank` (trench with conduit
array, grows in length), `chamber` (manhole/pull pit with cover), `pile_cap`
(pile group stubs + cap, grows in height), `piling_rig` (equipment visual on
the active pile cell), `ground_beam`, `bund_wall`, `manifold`, `building_shell`
(portal frame + cladding, grows by bay).
