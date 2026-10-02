# Pipe rack stick-built from steel members

*Recipe `rec_pipe_rack_stickbuilt`, sector `industrial`, typical duration 12 to 24 weeks.*

Build the rack on site: footings, scaffold, steel erection and connections, spool laydown, fittings, hydrotest, tray and cable, insulation and flush.

## Rationale

Stick-built racks need more site time but fewer heavy lifts. The recipe adds scaffold and hot work permits because most of the work is at height, and keeps the scaffold up until insulation is finished.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-FOOT-REBAR` Form and fix footing reinforcement (BIM-bound, from foundation element)
3. `STR-FOOT-POUR` Pour footing (BIM-bound, from foundation element; hold point: structural inspection)
4. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker)
5. `GEN-PERMIT-HOT` Hot work permit and fire watch (virtual task, permit marker)
6. `GEN-SCAFFOLD-ERECT` Erect access scaffold (virtual task, scaffold marker)
7. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
8. `STR-RACK-ERECT` Erect pipe rack frame (BIM-bound, from self element)
9. `STR-RACK-CONN` Bolt-up and weld rack connections (BIM-bound, from self element; hold point: welding inspection)
10. `PIP-SPOOL-LAY` Lift and lay pipe spools on rack (BIM-bound, from system element)
11. `PIP-FITTING-INSTALL` Install fittings, flanges and valves (BIM-bound, from system element)
12. `GEN-HYDROTEST` System hydrotest or pressure test (virtual task, test marker; hold point: pressure_test inspection)
13. `ELE-TRAY-INSTALL` Install cable tray (BIM-bound, from system element)
14. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
15. `INS-PIPE-INSULATE` Insulate and clad pipe (BIM-bound, from system element)
16. `GEN-SCAFFOLD-DISMANTLE` Dismantle access scaffold (virtual task, scaffold marker)
17. `CX-PIPE-FLUSH` Flush and blow pipework (BIM-bound, from system element)

## Ordering beyond the chain

* `STR-FOOT-POUR` before `STR-RACK-ERECT` (FS with a lag of 7 days): Footing strength
* `INS-PIPE-INSULATE` before `GEN-SCAFFOLD-DISMANTLE` (FS): Scaffold stays until insulation is done

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, laydown>=3
* Permits: hot_work, work_at_height
* Information: rack drawings, weld procedure

## Checks and hold points

* Hot work permits for every welding shift
* Scaffold inspected before each shift (tag system)
* Weld NDT before spool loading
* Hydrotest before insulation

## Typical pitfalls

* Taking scaffold down before insulation or cable work is finished
* Loading spools before NDT is signed off
* Hot work permits not renewed per shift
* Footing strength assumed instead of checked

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* National lifting operations guidance (lift planning and appointed person duties)
* Welding and non-destructive testing guidance for structural steelwork and piping
* Process piping guidance (installation, pressure testing, flushing and reinstatement)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

