# Retaining wall with temporary works

*Recipe `rec_retaining_wall`, sector `civil`, typical duration 4 to 10 weeks.*

Check the temporary works, shore and excavate, pour footing and wall, cure, backfill in layers and recover the shoring.

## Rationale

Backfilling a wall too early is the classic failure. The recipe separates pour, cure and backfill with a lag and treats the excavation support as a design-checked temporary works item.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-TW-CHECK` Temporary works design check and permit to load (virtual task, permit marker). Excavation support and surcharge design.
4. `GEN-SHORING-INSTALL` Install trench or pit shoring (virtual task, shoring marker)
5. `CIV-EARTH-CUT` Excavate cut to formation (BIM-bound, from foundation element)
6. `STR-FOOT-REBAR` Fix footing reinforcement (BIM-bound, from foundation element)
7. `STR-FOOT-POUR` Pour footing (BIM-bound, from foundation element; hold point: structural inspection)
8. `STR-WALL-POUR` Pour retaining or building wall (BIM-bound, from self element; hold point: structural inspection)
9. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
10. `CIV-STRUCT-BACKFILL` Backfill behind structure (BIM-bound, from self element). Free-draining fill in layers with drainage behind the wall.
11. `GEN-SHORING-REMOVE` Remove trench or pit shoring (virtual task, shoring marker)
12. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker)

## Ordering beyond the chain

* `STR-WALL-POUR` before `CIV-STRUCT-BACKFILL` (FS with a lag of 7 days): Wall strength and drainage before backfill

## Prerequisites

* Equipment: excavator, concrete_pump
* Site: access_road, laydown>=2
* Permits: work
* Information: wall design, temporary works design

## Checks and hold points

* Temporary works design check
* Wall strength before backfill
* Drainage layer and weep holes installed

## Typical pitfalls

* Compacting heavy plant against a young wall
* No drainage behind the wall
* Excavation support designed after the dig started

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Temporary works procedure guidance (design check, permit to load, permit to dismantle)
* Excavation safety guidance (shoring, access, services avoidance, hazard of buried services)
* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

