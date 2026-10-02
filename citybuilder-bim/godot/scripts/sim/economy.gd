class_name Economy
extends RefCounted
## Weekly outflow (crews, equipment, tile rent), progress payments with retention,
## overdraft fee and the bankruptcy counter (docs/01 section 5.4).

const OVERDRAFT_FEE_RATE: float = 0.02
const OVERDRAFT_GAME_OVER_WEEKS: int = 4


static func crew_cost(gs: SimState) -> float:
    var s: float = 0.0
    for c in gs.crews:
        var td: TradeDef = gs.bundle.trades_by_id.get(str(c["trade"]), null)
        if td != null:
            s += td.weekly_cost
    return s


static func equipment_cost(gs: SimState) -> float:
    var s: float = 0.0
    for e in gs.equipment_placed:
        var def: EquipmentDef = gs.equipment_def(str(e["id"]))
        if def != null:
            s += def.weekly_cost
    return s


static func tile_rent(gs: SimState) -> float:
    var s: float = 0.0
    for c in gs.tiles:
        s += gs.scenario.tile_weekly_cost(str((gs.tiles[c] as Dictionary).get("tile", "")))
    return s


static func weekly_outflow(gs: SimState) -> Dictionary:
    var crews: float = crew_cost(gs)
    var eq: float = equipment_cost(gs)
    var rent: float = tile_rent(gs)
    return {"crews": crews, "equipment": eq, "rent": rent, "total": crews + eq + rent}


## Earned value is paid on the contract value: payment = task cost * (budget / sum of task cost).
static func payment_factor(gs: SimState) -> float:
    var total: float = gs.bundle.total_task_cost()
    if total <= 0.0:
        return 1.0
    return gs.bundle.contract_budget() / total


## Gross value of finished-but-unpaid tasks.
static func unpaid_earned_value(gs: SimState) -> float:
    var s: float = 0.0
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if TaskRuntime.is_finished(rt.state) and not rt.paid:
            s += t.cost
    return s * payment_factor(gs)


static func is_payment_week(gs: SimState) -> bool:
    return (gs.week + 1) % gs.scenario.progress_payment_every_weeks == 0


## Pays out finished tasks now (minus retention). Returns net cash received.
static func pay_progress(gs: SimState) -> float:
    var gross: float = unpaid_earned_value(gs)
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if TaskRuntime.is_finished(rt.state) and not rt.paid:
            rt.paid = true
    if gross <= 0.0:
        return 0.0
    var retention: float = gross * gs.scenario.retention_pct / 100.0
    var net: float = gross - retention
    gs.retention_held += retention
    gs.cash += net
    gs.payments_received += net
    gs.log_event("Progress payment received: %s (retention held %s)" % [Fmt.money(net), Fmt.money(retention)])
    return net


static func overdraft_fee(cash: float) -> float:
    if cash >= 0.0:
        return 0.0
    return -cash * OVERDRAFT_FEE_RATE


static func run_week(gs: SimState) -> void:
    var out: Dictionary = weekly_outflow(gs)
    if float(out["total"]) > 0.0:
        gs.spend(float(out["total"]), "weekly crews, equipment and rent")
    if is_payment_week(gs):
        pay_progress(gs)
    var fee: float = overdraft_fee(gs.cash)
    if fee > 0.0:
        gs.spend(fee, "overdraft fee")
        gs.overdraft_fees_total += fee
    if gs.cash < -gs.scenario.overdraft_limit:
        gs.negative_weeks += 1
    else:
        gs.negative_weeks = 0
