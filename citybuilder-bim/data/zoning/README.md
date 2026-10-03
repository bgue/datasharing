# Zoning vocabulary (space tag rules)

`space_tags_<sector>.json` (schema `space_tags.schema.json`) tell the pipeline how to turn `IfcSpace` and `IfcZone` records into SiteBuilder zones: the zone tags that recipes, sequence cards, shift rules and mapping rules react to, plus crew caps, per-face caps and whether a second shift is allowed.

Validate with `python3 tools/validate_schemas.py space_tags data/zoning/*.json`.

## How the rules are meant to be evaluated

* Rules are tried **in file order**. Specific rules come first; the last rule of every file (`*-fallback`, `name_regex ".*"`) catches everything else so every space gets a zone with a sane crew cap.
* A rule matches when every key of its `match` block matches: `name_regex` on `IfcSpace.Name`, `long_name_regex` on `LongName`, `object_type_regex` on `ObjectType`, `properties` exactly on property values. All regexes are case-insensitive (`(?i)` is written out).
* The first matching rule gives the zone its `tags`, `max_crews`, `faces` (per-face crew caps) and `shift_allowed` (false adds the zone to the second-shift ban in the game). Generic rules that should apply on top of a specific one (the live-ward flag) are therefore written as dedicated specific rules (`hc-ward-live`) rather than relying on tag merging. If the pipeline merges tags across several matching rules, the generic `hc-live-any` rule also adds `occupied_adjacent` to every room whose long name contains "Live".
* `shift_allowed: false` marks rooms where double shift would break quiet hours, infection control or live traffic; the scenario's `shift.forbidden_zone_tags` list still applies on top.

## Room name vocabulary

The synthetic IFC generator (`bimseq synth-ifc`) should name its spaces with these words; real models usually use similar names. Tag ids are the contract with `data/logic` (recipes and cards) and the mapping rules.

### Healthcare

| Rule | Names matched (case-insensitive) | Tags | max_crews | Faces | Shift |
| --- | --- | --- | --- | --- | --- |
| `hc-or` | Operating Theatre/Theater, OR, OT, Theatre | `or_room`, `pressure_room` | 3 | ceiling_void 2, walls 2, floor 1 | no |
| `hc-imaging` | MRI, CT, X-ray, Imaging, Radiology, Scanner, Fluoroscopy, Angio | `imaging` | 3 | ceiling_void 2, walls 2, floor 1, plant_pad 1 | no |
| `hc-plant` | Plant Room, AHU, Air Handling, Mechanical, Electrical Room, Switch Room, Riser, Boiler, Chiller, Generator | `plant_room`, `heavy_lift_area` | 4 | plant_pad 3, ceiling_void 2, walls 2 | yes |
| `hc-sterile` | Sterile, CSSD, Decontamination | `sterile`, `pressure_room` | 3 | ceiling_void 2, walls 2, floor 1 | no |
| `hc-pressure` | Isolation, Negative Pressure, Protective Environment, Clean Room | `pressure_room` | 2 | ceiling_void 2, walls 2, floor 1 | no |
| `hc-ward-live` | Ward, Bed, Patient, Bay with LongName containing "Live" | `ward`, `occupied_adjacent` | 2 | ceiling_void 2, walls 2, floor 1 | no |
| `hc-ward` | Ward, Bed, Patient, Bay | `ward` | 2 | ceiling_void 2, walls 2, floor 1 | yes |
| `hc-corridor` | Corridor, Circulation, Hall, Passage | `corridor` | 2 | ceiling_void 2, walls 1, floor 1 | yes |
| `hc-pharmacy` | Pharmacy, Dispensary | `pharmacy` | 2 | none | yes |
| `hc-lab` | Lab, Laboratory, Pathology | `lab`, `pressure_room` | 2 | none | no |
| `hc-lobby` | Lobby, Reception, Waiting, Entrance, Atrium | `lobby` | 3 | none | yes |
| `hc-office` | Office, Admin, Staff, Meeting, Store | `office` | 2 | none | yes |
| `hc-live-any` | any room with LongName containing "Live" | `occupied_adjacent` | 2 | none | no |

Used downstream: `or_room`, `imaging` and `plant_room` select sequence cards (`card_or_room`, `card_imaging`, `card_plant_room`); `pressure_room` and `occupied_adjacent` drive the pressure test, ICRA and quiet-hours rules; `ward` selects the ward fit-out recipe through the `R-ceiling-ward` rule.

### Industrial

| Rule | Names matched | Tags | max_crews | Faces |
| --- | --- | --- | --- | --- |
| `ind-control` | Control Room, CCR, LER, Operator, Control Building | `control_room` | 2 | walls 2, ceiling_void 2, floor 1 |
| `ind-switchroom` | Switch Room, MCC, Substation, E-House, Electrical Room, Transformer Bay/Yard | `switchroom` | 3 | plant_pad 2, walls 2, ceiling_void 2 |
| `ind-pump-house` | Pump House, Pump Station, Pump Shelter | `pump_house` | 3 | plant_pad 2, ceiling_void 2 |
| `ind-compressor` | Compressor House/Building/Shelter, Machinery Hall, Turbine Hall | `compressor_house`, `heavy_lift_area` | 3 | plant_pad 2, structure 2 |
| `ind-rack` | Pipe Rack, Pipe Bridge, Rack | `pipe_rack` | 4 | structure 3, external 2, ceiling_void 2 |
| `ind-process-unit` | Process Unit/Area, Unit n, Area n, Process Module | `process_unit`, `heavy_lift_area` | 5 | plant_pad 3, structure 3, ceiling_void 2 |
| `ind-tank-farm` | Tank Farm, Bund, Tankage, Storage Area | `tank_farm`, `heavy_lift_area` | 3 | plant_pad 2, structure 2, below_ground 2 |
| `ind-laydown` | Laydown, Yard, Marshalling | `laydown` | 2 | none |
| `ind-warehouse` | Warehouse, Workshop, Store, Maintenance | `warehouse` | 2 | none |

`heavy_lift_area` is the tag the slab on grade rule keys on, so process units, compressor houses and tank farms get the slab recipe.

### Civil

| Rule | Names matched | Tags | max_crews | Faces | Shift |
| --- | --- | --- | --- | --- | --- |
| `civ-live-lane` | Live Lane/Carriageway/Traffic, Existing Lane/Carriageway, Traffic Lane | `live_traffic`, `segment` | 2 | floor 1, below_ground 1 | no |
| `civ-bridge` | Abutment, Pier, Span, Bridge, Deck, Girder | `bridge`, `heavy_lift_area` | 4 | structure 3, roof 2, below_ground 2 | yes |
| `civ-culvert` | Culvert, Headwall, Box Section | `culvert` | 3 | below_ground 3, structure 2 | yes |
| `civ-utilities` | Drain, Drainage, Chamber, Manhole, Utility, Duct Bank, Service Corridor | `utilities` | 3 | below_ground 3 | yes |
| `civ-verge` | Verge, Footway, Footpath, Landscape, Cycle Track/Lane, Shoulder | `verge` | 2 | floor 2 | yes |
| `civ-segment` | Carriageway, Lane, Segment, Chainage, CHnnn | `segment` | 3 | floor 2, below_ground 2 | yes |

`civil` is the one sector where `segment` zones normally outnumber everything else; the sequence card `card_road_segment` has no zone tag, so it applies to any zone, while `card_bridge` and `card_culvert` select on `bridge` and `culvert`.

## Authoring rules of thumb

* Put the most specific names first, especially where one word appears in several rooms ("pressure" in pressure rooms and plant rooms, "ward" for live and non-live wards).
* Do not use `^` or `$` anchors on names unless the model is known to use them exactly; room names often carry level and number suffixes ("Operating Theatre 2 L01").
* Keep `max_crews` realistic against the scenario crew pool: the zone cap only limits crews in the zone, it does not create crews.
* `faces` values are per-face crew caps in addition to `max_crews`; omit `faces` where face limits add nothing.
