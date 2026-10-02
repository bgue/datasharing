# Precast culvert crossing

*Recipe `rec_culvert`, sector `civil`, typical duration 3 to 8 weeks.*

Set out, dewater, shore and excavate the trench, lift culvert units, build headwalls and backfill, then survey.

## Rationale

A culvert is a short job that depends on controlling water. Dewatering runs in parallel with the dig, and backfill is symmetric and delayed until the headwalls have strength.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-DEWATER-INSTALL` Install wellpoints or sump pumps (virtual task, dewatering marker)
4. `GEN-SHORING-INSTALL` Install trench or pit shoring (virtual task, shoring marker)
5. `CIV-TRENCH-DIG` Dig drainage or culvert trench (BIM-bound, from self element)
6. `GEN-DEWATER-RUN` Run dewatering (time driven) (virtual task, dewatering marker; 10 days, time driven; starts with CIV-TRENCH-DIG)
7. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
8. `CIV-CULVERT-SET` Set precast culvert units (BIM-bound, from self element; hold point: structural inspection). Unit joints sealed and checked.
9. `STR-WALL-POUR` Pour retaining or building wall (BIM-bound, from self element). Headwalls and wing walls.
10. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
11. `CIV-STRUCT-BACKFILL` Backfill behind structure (BIM-bound, from self element). Backfill symmetrically in layers.
12. `GEN-SHORING-REMOVE` Remove trench or pit shoring (virtual task, shoring marker)
13. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker)

## Ordering beyond the chain

* `STR-WALL-POUR` before `CIV-STRUCT-BACKFILL` (FS with a lag of 7 days): Wall strength before backfill loading

## Prerequisites

* Equipment: excavator, mobile_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: work, watercourse_consent
* Information: culvert unit drawings, lift plan, diversion plan for the watercourse

## Checks and hold points

* Watercourse consent and flow diversion in place
* Joint seals checked before backfill
* Symmetric backfill and compaction

## Typical pitfalls

* Digging before the flow is diverted
* One-sided backfill displacing the units
* Skipping joint checks on precast units

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Highway drainage construction guidance (pipe bedding, testing, backfill)
* Excavation safety guidance (shoring, access, services avoidance, hazard of buried services)
* National lifting operations guidance (lift planning and appointed person duties)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

