# Generic ward or clinic fit-out

*Recipe `rec_ward_fitout`, sector `healthcare`, typical duration 8 to 16 weeks.*

The repeating recipe for wards, corridors and clinics: ICRA, framing, MEP, fire-stopping, boarding, ceilings, floors, doors, fixtures, equipment, balance and handover.

## Rationale

Wards repeat, so a stable recipe is worth more than a clever plan. The order here is the one that avoids rework: inspect before you close, close the ceiling before the floor, balance before clean.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection)
2. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element)
3. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from zone element)
4. `PLB-PIPE-INSTALL` Install water, waste and heating pipe (BIM-bound, from zone element)
5. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from zone element; hold point: electrical inspection)
6. `FIR-SPRINK-INSTALL` Install sprinkler pipework and drops (BIM-bound, from zone element; hold point: fire inspection)
7. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; hold point: fire inspection)
8. `ARC-WALL-BOARD` Board, tape and finish partition walls (BIM-bound, from zone element)
9. `ARC-CEILING-CLOSE` Close ceiling (BIM-bound, from zone element)
10. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element)
11. `ARC-DOOR-INSTALL` Hang doors and ironmongery (BIM-bound, from host element)
12. `ELE-LIGHT-INSTALL` Install light fittings and devices (BIM-bound, from zone element)
13. `MEP-DIFFUSER-SET` Set diffusers and grilles (BIM-bound, from zone element)
14. `PLB-FIXTURE-SET` Set sanitary fixtures and taps (BIM-bound, from zone element)
15. `MED-EQUIP-INSTALL` Install fixed clinical equipment (BIM-bound, from zone element). Bed-heads and nurse call.
16. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from system element)
17. `CX-FIRE-TEST` Fire alarm and sprinkler acceptance test (BIM-bound, from zone element; hold point: fire inspection)
18. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element)
19. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection)
20. `GEN-PUNCH-CLEAR` Punch list close-out (virtual task, test marker)

## Ordering beyond the chain

* `FIR-STOP-INSTALL` before `ARC-WALL-BOARD` (FS): Inspect before closing
* `ARC-CEILING-CLOSE` before `ARC-FLOOR-FINISH` (FS): Ceilings before floors
* `CX-AIR-BALANCE` before `GEN-ICRA-CLOSEOUT` (FS): Balance before clearance

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: icra
* Information: room data sheets, fire strategy

## Checks and hold points

* Above-ceiling inspection before close-out
* ICRA clearance before barrier removal
* Punch list closed before handover

## Typical pitfalls

* Boarding before inspections
* Fixtures before floors, then damaged
* Handing over before air balance

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)
* Fire safety guidance for healthcare buildings (compartmentation and fire-stopping)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

