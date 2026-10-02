extends TC
## Virtual tasks (docs/06 A.3): parsing, duration-driven progress, markers, gates / predecessors, no element visuals.

const RS := TaskRuntime.State


func _virtual_dict(id: String, step: String, phase: String, trade: String, duration: int, preds: Array = [], marker: Variant = "survey") -> Dictionary:
    return {
        "task_id": id, "element_guid": null, "ifc_class": "", "element_name": "Virtual %s" % step, "storey_id": "L00",
        "zone_id": "L00-Z1", "system_id": null, "step_id": step, "phase": phase, "trade": trade, "quantity": 50,
        "unit": "ea", "estimated_crew_days": 50, "cost": 100.0, "cells": [[2, 2]],
        "flags": {"requires_crane": false, "requires_access": false, "inspection": false, "inspection_type": null,
                "lead_time_weeks": 0, "laydown_cells": 0},
        "predecessors": preds, "rule_id": "R-recipe", "planned_start_day": 0, "planned_finish_day": duration,
        "virtual": true, "origin": "recipe", "recipe_id": "rec_test", "duration_days": duration, "marker": marker,
        "manual_id": null,
    }


func _state(extra: Array, mutate: Callable = Callable()) -> SimState:
    var d: Dictionary = fixture_dict()
    for t in extra:
        (d["tasks"] as Array).append(t)
    if mutate.is_valid():
        mutate.call(d)
    return state_from_dict(d)


func _staff(gs: SimState, trade: String, zone: String, n: int = 1) -> void:
    for i in n:
        gs.hired_this_week.clear()  # the weekly hiring cap is not what these tests are about
        var id: int = gs.hire(trade)
        ok(id > 0, "hired %s: %s" % [trade, gs.last_error])
        gs.assign_crew(id, zone)


func test_parse_virtual_fields() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 3)])
    var t: TaskData = gs.bundle.tasks_by_id["T000013"]
    ok(t.is_virtual, "virtual flag")
    eq(t.element_guid, "", "null element_guid parses to empty")
    eq(t.origin, "recipe", "origin")
    eq(t.recipe_id, "rec_test", "recipe id")
    eq(t.duration_days, 3, "duration days")
    eq(t.marker, "survey", "marker")
    eq(t.manual_id, "", "null manual id")
    near(t.estimated_crew_days, 3.0, "duration drives the effort, not the quantity")
    ok(not gs.bundle.tasks_by_element.has(""), "virtual tasks are not indexed by element")
    eq(gs.bundle.virtual_tasks.size(), 1, "virtual task list")
    var rule: TaskData = gs.bundle.tasks_by_id["T000001"]
    ok(not rule.is_virtual and rule.origin == "rule" and rule.duration_days == 0, "generated tasks keep the defaults")
    var d: Dictionary = t.export_dict()
    ok(d["element_guid"] == null and d["virtual"] == true and d["origin"] == "recipe", "export keeps virtual fields")
    eq(d["duration_days"], 3, "export duration")
    var c: Dictionary = gs.virtual_task_counts()
    eq(c["total"], 1, "virtual count")


func test_duration_task_advances_one_day_per_working_day() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 3)])
    _staff(gs, "finishes", "L00-Z1")  # no haul road: requires_access is false
    var rt: TaskRuntime = gs.runtime["T000013"]
    gs.refresh_states()
    eq(rt.state, RS.READY, "virtual task ready from the start (no road needed)")
    var days: Array[float] = []
    gs.before_work_day = func(_d: int) -> void: days.append(rt.progress)
    ok(gs.advance_week(), "week advances")
    eq(days.size(), 5, "five working days")
    near(days[0], 0.0, "no progress before day 0")
    near(days[1], 1.0, "one day of progress after day 0")
    near(days[2], 2.0, "two days after day 1")
    ok(TaskRuntime.is_finished(rt.state), "finished within the week: %s" % TaskRuntime.state_name(rt.state))
    eq(rt.actual_start_day, 0, "started on day 0")
    eq(rt.actual_finish_day, 3, "finished after exactly 3 working days (learning factor ignored)")


func test_duration_task_needs_exactly_one_crew() -> void:
    var d: Dictionary = fixture_dict()
    (d["scenario"]["crews_available"] as Dictionary)["finishes"] = 2  # the zone takes two crews without congestion
    (d["tasks"] as Array).append(_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 4))
    var gs: SimState = state_from_dict(d)
    _staff(gs, "finishes", "L00-Z1", 2)
    var pkg: PackageData = gs.bundle.package_of(gs.bundle.tasks_by_id["T000013"])
    eq(pkg.crew_min, 1, "duration package min crews")
    eq(pkg.crew_ideal, 1, "duration package ideal crews")
    eq(pkg.crew_max, 1, "duration package max crews")
    ok(gs.advance_week(), "week advances")
    var rt: TaskRuntime = gs.runtime["T000013"]
    eq(rt.actual_finish_day, 4, "two crews do not speed a 4 day task up")
    var res: Dictionary = Planner.staff_zone(gs, "L00-Z1", "max", false)
    ok(res.has("moved"), "zone.staff runs on a zone with virtual work")


func test_quantity_does_not_change_duration() -> void:
    var a: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2)])
    var big: Dictionary = _virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2)
    big["quantity"] = 5000
    big["estimated_crew_days"] = 5000
    var b: SimState = _state([big])
    for gs in [a, b]:
        _staff(gs, "finishes", "L00-Z1")
        (gs as SimState).advance_week()
        eq((gs.runtime["T000013"] as TaskRuntime).actual_finish_day, 2, "2 days whatever the quantity")


func test_requires_access_flag_is_respected() -> void:
    var vd: Dictionary = _virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2)
    vd["flags"]["requires_access"] = true
    var gs: SimState = _state([vd])
    _staff(gs, "finishes", "L00-Z1")
    gs.refresh_states()
    var rt: TaskRuntime = gs.runtime["T000013"]
    eq(rt.state, RS.READY, "ready but impeded")
    ok(rt.blocked_reason.contains("access"), "impediment: %s" % rt.blocked_reason)
    gs.advance_week()
    eq(rt.actual_start_day, -1, "does not start without access")
    build_road(gs)
    gs.advance_week()
    ok(TaskRuntime.is_finished(rt.state), "works once the road exists")


func test_counts_for_predecessors() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2)], func(d: Dictionary) -> void:
        for t in d["tasks"]:
            if str((t as Dictionary)["task_id"]) == "T000001":
                ((t as Dictionary)["predecessors"] as Array).append({"task_id": "T000013", "type": "FS", "lag_days": 0}))
    eq(state_of(gs, "T000001"), RS.BLOCKED, "footing waits for the virtual survey")
    ok(gs.runtime["T000001"].blocked_reason.begins_with("Waiting for"), "reason: %s" % gs.runtime["T000001"].blocked_reason)
    set_finished(gs, "T000013")
    gs.refresh_states()
    eq(state_of(gs, "T000001"), RS.READY, "footing ready once the virtual task is done")
    ok((gs.bundle.successors_by_task["T000013"] as Array).has("T000001"), "successor index covers the virtual task")


func test_counts_for_gates() -> void:
    # a virtual superstructure task in storey L00 holds the mep_roughin duct through gate G-struct
    var gs: SimState = _state([_virtual_dict("T000014", "GEN-LIFT-STUDY", "superstructure", "finishes", 2, [], "lift_plan")])
    for id in ["T000001", "T000002", "T000003", "T000004", "T000005", "T000006", "T000007", "T000008", "T000009"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.BLOCKED, "duct blocked while the virtual task is open")
    ok(gs.runtime["T000010"].blocked_reason.begins_with("Gate"), "by the gate: %s" % gs.runtime["T000010"].blocked_reason)
    set_finished(gs, "T000014")
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.READY, "duct released when the virtual task is done")


func test_counts_for_packages_and_completion() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 1)])
    var t: TaskData = gs.bundle.tasks_by_id["T000013"]
    var pkg: PackageData = gs.bundle.package_of(t)
    ok(pkg != null and pkg.task_ids.has("T000013"), "virtual task belongs to a package")
    eq(pkg.trade, "finishes", "package trade")
    ok(not gs.all_tasks_finished(), "level not finished with open virtual work")
    for tk in gs.bundle.tasks:
        if tk.task_id != "T000013":
            set_finished(gs, tk.task_id)
    ok(not gs.all_tasks_finished(), "still one virtual task open")
    set_finished(gs, "T000013")
    ok(gs.all_tasks_finished(), "virtual task counts for completion")


func test_markers_exposed() -> void:
    var gs: SimState = _state([
        _virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2),
        _virtual_dict("T000014", "GEN-DEWATER-RUN", "substructure", "mechanical", 5, [], "dewatering"),
        _virtual_dict("T000015", "GEN-LIFT-STUDY", "superstructure", "finishes", 2, [], null),
    ])
    var ms: Array[Dictionary] = gs.virtual_markers()
    eq(ms.size(), 3, "one marker per virtual task")
    var z: ZoneData = gs.bundle.zones_by_id["L00-Z1"]
    for m in ms:
        eq(m["zone_id"], "L00-Z1", "marker zone")
        eq(m["cell"], z.centre_cell(), "marker at the zone centre cell")
        eq(m["storey_id"], "L00", "marker storey")
        eq(m["state"], "READY", "state name of the runtime state")
    eq(ms[0]["marker"], "survey", "explicit marker")
    eq(ms[1]["marker"], "dewatering", "dewatering marker")
    eq(ms[2]["marker"], "lift_plan", "marker derived from the step id when absent")
    eq(ms[2]["index"], 2, "index within the zone")
    ok(z.cells.has(z.centre_cell()), "centre cell belongs to the zone")
    gs.set_task_state("T000013", RS.ACTIVE)
    eq(gs.virtual_markers()[0]["state"], "ACTIVE", "state follows the task")


func test_excluded_from_element_visuals() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 1)])
    eq(gs.element_visuals.size(), 11, "visual entries are the elements only")
    ok(not gs.element_visuals.has(""), "no visual entry for a null element")
    set_finished(gs, "T000013")
    gs.set_task_state("T000013", RS.ACTIVE)
    gs.set_task_state("T000013", RS.DONE)
    eq(gs.element_visuals.size(), 11, "still only elements")
    for guid in gs.element_visuals:
        eq(gs.element_visual(guid), SimState.Visual.GHOST, "no element changed: %s" % guid)


func test_bim_view_draws_markers_and_hook() -> void:
    var gs: SimState = _state([
        _virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2),
        _virtual_dict("T000014", "GEN-DEWATER-RUN", "substructure", "mechanical", 5, [], "dewatering"),
    ])
    var view := BimView.new()
    view.use_kits = false  # the placeholder markers (the visual kits install their own mesh provider)
    view.setup(gs)
    eq(view.marker_count(), 2, "two marker nodes")
    var total: int = 0
    for k in view.instance_counts():
        total += int(view.instance_counts()[k])
    eq(total, 11, "element instances exclude virtual tasks")
    var n1: MeshInstance3D = view.marker_node("T000013")
    var n2: MeshInstance3D = view.marker_node("T000014")
    ok(n1 != null and n2 != null, "marker nodes by task id")
    ok(n1.mesh is CylinderMesh, "placeholder cylinder")
    ok(n1.position.distance_to(n2.position) > 0.05, "markers are offset per index")
    var c1: Color = (n1.material_override as StandardMaterial3D).albedo_color
    near(c1.r, BimView.MARKER_COLORS["survey"].r, "survey colour (yellow)")
    near((n2.material_override as StandardMaterial3D).albedo_color.b, BimView.MARKER_COLORS["dewatering"].b, "dewatering colour (blue)")
    var a0: float = c1.a
    gs.set_task_state("T000013", RS.ACTIVE)
    ok((n1.material_override as StandardMaterial3D).albedo_color.a > a0, "active marker is more opaque")
    # the hook for the kits agent
    view.marker_mesh_provider = func(m: String) -> Mesh:
        return SphereMesh.new() if m == "survey" else null
    ok(view.marker_mesh_for("survey") is SphereMesh, "provider mesh used")
    ok(view.marker_mesh_for("dewatering") is CylinderMesh, "null result falls back to the placeholder")
    view.free()


func test_bim_view_markers_with_the_default_kit_provider() -> void:
    var gs: SimState = _state([_virtual_dict("T000013", "GEN-SURVEY-ASBUILT", "substructure", "finishes", 2)])
    var view := BimView.new()
    view.setup(gs)  # visual kits on: they install the marker mesh provider through the hook
    eq(view.marker_count(), 1, "marker node exists")
    ok(view.marker_node("T000013").mesh != null, "marker has a mesh")
    gs.set_task_state("T000013", RS.ACTIVE)
    gs.set_task_state("T000013", RS.DONE)
    ok(view.marker_node("T000013") != null, "marker survives state changes")
    view.free()
