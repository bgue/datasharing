# Live hospital tie-in during a shutdown window

*Recipe `rec_live_hospital_tie_in`, sector `healthcare`, typical duration 1 to 6 weeks.*

Permit and locate, set ICRA, plan the isolation, hot work permit, make the tie-in with test, restore and verify before handback.

## Rationale

A tie-in has a window of hours, not weeks. The recipe moves everything that can be done before the window ahead of it, and makes the permit the first step because without it nothing else can be booked.

## Sequence

1. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Clinical risk assessment and isolation plan.
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; optional; hold point: icra inspection)
4. `GEN-PERMIT-HOT` Hot work permit and fire watch (virtual task, permit marker)
5. `PLB-PIPE-INSTALL` Install water, waste and heating pipe (BIM-bound, from self element; optional). Prefabricate before the window; connect during the window.
6. `MEP-FITTING-INSTALL` Install fittings, dampers and valves (BIM-bound, from self element). Valves and tie-in fittings.
7. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection). Pressure test of the new connection before reinstatement.
8. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from self element; optional)
9. `CX-POWER-ENERGISE` Energise and test electrical systems (BIM-bound, from self element; optional; hold point: electrical inspection)
10. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from self element; optional)
11. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; optional; hold point: icra inspection)

## Ordering beyond the chain

* `GEN-PERMIT-WORK` before `PLB-PIPE-INSTALL` (FS): No tie-in without an approved window

## Prerequisites

* Site: access_road
* Permits: work, hot_work, isolation, icra
* Information: isolation plan, clinical risk assessment, contingency plan

## Checks and hold points

* Prefabrication complete before the window opens
* Contingency for failed tie-in documented
* Restoration verified with the clinical team

## Typical pitfalls

* Starting the window with unfinished prefabrication
* No contingency if the test fails
* Overrunning the window into clinical hours

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)
* Healthcare estates guidance for planned shutdowns and tie-ins

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

