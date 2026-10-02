class_name ScenarioData
extends RefCounted
## Playable level definition (scenario.schema.json).

var id: String = ""
var name: String = ""
var description: String = ""
var sector: String = ""
var difficulty: String = "tutorial"
var contract_weeks: int = -1  # -1 = derive from baseline
var contract_factor: float = 1.1
var budget: float = 0.0
var budget_factor: float = 1.15
var start_cash: float = 0.0
var overdraft_limit: float = 0.0
var progress_payment_every_weeks: int = 4
var retention_pct: float = 5.0
var start_week_of_year: int = 0
var monthly_factor: Array[float] = []
var crews_available: Dictionary = {}  # trade -> int
var equipment: Array[EquipmentDef] = []
var gates: Array[Vector2i] = []
var occupied_cells: Array[Vector2i] = []
var blocked_cells: Array[Vector2i] = []
## Array of {tile: String, cell: Vector2i, orientation: int}
var initial_tiles: Array[Dictionary] = []
var tile_costs: Dictionary = {}  # tile -> {place, weekly}
var events: Array[EventDef] = []
var score_weights: Dictionary = {"time": 35.0, "cost": 25.0, "safety": 20.0, "quality": 10.0, "stability": 10.0}
var max_incidents_hard: int = 3
var grade_thresholds: Dictionary = {"S": 90.0, "A": 75.0, "B": 60.0, "C": 40.0}
var hints: Array[Dictionary] = []  # {week, text}


static func from_dict(d: Dictionary) -> ScenarioData:
    var s := ScenarioData.new()
    s.id = str(d.get("id", ""))
    s.name = str(d.get("name", s.id))
    s.description = str(d.get("description", ""))
    s.sector = str(d.get("sector", ""))
    s.difficulty = str(d.get("difficulty", "tutorial"))
    var cw: Variant = d.get("contract_weeks", null)
    s.contract_weeks = -1 if cw == null else int(cw)
    s.contract_factor = float(d.get("contract_factor", 1.1))
    s.budget = float(d.get("budget", 0.0))
    s.budget_factor = float(d.get("budget_factor", 1.15))
    s.start_cash = float(d.get("start_cash", 0.0))
    s.overdraft_limit = float(d.get("overdraft_limit", 0.0))
    s.progress_payment_every_weeks = maxi(1, int(d.get("progress_payment_every_weeks", 4)))
    s.retention_pct = float(d.get("retention_pct", 5.0))
    var w: Dictionary = d.get("weather", {})
    s.start_week_of_year = int(w.get("start_week_of_year", 0))
    var mf: Variant = w.get("monthly_factor", [])
    if mf is Array:
        for f in mf:
            s.monthly_factor.append(float(f))
    s.crews_available = {}
    var ca: Dictionary = d.get("crews_available", {})
    for k in ca:
        s.crews_available[str(k)] = int(ca[k])
    var eq: Variant = d.get("equipment", [])
    if eq is Array:
        for e in eq:
            s.equipment.append(EquipmentDef.from_dict(e))
    var site: Dictionary = d.get("site", {})
    s.gates = ZoneData.cells_from_variant(site.get("gates", []))
    s.occupied_cells = ZoneData.cells_from_variant(site.get("occupied_cells", []))
    s.blocked_cells = ZoneData.cells_from_variant(site.get("blocked_cells", []))
    var it: Variant = site.get("initial_tiles", [])
    if it is Array:
        for t in it:
            var td: Dictionary = t
            s.initial_tiles.append({
                "tile": str(td.get("tile", "grass")),
                "cell": ZoneData.cell_from_variant(td.get("cell", [0, 0])),
                "orientation": int(td.get("orientation", 0)),
            })
    var tc: Dictionary = site.get("tile_costs", {})
    for k in tc:
        var c: Dictionary = tc[k]
        s.tile_costs[str(k)] = {"place": float(c.get("place", 0.0)), "weekly": float(c.get("weekly", 0.0))}
    var ev: Variant = d.get("events", [])
    if ev is Array:
        for e in ev:
            s.events.append(EventDef.from_dict(e))
    var sc: Dictionary = d.get("scoring", {})
    var wts: Dictionary = sc.get("weights", {})
    for k in wts:
        s.score_weights[str(k)] = float(wts[k])
    s.max_incidents_hard = int(sc.get("max_incidents_hard", 3))
    var gt: Dictionary = sc.get("grade_thresholds", {})
    for k in gt:
        s.grade_thresholds[str(k)] = float(gt[k])
    var th: Variant = d.get("tutorial_hints", [])
    if th is Array:
        for h in th:
            var hd: Dictionary = h
            s.hints.append({"week": int(hd.get("week", 0)), "text": str(hd.get("text", ""))})
    return s


func tile_place_cost(tile: String) -> float:
    var c: Dictionary = tile_costs.get(tile, {})
    return float(c.get("place", 0.0))


func tile_weekly_cost(tile: String) -> float:
    var c: Dictionary = tile_costs.get(tile, {})
    return float(c.get("weekly", 0.0))
