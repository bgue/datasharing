# Pressure-controlled room (isolation or protective environment)

*Recipe `rec_pressure_room`, sector `healthcare`, typical duration 8 to 16 weeks.*

Seal the envelope, install ducts and HEPA terminals, floors and doors, balance, test the pressure differential and hand over after ICRA clearance.

## Rationale

A pressure room is a mechanical system in which the building envelope is a component. Sealing, balance and test therefore appear as ordered steps; skipping one fails the commissioning.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection)
2. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element)
3. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from zone element)
4. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; hold point: fire inspection). Seal every penetration for air tightness.
5. `ARC-WALL-BOARD` Board, tape and finish partition walls (BIM-bound, from zone element)
6. `ARC-CEILING-CLOSE` Close ceiling (BIM-bound, from zone element). Sealed ceiling.
7. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element)
8. `ARC-DOOR-INSTALL` Hang doors and ironmongery (BIM-bound, from host element). Sealed doors with closers.
9. `MEP-DIFFUSER-SET` Set diffusers and grilles (BIM-bound, from zone element). HEPA terminals.
10. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from system element)
11. `CX-PRESSURE-TEST` Room pressure differential test (BIM-bound, from zone element; hold point: mechanical inspection). Pressure differential with doors closed and open.
12. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Monitor and alarm check.
13. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element)
14. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection)

## Ordering beyond the chain

* `FIR-STOP-INSTALL` before `ARC-WALL-BOARD` (FS): Seal before closing
* `CX-AIR-BALANCE` before `CX-PRESSURE-TEST` (FS): Balance first, then verify differential

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: icra
* Information: ventilation design, pressure regime schedule

## Checks and hold points

* Smoke test for envelope leakage
* Pressure differential at design airflow
* Alarm and monitor function test

## Typical pitfalls

* Unsealed penetrations that make the differential unreachable
* Balancing before the room is finished
* Doors that do not close against the pressure

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Healthcare ventilation guidance (air change rates, pressure regimes, validation)
* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

