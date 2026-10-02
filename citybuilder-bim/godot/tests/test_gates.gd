extends TC

const RS := TaskRuntime.State


func test_mep_cannot_start_before_structural_gate() -> void:
    var gs: SimState = new_state()
    # Drop the duct's direct predecessor so only the gate (storey scope) holds it.
    var duct: TaskData = gs.bundle.tasks_by_id["T000010"]
    duct.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.BLOCKED, "duct blocked: L00 superstructure tasks open")
    ok(gs.runtime["T000010"].blocked_reason.begins_with("Gate"), "reason names the gate: " + gs.runtime["T000010"].blocked_reason)
    for id in ["T000001", "T000002", "T000003", "T000004", "T000005", "T000006", "T000007"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.BLOCKED, "still blocked with one column open")
    set_finished(gs, "T000008")
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.READY, "gate opens once all structure on the storey is done")
    gs.free()


func test_gate_needs_inspection_not_just_work() -> void:
    var gs: SimState = new_state()
    var duct: TaskData = gs.bundle.tasks_by_id["T000010"]
    duct.predecessors.clear()
    # Footings (an earlier phase) are done; the cumulative gate also needs them.
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    # Make the column step an inspected step: finished only counts once INSPECTED.
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        var col: TaskData = gs.bundle.tasks_by_id[id]
        col.inspection = true
        col.inspection_type = "structural"
        gs.set_task_state(id, RS.AWAITING_INSPECTION)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.BLOCKED, "awaiting inspection does not open the gate")
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        gs.runtime[id].actual_finish_day = 0
        gs.set_task_state(id, RS.INSPECTED)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.READY, "inspected structure opens the gate")
    gs.free()


func test_cumulative_gate_holds_phase_not_named_by_any_gate() -> void:
    var gs: SimState = new_state()
    # ARC-WALL-BUILD is in phase "interiors": no gate names it, but G-struct (after
    # superstructure, before mep_roughin) is cumulative and so holds every later phase too.
    var wall: TaskData = gs.bundle.tasks_by_id["T000011"]
    wall.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.BLOCKED, "interiors held by the earlier structural gate")
    ok(String(gs.runtime["T000011"].blocked_reason).begins_with("Gate"), "reason: " + gs.runtime["T000011"].blocked_reason)
    for id in ["T000001", "T000002", "T000003", "T000004", "T000005", "T000006", "T000007"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.BLOCKED, "one column still open")
    set_finished(gs, "T000008")
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.READY, "released once everything at or below superstructure is done")
    gs.free()


func test_gate_covers_earlier_phases_not_only_after_phase() -> void:
    var gs: SimState = new_state()
    var duct: TaskData = gs.bundle.tasks_by_id["T000010"]
    duct.predecessors.clear()
    # columns are done, but an earlier-phase (substructure) footing is not: still held
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.BLOCKED, "open substructure task holds the gate")
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000010"), RS.READY, "open once all earlier phases are finished")
    gs.free()


func test_vacuous_gate_in_scope_without_tasks() -> void:
    var gs: SimState = new_state()
    # storey L01 holds only the slab (superstructure); remove it from consideration by giving
    # the gate a scope instance with no tasks at or below after_phase.
    var g: GateDef = GateDef.new()
    g.id = "G-vac"
    g.name = "Vacuous"
    g.after_phase = "substructure"
    g.before_phase = "superstructure"
    g.scope = "storey"
    gs.bundle.gates.clear()
    gs.bundle.gates.append(g)
    var slab: TaskData = gs.bundle.tasks_by_id["T000009"]
    slab.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000009"), RS.READY, "L01 has no substructure tasks: gate passes immediately")
    var col: TaskData = gs.bundle.tasks_by_id["T000005"]
    col.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "L00 has open footings: same gate holds the column")
    gs.free()


func test_project_scope_gate() -> void:
    var gs: SimState = new_state()
    var g: GateDef = GateDef.new()
    g.id = "G-proj"
    g.name = "Project gate"
    g.after_phase = "substructure"
    g.before_phase = "interiors"
    g.scope = "project"
    gs.bundle.gates.clear()  # isolate: only the project gate applies
    gs.bundle.gates.append(g)
    var wall: TaskData = gs.bundle.tasks_by_id["T000011"]
    wall.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.BLOCKED, "project gate: substructure open")
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.READY, "project gate open")
    gs.free()
