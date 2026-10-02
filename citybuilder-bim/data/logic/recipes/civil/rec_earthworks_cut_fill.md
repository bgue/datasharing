# Earthworks cut and fill

*Recipe `rec_earthworks_cut_fill`, sector `civil`, typical duration 6 to 20 weeks.*

Set out, clear, cut and place fill with testing; compaction and formation tests are hold points and the as-built survey closes the zone.

## Rationale

Cut and fill is a haulage problem. Fill can start once cutting has started, which the recipe expresses as a start-to-start link; the compaction test is the hold point before anything is built on the formation.

## Sequence

1. `GEN-SURVEY-SETOUT` Survey set-out of the work area (virtual task, survey marker)
2. `GEN-UTIL-LOCATE` Locate and mark buried utilities (virtual task, survey marker)
3. `GEN-SITE-CLEAR` Clear and grub corridor (BIM-bound, from self element). Clear and grub.
4. `CIV-EARTH-CUT` Excavate cut to formation (BIM-bound, from self element). Cut to formation; suitable material hauled to fill.
5. `CIV-EARTH-FILL` Place and compact fill (BIM-bound, from self element; starts with CIV-EARTH-CUT). Place in layers; starts once cutting is under way.
6. `CIV-COMPACT-TEST` Formation and compaction test (BIM-bound, from self element; hold point: geotechnical inspection)
7. `GEN-SURVEY-ASBUILT` As-built survey and record (virtual task, survey marker). Formation levels.

## Ordering beyond the chain

* `CIV-EARTH-CUT` before `CIV-EARTH-FILL` (SS with a lag of 5 days): Fill depends on cut material
* `CIV-EARTH-FILL` before `CIV-COMPACT-TEST` (FS): Test after placement

## Prerequisites

* Equipment: excavator
* Site: access_road, laydown>=2
* Information: earthworks balance, geotechnical report

## Checks and hold points

* Material acceptability tested before reuse as fill
* Layer thickness and compaction tests
* Formation inspected before drainage or pavement

## Typical pitfalls

* Placing wet or unsuitable material
* Skipping the layer-by-layer compaction tests
* Long haul distances ignored in the plan
* Rain days not in the programme

## References

General standards and guidance names only; check the current edition that applies to your project and jurisdiction.

* Specification for highway works style earthworks guidance (compaction, acceptability, testing)

## How to use

Steps marked BIM-bound are covered by tasks the mapper creates from the model. Steps marked virtual have no element: the pipeline creates virtual tasks for them when the recipe is applied. Optional steps are other work the model already covers with its own elements (or work the planner opts into); when a rule attaches the recipe to one anchor element, only the anchor element's own steps and the virtual steps are created by default.

