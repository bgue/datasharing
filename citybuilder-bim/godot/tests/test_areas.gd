extends TC
## Areas (project.areas): statistics, API (state.areas, view.jump_to_area), the Areas panel with the B key, the camera
## jump and the area filter of the timeline.

const RS := TaskRuntime.State


func _areas_dict() -> Dictionary:
    var d: Dictionary = minimal_dict()
    d["project"]["areas"] = [
        {"id": "ground", "name": "Ground plant", "cells": [[2, 2], [3, 2], [2, 3], [3, 3]], "storey_ids": ["L00"]},
        {"id": "upper", "name": "Level 1 wing", "cells": [[2, 2], [3, 2], [2, 3], [3, 3]], "storey_ids": ["L01"]},
    ]
    return d


func _api(gs: SimState) -> ApiServer:
    var api := ApiServer.new()
    api.gs = gs
    api._register()
    return api


func _call(api: ApiServer, m: String, p: Dictionary = {}) -> Variant:
    return (api._methods[m] as Callable).call(p)


func test_area_statistics_follow_the_simulation() -> void:
    var gs: SimState = state_from_dict(_areas_dict())
    var rows: Array[Dictionary] = Areas.list(gs)
    eq(rows.size(), 2, "two areas")
    var g: Dictionary = rows[0]
    eq(str(g["id"]), "ground", "id")
    eq(g["zone_ids"], ["L00-Z1"] as Array, "zone of the area")
    eq(int(g["cell_count"]), 4, "cell count")
    eq(g["bounds"], {"x": 2, "z": 2, "w": 2, "d": 2}, "bounds")
    ok(int(g["tasks"]) > 0, "tasks counted")
    near(float(g["done_share"]), 0.0, "nothing done yet")
    ok(not g.has("cells"), "cells only on request")
    set_finished(gs, "T000001")
    set_finished(gs, "T000005")
    var after: Dictionary = Areas.list(gs, true)[0]
    ok(float(after["done_share"]) > 0.0, "done share rises (%f)" % float(after["done_share"]))
    eq(int(after["tasks_done"]), 2, "two tasks done")
    eq((after["cells"] as Array).size(), 4, "cells on request")
    gs.free()


func test_targets_for_area_and_whole_site() -> void:
    var gs: SimState = state_from_dict(_areas_dict())
    var site: Dictionary = Areas.target(gs, "")
    ok(bool(site["site"]) and str(site["id"]) == Areas.SITE_ID, "empty id is the whole site")
    ok(bool(Areas.target(gs, "site")["site"]), "'site' too")
    var up: Dictionary = Areas.target(gs, "upper")
    eq(int(up["storey_index"]), 1, "the area of level 1 focuses storey 1")
    ok(Areas.target(gs, "nope").is_empty(), "unknown area")
    gs.free()


func test_api_state_areas_and_jump() -> void:
    var gs: SimState = state_from_dict(_areas_dict())
    var api: ApiServer = _api(gs)
    var rows: Variant = _call(api, "state.areas")
    ok(rows is Array and (rows as Array).size() == 2, "state.areas lists the areas")
    var first: Dictionary = (rows as Array)[0]
    for k in ["id", "name", "zone_ids", "storey_ids", "tasks", "done_share"]:
        ok(first.has(k), "row has %s" % k)
    var with_cells: Array = _call(api, "state.areas", {"with_cells": true})
    ok((with_cells[0] as Dictionary).has("cells"), "with_cells")
    var r: Variant = _call(api, "view.jump_to_area", {"id": "ground"})
    ok(r is Dictionary and not (r as Dictionary).has("__error"), "jump to a known area answers")
    eq(bool(r["framed"]), false, "nothing to move without a view")
    var bad: Variant = _call(api, "view.jump_to_area", {"id": "nope"})
    ok((bad as Dictionary).has("__error"), "unknown area is an error")
    var site: Variant = _call(api, "view.jump_to_area", {"id": "site"})
    eq(str(site["id"]), "site", "the whole site is a valid target")
    var zones: Array = _call(api, "state.zones", {"area_id": "upper"})
    eq(zones.size(), 1, "state.zones filters by area")
    eq(str(zones[0]["zone_id"]), "L01-Z1", "the zone of the upper area")
    var g: Dictionary = _call(api, "state.gantt", {"area_id": "ground"})
    for b in g["bars"]:
        eq(str(b["zone_id"]), "L00-Z1", "bars of the area only")
    ok((g["bars"] as Array).size() > 0, "and there are some")
    var none: Dictionary = _call(api, "state.gantt", {"area_id": "nope"})
    eq((none["bars"] as Array).size(), 0, "an unknown area has no bars")
    ok(api.method_names().has("state.areas") and api.method_names().has("view.jump_to_area"), "methods listed")
    api.free()
    gs.free()


func test_gantt_model_area_filter() -> void:
    var gs: SimState = state_from_dict(_areas_dict())
    var all: Dictionary = GanttModel.build(gs, {})
    var one: Dictionary = GanttModel.build(gs, {"area_id": "upper"})
    var zones: Array[String] = []
    for r in one["rows"]:
        if str(r["kind"]) == "zone":
            zones.append(str(r["zone_id"]))
    eq(zones, ["L01-Z1"] as Array[String], "only the zone of the area")
    ok((all["rows"] as Array).size() > (one["rows"] as Array).size(), "fewer rows than the whole site")
    var nothing: Dictionary = GanttModel.build(gs, {"area_id": "nope"})
    eq((nothing["rows"] as Array).size(), 0, "unknown area: no rows")
    gs.free()


func _main_with(d: Dictionary) -> Node:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    sc.set("current_bundle", SequenceBundle.from_dictionary(d))
    var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
    Engine.get_main_loop().root.add_child(main)
    return main


func _drop_main(main: Node) -> void:
    Engine.get_main_loop().root.remove_child(main)
    main.free()
    Engine.get_main_loop().root.get_node("Scenarios").set("current_bundle", null)


func test_areas_panel_toggle_list_and_jump() -> void:
    var main: Node = _main_with(_areas_dict())
    var gs: SimState = main.get("gs")
    var panel: AreasPanel = main.get("areas_panel")
    ok(panel != null and not panel.visible, "panel exists and starts hidden")
    var ev := InputEventAction.new()
    ev.action = "areas_toggle"
    ev.pressed = true
    main._unhandled_input(ev)
    ok(panel.visible, "the B action shows it")
    var rows: Array[Dictionary] = panel.entries()
    eq(rows.size(), 3, "whole site plus two areas")
    eq(str(rows[0]["id"]), "site", "whole site first")
    ok(panel.jump_to("upper"), "jump to the upper area")
    eq(gs.focus_storey_index, 1, "storey focus moved to level 1")
    var view: Node3D = main.get("view")
    near((view.get("camera_position") as Vector3).x, 2.5, "camera centred on the area (x)")
    near((view.get("camera_position") as Vector3).z, 2.5, "camera centred on the area (z)")
    eq(panel.selected, "upper", "the row is marked")
    ok(panel.jump_to("site"), "whole site")
    near((view.get("camera_position") as Vector3).x, float(gs.bundle.site_rect.position.x) + float(gs.bundle.site_rect.size.x - 1) * 0.5, "framed on the site rectangle")
    ok(not panel.jump_to("nope"), "unknown area")
    var api := ApiServer.new()
    api.gs = gs
    api._register()
    api.area_handler = Callable(panel, "jump_to")
    var r: Variant = _call(api, "view.jump_to_area", {"id": "ground"})
    ok(bool(r["framed"]), "through the API the camera moves")
    eq(gs.focus_storey_index, 0, "and the focus follows")
    api.free()
    main._unhandled_input(ev)
    ok(not panel.visible, "B again hides it")
    _drop_main(main)


func test_gantt_panel_shows_the_area_filter_only_when_areas_exist() -> void:
    var main: Node = _main_with(_areas_dict())
    var gantt: GanttPanel = main.get("gantt")
    ok(gantt._area_opt.visible, "area filter present")
    gantt.set_area_filter("upper")
    eq(gantt.area_filter, "upper", "filter set")
    var zone_rows: int = 0
    for r in gantt.renderer.rows:
        if str(r["kind"]) == "zone":
            zone_rows += 1
    eq(zone_rows, 1, "the timeline shows the zones of the area only")
    _drop_main(main)
    var plain: PackedScene = load("res://scenes/main.tscn")  # minimal bundle without areas
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    ok(bool(sc.call("select", MINIMAL_PATH)), "minimal selected")
    var m2: Node = plain.instantiate()
    Engine.get_main_loop().root.add_child(m2)
    ok(not (m2.get("gantt") as GanttPanel)._area_opt.visible, "no areas: no area filter")
    eq((m2.get("areas_panel") as AreasPanel).entries().size(), 1, "the panel lists only the whole site")
    _drop_main(m2)
