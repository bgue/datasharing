extends TC
## Progress visuals of ordinary (non-kit) elements (WP-Q): fill, grow-height / grow-length / count kinds, state tints,
## per-cell heat overlay, highlight API and the incremental update path.

const RS := TaskRuntime.State
const INDUSTRIAL: String = "res://scenarios/industrial_standard/sequence.json"
## Target and limit (ms) of one BimView weekly refresh on industrial_standard (limit leaves headroom for slow machines).
const TARGET_REFRESH_MS: float = 40.0
const MAX_REFRESH_MS: float = 120.0


func _view(gs: SimState) -> BimView:
    var v := BimView.new()
    v.setup(gs)
    return v


func _drop(v: BimView) -> void:
    v.free()


## Sets a task's runtime to `state` with `done` crew-days of progress (refreshes the element visuals).
func _work(gs: SimState, task_id: String, state: int, done: float) -> void:
    var rt: TaskRuntime = gs.runtime[task_id]
    rt.progress = done
    gs.set_task_state(task_id, state)
    gs.set_task_state(task_id, state)  # no-op when unchanged; progress was set first


func _state_from(d: Dictionary) -> SimState:
    return state_from_dict(d)


# ------------------------------------------------------------------ fill

func test_fill_of_slab_at_0_half_and_full() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    near(v.element_fill("SLAB1"), 0.0, "slab fill 0 at the start")
    _work(gs, "T000009", RS.ACTIVE, 1.8)
    near(v.element_fill("SLAB1"), 0.5, "1.8 of 3.6 crew-days is half")
    set_finished(gs, "T000009")
    near(v.element_fill("SLAB1"), 1.0, "finished slab fill 1")
    # two tasks on one element: the fill is the crew-day weighted share
    near(v.element_fill("DUCT1"), 0.0, "duct fill 0")
    set_finished(gs, "T000010")
    near(v.element_fill("DUCT1"), 0.6 / 1.6, "0.6 of 1.6 crew-days")
    near(v.element_fill("NOPE"), 0.0, "unknown element has no progress")
    _drop(v)


func test_grow_modes_by_kind() -> void:
    for k in ["wall", "column", "footing", "pile", "pier", "earthwork", "barrier", "tank", "culvert", "generic", "stair"]:
        eq(BimView.grow_mode(k), "height", "%s grows in height" % k)
    for k in ["duct", "pipe", "cable_tray", "kerb", "beam", "pavement", "slab", "roof", "deck"]:
        eq(BimView.grow_mode(k), "length", "%s grows along its extent" % k)
    for k in ["window", "door", "terminal", "equipment", "sign"]:
        eq(BimView.grow_mode(k), "count", "%s appears as a whole" % k)


# ------------------------------------------------------------------ grow height

func test_grow_height_scales_y_with_min_clamp_and_keeps_base() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var full: Transform3D = v.element_transform(gs.bundle.elements_by_guid["COL00"])
    var full_h: float = full.basis.get_scale().y
    var base_y: float = full.origin.y - full_h * 0.5
    # not started: the whole extent as a ghost
    near(v.instance_transform("COL00").basis.get_scale().y, full_h, "ghost column drawn at full height")
    ok(not v.outline_visible("COL00"), "no outline before the work starts")
    # started with no progress: the 6 % minimum
    _work(gs, "T000005", RS.ACTIVE, 0.0)
    var t0: Transform3D = v.instance_transform("COL00")
    near(t0.basis.get_scale().y, full_h * BimView.MIN_FILL, "started element shows the minimum fill")
    near(t0.origin.y - t0.basis.get_scale().y * 0.5, base_y, "base stays on the ground")
    near(t0.basis.get_scale().x, full.basis.get_scale().x, "width unchanged")
    ok(v.outline_visible("COL00"), "in progress: outline of the full extent")
    near(v.outline_transform("COL00").basis.get_scale().y, full_h, "outline spans the full height")
    # half done
    _work(gs, "T000005", RS.ACTIVE, 0.125)
    v.refresh_progress()
    var t1: Transform3D = v.instance_transform("COL00")
    near(t1.basis.get_scale().y, full_h * 0.5, "half of the crew-days is half the height")
    near(t1.origin.y - t1.basis.get_scale().y * 0.5, base_y, "base stays while growing")
    near(v.instance_colour("COL00").a, BimView.FRAMED_ALPHA, "in progress is drawn framed")
    # finished: full height, no outline
    set_finished(gs, "T000005")
    var t2: Transform3D = v.instance_transform("COL00")
    near(t2.basis.get_scale().y, full_h, "complete column at full height")
    near(t2.origin.y, full.origin.y, "complete column in its full place")
    ok(not v.outline_visible("COL00"), "outline gone when complete")
    _drop(v)


func test_flat_slab_grows_across_the_plan() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var full: Transform3D = v.element_transform(gs.bundle.elements_by_guid["SLAB1"])
    _work(gs, "T000009", RS.ACTIVE, 1.8)
    var t: Transform3D = v.instance_transform("SLAB1")
    near(t.basis.get_scale().x, full.basis.get_scale().x * 0.5, "half-poured slab covers half the span")
    near(t.basis.get_scale().y, full.basis.get_scale().y, "thickness unchanged")
    near(t.basis.get_scale().z, full.basis.get_scale().z, "other span unchanged")
    near(t.origin.x - t.basis.get_scale().x * 0.5, full.origin.x - full.basis.get_scale().x * 0.5, "pour starts at the first cell")
    _drop(v)


# ------------------------------------------------------------------ grow length

func test_duct_extends_along_the_cell_run() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    # DUCT1 runs over cells (2,2) and (3,2): along x, 12 m = two grid units, centred on x = 2.5
    var full: Transform3D = v.element_transform(gs.bundle.elements_by_guid["DUCT1"])
    near(full.basis.get_scale().x, 2.0, "duct is two cells long")
    set_finished(gs, "T000010")  # 0.6 of 1.6 crew-days
    var f: float = 0.6 / 1.6
    var t: Transform3D = v.instance_transform("DUCT1")
    near(t.basis.get_scale().x, 2.0 * f, "length scales with the fill")
    near(t.basis.get_scale().z, full.basis.get_scale().z, "section unchanged")
    near(t.origin.x - t.basis.get_scale().x * 0.5, 1.5, "starts at the first cell's edge")
    near(t.origin.y, full.origin.y, "height unchanged")
    ok(v.outline_visible("DUCT1"), "outline shows the rest of the run")
    near(v.outline_transform("DUCT1").basis.get_scale().x, 2.0, "outline spans the run")
    _drop(v)


func test_duct_along_z_grows_along_z() -> void:
    var d: Dictionary = minimal_dict()
    for e in d["elements"]:
        if e["guid"] == "DUCT1":
            e["cells"] = [[2, 2], [2, 3]]
    var gs: SimState = _state_from(d)
    var v: BimView = _view(gs)
    var t: Transform3D = v.instance_transform("DUCT1")
    near(t.basis.get_scale().z, 2.0, "run along z: length on the z axis")
    ok(t.basis.get_scale().x < 0.2, "section on the x axis")
    set_finished(gs, "T000010")
    var f: float = 0.6 / 1.6
    t = v.instance_transform("DUCT1")
    near(t.basis.get_scale().z, 2.0 * f, "z length scales with the fill")
    near(t.origin.z - t.basis.get_scale().z * 0.5, 1.5, "starts at the first cell along z")
    near(t.origin.x, 2.0, "x stays on the run")
    _drop(v)


# ------------------------------------------------------------------ count kinds

func test_count_kinds_flip_at_half() -> void:
    var d: Dictionary = minimal_dict()
    for e in d["elements"]:
        if e["guid"] == "DUCT1":
            e["visual"] = "terminal"
    var gs: SimState = _state_from(d)
    var v: BimView = _view(gs)
    eq(BimView.grow_mode("terminal"), "count", "terminal is a count kind")
    var full: Transform3D = v.element_transform(gs.bundle.elements_by_guid["DUCT1"])
    near(v.instance_colour("DUCT1").a, BimView.GHOST_ALPHA, "ghost at the start")
    set_finished(gs, "T000010")  # fill 0.375
    near(v.instance_colour("DUCT1").a, BimView.GHOST_ALPHA, "still a ghost below half")
    near(v.instance_transform("DUCT1").basis.get_scale().x, full.basis.get_scale().x, "always drawn whole")
    ok(not v.outline_visible("DUCT1"), "count kinds have no outline")
    _work(gs, "T000012", RS.ACTIVE, 0.4)  # 1.0 of 1.6 = 0.625
    v.refresh_progress()
    near(v.element_fill("DUCT1"), 0.625, "fill above half")
    near(v.instance_colour("DUCT1").a, 1.0, "solid from half")
    _drop(v)


# ------------------------------------------------------------------ tints

func test_inspected_and_rework_tints_match_the_kits() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var base: Color = v.base_colour("FOOT0")
    # the kits use the same tint colours and the inspected amount
    near(BimView.INSPECTED_AMOUNT, 0.22, "inspected amount as in the kits")
    ok(BimView.INSPECTED_TINT.is_equal_approx(KitBuilder.INSPECTED_TINT), "inspected tint colour as in the kits")
    ok(BimView.REWORK_TINT.is_equal_approx(KitBuilder.REWORK_TINT), "rework tint colour as in the kits")
    set_finished(gs, "T000001")
    eq(gs.element_visual("FOOT0"), SimState.Visual.INSPECTED, "footing inspected")
    var c: Color = v.instance_colour("FOOT0")
    var want: Color = base.lerp(BimView.INSPECTED_TINT, 0.22)
    ok(absf(c.r - want.r) < 0.01 and absf(c.g - want.g) < 0.01 and absf(c.b - want.b) < 0.01, "inspected = base lerp 0.22 to green")
    near(c.a, 1.0, "inspected is opaque")
    near(v.instance_transform("FOOT0").basis.get_scale().y, v.element_transform(gs.bundle.elements_by_guid["FOOT0"]).basis.get_scale().y, "inspected is full height")
    _work(gs, "T000002", RS.REWORK, 0.2)
    eq(gs.element_visual("FOOT1"), SimState.Visual.REWORK, "footing in rework")
    var r: Color = v.compute_colour("FOOT1", 0.0)
    var b1: Color = v.base_colour("FOOT1")
    ok(r.r > b1.r and r.g < b1.g, "rework is tinted red")
    var r2: Color = v.compute_colour("FOOT1", 1.0)
    ok(r2.r >= r.r and r2.g <= r.g, "the rework tint pulses")
    _drop(v)


# ------------------------------------------------------------------ heat overlay

func test_heat_cell_shares_before_and_after_a_week() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var heat: CellHeatOverlay = v.heat_overlay()
    ok(heat != null, "view owns a heat overlay")
    ok(not v.is_heat_visible(), "heat hidden by default")
    var sh: Dictionary = CellHeatOverlay.shares(gs, "L00")
    eq(sh.size(), 4, "four ground cells carry tasks")
    for c in sh:
        near(float(sh[c]["share"]), 0.0, "share 0 at the start")
    eq(int(sh[Vector2i(2, 2)]["tasks"]), 4, "(2,2): footing, column and the two duct tasks")
    eq(int(sh[Vector2i(2, 3)]["tasks"]), 3, "(2,3): footing, column, wall")
    set_finished(gs, "T000001")
    set_finished(gs, "T000005")
    sh = CellHeatOverlay.shares(gs, "L00")
    near(float(sh[Vector2i(2, 2)]["share"]), 0.49 / 2.09, "(2,2): 0.49 of 2.09 crew-days")
    near(float(sh[Vector2i(3, 3)]["share"]), 0.0, "(3,3) untouched")
    # a scripted week
    var before: float = 0.0
    for c in sh:
        before += float(sh[c]["share"])
    Planner.auto_layout(gs, 2)
    Planner.order_all_due(gs, 8)
    Planner.autopilot(gs, 2)
    var after_sh: Dictionary = CellHeatOverlay.shares(gs, "L00")
    var after: float = 0.0
    for c in after_sh:
        after += float(after_sh[c]["share"])
    ok(after > before, "a week of work raises the shares (%.3f -> %.3f)" % [before, after])
    # the upper storey: slab cells only
    var up: Dictionary = CellHeatOverlay.shares(gs, "L01")
    eq(up.size(), 4, "slab cells on L01")
    eq(int(up[Vector2i(2, 2)]["tasks"]), 1, "one slab task per cell")
    _drop(v)


func test_heat_overlay_colours_toggle_and_rework() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var heat: CellHeatOverlay = v.heat_overlay()
    eq(heat.layer_count(), 2, "one layer per storey")
    eq(heat.cell_counts("L00"), Vector2i(4, 0), "four task cells, none empty")
    ok(CellHeatOverlay.heat_colour(0.0).is_equal_approx(CellHeatOverlay.GREY), "0 % grey")
    ok(CellHeatOverlay.heat_colour(0.5).is_equal_approx(CellHeatOverlay.AMBER), "50 % amber")
    ok(CellHeatOverlay.heat_colour(1.0).is_equal_approx(CellHeatOverlay.GREEN), "100 % green")
    var red: Color = CellHeatOverlay.heat_colour(0.5, true)
    ok(red.r > 0.9 and red.g < CellHeatOverlay.AMBER.g * 0.6, "rework tints the cell red")
    v.set_heat_visible(true)
    ok(v.is_heat_visible() and heat.visible, "heat toggled on")
    var mm: MultiMesh = heat.layer_node("L00").multimesh
    eq(mm.instance_count, 4, "one quad per cell")
    heat.refresh()
    for i in 4:
        ok(heat.quad_colour("L00", i).is_equal_approx(CellHeatOverlay.GREY), "ground cells start grey")
    set_finished(gs, "T000001")
    set_finished(gs, "T000005")
    heat.refresh()
    ok(not heat.quad_colour("L00", 0).is_equal_approx(CellHeatOverlay.GREY), "(2,2) is no longer grey")
    # first cell in sorted order is (2,2); rework on a footing there
    _work(gs, "T000001", RS.REWORK, 0.1)
    heat.refresh()
    var c0: Color = heat.quad_colour("L00", 0)
    ok(c0.r > 0.5 and c0.g < 0.6, "a cell with rework is red-tinted")
    # focus: storey 0 full, storey 1 above the focus hidden
    ok(heat.layer_node("L00").visible and not heat.layer_node("L01").visible, "storeys above the focus are hidden")
    v.set_focus_storey(1)
    ok(heat.layer_node("L01").visible and heat.layer_node("L00").visible, "focus on 1: storey 0 is faint, storey 1 full")
    v.set_heat_visible(false)
    ok(not heat.visible, "heat toggled off")
    _drop(v)


func test_heat_marks_cells_without_tasks() -> void:
    var d: Dictionary = minimal_dict()
    (d["zones"] as Array)[0]["cells"] = [[2, 2], [3, 2], [2, 3], [3, 3], [4, 4]]
    var gs: SimState = _state_from(d)
    var v: BimView = _view(gs)
    eq(v.heat_overlay().cell_counts("L00"), Vector2i(4, 1), "the zone cell no task touches is hatched")
    var hv: Dictionary = ApiViews.heat_view(gs, "L00")
    eq(int(hv["empty_cells"]), 1, "view.heat counts the empty cell")
    eq((hv["cells"] as Array).size(), 4, "view.heat lists the task cells")
    _drop(v)


# ------------------------------------------------------------------ highlight

func test_highlight_adds_and_clears_instances() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    eq(v.highlight_count(), 0, "nothing highlighted at the start")
    eq(v.highlight_elements(["COL00", "COL01", "SLAB1", "NOPE"]), 3, "three known elements")
    eq(v.highlight_count(), 3, "three boxes")
    eq(v.highlight_instance_count(), 3, "three outline instances")
    eq(v.highlight_elements(["DUCT1"], Color(1, 0, 0)), 1, "a new highlight replaces the old one")
    eq(v.highlight_count(), 1, "one box")
    v.clear_highlight()
    eq(v.highlight_count(), 0, "cleared")
    eq(v.highlight_instance_count(), 0, "no instances after clear")
    eq(v.highlight_elements([]), 0, "empty list highlights nothing")
    _drop(v)


func test_highlight_uses_the_kit_instance_footprint() -> void:
    var b := SequenceBundle.load_from_path(INDUSTRIAL)
    ok(b.valid, "industrial valid")
    var gs := SimState.new()
    ok(gs.start(b), "industrial starts")
    var v: BimView = _view(gs)
    var kit_layer: KitLayer = v._kit_layer
    ok(kit_layer != null, "kits active on industrial_standard")
    var members: Array[String] = []
    var inst_id: int = -1
    for g in kit_layer.kit_instances.kit_guids():
        var i: int = kit_layer.kit_instances.instance_of(g)
        if inst_id < 0:
            inst_id = i
        if i == inst_id:
            members.append(g)
    ok(members.size() > 0, "found a kit instance")
    eq(v.highlight_elements(members), 1, "all members of one kit instance share one box")
    var inst: Dictionary = kit_layer.kit_instances.instances()[inst_id]
    var r: Rect2i = inst["rect"]
    var bx: Dictionary = v.element_box(members[0])
    near((bx["size"] as Vector3).x, float(r.size.x), "box spans the instance footprint in x")
    near((bx["size"] as Vector3).z, float(r.size.y), "box spans the instance footprint in z")
    v.free()


func test_api_view_methods() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var api := ApiServer.new()
    api.gs = gs
    api._register()
    var res: Variant = (api._methods["view.highlight"] as Callable).call({"guids": ["COL00"]})
    ok(res is Dictionary and (res as Dictionary).has("__error"), "highlight without a view is an error")
    api.bim_view = v
    res = (api._methods["view.highlight"] as Callable).call({"guids": ["COL00", "COL01", "XX"]})
    eq(int(res["highlighted"]), 2, "two known guids")
    eq((res["unknown"] as Array).size(), 1, "one unknown guid reported")
    eq(v.highlight_count(), 2, "view has two boxes")
    res = (api._methods["view.clear_highlight"] as Callable).call({})
    eq(v.highlight_count(), 0, "cleared through the API")
    res = (api._methods["view.set_heat"] as Callable).call({"on": true})
    ok(bool(res["on"]) and v.is_heat_visible(), "heat on through the API")
    res = (api._methods["view.set_heat"] as Callable).call({"on": false})
    ok(not bool(res["on"]) and not v.is_heat_visible(), "heat off through the API")
    res = (api._methods["view.heat"] as Callable).call({})
    eq(str(res["storey_id"]), "L00", "default storey is the focused one")
    eq((res["cells"] as Array).size(), 4, "four ground cells")
    ok((res["cells"][0] as Dictionary).has("share") and (res["cells"][0] as Dictionary).has("tasks"), "cell rows carry share and tasks")
    res = (api._methods["view.heat"] as Callable).call({"storey_id": "L01"})
    eq(str(res["storey_id"]), "L01", "explicit storey")
    res = (api._methods["view.heat"] as Callable).call({"storey_id": "ZZ"})
    ok((res as Dictionary).has("__error"), "unknown storey is an error")
    ok(api.method_names().has("view.highlight") and api.method_names().has("view.clear_highlight"), "methods listed")
    api.free()
    _drop(v)


# ------------------------------------------------------------------ incremental path

func test_incremental_update_leaves_unchanged_instances_alone() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var n0: int = v.transform_writes
    eq(v.refresh_progress(), 0, "nothing changed: nothing rewritten")
    eq(v.transform_writes, n0, "write counter unchanged")
    _work(gs, "T000009", RS.ACTIVE, 0.5)  # slab fill 0.139
    var n1: int = v.transform_writes
    ok(n1 > n0, "the slab was rewritten when its task started")
    # small progress: below the 2 % threshold -> no rewrite
    gs.runtime["T000009"].progress = 0.5 + 0.01 * 3.6
    eq(v.refresh_progress(), 0, "a move of 1 % is not rewritten")
    eq(v.transform_writes, n1, "write counter unchanged by a small move")
    gs.runtime["T000009"].progress = 0.5 + 0.05 * 3.6
    eq(v.refresh_progress(), 1, "a move of 5 % rewrites only the slab")
    eq(v.transform_writes, n1 + 1, "exactly one write")
    _drop(v)


func test_state_change_updates_only_the_elements_of_the_task() -> void:
    var gs: SimState = new_state()
    var v: BimView = _view(gs)
    var n0: int = v.transform_writes
    gs.set_task_state("T000001", RS.ACTIVE)
    eq(v.transform_writes, n0 + 1, "one element rewritten by one task state change")
    _drop(v)


func test_industrial_ten_weeks_refresh_time() -> void:
    var b := SequenceBundle.load_from_path(INDUSTRIAL)
    ok(b.valid, "industrial valid")
    var gs := SimState.new()
    ok(gs.start(b), "industrial starts")
    var v: BimView = _view(gs)
    ok(v._slots.size() > 100, "many non-kit elements drawn through the MultiMesh pass (%d)" % v._slots.size())
    Planner.auto_layout(gs, 4)
    Planner.order_all_due(gs, 8)
    var worst: float = 0.0
    var worst_refresh: float = 0.0
    var total: float = 0.0
    var writes0: int = v.transform_writes
    for w in 10:
        Planner.autopilot(gs, 1)  # BimView refreshes itself on week_advanced
        worst = maxf(worst, v.last_week_ms)
        worst_refresh = maxf(worst_refresh, v.last_refresh_ms)
        total += v.last_week_ms
    var writes: int = v.transform_writes - writes0
    print("      [element visuals] industrial_standard (%d non-kit elements), 10 autopilot weeks: BimView per week worst %.1f ms, mean %.1f ms (weekly refresh pass worst %.1f ms), %d instance writes (target < %d ms)" % [
            v._slots.size(), worst, total / 10.0, worst_refresh, writes, int(TARGET_REFRESH_MS)])
    ok(worst < MAX_REFRESH_MS, "weekly refresh stays cheap (worst %.1f ms)" % worst)
    ok(writes > 0, "progress was drawn")
    # every started element is drawn partially or whole with an outline only when incomplete
    var partial: int = 0
    for g in v._slots:
        var f: float = v.applied_fill(g)
        if f > 0.0 and f < 1.0 and v.outline_visible(g):
            partial += 1
    print("      [element visuals] partially built non-kit elements with outline after 10 weeks: %d" % partial)
    _drop(v)
