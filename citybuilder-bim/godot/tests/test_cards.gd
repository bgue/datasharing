extends TC
## Takt sequence cards and trains (docs/05 section 3).

const RS := TaskRuntime.State


func _card(extra_stations: Array = []) -> SequenceCardData:
    var stations: Array = [
        {"name": "Foundations", "select": {"phase": "substructure"}, "takt_weeks": 1},
        {"name": "Frame", "select": {"phase": "superstructure"}, "takt_weeks": 1},
        {"name": "MEP", "select": {"phase": "mep_roughin"}, "takt_weeks": 2},
    ]
    stations.append_array(extra_stations)
    return SequenceCardData.from_dict({"id": "card_test", "name": "Test card", "stations": stations, "auto_staff": "ideal"})


func _state_with_card(extra: Array = []) -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.bundle.cards_by_id["card_test"] = _card(extra)
    gs.bundle.card_ids.append("card_test")
    return gs


func _released(gs: SimState, id: String) -> bool:
    return (gs.package_runtime[id] as PackageRuntime).released


func test_apply_assigns_stations_and_holds_later_ones() -> void:
    var gs: SimState = _state_with_card()
    ok(Cards.apply(gs, "L00-Z1", "card_test"), "apply")
    eq((gs.package_runtime["P00001"] as PackageRuntime).station_index, 0, "footings: station 0")
    eq((gs.package_runtime["P00002"] as PackageRuntime).station_index, 1, "columns: station 1")
    eq((gs.package_runtime["P00003"] as PackageRuntime).station_index, 2, "duct: station 2")
    eq((gs.package_runtime["P00004"] as PackageRuntime).station_index, 3, "wall: implicit trailing station Other")
    eq((gs.package_runtime["P00005"] as PackageRuntime).station_index, 3, "commissioning: Other")
    ok(_released(gs, "P00001"), "current station released")
    for id in ["P00002", "P00003", "P00004", "P00005"]:
        ok(not _released(gs, id), "%s held (later station)" % id)
        eq(gs.package_runtime[id].state, "held", "%s shows held" % id)
    ok(_released(gs, "P00006"), "other zones unaffected")
    var v: Dictionary = ApiViews.zone_view(gs, gs.bundle.zones_by_id["L00-Z1"])
    eq(v["card"], "card_test", "zone view shows the card")
    eq(v["station"], "Foundations", "and the station")
    gs.free()


func test_station_advances_when_its_packages_are_done() -> void:
    var gs: SimState = _state_with_card()
    Cards.apply(gs, "L00-Z1", "card_test")
    for t in gs.bundle.packages_by_id["P00001"].tasks:
        set_finished(gs, t.task_id)
    Cards.sync_zone(gs, "L00-Z1")
    var zr: ZoneRuntime = gs.zone_runtime["L00-Z1"]
    eq(zr.station_index, 1, "advanced to Frame")
    ok(_released(gs, "P00002"), "Frame package released")
    ok(not _released(gs, "P00003"), "MEP still held")
    for t in gs.bundle.packages_by_id["P00002"].tasks:
        set_finished(gs, t.task_id)
    Cards.sync_zone(gs, "L00-Z1")
    eq(zr.station_index, 2, "advanced to MEP")
    gs.free()


func test_empty_stations_are_skipped_and_card_completes() -> void:
    var gs: SimState = _state_with_card([{"name": "Handover", "select": {"phase": "handover"}}])
    Cards.apply(gs, "L01-Z1", "card_test")  # only the slab (superstructure) lives here
    var zr: ZoneRuntime = gs.zone_runtime["L01-Z1"]
    eq(zr.station_index, 1, "foundations station is empty: skipped straight to Frame")
    set_finished(gs, "T000009")
    Cards.sync_zone(gs, "L01-Z1")
    ok(zr.card_done, "all stations done, including the empty ones")
    gs.free()


func test_held_station_packages_do_not_work() -> void:
    var gs: SimState = _state_with_card()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 5
    build_road(gs)
    Cards.apply(gs, "L00-Z1", "card_test", "off")
    gs.bundle.tasks_by_id["T000010"].predecessors.clear()
    gs.bundle.gates.clear()
    gs.refresh_states()
    gs.scenario.crews_available["mechanical"] = 1
    gs.assign_crew(gs.hire("mechanical"), "L00-Z1")
    gs.run_work_day(0)
    eq(state_of(gs, "T000010"), RS.READY, "MEP is a later station: nothing starts")
    gs.free()


func test_auto_staff_moves_idle_crews_and_releases_useless_ones() -> void:
    var gs: SimState = _state_with_card()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 5
    build_road(gs)  # without access nothing in the zone is workable, so nothing is staffed
    Cards.apply(gs, "L00-Z1", "card_test")
    gs.scenario.crews_available["mechanical"] = 1
    var concrete: int = gs.hire("concrete")  # idle, unassigned
    var mech: int = gs.hire("mechanical")
    gs.assign_crew(mech, "L00-Z1")  # nothing for it in this station
    Cards.update(gs)
    eq(str(gs.crew_by_id(concrete)["zone_id"]), "L00-Z1", "auto_staff moved the idle concrete crew into the zone")
    eq(str(gs.crew_by_id(mech)["zone_id"]), "", "and released the crew with nothing to do")
    gs.zone_runtime["L00-Z1"].auto_staff = "off"
    var c2: int = gs.hire("concrete")
    Cards.update(gs)
    eq(str(gs.crew_by_id(c2)["zone_id"]), "", "auto_staff off: crews stay where they are")
    eq(gs.crews.size(), 3, "hiring is never automatic")
    gs.free()


func test_behind_takt_flag() -> void:
    var gs: SimState = _state_with_card()
    Cards.apply(gs, "L00-Z1", "card_test")
    var zr: ZoneRuntime = gs.zone_runtime["L00-Z1"]
    ok(not zr.behind_takt, "on time at the start")
    gs.week = 1
    Cards.sync_zone(gs, "L00-Z1")
    ok(not zr.behind_takt, "takt is 1 week: exactly 1 week elapsed is still on time")
    gs.week = 2
    Cards.sync_zone(gs, "L00-Z1")
    ok(zr.behind_takt, "2 weeks on a 1-week station: behind takt")
    ok(gs.zone_status("L00-Z1")["behind_takt"], "zone status carries the flag")
    ok(ApiViews.package_view(gs, gs.bundle.packages_by_id["P00001"])["behind_takt"], "and the current station's package view")
    ok(not ApiViews.package_view(gs, gs.bundle.packages_by_id["P00002"])["behind_takt"], "later stations are not behind")
    ok(_released(gs, "P00001"), "nothing is forced")
    gs.free()


func test_train_stagger_holds_later_zones() -> void:
    var gs: SimState = _state_with_card()
    ok(Cards.train(gs, "card_test", ["L00-Z1", "L01-Z1"], 2), "train")
    ok(_released(gs, "P00001"), "zone 0 starts at once")
    ok(not _released(gs, "P00006"), "zone 1's slab package is held by the stagger")
    eq(gs.zone_runtime["L01-Z1"].hold_until_week, 2, "hold until week + k * stagger")
    gs.week = 1
    Cards.sync_zone(gs, "L01-Z1")
    ok(not _released(gs, "P00006"), "still held in week 1")
    gs.week = 2
    Cards.sync_zone(gs, "L01-Z1")
    ok(_released(gs, "P00006"), "released from week 2")
    gs.free()


func test_clear_releases_everything_and_leaves_crews() -> void:
    var gs: SimState = _state_with_card()
    Cards.apply(gs, "L00-Z1", "card_test")
    var c: int = gs.hire("concrete")
    gs.assign_crew(c, "L00-Z1")
    ok(Cards.clear(gs, "L00-Z1"), "clear")
    for id in ["P00001", "P00002", "P00003", "P00004", "P00005"]:
        ok(_released(gs, id), "%s released" % id)
    eq(str(gs.crew_by_id(c)["zone_id"]), "L00-Z1", "crews stay put")
    eq(gs.zone_runtime["L00-Z1"].card_id, "", "card removed")
    ok(not Cards.apply(gs, "L00-Z1", "card_nope"), "unknown card refused")
    gs.free()


func test_scenario_cards_override_library_cards_by_id() -> void:
    var d: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(MINIMAL_PATH))
    d["step_library"]["sequence_cards"] = [{"id": "card_a", "name": "Library A", "stations": [{"name": "S", "select": {"phase": "x"}}]},
        {"id": "card_b", "name": "Library B", "stations": [{"name": "S", "select": {"phase": "x"}}]}]
    d["scenario"]["sequence_cards"] = [{"id": "card_b", "name": "Scenario B", "stations": [{"name": "S1", "select": {"phase": "y"}, "takt_weeks": 3}]},
        {"id": "card_c", "name": "Scenario C", "stations": [{"name": "S", "select": {"trade": "t"}}]}]
    var b := SequenceBundle.from_dictionary(d)
    eq(b.card_ids, ["card_a", "card_b", "card_c"], "library cards, overridden and extended by id")
    eq(b.cards_by_id["card_b"].name, "Scenario B", "scenario overrides the library card")
    eq(b.cards_by_id["card_b"].stations[0].takt_weeks, 3, "with its own stations")
    eq(b.cards_by_id["card_a"].auto_staff, "ideal", "auto_staff default")
    eq(b.cards_by_id["card_c"].stations[0].takt_weeks, 1, "takt default 1 week")


func test_station_selectors_are_anded() -> void:
    var gs: SimState = new_state()
    var st := StationData.from_dict({"name": "x", "select": {"phase": "superstructure", "trade": "concrete", "work_face": "any"}})
    ok(st.matches(gs.bundle.packages_by_id["P00002"]), "all selectors match")
    var st2 := StationData.from_dict({"name": "x", "select": {"phase": "superstructure", "trade": "finishes"}})
    ok(not st2.matches(gs.bundle.packages_by_id["P00002"]), "one mismatch rejects")
    var st3 := StationData.from_dict({"name": "x", "select": {"discipline": "structure"}})
    ok(st3.matches(gs.bundle.packages_by_id["P00001"]), "discipline selector")
    gs.free()
