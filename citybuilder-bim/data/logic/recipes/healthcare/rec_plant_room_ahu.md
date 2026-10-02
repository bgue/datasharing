# Plant room: AHU replacement or installation in a live hospital

*Recipe `rec_plant_room_ahu`, sector `healthcare`, typical duration 6 to 20 weeks.*

Isolate and permit, plan the lift, set the AHU on its pad, connect ducts and cables, vendor commissioning, air balance and power energisation, with ICRA around the work.

## Rationale

Replacing an AHU in a live hospital is mostly about the window: ventilation can only be off when the clinical risk allows. The recipe puts the permit and lift plan ahead of the physical work and ends with air balance, because the wards depend on it.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection)
2. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Isolation, shutdown window and clinical risk assessment.
3. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
5. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Route, crane position and quiet-hours window.
6. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
7. `MEP-AHU-SET` Set air handling unit or plant item (BIM-bound, from self element; hold point: mechanical inspection)
8. `MEP-CHILLER-SET` Set chiller or boiler (BIM-bound, from self element; optional; hold point: mechanical inspection)
9. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from system element)
10. `MEP-FITTING-INSTALL` Install fittings, dampers and valves (BIM-bound, from system element)
11. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
12. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 5 days, time driven)
13. `CX-POWER-ENERGISE` Energise and test electrical systems (BIM-bound, from system element; hold point: electrical inspection)
14. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from system element)
15. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
16. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection)

## Ordering beyond the chain

* `GEN-LIFT-PLAN` before `MEP-AHU-SET` (FS): Lift plan approved before the unit moves
* `GEN-PERMIT-WORK` before `MEP-DUCT-INSTALL` (FS): No tie-in without the shutdown window

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, crane_reach, laydown>=2
* Permits: work, isolation, icra
* Information: vendor drawings, shutdown plan, lift plan

## Checks and hold points

* Clinical sign-off of the shutdown window
* Lift in a quiet-hours window
* Air balance and pressure regime restored before handback
* ICRA clearance

## Typical pitfalls

* Booking the shutdown window before the AHU is on site
* Crane lift outside the quiet-hours window
* Duct connections made before isolation is confirmed
* Handing back without rebalancing air

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation and site planning manuals for the equipment
* National lifting operations guidance (lift planning and appointed person duties)
* Healthcare ventilation guidance (air change rates, pressure regimes, validation)
* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

