# CT suite

*Recipe `rec_imaging_suite_ct`, sector `healthcare`, typical duration 10 to 22 weeks.*

Lead-lined room, services, floor, crane or trolley delivery, vendor installation and calibration, radiation checks, clean and handover. CT has a 20 week lead.

## Rationale

CT shares the shielded room logic of MRI but without the magnet logistics. The radiation survey is the key hold point because handing over an unshielded room is a regulatory failure.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection)
2. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element)
3. `ARC-SHIELD-WALL` Install lead-lined and RF-shielded wall (BIM-bound, from zone element). Lead lining to the radiation physicist specification.
4. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from zone element)
5. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from zone element; hold point: electrical inspection)
6. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; hold point: fire inspection)
7. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element)
8. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Route and weight check for the gantry.
9. `MED-CT-INSTALL` Install CT scanner (BIM-bound, from self element; hold point: electrical inspection)
10. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 7 days, time driven). Calibration and acceptance tests.
11. `CX-POWER-ENERGISE` Energise and test electrical systems (BIM-bound, from system element; hold point: electrical inspection)
12. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from system element)
13. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Radiation survey and interlock test.
14. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element)
15. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection)

## Ordering beyond the chain

* `ARC-SHIELD-WALL` before `MED-CT-INSTALL` (FS): Shielding before the scanner
* `ARC-FLOOR-FINISH` before `MED-CT-INSTALL` (FS): Floor finished before the gantry

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, laydown>=2
* Permits: work, icra
* Information: vendor site planning guide, radiation shielding report

## Checks and hold points

* Shielding verified by a radiation survey
* Interlocks and warning lights tested
* Floor level and loading checked

## Typical pitfalls

* Lead lining with gaps at services penetrations
* No radiation survey before use
* Gantry delivered before the floor finish

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation and site planning manuals for the equipment
* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)
* Radiation protection guidance for diagnostic imaging rooms

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

