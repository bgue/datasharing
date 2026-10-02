class_name Safety
extends RefCounted
## Weekly incident probability and consequences (docs/01 section 5.5).

const HOARDING_PENALTY: float = 1.5
const OVERTIME_PENALTY: float = 1.2
const MAX_PROBABILITY: float = 0.95


static func task_risk_term(gs: SimState, task: TaskData) -> float:
    var zone: ZoneData = gs.bundle.zones_by_id.get(task.zone_id, null)
    var cong: float = Productivity.zone_congestion(gs, task.zone_id)
    var congestion_penalty: float = 1.0 / maxf(cong, 0.01)
    var hoarding: float = 1.0
    if zone != null and not Logistics.has_tile_adjacent(gs.tiles, zone.cells, SiteTiles.HOARDING):
        hoarding = HOARDING_PENALTY
    var overtime: float = OVERTIME_PENALTY if gs.speed > 1 else 1.0
    var shift: float = gs.scenario.shift_risk_factor if gs.is_double_shift(task.zone_id) else 1.0
    return task.risk * 0.01 * congestion_penalty * hoarding * overtime * shift


static func incident_probability_for(gs: SimState, task_ids: Array[String]) -> float:
    var p: float = 0.0
    for tid in task_ids:
        p += task_risk_term(gs, gs.bundle.tasks_by_id[tid])
    return clampf(p, 0.0, MAX_PROBABILITY)


## Probability for the tasks worked in the current week.
static func incident_probability(gs: SimState) -> float:
    return incident_probability_for(gs, gs.worked_this_week)


static func run_week(gs: SimState) -> void:
    var p: float = incident_probability(gs)
    var roll: float = gs.rng.randf()
    if gs.worked_this_week.is_empty() or roll >= p:
        return
    var idx: int = gs.rng.randi() % gs.worked_this_week.size()
    var task: TaskData = gs.bundle.tasks_by_id[gs.worked_this_week[idx]]
    trigger_incident(gs, task.zone_id)


static func trigger_incident(gs: SimState, zone_id: String) -> void:
    gs.incidents += 1
    gs.incident_log.append({"week": gs.week, "zone_id": zone_id})
    gs.zone_paused_until[zone_id] = gs.week + 2
    var z: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    gs.log_event("INCIDENT in %s: zone stopped for a week" % (z.name if z != null else zone_id))
    gs.incident_occurred.emit(zone_id)
