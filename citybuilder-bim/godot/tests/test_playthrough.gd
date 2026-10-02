extends TC
## Scripted playthrough of the minimal scenario: logistics, crews, weeks, export.

const RS := TaskRuntime.State


## Hire the crane only while heavy-lift work is waiting or in progress.
func _manage_crane(gs: SimState) -> void:
    var needed: bool = false
    for t in gs.bundle.tasks:
        if t.requires_crane:
            var st: int = (gs.runtime[t.task_id] as TaskRuntime).state
            if st == RS.READY or st == RS.ACTIVE:
                needed = true
    if needed and gs.equipment_placed.is_empty():
        gs.place_equipment("mc1", Vector2i(1, 1))
    elif not needed and not gs.equipment_placed.is_empty():
        gs.remove_equipment(0)


## Simple planner: puts one crew of each trade where its READY/ACTIVE work is, fires idle trades.
func _autopilot(gs: SimState) -> void:
    for trade in gs.bundle.trade_ids():
        var best_zone: String = ""
        var best_n: int = 0
        var open_work: bool = false
        for z in gs.bundle.zones:
            var n: int = 0
            for t in gs.bundle.tasks_by_zone[z.id]:
                var task: TaskData = t
                if task.trade != trade:
                    continue
                var st: int = (gs.runtime[task.task_id] as TaskRuntime).state
                if st == RS.READY or st == RS.ACTIVE or st == RS.REWORK:
                    n += 1
                if not TaskRuntime.is_finished(st):
                    open_work = true
            if n > best_n:
                best_n = n
                best_zone = z.id
        var have: Array[Dictionary] = []
        for c in gs.crews:
            if str(c["trade"]) == trade:
                have.append(c)
        if best_n == 0 and not have.is_empty():
            for c in have:
                gs.fire(int(c["id"]))  # nothing to release: stop paying for idle crews
            continue
        if best_n > 0 and have.is_empty():
            gs.hire(trade)
            have = []
            for c in gs.crews:
                if str(c["trade"]) == trade:
                    have.append(c)
        if best_n > 0:
            for c in have:
                if str(c["zone_id"]) != best_zone:
                    gs.assign_crew(int(c["id"]), best_zone)


func test_minimal_scenario_completes_and_exports() -> void:
    var gs: SimState = new_state()
    var finished_signals: Array = []
    gs.level_finished.connect(func(r: Dictionary) -> void: finished_signals.append(r))

    # site layout: haul road from the gate (0,2) to the building, laydown, crane pad + crane, ICRA barrier
    ok(gs.place_tile(Vector2i(0, 2), "haul_road"), "road at gate")
    ok(gs.place_tile(Vector2i(1, 2), "haul_road"), "road to building")
    ok(gs.place_tile(Vector2i(1, 3), "laydown"), "laydown yard")
    ok(gs.place_tile(Vector2i(1, 1), "crane_pad"), "crane pad")
    ok(gs.place_tile(Vector2i(4, 3), "icra_barrier"), "icra barrier next to the live ward")
    ok(gs.zone_access("L00-Z1"), "ground zone reachable")

    # hire 2 concrete crews and assign them to the ground zone
    var c1: int = gs.hire("concrete")
    var c2: int = gs.hire("concrete")
    ok(c1 > 0 and c2 > 0, "two concrete crews hired")
    ok(gs.assign_crew(c1, "L00-Z1") and gs.assign_crew(c2, "L00-Z1"), "assigned to ground zone")
    near(Productivity.zone_congestion(gs, "L00-Z1"), 1.0, "two crews fit in a max_crews=2 zone")

    var weeks: int = 0
    while not gs.finished and weeks < 30:
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        _manage_crane(gs)
        _autopilot(gs)
        _manage_crane(gs)
        ok(gs.advance_week(), "advance week %d" % weeks)
        weeks += 1
    ok(gs.finished, "level finished within 30 weeks (weeks used: %d)" % weeks)
    ok(gs.won, "level won (reason: %s)" % str(gs.result.get("reason", "")))
    ok(weeks <= 30, "within 30 weeks")
    print("      [playthrough] finished in %d weeks (contract %d), cash %s, grade %s (%.1f)" % [
        gs.week, gs.bundle.contract_weeks(), Fmt.money(gs.cash), str(gs.result["grade"]), float(gs.result["total"])])
    eq(finished_signals.size(), 1, "level_finished emitted once")
    eq(gs.finished_task_count(), 12, "all 12 tasks finished")
    ok(float(gs.result["total"]) > 0.0, "score computed")
    ok(["S", "A", "B", "C", "D"].has(str(gs.result["grade"])), "grade assigned: " + str(gs.result["grade"]))
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        ok(rt.actual_start_day >= 0 and rt.actual_finish_day >= rt.actual_start_day, "%s dates sane (%d..%d)" % [t.task_id, rt.actual_start_day, rt.actual_finish_day])
        ok(rt.state == RS.DONE or rt.state == RS.INSPECTED, "%s finished" % t.task_id)
    # sequence respected: slab after all columns, wall + duct after slab, commissioning last
    var slab: TaskRuntime = gs.runtime["T000009"]
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        ok((gs.runtime[id] as TaskRuntime).actual_finish_day <= slab.actual_finish_day, "column %s finished before slab" % id)
    ok((gs.runtime["T000012"] as TaskRuntime).actual_finish_day >= (gs.runtime["T000010"] as TaskRuntime).actual_finish_day, "commissioning after duct")

    # export
    var path: String = "res://tests/_out_plan_export.json"
    var out_csv: String = "res://tests/_out_plan_export.csv"
    ok(PlanExport.export_to_path(gs, path, out_csv), "export wrote file")
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
    ok(parsed is Dictionary, "export parses as JSON")
    var d: Dictionary = parsed
    eq((d["tasks"] as Array).size(), 12, "12 tasks exported")
    eq(str(d["generator"]), "sitebuilder-godot", "generator tag")
    eq(str(d["sector"]), "healthcare", "sector")
    ok(str(d["generated_at"]).length() >= 19, "generated_at ISO time")
    for t in d["tasks"]:
        ok(t["actual_start_day"] != null, "%s actual_start_day set" % t["task_id"])
        ok(t["actual_finish_day"] != null, "%s actual_finish_day non-null" % t["task_id"])
        ok(t.has("predecessors") and t.has("flags"), "task keeps schema fields")
    var csv: String = FileAccess.get_file_as_string(out_csv)
    var lines: PackedStringArray = csv.strip_edges().split("\n")
    eq(lines.size(), 13, "csv has header + 12 rows")
    eq(lines[0].strip_edges(), "element_guid,task_id,step_id,planned_start,planned_finish,actual_start,actual_finish", "csv header")
    DirAccess.remove_absolute(ProjectSettings.globalize_path(path))
    DirAccess.remove_absolute(ProjectSettings.globalize_path(out_csv))
    gs.free()


func test_save_and_load_roundtrip() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    build_road(gs)
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    gs.advance_week()
    gs.advance_week()
    var data: Dictionary = gs.serialize()
    var text: String = JSON.stringify(data)
    var back: Variant = JSON.parse_string(text)
    ok(back is Dictionary, "serialises to JSON")
    var gs2: SimState = new_state()
    gs2.scenario.events.clear()
    ok(gs2.deserialize(back), "deserialise")
    eq(gs2.week, gs.week, "week")
    near(gs2.cash, gs.cash, "cash")
    eq(gs2.crews.size(), 1, "crews")
    eq(gs2.tiles.size(), gs.tiles.size(), "tiles")
    for t in gs.bundle.tasks:
        eq(state_of(gs2, t.task_id), state_of(gs, t.task_id), "state of %s" % t.task_id)
    eq(gs2.rng.state, gs.rng.state, "rng state restored")
    eq(gs2.rng.randf(), gs.rng.randf(), "next draws match")
    gs.free()
    gs2.free()
