class_name SiteTiles
extends RefCounted
## Tile ids (scenario.schema.json site.initial_tiles.tile) and static rules shared by
## the simulation (SimState) and the visual layer (SiteBuilder).

const HAUL_ROAD := "haul_road"
const LAYDOWN := "laydown"
const CRANE_PAD := "crane_pad"
const WELFARE := "welfare"
const HOARDING := "hoarding"
const GATE := "gate"
const ICRA_BARRIER := "icra_barrier"
const TRAFFIC_CONES := "traffic_cones"
const EXISTING_BUILDING := "existing_building"
const EXISTING_ROAD := "existing_road"
const TREES := "trees"
const GRASS := "grass"

## Laydown capacity (in `laydown_cells` units) that one laydown tile provides.
const LAYDOWN_CAPACITY_PER_TILE := 4

## Tiles the player can place, in palette order.
const PLAYER_TILES: Array[String] = [
    "haul_road", "laydown", "crane_pad", "welfare", "hoarding", "icra_barrier", "traffic_cones",
]
## Tiles that cannot be demolished by the player.
const PERMANENT_TILES: Array[String] = ["existing_building", "existing_road", "gate"]
## Tiles a haul-road path may pass over.
const ROAD_TILES: Array[String] = ["haul_road", "existing_road", "gate"]
const ALL_TILES: Array[String] = [
    "haul_road", "laydown", "crane_pad", "welfare", "hoarding", "gate", "icra_barrier",
    "traffic_cones", "existing_building", "existing_road", "trees", "grass",
]

const DISPLAY_NAMES: Dictionary = {
    "haul_road": "Haul road", "laydown": "Laydown yard", "crane_pad": "Crane pad",
    "welfare": "Welfare cabin", "hoarding": "Hoarding", "gate": "Site gate",
    "icra_barrier": "ICRA barrier", "traffic_cones": "Traffic cones",
    "existing_building": "Existing building", "existing_road": "Existing road",
    "trees": "Trees", "grass": "Grass",
}


static func is_road(tile_id: String) -> bool:
    return ROAD_TILES.has(tile_id)
