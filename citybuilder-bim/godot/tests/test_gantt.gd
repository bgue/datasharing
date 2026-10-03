extends TC
## Gantt / timeline panel (docs/05 section 5): model, filters, styles, window math, hit testing,
## collapse / expand, actions, visibility wiring, lane view and the industrial_standard load.

const IND_PATH: String = "res://scenarios/industrial_standard/sequence.json"


func _card() -> SequenceCardData:
    return SequenceCardData.from_dict({"id": "card_test", "name": "Test card", "auto_staff": "ideal", "stations": [
        {"name": "Foundations", "select": {"phase": "substructure"}, "takt_weeks": 1},
        {"name": "Frame", "select": {"phase": "superstructure"}, "takt_weeks": 1},
        {"name": "MEP", "select": {"phase": "mep_roughin"}, "takt_weeks": 2}]})


func _state() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.bundle.cards_by_id["card_test"] = _card()
    gs.bundle.card_ids.append("card_test")
    return gs


func _renderer(gs: SimState, opts: Dictionary = {}) -> GanttRenderer:
    var r := GanttRenderer.new()
    r.gs = gs
    r.size = Vector2(900, 400)
    r.set_model(GanttModel.build(gs, opts))
    return r


func _zone_rows(model: Dictionary) -> Array:
    var out: Array = []
    for r in model["rows"]:
        if r["kind"] == "zone":
            out.append(r)
    return out


func _bar_count(model: Dictionary) -> int:
    var n: int = 0
    for r in _zone_rows(model):
        n += (r["bars"] as Array).size()
    return n


func _bar_of(model: Dictionary, package_id: String) -> Dictionary:
    for r in _zone_rows(model):
        for b in r["bars"]:
            if b["package_id"] == package_id:
                return b
    return {}


# ------------------------------------------------------------------ model

func test_model_rows_and_planned_spans() -> void:
    var gs: SimState = _state()
    var m: Dictionary = GanttModel.build(gs)
    eq(_zone_rows(m).size(), gs.bundle.zones.size(), "one row per zone")
    var groups: int = 0
    for r in m["rows"]:
        if r["kind"] == "group":
            groups += 1
    eq(groups, gs.bundle.storeys.size(), "one group header per storey")
    eq((m["rows"] as Array)[0]["kind"], "group", "storey header first")
    eq(_bar_count(m), gs.bundle.packages.size(), "one bar per package")
    for p in gs.bundle.packages:
        var b: Dictionary = _bar_of(m, p.package_id)
        ok(not b.is_empty(), "bar for %s" % p.package_id)
        eq(b["planned_start_day"], p.planned_start_day, "%s planned start" % p.package_id)
        eq(b["planned_finish_day"], p.planned_finish_day, "%s planned finish" % p.package_id)
        eq(b["zone_id"], p.zone_id, "%s zone" % p.package_id)
        eq(b["span_start"], p.planned_start_day, "%s unstarted bar spans the plan" % p.package_id)
        eq(b["span_end"], maxi(p.planned_finish_day, p.planned_start_day + 1), "%s span end" % p.package_id)
    # same data as the API view
    var api: Dictionary = ApiViews.gantt(gs)
    eq((api["bars"] as Array).size(), _bar_count(m), "model reuses ApiViews.gantt bars")
    # overlapping bars of a zone sit in different lanes
    for r in _zone_rows(m):
        var bars: Array = r["bars"]
        for i in bars.size():
            for j in range(i + 1, bars.size()):
                var a: Dictionary = bars[i]
                var c: Dictionary = bars[j]
                if int(a["lane"]) == int(c["lane"]):
                    var disjoint: bool = int(a["span_end"]) <= int(c["span_start"]) or int(c["span_end"]) <= int(a["span_start"])
                    ok(disjoint, "bars in one lane do not overlap")
    gs.free()


func test_span_follows_progress() -> void:
    var gs: SimState = _state()
    gs.scenario.events.clear()
    var pid: String = "P00001"
    for t in gs.bundle.packages_by_id[pid].tasks:
        set_finished(gs, t.task_id)
    Packages.refresh_states(gs)
    var m: Dictionary = GanttModel.build(gs)
    var b: Dictionary = _bar_of(m, pid)
    eq(b["state"], "done", "done package")
    near(float(b["progress"]), 1.0, "full progress")
    eq(b["span_start"], 0, "starts at its actual start")
    eq(b["span_end"], 1, "actual finish day 0 -> end exclusive 1")
    gs.free()


func test_filters_reduce_rows_and_bars() -> void:
    var gs: SimState = _state()
    var all: Dictionary = GanttModel.build(gs)
    var s0: Dictionary = GanttModel.build(gs, {"storey_id": "L00"})
    eq(_zone_rows(s0).size(), 1, "storey filter keeps the zones of that storey")
    ok(_bar_count(s0) < _bar_count(all), "storey filter reduces bars")
    for r in _zone_rows(s0):
        eq(r["storey_id"], "L00", "only L00 rows")
    var d: Dictionary = GanttModel.build(gs, {"discipline": "mechanical"})
    ok(_bar_count(d) > 0 and _bar_count(d) < _bar_count(all), "discipline filter reduces bars")
    for r in _zone_rows(d):
        for b in r["bars"]:
            eq(b["discipline"], "mechanical", "only mechanical bars")
    ok(_zone_rows(d).size() < _zone_rows(all).size(), "zones without a matching package are dropped")
    var none: Dictionary = GanttModel.build(gs, {"discipline": "nonexistent"})
    eq((none["rows"] as Array).size(), 0, "no rows when nothing matches")
    var only: Dictionary = GanttModel.build(gs, {"zone_ids": ["L01-Z1"], "group": false})
    eq((only["rows"] as Array).size(), 1, "single zone, no group header")
    eq(only["rows"][0]["zone_id"], "L01-Z1", "the requested zone")
    gs.free()


func test_my_crews_filter() -> void:
    var gs: SimState = _state()
    var mine: Dictionary = GanttModel.build(gs, {"mine": true})
    eq(_bar_count(mine), 0, "no crews, no bars")
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 5
    var id: int = gs.hire("concrete")
    ok(id >= 0 and gs.assign_crew(id, "L00-Z1"), "crew hired and assigned")
    mine = GanttModel.build(gs, {"mine": true})
    ok(_bar_count(mine) > 0, "bars of the crew's trade in its zone")
    for r in _zone_rows(mine):
        eq(r["zone_id"], "L00-Z1", "only the crew's zone")
        for b in r["bars"]:
            eq(b["trade"], "concrete", "only the crew's trade")
    gs.free()


func test_markers_and_stations() -> void:
    var gs: SimState = _state()
    # a delivery and an incident in L00-Z1
    var task: TaskData = gs.bundle.tasks_by_id["T000010"]
    task.lead_time_weeks = 2
    var rt: TaskRuntime = gs.runtime["T000010"]
    rt.ordered = true
    rt.delivery_week = 3
    gs.incident_log.append({"week": 1, "zone_id": "L00-Z1"})
    ok(Cards.apply(gs, "L00-Z1", "card_test"), "card applied")
    var m: Dictionary = GanttModel.build(gs)
    var row: Dictionary = _zone_rows(m)[0]
    eq(row["deliveries"], [15], "delivery diamond at week 3 = day 15")
    eq(row["incidents"], [7], "incident tick in week 1")
    eq(row["card_id"], "card_test", "row knows its card")
    var st: Array = row["stations"]
    eq(st.size(), 3, "three station brackets")
    eq(st[0]["name"], "Foundations", "first station")
    ok(st[0]["current"], "first station is current")
    eq(st[0]["start_day"], 0, "current station starts at its start week")
    eq(st[0]["end_day"], 5, "takt of one week")
    eq(st[1]["start_day"], 5, "next station follows")
    eq(st[2]["end_day"], 20, "MEP takes two weeks")
    var other: Dictionary = _zone_rows(m)[1]
    eq((other["stations"] as Array).size(), 0, "no brackets without a card")
    var held: Dictionary = _bar_of(m, "P00002")
    eq(held["station"], "Frame", "bar carries its station name")
    ok(held["held"], "later station is held")
    gs.free()


# ------------------------------------------------------------------ styles

func test_style_for_flags() -> void:
    var plain: Dictionary = GanttRenderer.style_for({"discipline": "mechanical", "progress": 0.4, "state": "active"})
    ok(not plain["hatched"], "not hatched")
    near(float(plain["outline_width"]), 0.0, "no outline")
    near(float(plain["fill"]), 0.4, "fill = progress")
    eq((plain["fill_color"] as Color).to_html(false), GanttRenderer.discipline_color("mechanical").to_html(false), "discipline colour")
    var held: Dictionary = GanttRenderer.style_for({"discipline": "mechanical", "progress": 0.0, "state": "held", "held": true})
    ok(held["hatched"], "held bars are hatched")
    var under: Dictionary = GanttRenderer.style_for({"discipline": "mechanical", "progress": 0.5, "state": "understaffed", "understaffed": true})
    ok(float(under["outline_width"]) > 0.0, "understaffed gets an outline")
    ok((under["outline_color"] as Color).r > 0.9 and (under["outline_color"] as Color).g < 0.4, "outline is red")
    ok(not under["hatched"], "understaffed is not hatched")
    var behind: Dictionary = GanttRenderer.style_for({"discipline": "mechanical", "progress": 0.5, "state": "active", "behind_takt": true})
    ok(behind["behind_takt"], "flag kept")
    var f: Color = behind["fill_color"]
    ok(f.r > 0.9 and f.g > 0.6 and f.g < 0.85 and f.b < 0.3, "behind takt fills amber")
    ok((GanttRenderer.style_for({"discipline": "civil", "state": "done", "progress": 1.0})["fill_color"] as Color).a < 1.0, "done bars are dimmed")
    var unknown: Dictionary = GanttRenderer.style_for({"discipline": "weird"})
    eq((unknown["fill_color"] as Color).to_html(false), GanttRenderer.discipline_color("general").to_html(false), "unknown discipline falls back to general")


func test_style_from_real_flags() -> void:
    var gs: SimState = _state()
    gs.hold_package("P00002")
    var m: Dictionary = GanttModel.build(gs)
    var b: Dictionary = _bar_of(m, "P00002")
    ok(b["held"], "held flag from the sim")
    ok(GanttRenderer.style_for(b)["hatched"], "and the style hatches it")
    gs.free()


# ------------------------------------------------------------------ window and hit testing

func test_zoom_window_math() -> void:
    var gs: SimState = _state()
    var r: GanttRenderer = _renderer(gs)
    eq(r.zoom_weeks, 26, "default zoom")
    near(r.from_day, 0.0, "week 0: window starts at 0")
    for w in [12, 26, 52]:
        r.set_zoom(w)
        near(r.window_days(), float(w * 5), "%d weeks = %d days" % [w, w * 5])
        near(r.to_day() - r.from_day, float(w * 5), "window width")
        for d in [0.0, 7.0, 33.5, 120.0]:
            near(r.x_to_day(r.day_to_x(d)), d, "day -> x -> day round trip (%d wk, day %s)" % [w, str(d)], 0.0001)
        near(r.day_to_x(r.from_day), r.label_w, "window start at the label column")
        near(r.day_to_x(r.to_day()), r.size.x - GanttRenderer.SB_W, "window end at the right edge", 0.01)
    r.set_zoom(7)
    eq(r.zoom_weeks, 52, "invalid zoom ignored")
    gs.week = 10
    r.set_model(GanttModel.build(gs))
    near(r.from_day, 40.0, "window starts at max(0, week - 2) * 5")
    r.scroll_days(-100.0)
    near(r.from_day, 0.0, "scrolling clamps at 0")
    ok(not r.follow, "scrolling stops following the week")
    gs.week = 12
    r.set_model(GanttModel.build(gs))
    near(r.from_day, 0.0, "stays where the user put it")
    r.set_zoom(12)
    near(r.from_day, 50.0, "zoom re-follows the current week")
    r.free()
    gs.free()


func test_hit_testing() -> void:
    var gs: SimState = _state()
    var r: GanttRenderer = _renderer(gs)
    var m: Dictionary = GanttModel.build(gs)
    for p in gs.bundle.packages:
        var rc: Rect2 = r.bar_rect(p.package_id)
        ok(rc.size.x > 0.0, "%s has a rectangle" % p.package_id)
        var b: Dictionary = r.bar_at(rc.get_center())
        eq(str(b.get("package_id", "")), p.package_id, "centre of %s hits it" % p.package_id)
        eq(str(b.get("zone_id", "")), p.zone_id, "hit carries the zone")
    eq(r.bar_at(Vector2(r.label_w + 2.0, 1.0)), {}, "axis band hits nothing")
    eq(r.bar_at(Vector2(10, 100)), {}, "label column hits no bar")
    eq(r.bar_at(Vector2(880, 390)), {}, "empty space hits nothing")
    var rc2: Rect2 = r.bar_rect("P00001")
    eq(r.row_at(rc2.get_center())["zone_id"], "L00-Z1", "row_at finds the zone row")
    # click a bar: zone_selected(zone)
    var got: Array[String] = []
    r.zone_selected.connect(func(z: String) -> void: got.append(z))
    r.click_at(rc2.get_center())
    eq(got, ["L00-Z1"] as Array[String], "clicking a bar selects its zone")
    eq(r.selected_zone_id, "L00-Z1", "renderer highlights it")
    ok(m["rows"].size() > 0, "model sanity")
    r.free()
    gs.free()


func test_tooltip_text() -> void:
    var gs: SimState = _state()
    gs.hold_package("P00002")
    var r: GanttRenderer = _renderer(gs)
    var pt: Vector2 = r.bar_rect("P00002").get_center()
    var tip: String = r.tooltip_for(pt)
    ok(tip.contains("State: held"), "tooltip shows the state: " + tip)
    ok(tip.contains("Crews: now 0 / ideal"), "tooltip shows crews now / ideal")
    ok(tip.contains("max"), "and max")
    ok(tip.contains("crew-days"), "remaining crew-days")
    ok(tip.contains("Plan: day"), "plan span")
    var p: PackageData = gs.bundle.packages_by_id["P00002"]
    ok(tip.contains(p.name), "package name")
    Cards.apply(gs, "L00-Z1", "card_test")
    r.set_model(GanttModel.build(gs))
    tip = r.tooltip_for(r.bar_rect("P00002").get_center())
    ok(tip.contains("Station: Frame"), "station in the tooltip: " + tip)
    ok(r.tooltip_for(Vector2(10, 5)) == "", "no tooltip on the axis")
    var group_tip: String = r.tooltip_for(Vector2(10, r.axis_h + 5.0))
    ok(group_tip.contains("zones"), "group header tooltip")
    var blocked: Dictionary = {"name": "x", "state": "waiting", "blocked_reason": "needs 2 crews", "planned_start_day": 0,
            "planned_finish_day": 3, "behind_takt": true}
    var t2: String = GanttRenderer.tooltip_text(blocked)
    ok(t2.contains("needs 2 crews") and t2.contains("behind takt"), "blocked reason and behind takt")
    r.free()
    gs.free()


func test_collapse_and_expand() -> void:
    var gs: SimState = _state()
    var r: GanttRenderer = _renderer(gs)
    var total: int = r.display_rows().size()
    eq(total, 4, "2 storey headers + 2 zones")
    r.toggle_collapsed("L00")
    ok(r.is_collapsed("L00"), "collapsed")
    eq(r.display_rows().size(), 3, "its zone row is hidden, the header stays")
    eq(r.bar_rect("P00001"), Rect2(), "collapsed bars have no rectangle")
    # clicking the header (first row, below the axis) toggles it back
    r.click_at(Vector2(30, r.axis_h + 5.0))
    ok(not r.is_collapsed("L00"), "clicking the header expands again")
    eq(r.display_rows().size(), total, "rows are back")
    # double click a zone row: task level for that zone only
    var invalidated: Array[int] = [0]
    r.model_invalidated.connect(func() -> void: invalidated[0] += 1)
    var pt: Vector2 = r.bar_rect("P00001").get_center()
    r.click_at(pt, true)
    eq(invalidated[0], 1, "double click asks for a rebuild")
    ok(r.expanded.has("L00-Z1"), "zone expanded")
    r.set_model(GanttModel.build(gs, {"expanded": r.expanded}))
    var tasks: int = 0
    for row in r.display_rows():
        if row["kind"] == "task":
            tasks += 1
            eq(row["zone_id"], "L00-Z1", "task rows only for the expanded zone")
            eq((row["bars"] as Array).size(), 1, "one bar per task row")
    eq(tasks, (gs.bundle.tasks_by_zone["L00-Z1"] as Array).size(), "one sub-row per task of the zone")
    # second double click collapses
    r.click_at(r.bar_rect("P00001").get_center(), true)
    ok(not r.expanded.has("L00-Z1"), "double click again collapses")
    r.set_model(GanttModel.build(gs, {"expanded": r.expanded}))
    for row in r.display_rows():
        ok(row["kind"] != "task", "no task rows left")
    # lane view cannot expand
    r.allow_expand = false
    r.toggle_expanded("L00-Z1")
    ok(r.expanded.is_empty(), "allow_expand = false")
    r.free()
    gs.free()


func test_task_rows_state() -> void:
    var gs: SimState = _state()
    gs.set_task_state("T000001", TaskRuntime.State.ACTIVE)
    var rt: TaskRuntime = gs.runtime["T000001"]
    rt.progress = rt.required * 0.5
    rt.actual_start_day = 0
    var rows: Array = GanttModel.task_rows(gs, "L00-Z1", 2)
    var b: Dictionary = (rows[0]["bars"] as Array)[0]
    eq(rows[0]["task_id"], "T000001", "sorted by planned start")
    eq(b["state"], "active", "task state")
    near(float(b["progress"]), 0.5, "task progress")
    ok(int(b["span_end"]) >= 2, "an active task runs up to now")
    ok(GanttRenderer.tooltip_text(b).contains("State: active"), "task tooltip")
    gs.free()


# ------------------------------------------------------------------ actions

func test_context_actions_call_the_sim() -> void:
    var gs: SimState = _state()
    var r: GanttRenderer = _renderer(gs)
    var inv: Array[int] = [0]
    r.model_invalidated.connect(func() -> void: inv[0] += 1)
    ok(r.toggle_hold("P00001"), "hold")
    eq(gs.package_runtime["P00001"].state, "held", "package held")
    ok(r.toggle_hold("P00001"), "release")
    ok(gs.package_runtime["P00001"].released, "package released again")
    ok(r.set_priority("P00001", 42), "priority")
    eq(gs.package_runtime["P00001"].priority, 42, "priority set")
    ok(r.apply_card("L00-Z1", "card_test"), "apply card")
    eq(gs.zone_runtime["L00-Z1"].card_id, "card_test", "card applied")
    ok(r.clear_card("L00-Z1"), "clear card")
    eq(gs.zone_runtime["L00-Z1"].card_id, "", "card cleared")
    ok(not r.apply_card("L00-Z1", "nope"), "unknown card fails")
    eq(inv[0], 6, "every action asks for a refresh")
    r.free()
    gs.free()


func test_context_menu_structure_and_dispatch() -> void:
    var gs: SimState = _state()
    var r: GanttRenderer = _renderer(gs)
    Engine.get_main_loop().root.add_child(r)
    r._ensure_menus()
    ok(r._menu.item_count >= 4, "hold, priority, apply card submenu, clear card")
    eq(r._card_menu.item_count, gs.bundle.card_list().size(), "one entry per card")
    r._ctx_bar = r.bar_at(r.bar_rect("P00001").get_center())
    eq(r._ctx_bar["package_id"], "P00001", "context bar resolved by hit test")
    r._on_menu(GanttRenderer.Menu.HOLD)
    eq(gs.package_runtime["P00001"].state, "held", "menu Hold calls hold_package")
    r._on_card_menu(0)
    eq(gs.zone_runtime["L00-Z1"].card_id, "card_test", "card submenu applies the card")
    r._on_menu(GanttRenderer.Menu.CLEAR_CARD)
    eq(gs.zone_runtime["L00-Z1"].card_id, "", "menu Clear card")
    Engine.get_main_loop().root.remove_child(r)
    r.free()
    gs.free()


# ------------------------------------------------------------------ scene wiring

func _make_main(visible_default: bool = true) -> Node:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    ok(bool(sc.call("select", MINIMAL_PATH)), "select minimal bundle")
    (sc.get("current_bundle") as SequenceBundle).scenario.gantt_visible_default = visible_default
    var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
    Engine.get_main_loop().root.add_child(main)
    return main


func _drop_main(main: Node) -> void:
    Engine.get_main_loop().root.remove_child(main)
    main.free()
    Engine.get_main_loop().root.get_node("Scenarios").set("current_bundle", null)


func _toggle_event() -> InputEventAction:
    var ev := InputEventAction.new()
    ev.action = "gantt_toggle"
    ev.pressed = true
    return ev


func test_action_is_bound_to_t() -> void:
    ok(InputMap.has_action("gantt_toggle"), "gantt_toggle action exists")
    var found: bool = false
    for e in InputMap.action_get_events("gantt_toggle"):
        if e is InputEventKey and (e as InputEventKey).physical_keycode == KEY_T:
            found = true
    ok(found, "bound to T")


func test_visibility_toggle_action_and_signal() -> void:
    var main: Node = _make_main(true)
    var g: GanttPanel = main.get("gantt")
    ok(g != null, "gantt panel built")
    ok(g.visible, "visible by default when the scenario says so")
    var dock: Control = main.get("left_dock")
    var right_dock: Control = main.get("right_dock")
    var hint: Control = main.get("hint_bar")
    var base_bottom: float = -8.0
    var ui: Control = main.get("ui_root")
    var vh: float = ui.get_viewport_rect().size.y
    ok(g.bottom_inset() > 0.0, "visible panel takes screen area")
    near(dock.offset_bottom, base_bottom - g.bottom_inset(), "left dock (crews, charts) ends above the timeline")
    near(right_dock.offset_bottom, base_bottom - g.bottom_inset(), "right dock (inspector, procurement) ends above the timeline")
    near(hint.offset_bottom, vh + base_bottom - g.bottom_inset(), "hint bar sits above the timeline")
    var cam: Camera3D = main.get("camera")
    var shifted: float = absf(cam.v_offset)
    ok(shifted > 0.0, "camera shifted to keep the model in the free area")
    main._unhandled_input(_toggle_event())
    ok(not g.visible, "T hides it")
    near(g.bottom_inset(), 0.0, "hidden panel frees the area")
    near(dock.offset_bottom, base_bottom, "left dock back at the bottom")
    near(right_dock.offset_bottom, base_bottom, "right dock back at the bottom")
    near(hint.offset_bottom, vh + base_bottom, "hint bar back at the bottom")
    var view_node: Node = main.get("view")
    ok(view_node.ui_insets.w >= hint.size.y, "camera free area ends above the hint bar")
    ok(view_node.ui_insets.y > 0.0 and view_node.ui_insets.x > 0.0, "camera free area starts below the top bar and right of the left dock")
    var top: TopBar = main.get("top_bar")
    top.gantt_toggled.emit()
    ok(g.visible, "top bar button shows it")
    top.gantt_toggled.emit()
    ok(not g.visible, "and hides it")
    g.set_open(true)
    ok(g.rebuild_count > 0, "showing rebuilds the model")
    # splitter: height fraction clamps and reflows
    g.set_height_fraction(0.9)
    near(g.height_fraction, GanttPanel.MAX_FRACTION, "splitter clamps at the maximum")
    near(g.anchor_top, 1.0 - GanttPanel.MAX_FRACTION, "anchor follows the splitter")
    g.set_height_fraction(0.3)
    _drop_main(main)


func test_hidden_by_default_when_scenario_says_so() -> void:
    var main: Node = _make_main(false)
    var g: GanttPanel = main.get("gantt")
    ok(not g.visible, "hidden when gantt_visible_default is false")
    near(g.bottom_inset(), 0.0, "no inset while hidden")
    near((main.get("left_dock") as Control).offset_bottom, -8.0, "docks at the bottom")
    main._unhandled_input(_toggle_event())
    ok(g.visible, "T shows it")
    _drop_main(main)


func test_zone_click_routes_to_inspector() -> void:
    var main: Node = _make_main(true)
    var g: GanttPanel = main.get("gantt")
    g.renderer.zone_selected.emit("L01-Z1")
    var insp: ZoneInspector = main.get("inspector")
    eq(insp.zone_id, "L01-Z1", "inspector follows the timeline selection")
    eq(main.get("pinned_zone"), "L01-Z1", "zone pinned")
    eq((main.get("gs") as SimState).focus_storey_index, 1, "storey focus follows")
    _drop_main(main)


func test_filters_through_panel_and_debounce() -> void:
    var main: Node = _make_main(true)
    var g: GanttPanel = main.get("gantt")
    var gs: SimState = main.get("gs")
    var all_rows: int = g.renderer.display_rows().size()
    g.set_storey_filter("L01")
    ok(g.renderer.display_rows().size() < all_rows, "storey filter reduces rows")
    g.set_storey_filter("")
    g.set_discipline_filter("mechanical")
    var bars: int = 0
    for row in g.renderer.display_rows():
        if row["kind"] == "zone":
            bars += (row["bars"] as Array).size()
    eq(bars, 1, "one mechanical package in minimal")
    g.set_discipline_filter("")
    g.set_mine_only(true)
    eq(g.renderer.display_rows().size(), 0, "my crews only: nothing without crews")
    g.set_mine_only(false)
    # debounce: many events -> one rebuild on the next frame
    var before: int = g.rebuild_count
    for i in 5:
        gs.crews_changed.emit()
        gs.task_state_changed.emit("T000001", 0, 1)
    eq(g.rebuild_count, before, "nothing rebuilt synchronously")
    g._process(0.0)
    eq(g.rebuild_count, before + 1, "one rebuild for the whole burst")
    _drop_main(main)


func test_lane_view_in_inspector() -> void:
    var main: Node = _make_main(true)
    var insp: ZoneInspector = main.get("inspector")
    ok(not insp._lane.visible, "no lane without a zone")
    insp.show_zone("L00-Z1")
    insp.refresh()
    ok(not insp._lane.visible, "the lane yields to the open timeline panel (it shows the same bars)")
    (main.get("gantt") as GanttPanel).set_open(false)
    insp.refresh()
    ok(insp._lane.visible, "lane shown for a zone")
    eq(insp._lane.rows.size(), 1, "a single zone row, no group header")
    eq(insp._lane.rows[0]["zone_id"], "L00-Z1", "the inspected zone")
    ok(insp._lane.fit_height and not insp._lane.show_labels, "lane configuration")
    ok(insp._lane.custom_minimum_size.y >= 60.0, "about 60 px tall")
    _drop_main(main)


# ------------------------------------------------------------------ industrial_standard

func test_industrial_model_cull_and_draw() -> void:
    var b: SequenceBundle = SequenceBundle.load_from_path(IND_PATH)
    ok(b.valid, "industrial_standard loads")
    var gs := SimState.new()
    ok(gs.start(b), "industrial_standard starts")
    gs.scenario.events.clear()
    # some state: cards on three zones, a few deliveries, an incident, some held packages
    var zones: Array = b.zones.slice(0, 3)
    var card_id: String = b.card_list()[0].id
    for z in zones:
        Cards.apply(gs, (z as ZoneData).id, card_id)
    var ordered: int = 0
    for t in b.tasks:
        if t.lead_time_weeks > 0 and ordered < 6:
            (gs.runtime[t.task_id] as TaskRuntime).ordered = true
            (gs.runtime[t.task_id] as TaskRuntime).delivery_week = 4 + ordered
            ordered += 1
    gs.incident_log.append({"week": 2, "zone_id": (zones[0] as ZoneData).id})
    for i in 5:
        gs.hold_package(b.packages[i * 7].package_id)
    gs.week = 6
    var best_build: float = 1.0e9
    var model: Dictionary = {}
    for i in 5:
        var t0: int = Time.get_ticks_usec()
        model = GanttModel.build(gs)
        best_build = minf(best_build, float(Time.get_ticks_usec() - t0) / 1000.0)
    print("      [gantt] industrial_standard: %d packages, %d tasks, model build %.2f ms (best of 5)" % [b.packages.size(), b.tasks.size(), best_build])
    eq(_bar_count(model), b.packages.size(), "a bar for each of the packages")
    eq(_zone_rows(model).size(), b.zones.size(), "a row per zone")
    ok(best_build < 20.0, "model build under 20 ms (%.2f)" % best_build)
    # culling: a 12-week window shows far fewer bars than exist
    var sv := SubViewport.new()
    sv.size = Vector2i(1280, 360)
    sv.disable_3d = true
    sv.render_target_update_mode = SubViewport.UPDATE_ALWAYS
    Engine.get_main_loop().root.add_child(sv)
    var r := GanttRenderer.new()
    r.gs = gs
    sv.add_child(r)
    r.size = Vector2(1280, 360)
    r.set_zoom(12)
    r.set_model(model)
    var visible_bars: int = r.count_visible()
    print("      [gantt] culling: %d of %d bars in the 12-week window, %d rows drawn of %d" % [visible_bars, b.packages.size(), r.drawn_rows, r.display_rows().size()])
    ok(visible_bars > 0 and visible_bars < b.packages.size(), "culling draws only the window (%d of %d)" % [visible_bars, b.packages.size()])
    ok(r.drawn_rows < r.display_rows().size(), "rows below the fold are not drawn")
    r.scroll_days(5000.0)
    eq(r.count_visible() <= visible_bars, true, "scrolling to the end keeps culling")
    r.follow = true
    r.set_zoom(52)
    r.set_model(model)
    var wide: int = r.count_visible()
    ok(wide >= visible_bars, "a wider window shows at least as many bars (%d)" % wide)
    # forced draws through the scene tree
    r.set_zoom(26)
    r.set_model(model)
    var best_draw: float = 1.0e9
    for i in 4:
        r.queue_redraw()
        await Engine.get_main_loop().process_frame
        await Engine.get_main_loop().process_frame
        if r.draw_count > 0:
            best_draw = minf(best_draw, r.draw_time_ms)
    if r.draw_count > 0:
        print("      [gantt] industrial_standard _draw: %.2f ms (best of %d draws, %d bars, %d rows)" % [best_draw, r.draw_count, r.drawn_bars, r.drawn_rows])
        ok(best_draw < 5.0, "_draw under 5 ms (%.2f)" % best_draw)
    else:
        print("      [gantt] _draw did not run headless; culling counts and model build time checked instead")
    # expanded zone and a collapsed storey must also draw
    r.expanded[(zones[0] as ZoneData).id] = true
    r.set_model(GanttModel.build(gs, {"expanded": r.expanded}))
    r.queue_redraw()
    await Engine.get_main_loop().process_frame
    ok(r.count_visible() > 0, "expanded zone counted")
    Engine.get_main_loop().root.remove_child(sv)
    sv.free()
    gs.free()
