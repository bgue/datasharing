extends TC
## Instantiates the real main scene against the minimal bundle and exercises the visual layers.

const RS := TaskRuntime.State


func test_scripts_compile() -> void:
    var failures: Array[String] = []
    var stack: Array[String] = ["res://scripts"]
    while not stack.is_empty():
        var dir_path: String = stack.pop_back()
        var d := DirAccess.open(dir_path)
        for sub in d.get_directories():
            stack.append(dir_path + "/" + sub)
        for f in d.get_files():
            if f.ends_with(".gd"):
                var s: Variant = ResourceLoader.load(dir_path + "/" + f, "", ResourceLoader.CACHE_MODE_IGNORE)
                if s == null or not (s as GDScript).can_instantiate():
                    failures.append(dir_path + "/" + f)
    ok(failures.is_empty(), "scripts that do not compile: %s" % str(failures))


func _make_main() -> Node:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    ok(bool(sc.call("select", MINIMAL_PATH)), "select minimal bundle")
    var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
    Engine.get_main_loop().root.add_child(main)
    return main


func _drop_main(main: Node) -> void:
    Engine.get_main_loop().root.remove_child(main)
    main.free()
    Engine.get_main_loop().root.get_node("Scenarios").set("current_bundle", null)


func test_main_scene_builds() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    ok(gs != null and gs.running, "GameState started by main")
    var bim: BimView = main.get("bim_view")
    ok(bim != null, "BimView created")
    eq(bim.instance_counts().get("footing", 0), 4, "4 footings as one MultiMesh")
    eq(bim.instance_counts().get("column", 0), 4, "4 columns")
    eq(bim.instance_counts().get("slab", 0), 1, "1 slab")
    eq(bim.instance_counts().size(), 5, "five visual kinds (footing, column, slab, duct, wall)")
    # initial: everything ghosted
    near(bim.compute_colour("FOOT0").a, BimView.GHOST_ALPHA, "footing starts as ghost")
    var gm: GridMap = main.get("grid")
    var used: Array[Vector3i] = gm.get_used_cells()
    eq(used.size(), 3, "gate + two existing buildings on the GridMap")
    ok(main.get("top_bar") != null and main.get("crew_panel") != null and main.get("inspector") != null, "UI built")
    _drop_main(main)


func test_bim_view_follows_state() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    var bim: BimView = main.get("bim_view")
    gs.set_task_state("T000001", RS.ACTIVE)
    near(bim.compute_colour("FOOT0").a, BimView.FRAMED_ALPHA, "active element is framed")
    set_finished(gs, "T000001")
    near(bim.compute_colour("FOOT0").a, 1.0, "finished element is solid")
    eq(gs.element_visual("FOOT0"), SimState.Visual.INSPECTED, "inspected footing")
    gs.set_task_state("T000002", RS.REWORK)
    eq(gs.element_visual("FOOT1"), SimState.Visual.REWORK, "rework visual")
    # storey filter: slab sits on storey 1; focus 0 -> above-focus ghost
    gs.set_task_state("T000009", RS.DONE)
    near(bim.compute_colour("SLAB1").a, BimView.ABOVE_FOCUS_ALPHA, "elements above focus storey render as ghost")
    bim.set_focus_storey(1)
    bim._apply_all()
    near(bim.compute_colour("SLAB1").a, 1.0, "focus on storey 1 shows it fully")
    # ghost toggle hides NOT_STARTED
    bim.set_ghost_visible(false)
    bim._apply_all()
    near(bim.compute_colour("COL03").a, 0.0, "ghost elements hidden when toggled off")
    _drop_main(main)


func test_element_transform_units() -> void:
    var main: Node = _make_main()
    var bim: BimView = main.get("bim_view")
    var gs: SimState = main.get("gs")
    var slab: ElementData = gs.bundle.elements_by_guid["SLAB1"]
    var xf: Transform3D = bim.element_transform(slab)
    # cells (2..3, 2..3) -> centroid (2.5, 2.5); storey 1 at 1 * 4 / 6 grid units; size 12 m / 6 m per cell
    near(xf.origin.x, 2.5, "slab centroid x")
    near(xf.origin.z, 2.5, "slab centroid z")
    near(xf.basis.get_scale().x, 2.0, "12 m is two grid units")
    near(xf.origin.y, 4.0 / 6.0 - 0.15 / 6.0, "slab top sits at storey level")
    var col: ElementData = gs.bundle.elements_by_guid["COL00"]
    var cx: Transform3D = bim.element_transform(col)
    near(cx.origin.y, 2.0 / 6.0, "column centred half a storey above ground")
    _drop_main(main)


func test_zone_overlay_colours() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    var ov: ZoneOverlay = main.get("overlay")
    eq(ov.zone_color_key("L00-Z1"), "ready", "ready tasks, no crews: green")
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    eq(ov.zone_color_key("L00-Z1"), "blocked", "crew present but no road: blocked (yellow)")
    build_road(gs)
    gs.set_task_state("T000001", RS.ACTIVE)
    eq(ov.zone_color_key("L00-Z1"), "active", "work in progress: blue")
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 9
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    eq(ov.zone_color_key("L00-Z1"), "congested", "3 crews in a 2-crew zone: red")
    for t in gs.bundle.tasks_by_zone["L01-Z1"]:
        set_finished(gs, (t as TaskData).task_id)
    eq(ov.zone_color_key("L01-Z1"), "done", "all tasks finished: grey")
    ov.refresh()
    _drop_main(main)


func test_ui_panels_render_text() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    var insp: ZoneInspector = main.get("inspector")
    insp.show_zone("L00-Z1")
    insp.refresh()
    ok(insp._text.text.contains("Ready (4)"), "inspector groups ready tasks: " + insp._text.text.substr(0, 120))
    ok(insp._text.text.contains("Waiting for"), "inspector shows blocked reason")
    var crew: CrewPanel = main.get("crew_panel")
    gs.hire("concrete")
    crew.refresh()
    ok(crew._body.get_child_count() > 4, "crew panel lists trades and crews")
    var charts: ChartsPanel = main.get("charts")
    var s: Dictionary = charts.series()
    eq((s["planned"] as Array).size(), 5, "planned S-curve: origin + 4 weeks")
    var top: TopBar = main.get("top_bar")
    top.refresh()
    ok(top._week.text.begins_with("Week 0 / 6"), "week label: " + top._week.text)
    eq(top._tracker.get_child_count(), 2, "phase tracker has one row per storey")
    _drop_main(main)


func _press(node: Node, action: String) -> void:
    var ev := InputEventAction.new()
    ev.action = action
    ev.pressed = true
    node.call("_unhandled_input", ev)


func test_input_and_interaction() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    var builder: SiteBuilder = main.get("builder")
    var grid: GridMap = main.get("grid")
    # per-frame callbacks must not error headless
    builder._process(0.1)
    (main.get("overlay") as ZoneOverlay)._process(0.1)
    (main.get("bim_view") as BimView)._process(0.1)
    # builder: place a haul road at the cursor cell via the build action
    builder.cell = Vector2i(1, 2)
    builder._last_world_ok = true
    builder.select_tile("haul_road")
    var before: int = grid.get_used_cells().size()
    _press(builder, "build")
    eq(gs.tile_at(Vector2i(1, 2)), "haul_road", "build action places the selected tile")
    eq(grid.get_used_cells().size(), before + 1, "GridMap updated")
    builder.cell = Vector2i(2, 2)
    _press(builder, "build")
    eq(gs.tile_at(Vector2i(2, 2)), "", "footprint cell refused")
    _press(builder, "structure_next")
    eq(builder.current_tile(), "laydown", "Q/E cycles the palette")
    _press(builder, "rotate")
    eq(builder.orientation, 1, "rotate")
    builder.cell = Vector2i(1, 2)
    _press(builder, "demolish")
    eq(gs.tile_at(Vector2i(1, 2)), "", "demolish removes the tile")
    # crane arming
    gs.place_tile(Vector2i(1, 1), "crane_pad")
    builder.arm_equipment("mc1")
    builder.cell = Vector2i(1, 1)
    _press(builder, "build")
    eq(gs.equipment_placed.size(), 1, "equipment placed on pad via builder")
    # speed / focus / ghost keys on main
    _press(main, "speed_2")
    eq(gs.speed, 2, "speed_2")
    _press(main, "speed_pause")
    eq(gs.speed, 0, "pause")
    _press(main, "speed_pause")
    eq(gs.speed, 2, "resume at last speed")
    _press(main, "speed_pause")
    _press(main, "storey_up")
    eq(gs.focus_storey_index, 1, "PgUp focuses storey 1")
    _press(main, "storey_up")
    eq(gs.focus_storey_index, 1, "clamped at top storey")
    _press(main, "storey_down")
    eq(gs.focus_storey_index, 0, "PgDn")
    _press(main, "toggle_ghost")
    ok(not (main.get("bim_view") as BimView).show_ghost, "ghost toggled")
    # assign mode: select crew, click zone
    var id: int = gs.hire("concrete")
    var crew_panel: CrewPanel = main.get("crew_panel")
    crew_panel.refresh()
    crew_panel.select_crew(id)
    eq(main.get("mode"), 1, "selecting a crew switches to assign mode")
    main.call("_on_zone_clicked", "L00-Z1")
    eq(str(gs.crew_by_id(id)["zone_id"]), "L00-Z1", "clicking a zone assigns the selected crew")
    # time passes through main._process at speed 1
    gs.scenario.events.clear()
    gs.speed = 1
    main.call("_process", 10.0)
    eq(gs.week, 1, "main advances a week when the timer elapses")
    # save / load keys
    _press(main, "save")
    ok(SaveGame.has_save(gs.scenario.id), "F1 wrote a save")
    gs.advance_week()
    _press(main, "load")
    eq(gs.week, 1, "F2 restored week 1")
    # export
    main.call("_export")
    ok(FileAccess.file_exists(PlanExport.user_json_path(gs.scenario.id)), "F5 export wrote user file")
    DirAccess.remove_absolute(ProjectSettings.globalize_path(PlanExport.user_json_path(gs.scenario.id)))
    DirAccess.remove_absolute(ProjectSettings.globalize_path(PlanExport.user_csv_path(gs.scenario.id)))
    DirAccess.remove_absolute(ProjectSettings.globalize_path(SaveGame.path_for(gs.scenario.id)))
    _drop_main(main)


func test_end_of_level_report_and_events_ui() -> void:
    var main: Node = _make_main()
    var gs: SimState = main.get("gs")
    var report: Report = main.get("report")
    var toast: EventToast = main.get("toast")
    var ev: EventDef = EventDef.from_dict({"id": "rfi", "name": "RFI", "text": "Steel RFI", "weight": 1, "effect": {},
        "choices": [{"label": "Pay", "effect": {"cash_delta": -500}}, {"label": "Wait", "effect": {}}]})
    Events.fire(gs, ev)
    ok(toast.visible, "event toast shown")
    eq(toast._buttons.get_child_count(), 2, "one button per choice")
    eq(gs.speed, 0, "choice events pause the game")
    var c0: float = gs.cash
    toast._choose(0)
    near(gs.cash, c0 - 500.0, "button resolves the choice")
    ok(not toast.visible, "toast hidden after choosing")
    gs.finish_level(true, "test")
    ok(report._final_panel.visible, "final report shown at level end")
    ok(report._final_box.get_child_count() >= 5, "score breakdown rows")
    _drop_main(main)
