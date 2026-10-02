# Traffic stage switch

*Recipe `rec_traffic_stage_switch`, sector `civil`, typical duration 1 to 4 weeks.*

Agree the permit, survey the new alignment, set up stage 1 and then the switch to stage 2 with signing and checks.

## Rationale

Traffic switches are short but have a long tail of approvals. The recipe puts the permit up front and adds a stability check between stages because closures cost goodwill every week they stay in place.

## Sequence

1. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Road authority approval of the traffic management plan.
2. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker). Mark taper, cone and sign positions.
3. `CIV-TM-STAGE1` Traffic management stage 1: close lane and set cones (BIM-bound, from self element; optional). Initial closure and works zone.
4. `CIV-TM-STAGE2` Traffic management stage 2: switch traffic (BIM-bound, from self element). Switch traffic to the completed carriageway.
5. `stage1_check` Pre-commissioning checks (virtual task, test marker). Night-time inspection of signing, lighting and taper.
6. `stage2_check` Pre-commissioning checks (virtual task, test marker). Post-switch drive-through and review with the road authority.
7. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Record as-implemented layout.

## Ordering beyond the chain

* `CIV-TM-STAGE1` before `CIV-TM-STAGE2` (FS with a lag of 5 days): Allow a week of observation

## Prerequisites

* Site: access_road
* Permits: road_authority
* Information: traffic management plan, signing schedule

## Checks and hold points

* Drive-through of every stage before opening
* Emergency access maintained
* Public information issued before the switch

## Typical pitfalls

* Switching at night without a check of lighting and signs
* No public information
* Leaving the old layout markings visible and confusing
* Underestimating the permit lead time

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Temporary traffic management guidance for road works (signs, lighting and guarding)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

