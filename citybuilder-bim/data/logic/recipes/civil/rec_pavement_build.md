# Pavement build-up

*Recipe `rec_pavement_build`, sector `civil`, typical duration 6 to 20 weeks.*

Prepare and test subgrade, lay subbase and base, kerb, place asphalt layers and mark; surveys and pavement inspections are hold points.

## Rationale

Pavement is the last structural layer and the first thing the public sees. The recipe keeps drainage ahead of the subgrade and makes asphalt a weather-sensitive step run by a paving train.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `CIV-SUBGRADE-PREP` Prepare and proof-roll subgrade (BIM-bound, from self element; hold point: geotechnical inspection)
3. `CIV-SUBBASE-LAY` Lay and compact subbase (BIM-bound, from self element)
4. `CIV-BASE-LAY` Lay and compact road base (BIM-bound, from self element; hold point: pavement inspection)
5. `CIV-KERB-LAY` Lay kerb and channel (BIM-bound, from self element; optional)
6. `CIV-ASPHALT-BASE` Place asphalt base and binder (BIM-bound, from self element). Needs a paver and a paving train of three crews.
7. `CIV-ASPHALT-WEAR` Place asphalt wearing course (BIM-bound, from self element; hold point: pavement inspection). Temperature and rain sensitive.
8. `CIV-MARKING-APPLY` Apply road markings (BIM-bound, from self element)
9. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Final levels, thickness cores and ride quality.

## Ordering beyond the chain

* `CIV-KERB-LAY` before `CIV-ASPHALT-BASE` (FS): Kerbs set before binder layers

## Prerequisites

* Equipment: paver
* Site: access_road, laydown>=2
* Information: pavement design, mix design approvals

## Checks and hold points

* Drainage tested and backfilled before subgrade
* Compaction tests on each granular layer
* Asphalt temperature and density records
* Ride and levels survey

## Typical pitfalls

* Paving in cold or wet weather
* Kerbs after the binder layer so levels do not work
* Compaction tests skipped on granular layers

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Road pavement construction guidance (layer thickness, compaction, asphalt temperature and rolling)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied to a zone.

