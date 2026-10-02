# Vertical vessel or column on pad

*Recipe `rec_vertical_vessel`, sector `industrial`, typical duration 6 to 12 weeks.*

Pour the pad with anchor bolts, survey them, lift the vessel with a crane, set platforms, tie in piping and instruments, hydrotest, insulate and pre-commission.

## Rationale

Tall vessels are lifted once. The recipe front-loads the anchor survey and lift plan, and puts piping and instruments after the set, because every nozzle position depends on the final orientation.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-EQPAD-POUR` Pour equipment pad with anchor bolts (BIM-bound, from foundation element; hold point: structural inspection)
3. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
5. `lift` nested recipe `rec_crane_lift_heavy`. Tailing and upending usually needs two cranes; nest the heavy lift recipe.
6. `PRC-EQUIP-SET` Set heavy equipment or stack (BIM-bound, from self element; hold point: mechanical inspection). Upend and set on anchors.
7. `ARC-STAIR-INSTALL` Install stair or access platform (BIM-bound, from self element; optional). Ladders and platforms.
8. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from system element)
9. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from system element)
10. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection)
11. `INS-COATING-APPLY` Apply fireproofing or coating (BIM-bound, from self element)
12. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
13. `CX-PROCESS-STARTUP` Process system start-up (BIM-bound, from system element)

## Ordering beyond the chain

* `STR-EQPAD-POUR` before `PRC-EQUIP-SET` (FS with a lag of 7 days): Pad cure
* `GEN-ANCHOR-SURVEY` before `PRC-EQUIP-SET` (FS): Vessel cannot be set on unchecked anchors

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: work
* Information: vendor drawings, lift plan, foundation design

## Checks and hold points

* Anchor survey signed off before the lift
* Lift plan including tailing crane and ground bearing
* Nozzle orientation verified before welding
* Hydrotest before insulation

## Typical pitfalls

* Ground bearing for the crane not checked
* Anchor bolts misplaced by a few millimetres, forcing slotted base rings
* Piping prefabricated before the vessel orientation is known
* Wind limits ignored on the day of the lift

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* National lifting operations guidance (lift planning and appointed person duties)
* Process piping guidance (installation, pressure testing, flushing and reinstatement)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

