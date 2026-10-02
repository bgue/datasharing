# Sector content (WP-B)

Three sector packs drive the mapper, scheduler and game. Each folder holds:

| File | Schema | What it is |
| --- | --- | --- |
| `step_library.json` | `step_library.schema.json` | phases, trades, steps with rates and costs, predecessor rules, gates |
| `mapping_rules.json` | `mapping_rules.schema.json` | element-to-step rules (priority ordered) plus a default rule |
| `scenario_tutorial.json`, `scenario_standard.json`, `scenario_hard.json` | `scenario.schema.json` | the three levels: crews, equipment, site grid, events, scoring, hints |

Validate everything with:

```
python3 tools/validate_schemas.py step_library data/sectors/*/step_library.json
python3 tools/validate_schemas.py mapping_rules data/sectors/*/mapping_rules.json
python3 tools/validate_schemas.py scenario data/sectors/*/scenario_*.json
```

Counts at a glance:

| Sector | Phases | Trades | Steps | Rules (+default) | Gates | Events per level (tutorial / standard / hard) |
| --- | --- | --- | --- | --- | --- | --- |
| industrial | 12 | 9 | 79 | 53 | 6 | 12 / 15 / 15 |
| civil | 10 | 9 | 70 | 45 | 6 | 12 / 15 / 15 |
| healthcare | 11 | 10 | 77 | 49 | 6 | 12 / 15 / 15 |

## How a library is organised

* **Phases** follow `docs/02-sequencing-model.md` section 7 exactly, ids lower snake case, `order` 0..n.
* **Step ids** are `DISCIPLINE-OBJECT-ACTION` (`STR-SLAB-POUR`, `MEP-DUCT-INSTALL`, `PRC-MOD-SET`, `MED-MRI-INSTALL`, `CX-AIR-BALANCE`). Prefixes used: `GEN` general, `CIV` civil, `STR` structure, `ARC` architecture, `MEP`/`PLB`/`ELE`/`FIR` services, `PRC`/`PIP`/`IC`/`INS` process, `MED` medical, `CX` commissioning.
* **Predecessors** use the scope vocabulary of the model doc. Conventions used throughout:
  * Floor convention: slab on storey N is the floor of N. Columns need the footing (`same_cell_below` and `same_cell`) and the slab below; slabs need columns and beams below. Services on storey N need the slab above (`same_cell_above`), so ceilings and rough-in sit under a poured, struck deck.
  * Rules that may match nothing (a cell with no footing, a zone with no ICRA task) are silently skipped by the mapper, so they are deliberately not `required`. `required: true` is reserved for the logic that must exist: commissioning after the system installs (`same_system`), pile caps after piles, beams after bearings, insulation chains after tests.
  * Every step in phase order 2 or later has at least one predecessor rule, and the cell-scope rules have `same_zone`, `same_system` or other fallbacks so synthetic and IFC-derived models (where storeys, zones and systems differ) still resolve; the mapped synthetic projects have no later-phase task without a predecessor.
* No step depends on a step in a later phase (checked by the build scripts), otherwise gates would deadlock. Chains in `mapping_rules.json` never go back in phase either.
* **Tags** are derived from the flags (`weather_sensitive`, `noisy`, `dusty`, `heavy_lift`, `long_lead`, `hold_point`, `high_risk`, trade id) plus a few hand tags used by events: `steel_connection`, `hot_work`, `module`, `transformer`, `mri`, `bearing`, `road_closure`, `asphalt`, `excavation`, `piling`, `live_traffic`, `medical_gas`, `shielding`, `icra`.
* **Inspections** only use the types `structural, mechanical, electrical, plumbing, fire, medical_gas, icra, pavement, geotechnical, welding, pressure_test`; gates reference the same strings.
* **Default rule**: every library has `GEN-ELEM-INSTALL` (count) so unmatched elements are still buildable; the mapper lists them in `unmapped_elements`.

### Mapping rules

* Priorities start at 400 and fall by 5 per rule, specific rules first, class-wide fallbacks last.
* `min_quantity` is used on every `count` emit (1) because many elements carry no explicit count; the mapper raises the quantity to the minimum, it does not skip.
* `quantity_factor` plus `unit_override` convert between bases, for example rebar tonnes = 0.11 x concrete m3 (`unit_override: t`), wall concrete = 0.25 to 0.35 m x wall area (`m3`), trench volume = 1.2 to 1.4 m3 per metre.
* `continue: true` with `chain: false` is used for steps that must exist beside the normal chain without being part of it: the healthcare ICRA setup, HEPA clean and clearance tasks, and the civil traffic-management stage 1 tasks. Their ordering comes from predecessor rules in the library (`same_zone`).
* Coverage: the rules name every class in `docs/02` section 4.1 (all, all MEP, sector rows) explicitly. The synthetic generators in `tools/bimseq/synth` produce a subset of those classes, so some rules are only exercised by a real IFC import.

## Rate and cost assumptions

> **These are order-of-magnitude planning figures for a game, not estimating data.** Do not price real work from them.

Rough sources of the assumptions (rounded, mixed 2020s USD, no location factor):

| Item | Assumption | Basis |
| --- | --- | --- |
| Concrete pour | 10 to 15 m3 per crew-day, 190 to 380 per m3 | typical placing rates with pump; ready-mix plus placing, order of published unit-cost books (RSMeans, Spon) |
| Rebar | 1.5 to 2.5 t per crew-day, 1,850 per t | fix rates for mixed bar; supply and fix |
| Formwork | 25 to 40 m2 per crew-day, 40 to 48 per m2 | slab and wall forms, material and reuse allowance |
| Steel erection | 8 to 15 t per crew-day, 3,200 to 3,500 per t | fabricated, delivered and erected; heavy lifts at the low end |
| Blockwork | 15 to 25 m2 per crew-day | mason plus labourer |
| Drywall framing and boarding | 40 to 60 m2 per crew-day | partition systems |
| Duct, pipe, tray | 15 to 25 m, 20 to 40 m, 30 to 50 m per crew-day | install only, no commissioning |
| Earthworks cut, fill | 400 to 800, 300 to 600 m3 per crew-day, 7 to 14 per m3 | excavator plus haul, compaction included for fill |
| Asphalt | 800 to 1,500 m2 per crew-day, 24 to 28 per m2 | paver crew, 50 to 80 mm layers |
| Piling | 2 to 4 per crew-day, 2,800 to 6,500 each | bored or driven, 10 to 18 m |
| Lead times | MRI 26, CT 20, steriliser 12, transformer 40, chiller 16, switchgear 24, AHU 12, process module 30, bridge bearing 10, precast beam 8, curtain wall 14 weeks | typical published supplier ranges |
| Crew weekly cost | 6,500 to 13,500 per crew-week | blended labour rate times crew size plus small tools; specialist crews (piling, erection, medical) at the top |

Notes:

* `rate_per_crew_day` is the output of one crew of `crew_size` workers. `unit_cost` is material plus subcontract per unit and excludes crew wages (the weekly crew cost is paid on top).
* Owner-supplied equipment is **scaled down for gameplay** so cash flow is playable: MRI 350k, CT 220k, steriliser 90k, chiller 110k, industrial process module 120k, tank 80k, transformer 85k, bridge beam 38k each. Real prices are higher.
* Rates apply to element quantities from the model; the baseline scheduler gives every task at least one working day for one crew, so very small elements still occupy a crew slot. Crew counts in the scenarios were tuned so the baseline finish of the synthetic projects lands in the playable range (see below).
* Weather `monthly_factor` is a temperate-climate multiplier (Jan to Dec) for steps flagged `weather_sensitive`. Civil is harsher in winter.

### Baseline lengths of the synthetic projects

Baseline length depends on the scheduler version and on the crew counts in the scenarios, so treat any number here as a snapshot. With the cumulative-gate scheduler and per-task crew slots (`bimseq.scheduler.build_sequence`) the standard levels came out at industrial 80, civil 72 and healthcare 79 weeks (tutorial 57 / 53 / 63, hard 116 / 94 / 112). The later package-aware `build-samples` run reported standard finish weeks of 36, 20 and 47 and raised start cash and overdraft in all three standard scenarios to cover 8 and 4 weeks of average planned spend. Re-tune crews and cash against whatever WP-D settles on.

The contract is `baseline x contract_factor` (1.3 tutorial, 1.1 standard, 1.0 hard); budget is `tasks x budget_factor` (1.25, 1.15, 1.05). Hard crews are fewer, so the hard baseline itself is longer; the factor 1.0 removes the slack.

## Scenarios

Each level shares the sector library and rules and changes crews, cash, site and event weights.

* **Tutorial**: generous crews and cash, quiet neighbour, 2 to 3 gates, mild events (hard-only events removed, effects halved, weights x0.6, first fire 3 weeks later), 10 hints.
* **Standard**: balanced crews, normal events, 2 gates.
* **Hard**: scarce crews, thin cash, one gate, more occupied cells and blocked cells, harsher events (effects x1.25, weights x1.3), higher retention.
* Scoring weights are time 35, cost 25, safety 20, quality 10, stability 10 for all levels; grade thresholds S/A/B/C are 85/70/55/35 (tutorial), 90/75/60/40, 92/80/65/45 (hard).
* Site grids: industrial 16 x 12 (12 x 8 interior, ring road around it, operating plant on the east), civil 32 x 8 (30 x 6 corridor, live carriageway along the north edge), healthcare 14 x 10 (8 x 6 interior, live hospital on the east edge). Gates are on the perimeter, `occupied_cells` carry the live hospital, traffic or plant, and `initial_tiles` show existing buildings, roads, trees and gate tiles.

## Per-sector summary

### Industrial (process hall, pipe rack, modules, transformer yard)

Phases: mobilise, earthworks, foundations, steel, pipe_rack, equipment, piping, electrical_instrumentation, insulation, precommissioning, commissioning, handover.

| Gate | Scope | Holds | Requires |
| --- | --- | --- | --- |
| G-ground | zone | foundations until piles and platform are proven | geotechnical |
| G-foundations | zone | steel until footings, pads and slabs are inspected | structural |
| G-steel-equipment | zone | equipment until connections are welded and inspected | welding |
| G-pressure-test | zone | insulation until hydrotests pass | pressure_test |
| G-precomm | project | commissioning until flush, loop checks and megger are done | pressure_test, electrical |
| G-commission | project | handover until systems are energised and started | electrical |

Signature steps: `PRC-MOD-SET` (module, 30 week lead, crane, pad, 0.5 per crew-day), `ELE-XFMR-SET` (40 weeks), `STR-EQPAD-POUR` (cure time before the lift), `STR-STEEL-COL` and `STR-STEEL-CONN` (heavy lift, welding hold point), pipe rack chain `STR-RACK-ERECT` then `STR-RACK-CONN` then `PIP-SPOOL-LAY` then `PIP-PIPE-TEST`, `INS-PIPE-INSULATE` (after hydrotest of the same system), system commissioning `CX-POWER-ENERGISE`, `CX-PROCESS-STARTUP`.

### Civil (road, culvert, three-span bridge)

Phases: mobilise, traffic_stage_1, utilities, earthworks, drainage, structures, traffic_stage_2, pavement, finishing, handover.

| Gate | Scope | Holds | Requires |
| --- | --- | --- | --- |
| G-ts1 | zone | utilities until traffic management stage 1 is up | none |
| G-ground | storey | drainage until cut and fill are tested; storey scope because drains are on the underground storey (which also holds utilities and traffic stage 1) while earthworks are at ground level, the cell-above predecessors on `CIV-TRENCH-DIG` tie drains to the cut above | geotechnical |
| G-structures-ts2 | zone | later work in a zone until its structures are inspected; the stage 2 switch itself waits for the bridge deck project-wide through predecessor rules | structural |
| G-ts2-pavement | zone | pavement until traffic stage 2 is in place; `CIV-SUBGRADE-PREP` also waits for stage 2 project-wide | none |
| G-pavement | zone | finishing until base and wearing course pass | pavement |
| G-handover | project | handover until lighting cables and pavement are signed off | electrical, pavement |

Signature steps: `CIV-TM-STAGE1` and `CIV-TM-STAGE2` (lane closure, goodwill cost), `CIV-UTIL-DIVERT` (before earthworks in the same zone), `CIV-EARTH-FILL` (SS +5 days after cut project-wide), `CIV-TRENCH-DIG`, `CIV-DRAIN-INSTALL`, `CIV-DRAIN-BACKFILL` (all before pavement), bridge chain `CIV-PILE-DRIVE`, `STR-CAP-POUR`, `STR-PIER-POUR`, `STR-BEARING-SET` (10 week lead), `STR-BEAM-LIFT` (crane, night closure, risk 0.85), `STR-DECK-POUR`, `STR-PARAPET-INSTALL`; pavement chain `CIV-SUBGRADE-PREP`, `CIV-SUBBASE-LAY`, `CIV-BASE-LAY`, `CIV-ASPHALT-BASE`, `CIV-ASPHALT-WEAR`.

### Healthcare (hospital wing next to a live ward)

Phases: mobilise, substructure, superstructure, envelope, mep_roughin, interiors, mep_finish, medical_equipment, commissioning, icra_closeout, handover.

| Gate | Scope | Holds | Requires |
| --- | --- | --- | --- |
| G-foundations | storey | frame until piles, footings, slab and drains are inspected | geotechnical, structural, plumbing |
| G-weathertight | storey | MEP rough-in until the envelope is closed | structural |
| G-ceiling-closeout | zone | boarding and ceilings until every rough-in inspection passes | mechanical, plumbing, electrical, fire, medical_gas |
| G-clinical-ready | zone | medical equipment until the gas test passes | medical_gas |
| G-commissioning | project | ICRA close-out until tests, balance and certification are done | medical_gas, electrical, fire, mechanical |
| G-handover | project | handover until infection control clears every barrier | icra |

Signature steps: `GEN-ICRA-SETUP` (matched by the zone tag `occupied_adjacent`; dusty or noisy steps in the zone list it as a same-zone predecessor), `STR-SLAB-FORM`, `STR-SLAB-POUR`, `STR-SLAB-STRIKE` (7 day lag; work below waits for the strike above), `MED-GAS-PIPE-INSTALL`, `MED-GAS-TEST` (before walls close and before equipment), `ARC-CEILING-CLOSE` (after all rough-in in the cell), `ARC-SHIELD-WALL` (properties.Shielding), `MED-MRI-INSTALL` (26 weeks), `MED-CT-INSTALL` (20), `MED-STERIL-INSTALL` (12), `ELE-XFMR-SET` (40), `ELE-SWGR-SET` (24), `MEP-CHILLER-SET` (16), `MEP-AHU-SET` (12), `ARC-CURTAIN-INSTALL` (14), `CX-AIR-BALANCE`, `CX-PRESSURE-TEST` (zone tag `pressure_room`), `MED-GAS-CERT`, `GEN-HEPA-CLEAN`, `GEN-ICRA-CLOSEOUT`.

## Events

Events use only fields the schema defines. Triggers refer to real phase ids, step ids and tags of the sector (checked by script). Choice events (2 to 3 options) exist for steel RFIs, module slips, shutdown windows (industrial); rock, traffic complaints, night closure, bearing slips, archaeology (civil); quiet hours, dust complaints, ceiling clashes, MRI slips (healthcare).

## Known limits of the schema (what was wanted instead)

* Predecessor rules are per element; there is no zone-level step, so ICRA barriers and traffic-management closures are emitted as ordinary tasks from matching elements (`continue: true`) and found by `same_zone` predecessors.
* `inspection_type` gates only say which types must have passed; there is no way to say which phase's inspections count, so gate requirements name types that appear in the gated phase or earlier.
* Equipment needs (piling rig, paver, concrete pump, scissor lift) cannot be tied to a step; only `requires_crane` exists. Steps carry tags such as `piling` and `asphalt` for events and for the game to interpret.
* Zone tags are static per project; a live-traffic cell cannot change to free at the stage 2 switch.
* Event effects cannot target a single step id or a gate; only tag, trade and zone tag.

## Gate scope notes (cumulative gates)

The scheduler treats gates cumulatively: every task of the `after_phase` or earlier in the scope instance must finish before any task of the `before_phase` or later starts, and an instance with no prerequisite tasks is vacuous. Zone and storey scopes therefore only bite where both sides live in the same zone or storey. The synthetic civil project splits storeys (utilities and drainage on UG1, earthworks, structures and pavement on L00) and puts the stage 2 tasks in the live-traffic zones, so cross-storey and cross-zone ordering is also carried by predecessor rules with `same_cell_above`, `same_cell_below`, `same_storey` and `project` scopes. A scan of the mapped synthetic projects finds no task in phase order 2 or later that starts on day 0.

## Work faces, crew profiles, packaging and sequence cards (v2)

Fields from `docs/05-complex-areas-and-control-api.md`. All are additive; the mapper and old bundles ignore them.

### Work faces

Every step has a `work_face`, the place in the zone where the work physically happens:

| Face | Used for |
| --- | --- |
| `below_ground` | earthworks, piles, footings, caps, underground drains, ducts, trenching, backfill around foundations |
| `structure` | columns, beams, slabs, walls, steel erection and connections, bridge piers, abutments, bearings, beams, bridge backfill |
| `external` | external walls, curtain wall, cladding, rack spool laydown (industrial), civil signs, lighting, small-building envelope |
| `roof` | roof decks and membranes; civil bridge deck and parapets (see below) |
| `ceiling_void` | ducts, pipes, trays, cable pull, sprinklers, terminals, light fittings, ceilings, pipe insulation |
| `walls` | partitions, doors, windows, lead lining, boarding, panels, fixtures, blockwork |
| `floor` | floor finishes; civil subgrade, subbase, base, kerbs, asphalt, markings, guardrail, landscape and approach slabs |
| `plant_pad` | AHUs, chillers, transformers, switchgear, tanks, modules, pumps, medical devices, instrument installs (industrial) |
| `any` | inspections, tests, commissioning, set-out, paperwork, traffic management, default step, healthcare fire-stopping |

Deviations from the plain reading, made to avoid deadlocks when a card releases one station at a time:

* Civil bridge deck, deck waterproofing and parapets use `roof` (the top of the structure) so they are not in the same package as the piers; otherwise a pier, the beam lift and the deck would share one concrete `structure` package and wait on each other.
* Civil `STR-SLAB-POUR` (approach and base slabs) is `floor`, and `CIV-STRUCT-BACKFILL` is `structure`, because the approach slab needs the backfill that needs the abutment.
* Healthcare fire-stopping and gas test are `any`: they come after the ceiling-void services but share a trade with framing, and must not be in the framing package.
* `exclusive_faces` is `["floor"]` for industrial and healthcare and `["floor", "below_ground"]` for civil, so paving and trenching in one zone slow each other.

### Crew profiles

`crew_profile` is set only where the demand is real. `min` is a hard minimum for the package (crews below it do not progress).

| Sector | min 2 | ideal and max given |
| --- | --- | --- |
| industrial | piling, footing, pile cap, pad, ground slab, wall, mezzanine slab pours; steel columns, beams, rack erection; roof deck | module, tank, heavy equipment, transformer, switchgear: 2 / 2 / 3; pump set 1 / 1 / 2 |
| civil | piling, pile cap, pier, abutment, deck pours; bridge beam lift, steel erect, culvert units | beam lift 2 / 2 / 3; asphalt base and wearing course min 3; equipment set 1 / 1 / 2 |
| healthcare | piling, ground slab, suspended slab and wall pours; steel erection; curtain wall; roof | AHU, chiller, transformer, switchgear 2 / 2 / 3; MRI and CT min 1, ideal 2, max 2; steriliser 1 / 1 / 2 |

Because of the minimums the civil scenarios were changed so every trade can reach its minimum: piling 2 crews, erection 2 and paving 3 at standard and hard (tutorial already had them). The industrial and healthcare scenarios already offered at least 2 crews of every trade that has a min of 2.

### Packaging

| Sector | `target_duration_days` | `max_crew_days_per_package` |
| --- | --- | --- |
| healthcare | 10 | 50 |
| industrial | 15 | 80 |
| civil | 10 | 60 |

`group_by` is the default `zone_id, phase, trade, work_face`; `max_over_ideal` 1 and `over_ideal_factor` 0.6 are written out explicitly.

### Sequence cards

Each card covers every phase of the sector in order, so a zone running the recipe never has a package parked in the implicit trailing `Other` station that something else waits on. A script checks, per card, that no step waits for a predecessor (same element, host, cell, zone or system) in a later station and that every phase has a station. Packages that no station selects (the default `GEN-ELEM-INSTALL`, civil equipment sets) end up in `Other`, which nothing depends on. Stations in already finished phases complete instantly, which is why room recipes start with mobilise, substructure, structure and envelope stations with a 1 week takt.

| Sector | Card | Zone tag | Notes |
| --- | --- | --- | --- |
| healthcare | `card_or_room` | `or_room` | plant, frame, MEP, tests and fire-stopping, lead lining, ceilings, floors, finish, equipment, commissioning |
| healthcare | `card_imaging` | `imaging` | shielding 2 weeks, scanner install 3 weeks, commissioning 2 weeks |
| healthcare | `card_plant_room` | `plant_room` | plant set 3 weeks, combined services rough-in, then enclosing interiors |
| healthcare | `card_ward_generic` | none | default recipe |
| industrial | `card_pipe_rack` | `pipe_rack` | foundations, rack steel, spool lay, hydrotest, tray, insulation, flush |
| industrial | `card_process_unit` | `process_unit` | pads, heavy lifts (3 weeks), tie-in piping, E and I, insulation, start-up |
| industrial | `card_building_bay` | none | steel frame, cladding, roof, walls and openings, services, finishes |
| civil | `card_bridge` | `bridge` | piles, caps, culvert units, piers and abutments, backfill, approach slabs, bearings and beams, deck, parapets |
| civil | `card_road_segment` | none | traffic stage, utilities, earthworks, drainage, subgrade, kerbs, asphalt, finishing |
| civil | `card_culvert` | `culvert` | trench, precast units, headwalls and backfill |

All cards use `auto_staff: "ideal"`. Each hard scenario also carries a scenario-level override of one card (`card_process_unit`, `card_road_segment`, `card_or_room`) with every station longer than one week shortened by one week; the id matches, so it replaces the library card. The zone tags `or_room`, `imaging`, `plant_room`, `process_unit`, `bridge`, `culvert` and `segment` depend on the synthetic generators; the tags `pipe_rack` and `pressure_room` already exist there.

### Second shift and Gantt defaults

| Level | `max_zones` | `cost_factor` | `risk_factor` | `gantt_visible_default` |
| --- | --- | --- | --- | --- |
| tutorial | 3 | 2.0 | default 1.5 | false |
| standard | default 2 | default 2.2 | default 1.5 | true |
| hard | 1 | 2.5 | 1.8 | true |

`forbidden_zone_tags` is `occupied_adjacent` for healthcare (quiet hours next to the live ward) and industrial (the default), and `live_traffic` for civil (no night shift next to the live carriageway). Productivity and inspection-fail factors stay at the schema defaults (1.8 and +0.05).

## Virtual steps for the construction logic library

For the recipes in `data/logic/` each library gained about 20 non-BIM steps with the same ids in all three sectors: `GEN-SURVEY-SETOUT`, `GEN-SURVEY-ASBUILT`, `GEN-UTIL-LOCATE`, `GEN-DEWATER-INSTALL`, `GEN-DEWATER-RUN`, `GEN-SHORING-INSTALL`, `GEN-SHORING-REMOVE`, `GEN-SCAFFOLD-ERECT`, `GEN-SCAFFOLD-DISMANTLE`, `GEN-TW-CHECK`, `GEN-LIFT-PLAN`, `GEN-CRANE-MOBILISE`, `GEN-PERMIT-WORK`, `GEN-PERMIT-HOT`, `GEN-HYDROTEST`, `GEN-PRECOMM-CHECK`, `GEN-CURING-WATCH`, `GEN-ANCHOR-SURVEY`, `GEN-VENDOR-REP` and `GEN-PUNCH-CLEAR` (the last already existed in civil). They are tagged `virtual`, have work face `any` except dewatering and shoring (`below_ground`), and no mapping rule emits them: they only appear when a recipe or a manual sequence adds them. Library sizes are now 79 steps (industrial), 70 (civil) and 77 (healthcare), so the earlier 30 to 60 target no longer applies.

No new trade was added: crews would also have needed new entries in every scenario's `crews_available`, which the logic work package does not own. Instead each step uses an existing, sensible trade, for example survey and utility locate on the civil crew (industrial), the QA team (civil) and the groundworks crew (healthcare), scaffold on the steel, erection and envelope crews, paperwork on the commissioning team or QA team, hydrotest on the piping, utilities and plumbing crews. Paperwork steps have `requires_access: false`.

Existing steps gained optional predecessors on the virtual steps (all skipped when the virtual task does not exist, so the synthetic baselines are unchanged): every `requires_crane` step waits for `GEN-LIFT-PLAN` in the zone, excavation and trenching wait for set-out, utility locate, shoring and dewatering install, piling waits for set-out, hot work steps wait for `GEN-PERMIT-HOT`, equipment set steps wait for `GEN-ANCHOR-SURVEY`, and civil bearings wait for the anchor survey. `GEN-HYDROTEST` requires pipe installation in the same system. Phase ordering is preserved: virtual set-out, lift plan and permits are in the mobilise phase.

## Recipes attached to mapping rules

Rules may carry `recipe: "rec_..."` (see `data/logic/README.md`). The recipe is expanded once per matched element, so the rules attach it to one anchor per installation: industrial modules, tanks, transformers, pumps, turbines and compressors, exchangers, vessels, pile caps, pit excavations, rack lines (one expansion loop each) and slabs in the heavy-lift bays; civil pile caps (bridge pier), the first girder of each span (deck), culvert segments, headwalls (retaining wall recipe), the first segment of each drain and each diverted service, one wearing-course element, signs and temporary signs; healthcare MRI, CT, sterilisers, AHUs, the first line of each medical gas system, one ceiling per operating room or pressure room, dampers next to the live ward and the ICRA slabs. About 15 new narrow rules (clones of the generic ones) exist for this, with `_lead`, `-bay`, `-or` or `-pressure` style ids. The healthcare hygienic floor rule now only matches `FLOORING` so hygienic ceilings reach the ceiling rules.

Result on the synthetic bundles (standard scenario, `build-samples`):

| Sector | Rules with a recipe | Virtual tasks | Total tasks (before) | Sequencing gaps | Baseline weeks (before) |
| --- | --- | --- | --- | --- | --- |
| industrial | 14 | 257 | 2189 (1931) | 0 | 39 (36) |
| civil | 9 | 167 | 1755 (1588) | 0 | 24 (20) |
| healthcare | 9 | 161 | 1526 (1370) | 0 | 49 (47) |

To keep the gaps at zero the library predecessors on virtual steps were reduced to cross-element logic: civil `GEN-SHORING-REMOVE` moved to the structures phase and `GEN-DEWATER-RUN` to the drainage phase, `GEN-HYDROTEST` is no longer `required`, civil anchor survey and curing watch wait only for the set-out, and `STR-BEARING-SET` no longer waits for the anchor survey. Crane steps keep waiting for the lift plan in their zone.
