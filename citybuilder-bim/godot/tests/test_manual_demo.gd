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
    var pile: TaskData = b.tasks_by_id["T000156"]
    eq(pile.element_guids.size(), 8, "the manual pile task binds all eight elements of its manual entry")
    # the zone is in manual mode at start and the chain is played by the autopilot
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    gs.cash += 1.0e9
    ok(gs.manual_zones.has(ZONE), "manual mode at start")
    eq(gs.virtual_markers().size(), b.virtual_tasks.size(), "markers for every virtual task")
    Planner.auto_layout(gs, 4)
    var res: Dictionary = Planner.autopilot(gs, WEEKS, "ideal", 1.0, true, 8, true)
    ok(int(res["weeks_run"]) > 0, "weeks played")
    var chain: Array[String] = ["T000157", "T000155", "T000156", "T000158", "T000153", "T000154"]
    var done: int = 0
    for id in chain:
        if TaskRuntime.is_finished(state_of(gs, id)):
            done += 1
    eq(done, chain.size(), "the whole manual chain is finished after %d weeks (%d of %d)" % [gs.week, done, chain.size()])
    var s: TaskRuntime = gs.runtime["T000157"]
    var e: TaskRuntime = gs.runtime["T000155"]
    ok(e.actual_start_day >= s.actual_finish_day, "excavation after the set-out survey")
    ok(gs.runtime["T000154"].actual_start_day >= gs.runtime["T000153"].actual_finish_day, "pour after the formwork")
    ok(gs.finished_task_count() > 100, "the rest of the project progressed too: %d tasks" % gs.finished_task_count())
