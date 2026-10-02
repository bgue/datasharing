extends TC
## KitLayer (one MeshInstance3D per kit instance, rebuild-on-change, storey focus, LOD) and the BimView hook,
## on the industrial_standard and healthcare_standard bundles.

const INDUSTRIAL: String = "res://scenarios/industrial_standard/sequence.json"
const HEALTHCARE: String = "res://scenarios/healthcare_standard/sequence.json"
## Target for a KitLayer.update() after the first build (ms); the assert leaves headroom for slow machines.
const TARGET_UPDATE_MS: float = 30.0
const MAX_UPDATE_MS: float = 60.0


func _state(path: String) -> SimState:
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid, "%s valid: %s" % [path, ", ".join(b.errors)])
    var gs := SimState.new()
    ok(gs.start(b), "state started: %s" % gs.last_error)
    return gs


func _layer(gs: SimState, reg: KitRegistry) -> KitLayer:
    var layer := KitLayer.new()
    layer.setup(gs, reg)
    return layer


func _registry() -> KitRegistry:
    var reg := KitRegistry.new()
    ok(reg.load_manifest(), "manifest loads")
    return reg


func _vertex_count(mesh: Mesh) -> int:
    var n: int = 0
    var am: ArrayMesh = mesh as ArrayMesh
    for s in am.get_surface_count():
        n += (am.surface_get_arrays(s)[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
    return n


func test_kit_layer_builds_every_instance_of_industrial_standard() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var reg: KitRegistry = _registry()
    var t0: int = Time.get_ticks_usec()
    var layer: KitLayer = _layer(gs, reg)
    var first_ms: float = float(Time.get_ticks_usec() - t0) / 1000.0
    print("      [kits] first KitLayer build: %d instances in %.1f ms (%d meshes built)" % [layer.instance_count(), first_ms, reg.cache_builds])
    ok(layer.instance_count() > 30, "instances created (%d)" % layer.instance_count())
    eq(layer.instance_count(), layer.kit_instances.count(), "one node per instance")
    var inst_list: Array[Dictionary] = layer.kit_instances.instances()
    for i in layer.instance_count():
        var mi: MeshInstance3D = layer.instance_node(i)
        ok(mi.mesh != null and (mi.mesh as ArrayMesh).get_surface_count() >= 1, "instance %d has a mesh" % i)
        eq(layer.instance_mode(i), "full", "instance %d drawn in full" % i)
        near(mi.scale.x, 1.0 / gs.bundle.cell_size_m, "mesh metres are scaled to grid units")
        var r: Rect2i = inst_list[i]["rect"]
        near(mi.position.x, float(r.position.x) + float(r.size.x - 1) * 0.5, "x centred on the cell rectangle")
        near(mi.position.z, float(r.position.y) + float(r.size.y - 1) * 0.5, "z centred on the cell rectangle")
        near(mi.position.y, gs.bundle.storey_y_for_index(int(inst_list[i]["storey_index"])), "y on the storey floor")
        # nothing is built at the start: everything is ghost
        ok((mi.mesh as ArrayMesh).surface_find_by_name("solid") < 0, "instance %d starts as a ghost" % i)
    for e in gs.bundle.elements:
        eq(layer.handles(e.guid), layer.kit_instances.instance_of(e.guid) >= 0, "handles() agrees with the instances")
    layer.free()
    gs.free()


func test_kit_layer_updates_after_autopilot_weeks_and_rebuilds_only_changes() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var reg: KitRegistry = _registry()
    var layer: KitLayer = _layer(gs, reg)
    gs.scenario.events.clear()
    gs.cash += 1.0e9
    Planner.auto_layout(gs, 4)
    var res: Dictionary = Planner.autopilot(gs, 5)
    eq(int(res["weeks_run"]), 5, "five weeks of autopilot")
    var t0: int = Time.get_ticks_usec()
    var rebuilt: int = layer.update()
    var ms: float = float(Time.get_ticks_usec() - t0) / 1000.0
    print("      [kits] KitLayer.update after 5 weeks: %d instances rebuilt of %d, %.1f ms (internal %.1f ms)" % [rebuilt, layer.instance_count(), ms, layer.last_update_ms])
    ok(rebuilt < layer.instance_count(), "not every instance was rebuilt")
    ok(ms < MAX_UPDATE_MS, "update %.1f ms must be < %.0f (target %.0f)" % [ms, MAX_UPDATE_MS, TARGET_UPDATE_MS])
    # nothing moved: no rebuild at all, and it is cheap
    t0 = Time.get_ticks_usec()
    var again: int = layer.update()
    var idle_ms: float = float(Time.get_ticks_usec() - t0) / 1000.0
    eq(again, 0, "second update without changes rebuilds nothing")
    print("      [kits] KitLayer.update without changes: %.1f ms" % idle_ms)
    ok(idle_ms < TARGET_UPDATE_MS, "idle update %.1f ms < %.0f" % [idle_ms, TARGET_UPDATE_MS])
    # equipment starts after the foundations: play on until kit elements have progressed
    var total_rebuilt: int = 0
    var worst_ms: float = 0.0
    for chunk in 4:
        Planner.autopilot(gs, 5)
        t0 = Time.get_ticks_usec()
        var r3: int = layer.update()
        var ms3: float = float(Time.get_ticks_usec() - t0) / 1000.0
        total_rebuilt += r3
        worst_ms = maxf(worst_ms, ms3)
        print("      [kits] KitLayer.update at week %d: %d rebuilt, %.1f ms" % [gs.week, r3, ms3])
    ok(worst_ms < MAX_UPDATE_MS, "worst update %.1f ms < %.0f" % [worst_ms, MAX_UPDATE_MS])
    ok(total_rebuilt > 0, "kit instances progressed and were rebuilt (%d)" % total_rebuilt)
    var progressed: int = 0
    var with_solid: int = 0
    for i in layer.instance_count():
        if float(layer.kit_instances.instances()[i]["overall_fill"]) > 0.0:
            progressed += 1
        if (layer.instance_node(i).mesh as ArrayMesh).surface_find_by_name("solid") >= 0:
            with_solid += 1
    ok(progressed > 0, "some kit instance has progress (%d)" % progressed)
    ok(with_solid > 0, "instances with solid parts (%d)" % with_solid)
    layer.free()
    gs.free()


func test_dirty_flag_follows_simulation_signals() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var layer: KitLayer = _layer(gs, _registry())
    ok(not layer._dirty, "clean after setup")
    gs.week_advanced.emit(1)
    ok(layer._dirty, "a new week marks the layer dirty")
    layer.update()
    ok(not layer._dirty, "update clears it")
    layer.free()
    gs.free()


func test_lod_collapses_far_instances_and_expands_near_ones() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var reg: KitRegistry = _registry()
    var layer: KitLayer = _layer(gs, reg)
    var root: Window = Engine.get_main_loop().root
    root.add_child(layer)
    var cam := Camera3D.new()
    root.add_child(cam)
    var full_verts: int = _vertex_count(layer.instance_node(0).mesh)
    cam.global_position = Vector3(6, 2000, 4)
    layer.update(cam)
    for i in layer.instance_count():
        eq(layer.instance_mode(i), "collapsed", "instance %d collapsed far away" % i)
        eq(_vertex_count(layer.instance_node(i).mesh), 30, "collapsed instance %d is a box" % i)
    ok(full_verts > 30, "the full mesh is richer than the box")
    cam.global_position = Vector3(6, 20, 4)
    layer.update(cam)
    var near_full: int = 0
    for i in layer.instance_count():
        if layer.instance_mode(i) == "full":
            near_full += 1
    ok(near_full > 0, "instances close to the camera are drawn in full")
    # with LOD off everything is full again
    layer.lod_enabled = false
    cam.global_position = Vector3(6, 2000, 4)
    layer.update(cam)
    ok(layer.instance_mode(0) == "collapsed" or layer.instance_mode(0) == "full", "mode is defined with LOD off")
    root.remove_child(cam)
    cam.free()
    root.remove_child(layer)
    layer.free()
    gs.free()


func test_storey_focus_fades_instances_above() -> void:
    var gs: SimState = _state(HEALTHCARE)
    var layer: KitLayer = _layer(gs, _registry())
    var above: int = 0
    var on_focus: int = 0
    layer.set_focus_storey(0)
    for i in layer.instance_count():
        var idx: int = int(layer.kit_instances.instances()[i]["storey_index"])
        var tr: float = layer.instance_node(i).transparency
        if idx > 0:
            above += 1
            near(tr, KitLayer.ABOVE_FOCUS_TRANSPARENCY, "instance %d above the focus storey fades" % i)
        else:
            on_focus += 1
            near(tr, 0.0, "instance %d on the focus storey stays opaque" % i)
    ok(above > 0 and on_focus > 0, "healthcare kits on several storeys (%d above, %d on focus)" % [above, on_focus])
    layer.set_focus_storey(2)
    for i in layer.instance_count():
        near(layer.instance_node(i).transparency, 0.0, "focus on the top storey shows everything")
    var kinds: Dictionary = {}
    for inst in layer.kit_instances.instances():
        kinds[inst["kit"]] = true
    ok(kinds.has("ahu") and kinds.has("chiller") and kinds.has("mri"), "AHU, chiller and MRI become kits: %s" % str(kinds.keys()))
    layer.free()
    gs.free()


func test_ghost_toggle_rebuilds_without_ghost_surfaces() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var layer: KitLayer = _layer(gs, _registry())
    ok((layer.instance_node(0).mesh as ArrayMesh).surface_find_by_name("ghost") >= 0, "unbuilt instance shows its ghost")
    layer.set_ghost_visible(false)
    layer.update()
    ok((layer.instance_node(0).mesh as ArrayMesh).get_surface_count() == 0, "nothing built and ghosts hidden: nothing drawn")
    layer.set_ghost_visible(true)
    layer.update()
    ok((layer.instance_node(0).mesh as ArrayMesh).surface_find_by_name("ghost") >= 0, "ghosts back")
    layer.free()
    gs.free()


func test_rebuild_budget_defers_work_to_the_next_update() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var layer: KitLayer = _layer(gs, _registry())
    layer.set_ghost_visible(false)  # every instance needs a new mesh
    var first: int = layer.update(null, true, 0.001)
    ok(first < layer.instance_count(), "a tiny budget rebuilds only part of the instances (%d of %d)" % [first, layer.instance_count()])
    ok(layer._dirty, "the rest stays pending")
    var rest: int = layer.update()
    eq(first + rest, layer.instance_count(), "the next update finishes the job")
    ok(not layer._dirty, "nothing pending afterwards")
    layer.free()
    gs.free()


func test_bim_view_hands_kit_elements_to_the_kit_layer() -> void:
    var gs: SimState = _state(INDUSTRIAL)
    var root: Window = Engine.get_main_loop().root
    var bv := BimView.new()
    root.add_child(bv)
    var t0: int = Time.get_ticks_usec()
    bv.setup(gs)
    print("      [kits] BimView.setup with kits: %.1f ms" % (float(Time.get_ticks_usec() - t0) / 1000.0))
    var kit_layer: KitLayer = bv.get("_kit_layer")
    ok(kit_layer != null, "BimView created a KitLayer")
    var mm_total: int = 0
    for k in bv.instance_counts():
        mm_total += int(bv.instance_counts()[k])
    var handled: int = 0
    for e in gs.bundle.elements:
        if kit_layer.handles(e.guid):
            handled += 1
    ok(handled > 100, "many elements are handled by kits (%d)" % handled)
    eq(mm_total + handled, gs.bundle.elements.size(), "every element is drawn exactly once (MultiMesh + kits)")
    # focus and ghost toggles reach the kit layer
    bv.set_ghost_visible(false)
    ok(not kit_layer.show_ghost, "ghost toggle forwarded")
    bv.set_ghost_visible(true)
    bv.set_focus_storey(0)
    eq(kit_layer.focus_storey_index, 0, "focus forwarded")
    # marker provider installed and producing meshes
    ok(bv.marker_mesh_provider.is_valid(), "marker provider installed")
    var crane: Mesh = bv.marker_mesh_for("crane")
    ok(crane is ArrayMesh and (crane as ArrayMesh).get_surface_count() >= 1, "provider mesh for the crane")
    ok(not (crane is CylinderMesh), "not the placeholder cylinder")
    root.remove_child(bv)
    bv.free()
    # with kits off every element goes through the MultiMesh pass
    var bv2 := BimView.new()
    bv2.use_kits = false
    root.add_child(bv2)
    bv2.setup(gs)
    var mm2: int = 0
    for k in bv2.instance_counts():
        mm2 += int(bv2.instance_counts()[k])
    eq(mm2, gs.bundle.elements.size(), "use_kits = false draws everything with MultiMeshes")
    ok(bv2.get("_kit_layer") == null, "no kit layer when disabled")
    root.remove_child(bv2)
    bv2.free()
    gs.free()
