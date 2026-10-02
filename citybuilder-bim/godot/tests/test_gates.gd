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


func test_gate_scope_storey_ignores_other_storeys() -> void:
    var gs: SimState = new_state()
    var wall: TaskData = gs.bundle.tasks_by_id["T000011"]  # interiors phase, no gate before it
    wall.predecessors.clear()
    gs.refresh_states()
    eq(state_of(gs, "T000011"), RS.READY, "no gate defined before interiors -> ready")
    gs.free()


func test_project_scope_gate() -> void:
    var gs: SimState = new_state()
    var g: GateDef = GateDef.new()
    g.id = "G-proj"
    g.name = "Project gate"
    g.after_phase = "substructure"
    g.before_phase = "interiors"
    g.scope = "project"
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
