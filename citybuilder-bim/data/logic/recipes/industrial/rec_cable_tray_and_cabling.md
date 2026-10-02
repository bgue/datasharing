# Cable tray and cabling run

*Recipe `rec_cable_tray_and_cabling`, sector `industrial`, typical duration 2 to 8 weeks.*

Erect access, install tray, pull cable, megger test and terminate; dismantle access when finished.

## Rationale

Containment is installed once and cabled many times. The recipe keeps access in place until test results are accepted so a failed cable can be pulled again without rebuilding the scaffold.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker). Route marking and clash check.
2. `GEN-SCAFFOLD-ERECT` Erect access scaffold (virtual task, scaffold marker; optional)
3. `ELE-TRAY-INSTALL` Install cable tray (BIM-bound, from system element)
4. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
5. `ELE-CABLE-TEST` Megger and continuity test (BIM-bound, from system element; hold point: electrical inspection)
6. `ELE-PANEL-INSTALL` Install local panel or junction box (BIM-bound, from self element; optional)
7. `GEN-SCAFFOLD-DISMANTLE` Dismantle access scaffold (virtual task, scaffold marker; optional)
8. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Insulation resistance records and cable schedule check.

## Ordering beyond the chain

* `ELE-CABLE-TEST` before `GEN-SCAFFOLD-DISMANTLE` (FS): Keep access until test results are accepted

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: work_at_height
* Information: cable schedule, tray layout

## Checks and hold points

* Segregation of power and instrument cables
* Bend radius and pulling tension limits
* Megger results on every cable before termination

## Typical pitfalls

* Tray installed before the piping layout is fixed, causing clashes
* Cables pulled before the tray is finished and bonded
* Scaffold removed before tests

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Electrical installation guidance for industrial plant (containment, cabling, testing)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

