extends TC
## Work packages (docs/05 section 1): grouping / synthesis, crew profile, demand curve, hold / release, states.

const RS := TaskRuntime.State


func _bundle_with_packaging(packaging: Dictionary) -> SequenceBundle:
    var d: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(MINIMAL_PATH))
    d["step_library"]["packaging"] = packaging
    return SequenceBundle.from_dictionary(d)


## Minimal state with a road + crane so the ground-zone concrete work can proceed, events off, wide zone.
func _staffed_state() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 10
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    return gs


func _hire_to(gs: SimState, trade: String, n: int, zone: String) -> Array[int]:
    gs.scenario.crews_available[trade] = 20
    gs.bundle.trades_by_id[trade].max_hire_per_week = 20
    var ids: Array[int] = []
    for i in n:
        var id: int = gs.hire(trade)
        gs.assign_crew(id, zone)
        ids.append(id)
    return ids


func test_synthesis_groups_by_zone_phase_trade_face() -> void:
    var b: SequenceBundle = load_bundle()
    ok(b.packages_synthesised, "minimal has no packages: synthesised")
    eq(b.packages.size(), 6, "six packages (footings, columns, slab, duct, wall, commissioning)")
    eq(b.packages[0].package_id, "P00001", "deterministic ids from P00001")
    eq(b.packages[0].phase, "substructure", "ordered by storey, zone, phase order")
    eq(b.packages[0].task_ids.size(), 4, "four footings in one package")
    near(b.packages[0].total_crew_days, 0.96, "total crew days")
    eq(b.packages[5].zone_id, "L01-Z1", "storey 1 package last")
    for t in b.tasks:
        ok(t.package_id != "" and b.packages_by_id.has(t.package_id), "%s has a package" % t.task_id)
        eq(b.package_of(t).zone_id, t.zone_id, "package zone matches task zone")
    eq((b.packages_by_zone["L00-Z1"] as Array).size(), 5, "packages by zone")


func test_synthesis_splits_big_packages() -> void:
    var b: SequenceBundle = _bundle_with_packaging({"max_crew_days_per_package": 0.5})
    var sub: int = 0
    for p in b.packages:
        if p.phase == "substructure":
            sub += 1
            ok(p.total_crew_days <= 0.5 + 0.0001 or p.tasks.size() == 1, "split package within the limit")
    eq(sub, 2, "0.96 crew-days at 0.5 per package splits the footings in two")


func test_group_by_option() -> void:
    var b: SequenceBundle = _bundle_with_packaging({"group_by": ["zone_id", "trade"]})
    var concrete_l00: int = 0
    for p in b.packages:
        if p.zone_id == "L00-Z1" and p.trade == "concrete":
            concrete_l00 += 1
    eq(concrete_l00, 1, "grouping by zone and trade merges footing and column phases")


func test_bundle_packages_are_used_when_complete() -> void:
    var d: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(MINIMAL_PATH))
    var ids: Array = []
    for t in d["tasks"]:
        ids.append(t["task_id"])
    d["packages"] = [{"package_id": "P00001", "name": "All", "zone_id": "L00-Z1", "storey_id": "L00", "phase": "x",
        "trade": "concrete", "discipline": "structure", "work_face": "any", "task_ids": ids, "total_crew_days": 9.0,
        "crew_profile": {"min": 1, "ideal": 9, "max": 12}}]
    var b := SequenceBundle.from_dictionary(d)
    ok(not b.packages_synthesised, "given packages are used")
    eq(b.packages.size(), 1, "one package")
    eq(b.packages[0].crew_ideal, 2, "profile clamped to the zone max_crews (2)")
    eq(b.packages[0].crew_max, 2, "max clamped too")
    # an incomplete package list falls back to synthesis
    d["packages"][0]["task_ids"] = ["T000001"]
    var b2 := SequenceBundle.from_dictionary(d)
    ok(b2.packages_synthesised, "incomplete coverage: synthesise")


func test_crew_profile_derivation() -> void:
    var b: SequenceBundle = load_bundle()
    var slab: PackageData = b.packages_by_id["P00006"]
    eq([slab.crew_min, slab.crew_ideal, slab.crew_max], [1, 1, 2], "3.6 crew-days / 10 -> ideal 1, max ideal + 1 (zone max 2)")
    b.zones_by_id["L01-Z1"].max_crews = 8
    slab.total_crew_days = 35.0
    b.derive_profile(slab)
    eq([slab.crew_min, slab.crew_ideal, slab.crew_max], [1, 4, 5], "ideal = ceil(35 / 10) = 4, max = ideal + 1")
    b.steps_by_id["STR-SLAB-POUR"].crew_min = 3
    b.derive_profile(slab)
    eq(slab.crew_min, 3, "min = max of member step mins")


func test_below_min_crews_make_no_progress() -> void:
    var gs: SimState = _staffed_state()
    var p: PackageData = gs.bundle.packages_by_id["P00001"]
    p.crew_min = 2
    p.crew_ideal = 2
    p.crew_max = 3
    for t in p.tasks:
        (gs.runtime[t.task_id] as TaskRuntime).required = 100.0
    var crews: Array[int] = _hire_to(gs, "concrete", 1, "L00-Z1")
    gs.refresh_states()
    eq(gs.package_runtime["P00001"].state, "understaffed", "one crew < min 2: understaffed")
    eq(gs.package_runtime["P00001"].blocked_reason, "needs 2 crews", "reason names the shortfall")
    gs.run_work_day(0)
    near((gs.runtime["T000001"] as TaskRuntime).progress, 0.0, "no progress below min")
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.refresh_states()
    eq(gs.package_runtime["P00001"].state, "active", "two crews meet the minimum")
    gs.run_work_day(1)
    ok((gs.runtime["T000001"] as TaskRuntime).progress > 0.0, "progress with min crews")
    crews.clear()
    gs.free()


func test_over_ideal_crews_contribute_less_and_extra_spill() -> void:
    var base: float = 0.0
    var results: Dictionary = {}
    for n in [2, 3, 4]:
        var gs: SimState = _staffed_state()
        var p: PackageData = gs.bundle.packages_by_id["P00001"]
        p.crew_min = 1
        p.crew_ideal = 2
        p.crew_max = 3
        for t in p.tasks:
            (gs.runtime[t.task_id] as TaskRuntime).required = 100.0
        _hire_to(gs, "concrete", n, "L00-Z1")
        gs.refresh_states()
        gs.run_work_day(0)
        var prog: float = 0.0
        for t in p.tasks:
            prog += (gs.runtime[t.task_id] as TaskRuntime).progress
        results[n] = prog
        if n == 4:
            eq(gs.package_runtime["P00001"].crews_now, 3, "crews beyond max are not on this package")
            eq(Packages.allocate(gs)["idle"].size(), 1, "the fourth crew idles (package full, nothing to spill to)")
            eq(gs.crew_days_by_trade["concrete"][1], 1.0, "idle crew-day booked")
        gs.free()
    base = float(results[2]) / 2.0  # progress per crew-day at full contribution
    near(float(results[3]), base * (2.0 + 0.6), "third crew (over ideal) counts 0.6", 0.001)
    near(float(results[4]), float(results[3]), "fourth crew (beyond max) adds nothing", 0.001)


func test_spill_to_next_package_of_the_trade() -> void:
    var gs: SimState = _staffed_state()
    gs.place_tile(Vector2i(1, 1), "crane_pad")
    gs.place_equipment("mc1", Vector2i(1, 1))
    gs.bundle.tasks_by_id["T000005"].predecessors.clear()  # columns package has ready work too
    var p1: PackageData = gs.bundle.packages_by_id["P00001"]
    p1.crew_max = 1
    p1.crew_ideal = 1
    _hire_to(gs, "concrete", 2, "L00-Z1")
    gs.refresh_states()
    var alloc: Dictionary = Packages.allocate(gs)
    eq((alloc["by_package"]["P00001"] as Array).size(), 1, "first package takes its max")
    eq((alloc["by_package"].get("P00002", []) as Array).size(), 1, "the extra crew spills to the next released package")
    gs.run_work_day(0)
    ok((gs.runtime["T000005"] as TaskRuntime).state != RS.READY, "spilled crew started the column")
    gs.free()


func test_priority_decides_which_package_is_worked() -> void:
    var gs: SimState = _staffed_state()
    gs.place_tile(Vector2i(1, 1), "crane_pad")
    gs.place_equipment("mc1", Vector2i(1, 1))
    gs.bundle.tasks_by_id["T000005"].predecessors.clear()
    _hire_to(gs, "concrete", 1, "L00-Z1")
    gs.refresh_states()
    eq((Packages.allocate(gs)["by_package"] as Dictionary).keys(), ["P00001"], "earliest planned start first")
    gs.set_package_priority("P00002", -5)
    eq((Packages.allocate(gs)["by_package"] as Dictionary).keys(), ["P00002"], "lower priority value wins")
    gs.free()


func test_hold_and_release() -> void:
    var gs: SimState = _staffed_state()
    var crew: Array[int] = _hire_to(gs, "concrete", 1, "L00-Z1")
    gs.refresh_states()
    ok(gs.hold_package("P00001"), "hold")
    eq(gs.package_runtime["P00001"].state, "held", "state held")
    gs.run_work_day(0)
    eq(state_of(gs, "T000001"), RS.READY, "held package starts nothing")
    ok(gs.release_package("P00001"), "release")
    gs.run_work_day(1)
    ok(state_of(gs, "T000001") != RS.READY, "released package works")
    # active tasks of a held package finish
    gs.hold_package("P00001")
    var left: int = 0
    for t in gs.bundle.packages_by_id["P00001"].tasks:
        if state_of(gs, t.task_id) == RS.ACTIVE:
            left += 1
    for d in 3:
        gs.run_work_day(2 + d)
    for t in gs.bundle.packages_by_id["P00001"].tasks:
        ok(state_of(gs, t.task_id) != RS.ACTIVE, "active tasks finish while held")
    ok(not gs.hold_package("nope"), "unknown package refused")
    crew.clear()
    gs.free()


func test_states() -> void:
    var gs: SimState = _staffed_state()
    var p: PackageData = gs.bundle.packages_by_id["P00001"]
    eq(Packages.compute_state(gs, p, 0)["state"], "ready", "ready tasks, no crews")
    p.crew_min = 2
    eq(Packages.compute_state(gs, p, 1)["state"], "understaffed", "understaffed")
    p.crew_ideal = 2
    p.crew_max = 3
    eq(Packages.compute_state(gs, p, 2)["state"], "active", "active at ideal")
    eq(Packages.compute_state(gs, p, 3)["state"], "over_ideal", "over ideal")
    var q: PackageData = gs.bundle.packages_by_id["P00002"]
    var w: Dictionary = Packages.compute_state(gs, q, 0)
    eq(w["state"], "waiting", "columns wait for the footings")
    ok(String(w["reason"]).begins_with("Waiting for"), "waiting reason: " + str(w["reason"]))
    gs.package_runtime["P00001"].released = false
    eq(Packages.compute_state(gs, p, 0)["state"], "held", "held")
    for t in p.tasks:
        set_finished(gs, t.task_id)
    eq(Packages.compute_state(gs, p, 0)["state"], "done", "done")
    gs.free()


func test_package_state_signal() -> void:
    var gs: SimState = _staffed_state()
    var seen: Array = []
    gs.package_state_changed.connect(func(id: String, st: String) -> void: seen.append([id, st]))
    gs.refresh_states()
    gs.hold_package("P00001")
    ok(seen.has(["P00001", "held"]), "package_state_changed emitted: %s" % str(seen))
    gs.free()
