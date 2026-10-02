class_name RoadAutotile
extends RefCounted
## Neighbour-based road tile selection among Kenney straight / corner / intersection / split.
##
## Base orientation of the Kenney models (k = 0): straight runs along Z (open sides +z,-z);
## corner opens to -x and +z; split (T) opens to -x,+x,+z; intersection opens on all four.
## One GridMap quarter turn k maps a direction (dx, dz) to (dz, -dx).

const BASE_OPEN: Dictionary = {
    "straight": [Vector2i(0, 1), Vector2i(0, -1)],
    "corner": [Vector2i(-1, 0), Vector2i(0, 1)],
    "split": [Vector2i(-1, 0), Vector2i(1, 0), Vector2i(0, 1)],
    "intersection": [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)],
}


static func rotate_dir(d: Vector2i, k: int) -> Vector2i:
    var r: Vector2i = d
    for i in posmod(k, 4):
        r = Vector2i(r.y, -r.x)
    return r


static func open_sides(variant: String, k: int) -> Array[Vector2i]:
    var out: Array[Vector2i] = []
    for d in BASE_OPEN[variant]:
        out.append(rotate_dir(d, k))
    return out


static func _same_set(a: Array[Vector2i], b: Array[Vector2i]) -> bool:
    if a.size() != b.size():
        return false
    for d in a:
        if not b.has(d):
            return false
    return true


## neighbours: directions (4-neighbourhood) that hold a road-like tile.
## Returns {"variant": String, "k": int}.
static func choose(neighbours: Array[Vector2i]) -> Dictionary:
    var variant: String = "straight"
    match neighbours.size():
        4:
            variant = "intersection"
        3:
            variant = "split"
        2:
            var a: Vector2i = neighbours[0]
            var b: Vector2i = neighbours[1]
            variant = "straight" if (a + b) == Vector2i.ZERO else "corner"
        _:
            variant = "straight"
    var want: Array[Vector2i] = neighbours
    if neighbours.size() == 1:
        # dead end: run straight along that axis
        want = [neighbours[0], -neighbours[0]]
    elif neighbours.is_empty():
        want = [Vector2i(0, 1), Vector2i(0, -1)]
    for k in 4:
        if _same_set(open_sides(variant, k), want):
            return {"variant": variant, "k": k}
    return {"variant": variant, "k": 0}
