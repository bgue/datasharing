# Scaffold access for any installation

*Recipe `rec_scaffold_access`, sector `all`, typical duration 1 to 6 weeks.*

Design check, erect, inspect and tag the scaffold, keep it through the work, dismantle after the last trade has finished.

## Rationale

Scaffold is the most common virtual cost of an installation: it has no BIM element but it controls how fast the trades can work at height. The recipe makes the sequence visible so it can be planned and staffed.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker). Mark the scaffold footprint and ground check.
2. `GEN-TW-CHECK` Temporary works design check and permit to load (virtual task, permit marker). Design check for loads and ties.
3. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker; optional). Permit to erect near live areas.
4. `GEN-SCAFFOLD-ERECT` Erect access scaffold (virtual task, scaffold marker). Erect in lifts with handover inspection.
5. `scaffold_inspection` Pre-commissioning checks (virtual task, test marker). Inspection and tag before first use, then at the agreed interval.
6. `GEN-SCAFFOLD-DISMANTLE` Dismantle access scaffold (virtual task, scaffold marker). Only after the last trade confirms they are finished.

## Ordering beyond the chain

* `scaffold_inspection` before `GEN-SCAFFOLD-DISMANTLE` (FS): Dismantle only after the last handover

## Prerequisites

* Equipment: lift
* Site: access_road, laydown>=1
* Permits: work_at_height
* Information: scaffold design, trade programme

## Checks and hold points

* Competent person design check
* Handover inspection and tag
* Weekly inspection and after weather events
* Ties and loading limits respected

## Typical pitfalls

* Dismantling while another trade still needs access
* Overloading platforms with materials
* Skipping inspections after storms
* Ties removed to make room for cladding

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Scaffolding and access guidance (design, erection, inspection and tagging)
* Temporary works procedure guidance (design check, permit to load, permit to dismantle)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

