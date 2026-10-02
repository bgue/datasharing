extends TC
## Manual sequencing (docs/06 A.3): manual mode, authored / virtual tasks, links, update / remove, export, save / load.

const RS := TaskRuntime.State
const ZONE: String = "L00-Z1"


func _state() -> SimState:
    var gs: SimState = fixture_state()
    gs.inspection_fail_override = 0.0
    return gs


## excavate -> pile -> survey (virtual) -> form (+2 days lag) -> slab pour, in the ground zone, in manual mode.
func _chain(gs: SimState) -> Dictionary:
    ok(Manual.set_mode(gs, ZONE, true), "manual mode on")
    var a: String = Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": ZONE, "elements": ["FOOT0"], "quantity": 20})
    var b: String = Manual.add_task(gs, {"step": "CIV-PILE-DRIVE", "zone_id": ZONE, "elements": ["FOOT0"], "quantity": 8, "after": [a]})
    var c: String = Manual.add_task(gs, {"step": "GEN-SURVEY-ASBUILT", "zone_id": ZONE, "virtual": true, "duration_days": 2,
            "marker": "survey", "after": [b], "name": "As-built survey of piles"})
    var d: String = Manual.add_task(gs, {"step": "STR-SLAB-FORM", "zone_id": ZONE, "elements": ["FOOT0"], "quantity": 40,
            "after": [c], "lag_days": 2})
    var e: String = Manual.add_task(gs, {"step": "STR-SLAB-POUR", "zone_id": ZONE, "elements": ["SLAB1"], "after": [d]})
    for id in [a, b, c, d, e]:
        ok(id != "", "chain task created: %s" % gs.last_error)
    return {"a": a, "b": b, "c": c, "d": d, "e": e}


func _rt(gs: SimState, id: String) -> TaskRuntime:
    return gs.runtime[id]


func test_set_mode_holds_and_releases_generated_packages() -> void:
    var gs: SimState = new_state()
    build_road(gs)
    gs.hire("concrete")
    gs.assign_crew(1, ZONE)
    var zone_pkgs: Array = gs.bundle.packages_by_zone[ZONE]
    ok(not zone_pkgs.is_empty(), "ground zone has packages")
    ok(Manual.set_mode(gs, ZONE, true), "set_mode on")
    ok(gs.manual_zones.has(ZONE), "zone recorded in manual mode")
    for p in zone_pkgs:
        var prt: PackageRuntime = gs.package_runtime[(p as PackageData).package_id]
        ok(prt.frozen and not prt.released, "package frozen and held: %s" % (p as PackageData).package_id)
        eq(prt.state, "held", "package state held")
    for t in gs.bundle.tasks_by_zone[ZONE]:
        eq(state_of(gs, (t as TaskData).task_id), RS.BLOCKED, "generated task excluded from readiness")
        ok(gs.runtime[(t as TaskData).task_id].blocked_reason.contains("manual mode"), "reason names the manual mode")
    ok(gs.is_frozen_task(gs.bundle.tasks_by_id["T000001"]), "frozen task")
    ok(not gs.is_frozen_task(gs.bundle.tasks_by_id["T000009"]), "other zone untouched")
    eq(ApiViews.zone_view(gs, gs.bundle.zones_by_id[ZONE])["manual_mode"], true, "zone view flag")
    gs.advance_week()
    near((gs.runtime["T000001"] as TaskRuntime).progress, 0.0, "no work in a manual zone without manual tasks")
    eq(state_of(gs, "T000001"), RS.BLOCKED, "still frozen after a week")
    ok(Manual.set_mode(gs, ZONE, false), "set_mode off")
    for p in zone_pkgs:
        var prt2: PackageRuntime = gs.package_runtime[(p as PackageData).package_id]
        ok(not prt2.frozen and prt2.released, "package released again")
    eq(state_of(gs, "T000001"), RS.READY, "generated work flows again")
    gs.advance_week()
    ok((gs.runtime["T000001"] as TaskRuntime).progress > 0.0, "crews work the zone after the mode is switched off")
    ok(not Manual.set_mode(gs, "NOPE", true), "unknown zone refused")
    ok(gs.last_error.contains("no such zone"), "error: %s" % gs.last_error)


func test_manual_chain_is_played_by_crews() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    eq(ids["a"], "M000001", "runtime ids start at M000001")
    eq(ids["e"], "M000005", "runtime ids are sequential")
    near((gs.runtime[ids["a"]] as TaskRuntime).required, 1.0, "excavation 20 m3 / 20 per crew-day")
    near((gs.runtime[ids["b"]] as TaskRuntime).required, 2.0, "piles 8 / 4 per crew-day")
    near((gs.runtime[ids["c"]] as TaskRuntime).required, 2.0, "virtual survey is duration driven")
    var slab: TaskData = gs.bundle.tasks_by_id[ids["e"]]
    near(slab.quantity, 43.2, "default quantity reuses the BIM quantity of the generated task")
    ok(gs.bundle.tasks_by_id[ids["c"]].is_virtual, "survey is virtual")
    eq(gs.bundle.tasks_by_id[ids["b"]].origin, "manual", "origin manual")
    eq(state_of(gs, ids["a"]), RS.READY, "first manual task ready (zone access still missing -> impeded)")
    Planner.auto_layout(gs, 4)  # haul road and laydown for the slab pour
    Planner.autopilot(gs, 6, "ideal", 1.0, true, 8, true)
    for k in ids:
        ok(TaskRuntime.is_finished(state_of(gs, ids[k])), "manual task %s finished: %s" % [ids[k], TaskRuntime.state_name(state_of(gs, ids[k]))])
    var a: TaskRuntime = _rt(gs, ids["a"])
    var b: TaskRuntime = _rt(gs, ids["b"])
    var c: TaskRuntime = _rt(gs, ids["c"])
    var d: TaskRuntime = _rt(gs, ids["d"])
    var e: TaskRuntime = _rt(gs, ids["e"])
    ok(b.actual_start_day >= a.actual_finish_day, "pile after excavation")
    ok(c.actual_start_day >= b.actual_finish_day, "survey after piles")
    ok(d.actual_start_day >= c.actual_finish_day + 2, "form after survey + 2 days lag (started %d, survey done %d)" % [d.actual_start_day, c.actual_finish_day])
    ok(e.actual_start_day >= d.actual_finish_day, "slab after formwork")
    eq(c.actual_finish_day - c.actual_start_day, 2, "survey took exactly its 2 days")
    near((gs.runtime["T000001"] as TaskRuntime).progress, 0.0, "generated tasks of the manual zone never ran")
    ok(not gs.finished or gs.won, "level not lost")
    var m: Array[Dictionary] = gs.virtual_markers()
    eq(m.size(), 1, "one virtual marker")
    eq(m[0]["task_id"], ids["c"], "marker of the survey")
    eq(m[0]["state"], "DONE", "marker state")
    eq(ApiViews.summary(gs)["manual_zones"], 1, "summary counts manual zones")
    eq(ApiViews.summary(gs)["virtual_tasks"], 1, "summary counts virtual tasks")


func test_add_task_validation() -> void:
    var gs: SimState = _state()
    eq(Manual.add_task(gs, {"zone_id": ZONE, "elements": ["FOOT0"]}), "", "step required")
    eq(Manual.add_task(gs, {"step": "NO-SUCH-STEP", "zone_id": ZONE, "elements": ["FOOT0"]}), "", "unknown step")
    ok(gs.last_error.contains("unknown step"), gs.last_error)
    eq(Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": ZONE, "elements": ["NOPE"]}), "", "unknown element")
    eq(Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": "NOZONE", "elements": ["FOOT0"]}), "", "unknown zone")
    eq(Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": ZONE}), "", "needs elements or virtual")
    eq(Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "elements": ["FOOT0"], "after": ["T999999"]}), "", "unknown predecessor")
    eq(Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "elements": ["FOOT0"], "link_type": "XX", "after": ["T000001"]}), "", "bad link type")
    eq(gs.bundle.tasks.size(), 12, "nothing was added by the failures")
    # zone defaults to the element's zone; links to generated tasks are allowed; inline step definitions register steps
    var id: String = Manual.add_task(gs, {"step": "ARC-PAINT", "elements": ["WALL1"], "after": ["T000011"],
            "step_def": {"id": "ARC-PAINT", "name": "Paint wall", "phase": "interiors", "trade": "finishes", "discipline": "architecture",
                    "quantity_basis": "area_m2", "rate_per_crew_day": 50, "unit_cost": 5}})
    ok(id != "", "inline step definition: %s" % gs.last_error)
    ok(gs.bundle.steps_by_id.has("ARC-PAINT"), "step registered in the bundle library")
    eq(gs.bundle.tasks_by_id[id].zone_id, ZONE, "zone from the element")
    eq(gs.bundle.tasks_by_id[id].predecessors[0]["task_id"], "T000011", "link to a generated task")
    ok((gs.bundle.successors_by_task["T000011"] as Array).has(id), "successor index updated")
    ok(gs.bundle.tasks_by_element["WALL1"].has(gs.bundle.tasks_by_id[id]), "element index updated")
    ok(gs.bundle.packages_by_id.has(gs.bundle.tasks_by_id[id].package_id), "package created")
    ok(gs.package_runtime.has(gs.bundle.tasks_by_id[id].package_id), "package runtime created")
    ok(gs.bundle.packages_by_id[gs.bundle.tasks_by_id[id].package_id].manual, "authored package flagged manual")


func test_links_lags_and_cycles() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    ok(not Manual.link(gs, ids["e"], ids["a"]), "cycle refused")
    ok(gs.last_error.contains("cycle"), gs.last_error)
    ok(not Manual.link(gs, ids["a"], ids["a"]), "self link refused")
    ok(not Manual.link(gs, "NOPE", ids["a"]), "unknown task refused")
    ok(not Manual.link(gs, ids["a"], ids["b"], "ZZ"), "bad type refused")
    ok(Manual.link(gs, ids["a"], ids["d"], "SS", 3), "extra SS link with lag")
    var d: TaskData = gs.bundle.tasks_by_id[ids["d"]]
    var found: bool = false
    for p in d.predecessors:
        if p["task_id"] == ids["a"]:
            found = true
            eq(p["type"], "SS", "link type stored")
            eq(p["lag_days"], 3, "lag stored")
    ok(found, "link present")
    ok((gs.bundle.successors_by_task[ids["a"]] as Array).has(ids["d"]), "successor index")
    ok(Manual.link(gs, ids["a"], ids["d"], "FS", 1), "re-linking updates the existing link")
    var n: int = 0
    for p in d.predecessors:
        if p["task_id"] == ids["a"]:
            n += 1
            eq(p["type"], "FS", "updated type")
    eq(n, 1, "no duplicate link")
    ok(Manual.unlink(gs, ids["a"], ids["d"]), "unlink")
    ok(not (gs.bundle.successors_by_task[ids["a"]] as Array).has(ids["d"]), "successor removed")
    ok(not Manual.unlink(gs, ids["a"], ids["d"]), "unlinking twice fails")
    # the lag is honoured by readiness: an SS link with lag 3 holds a task back until 3 days after the start
    var x: String = Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": ZONE, "elements": ["FOOT1"], "quantity": 100})
    var y: String = Manual.add_task(gs, {"step": "CIV-EARTH-CUT", "zone_id": ZONE, "elements": ["FOOT2"], "quantity": 20,
            "after": [x], "link_type": "SS", "lag_days": 3})
    var ty: TaskData = gs.bundle.tasks_by_id[y]
    ok(Readiness.predecessor_block(gs, ty).begins_with("Waiting to start"), "SS waits for the start")
    _rt(gs, x).actual_start_day = 0
    ok(Readiness.predecessor_block(gs, ty, 2).begins_with("Lag after start"), "SS lag holds on day 2")
    eq(Readiness.predecessor_block(gs, ty, 3), "", "SS lag over on day 3")


func test_update_and_remove_task() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    var b: TaskData = gs.bundle.tasks_by_id[ids["b"]]
    var pkg: PackageData = gs.bundle.package_of(b)
    var before_total: float = pkg.total_crew_days
    ok(Manual.update_task(gs, ids["b"], {"quantity": 16}), "update quantity: %s" % gs.last_error)
    b = gs.bundle.tasks_by_id[ids["b"]]
    near(b.estimated_crew_days, 4.0, "estimated crew days follow the quantity")
    near(_rt(gs, ids["b"]).required, 4.0, "runtime requirement follows")
    near(gs.bundle.package_of(b).total_crew_days, before_total + 2.0, "package total updated")
    ok((gs.bundle.successors_by_task[ids["b"]] as Array).has(ids["c"]), "successors kept after the update")
    ok(Manual.update_task(gs, ids["c"], {"duration_days": 4, "name": "Survey (extended)", "marker": "survey"}), "update the virtual survey")
    var c: TaskData = gs.bundle.tasks_by_id[ids["c"]]
    eq(c.duration_days, 4, "duration")
    near(c.estimated_crew_days, 4.0, "duration drives effort")
    eq(c.element_name, "Survey (extended)", "renamed")
    ok(c.is_virtual and gs.bundle.virtual_tasks.has(c), "still virtual and indexed")
    # replace the predecessors: the form follows the piles directly
    ok(Manual.update_task(gs, ids["d"], {"after": [ids["b"]], "lag_days": 1}), "replace links")
    var d: TaskData = gs.bundle.tasks_by_id[ids["d"]]
    eq(d.predecessors.size(), 1, "one predecessor")
    eq(d.predecessors[0]["task_id"], ids["b"], "now after the piles")
    eq(d.predecessors[0]["lag_days"], 1, "lag")
    ok(not (gs.bundle.successors_by_task[ids["c"]] as Array).has(ids["d"]), "old successor entry removed")
    ok((gs.bundle.successors_by_task[ids["b"]] as Array).has(ids["d"]), "new successor entry")
    ok(not Manual.update_task(gs, ids["a"], {"after": [ids["e"]]}), "a cycle through update is refused")
    ok(not Manual.update_task(gs, "T000001", {"quantity": 1}), "generated tasks cannot be updated")
    ok(gs.last_error.contains("generated"), gs.last_error)
    # remove with bridging: removing the survey keeps the chain order
    var survey_pkg: String = gs.bundle.tasks_by_id[ids["c"]].package_id
    ok(Manual.link(gs, ids["c"], ids["e"]), "survey also precedes the pour")
    ok(Manual.remove_task(gs, ids["c"]), "remove the survey: %s" % gs.last_error)
    ok(not gs.bundle.tasks_by_id.has(ids["c"]), "gone from the bundle")
    ok(not gs.runtime.has(ids["c"]), "gone from the runtime")
    eq(gs.bundle.virtual_tasks.size(), 0, "gone from the virtual list")
    ok(not gs.bundle.packages_by_id.has(survey_pkg), "emptied package removed")
    ok(not gs.package_runtime.has(survey_pkg), "emptied package runtime removed")
    var e: TaskData = gs.bundle.tasks_by_id[ids["e"]]
    var has_b: bool = false
    for p in e.predecessors:
        ok(p["task_id"] != ids["c"], "no dangling link to the removed task")
        if p["task_id"] == ids["b"]:
            has_b = true
    ok(has_b, "the pour inherited the survey's predecessor (bridge)")
    eq(gs.virtual_markers().size(), 0, "marker removed")
    ok(not Manual.remove_task(gs, "T000001"), "generated tasks cannot be removed")
    ok(not Manual.remove_task(gs, ids["c"]), "removing twice fails")
    # a started task cannot be removed or changed
    gs.set_task_state(ids["a"], RS.ACTIVE)
    ok(not Manual.remove_task(gs, ids["a"]), "started task cannot be removed")
    ok(not Manual.update_task(gs, ids["a"], {"quantity": 1}), "started task cannot be updated")


func test_planned_dates_follow_the_links() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    var a: TaskData = gs.bundle.tasks_by_id[ids["a"]]
    var b: TaskData = gs.bundle.tasks_by_id[ids["b"]]
    var d: TaskData = gs.bundle.tasks_by_id[ids["d"]]
    var c: TaskData = gs.bundle.tasks_by_id[ids["c"]]
    eq(a.planned_start_day, 0, "first task planned today")
    ok(b.planned_start_day >= a.planned_finish_day, "pile planned after excavation")
    ok(d.planned_start_day >= c.planned_finish_day + 2, "form planned after survey + lag")
    ok(d.planned_finish_day > d.planned_start_day, "positive duration")


func test_export_documents() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    var doc: Dictionary = Manual.export_doc(gs)
    eq(doc["schema_version"], "1.0", "schema version")
    ok(doc.has("tasks") and doc["tasks"] is Array, "tasks array")
    eq((doc["zones_in_manual_mode"] as Array), [ZONE], "zones in manual mode")
    eq((doc["tasks"] as Array).size(), 5, "all five authored tasks exported")
    var re := RegEx.new()
    re.compile("^M[0-9]{4,6}$")
    var seen: Dictionary = {}
    for t in doc["tasks"]:
        var td: Dictionary = t
        for k in ["id", "step", "zone_id"]:
            ok(td.has(k) and str(td[k]) != "", "required field %s" % k)
        ok(re.search(str(td["id"])) != null, "manual id pattern: %s" % td["id"])
        seen[td["id"]] = td
        for aid in td.get("after", []):
            ok(seen.has(aid) or str(aid).begins_with("T"), "after references an earlier manual task or a T-id: %s" % aid)
        if td.has("link_type"):
            ok(["FS", "SS", "FF"].has(td["link_type"]), "link type value")
    var survey: Dictionary = seen[ids["c"]]
    eq(survey["virtual"], true, "virtual flag exported")
    eq(survey["duration_days"], 2, "duration exported")
    eq(survey["marker"], "survey", "marker exported")
    ok(not survey.has("elements") or (survey["elements"] as Array).is_empty(), "virtual task has no elements")
    var form: Dictionary = seen[ids["d"]]
    eq(form["lag_days"], 2, "lag exported")
    eq(form["after"], [ids["c"]], "after exported")
    eq(form["elements"], ["FOOT0"], "elements exported")
    var parsed: Variant = JSON.parse_string(JSON.stringify(doc))
    ok(parsed is Dictionary, "document survives JSON")
    var out := FileAccess.open("user://manual_sequence_test.json", FileAccess.WRITE)  # validated against the schema by tools
    out.store_string(JSON.stringify(doc, " "))
    out.close()
    # plan export: manual and virtual tasks are included with element_guid null, virtual, origin, manual_id
    var plan: Dictionary = PlanExport.build(gs)
    var rows: Dictionary = {}
    var tre := RegEx.new()
    tre.compile("^T[0-9]{6}$")
    for t in plan["tasks"]:
        var row: Dictionary = t
        ok(tre.search(str(row["task_id"])) != null, "exported T-id: %s" % row["task_id"])
        rows[row["task_id"]] = row
    eq(rows.size(), 17, "12 generated + 5 manual tasks")
    var vrow: Dictionary = {}
    for k in rows:
        if rows[k].get("manual_id", null) == ids["c"]:
            vrow = rows[k]
    ok(not vrow.is_empty(), "virtual task exported")
    ok(vrow["element_guid"] == null, "element_guid null")
    eq(vrow["virtual"], true, "virtual true")
    eq(vrow["origin"], "manual", "origin")
    ok(int(str(vrow["task_id"]).substr(1)) > 12, "T-id beyond the maximum")
    for k in rows:
        for p in rows[k]["predecessors"]:
            ok(rows.has(p["task_id"]), "predecessor exists in the export: %s" % p["task_id"])
    ok(plan.has("manual"), "plan export carries the manual block")
    var pout := FileAccess.open("user://plan_export_manual_test.json", FileAccess.WRITE)
    pout.store_string(JSON.stringify(plan, " "))
    pout.close()
    var csv: String = PlanExport.build_csv(gs)
    ok(not csv.contains("<null>"), "csv has no null markers")
    eq(csv.strip_edges().split("\n").size(), 18, "csv rows")


func test_save_load_round_trip() -> void:
    var gs: SimState = _state()
    var ids: Dictionary = _chain(gs)
    Manual.update_task(gs, ids["b"], {"quantity": 12})
    Manual.link(gs, ids["a"], ids["e"], "FS", 4)
    build_road(gs)
    Planner.autopilot(gs, 1, "ideal", 1.0, true, 8, true)  # some progress
    var applied_before: int = gs.manual_applied.size()
    var saved: Dictionary = JSON.parse_string(JSON.stringify(gs.serialize()))  # through real JSON
    # a brand new state on a fresh bundle
    var gs2: SimState = fixture_state()
    ok(gs2.deserialize(saved), "load into a fresh state: %s" % gs2.last_error)
    ok(gs2.manual_zones.has(ZONE), "manual zone restored")
    eq(gs2.bundle.tasks.size(), gs.bundle.tasks.size(), "task count")
    eq(gs2.manual_next_id, gs.manual_next_id, "id counter")
    for t in gs.bundle.tasks:
        var t2: TaskData = gs2.bundle.tasks_by_id.get(t.task_id, null)
        ok(t2 != null, "task restored: %s" % t.task_id)
        if t2 == null:
            continue
        eq(t2.predecessors.size(), t.predecessors.size(), "predecessors of %s" % t.task_id)
        for i in t.predecessors.size():
            eq(t2.predecessors[i]["task_id"], t.predecessors[i]["task_id"], "pred id of %s" % t.task_id)
            eq(t2.predecessors[i]["lag_days"], t.predecessors[i]["lag_days"], "pred lag of %s" % t.task_id)
        eq(t2.package_id, t.package_id, "package of %s" % t.task_id)
        eq(t2.is_virtual, t.is_virtual, "virtual of %s" % t.task_id)
        near(t2.estimated_crew_days, t.estimated_crew_days, "effort of %s" % t.task_id)
        eq(state_of(gs2, t.task_id), state_of(gs, t.task_id), "state of %s" % t.task_id)
        near((gs2.runtime[t.task_id] as TaskRuntime).progress, (gs.runtime[t.task_id] as TaskRuntime).progress, "progress of %s" % t.task_id)
    eq(gs2.bundle.packages.size(), gs.bundle.packages.size(), "package count")
    for p in gs.bundle.packages:
        ok(gs2.package_runtime.has(p.package_id), "package runtime restored: %s" % p.package_id)
        eq((gs2.package_runtime[p.package_id] as PackageRuntime).frozen, (gs.package_runtime[p.package_id] as PackageRuntime).frozen, "frozen flag")
    eq(gs2.virtual_markers().size(), 1, "markers restored")
    eq(gs2.manual_applied.size(), applied_before, "applied recipes restored")
    eq(gs2.week, gs.week, "week")
    # loading into the state that holds the edits replays them on the pristine tasks
    ok(Manual.remove_task(gs, ids["e"]), "edit after saving")
    ok(gs.bundle.edited, "bundle marked edited")
    ok(gs.deserialize(saved), "load into the edited state: %s" % gs.last_error)
    ok(gs.bundle.tasks_by_id.has(ids["e"]), "the removed task is back after loading")
    eq(gs.bundle.tasks.size(), gs2.bundle.tasks.size(), "same task count after the reload")
    # a restart drops the edits
    ok(gs.start(gs.bundle), "restart")
    eq(gs.bundle.tasks.size(), 12, "restart starts from the pristine tasks")
    ok(gs.manual_zones.is_empty(), "restart clears the manual zones")


func test_new_tasks_work_with_gates_and_caches() -> void:
    # a manual structure task added late holds the MEP task behind the storey gate; removing it frees the duct
    var gs: SimState = _state()
    for id in ["T000001", "T000002", "T000003", "T000004", "T000005", "T000006", "T000007", "T000008", "T000009"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.READY, "duct ready before the manual task")
    var m: String = Manual.add_task(gs, {"step": "STR-SLAB-FORM", "zone_id": ZONE, "elements": ["FOOT0"], "quantity": 10})
    ok(m != "", "task added: %s" % gs.last_error)
    eq(state_of(gs, "T000010"), RS.BLOCKED, "gate now sees the new superstructure task")
    ok(gs.runtime["T000010"].blocked_reason.begins_with("Gate"), "by the gate: %s" % gs.runtime["T000010"].blocked_reason)
    ok(Manual.remove_task(gs, m), "remove it")
    eq(state_of(gs, "T000010"), RS.READY, "duct ready again")


func test_updating_a_loaded_manual_task_survives_save_and_restart() -> void:
    # T000012 plays the part of a manual task that came with the bundle (pipeline --manual merge)
    var d: Dictionary = fixture_dict()
    for t in d["tasks"]:
        if str((t as Dictionary)["task_id"]) == "T000012":
            (t as Dictionary)["origin"] = "manual"
            (t as Dictionary)["manual_id"] = "M0001"
    var gs: SimState = state_from_dict(d)
    var t12: TaskData = gs.bundle.tasks_by_id["T000012"]
    ok(t12.is_authored(), "loaded manual task is authored")
    eq(Manual.first_free_id(gs.bundle), 2, "runtime ids continue after the loaded manual ids")
    ok(Manual.update_task(gs, "M0001", {"quantity": 5}), "update by manual id: %s" % gs.last_error)
    near(gs.bundle.tasks_by_id["T000012"].quantity, 5.0, "quantity updated")
    eq(gs.bundle.tasks_by_id["T000012"].manual_id, "M0001", "ids kept")
    var doc: Dictionary = Manual.export_doc(gs)
    eq((doc["tasks"] as Array)[0]["id"], "M0001", "exported under its manual id")
    var saved: Dictionary = JSON.parse_string(JSON.stringify(gs.serialize()))
    var gs2: SimState = state_from_dict(d)  # a fresh state on the same bundle dictionary
    ok(gs2.deserialize(saved), "load: %s" % gs2.last_error)
    near(gs2.bundle.tasks_by_id["T000012"].quantity, 5.0, "update restored")
    eq(gs2.bundle.tasks_by_id["T000012"].predecessors.size(), 1, "links intact")
    ok((gs2.bundle.successors_by_task["T000010"] as Array).has("T000012"), "successor index intact")
