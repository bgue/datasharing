# Operating room fit-out

*Recipe `rec_or_room`, sector `healthcare`, typical duration 10 to 20 weeks.*

Build an operating room as a repeating recipe: ICRA, framing, services, gas test, fire-stopping, sealed boarding and ceilings, hygienic floor, theatre fixtures, air balance, pressure test, clean and handover.

## Rationale

An operating room is the most constrained room in a hospital: sealed finishes, high air change rates, gas outlets and ICRA. The recipe encodes the order that avoids rework: test before close, ceilings before floors, balance before clean.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; optional; hold point: icra inspection)
2. `GEN-PERMIT-HOT` Hot work permit and fire watch (virtual task, permit marker). Brazing of medical gas pipework.
3. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element; optional)
4. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from zone element; optional)
5. `PLB-PIPE-INSTALL` Install water, waste and heating pipe (BIM-bound, from zone element; optional)
6. `MED-GAS-PIPE-INSTALL` Install medical gas pipe (BIM-bound, from self element; optional)
7. `MED-GAS-TEST` Medical gas pressure test and purge (BIM-bound, from self element; optional; hold point: medical_gas inspection)
8. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; optional; hold point: fire inspection)
9. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from zone element; optional; hold point: electrical inspection)
10. `ARC-WALL-BOARD` Board, tape and finish partition walls (BIM-bound, from zone element; optional). Sealed, smooth walls.
11. `ARC-CEILING-CLOSE` Close ceiling (BIM-bound, from zone element). Sealed hygienic ceiling.
12. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element; optional). Welded, coved flooring.
13. `ARC-DOOR-INSTALL` Hang doors and ironmongery (BIM-bound, from host element; optional)
14. `ELE-LIGHT-INSTALL` Install light fittings and devices (BIM-bound, from zone element; optional)
15. `MEP-DIFFUSER-SET` Set diffusers and grilles (BIM-bound, from zone element; optional). HEPA terminals.
16. `MED-EQUIP-INSTALL` Install fixed clinical equipment (BIM-bound, from zone element; optional). Pendants and theatre lights.
17. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from self element; optional)
18. `CX-PRESSURE-TEST` Room pressure differential test (BIM-bound, from zone element; hold point: mechanical inspection)
19. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element; optional)
20. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; optional; hold point: icra inspection)

## Ordering beyond the chain

* `MED-GAS-TEST` before `ARC-WALL-BOARD` (FS): Walls cannot close over untested gas pipe
* `FIR-STOP-INSTALL` before `ARC-WALL-BOARD` (FS): Fire-stopping inspected before boarding
* `ARC-CEILING-CLOSE` before `ARC-FLOOR-FINISH` (FS): Ceilings before floors

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: hot_work, icra
* Information: room data sheet, ventilation design, medical gas layout

## Checks and hold points

* ICRA barrier verified before dusty work
* Medical gas pressure test before wall closure
* Fire-stopping inspected before boarding
* Air balance and pressure regime test before clean
* ICRA clearance before barrier removal

## Typical pitfalls

* Closing walls before the gas test
* Dust from late cutting after HEPA terminals are in
* Air balance before the room is sealed
* Equipment arriving before the floor is finished

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)
* Medical gas pipeline system guidance (design, installation, testing and verification)
* Healthcare ventilation guidance (air change rates, pressure regimes, validation)
* Fire safety guidance for healthcare buildings (compartmentation and fire-stopping)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

