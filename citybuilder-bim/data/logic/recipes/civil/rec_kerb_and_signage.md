# Kerbs, signs and roadside furniture

*Recipe `rec_kerb_and_signage`, sector `civil`, typical duration 2 to 8 weeks.*

Set out, lay kerbs, install guardrail and signs, light and cable where required, apply markings, then pre-commission and close out defects.

## Rationale

Roadside furniture is small, numerous and visible. The recipe puts markings after the final surface and finishes with a pre-commissioning check and punch list so the handover has no loose ends.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker; optional)
3. `CIV-KERB-LAY` Lay kerb and channel (BIM-bound, from self element)
4. `CIV-GUARDRAIL-INSTALL` Install safety barrier (BIM-bound, from self element)
5. `CIV-SIGN-INSTALL` Install sign or gantry sign (BIM-bound, from self element)
6. `CIV-LIGHT-INSTALL` Erect lighting column or luminaire (BIM-bound, from self element; optional)
7. `CIV-CABLE-PULL` Pull and test cable (BIM-bound, from self element; optional)
8. `CIV-MARKING-APPLY` Apply road markings (BIM-bound, from self element)
9. `GEN-PRECOMM-CHECK` Pre-commissioning checks (virtual task, test marker). Lighting, sign visibility and marking retro-reflectivity.
10. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker)
11. `GEN-PUNCH-CLEAR` Clear defects and demobilise (virtual task, test marker)

## Ordering beyond the chain

* `CIV-KERB-LAY` before `CIV-MARKING-APPLY` (FS): Kerbs and channels fixed before lines are painted

## Prerequisites

* Equipment: lift
* Site: access_road
* Permits: work
* Information: signing schedule, marking plan

## Checks and hold points

* Sign positions checked against sight lines
* Marking after final surfacing
* Lighting test and electrical certificate

## Typical pitfalls

* Markings before the final surface
* Signs placed from plan without a sight line check
* No electrical certificate for lighting

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Temporary traffic management guidance for road works (signs, lighting and guarding)
* Road signing and marking guidance

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

