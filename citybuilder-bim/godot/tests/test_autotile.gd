extends TC

const N := Vector2i(0, -1)
const S := Vector2i(0, 1)
const E := Vector2i(1, 0)
const W := Vector2i(-1, 0)


func _pick(dirs: Array[Vector2i]) -> Dictionary:
    return RoadAutotile.choose(dirs)


func _opens(r: Dictionary) -> Array[Vector2i]:
    return RoadAutotile.open_sides(str(r["variant"]), int(r["k"]))


func test_variants_by_neighbour_count() -> void:
    eq(_pick([]).variant, "straight", "isolated -> straight")
    eq(_pick([E]).variant, "straight", "dead end -> straight")
    eq(_pick([E, W]).variant, "straight", "opposite -> straight")
    eq(_pick([N, S]).variant, "straight", "opposite -> straight")
    eq(_pick([N, E]).variant, "corner", "adjacent -> corner")
    eq(_pick([S, W]).variant, "corner", "adjacent -> corner")
    eq(_pick([N, E, S]).variant, "split", "three -> split")
    eq(_pick([N, E, S, W]).variant, "intersection", "four -> intersection")


func test_orientation_matches_open_sides() -> void:
    for dirs in [[E, W], [N, S], [N, E], [E, S], [S, W], [W, N], [N, E, S], [E, S, W], [S, W, N], [W, N, E], [N, E, S, W]]:
        var typed: Array[Vector2i] = []
        typed.assign(dirs)
        var r: Dictionary = _pick(typed)
        var open: Array[Vector2i] = _opens(r)
        eq(open.size(), typed.size(), "open side count for %s" % str(typed))
        for d in typed:
            ok(open.has(d), "variant %s k=%d opens towards %s" % [r["variant"], r["k"], str(d)])
    # dead end aligns with the neighbour axis
    var r1: Dictionary = _pick([E])
    ok(_opens(r1).has(E), "dead end towards east runs along x")
    var r2: Dictionary = _pick([N])
    ok(_opens(r2).has(N), "dead end towards north runs along z")


func test_catalog_resolves_roads() -> void:
    var cat := SiteTileCatalog.new()
    var tiles: Dictionary = {}
    for c in [Vector2i(1, 1), Vector2i(2, 1), Vector2i(1, 2)]:
        tiles[c] = {"tile": "haul_road", "orientation": 0}
    var r: Dictionary = cat.resolve(tiles, Vector2i(1, 1), "haul_road", 0)
    eq(int(r["item"]), int(cat.item_ids["haul_road:corner"]), "L-shaped road gets a corner")
    var r2: Dictionary = cat.resolve(tiles, Vector2i(2, 1), "haul_road", 0)
    eq(int(r2["item"]), int(cat.item_ids["haul_road:default"]), "end piece is straight")
    ok(cat.item_ids.has("existing_building:3"), "building variants present")
    ok(cat.tiles.size() == SiteTiles.ALL_TILES.size(), "all tile ids have a SiteTile")
    for id in SiteTiles.ALL_TILES:
        ok(cat.tiles.has(id), "catalog has %s" % id)
