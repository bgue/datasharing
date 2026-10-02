# Transformer and switchgear room

*Recipe `rec_transformer_and_switchgear`, sector `industrial`, typical duration 10 to 30 weeks.*

Pour the plinth, set the transformer by crane, install switchgear and containment, pull and test cables, obtain permits and energise.

## Rationale

Transformers and switchgear have the longest lead times on an industrial job and an unforgiving commissioning order: plinth, set, cables, test, permit, energise. The recipe makes the permit and protection check visible so energisation is not a surprise date.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `STR-EQPAD-POUR` Pour equipment pad with anchor bolts (BIM-bound, from foundation element; hold point: structural inspection). Transformer plinth with oil bund.
3. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven)
4. `GEN-LIFT-PLAN` Prepare and approve lift plan (virtual task, lift_plan marker)
5. `GEN-CRANE-MOBILISE` Mobilise, assemble and load-test crane (virtual task, crane marker)
6. `ELE-XFMR-SET` Set power transformer (BIM-bound, from self element; hold point: electrical inspection)
7. `ELE-SWGR-SET` Set switchgear and MCC lineup (BIM-bound, from self element; hold point: electrical inspection). Switchgear lineup inside the finished switchroom.
8. `ELE-TRAY-INSTALL` Install cable tray (BIM-bound, from system element)
9. `ELE-CABLE-PULL` Pull and terminate cable (BIM-bound, from system element)
10. `ELE-CABLE-TEST` Megger and continuity test (BIM-bound, from system element; hold point: electrical inspection)
11. `energisation_permit` Permit to work (virtual task, permit marker). Energisation permit and lock-out plan.
12. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Protection relay settings, interlocks, phasing.
13. `CX-POWER-ENERGISE` Energise and test power system (BIM-bound, from system element; hold point: electrical inspection)

## Ordering beyond the chain

* `STR-EQPAD-POUR` before `ELE-XFMR-SET` (FS with a lag of 7 days): Plinth cure

## Prerequisites

* Equipment: crawler_crane
* Site: access_road, laydown>=3, crane_reach
* Permits: energisation
* Information: single line diagram, protection settings, lift plan

## Checks and hold points

* Switchroom weathertight before gear delivery
* Cable megger tests before energisation
* Protection settings issued and checked
* Energisation permit and lock-out plan

## Typical pitfalls

* Gear delivered into an unfinished, wet switchroom
* Energising before protection settings are loaded
* No lock-out plan for adjacent live equipment

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Electrical installation guidance for industrial plant (switchgear, transformers, protection)
* National lifting operations guidance (lift planning and appointed person duties)
* Pre-commissioning and commissioning guidance for process plants (system completion and handover)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

