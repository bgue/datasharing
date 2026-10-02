# Storage tank on ring-beam foundation

*Recipe `rec_tank_ring_foundation`, sector `industrial`, typical duration 8 to 14 weeks.*

Excavate and dewater, build the ring beam and pad, cure and survey, set the tank, water-fill test, tie in the nozzles and apply insulation.

## Rationale

A tank is only as good as its foundation settlement behaviour. The recipe surveys the ring and pad before the shell goes on and holds the water-fill test until the baseline exists, so differential settlement shows up as a measured trend.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
3. `GEN-PERMIT-HOT` Hot work permit and fire watch (virtual task, permit marker)
4. `CIV-EARTH-CUT` Excavate pad, trench or bulk cut (BIM-bound, from foundation element; optional). To formation level.
5. `GEN-DEWATER-RUN` Run dewatering (time driven) (virtual task, dewatering marker; 10 days, time driven; starts with CIV-EARTH-CUT)
6. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element; optional). Ring beam reinforcement.
7. `STR-FOOT-POUR` Pour footing (BIM-bound, from foundation element; optional; hold point: structural inspection). Ring beam and compacted pad.
8. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; optional; 14 days, time driven)
9. `settlement_baseline` As-built survey and record (virtual task, survey marker). Level survey of the ring and pad as the settlement baseline.
10. `PRC-TANK-SET` Set tank or vessel (BIM-bound, from self element). Erect shell, roof and fittings or set a shop-built tank.
11. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from self element; optional). Nozzle tie-ins.
12. `INS-COATING-APPLY` Apply fireproofing or coating (BIM-bound, from self element; optional)
13. `PRC-TANK-HYDRO` Tank water-fill and settlement test (BIM-bound, from self element; hold point: pressure_test inspection). Water-fill test with settlement monitoring.
14. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)

## Ordering beyond the chain

* `STR-FOOT-POUR` before `PRC-TANK-SET` (FS with a lag of 14 days): Ring beam cure
* `GEN-SURVEY-ASBUILT` before `PRC-TANK-HYDRO` (FS): Settlement survey before and during the fill

## Prerequisites

* Equipment: crawler_crane, excavator
* Site: access_road, laydown>=3, crane_reach
* Permits: hot_work
* Information: tank vendor drawings, foundation design, lift plan

## Checks and hold points

* Settlement survey before and during the water-fill test
* Crane lift plan above 20 tonnes
* Weld inspection records for the shell
* Water disposal permit for the fill water

## Typical pitfalls

* No settlement baseline, so the test cannot be interpreted
* Fill water source or disposal not permitted
* Hot work near stored product without a permit
* Insulating before leak checks

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* API storage tank standards (welded tanks for oil storage)
* National lifting operations guidance (lift planning and appointed person duties)
* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

