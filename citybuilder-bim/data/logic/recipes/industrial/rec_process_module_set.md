# Process module or skid set on pad

*Recipe `rec_process_module_set`, sector `industrial`, typical duration 6 to 16 weeks.*

Prepare the pad and anchor survey, plan and mobilise the crane, set the module, make the connections, attend with the vendor, hydrotest and start up.

## Rationale

A module has a 30 week lead time and one delivery day. The planner should treat crane, pad, road and delivery as one coincidence, which is why the recipe lists them as explicit steps and prerequisites.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-GSLAB-POUR` Pour ground slab (BIM-bound, from foundation element; optional). Pad or ground slab area under the module.
3. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
5. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Lift plan with road access and swept path checked.
6. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
7. `PRC-MOD-SET` Set process module (BIM-bound, from self element; hold point: mechanical inspection). Needs a cured pad, crane reach and road access in the same week.
8. `PRC-MOD-CONNECT` Connect module piping and utilities (BIM-bound, from self element). Piping and utilities hook-up.
9. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from system element)
10. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
11. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from system element)
12. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 5 days, time driven)
13. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection)
14. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
15. `CX-PROCESS-STARTUP` Process system start-up (BIM-bound, from system element)

## Ordering beyond the chain

* `GEN-LIFT-PLAN` before `PRC-MOD-SET` (FS): Lift plan before the lift
* `GEN-ANCHOR-SURVEY` before `PRC-MOD-SET` (FS): Pad accepted before delivery

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: work
* Information: module drawings, lift plan, delivery schedule

## Checks and hold points

* Delivery slot, road access and crane booked for the same week
* Pad survey signed before delivery
* Hydrotest before insulation and start-up

## Typical pitfalls

* Module delivered with no crane on site
* Pad not cured or surveyed
* Connection spools prefabricated before the module position is known
* Vendor commissioning engineers not booked

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* National lifting operations guidance (lift planning and appointed person duties)
* Pre-commissioning and commissioning guidance for process plants (system completion and handover)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

