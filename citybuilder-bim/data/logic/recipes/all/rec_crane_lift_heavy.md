# Heavy crane lift

*Recipe `rec_crane_lift_heavy`, sector `all`, typical duration 1 to 4 weeks.*

Permit, survey, lift plan, crane mobilisation and load test, the lift itself (bound to a BIM step), as-built survey and demobilisation attendance.

## Rationale

A heavy lift is a paperwork-heavy event: most of the time is plan, mobilise and check, not hooking up. The recipe is a wrapper to nest or copy next to the actual BIM lift step, so the planner sees the whole cost and the order.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker). Crane position, ground bearing check and exclusion zone.
2. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Lifting permit and exclusion zone approval.
3. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Plan by an appointed person: weights, radii, rigging, weather limits.
4. `GEN-TW-CHECK` Temporary works design check and permit to load (virtual task, permit marker; optional). Crane base and mat design check.
5. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker). Assemble, inspect and load-test.
6. `pre_lift_check` Pre-commissioning checks (virtual task, test marker). Toolbox talk, rigging inspection, weather check on the day.
7. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Positions of the set item against the design.
8. `GEN-PUNCH-CLEAR` Punch list close-out (virtual task, test marker; optional). Demobilise and close the permit.

## Ordering beyond the chain

* `GEN-LIFT-PLAN` before `GEN-CRANE-MOBILISE` (FS): The crane is selected from the plan
* `pre_lift_check` before `GEN-SURVEY-ASBUILT` (FS): The lifted step runs between these two; bind it from the sector library

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: lifting
* Information: lift plan, ground bearing assessment, weight and centre of gravity data

## Checks and hold points

* Appointed person signs the plan
* Ground bearing confirmed under the crane and the load path
* Weather limits stated and checked on the day
* Exclusion zone established

## Typical pitfalls

* Crane booked before the weight is known
* Ground bearing assumed
* Lifting in wind above the limit
* Overlapping lifts in the same exclusion zone

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* National lifting operations guidance (lift planning and appointed person duties)
* Temporary works procedure guidance (design check, permit to load, permit to dismantle)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

