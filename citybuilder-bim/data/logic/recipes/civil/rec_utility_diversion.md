# Utility diversion ahead of earthworks

*Recipe `rec_utility_diversion`, sector `civil`, typical duration 3 to 12 weeks.*

Locate the service, get the owner permit, set up traffic management, shore the trench, divert the service, prove it and reinstate with as-builts.

## Rationale

A struck service stops the job and can hurt people. Diversion comes first in the sequence, and the old line is only abandoned after the new line is proved, so earthworks can proceed over known ground.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker). Trial holes at crossings.
3. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Utility owner permit and standby supervision.
4. `CIV-TM-STAGE1` Traffic management stage 1: close lane and set cones (BIM-bound, from self element). Lane closure and cones for the diversion trench.
5. `GEN-TW-CHECK` Temporary works design check and permit to load (virtual task, permit marker). Trench support design for deep sections.
6. `GEN-SHORING-INSTALL` Install trench or pit shoring (virtual task, shoring marker)
7. `CIV-UTIL-DIVERT` Divert or protect existing utility (BIM-bound, from self element)
8. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection). Pressure or integrity test of the new line.
9. `CIV-UTIL-TEST` Prove diverted service (BIM-bound, from self element; hold point: pressure_test inspection)
10. `GEN-SHORING-REMOVE` Remove trench or pit shoring (virtual task, shoring marker)
11. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). As-built record handed to the utility owner.

## Ordering beyond the chain

* `CIV-UTIL-TEST` before `GEN-SHORING-REMOVE` (FS): Keep the trench supported until the line is accepted

## Prerequisites

* Equipment: excavator
* Site: access_road
* Permits: utility_owner, work
* Information: utility drawings, owner method statement

## Checks and hold points

* Services located and exposed before mechanical digging nearby
* Owner witnesses the tie-in
* New line proved before the old line is abandoned
* As-built recorded

## Typical pitfalls

* Digging over a service that was not trial-holed
* Abandoning the old line before the new one is proved
* Traffic management removed while the trench is open
* Missing as-builts for the next contractor

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Excavation safety guidance (shoring, access, services avoidance, hazard of buried services)
* Temporary traffic management guidance for road works (signs, lighting and guarding)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

