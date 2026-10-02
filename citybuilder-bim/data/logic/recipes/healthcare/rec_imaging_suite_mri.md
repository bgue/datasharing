# MRI suite

*Recipe `rec_imaging_suite_mri`, sector `healthcare`, typical duration 16 to 32 weeks.*

Shielded room first, services and RF cage, floor, crane lift of the magnet, vendor shimming, power and air checks, clean and hand over. The MRI has a 26 week lead.

## Rationale

The MRI is ordered 26 weeks ahead and delivered through a wall opening. The recipe keeps the room open and shielded until the magnet is in, and counts the vendor ramp-up as time, because it is not labour that crews can accelerate.

## Sequence

1. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; optional; hold point: icra inspection)
2. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). Quench pipe route and magnet delivery permit.
3. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker). Route, door opening and rigging plan.
4. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
5. `GEN-ANCHOR-SURVEY` Anchor bolt and grout survey (virtual task, survey marker). Magnet plinth and floor loading check.
6. `ARC-WALL-FRAME` Frame partition walls (BIM-bound, from zone element; optional)
7. `MEP-DUCT-INSTALL` Install ductwork (BIM-bound, from zone element; optional)
8. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from zone element; optional; hold point: electrical inspection)
9. `FIR-STOP-INSTALL` Fire-stop penetrations (BIM-bound, from zone element; optional; hold point: fire inspection)
10. `ARC-SHIELD-WALL` Install lead-lined and RF-shielded wall (BIM-bound, from zone element; optional). RF and magnetic shielding as the vendor specifies.
11. `ARC-FLOOR-FINISH` Install hygienic floor finish (BIM-bound, from zone element; optional). Non-magnetic materials only.
12. `MED-MRI-INSTALL` Install MRI scanner (BIM-bound, from self element; hold point: electrical inspection). Wall opening left until the magnet is in.
13. `GEN-VENDOR-REP` Vendor representative attendance (time driven) (virtual task, permit marker; 15 days, time driven). Ramp-up, shimming, image quality tests.
14. `CX-POWER-ENERGISE` Energise and test electrical systems (BIM-bound, from self element; optional; hold point: electrical inspection)
15. `CX-AIR-BALANCE` Test and balance air system (BIM-bound, from self element; optional)
16. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element; optional)
17. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; optional; hold point: icra inspection)
18. `GEN-HANDOVER-DOC` Room-by-room handover documentation (BIM-bound, from zone element)

## Ordering beyond the chain

* `ARC-SHIELD-WALL` before `MED-MRI-INSTALL` (FS): Shielded room before magnet
* `GEN-LIFT-PLAN` before `MED-MRI-INSTALL` (FS): Route and rigging plan before delivery

## Prerequisites

* Equipment: mobile_crane
* Site: access_road, crane_reach, laydown>=2
* Permits: work, icra
* Information: vendor site planning guide, lift plan, magnetic field plot

## Checks and hold points

* Vendor shielding design approved
* Floor loading and plinth survey
* No ferrous materials near the magnet area
* Quench pipe discharge route inspected

## Typical pitfalls

* Closing the wall opening before the magnet arrives
* Ordering the magnet late
* Ferrous materials in the room fit-out
* No plan for the quench pipe discharge

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Manufacturer installation and site planning manuals for the equipment
* National lifting operations guidance (lift planning and appointed person duties)
* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

