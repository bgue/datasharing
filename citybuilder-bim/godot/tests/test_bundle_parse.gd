extends TC


func test_minimal_counts() -> void:
    var b: SequenceBundle = load_bundle()
    eq(b.tasks.size(), 12, "task count")
    eq(b.storeys.size(), 2, "storey count")
    eq(b.zones.size(), 2, "zone count")
    eq(b.elements.size(), 11, "element count")
    eq(b.steps.size(), 6, "step count")
    eq(b.trades.size(), 4, "trade count")
    eq(b.gates.size(), 1, "gate count")


func test_indices() -> void:
    var b: SequenceBundle = load_bundle()
    eq((b.tasks_by_zone["L00-Z1"] as Array).size(), 11, "ground zone tasks")
    eq((b.tasks_by_zone["L01-Z1"] as Array).size(), 1, "level 1 zone tasks")
    eq((b.tasks_by_element["DUCT1"] as Array).size(), 2, "duct has two tasks")
    eq((b.successors_by_task["T000009"] as Array).size(), 2, "slab has duct+wall successors")
    eq((b.successors_by_task["T000001"] as Array).size(), 1, "footing -> column")
    eq(b.storey_index_by_id["L01"], 1, "storey index")
    ok(b.steps_by_id.has("STR-SLAB-POUR"), "steps_by_id")
    ok(b.zones_by_id.has("L01-Z1"), "zones_by_id")
    ok(b.storeys_by_id.has("L00"), "storeys_by_id")
    eq(b.zones_covering("L00", Vector2i(2, 2)).size(), 1, "zones covering cell on ground")
    eq(b.zones_covering("L00", Vector2i(7, 5)).size(), 0, "no zone off-site")
    eq(b.element_discipline("DUCT1"), "mechanical", "discipline from first task")


func test_derived_values() -> void:
    var b: SequenceBundle = load_bundle()
    eq(b.contract_weeks(), 6, "contract weeks from scenario")
    near(b.contract_budget(), 120000.0, "budget")
    near(b.total_task_cost(), 27080.0, "total task cost")
    near(b.storey_y("L01"), 4.0 / 6.0, "storey y in grid units")
    eq(b.scenario.gates[0], Vector2i(0, 2), "gate cell")
    eq(b.scenario.events.size(), 2, "events")
    var t: TaskData = b.tasks_by_id["T000009"]
    eq(t.predecessors.size(), 4, "slab predecessors")
    ok(t.inspection, "slab inspection flag")


func test_invalid_bundle() -> void:
    var b := SequenceBundle.from_dictionary({"project": {}})
    ok(not b.valid, "missing keys -> invalid")
