extends SceneTree
## Kit catalogue sheet: every kit at fills 0, 0.5 and 1.0 (footprint 2x1; rack 4x1, culvert 3x1) in a labelled grid,
## one SubViewport per kit, saved as a PNG. Run (needs a display, e.g. xvfb):
##   xvfb-run -a -s "-screen 0 1920x1080x24" godot --path godot --rendering-driver opengl3 \
##       --script res://tools/kit_catalogue.gd -- [out=/path/kits_catalogue.png] [kits=rack,tank] [width=1920] [height=1080]
## `mode=installations [weeks=40] [scenario=industrial_standard]` instead plays the scenario in the real game scene, opens
## the Installations panel, selects and hovers the pipe rack and saves docs/img/industrial_installations.png.
## The layout helpers are static so tests can check them without rendering.

const DEFAULT_OUT: String = "res://../docs/img/kits_catalogue.png"
const FILLS: Array[float] = [0.0, 0.5, 1.0]
const COLUMNS: int = 8
const HEADER_H: float = 40.0
const BG: Color = Color(0.72, 0.82, 0.92)
const FOOTPRINTS: Dictionary = {"rack": Vector2i(4, 1), "culvert": Vector2i(3, 1)}
const DEFAULT_FOOTPRINT: Vector2i = Vector2i(2, 1)
const CELL_M: float = 6.0
const WAIT_FRAMES: int = 8

var _out: String = DEFAULT_OUT
var _size: Vector2i = Vector2i(1920, 1080)
var _frames: int = 0
var _reg: KitRegistry = null
var _kits: Array[String] = []
var _stats: Dictionary = {}
var _mode: String = "catalogue"
var _weeks: int = 40
var _scenario: String = "industrial_standard"
var _scale: float = 0.7  ## installations screenshot downscale (keeps the PNG under 400 KB)


## Footprint of a kit in the sheet.
static func footprint_for(kit: String) -> Vector2i:
    return FOOTPRINTS.get(kit, DEFAULT_FOOTPRINT)


## Positions (metres) of the three fill meshes of a kit with bounding size `size`: a diagonal, 0% back left to 100% front
## right, spaced so that neighbours do not overlap on screen.
static func fill_offsets(size: Vector3) -> Array[Vector3]:
    var out: Array[Vector3] = []
    var sx: float = maxf(size.x * 0.95 + 1.0, size.y * 0.25)
    var sz: float = size.z * 0.3 + 1.0
    for i in FILLS.size():
        out.append(Vector3((float(i) - 1.0) * sx, 0.0, (float(i) - 1.0) * sz))
    return out


static func grid_cell(index: int, total: int, sheet: Vector2) -> Rect2:
    var rows: int = int(ceil(float(total) / float(COLUMNS)))
    var cw: float = sheet.x / float(COLUMNS)
    var ch: float = sheet.y / float(rows)
    return Rect2(float(index % COLUMNS) * cw, float(index / COLUMNS) * ch, cw, ch)


func _initialize() -> void:
    var only: PackedStringArray = PackedStringArray()
    for a in OS.get_cmdline_user_args():
        if a.begins_with("out="):
            _out = a.substr(4)
        elif a.begins_with("kits="):
            only = a.substr(5).split(",")
        elif a.begins_with("mode="):
            _mode = a.substr(5)
        elif a.begins_with("weeks="):
            _weeks = int(a.substr(6))
        elif a.begins_with("scale="):
            _scale = float(a.substr(6))
        elif a.begins_with("scenario="):
            _scenario = a.substr(9)
        elif a.begins_with("width="):
            _size.x = int(a.substr(6))
        elif a.begins_with("height="):
            _size.y = int(a.substr(7))
    if _mode == "installations" and _out == DEFAULT_OUT:
        _out = "res://../docs/img/industrial_installations.png"
    if _out.begins_with("res://"):
        _out = ProjectSettings.globalize_path(_out).simplify_path()
    if _mode == "installations":
        await _installations_shot()
        return
    _reg = KitRegistry.new()
    _reg.load_manifest()
    for k in _reg.kits.keys():  # manifest order
        if only.is_empty() or only.has(str(k)):
            _kits.append(str(k))
    DisplayServer.window_set_size(_size)
    root.size = _size
    root.transparent_bg = false
    RenderingServer.set_default_clear_color(Color(0.1, 0.11, 0.14))
    _build_sheet()


func _build_sheet() -> void:
    var ui := Control.new()
    ui.set_anchors_preset(Control.PRESET_FULL_RECT)
    ui.theme = UiStyle.make_theme()
    root.add_child(ui)
    var bg := ColorRect.new()
    bg.color = Color(0.1, 0.11, 0.14)
    bg.set_anchors_preset(Control.PRESET_FULL_RECT)
    ui.add_child(bg)
    var sheet := Vector2(_size)
    for n in _kits.size():
        var kit: String = _kits[n]
        var cell: Rect2 = grid_cell(n, _kits.size(), sheet).grow(-3.0)
        var vp_rect := Rect2(cell.position + Vector2(0, HEADER_H), cell.size - Vector2(0, HEADER_H))
        var holder := SubViewportContainer.new()
        holder.position = vp_rect.position
        holder.size = vp_rect.size
        holder.stretch = true
        ui.add_child(holder)
        var svp := SubViewport.new()
        svp.own_world_3d = true
        svp.size = Vector2i(vp_rect.size)
        svp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
        svp.msaa_3d = Viewport.MSAA_2X
        holder.add_child(svp)
        var labels: Array = _fill_viewport(svp, kit)
        # header: kit title and its builder stats
        var head := Label.new()
        head.text = _reg.kit_title(kit)
        head.add_theme_font_size_override("font_size", 19)
        head.position = cell.position + Vector2(8, 2)
        ui.add_child(head)
        var sub := Label.new()
        var st: Dictionary = _stats.get(kit, {})
        sub.text = "%s · %d tris" % [kit, int(st.get("tris", 0))]
        sub.add_theme_font_size_override("font_size", 12)
        sub.add_theme_color_override("font_color", Color(0.62, 0.66, 0.74))
        sub.position = cell.position + Vector2(8, 24)
        ui.add_child(sub)
        for l in labels:
            var lab := Label.new()
            lab.text = str(l["text"])
            lab.add_theme_font_size_override("font_size", 13)
            lab.add_theme_color_override("font_color", Color(0.15, 0.17, 0.22))
            lab.add_theme_color_override("font_outline_color", Color(1, 1, 1, 0.8))
            lab.add_theme_constant_override("outline_size", 3)
            var p: Vector2 = l["pos"]
            lab.position = vp_rect.position + p - Vector2(14, 0)
            ui.add_child(lab)


## Builds the 3D scene of one kit in `svp`; returns [{text, pos}] label anchors in viewport pixels.
func _fill_viewport(svp: SubViewport, kit: String) -> Array:
    var fp: Vector2i = footprint_for(kit)
    var h: float = float(_reg.kit_param(kit, "default_height_m", 4.0))
    var world := Node3D.new()
    svp.add_child(world)
    var env := WorldEnvironment.new()
    var e := Environment.new()
    e.background_mode = Environment.BG_COLOR
    e.background_color = BG
    e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
    e.ambient_light_color = Color(0.72, 0.72, 0.78)
    e.ambient_light_energy = 0.65
    env.environment = e
    world.add_child(env)
    var sun := DirectionalLight3D.new()
    sun.rotation_degrees = Vector3(-52, -38, 0)
    world.add_child(sun)
    var meshes: Array[Mesh] = []
    var tris: int = 0
    var size := Vector3.ZERO
    for i in FILLS.size():
        var fills: Dictionary = {}
        for l in _reg.layer_ids(kit):
            fills[l] = FILLS[i]
        var variant: String = "rack_mpei" if kit == "rack" else ""
        var mesh: Mesh = _reg.build(kit, {"footprint_cells": fp, "height_m": h, "cell_size_m": CELL_M,
                "layer_fills": fills, "variant": variant, "seed": 3})
        meshes.append(mesh)
        tris = maxi(tris, int(mesh.get_meta("triangles", 0)))
        size = size.max(mesh.get_aabb().size)
    _stats[kit] = {"tris": tris}
    # the three fills on a diagonal: 0% back left, 100% front right
    var offsets: Array[Vector3] = fill_offsets(size)
    var lo := Vector3(1e9, 0.0, 1e9)
    var hi := Vector3(-1e9, h, -1e9)
    var corners: Array[Vector3] = []
    for i in FILLS.size():
        var bb: AABB = meshes[i].get_aabb()
        var mi := MeshInstance3D.new()
        mi.mesh = meshes[i]
        mi.position = offsets[i]
        world.add_child(mi)
        var pad := MeshInstance3D.new()
        var pm := PlaneMesh.new()
        pm.size = Vector2(maxf(bb.size.x, 3.0) + 1.0, maxf(bb.size.z, 3.0) + 1.0)
        var gm := StandardMaterial3D.new()
        gm.albedo_color = Color(0.45, 0.62, 0.38)
        pm.material = gm
        pad.mesh = pm
        pad.position = offsets[i] + Vector3(bb.get_center().x, -0.03, bb.get_center().z)
        world.add_child(pad)
        for k in 8:
            corners.append(offsets[i] + bb.position + Vector3(bb.size.x * float(k & 1), bb.size.y * float((k >> 1) & 1), bb.size.z * float((k >> 2) & 1)))
        lo = lo.min(offsets[i] + bb.position)
        hi = hi.max(offsets[i] + bb.end)
    var cam := Camera3D.new()
    cam.fov = 22.0
    world.add_child(cam)
    var centre: Vector3 = (lo + hi) * 0.5
    var dir: Vector3 = Vector3(0.25, 0.6, 0.75).normalized()
    var aspect: float = float(svp.size.x) / float(svp.size.y)
    var tan_v: float = tan(deg_to_rad(cam.fov * 0.5))
    var tan_h: float = tan_v * aspect
    var d_lo: float = 5.0
    var d_hi: float = 900.0
    for _it in 26:
        var d: float = (d_lo + d_hi) * 0.5
        cam.transform = _look(centre + dir * d, centre)
        var inv: Transform3D = cam.transform.affine_inverse()
        var fits: bool = true
        for c in corners:
            var q: Vector3 = inv * c
            if q.z >= -0.1 or absf(q.x) / (-q.z) > tan_h * 0.94 or absf(q.y) / (-q.z) > tan_v * 0.86:
                fits = false
                break
        if fits:
            d_hi = d
        else:
            d_lo = d
    cam.transform = _look(centre + dir * d_hi, centre - Vector3(0, 0, 0))
    cam.current = true
    var inv2: Transform3D = cam.transform.affine_inverse()
    var labels: Array = []
    for i in FILLS.size():
        var bb2: AABB = meshes[i].get_aabb()
        var base: Vector3 = offsets[i] + Vector3(bb2.get_center().x, 0, bb2.end.z)
        var q2: Vector3 = inv2 * base
        var sx: float = float(svp.size.x) * 0.5 + (q2.x / (-q2.z)) / tan_h * float(svp.size.x) * 0.5
        var sy: float = float(svp.size.y) * 0.5 - (q2.y / (-q2.z)) / tan_v * float(svp.size.y) * 0.5
        labels.append({"text": "%d%%" % int(FILLS[i] * 100.0), "pos": Vector2(sx, minf(sy + 2.0, float(svp.size.y) - 18.0))})
    return labels


static func _look(pos: Vector3, target: Vector3) -> Transform3D:
    return Transform3D(Basis.looking_at(target - pos, Vector3.UP), pos)


func _process(_delta: float) -> bool:
    if _mode != "catalogue":
        return false
    _frames += 1
    if _frames == WAIT_FRAMES:
        var img: Image = root.get_viewport().get_texture().get_image()
        if img.get_size() != _size:
            img.resize(_size.x, _size.y, Image.INTERPOLATE_LANCZOS)
        img.convert(Image.FORMAT_RGB565)  # fewer colours: the PNG stays well under 400 KB
        img.convert(Image.FORMAT_RGB8)
        DirAccess.make_dir_recursive_absolute(_out.get_base_dir())
        var err: int = img.save_png(_out)
        var bytes: int = FileAccess.get_file_as_bytes(_out).size() if err == OK else 0
        print("kit catalogue: %d kits -> %s (%dx%d, %d KB, error %d)" % [_kits.size(), _out, img.get_width(), img.get_height(), bytes / 1024, err])
        quit(0 if err == OK else 1)
    return false


# ------------------------------------------------------------------ installations screenshot

func _installations_shot() -> void:
    var scenarios: Node = root.get_node("Scenarios")
    if not bool(scenarios.call("select", str(scenarios.call("path_for", _scenario)))):
        printerr("kit_catalogue: cannot load scenario %s" % _scenario)
        quit(1)
        return
    DisplayServer.window_set_size(_size)
    root.size = _size
    root.content_scale_size = Vector2i.ZERO
    for i in 3:
        await process_frame
    var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
    root.add_child(main)
    await process_frame
    var gs: SimState = main.get("gs")
    gs.speed = 0
    Planner.auto_layout(gs)
    var guard: int = 0
    while gs.week < _weeks and not gs.finished and guard < _weeks * 3 + 10:
        guard += 1
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        Planner.autopilot(gs, _weeks - gs.week)
    if not gs.pending_event.is_empty():
        gs.resolve_event(0)
    gs.speed = 0
    (main.get("report") as Report).hide_week()
    (main.get("toast") as Control).visible = false
    for i in 4:
        await process_frame
    (main.get("gantt") as GanttPanel).set_open(false)
    var panel: InstallationsPanel = main.get("installations")
    panel.toggle()
    main.call("_reflow_bottom")
    # generic ghost elements are noise in this picture: hide them, keep the kit ghosts (the plan of each installation)
    (main.get("bim_view") as BimView).set_ghost_visible(false)
    var layer: KitLayer = panel.kit_layer()
    layer.set_ghost_visible(true)
    layer.update(null, true)
    panel.refresh()
    # the pipe-rack section with electrical work (else any rack, else the first instance)
    var pick: int = 0
    var cells: Array = []
    for i in layer.instance_count():
        var inst: Dictionary = layer.kit_instances.instances()[i]
        if inst["kit"] == "rack" and (pick == 0 or inst["variant"] == "rack_ei"):
            pick = i
        if inst["kit"] == "rack" or inst["kit"] == "tank":
            cells.append_array(inst["cells"])
    panel.select(pick)
    var view: Node3D = main.get("view")
    var cp: Vector3 = view.get("camera_position")
    cp.y = 0.0
    view.set("camera_position", cp)
    # keep the model clear of the docks and of the Installations list
    view.call("set_insets", 288.0, 74.0, 318.0 + 330.0 + 24.0, 12.0)
    view.call("frame_cells", cells, 4.5, 1.5, true)
    view.call("snap")
    for i in 6:
        await process_frame
    # hover the picked instance so the tooltip is in the picture
    var cam: Camera3D = main.get("camera")
    var bb: AABB = layer.instance_aabb(pick)
    var sp: Vector2 = cam.unproject_position(bb.position + bb.size * Vector3(0.5, 0.45, 0.5))
    # a point of the box where the picker really returns this instance (nearer boxes may cover the centre)
    var found: bool = false
    for fy in [0.5, 0.8, 0.3]:
        for fx in [0.5, 0.25, 0.75, 0.1, 0.9]:
            for fz in [0.5, 0.2, 0.8]:
                var cand: Vector2 = cam.unproject_position(bb.position + bb.size * Vector3(fx, fy, fz))
                if not found and panel.picker.pick_at(cand) == pick:
                    sp = cand
                    found = true
    Input.warp_mouse(sp)
    var ev := InputEventMouseMotion.new()
    ev.position = sp
    ev.global_position = sp
    Input.parse_input_event(ev)
    for i in 8:
        await process_frame
    await RenderingServer.frame_post_draw
    var img: Image = root.get_texture().get_image()
    if _scale < 0.999:
        img.resize(int(img.get_width() * _scale), int(img.get_height() * _scale), Image.INTERPOLATE_LANCZOS)
    img.convert(Image.FORMAT_RGB565)  # fewer colours: the PNG stays well under 400 KB
    img.convert(Image.FORMAT_RGB8)
    DirAccess.make_dir_recursive_absolute(_out.get_base_dir())
    var err: int = img.save_png(_out)
    var bytes: int = FileAccess.get_file_as_bytes(_out).size() if err == OK else 0
    print("kit catalogue: installations screenshot %s (%dx%d, %d KB, error %d, instance %d, hovered %d)" % [_out, img.get_width(), img.get_height(), bytes / 1024, err, pick, panel.picker.hovered])
    root.remove_child(main)
    main.free()
    quit(0 if err == OK else 1)
