# Bridge pier on piled foundation

*Recipe `rec_bridge_pier`, sector `civil`, typical duration 8 to 18 weeks.*

Set out, drive piles, cast cap, build the pier with scaffold and curing watch, survey the bearing seat and set bearings.

## Rationale

A pier is a sequence in which each step creates the datum for the next. The recipe builds survey, cure and temporary works checks into the chain so beams are never lowered onto a seat that has not been measured.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-DEWATER-INSTALL` Install wellpoints or sump pumps (virtual task, dewatering marker; optional)
3. `CIV-PILE-DRIVE` Drive or bore pile (BIM-bound, from self element; hold point: geotechnical inspection)
4. `pile_survey` As-built survey and record (virtual task, survey marker)
5. `STR-CAP-REBAR` Fix pile cap reinforcement (BIM-bound, from foundation element)
6. `STR-CAP-POUR` Pour pile cap (BIM-bound, from foundation element; hold point: structural inspection)
7. `cap_cure` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
8. `GEN-SCAFFOLD-ERECT` Erect access scaffold (virtual task, scaffold marker)
9. `STR-PIER-POUR` Form, reinforce and pour pier or column (BIM-bound, from self element; hold point: structural inspection)
10. `pier_cure` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
11. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker). Bearing seat levels and plan position.
12. `STR-BEARING-SET` Set bridge bearings (BIM-bound, from self element; hold point: structural inspection). 10 week lead item.
13. `GEN-SCAFFOLD-DISMANTLE` Dismantle access scaffold (virtual task, scaffold marker)

## Ordering beyond the chain

* `STR-CAP-POUR` before `STR-PIER-POUR` (FS with a lag of 7 days): Cap strength before pier load

## Prerequisites

* Equipment: piling_rig, mobile_crane, concrete_pump
* Site: access_road, laydown>=3, crane_reach
* Permits: work, work_at_height
* Information: pile schedule, pier formwork design, bearing schedule

## Checks and hold points

* Pile integrity tests before cap reinforcement
* Formwork design check before the pier pour
* Bearing seat survey before bearings arrive
* Concrete cube results before beams land

## Typical pitfalls

* Ordering bearings late: a 10 week lead can stall beam erection
* Skipping the bearing seat survey
* Formwork pressure and pour rate not checked
* Scaffold removed before the cure period

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Bridge construction guidance (bearings, precast beams, decks, parapets)
* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)
* Temporary works procedure guidance (design check, permit to load, permit to dismantle)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

