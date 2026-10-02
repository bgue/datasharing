# Shell-and-tube heat exchanger set

*Recipe `rec_heat_exchanger`, sector `industrial`, typical duration 4 to 8 weeks.*

Pour supports, survey, lift the exchanger, set sliding and fixed saddles, tie in, hydrotest shell and tube sides, insulate and pre-commission.

## Rationale

Exchangers need maintenance space for pulling the bundle and two independent tests. The recipe keeps both visible, so insulation does not hide an untested side.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-EQPAD-POUR` Pour equipment pad with anchor bolts (BIM-bound, from foundation element; hold point: structural inspection)
3. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
5. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
6. `PRC-EQUIP-SET` Set heavy equipment or stack (BIM-bound, from self element; hold point: mechanical inspection). Fixed saddle first; allow tube bundle pull space.
7. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from system element)
8. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection). Shell side and tube side tested separately.
9. `INS-COATING-APPLY` Apply fireproofing or coating (BIM-bound, from self element)
10. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from system element)
11. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
12. `CX-PROCESS-STARTUP` Process system start-up (BIM-bound, from system element)

## Ordering beyond the chain

* `STR-EQPAD-POUR` before `PRC-EQUIP-SET` (FS with a lag of 7 days): Cure
* `GEN-HYDROTEST` before `INS-COATING-APPLY` (FS): Insulation after tests

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, laydown>=2, crane_reach
* Information: vendor drawings, lift plan

## Checks and hold points

* Bundle pull space kept clear
* Fixed and sliding saddles correct
* Both sides tested before insulation

## Typical pitfalls

* Supports placed so the bundle cannot be pulled
* Only one side hydrotested
* Insulating before the test

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* Process piping guidance (installation, pressure testing, flushing and reinstatement)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

