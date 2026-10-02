# Horizontal vessel on saddles

*Recipe `rec_horizontal_vessel`, sector `industrial`, typical duration 4 to 8 weeks.*

Pour saddle footings, survey the anchors, lift the vessel onto saddles, align, tie in, test and insulate.

## Rationale

Horizontal vessels move with temperature, so saddle fixity and slope matter. The recipe makes the as-installed alignment survey a record before tie-in rather than something found when the piping will not fit.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
3. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element; optional)
4. `STR-FOOT-POUR` Pour footing (BIM-bound, from foundation element; optional; hold point: structural inspection). Saddle footings.
5. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; optional; 7 days, time driven)
6. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker). Saddle spacing and level, sliding saddle slots.
7. `alignment_survey` As-built survey and record (virtual task, survey marker). Alignment and slope as installed.
8. `PRC-EQUIP-SET` Set heavy equipment or stack (BIM-bound, from self element; hold point: mechanical inspection). Fixed saddle first, sliding saddle with PTFE pads.
9. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from self element; optional)
10. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection)
11. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from self element; optional)
12. `INS-COATING-APPLY` Apply fireproofing or coating (BIM-bound, from self element; optional)
13. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker)

## Ordering beyond the chain

* `STR-FOOT-POUR` before `PRC-EQUIP-SET` (FS with a lag of 7 days): Cure

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, laydown>=2, crane_reach
* Information: vendor drawings, lift plan

## Checks and hold points

* Fixed and sliding saddle correctly assigned
* Slope to drain checked after setting
* Lift plan for the loaded rigging
* Hydrotest before insulation

## Typical pitfalls

* Both saddles fixed so thermal growth loads the nozzles
* Slope to drain forgotten
* Tie-in welding before alignment is accepted

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* National lifting operations guidance (lift planning and appointed person duties)
* Process piping guidance (installation, pressure testing, flushing and reinstatement)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

