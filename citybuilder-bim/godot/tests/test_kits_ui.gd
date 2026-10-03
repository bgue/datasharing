extends TC
## WP-S: kit palette and edge shading, picking maths, tooltip text, the Installations panel, the API views and the
## catalogue tool (its layout helpers; the PNG itself needs a display, see tools/kit_catalogue.gd).

const INDUSTRIAL: String = "res://scenarios/industrial_standard/sequence.json"
const COLORMAP: String = "res://models/Textures/colormap.png"


func _root() -> Window:
    return Engine.get_main_loop().root


func _registry() -> KitRegistry:
    var r := KitRegistry.new()
    ok(r.load_manifest(), "manifest loads")
    return r


func _state() -> SimState:
    var b := SequenceBundle.load_from_path(INDUSTRIAL)
    ok(b.valid, "industrial bundle valid")
    var gs := SimState.new()
    ok(gs.start(b), "state started")
    return gs


# ------------------------------------------------------------------ palette

func test_palette_table_loads_and_matches_the_builder_constants() -> void:
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://kits/palette.json"))
    ok(parsed is Dictionary, "palette.json parses")
    var pal: Dictionary = parsed
    var base: Dictionary = pal["base"]
    var roles: Dictionary = pal["roles"]
    ok(base.size() >= 14 and roles.size() >= 16, "palette has base colours and roles")
    var consts: Dictionary = (load("res://scripts/kits/kit_builder.gd") as GDScript).get_script_constant_map()
    for name in roles:
        ok(consts.has(name), "KitBuilder.%s exists" % name)
        var want: Color = Color.html(str((roles[name] as Dictionary)["hex"]))
        var got: Color = consts[name]
        ok(absf(want.r - got.r) < 0.004 and absf(want.g - got.g) < 0.004 and absf(want.b - got.b) < 0.004,
                "%s matches the palette table (%s vs %s)" % [name, want.to_html(false), got.to_html(false)])
    # the base colours are dominant solid colours of the Kenney colormap
    var img := Image.load_from_file(COLORMAP)
    ok(img != null and not img.is_empty(), "colormap loads")
    var present: Dictionary = {}
    for y in range(0, img.get_height(), 2):
        for x in range(0, img.get_width(), 2):
            present[img.get_pixel(x, y).to_html(false)] = true
    for name in base:
        ok(present.has(str(base[name])), "base colour %s (%s) occurs in colormap.png" % [name, base[name]])
    # discipline identity: structure grey, process orange, electrical yellow, mechanical blue
    var ident: Dictionary = pal["discipline_identity"]
    ok(consts[ident["process"]].r > 0.9 and consts[ident["process"]].b < 0.4, "process stays orange")
    ok(consts[ident["electrical"]].r > 0.9 and consts[ident["electrical"]].g > 0.7 and consts[ident["electrical"]].b < 0.4, "electrical stays yellow")
    ok(consts[ident["mechanical"]].b > consts[ident["mechanical"]].r, "mechanical stays blue")
    var st: Color = consts[ident["structure"]]
    ok(absf(st.r - st.g) < 0.08 and absf(st.b - st.r) < 0.16, "structure stays grey")


func test_edge_darkening_separates_side_from_top_faces() -> void:
    var b := KitBuilder.new()
    var mesh: ArrayMesh = b.build({"footprint_cells": Vector2i(1, 1), "height_m": 4.0})
    ok(mesh.get_surface_count() == 0, "an empty builder draws nothing")
    var box := _BoxKit.new()
    var m: ArrayMesh = box.build({"footprint_cells": Vector2i(1, 1), "height_m": 4.0})
    var arr: Array = m.surface_get_arrays(0)
    var n: PackedVector3Array = arr[Mesh.ARRAY_NORMAL]
    var c: PackedColorArray = arr[Mesh.ARRAY_COLOR]
    var top: float = -1.0
    var side: float = -1.0
    for i in n.size():
        if n[i].y > 0.9:
            top = c[i].r
        elif absf(n[i].x) > 0.9:
            side = c[i].r
    ok(top > 0.0 and side > 0.0, "box has top and side faces")
    ok(side < top * 0.95, "side faces are darker than the top (%.3f < %.3f)" % [side, top])
    ok(side > top * 0.6, "but only slightly (%.3f)" % side)


## One white box, to look at how its faces are shaded.
class _BoxKit extends KitBuilder:
    func _build() -> void:
        if begin_layer("x"):
            box(Vector3(0, 1, 0), Vector3(2, 2, 2), Color(0.8, 0.8, 0.8))


# ------------------------------------------------------------------ picking maths

func test_ray_box_intersection() -> void:
    var box := AABB(Vector3(0, 0, 0), Vector3(2, 2, 2))
    near(KitPicker.ray_aabb(Vector3(1, 1, 10), Vector3(0, 0, -1), box), 8.0, "straight hit on the +z face")
    near(KitPicker.ray_aabb(Vector3(-5, 1, 1), Vector3(1, 0, 0), box), 5.0, "hit along x")
    near(KitPicker.ray_aabb(Vector3(1, 10, 1), Vector3(0, -1, 0), box), 8.0, "hit from above")
    ok(KitPicker.ray_aabb(Vector3(5, 1, 10), Vector3(0, 0, -1), box) < 0.0, "miss to the side")
    ok(KitPicker.ray_aabb(Vector3(1, 1, 10), Vector3(0, 0, 1), box) < 0.0, "box behind the ray")
    near(KitPicker.ray_aabb(Vector3(1, 1, 1), Vector3(0, 0, 1), box), 0.0, "a ray starting inside hits at 0")
    var diag: float = KitPicker.ray_aabb(Vector3(-2, -2, -2), Vector3(1, 1, 1).normalized(), box)
    near(diag, 2.0 * sqrt(3.0), "diagonal ray reaches the corner", 0.001)
    ok(KitPicker.ray_aabb(Vector3(3, 1, 1), Vector3(0, 0, 1), box) < 0.0, "parallel ray outside the slab misses")


func test_pick_returns_the_nearest_box() -> void:
    var boxes: Array = [
        AABB(Vector3(0, 0, 0), Vector3(2, 2, 2)),    # 0: far
        AABB(Vector3(0, 0, 5), Vector3(2, 2, 2)),    # 1: near the camera
        AABB(Vector3(10, 0, 0), Vector3(2, 2, 2)),   # 2: elsewhere
        null,                                          # 3: not pickable
    ]
    eq(KitPicker.pick(boxes, Vector3(1, 1, 20), Vector3(0, 0, -1)), 1, "nearest of two aligned boxes")
    eq(KitPicker.pick(boxes, Vector3(1, 1, -20), Vector3(0, 0, 1)), 0, "from the other side the other box is nearer")
    eq(KitPicker.pick(boxes, Vector3(11, 1, 20), Vector3(0, 0, -1)), 2, "third box")
    eq(KitPicker.pick(boxes, Vector3(30, 1, 20), Vector3(0, 0, -1)), -1, "nothing hit")
    eq(KitPicker.pick([], Vector3.ZERO, Vector3.FORWARD), -1, "no boxes")
    eq(KitPicker.pick(boxes, Vector3(5, 20, 1), Vector3(0, -1, 0).normalized()), -1, "ray passing between boxes")


func test_picking_through_a_camera_hits_the_instance_under_the_cursor() -> void:
    var gs: SimState = _state()
    var layer := KitLayer.new()
    layer.setup(gs, _registry())
    var root: Window = _root()
    root.add_child(layer)
    var cam := Camera3D.new()
    root.add_child(cam)
    var pick_i: int = -1
    for i in layer.instance_count():
        if layer.kit_instances.instances()[i]["kit"] == "stack":
            pick_i = i
    ok(pick_i >= 0, "stack instance exists")
    var bb: AABB = layer.instance_aabb(pick_i)
    var c: Vector3 = bb.get_center()
    cam.look_at_from_position(c + Vector3(0, 12, 0.01), c, Vector3(0, 0, -1))
    cam.current = true
    var picker := KitPicker.new()
    picker.kit_layer_provider = func() -> KitLayer: return layer
    picker.camera = cam
    root.add_child(picker)
    var centre: Vector2 = root.get_visible_rect().size * 0.5
    eq(picker.pick_at(centre), pick_i, "the screen centre looks straight down onto the stack")
    # boxes() are the instance world boxes
    eq(picker.boxes().size(), layer.instance_count(), "one box per instance")
    # selecting emits and outlines
    var got: Array = []
    picker.installation_selected.connect(func(i: int) -> void: got.append(i))
    picker.select(pick_i)
    eq(got, [pick_i], "installation_selected emitted")
    eq(layer.selected_index, pick_i, "layer outlines the selection")
    ok(layer.get_node("Selection").visible, "selection outline visible")
    picker.select(-1)
    ok(not layer.get_node("Selection").visible, "cleared")
    root.remove_child(picker)
    picker.free()
    root.remove_child(cam)
    cam.free()
    root.remove_child(layer)
    layer.free()
    gs.free()


# ------------------------------------------------------------------ tooltip text

func test_tooltip_text_formatting() -> void:
    var reg: KitRegistry = _registry()
    var inst: Dictionary = {
        "kit": "rack", "name": "Pipe rack 4x1 at (4,8)", "zone_id": "L00-Z14", "variant": "rack_ei",
        "overall_fill": 0.4, "layer_fills": {"steel": 1.0, "piping": 0.4, "ei": 0.0, "mech": 0.0},
        "present_layers": ["steel", "piping", "ei"],
    }
    var lines: PackedStringArray = KitPicker.tooltip_text(inst, reg).split("\n")
    eq(lines.size(), 4, "four lines")
    eq(lines[0], "Pipe rack 4x1 at (4,8) · L00-Z14", "name and zone")
    eq(lines[1], "Pipe rack (rack_ei)", "kit and variant")
    eq(lines[2], "Overall 40%", "overall fill")
    eq(lines[3], "steel 100% · piping 40% · EI 0%", "present layers in manifest order, short ids upper-cased")
    var plain: Dictionary = {"kit": "tank", "name": "Storage tank TK-1", "zone_id": "", "variant": "", "overall_fill": 0.0,
            "layer_fills": {}, "present_layers": []}
    var l2: PackedStringArray = KitPicker.tooltip_text(plain, reg).split("\n")
    eq(l2[0], "Storage tank TK-1", "no zone: just the name")
    eq(l2[1], "Storage tank", "no variant: just the kit title")
    eq(l2.size(), 3, "no layers line when nothing is present")
    eq(KitRegistry.layer_label("ei"), "EI", "EI label")
    eq(KitRegistry.layer_label("ring_foundation"), "ring foundation", "underscores become spaces")
    eq(reg.kit_title("pump_plinth"), "Pump", "kit title")
    eq(reg.kit_title("bridge_pier"), "Bridge pier", "kit title")
    for id in reg.kit_ids():
        ok(reg.kit_title(id) != "", "%s has a title" % id)


# ------------------------------------------------------------------ installations panel

class _FakeView extends Node3D:
    var calls: Array = []
    var camera_position: Vector3 = Vector3.ZERO

    func frame_cells(cells: Array, height: float = 2.0, pad: float = 1.0, snap_now: bool = false, _min_zoom: float = 4.0) -> void:
        calls.append({"cells": cells, "height": height, "pad": pad})


func test_installations_panel_lists_every_instance() -> void:
    var gs: SimState = _state()
    var root: Window = _root()
    var ui := Control.new()
    root.add_child(ui)
    var bv := BimView.new()
    root.add_child(bv)
    bv.setup(gs)
    var view := _FakeView.new()
    root.add_child(view)
    var panel: InstallationsPanel = InstallationsPanel.create(ui, gs, bv, view, null)
    var layer: KitLayer = bv.kit_layer()
    ok(layer != null, "kit layer exists")
    var total: int = layer.kit_instances.count()
    eq(panel.row_count(), total, "one row per kit instance (%d)" % total)
    eq(panel.total_count(), total, "total count")
    eq(panel.header_text(), "%d installations · 0 complete" % total, "header counts")
    ok(not panel.visible, "hidden until toggled")
    panel.toggle()
    ok(panel.visible, "toggle opens")
    # groups: sorted by kit title, each instance exactly once
    var seen: Dictionary = {}
    var prev: String = ""
    for g in panel.groups():
        ok(str(g["title"]) >= prev, "groups sorted by title")
        prev = str(g["title"])
        for i in g["items"]:
            ok(not seen.has(i), "instance %d listed once" % i)
            seen[i] = true
            eq(layer.kit_instances.instances()[i]["kit"], g["kit"], "row sits in its kit group")
    eq(seen.size(), total, "all instances grouped")
    # filter
    panel._filter.text = "pump"
    panel.rebuild()
    eq(panel.row_count(), 10, "filter 'pump' keeps the ten pumps")
    panel._filter.text = "L00-Z14"
    panel.rebuild()
    ok(panel.row_count() > 0 and panel.row_count() < total, "zone filter narrows the list (%d)" % panel.row_count())
    panel._filter.text = "no such thing"
    panel.rebuild()
    eq(panel.row_count(), 0, "nothing matches")
    panel._filter.text = ""
    panel.rebuild()
    eq(panel.row_count(), total, "cleared filter restores every row")
    # selection frames the camera and emits
    var got: Array = []
    panel.installation_selected.connect(func(i: int) -> void: got.append(i))
    var target: int = 12
    panel.select(target, true, true)
    eq(got, [target], "selection emitted")
    eq(panel.selected, target, "selected index")
    eq(layer.selected_index, target, "outline follows the selection")
    eq(view.calls.size(), 1, "camera framed once")
    var cells: Array = view.calls[0]["cells"]
    eq(cells.size(), (layer.kit_instances.instances()[target]["cells"] as Array).size(), "framed on the instance cells")
    ok(float(view.calls[0]["height"]) > 0.0, "height of the installation passed for the fit")
    near(view.camera_position.y, gs.bundle.storey_y_for_index(int(layer.kit_instances.instances()[target]["storey_index"])), "camera target on the storey plane")
    ok(not panel.jump_to(-1), "invalid index does nothing")
    ok(not panel.jump_to(total), "index past the end does nothing")
    # complete count follows progress
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        rt.progress = rt.required
        rt.state = TaskRuntime.State.INSPECTED
    layer.update()
    panel.refresh()
    eq(panel.complete_count(), total, "everything complete")
    eq(panel.header_text(), "%d installations · %d complete" % [total, total], "header after completion")
    root.remove_child(view)
    view.free()
    root.remove_child(bv)
    bv.free()
    root.remove_child(ui)
    ui.free()
    gs.free()


# ------------------------------------------------------------------ API views

func test_api_installation_views() -> void:
    var gs: SimState = _state()
    var api := ApiServer.new()
    api.gs = gs
    var res: Dictionary = api._m_view_installations({})
    var ki := KitInstances.new(gs.bundle, _registry())
    eq(int(res["count"]), ki.count(), "count of installations")
    eq((res["installations"] as Array).size(), ki.count(), "one row per instance")
    eq(int(res["complete"]), 0, "none complete at the start")
    var row: Dictionary = (res["installations"] as Array)[0]
    for k in ["index", "kit", "title", "name", "variant", "cells", "storey_id", "zone_id", "element_count", "overall_fill", "complete", "layer_fills", "present_layers", "height_m"]:
        ok(row.has(k), "installation row has %s" % k)
    ok((row["cells"] as Array).size() >= 1 and (row["cells"][0] as Array).size() == 2, "cells are [x, z] pairs")
    ok(int(row["element_count"]) >= 1, "element count")
    ok(JSON.stringify(res) != "", "JSON-serialisable")
    # element layers
    var pump_guid: String = ""
    for e in gs.bundle.elements:
        if ki.registry.kit_for_element(e) == "pump_plinth":
            pump_guid = e.guid
            break
    var el: Dictionary = api._m_view_element_layers({"guid": pump_guid})
    eq(el["kit"], "pump_plinth", "element_layers names the kit")
    ok(el.has("layers") and el.has("overall_fill") and el.has("installation") and el.has("variant"), "element_layers keys")
    var wall_guid: String = ""
    for e in gs.bundle.elements:
        if ki.registry.kit_for_element(e) == "":
            wall_guid = e.guid
            break
    var none: Dictionary = api._m_view_element_layers({"guid": wall_guid})
    eq(none["kit"], null, "elements without a kit report kit null")
    eq(int(none["installation"]), -1, "and installation -1")
    ok(api._m_view_element_layers({"guid": "nope"}).has("__error"), "unknown element is an error")
    ok(api._m_view_element_layers({}).has("__error"), "missing guid is an error")
    # progress shows up in the view
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        rt.progress = rt.required
        rt.state = TaskRuntime.State.INSPECTED
    var done: Dictionary = api._m_view_installations({})
    eq(int(done["complete"]), ki.count(), "all complete after the work is done")
    var el2: Dictionary = api._m_view_element_layers({"guid": pump_guid})
    near(float(el2["overall_fill"]), 1.0, "element layers follow progress")
    # jump
    var r0: Dictionary = api._m_view_jump_to_installation({"index": 3})
    eq(r0["framed"], false, "no view attached: not framed")
    eq(int(r0["index"]), 3, "index echoed")
    ok(r0["installation"] is Dictionary, "installation returned")
    var jumps: Array = []
    api.jump_handler = func(i: int) -> bool:
        jumps.append(i)
        return true
    var r1: Dictionary = api._m_view_jump_to_installation({"index": 5})
    eq(r1["framed"], true, "handler framed it")
    eq(jumps, [5], "handler called with the index")
    ok(api._m_view_jump_to_installation({"index": 9999}).has("__error"), "index out of range")
    ok(api._m_view_jump_to_installation({"index": -1}).has("__error"), "negative index")
    ok(api._m_view_jump_to_installation({}).has("__error"), "missing index")
    api.free()
    gs.free()


func test_api_registers_the_view_methods() -> void:
    var api := ApiServer.new()
    api.gs = SimState.new()
    api._register()
    var names: Array[String] = api.method_names()
    for m in ["view.installations", "view.element_layers", "view.jump_to_installation"]:
        ok(names.has(m), "%s registered" % m)
    api.gs.free()
    api.free()


# ------------------------------------------------------------------ catalogue tool

func test_catalogue_script_parses_and_lays_out_a_grid() -> void:
    var script: GDScript = load("res://tools/kit_catalogue.gd") as GDScript
    ok(script != null and script.can_instantiate(), "kit_catalogue.gd compiles")
    var reg: KitRegistry = _registry()
    eq(script.call("footprint_for", "rack"), Vector2i(4, 1), "rack 4x1")
    eq(script.call("footprint_for", "culvert"), Vector2i(3, 1), "culvert 3x1")
    eq(script.call("footprint_for", "tank"), Vector2i(2, 1), "others 2x1")
    var offs: Array = script.call("fill_offsets", Vector3(10, 5, 4))
    eq(offs.size(), 3, "three fills")
    ok(offs[0].x < offs[1].x and offs[1].x < offs[2].x, "0% back left to 100% front right")
    ok(offs[2].x - offs[0].x > 10.0, "meshes do not overlap")
    var sheet := Vector2(1600, 900)
    var n: int = reg.kit_ids().size()
    var area: float = 0.0
    for i in n:
        var r: Rect2 = script.call("grid_cell", i, n, sheet)
        area += r.size.x * r.size.y
        ok(Rect2(Vector2.ZERO, sheet).encloses(r), "cell %d inside the sheet" % i)
        for j in range(i + 1, n):
            ok(not r.intersects(script.call("grid_cell", j, n, sheet), false), "cells %d and %d do not overlap" % [i, j])
    var rows: int = int(ceil(float(n) / float(script.get("COLUMNS"))))
    near(area, sheet.x * sheet.y * float(n) / float(int(script.get("COLUMNS")) * rows), "the cells tile the sheet", 1.0)
