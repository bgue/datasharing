# Pipe rack by prefabricated modules

*Recipe `rec_pipe_rack_module`, sector `industrial`, typical duration 8 to 16 weeks.*

Install a pipe rack as large prefabricated modules: footings, anchor survey, lift plan and crane, module lifts and connections, spool tie-in, tray, hydrotest and insulation.

## Rationale

Modular racks trade site labour for crane time and tolerance risk. Everything depends on foundations being within tolerance when a module that cannot be modified arrives, so the anchor survey and lift plan are explicit steps rather than assumptions.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
3. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
4. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element; optional)
5. `STR-FOOT-POUR` Pour footing (BIM-bound, from foundation element; optional; hold point: structural inspection)
6. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; optional; 7 days, time driven)
7. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker). Anchor bolt positions and levels against the module drawing.
8. `STR-RACK-ERECT` Erect pipe rack frame (BIM-bound, from self element). Modules lifted bay by bay, tier by tier.
9. `STR-RACK-CONN` Bolt-up and weld rack connections (BIM-bound, from self element; hold point: welding inspection)
10. `PIP-SPOOL-LAY` Lift and lay pipe spools on rack (BIM-bound, from self element; optional). Spool connections between modules and tie-ins.
11. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection)
12. `ELE-TRAY-INSTALL` Install cable tray (BIM-bound, from self element; optional)
13. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from self element; optional)
14. `INS-PIPE-INSULATE` Insulate and clad pipe (BIM-bound, from self element; optional)
15. `CX-PIPE-FLUSH` Flush and blow pipework (BIM-bound, from self element; optional)

## Ordering beyond the chain

* `STR-FOOT-POUR` before `STR-RACK-ERECT` (FS with a lag of 7 days): Footing strength before module load
* `GEN-LIFT-PLAN` before `STR-RACK-ERECT` (FS): No lift without an approved plan
* `GEN-HYDROTEST` before `INS-PIPE-INSULATE` (FS): Insulation after the test

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: hot_work
* Information: module drawings, lift plan, weld procedure

## Checks and hold points

* Anchor survey signed off before the first module
* Lift plan for every module over the crane threshold
* Weld NDT results before spool loading
* Hydrotest before insulation

## Typical pitfalls

* Module arrives before the footings have cured
* Anchor bolts out of tolerance discovered during the lift
* Lift without a plan for the heaviest module
* Insulating before the hydrotest

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* National lifting operations guidance (lift planning and appointed person duties)
* Welding and non-destructive testing guidance for structural steelwork and piping
* Process piping guidance (installation, pressure testing, flushing and reinstatement)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

