# Medical gas pipeline system

*Recipe `rec_medical_gas_system`, sector `healthcare`, typical duration 6 to 16 weeks.*

Hot work permit, install pipework, pressure test and purge, fire-stop and close walls, install outlets, third-party certification and handover.

## Rationale

Medical gas is a safety-critical life-support system. Installation errors cause deaths, so the recipe makes the test a hold point before wall closure and certification a second hold point before first use.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-PERMIT-HOT` Hot work permit and fire watch (virtual task, permit marker). Brazing with oxygen-free nitrogen purge.
3. `MED-GAS-PIPE-INSTALL` Install medical gas pipe (BIM-bound, from system element)
4. `MED-GAS-TEST` Medical gas pressure test and purge (BIM-bound, from system element; hold point: medical_gas inspection). Standing pressure, leak and cross-connection tests.
5. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from system element; hold point: fire inspection)
6. `ARC-WALL-BOARD` Board, tape and finish partition walls (BIM-bound, from system element). Walls closed only after the test.
7. `MED-EQUIP-INSTALL` Install fixed clinical equipment (BIM-bound, from system element). Terminal units, alarms and pendants.
8. `MED-GAS-CERT` Medical gas certification (BIM-bound, from system element; hold point: medical_gas inspection). Independent verification of identity, flow and purity.
9. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Alarm panel and area valve checks.
10. `GEN-HANDOVER-DOC` Room-by-room handover documentation (BIM-bound, from system element)

## Ordering beyond the chain

* `MED-GAS-TEST` before `ARC-WALL-BOARD` (FS): No wall closure before the pressure test
* `MED-GAS-TEST` before `MED-EQUIP-INSTALL` (FS): Equipment connects to a tested system only

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: hot_work
* Information: gas layout, alarm schedule

## Checks and hold points

* Brazers qualified and records kept
* Pressure, leak and cross-connection tests witnessed
* Third-party verification before first use
* Area valves labelled

## Typical pitfalls

* Closing walls before testing
* Non-qualified brazing
* Cross-connection between gases found only at certification
* Skipping the purge

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Medical gas pipeline system guidance (design, installation, testing and verification)
* Fire safety guidance for healthcare buildings (compartmentation and fire-stopping)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

