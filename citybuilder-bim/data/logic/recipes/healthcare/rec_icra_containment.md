# ICRA containment lifecycle

*Recipe `rec_icra_containment`, sector `healthcare`, typical duration 1 to 4 weeks.*

Permit, erect the barrier with negative air, verify it, work behind it, HEPA clean, clear and remove it.

## Rationale

The barrier is the licence to do dusty or noisy work next to patients. The recipe is the lifecycle of that licence: permit, verification, clean, clearance.

## Sequence

1. `GEN-PERMIT-WORK` Permit to work (virtual task, permit marker). ICRA permit with risk class and mitigation.
2. `GEN-ICRA-SETUP` Erect ICRA barrier and negative air (BIM-bound, from zone element; hold point: icra inspection). Barrier, negative air, sticky mat, signage.
3. `barrier_check` Pre-commissioning checks (virtual task, test marker). Smoke test and pressure differential check.
4. `GEN-HEPA-CLEAN` HEPA terminal clean (BIM-bound, from zone element). After the dusty work.
5. `GEN-ICRA-CLOSEOUT` ICRA clearance and barrier removal (BIM-bound, from zone element; hold point: icra inspection). Clearance, then barrier removal.
6. `GEN-PUNCH-CLEAR` Punch list close-out (virtual task, test marker; optional)

## Ordering beyond the chain

* `barrier_check` before `GEN-HEPA-CLEAN` (FS): Dusty work happens between these two steps and is not part of the recipe

## Prerequisites

* Site: access_road
* Permits: icra
* Information: ICRA risk matrix, method statement

## Checks and hold points

* Daily barrier and pressure checks while dusty work runs
* Terminal clean before clearance
* Infection control sign-off before removal

## Typical pitfalls

* Starting dusty work before the barrier is verified
* Removing the barrier before clearance
* Gaps at penetrations and door seals

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Infection prevention and control guidance for construction and renovation in healthcare facilities (ICRA)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

