# Piled foundation with pile caps

*Recipe `rec_piled_foundation`, sector `industrial`, typical duration 4 to 10 weeks.*

Set out and drive piles, test their integrity, survey as-built positions, excavate and pour pile caps with a cure watch.

## Rationale

A piled foundation is a chain of tolerances: set-out, pile, as-built, cap. Surveying the as-built positions before the cap formwork catches off-position piles while they can still be fixed on paper rather than in concrete.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Piling near live services or plant needs a permit.
4. `CIV-PILE-DRIVE` Drive piles (BIM-bound, from self element; hold point: geotechnical inspection). Integrity testing of a sample of piles.
5. `pile_survey` As-built survey and record (virtual task, survey marker). As-built pile positions and levels, check against the pile cap design.
6. `CIV-EARTH-CUT` Excavate pad, trench or bulk cut (BIM-bound, from foundation element). Excavate to cap level and trim pile heads.
7. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element)
8. `STR-PILECAP-POUR` Pour pile cap (BIM-bound, from foundation element; hold point: structural inspection)
9. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
10. `CIV-BACKFILL-COMPACT` Backfill and compact around foundation (BIM-bound, from foundation element)

## Ordering beyond the chain

* `CIV-PILE-DRIVE` before `STR-PILECAP-POUR` (FS with a lag of 3 days): Allow heave and testing before the cap is cast

## Prerequisites

* Equipment: piling_rig, excavator
* Site: access_road, laydown>=2
* Permits: work
* Information: pile schedule, ground investigation, working platform design

## Checks and hold points

* Working platform designed for the rig
* Pile set-out checked by a second person
* Integrity test results before cap reinforcement
* Pile head tolerances recorded

## Typical pitfalls

* Casting caps before pile tests are approved
* No as-built survey so cap rebar clashes with pile heads
* Rig on an unprepared platform
* Driving piles near live services without a permit

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Piling construction guidance (bored and driven piles, working platforms)
* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

