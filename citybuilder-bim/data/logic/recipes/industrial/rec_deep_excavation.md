# Deep excavation or pit with shoring and dewatering

*Recipe `rec_deep_excavation`, sector `industrial`, typical duration 3 to 8 weeks.*

Excavate a deep pit or sump for a basement, pump pit or tank base: survey, locate services, approve the shoring design, install dewatering and shoring, dig, survey the formation, backfill and recover the shoring.

## Rationale

Deep pits fail through water, collapse and struck services. The recipe puts the paperwork and the temporary works in front of the dig and keeps dewatering running in parallel with excavation, so the team never excavates below the water table without control.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Excavation permit with service drawings attached.
4. `GEN-TW-CHECK` Temporary works design check and permit to load (virtual task, permit marker). Shoring design check before any digging.
5. `GEN-DEWATER-INSTALL` Install wellpoints or sump pumps (virtual task, dewatering marker)
6. `GEN-SHORING-INSTALL` Install trench or pit shoring (virtual task, shoring marker; hold point: structural inspection). Shoring inspected before each lift of excavation.
7. `CIV-EARTH-CUT` Excavate pad, trench or bulk cut (BIM-bound, from self element). Dig in stages, shoring follows the excavation.
8. `GEN-DEWATER-RUN` Run dewatering (time driven) (virtual task, dewatering marker; 15 days, time driven; starts with CIV-EARTH-CUT). Run until the base slab has enough weight against uplift.
9. `formation_survey` As-built survey and record (virtual task, survey marker). Formation level and clearance check.
10. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from self element). Blinding then base reinforcement.
11. `CIV-BACKFILL-COMPACT` Backfill and compact around foundation (BIM-bound, from self element; optional)
12. `GEN-SHORING-REMOVE` Remove trench or pit shoring (virtual task, shoring marker)

## Ordering beyond the chain

* `GEN-DEWATER-INSTALL` before `CIV-EARTH-CUT` (FS): Groundwater must be controlled before the cut goes below the water table

## Prerequisites

* Equipment: excavator
* Site: access_road, laydown>=2
* Permits: excavation
* Information: service drawings, shoring design, geotechnical report

## Checks and hold points

* Buried services located and marked before the first dig
* Shoring design checked and permit to load issued
* Daily inspection of the excavation face and shoring
* Formation level surveyed before any concrete

## Typical pitfalls

* Starting the dig before the shoring design is checked
* Stopping dewatering before the structure has enough weight against uplift
* Surcharge loads (cranes, spoil heaps) too close to the edge
* Forgetting to survey the formation before blinding

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Excavation safety guidance (shoring, access, services avoidance, hazard of buried services)
* Temporary works procedure guidance (design check, permit to load, permit to dismantle)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

