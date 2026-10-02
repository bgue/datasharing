# Drainage run with chambers

*Recipe `rec_drainage_run`, sector `civil`, typical duration 3 to 12 weeks.*

Set out, locate services, set traffic management, shore and dig the trench, lay pipe and chambers, test and backfill.

## Rationale

Drainage is the first thing built below the pavement and the last thing anyone wants to dig up. The recipe gates backfill on the test and records inverts, so the pavement team inherits a known quantity.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `CIV-TM-STAGE1` Traffic management stage 1: close lane and set cones (BIM-bound, from self element; optional)
4. `GEN-SHORING-INSTALL` Install trench or pit shoring (virtual task, shoring marker)
5. `GEN-DEWATER-INSTALL` Install wellpoints or sump pumps (virtual task, dewatering marker; optional)
6. `CIV-TRENCH-DIG` Dig drainage or culvert trench (BIM-bound, from system element)
7. `CIV-DRAIN-INSTALL` Lay drainage pipe (BIM-bound, from system element). Bedding and laying to level.
8. `CIV-CHAMBER-SET` Set manhole or gully chamber (BIM-bound, from system element)
9. `CIV-DRAIN-TEST` CCTV and air test drain (BIM-bound, from system element; hold point: plumbing inspection). Air or water test and CCTV.
10. `CIV-DRAIN-BACKFILL` Backfill and compact trench (BIM-bound, from system element)
11. `GEN-SHORING-REMOVE` Remove trench or pit shoring (virtual task, shoring marker)
12. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Invert levels and chamber positions.

## Ordering beyond the chain

* `CIV-DRAIN-TEST` before `CIV-DRAIN-BACKFILL` (FS): Backfill only after the test

## Prerequisites

* Equipment: excavator
* Site: access_road, laydown>=2
* Permits: work
* Information: drainage drawings, service drawings

## Checks and hold points

* Pipe bedding and levels checked before backfill
* Test results before backfill
* Chamber covers set to final level later

## Typical pitfalls

* Backfilling before the pipe test
* Falls reversed because levels were not checked
* Open trenches left over weekends without guarding

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Highway drainage construction guidance (pipe bedding, testing, backfill)
* Excavation safety guidance (shoring, access, services avoidance, hazard of buried services)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

