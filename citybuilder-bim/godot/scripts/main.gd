extends Node3D
## Root of the game scene. Keeps the Kenney View / Camera / GridMap / Sun / CanvasLayer nodes from
## main.tscn and builds the rest in code: ground, SiteBuilder, BimView, ZoneOverlay and the UI.

enum Mode { BUILD, ASSIGN }

const MENU_SCENE: String = "res://scenes/menu.tscn"
const SECONDS_PER_WEEK: float = 3.0
const MARGIN: float = 8.0
const LEFT_W: float = 280.0
const RIGHT_W: float = 318.0
const EDITOR_W: float = 1100.0
const INSTALLATIONS_W: float = 330.0

@onready var view: Node3D = $View
@onready var camera: Camera3D = $View/Camera
@onready var grid: GridMap = $GridMap
@onready var canvas: CanvasLayer = $CanvasLayer

var gs: SimState = null
var builder: SiteBuilder = null
var bim_view: BimView = null
var overlay: ZoneOverlay = null
var ui_root: Control = null
var top_bar: TopBar = null
var crew_panel: CrewPanel = null
var inspector: ZoneInspector = null
var procurement: ProcurementPanel = null
var charts: ChartsPanel = null
var gantt: GanttPanel = null
var seq_editor: SequenceEditor = null
var installations: InstallationsPanel = null
var whats_needed: WhatsNeededDialog = null
var toast: EventToast = null
var report: Report = null
var hint_bar: HintBar = null
## HUD docks: crews + charts on the left, zone inspector + procurement on the right (children stack, never overlap).
var left_dock: VBoxContainer = null
var right_dock: VBoxContainer = null
var focus_badge: PanelContainer = null
var _focus_label: Label = null
var _focus_plane: MeshInstance3D = null
var _editor_hid_left: bool = false
var _layout_pending: bool = false
var _badge_wanted: bool = false
var _editor_was_open: bool = false
var _editor_hid_gantt: bool = false

var mode: int = Mode.BUILD
var pinned_zone: String = "":
    set(v):
        pinned_zone = v
        if overlay != null:
            overlay.set_highlight_zone(v)  # pulsing rim in the 3D view
var _accum: float = 0.0
var _last_speed: int = 1
var _ground: Node3D = null


func _ready() -> void:
    gs = get_node("/root/GameState")
    var scenarios: Node = get_node("/root/Scenarios")
    if scenarios.current_bundle == null:
        for a in OS.get_cmdline_user_args():
            if a.begins_with("--scenario="):
                scenarios.call("select", "res://scenarios/%s/sequence.json" % a.substr(11))
    if scenarios.current_bundle == null:
        get_tree().change_scene_to_file.call_deferred(MENU_SCENE)
        return
    if not gs.start(scenarios.current_bundle):
        push_error("cannot start scenario: %s" % gs.last_error)
        get_tree().change_scene_to_file.call_deferred(MENU_SCENE)
        return
    _build_world()
    _build_ui()
    _wire()
    _set_mode(Mode.BUILD)
    _set_focus(0)
    _reflow_bottom()
    view.call("frame_site", gs.bundle.site_rect)  # again, now that the HUD insets are known


func _exit_tree() -> void:
    if gs != null and gs.speed != 0:
        gs.speed = 0


# ------------------------------------------------------------------ construction

func _build_world() -> void:
    var b: SequenceBundle = gs.bundle
    view.call("frame_site", b.site_rect)
    _build_ground(b)
    builder = SiteBuilder.new()
    builder.name = "SiteBuilder"
    add_child(builder)
    builder.setup(gs, grid, camera)
    bim_view = BimView.new()
    bim_view.name = "BimView"
    add_child(bim_view)
    bim_view.setup(gs)
    overlay = ZoneOverlay.new()
    overlay.name = "ZoneOverlay"
    add_child(overlay)
    overlay.setup(gs, camera)


## Desaturated sage terrain: a large outer plane, a darker base plate under the site footprint and a thin lighter
## edge, so pale elements and the Kenney tiles keep their contrast.
const GROUND_OUTER_COLOR: Color = Color(0.52, 0.57, 0.46)
const GROUND_PLATE_COLOR: Color = Color(0.40, 0.46, 0.36)


func _build_ground(b: SequenceBundle) -> void:
    _ground = Node3D.new()
    _ground.name = "Ground"
    add_child(_ground)
    var centre := Vector3(b.site_rect.position.x + (b.site_rect.size.x - 1) * 0.5, 0.0,
            b.site_rect.position.y + (b.site_rect.size.y - 1) * 0.5)
    var outer := MeshInstance3D.new()
    outer.name = "Terrain"
    var om := PlaneMesh.new()
    om.size = Vector2(b.site_rect.size.x + 1.0 + 40.0, b.site_rect.size.y + 1.0 + 40.0)
    var omat := StandardMaterial3D.new()
    omat.albedo_color = GROUND_OUTER_COLOR
    omat.roughness = 1.0
    om.material = omat
    outer.mesh = om
    outer.position = centre + Vector3(0, -0.05, 0)
    _ground.add_child(outer)
    var plane := MeshInstance3D.new()
    plane.name = "BasePlate"
    var pm := PlaneMesh.new()
    pm.size = Vector2(b.site_rect.size.x + 1.0, b.site_rect.size.y + 1.0)
    var mat := StandardMaterial3D.new()
    mat.albedo_color = GROUND_PLATE_COLOR
    mat.roughness = 1.0
    pm.material = mat
    plane.mesh = pm
    plane.position = centre + Vector3(0, -0.02, 0)
    _ground.add_child(plane)
    # blocked cells read as dark pads
    for c in gs.scenario.blocked_cells:
        var mi := MeshInstance3D.new()
        var bm := BoxMesh.new()
        bm.size = Vector3(0.96, 0.08, 0.96)
        var bmat := StandardMaterial3D.new()
        bmat.albedo_color = Color(0.2, 0.2, 0.22)
        bm.material = bmat
        mi.mesh = bm
        mi.position = Vector3(c.x, 0.0, c.y)
        _ground.add_child(mi)


func _build_ui() -> void:
    ui_root = Control.new()
    ui_root.name = "UI"
    ui_root.set_anchors_preset(Control.PRESET_FULL_RECT)
    ui_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
    ui_root.theme = UiStyle.make_theme()
    canvas.add_child(ui_root)

    top_bar = _scene("res://scenes/ui/top_bar.tscn") as TopBar
    UiStyle.place(top_bar, Rect2(0, 0, 1, 0), Vector4(8, 6, -8, 0))
    ui_root.add_child(top_bar)
    top_bar.setup(gs)

    left_dock = _make_dock("LeftDock", LEFT_W)
    ui_root.add_child(left_dock)
    right_dock = _make_dock("RightDock", RIGHT_W)
    ui_root.add_child(right_dock)

    crew_panel = _scene("res://scenes/ui/crew_panel.tscn") as CrewPanel
    crew_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
    left_dock.add_child(crew_panel)
    crew_panel.setup(gs)

    inspector = _scene("res://scenes/ui/zone_inspector.tscn") as ZoneInspector
    inspector.size_flags_vertical = Control.SIZE_EXPAND_FILL
    right_dock.add_child(inspector)
    inspector.setup(gs)

    procurement = _scene("res://scenes/ui/procurement_panel.tscn") as ProcurementPanel
    # procurement takes what the inspector leaves: all of it while no zone is shown, a third of it otherwise
    procurement.size_flags_vertical = Control.SIZE_EXPAND_FILL
    procurement.size_flags_stretch_ratio = 1.0
    inspector.size_flags_stretch_ratio = 2.5
    right_dock.add_child(procurement)
    procurement.setup(gs)

    charts = _scene("res://scenes/ui/charts_panel.tscn") as ChartsPanel
    left_dock.add_child(charts)
    charts.setup(gs)

    gantt = GanttPanel.new()
    gantt.name = "GanttPanel"
    ui_root.add_child(gantt)
    gantt.setup(gs, gs.scenario.gantt_visible_default)

    seq_editor = SequenceEditor.new()
    seq_editor.name = "SequenceEditor"
    UiStyle.place(seq_editor, Rect2(1, 0, 1, 1), Vector4(-EDITOR_W - RIGHT_W - 16, 74, -RIGHT_W - 16, -8))
    ui_root.add_child(seq_editor)
    seq_editor.setup(gs, bim_view)
    installations = InstallationsPanel.create(ui_root, gs, bim_view, view, camera)  # kit installations list + picking (I)
    inspector.gs_kit_colours = bim_view != null and bim_view.marker_mesh_provider.is_valid()

    whats_needed = WhatsNeededDialog.new()
    whats_needed.name = "WhatsNeededDialog"
    ui_root.add_child(whats_needed)
    whats_needed.setup(gs)

    hint_bar = _scene("res://scenes/ui/hint_bar.tscn") as HintBar
    UiStyle.place(hint_bar, Rect2(0.5, 1, 0.5, 1), Vector4(-300, -8, 300, -8))
    hint_bar.grow_horizontal = Control.GROW_DIRECTION_BOTH
    hint_bar.grow_vertical = Control.GROW_DIRECTION_BEGIN
    ui_root.add_child(hint_bar)
    hint_bar.setup(gs)

    focus_badge = PanelContainer.new()
    focus_badge.name = "FocusBadge"
    focus_badge.mouse_filter = Control.MOUSE_FILTER_IGNORE
    _focus_label = UiStyle.label("", 14, UiStyle.WARN)
    focus_badge.add_child(_focus_label)
    ui_root.add_child(focus_badge)

    toast = _scene("res://scenes/ui/event_toast.tscn") as EventToast
    UiStyle.place(toast, Rect2(0.5, 0, 0.5, 0), Vector4(-210, 82, 210, 82))
    toast.grow_horizontal = Control.GROW_DIRECTION_BOTH
    ui_root.add_child(toast)
    toast.setup(gs)

    report = _scene("res://scenes/ui/report.tscn") as Report
    ui_root.add_child(report)
    report.setup(gs)


func _scene(path: String) -> Control:
    return (load(path) as PackedScene).instantiate() as Control


func _make_dock(dock_name: String, width: float) -> VBoxContainer:
    var d := VBoxContainer.new()
    d.name = dock_name
    d.mouse_filter = Control.MOUSE_FILTER_IGNORE
    d.custom_minimum_size = Vector2(width, 0)
    d.add_theme_constant_override("separation", MARGIN as int)
    return d


func _wire() -> void:
    top_bar.speed_requested.connect(_set_speed)
    top_bar.next_week_requested.connect(func() -> void: _advance())
    top_bar.panel_toggled.connect(_toggle_panel)
    top_bar.export_requested.connect(_export)
    top_bar.menu_requested.connect(_to_menu)
    top_bar.gantt_toggled.connect(func() -> void: gantt.toggle())
    gantt.zone_selected.connect(_on_gantt_zone)
    top_bar.sequence_toggled.connect(_toggle_sequence_editor)
    inspector.sequence_editor_requested.connect(func(zid: String) -> void:
        pinned_zone = zid
        seq_editor.open_for_zone(zid)
        _reflow_bottom())
    inspector.whats_needed_requested.connect(func(zid: String) -> void: whats_needed.open_for_zone(zid))
    seq_editor.whats_needed_requested.connect(func(zid: String, guid: String) -> void:
        if guid != "":
            whats_needed.open_for_element(guid)
        else:
            whats_needed.open_for_zone(zid))
    seq_editor.message.connect(hint_bar.show_message)
    seq_editor.closed.connect(_reflow_bottom)
    seq_editor.visibility_changed.connect(_reflow_bottom)
    for p in [crew_panel, inspector, procurement, charts, installations]:
        (p as Control).visibility_changed.connect(_queue_layout)
    whats_needed.message.connect(hint_bar.show_message)
    gantt.layout_changed.connect(_reflow_bottom)
    get_viewport().size_changed.connect(_reflow_bottom)
    top_bar.minimum_size_changed.connect(_queue_layout)
    hint_bar.minimum_size_changed.connect(_queue_layout)
    toast.minimum_size_changed.connect(_queue_layout)
    toast.visibility_changed.connect(_queue_layout)
    crew_panel.crew_selected.connect(_on_crew_selected)
    crew_panel.equipment_arm_requested.connect(func(id: String) -> void:
        _set_mode(Mode.BUILD)
        builder.arm_equipment(id)
        _update_tool_text())
    crew_panel.message.connect(hint_bar.show_message)
    procurement.message.connect(hint_bar.show_message)
    inspector.message.connect(hint_bar.show_message)
    builder.message.connect(hint_bar.show_message)
    builder.palette_changed.connect(func(_t: String) -> void: _update_tool_text())
    builder.equipment_armed_changed.connect(func(_id: String) -> void: _update_tool_text())
    overlay.zone_hovered.connect(_on_zone_hovered)
    overlay.zone_clicked.connect(_on_zone_clicked)
    report.export_requested.connect(_export)
    report.menu_requested.connect(_to_menu)
    report.restart_requested.connect(func() -> void:
        gs.start(gs.bundle)
        crew_panel.selected_crew_id = -1
        pinned_zone = ""
        _set_mode(Mode.BUILD)
        _set_focus(0))
    gs.event_fired.connect(func(_e: EventDef, choices: Array) -> void:
        if not choices.is_empty():
            _set_speed(0))
    gs.incident_occurred.connect(func(_z: String) -> void: hint_bar.show_message("Incident! The zone is stopped for a week."))


# ------------------------------------------------------------------ mode / focus / speed

func _set_mode(m: int) -> void:
    mode = m
    builder.active = (m == Mode.BUILD)
    overlay.pick_enabled = (m == Mode.ASSIGN)
    top_bar.set_mode_text("Build mode" if m == Mode.BUILD else "Assign mode")
    _update_tool_text()


func _update_tool_text() -> void:
    if mode == Mode.BUILD:
        if builder.pending_equipment != "":
            var def: EquipmentDef = gs.equipment_def(builder.pending_equipment)
            hint_bar.set_tool_text("[Build] Place %s on a crane pad (Esc cancels)" % (def.name if def != null else ""))
        else:
            var t: String = builder.current_tile()
            hint_bar.set_tool_text("[Build] %s: %s, rent %s/wk | LMB place, RMB rotate, DEL demolish, Q/E tile | Tab: assign" % [
                str(SiteTiles.DISPLAY_NAMES.get(t, t)), Fmt.money(gs.scenario.tile_place_cost(t)),
                Fmt.money(gs.scenario.tile_weekly_cost(t))])
    else:
        hint_bar.set_tool_text("[Assign] Select a crew, click a zone | PgUp/PgDn storey | G ghost | Tab: build")


func _set_focus(idx: int) -> void:
    var lo: int = gs.bundle.storeys[0].index
    var hi: int = gs.bundle.storeys[gs.bundle.storeys.size() - 1].index
    idx = clampi(idx, lo, hi)
    gs.focus_storey_index = idx
    bim_view.set_focus_storey(idx)
    overlay.set_focus_storey(idx)
    view.call("set_focus_height", gs.bundle.storey_y_for_index(idx))
    _update_focus_cue(idx)
    gs.focus_changed.emit(idx)


func _set_speed(s: int) -> void:
    gs.speed = s
    if s > 0:
        _last_speed = s
    _accum = 0.0
    top_bar.refresh()


func _advance() -> void:
    if not gs.pending_event.is_empty():
        hint_bar.show_message("Resolve the event first.")
        return
    gs.advance_week()


func _toggle_panel(n: String) -> void:
    var p: Control = null
    match n:
        "Crews":
            p = crew_panel
        "Zone":
            p = inspector
        "Procure":
            p = procurement
        "Charts":
            p = charts
    if p != null:
        p.visible = not p.visible


## N key / top bar "N" button: toggles the sequence editor for the zone selected in the inspector.
func _toggle_sequence_editor() -> void:
    seq_editor.toggle(pinned_zone if pinned_zone != "" else inspector.zone_id)
    _reflow_bottom()


## A zone picked in the timeline: pin it, show it in the inspector and follow its storey.
func _on_gantt_zone(zone_id: String) -> void:
    pinned_zone = zone_id
    inspector.visible = true
    inspector.show_zone(zone_id)
    seq_editor.show_zone(zone_id)
    gantt.select_zone(zone_id)
    var z: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    if z != null and gs.bundle.storeys_by_id.has(z.storey_id):
        _set_focus((gs.bundle.storeys_by_id[z.storey_id] as StoreyData).index)


## Lays out the HUD around the timeline and the docks, and tells the camera which part of the screen is free:
## top bar and docks keep their margins, the left dock yields to the sequence editor when the screen is too narrow,
## the hint bar sits in the free middle, and the 3D camera is shifted so the model stays centred in what is left.
func _reflow_bottom() -> void:
    if gantt == null or left_dock == null:
        return
    # On a screen shorter than 900 px the timeline steps aside while the editor is open (the editor has its own lane);
    # it comes back when the editor closes. Pressing T while the editor is open still shows it.
    var editor_open: bool = seq_editor != null and seq_editor.visible
    if editor_open != _editor_was_open:
        _editor_was_open = editor_open
        var short_screen: bool = get_viewport().get_visible_rect().size.y < 900.0
        if editor_open and gantt.visible and short_screen:
            _editor_hid_gantt = true
            gantt.set_open(false)
        elif not editor_open and _editor_hid_gantt:
            _editor_hid_gantt = false
            gantt.set_open(true)
    var vp: Vector2 = get_viewport().get_visible_rect().size
    var inset: float = gantt.bottom_inset()
    var top: float = top_bar.offset_top + maxf(top_bar.size.y, top_bar.get_combined_minimum_size().y) + MARGIN
    var bottom: float = MARGIN + inset
    charts.set_compact(vp.y - top - bottom < 540.0)
    inspector.set_show_lane(not gantt.visible)
    # the editor wants EDITOR_W px (at least its minimum); the left dock yields when both cannot fit
    var right_used: bool = inspector.visible or procurement.visible
    var right_w: float = (RIGHT_W + MARGIN) if right_used else 0.0
    # the installations list (kits) is a third column left of the right dock
    var inst: Control = ui_root.get_node_or_null("InstallationsPanel") as Control
    var inst_w: float = (INSTALLATIONS_W + MARGIN) if inst != null and inst.visible else 0.0
    right_w += inst_w
    var left_used: bool = crew_panel.visible or charts.visible
    var left_w: float = (LEFT_W + MARGIN) if left_used and not _editor_hid_left else 0.0
    if seq_editor.visible:
        var need: float = seq_editor.custom_minimum_size.x + MARGIN * 2.0
        var room_with_left: float = vp.x - right_w - (LEFT_W + MARGIN) - MARGIN
        if left_used and not _editor_hid_left and room_with_left < need:
            _editor_hid_left = true
            left_w = 0.0
    elif _editor_hid_left:
        _editor_hid_left = false
        left_w = (LEFT_W + MARGIN) if left_used else 0.0
    left_dock.visible = not _editor_hid_left
    _set_rect(left_dock, Vector4(MARGIN, top, MARGIN + LEFT_W, -bottom), 0.0)
    _set_rect(right_dock, Vector4(-MARGIN - RIGHT_W, top, -MARGIN, -bottom), 1.0)
    # free area between the docks
    var free_l: float = MARGIN + left_w
    var free_r: float = vp.x - MARGIN - right_w
    var free_cx: float = (free_l + free_r) * 0.5
    # hint bar: bottom of the free area (explicit rect from its minimum size, it is re-laid out when that changes)
    var hmin: Vector2 = hint_bar.get_combined_minimum_size()
    var hw: float = clampf(maxf(hmin.x, 600.0), 0.0, maxf(free_r - free_l, 600.0))
    var hint_h: float = hmin.y
    _set_free_rect(hint_bar, vp, free_cx - hw * 0.5, vp.y - bottom - hint_h, hw, hint_h)
    # sequence editor: right-aligned against the right dock
    if seq_editor != null:
        var ew: float = clampf(free_r - free_l, seq_editor.custom_minimum_size.x, EDITOR_W)
        seq_editor.anchor_left = 0.0
        seq_editor.anchor_right = 0.0
        seq_editor.anchor_top = 0.0
        seq_editor.anchor_bottom = 1.0
        seq_editor.offset_left = free_r - ew
        seq_editor.offset_right = free_r
        seq_editor.offset_top = top
        seq_editor.offset_bottom = -(bottom + hint_h + MARGIN)  # the hint bar stays visible under it
    # installations list (kits agent): between the editor column and the right dock
    if inst != null:
        inst.anchor_left = 1.0
        inst.anchor_right = 1.0
        inst.offset_right = -MARGIN - (right_w - inst_w)
        inst.offset_left = inst.offset_right - INSTALLATIONS_W
        inst.offset_top = top
        inst.offset_bottom = -bottom
    # weekly report: right end of the free area, above the hint bar
    report.dock_week_panel(vp.x - free_r, bottom + hint_h + MARGIN)
    # focus badge: top-left of the free area (hidden under the editor); toast: centred under it
    focus_badge.visible = _badge_wanted and not seq_editor.visible
    var bmin: Vector2 = focus_badge.get_combined_minimum_size()
    _set_free_rect(focus_badge, vp, free_l, top, bmin.x, bmin.y)
    var tmin: Vector2 = toast.get_combined_minimum_size()
    var tw: float = maxf(tmin.x, 420.0)
    _set_free_rect(toast, vp, free_cx - tw * 0.5, top + bmin.y + MARGIN, tw, tmin.y)
    view.call("set_insets", free_l, top, vp.x - free_r, maxf(bottom, hint_h + MARGIN * 2.0))


## Places a control at an exact pixel rect (top-left anchored), independent of its previous size.
func _set_free_rect(c: Control, vp: Vector2, x: float, y: float, w: float, h: float) -> void:
    c.anchor_left = 0.0
    c.anchor_right = 0.0
    c.anchor_top = 0.0
    c.anchor_bottom = 0.0
    c.offset_left = x
    c.offset_top = y
    c.offset_right = x + w
    c.offset_bottom = y + h
    c.grow_horizontal = Control.GROW_DIRECTION_END
    c.grow_vertical = Control.GROW_DIRECTION_END


func _set_rect(c: Control, o: Vector4, anchor_x: float) -> void:
    c.anchor_left = anchor_x
    c.anchor_right = anchor_x
    c.anchor_top = 0.0
    c.anchor_bottom = 1.0
    c.offset_left = o.x
    c.offset_top = o.y
    c.offset_right = o.z
    c.offset_bottom = o.w


## Layout after the current frame (panel visibility changes arrive in bursts).
func _queue_layout() -> void:
    if _layout_pending:
        return
    _layout_pending = true
    _flush_layout.call_deferred()


func _flush_layout() -> void:
    _layout_pending = false
    _reflow_bottom()


## Storey focus cue: a badge naming the focused storey and, above ground level, a translucent plane at its floor.
func _update_focus_cue(idx: int) -> void:
    if _focus_label == null:
        return
    var st: StoreyData = null
    for s in gs.bundle.storeys:
        if s.index == idx:
            st = s
    var lo: int = gs.bundle.storeys[0].index
    var hi: int = gs.bundle.storeys[gs.bundle.storeys.size() - 1].index
    _focus_label.text = "Storey focus: %s   (PgUp / PgDn)" % (st.name if st != null else str(idx))
    _badge_wanted = hi > lo
    focus_badge.visible = _badge_wanted and not seq_editor.visible
    if _focus_plane == null:
        _focus_plane = MeshInstance3D.new()
        _focus_plane.name = "FocusPlane"
        var pm := PlaneMesh.new()
        var r: Rect2i = gs.bundle.site_rect
        pm.size = Vector2(r.size.x + 1.0, r.size.y + 1.0)
        var mat := StandardMaterial3D.new()
        mat.albedo_color = Color(0.35, 0.6, 1.0, 0.2)
        mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
        mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
        mat.cull_mode = BaseMaterial3D.CULL_DISABLED
        pm.material = mat
        _focus_plane.mesh = pm
        _focus_plane.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        add_child(_focus_plane)
        # a bright rim makes the floor level readable from any angle
        var rim_mat := StandardMaterial3D.new()
        rim_mat.albedo_color = Color(0.3, 0.6, 1.0, 0.9)
        rim_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
        var hx: float = pm.size.x * 0.5
        var hz: float = pm.size.y * 0.5
        for e in [[Vector3(pm.size.x, 0.06, 0.12), Vector3(0, 0, -hz)], [Vector3(pm.size.x, 0.06, 0.12), Vector3(0, 0, hz)],
                [Vector3(0.12, 0.06, pm.size.y), Vector3(-hx, 0, 0)], [Vector3(0.12, 0.06, pm.size.y), Vector3(hx, 0, 0)]]:
            var rim := MeshInstance3D.new()
            var bm := BoxMesh.new()
            bm.size = e[0]
            bm.material = rim_mat
            rim.mesh = bm
            rim.position = e[1]
            rim.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
            _focus_plane.add_child(rim)
        _focus_plane.position = Vector3(r.position.x + (r.size.x - 1) * 0.5, 0.0, r.position.y + (r.size.y - 1) * 0.5)
    var y: float = gs.bundle.storey_y_for_index(idx)
    _focus_plane.visible = idx > lo
    _focus_plane.position.y = y + 0.02


func _on_crew_selected(crew_id: int) -> void:
    if crew_id >= 0:
        _set_mode(Mode.ASSIGN)
    inspector.show_zone(overlay.hovered_zone_id if overlay.hovered_zone_id != "" else pinned_zone)


func _on_zone_hovered(zone_id: String) -> void:
    inspector.show_zone(zone_id if zone_id != "" else pinned_zone)


func _on_zone_clicked(zone_id: String) -> void:
    pinned_zone = zone_id
    inspector.show_zone(zone_id)
    seq_editor.show_zone(zone_id)
    if crew_panel.selected_crew_id >= 0:
        if gs.assign_crew(crew_panel.selected_crew_id, zone_id):
            var z: ZoneData = gs.bundle.zones_by_id[zone_id]
            hint_bar.show_message("Crew assigned to %s" % z.name)


# ------------------------------------------------------------------ actions

func _export() -> void:
    var path: String = PlanExport.export_to_user(gs)
    if path == "":
        hint_bar.show_message("Plan export failed")
    else:
        hint_bar.show_message("Plan exported: %s" % ProjectSettings.globalize_path(path))


func _to_menu() -> void:
    get_node("/root/Scenarios").set("current_bundle", null)
    get_tree().change_scene_to_file(MENU_SCENE)


func _process(delta: float) -> void:
    if gs == null or gs.bundle == null:
        return
    if gs.speed > 0 and not gs.finished and gs.pending_event.is_empty():
        _accum += delta * float(gs.speed)
        if _accum >= SECONDS_PER_WEEK:
            _accum = 0.0
            gs.advance_week()


func _unhandled_input(event: InputEvent) -> void:
    if gs == null or gs.bundle == null:
        return
    if event.is_action_pressed("speed_pause"):
        _set_speed(_last_speed if gs.speed == 0 else 0)
    elif event.is_action_pressed("speed_1"):
        _set_speed(1)
    elif event.is_action_pressed("speed_2"):
        _set_speed(2)
    elif event.is_action_pressed("speed_4"):
        _set_speed(4)
    elif event.is_action_pressed("storey_up"):
        _set_focus(gs.focus_storey_index + 1)
    elif event.is_action_pressed("storey_down"):
        _set_focus(gs.focus_storey_index - 1)
    elif event.is_action_pressed("gantt_toggle"):
        gantt.toggle()
    elif event.is_action_pressed("installations_toggle"):
        installations.toggle()
    elif event.is_action_pressed("sequence_editor_toggle"):
        _toggle_sequence_editor()
    elif event.is_action_pressed("toggle_ghost"):
        bim_view.set_ghost_visible(not bim_view.show_ghost)
        hint_bar.show_message("Ghost elements: %s" % ("shown" if bim_view.show_ghost else "hidden"))
    elif event.is_action_pressed("export_plan"):
        _export()
    elif event.is_action_pressed("toggle_mode"):
        _set_mode(Mode.ASSIGN if mode == Mode.BUILD else Mode.BUILD)
    elif event.is_action_pressed("cancel"):
        crew_panel.selected_crew_id = -1
        crew_panel.select_crew(-1)
        pinned_zone = ""
        _set_mode(Mode.BUILD)
    elif event.is_action_pressed("save"):
        hint_bar.show_message("Game saved" if SaveGame.save(gs) else "Save failed")
    elif event.is_action_pressed("load"):
        if SaveGame.load_into(gs):
            hint_bar.show_message("Game loaded")
        else:
            hint_bar.show_message("Load failed: %s" % gs.last_error)
