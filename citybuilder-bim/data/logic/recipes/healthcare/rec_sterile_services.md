# Sterile services department (CSSD)

*Recipe `rec_sterile_services`, sector `healthcare`, typical duration 10 to 20 weeks.*

Utilities, floors and walls, steriliser lifts and installation, steam and water tie-in, vendor validation, pressure tests and clearance. Sterilisers have a 12 week lead.

## Rationale

Sterile services is a process plant inside a hospital: steam, RO water, drains and one-way flow. The recipe proves the utilities and the floor before the machines arrive, and treats validation as a vendor-driven time step.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection)
2. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element)
3. `PLB-PIPE-INSTALL` Install water, waste and heating pipe (BIM-bound, from system element). Steam, condensate, RO water and drains.
4. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
5. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; hold point: fire inspection)
6. `ARC-WALL-BOARD` Board, tape and finish partition walls (BIM-bound, from zone element)
7. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element). Chemical-resistant floor with falls.
8. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Door and route widths for the machines.
9. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker; optional)
10. `MED-STERIL-INSTALL` Install steriliser or washer-disinfector (BIM-bound, from self element; hold point: mechanical inspection)
11. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection). Steam line pressure test.
12. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 7 days, time driven). Commissioning and validation cycles.
13. `CX-PRESSURE-TEST` Room pressure differential test (BIM-bound, from zone element; hold point: mechanical inspection)
14. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
15. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element)
16. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection)

## Ordering beyond the chain

* `ARC-FLOOR-FINISH` before `MED-STERIL-INSTALL` (FS): Floor finished before machine delivery
* `GEN-HYDROTEST` before `GEN-VENDOR-REP` (FS): Services proven before validation cycles

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, laydown>=2
* Permits: work, icra
* Information: vendor drawings, steam and water quality specification

## Checks and hold points

* Steam, water and drain quality checked before validation
* Door and route widths verified before delivery
* Validation cycles recorded
* Pressure regime between dirty and clean sides

## Typical pitfalls

* Machines delivered before floors and drains are finished
* Water quality not tested before validation
* Door widths too narrow for the sterilisers

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation and site planning manuals for the equipment
* Decontamination of reusable medical devices guidance
* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

