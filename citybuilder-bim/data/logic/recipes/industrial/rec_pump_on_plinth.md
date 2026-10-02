# Pump set on grouted plinth

*Recipe `rec_pump_on_plinth`, sector `industrial`, typical duration 3 to 6 weeks.*

Pour the plinth, cure, survey anchors, set the pump with vendor attendance, grout, connect piping and power, test and run.

## Rationale

Pumps fail from misalignment and pipe strain. Piping goes in after the pump is set and aligned, and the vendor representative is a time-driven step because their presence gates the grouting and the warranty.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-EQPAD-POUR` Pour equipment pad with anchor bolts (BIM-bound, from foundation element; optional; hold point: structural inspection)
3. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; optional; 7 days, time driven)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker). Anchor bolts and grout space.
5. `PRC-PUMP-SET` Set pump, compressor or fan (BIM-bound, from self element). Set and level on shims.
6. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 1 days, time driven). Alignment check and grouting witnessed by the vendor.
7. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from self element; optional). Strain-free pipe connections.
8. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from self element; optional)
9. `IC-INSTR-INSTALL` Install field instrument (BIM-bound, from self element; optional)
10. `CX-PIPE-FLUSH` Flush and blow pipework (BIM-bound, from self element; optional)
11. `IC-LOOP-CHECK` Instrument loop check (BIM-bound, from self element; optional)
12. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Rotation check, seal flush plan, lube.
13. `CX-PROCESS-STARTUP` Process system start-up (BIM-bound, from self element)

## Ordering beyond the chain

* `STR-EQPAD-POUR` before `PRC-PUMP-SET` (FS with a lag of 7 days): Plinth cure

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, crane_reach
* Information: vendor drawings, alignment procedure

## Checks and hold points

* Pipe strain check with dial indicators before final bolting
* Rotation check uncoupled before coupling
* Seal and lubrication systems commissioned before run

## Typical pitfalls

* Pipe forced to fit the pump flange
* Grouting before the alignment is accepted
* Running dry because seal flush was not commissioned

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation manuals and vendor drawings
* Process piping guidance (installation, pressure testing, flushing and reinstatement)
* Pre-commissioning and commissioning guidance for process plants (system completion and handover)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

