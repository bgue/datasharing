# Bridge beams, deck and parapets

*Recipe `rec_bridge_deck`, sector `civil`, typical duration 6 to 14 weeks.*

Close the road for the night lift, plan and mobilise the crane, lift beams, build the deck, cure, waterproof, fit parapets and survey.

## Rationale

The beam lift is the riskiest single event on a road job: a crane, a closed road and a night possession all have to coincide. The recipe puts the permit and the lift plan in front of everything and treats the cure watch as time, not labour.

## Sequence

1. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Night possession and road closure permit.
2. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Beam lift plan with crane positions and exclusion zone.
3. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
4. `CIV-TM-STAGE1` Traffic management stage 1: close lane and set cones (BIM-bound, from self element; optional). Road closure for the lift.
5. `STR-BEAM-LIFT` Lift precast beam (night closure) (BIM-bound, from self element; hold point: structural inspection). Beams lifted onto bearings at night.
6. `GEN-SCAFFOLD-ERECT` Erect access scaffold (virtual task, scaffold marker). Edge protection and soffit access.
7. `STR-DECK-REBAR` Fix deck reinforcement and formwork (BIM-bound, from self element; optional)
8. `STR-DECK-POUR` Pour bridge deck (BIM-bound, from self element; optional; hold point: structural inspection)
9. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; optional; 7 days, time driven)
10. `STR-DECK-WATERPROOF` Waterproof deck (BIM-bound, from self element; optional)
11. `STR-PARAPET-INSTALL` Install bridge parapet or barrier (BIM-bound, from self element; optional)
12. `GEN-SCAFFOLD-DISMANTLE` Dismantle access scaffold (virtual task, scaffold marker)
13. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Deck levels and camber before surfacing.

## Ordering beyond the chain

* `STR-BEAM-LIFT` before `STR-DECK-REBAR` (FS): Deck forms bear on the beams
* `STR-DECK-POUR` before `STR-DECK-WATERPROOF` (FS with a lag of 7 days): Cure before waterproofing

## Prerequisites

* Equipment: crawler_crane, concrete_pump
* Site: access_road, laydown>=3, crane_reach
* Permits: road_closure, work_at_height
* Information: lift plan, traffic management plan, deck pour plan

## Checks and hold points

* Possession confirmed before mobilising the crane
* Lift plan reviewed with the crane supplier
* Cube results before waterproofing
* Level survey before surfacing

## Typical pitfalls

* Mobilising the crane without a confirmed possession
* Deck pour before the beams are secured laterally
* Waterproofing a green deck
* No camber survey before surfacing

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Bridge construction guidance (bearings, precast beams, decks, parapets)
* National lifting operations guidance (lift planning and appointed person duties)
* Temporary traffic management guidance for road works (signs, lighting and guarding)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

