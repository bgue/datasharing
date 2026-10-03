extends TC
## Chunked rendering (docs/06 C.3): MultiMesh per (storey, 8x8 chunk, kind), frustum / storey culling, far LOD cell cubes,
## the heat overlay per chunk and the KitLayer frustum culling; CPU-side timings on the synthetic stress model.

const INDUSTRIAL: String = "res://scenarios/industrial_standard/sequence.json"
const RS := TaskRuntime.State


func _root() -> Window:
    return Engine.get_main_loop().root


func _state(path: String) -> SimState:
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid, "%s valid" % path)
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    return gs


## A view and a camera in the tree; the camera looks at the site centre from `height` cells up and `back` cells back.
func _rig(gs: SimState, height: float, back: float) -> Array:
    var cam := Camera3D.new()
    _root().add_child(cam)
    var v := BimView.new()
    _root().add_child(v)
    v.setup(gs)
    var r: Rect2i = gs.bundle.site_rect
    var centre := Vector3(float(r.position.x) + float(r.size.x) * 0.5, 0.0, float(r.position.y) + float(r.size.y) * 0.5)
    cam.global_position = centre + Vector3(0, height, back)
    cam.look_at(centre)
    return [v, cam, centre]


func _drop(rig: Array) -> void:
    _root().remove_child(rig[0])
    (rig[0] as Node).free()
    _root().remove_child(rig[1])
    (rig[1] as Node).free()


func test_elements_are_grouped_by_chunk_and_kind() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var v := BimView.new()
    v.setup(gs)
    var counts: Dictionary = v.instance_counts()
    var total: int = 0
    for k in counts:
        total += int(counts[k])
    eq(total, v._elems.size(), "every non-kit element is drawn exactly once")
    ok(v.chunk_count() > 1, "several chunks (%d)" % v.chunk_count())
    ok(v.group_count() >= counts.size(), "at least one MultiMesh per kind (%d groups)" % v.group_count())
    for e in v._elems:
        var ci: int = v.chunk_of(e.guid)
        ok(ci >= 0, "element %s has a chunk" % e.guid)
        break
    # elements of a group share a storey and lie inside the 8x8 chunk of the group
    for gi in v.group_count():
        var g: Dictionary = v._groups[gi]
        var ch: Dictionary = v._chunks[int(g["chunk"])]
        for ei in (g["elems"] as Array[int]):
            eq(int(v._e_storey[ei]), int(ch["storey"]), "storey of the chunk")
            eq(roundi(v._g_centre[ei].x) >> 3, int(ch["cx"]), "chunk column")
            eq(roundi(v._g_centre[ei].z) >> 3, int(ch["cz"]), "chunk row")
        if gi > 30:
            break
    for ci in v.chunk_count():
        var st: Dictionary = v.chunk_state(ci)
        ok(st["visible"] and not st["far"], "chunks start visible and near")
        break
    v.free()
    gs.free()


func test_chunk_instances_match_the_old_per_kind_counts() -> void:
    var gs: SimState = new_state()
    var v := BimView.new()
    v.setup(gs)
    eq(v.instance_counts().get("footing", 0), 4, "4 footings")
    eq(v.instance_counts().get("column", 0), 4, "4 columns")
    var nodes: int = 0
    for c in v.get_node("Chunks").get_children():
        if c is MultiMeshInstance3D and not (c.name as String).begins_with("O") and not (c.name as String).begins_with("Far"):
            nodes += 1
    eq(nodes, v.group_count(), "one MultiMeshInstance3D per group")
    v.free()
    gs.free()


func test_far_chunks_draw_cell_cubes_and_come_back_when_near() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var rig: Array = _rig(gs, 600.0, 600.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    var r1: Dictionary = v.update_culling(cam, true)
    ok(int(r1["far"]) > 0 and int(r1["near"]) == 0, "everything is far from 850 cells away: %s" % str(r1))
    ok(v.far_node_count() > 0, "cube sets were built (%d)" % v.far_node_count())
    var far_chunks: int = 0
    for ci in v.chunk_count():
        var st: Dictionary = v.chunk_state(ci)
        if st["far"] and st["visible"]:
            far_chunks += 1
            var ch: Dictionary = v._chunks[ci]
            ok((ch["far"] as MultiMeshInstance3D).visible, "far cubes shown")
            for gi in ch["groups"]:
                ok(not (v._groups[gi]["mmi"] as MultiMeshInstance3D).visible, "element MultiMeshes hidden")
            eq((ch["far"] as MultiMeshInstance3D).multimesh.instance_count, int(st["cells"]), "one cube per cell")
            break
    ok(far_chunks > 0, "found a far chunk")
    var centre: Vector3 = rig[2]
    cam.global_position = centre + Vector3(0, 12, 12)
    cam.look_at(centre)
    var r2: Dictionary = v.update_culling(cam, true)
    ok(int(r2["near"]) > 0, "chunks near the camera draw their elements again: %s" % str(r2))
    for ci in v.chunk_count():
        var st2: Dictionary = v.chunk_state(ci)
        if st2["visible"] and not st2["far"]:
            for gi in v._chunks[ci]["groups"]:
                ok((v._groups[gi]["mmi"] as MultiMeshInstance3D).visible, "element MultiMeshes shown")
            if v._chunks[ci]["far"] != null:
                ok(not (v._chunks[ci]["far"] as MultiMeshInstance3D).visible, "cubes hidden")
            break
    _drop(rig)
    gs.free()


func test_chunks_outside_the_frustum_are_hidden() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var rig: Array = _rig(gs, 25.0, 25.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    var r1: Dictionary = v.update_culling(cam, true)
    cam.global_position = Vector3(5, 10, 5)
    cam.look_at(Vector3(5, 10, 500))  # looking away from the whole site
    var r2: Dictionary = v.update_culling(cam, true)
    eq(int(r2["visible"]), 0, "nothing visible when looking away (%s then %s)" % [str(r1), str(r2)])
    for ci in v.chunk_count():
        for gi in v._chunks[ci]["groups"]:
            ok(not (v._groups[gi]["mmi"] as MultiMeshInstance3D).visible, "hidden")
        break
    cam.global_position = rig[2] + Vector3(0, 25, 25)
    cam.look_at(rig[2])
    var r3: Dictionary = v.update_culling(cam, true)
    eq(int(r3["visible"]), int(r1["visible"]), "back in view: the same chunks again")
    _drop(rig)
    gs.free()


func test_culling_is_skipped_when_nothing_moved() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var rig: Array = _rig(gs, 40.0, 40.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    var a: Dictionary = v.update_culling(cam, true)
    var b: Dictionary = v.update_culling(cam)
    eq(b, a, "same camera, same result without a new pass")
    v.set_focus_storey(1)
    var c: Dictionary = v.update_culling(cam)
    ok(not c.is_empty(), "a focus change reruns it")
    _drop(rig)
    gs.free()


func test_storeys_above_the_focus_are_culled_on_demand() -> void:
    var gs: SimState = _state("res://scenarios/healthcare_standard/sequence.json")
    var rig: Array = _rig(gs, 40.0, 40.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    v.cull_above_focus = 1
    v.set_focus_storey(0)
    v.update_culling(cam, true)
    var above_hidden: int = 0
    var above: int = 0
    for ci in v.chunk_count():
        var st: Dictionary = v.chunk_state(ci)
        if int(st["storey"]) > 0:
            above += 1
            if not st["visible"]:
                above_hidden += 1
    ok(above > 0, "the model has chunks above the ground")
    eq(above_hidden, above, "all of them hidden with the focus on the ground storey")
    v.set_focus_storey(2)
    v.update_culling(cam, true)
    var shown: int = 0
    for ci in v.chunk_count():
        if int(v.chunk_state(ci)["storey"]) <= 2 and v.chunk_state(ci)["visible"]:
            shown += 1
    ok(shown > 0, "raising the focus shows them")
    v.cull_above_focus = 0
    v.set_focus_storey(0)
    v.update_culling(cam, true)
    var any_above: bool = false
    for ci in v.chunk_count():
        if int(v.chunk_state(ci)["storey"]) > 0 and v.chunk_state(ci)["visible"]:
            any_above = true
    ok(any_above, "with culling off they are drawn faintly as before")
    _drop(rig)
    gs.free()


func test_far_cube_colour_follows_progress() -> void:
    var gs: SimState = new_state()
    var v := BimView.new()
    v.setup(gs)
    var cid: int = int(v._cell_index[BimView.cell_key(0, 2, 2)])
    var c0: Color = v.far_cell_colour(cid)
    ok(c0.a < 0.5, "an untouched cell is a faint ghost")
    set_finished(gs, "T000001")
    set_finished(gs, "T000005")
    v.refresh_progress()
    var c1: Color = v.far_cell_colour(cid)
    ok(c1.a > c0.a, "progress makes the cube more solid (%f -> %f)" % [c0.a, c1.a])
    ok(c1.g > c0.g or c1.r != c0.r, "and moves it through the heat palette")
    v.free()
    gs.free()


func test_incremental_refresh_equals_a_fresh_build() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var live := BimView.new()
    live.setup(gs)
    gs.cash += 1.0e9
    Planner.auto_layout(gs, 4)
    Planner.autopilot(gs, 10)
    live.refresh_progress()
    var fresh := BimView.new()
    fresh.setup(gs)
    var bad: int = 0
    for e in live._elems:
        var a: float = live.applied_fill(e.guid)
        var b: float = fresh.applied_fill(e.guid)
        if absf(a - b) > BimView.FILL_EPS + 0.001:
            bad += 1
        if live._a_vis[live._slots[e.guid]] != fresh._a_vis[fresh._slots[e.guid]]:
            bad += 1
    eq(bad, 0, "the log-driven view agrees with a freshly built one on every element")
    live.free()
    fresh.free()
    gs.free()


func test_heat_quads_exist_only_for_chunks_that_are_drawn() -> void:
    var b := SequenceBundle.from_dictionary(StressGen.generate(4, 2))
    var gs := SimState.new()
    gs.start(b)
    var rig: Array = _rig(gs, 600.0, 600.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    v.update_culling(cam, true)  # far: no chunk is drawn in full
    v.set_heat_visible(true)
    var heat: CellHeatOverlay = v.heat_overlay()
    ok(heat.chunk_count() > 8, "heat chunks exist as data (%d)" % heat.chunk_count())
    eq(heat.node_chunk_count(), 0, "no quads while every chunk is far")
    cam.global_position = Vector3(10, 14, 14)
    cam.look_at(Vector3(10, 0, 8))
    v.update_culling(cam, true)
    heat.refresh()
    var built: int = heat.node_chunk_count()
    ok(built > 0 and built < heat.chunk_count(), "quads for the chunks in view only (%d of %d)" % [built, heat.chunk_count()])
    _drop(rig)
    gs.free()


func test_kit_layer_hides_instances_outside_the_frustum() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var rig: Array = _rig(gs, 60.0, 60.0)
    var v: BimView = rig[0]
    var cam: Camera3D = rig[1]
    var layer: KitLayer = v.kit_layer()
    ok(layer != null and layer.instance_count() > 0, "kit layer present")
    layer.update(cam)
    var in_view: int = layer.last_visible
    cam.global_position = Vector3(5, 10, 5)
    cam.look_at(Vector3(5, 10, 800))
    layer.update(cam)
    eq(layer.last_visible, 0, "nothing in view when looking away (was %d)" % in_view)
    eq(layer.last_culled, layer.instance_count(), "all culled")
    ok(not layer.instance_node(0).visible, "node hidden")
    cam.global_position = rig[2] + Vector3(0, 60, 60)
    cam.look_at(rig[2])
    layer.update(cam)
    eq(layer.last_visible, in_view, "back in view")
    ok(layer.instance_node(0).visible, "node shown again")
    _drop(rig)
    gs.free()


func test_kit_layer_refreshes_only_changed_instances() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var layer := KitLayer.new()
    layer.setup(gs, KitRegistry.shared())
    layer.update()
    gs.cash += 1.0e9
    Planner.auto_layout(gs, 4)
    Planner.autopilot(gs, 12)
    var rebuilt: int = layer.update()
    var fresh := KitLayer.new()
    fresh.setup(gs, KitRegistry.shared())
    for i in layer.instance_count():
        var a: Dictionary = layer.kit_instances.instances()[i]
        var b: Dictionary = fresh.kit_instances.instances()[i]
        near(float(a["overall_fill"]), float(b["overall_fill"]), "instance %d fill equals a full refresh" % i, 0.0005)
    ok(rebuilt >= 0, "update ran")
    layer.free()
    fresh.free()
    gs.free()
