extends TC
## The healthcare_manual_demo bundle (bundle with a `manual` block): a zone in manual mode played with the autopilot.

const PATH: String = "res://scenarios/healthcare_manual_demo/sequence.json"
const ZONE: String = "L00-Z3"
const WEEKS: int = 20


func test_manual_zone_holds_only_manual_tasks_and_plays() -> void:
    if not FileAccess.file_exists(PATH):
        ok(true, "healthcare_manual_demo not synced yet: skipped")
        return
    var b := SequenceBundle.load_from_path(PATH)
    ok(b.valid, "demo bundle valid: %s" % ", ".join(b.errors))
    ok(b.manual != null, "manual block parsed")
    ok(b.manual.zones_in_manual_mode.has(ZONE), "manual zone declared")
    ok(not b.recipes.is_empty(), "recipes embedded")
    var zone_tasks: Array = b.tasks_by_zone[ZONE]
    ok(not zone_tasks.is_empty(), "manual zone has tasks")
    var virtual_n: int = 0
    for t in zone_tasks:
        var task: TaskData = t
        ok(task.is_authored() and task.origin == "manual", "only manual tasks in the manual zone: %s (%s)" % [task.task_id, task.origin])
        ok(task.manual_id != "", "manual id kept: %s" % task.task_id)
        if task.is_virtual:
            virtual_n += 1
            eq(task.element_guid, "", "virtual task has no element")
    eq(virtual_n, 2, "two virtual survey tasks")
    eq(b.virtual_tasks.size() >= 2, true, "virtual index")
    var pile: TaskData = b.tasks_by_id[b.resolve_task_id("M0003")]
    eq(pile.element_guids.size(), 8, "the manual pile task binds all eight elements of its manual entry")
    # the zone is in manual mode at start and the chain is played by the autopilot
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    gs.cash += 1.0e9
    ok(gs.manual_zones.has(ZONE), "manual mode at start")
    eq(gs.virtual_markers().size(), b.virtual_tasks.size(), "markers for every virtual task")
    Planner.auto_layout(gs, 4)
    var played: int = 0
    while gs.week < WEEKS and not gs.finished:  # the autopilot stops at events with choices: take the first choice
        var res: Dictionary = Planner.autopilot(gs, WEEKS - gs.week, "ideal", 1.0, true, 8, true)
        played += int(res["weeks_run"])
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        elif int(res["weeks_run"]) == 0:
            break
    eq(gs.week, WEEKS, "played %d weeks" % WEEKS)
    # the chain by manual id (task ids change whenever the bundle is rebuilt)
    var chain: Array[String] = []
    for mid in ["M0001", "M0002", "M0003", "M0004", "M0005", "M0006"]:
        chain.append(gs.bundle.resolve_task_id(mid))
    var done: int = 0
    for id in chain:
        var rt0: TaskRuntime = gs.runtime[id]
        if TaskRuntime.is_finished(rt0.state):
            done += 1
        else:
            print("      [manual-demo] unfinished %s %s state=%s reason='%s' progress=%.2f/%.2f" % [id, gs.bundle.tasks_by_id[id].step_id,
                    TaskRuntime.state_name(rt0.state), rt0.blocked_reason, rt0.progress, rt0.required])
    eq(done, chain.size(), "the whole manual chain is finished after %d weeks (%d of %d)" % [gs.week, done, chain.size()])
    var s: TaskRuntime = gs.runtime[chain[0]]
    var e: TaskRuntime = gs.runtime[chain[1]]
    ok(e.actual_start_day >= s.actual_finish_day, "excavation after the set-out survey")
    ok(gs.runtime[chain[5]].actual_start_day >= gs.runtime[chain[4]].actual_finish_day, "pour after the formwork")
    ok(gs.finished_task_count() > 100, "the rest of the project progressed too: %d tasks" % gs.finished_task_count())
