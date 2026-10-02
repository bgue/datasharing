# 01 — Game Design: SiteBuilder

> Gameplay-first design. The BIM model is the level. The schedule is the save game.

## 1. Fantasy and pitch

You are the construction planner on a real project. The architect and engineers
have already decided *what* gets built: that is the BIM model, shown as a ghost
over an empty site. Your job is *how* and *when*. You lay out the site like a
city (roads, cranes, laydown yards, welfare cabins, hoarding), hire crews, and
release work package by package, bay by bay, floor by floor. Every element in
the model must be built, inspected and commissioned before the handover date,
without blowing the budget or hurting anyone.

One sentence: **SimCity where the zoning plan is an IFC file and the win
condition is handover.**

## 2. Core loop (one in-game week)

```
 PLAN  ──>  RELEASE  ──>  SIMULATE  ──>  REVIEW  ──>  (next week)
 look at     assign       crews work,     S-curve,
 ghost BIM   crews to     deliveries      events,
 and site    work         land, events    inspections,
 layout      packages     fire            cash
             in zones
```

* **Plan** (paused): the player inspects the ghost model, filters by storey,
  system (structure, envelope, MEP, finishes) or zone, and sees which steps
  are *ready* (all predecessors done), *blocked* (why) and *done*.
* **Release**: drag a crew onto a zone to release the ready steps there. The
  player can also place/move site logistics (crane, laydown, access road,
  hoarding, welfare, ICRA barrier, traffic diversion).
* **Simulate**: time advances one week (or runs at 1x/2x/4x until an event).
  Each released step accumulates progress from its crew's productivity,
  modified by congestion, weather, access, equipment and learning curve.
* **Review**: weekly report. Planned vs. actual S-curve, cash, incidents,
  inspection results, upcoming long-lead deliveries, late steps.

A level lasts 20 to 120 in-game weeks, 15 to 60 real minutes.

## 3. What the player controls

| Control | Grid? | Effect |
| --- | --- | --- |
| Site layout tiles (haul road, laydown, crane pad, welfare, hoarding, gate, ICRA barrier, traffic cones) | Yes, Kenney GridMap | Unlock access, reach, storage; cost weekly rent |
| Crews (hire/fire per trade) | No | Weekly cost; productivity; limited supply per week |
| Work release (assign crew to zone/package) | Zone selection on grid | Starts steps in that zone |
| Equipment (tower crane, mobile crane, excavator, concrete pump, scissor lifts) | Crane pad tile + reach radius | Enables heavy-lift and height steps; weekly hire |
| Procurement (order long-lead items) | No | Delivery date = order week + lead time; late order blocks install step |
| Inspections and hold points (request) | No | Needed to unlock next phase; may fail |
| Speed (pause, 1x, 2x, 4x), overlays, filters | No | UX |

## 4. What the BIM model controls

The BIM model is loaded from `sequence.json` (see `docs/02-sequencing-model.md`),
already mapped element → step. The game never parses IFC itself.

* **Target state**: all elements present, inspected, commissioned.
* **Spatial layout**: each element is voxelised onto the site grid
  (1 cell = one structural bay ≈ 6 m × 6 m, one storey tall). A cell may hold
  many elements across many steps. Zones are groups of cells per storey.
* **Dependencies**: predecessors come from the mapping rules (slab needs
  columns below, walls need slab, ducts need structure and precede ceilings,
  equipment needs pad and crane reach, commissioning needs all MEP in system).
* **Quantities**: productivity is applied to real quantities (m³ concrete,
  tonnes steel, m² wall, m duct, each equipment).

The player cannot change the model. They can only change order, resources,
layout and procurement. Mistakes are cheap in the game and expensive in life,
which is the point.

## 5. Systems

### 5.1 Progress and productivity

Each step `s` has quantity `Q_s` and a base rate `r_s` (units per crew-day). Weekly
progress of a crew on a step:

```
progress = r_s * crew_size_factor * 5 days
         * congestion(zone)      # 1.0 at ≤ max_crews, 0.6 at +1, 0.35 at +2
         * weather(week, step)   # exterior concrete/earthworks/roofing suffer
         * access(zone)          # 0 if no haul road / crane reach when required
         * learning(step_type)   # 0.8 on first repetition → 1.0 after 3
```

Steps finish when progress ≥ Q_s. Steps with `inspection: true` then wait for an
inspection (1 week, 10% base fail → rework 25% of Q_s).

### 5.2 Space: trade stacking and congestion

Every zone has `max_crews`. Exceeding it is allowed but slows everyone (the
classic "trade stacking" failure). This is the main spatial puzzle: too few
crews and you are late; too many and they trip over each other.

### 5.3 Logistics

* **Access**: a zone is reachable if a haul-road path exists from a gate to a
  cell adjacent to the zone (BFS on the grid). Unreachable zones get access = 0
  for steps that need deliveries (almost everything except inspections).
* **Crane reach**: heavy steps (`requires_crane`) need a crane whose reach
  radius covers the zone. Tower cranes cover height; mobile cranes have
  larger footprint and weekly hire.
* **Laydown**: each released step consumes `laydown_cells` while active.
  Running out of laydown blocks new releases of material-heavy steps.
* **Deliveries**: long-lead steps (`lead_time_weeks > 0`) need the item ordered
  early. A story-sized decision in healthcare (MRI, 26 weeks) and industrial
  (transformer, 40 weeks).

### 5.4 Money

Budget = sum of step `cost` + contingency. Weekly outflow = crews + equipment
hire + rent for logistics tiles + materials when a step starts. Monthly
inflow = progress payment for inspected steps (earned value). Cash < 0 is
allowed with an overdraft fee; prolonged negative cash ends the level.

### 5.5 Safety

Each active step has `risk` (0 to 1). Weekly incident probability grows with
congestion, overtime (speed > 1x for too long) and missing hoarding/barriers.
An incident stops the zone for a week and costs score. Three recordables fail
the level on hard mode.

### 5.6 Events (weekly deck, sector-specific)

Examples: rain week (civil earthworks ×0.3), RFI on a steel connection (steel
steps in zone paused 1 week unless `rfi_buffer` card used), hospital asks for
quiet hours (no noisy steps near live ward tile), utility strike (civil, if
utility diversion step not done first), late ship from fabricator (industrial
module slips 2 weeks), infection control audit (healthcare, fail if any ICRA
barrier tile missing next to an occupied tile).

### 5.7 Phases and gates

Steps carry `phase` (mobilise, substructure, superstructure, envelope,
mep_roughin, fitout, commissioning, handover for buildings; mobilise,
traffic_stage_1..n, earthworks, structures, pavement, finishing for civil).
A **gate** is a hold point: all steps of a phase in a zone done + inspected
before the next phase can release there. Gates are where the player feels
the sequence "click" into place, floor by floor.

## 6. Sector flavour

### Industrial (process building, pipe rack, equipment)

* Few, very heavy steps. Crane placement and module delivery order dominate.
* Pipe rack is a linear structure: foundations → steel → pipe spools → insulation.
* Equipment modules need pad, crane reach and road access on the same week.
* Commissioning is system-based (power, process, utilities) not floor-based.

### Civil (road, bridge, culvert)

* Linear site. Zones are chainage segments, not storeys.
* Traffic-management stages replace storeys as the macro-sequence: you can only
  work in the lane you have closed, and closing a lane costs public goodwill
  per week.
* Earthworks balance: cut in one segment supplies fill for another; hauling
  distance (grid path length) sets productivity.
* Bridge: piles → pile caps → piers → beams (crane + road closure night) → deck → barriers.

### Healthcare (hospital wing)

* Dense MEP: many steps per cell, strict trade stacking, ceiling-close-out gates.
* Live-hospital constraints: occupied tiles adjacent to site demand ICRA
  barriers, quiet hours and infection-control inspections.
* Long-lead medical equipment (MRI, CT, sterilisers) with room-build-around rules.
* Commissioning gates are strict: pressure testing, air balancing, medical gas
  certification before any room is "handed over".

## 7. Scoring and progression

| Metric | Weight | Source |
| --- | --- | --- |
| Finish week vs. contract date | 35 | sim |
| Cost vs. budget | 25 | sim |
| Safety (incidents) | 20 | sim |
| Quality (inspection first-pass rate) | 10 | sim |
| Plan stability (changes to released plan) | 10 | sim |

Grades S/A/B/C. Each sector has 3 levels (tutorial, standard, hard) that
unlock in order. The plan the player executed is exported as
`plan_export.json` (schema `element_step_map.schema.json` with actual dates),
usable as a real 4D baseline.

## 8. Presentation (built on Kenney kit)

* Kenney grid, camera and selector are kept as is.
* BIM elements render as procedural low-poly stand-ins coloured by category
  and tinted by state: ghost (target), framed (in progress), solid (done),
  outlined green (inspected). No new art is required for v1.
* Site logistics reuse Kenney tiles: `road-*` for haul roads, `pavement` for
  laydown, `building-garage` for welfare, `grass-trees` as untouched land.
* HUD: week counter, cash, crews panel, zone inspector, S-curve mini chart,
  event toast, phase/gate tracker per storey.

## 9. Non-goals for v1

Multiplayer, actual IFC geometry rendering, cost estimating accuracy, path
finding for individual workers, procedural model generation inside Godot.
