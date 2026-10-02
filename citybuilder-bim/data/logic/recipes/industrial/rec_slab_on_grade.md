# Slab on grade

*Recipe `rec_slab_on_grade`, sector `industrial`, typical duration 2 to 6 weeks.*

Prepare and compact the platform, place under-slab drainage, pour the ground slab, keep it cured and survey the finished levels.

## Rationale

The slab is the plant floor for everything above it. Drainage and sleeves must go in first because coring a cured slab is slow and risks reinforcement; the cure watch and the survey document what the equipment installers will inherit.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-SITE-PREP` Strip topsoil and prepare platform (BIM-bound, from self element). Strip and proof-roll.
3. `CIV-EARTH-FILL` Place and compact engineered fill (BIM-bound, from self element). Engineered fill in layers with compaction tests.
4. `CIV-DRAIN-INSTALL` Lay underground drain or duct bank (BIM-bound, from system element; optional). Under-slab drainage and sleeves.
5. `STR-GSLAB-POUR` Pour ground slab (BIM-bound, from self element; hold point: structural inspection)
6. `GEN-CURING-WATCH` Concrete curing watch (time driven) (virtual task, test marker; 7 days, time driven). Wet cure or membrane, protect from traffic.
7. `flatness_survey` As-built survey and record (virtual task, survey marker). Level and flatness survey for equipment and racking.
8. `ARC-FLOOR-FINISH` Install floor finish (BIM-bound, from self element; optional)

## Ordering beyond the chain

* `STR-GSLAB-POUR` before `ARC-FLOOR-FINISH` (FS with a lag of 14 days): Moisture and shrinkage before finishes

## Prerequisites

* Equipment: concrete_pump
* Site: access_road, laydown>=2
* Information: slab joint layout, underslab services drawing

## Checks and hold points

* Compaction test results before blinding
* Services inspected before the pour
* Joint saw-cutting window respected
* Survey flatness against the specification

## Typical pitfalls

* Pouring before under-slab services are inspected
* Late saw-cutting causing random cracks
* Heavy traffic on a young slab
* Levels not surveyed so equipment needs shimming

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Concrete construction guidance (formwork, placing, curing, hot and cold weather concreting)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

