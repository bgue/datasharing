class_name TaskStore
extends RefCounted
## Structure-of-arrays storage of the mutable per-task simulation state (docs/06 C.3: PackedArrays indexed by task
## index, task id -> index Dictionary). `SimState.runtime` is a TaskStore: `runtime[task_id]` returns a thin
## `TaskRuntime` view (cached per task, so identity is stable) with the old field names, so UI, API and tests keep
## using `rt.state`, `rt.progress`, ... while the hot simulation loops read the arrays through `idx_of(id)`.
##
## The store also keeps the sets and counters the per-day loop needs without scanning every task: state counts,
## the ACTIVE / REWORK set, the AWAITING_INSPECTION set, finished-but-unpaid tasks, ordered long-lead tasks, plus
## a change log (task indices whose state / progress / blocked reason changed) that views and package refreshes
## consume with their own cursor (`changes_since`).

const S := TaskRuntime.State
const LOG_CAP: int = 1 << 20

var ids: PackedStringArray = PackedStringArray()
var index: Dictionary = {}  # task id -> slot
var alive: PackedByteArray = PackedByteArray()
var task_refs: Array[TaskData] = []  # slot -> TaskData (null for removed slots)
var _views: Array = []  # slot -> TaskRuntime (lazy)

var state: PackedInt32Array = PackedInt32Array()
var progress: PackedFloat64Array = PackedFloat64Array()
var required: PackedFloat64Array = PackedFloat64Array()
var actual_start_day: PackedInt32Array = PackedInt32Array()
var actual_finish_day: PackedInt32Array = PackedInt32Array()
var work_done_day: PackedInt32Array = PackedInt32Array()
var inspection_due_day: PackedInt32Array = PackedInt32Array()
var earliest_start: PackedInt32Array = PackedInt32Array()
var inspection_failures: PackedInt32Array = PackedInt32Array()
var rework_days_added: PackedFloat64Array = PackedFloat64Array()
var delivery_week: PackedInt32Array = PackedInt32Array()
var reason: PackedStringArray = PackedStringArray()
## Slot of the predecessor that held the task back at its last evaluation (-1: none, i.e. not blocked by a
## predecessor). Finishing or starting any other predecessor cannot change the verdict, see Readiness.note_changed.
var blocker: PackedInt32Array = PackedInt32Array()
## Gate that holds the task (index into bundle.gates, -1 none) and its open-task count when the reason was written: a gate
## whose count did not move cannot release the task (Readiness.refresh_incremental skips it).
var gate_idx: PackedInt32Array = PackedInt32Array()
var gate_cnt: PackedInt32Array = PackedInt32Array()
## bit 0 ordered, bit 1 paid, bit 2 worked_double
var flags: PackedByteArray = PackedByteArray()

var counts: PackedInt32Array = PackedInt32Array()  # tasks per State
var active: Dictionary = {}  # slot -> true for ACTIVE / REWORK
var awaiting: Dictionary = {}  # slot -> true for AWAITING_INSPECTION
var ready: Dictionary = {}  # slot -> true for READY
var unpaid: Dictionary = {}  # slot -> true for finished tasks not paid yet
var ordered: Dictionary = {}  # slot -> true for ordered long-lead tasks
var live_count: int = 0
## Per zone: tasks per state, READY tasks with an impediment and BLOCKED tasks held by a gate / delivery (zone_status).
var zone_index: Dictionary = {}  # zone id -> zone slot
var zone_of: PackedInt32Array = PackedInt32Array()  # task slot -> zone slot
var zone_counts: PackedInt32Array = PackedInt32Array()  # zone slot * 8 + state
var zone_impeded: PackedInt32Array = PackedInt32Array()
var zone_gate: PackedInt32Array = PackedInt32Array()
var cls: PackedByteArray = PackedByteArray()  # task slot -> 0 none / 1 impeded / 2 gate or delivery hold
## Called as listener.call(slot, old_state, new_state) after every state change (SimState: element visuals, readiness
## notes, the task_state_changed signal), unless `silent` (bulk loads).
var listener: Callable = Callable()
var silent: bool = false
## Callable(slot, stored_reason) -> String: the reason of a task held by a gate with the gate's current open-task count
## (TaskRuntime.blocked_reason): the counts move every week and rewriting every held task's text would cost more than
## reading it on demand.
var gate_live: Callable = Callable()
## Per-package caches kept coherent here because every task change passes `_note`: `work_cache` (package id ->
## [has active work, has ready work], see Packages.has_work) and `touched_pkgs` (packages whose tasks changed since
## Packages.refresh_states last drained it).
var work_cache: Dictionary = {}
var touched_pkgs: Dictionary = {}
## Per (storey, phase) group: [total, finished, started] (started = ACTIVE / AWAITING_INSPECTION / REWORK).
var group_index: Dictionary = {}  # "storey|phase" -> group
var group_keys: Array[PackedStringArray] = []  # group -> [storey_id, phase]
var group_of: PackedInt32Array = PackedInt32Array()
var group_counts: PackedInt32Array = PackedInt32Array()

var _log: PackedInt32Array = PackedInt32Array()
var _log_base: int = 0
## Bumped whenever the log was cut (consumers behind it must rebuild from scratch).
var log_epoch: int = 0

const F_ORDERED: int = 1
const F_PAID: int = 2
const F_DOUBLE: int = 4


func _init() -> void:
    counts.resize(TaskRuntime.STATE_NAMES.size())


func clear() -> void:
    ids = PackedStringArray()
    index.clear()
    alive = PackedByteArray()
    task_refs = []
    _views = []
    state = PackedInt32Array()
    actual_start_day = PackedInt32Array()
    actual_finish_day = PackedInt32Array()
    work_done_day = PackedInt32Array()
    inspection_due_day = PackedInt32Array()
    earliest_start = PackedInt32Array()
    inspection_failures = PackedInt32Array()
    delivery_week = PackedInt32Array()
    progress = PackedFloat64Array()
    required = PackedFloat64Array()
    rework_days_added = PackedFloat64Array()
    reason = PackedStringArray()
    blocker = PackedInt32Array()
    gate_idx = PackedInt32Array()
    gate_cnt = PackedInt32Array()
    flags = PackedByteArray()
    counts.fill(0)
    zone_index.clear()
    zone_of = PackedInt32Array()
    zone_counts = PackedInt32Array()
    zone_impeded = PackedInt32Array()
    zone_gate = PackedInt32Array()
    cls = PackedByteArray()
    group_index.clear()
    group_keys = []
    group_of = PackedInt32Array()
    group_counts = PackedInt32Array()
    active.clear()
    awaiting.clear()
    ready.clear()
    unpaid.clear()
    ordered.clear()
    live_count = 0
    _log = PackedInt32Array()
    _log_base = 0
    log_epoch += 1
    work_cache.clear()
    touched_pkgs.clear()


## Adds a task (NOT_STARTED); returns its slot.
func add_task(task: TaskData) -> int:
    var i: int = ids.size()
    ids.append(task.task_id)
    index[task.task_id] = i
    alive.append(1)
    task_refs.append(task)
    _views.append(null)
    state.append(S.NOT_STARTED)
    progress.append(0.0)
    required.append(task.estimated_crew_days)
    actual_start_day.append(-1)
    actual_finish_day.append(-1)
    work_done_day.append(-1)
    inspection_due_day.append(-1)
    earliest_start.append(-1)
    inspection_failures.append(0)
    rework_days_added.append(0.0)
    delivery_week.append(-1)
    reason.append("")
    blocker.append(-1)
    gate_idx.append(-1)
    gate_cnt.append(0)
    flags.append(0)
    counts[S.NOT_STARTED] += 1
    live_count += 1
    var zi: int = int(zone_index.get(task.zone_id, -1))
    if zi < 0:
        zi = zone_index.size()
        zone_index[task.zone_id] = zi
        zone_counts.resize((zi + 1) * 8)
        zone_impeded.resize(zi + 1)
        zone_gate.resize(zi + 1)
    zone_of.append(zi)
    cls.append(0)
    zone_counts[zi * 8 + S.NOT_STARTED] += 1
    var gk: String = "%s|%s" % [task.storey_id, task.phase]
    var g: int = int(group_index.get(gk, -1))
    if g < 0:
        g = group_keys.size()
        group_index[gk] = g
        group_keys.append(PackedStringArray([task.storey_id, task.phase]))
        group_counts.resize((g + 1) * 3)
    group_of.append(g)
    group_counts[g * 3] += 1
    return i


func remove_task(task_id: String) -> void:
    var i: int = int(index.get(task_id, -1))
    if i < 0:
        return
    _unlink(i)
    index.erase(task_id)
    alive[i] = 0
    task_refs[i] = null
    _views[i] = null
    counts[state[i]] -= 1
    live_count -= 1
    var zr: int = zone_of[i]
    zone_counts[zr * 8 + state[i]] -= 1
    if cls[i] == 1:
        zone_impeded[zr] -= 1
    elif cls[i] == 2:
        zone_gate[zr] -= 1
    cls[i] = 0
    var g: int = group_of[i]
    group_counts[g * 3] -= 1
    var cat: int = _group_cat(state[i])
    if cat > 0:
        group_counts[g * 3 + cat] -= 1


func _unlink(i: int) -> void:
    active.erase(i)
    awaiting.erase(i)
    ready.erase(i)
    unpaid.erase(i)
    ordered.erase(i)


# ------------------------------------------------------------------ Dictionary-like access (the old `runtime` API)

func has(task_id: String) -> bool:
    return index.has(task_id)


func size() -> int:
    return live_count


func slot_count() -> int:
    return ids.size()


func idx_of(task_id: String) -> int:
    return int(index.get(task_id, -1))


## The TaskRuntime view of a task, null for an unknown id.
func get_rt(task_id: String) -> TaskRuntime:
    var i: int = int(index.get(task_id, -1))
    return null if i < 0 else view_at(i)


func view_at(i: int) -> TaskRuntime:
    var v: Variant = _views[i]
    if v == null:
        var rt := TaskRuntime.new()
        rt.store = self
        rt.idx = i
        _views[i] = rt
        return rt
    return v


func erase(task_id: String) -> void:
    remove_task(task_id)


func _get(property: StringName) -> Variant:
    var i: int = int(index.get(String(property), -1))
    if i < 0:
        return null
    return view_at(i)


## `for task_id in runtime` (live tasks in slot order).
var _it: int = 0


func _iter_init(_arg: Variant) -> bool:
    _it = 0
    while _it < ids.size() and alive[_it] == 0:
        _it += 1
    return _it < ids.size()


func _iter_next(_arg: Variant) -> bool:
    _it += 1
    while _it < ids.size() and alive[_it] == 0:
        _it += 1
    return _it < ids.size()


func _iter_get(_arg: Variant) -> Variant:
    return ids[_it]


## ACTIVE / REWORK slots in ascending (task) order.
func active_sorted() -> Array:
    var out: Array = active.keys()
    out.sort()
    return out


func keys() -> Array:
    var out: Array = []
    for i in ids.size():
        if alive[i] != 0:
            out.append(ids[i])
    return out


# ------------------------------------------------------------------ writes (counters, sets and the change log)

func set_state(i: int, new_state: int) -> void:
    var old: int = state[i]
    if old == new_state:
        return
    state[i] = new_state
    counts[old] -= 1
    counts[new_state] += 1
    var zs: int = zone_of[i] * 8
    zone_counts[zs + old] -= 1
    zone_counts[zs + new_state] += 1
    _reclass(i)
    var g3: int = group_of[i] * 3
    var oc: int = _group_cat(old)
    var nc: int = _group_cat(new_state)
    if oc != nc:
        if oc > 0:
            group_counts[g3 + oc] -= 1
        if nc > 0:
            group_counts[g3 + nc] += 1
    var was_work: bool = old == S.ACTIVE or old == S.REWORK
    var is_work: bool = new_state == S.ACTIVE or new_state == S.REWORK
    if is_work and not was_work:
        active[i] = true
    elif was_work and not is_work:
        active.erase(i)
    if new_state == S.AWAITING_INSPECTION:
        awaiting[i] = true
    elif old == S.AWAITING_INSPECTION:
        awaiting.erase(i)
    if new_state == S.READY:
        ready[i] = true
    elif old == S.READY:
        ready.erase(i)
    var was_fin: bool = old == S.DONE or old == S.INSPECTED
    var is_fin: bool = new_state == S.DONE or new_state == S.INSPECTED
    if is_fin and not was_fin:
        if (flags[i] & F_PAID) == 0:
            unpaid[i] = true
    elif was_fin and not is_fin:
        unpaid.erase(i)
    _note(i, true, not (old <= S.BLOCKED and new_state <= S.BLOCKED))
    if not silent and listener.is_valid():
        listener.call(i, old, new_state)


static func _class_of(st: int, why: String) -> int:
    if st == S.READY and why != "":
        return 1
    if st == S.BLOCKED and (why.begins_with("Gate") or why.begins_with("Long-lead") or why.begins_with("Delivery")):
        return 2
    return 0


## Keeps the per-zone impeded / held counters in step with a task's state and reason.
func _reclass(i: int) -> void:
    var nw: int = _class_of(state[i], reason[i])
    var old: int = cls[i]
    if nw == old:
        return
    var z: int = zone_of[i]
    if old == 1:
        zone_impeded[z] -= 1
    elif old == 2:
        zone_gate[z] -= 1
    if nw == 1:
        zone_impeded[z] += 1
    elif nw == 2:
        zone_gate[z] += 1
    cls[i] = nw


## 1 = finished, 2 = started (in progress or awaiting inspection), 0 = neither.
static func _group_cat(s: int) -> int:
    if s == S.DONE or s == S.INSPECTED:
        return 1
    if s == S.ACTIVE or s == S.AWAITING_INSPECTION or s == S.REWORK:
        return 2
    return 0


func set_progress(i: int, v: float) -> void:
    progress[i] = v
    _note(i, false, true)


func set_reason(i: int, s: String) -> void:
    if reason[i] != s:
        reason[i] = s
        _reclass(i)
        _note(i, true, false)


func set_flag(i: int, bit: int, on: bool) -> void:
    if on:
        flags[i] = flags[i] | bit
    else:
        flags[i] = flags[i] & ~bit
    if bit == F_ORDERED:
        if on:
            ordered[i] = true
        else:
            ordered.erase(i)
    elif bit == F_PAID:
        if on:
            unpaid.erase(i)
        elif state[i] == S.DONE or state[i] == S.INSPECTED:
            unpaid[i] = true


func is_finished_at(i: int) -> bool:
    var s: int = state[i]
    return s == S.DONE or s == S.INSPECTED


func finished_count() -> int:
    return counts[S.DONE] + counts[S.INSPECTED]


## `structural` = a state or blocked-reason change (drops the package's has_work flags); progress alone does not.
## `visual` = something the views draw changed (progress, or a state change beyond NOT_STARTED / READY / BLOCKED flips):
## only those go into the change log the views read.
func _note(i: int, structural: bool = true, visual: bool = true) -> void:
    var pid: String = task_refs[i].package_id
    if structural:
        work_cache.erase(pid)
    touched_pkgs[pid] = true
    if not visual:
        return
    _log.append(i)
    if _log.size() > LOG_CAP:
        var cut: int = LOG_CAP / 2
        _log = _log.slice(cut)
        _log_base += cut
        log_epoch += 1


# ------------------------------------------------------------------ change log

func log_end() -> int:
    return _log_base + _log.size()


## Slots touched since `cursor` (an earlier log_end()); null when the log was cut past the cursor (rebuild instead).
func changes_since(cursor: int) -> Variant:
    if cursor < _log_base:
        return null
    return _log.slice(cursor - _log_base)


## Distinct slots (Dictionary slot -> true) touched since `cursor`, or null (see changes_since).
func changed_set_since(cursor: int) -> Variant:
    var list: Variant = changes_since(cursor)
    if list == null:
        return null
    var out: Dictionary = {}
    for i in (list as PackedInt32Array):
        out[i] = true
    return out
