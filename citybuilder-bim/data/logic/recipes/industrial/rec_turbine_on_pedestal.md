# Turbine or compressor train on pedestal

*Recipe `rec_turbine_on_pedestal`, sector `industrial`, typical duration 12 to 26 weeks.*

Cast a massive pedestal, cure it long, survey anchors, lift the machine, align with vendor supervision, connect and commission the train.

## Rationale

A turbine train combines the longest cure, the heaviest lift and the most vendor involvement on a plant. Putting those as explicit steps shows that the machine date is set by procurement and cure, not by crews.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element)
3. `STR-EQPAD-POUR` Pour equipment pad with anchor bolts (BIM-bound, from foundation element; hold point: structural inspection). Mass pour with temperature control.
4. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 14 days, time driven). Thermal monitoring and cure.
5. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
6. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker)
7. `lift` nested recipe `rec_crane_lift_heavy`. Heaviest lift on site: engineered lift.
8. `PRC-EQUIP-SET` Set heavy equipment or stack (BIM-bound, from self element; hold point: mechanical inspection)
9. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 10 days, time driven). Vendor supervises alignment, coupling and grouting.
10. `PIP-PIPE-INSTALL` Install process pipe (BIM-bound, from system element)
11. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
12. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from system element)
13. `IC-LOOP-CHECK` Instrument loop check (BIM-bound, from system element)
14. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)
15. `CX-PROCESS-STARTUP` Process system start-up (BIM-bound, from system element)

## Ordering beyond the chain

* `STR-EQPAD-POUR` before `PRC-EQUIP-SET` (FS with a lag of 21 days): Large pedestal cure before machine load
* `GEN-VENDOR-REP` before `PIP-PIPE-INSTALL` (FS): Pipe fit-up after alignment acceptance

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: work
* Information: vendor drawings, engineered lift plan, pedestal design, alignment procedure

## Checks and hold points

* Mass pour thermal control plan
* Engineered lift plan with ground bearing and weather limits
* Vendor alignment records signed
* Pipe strain checks before coupling

## Typical pitfalls

* Short cure of the pedestal under a massive load
* Vendor supervision not booked, delaying alignment
* Piping connected before alignment is accepted

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* National lifting operations guidance (lift planning and appointed person duties)
* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

