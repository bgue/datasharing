class_name TradeDef
extends RefCounted
## A crew trade from the step library.

var id: String = ""
var name: String = ""
var weekly_cost: float = 0.0
var crew_size: int = 4
var max_hire_per_week: int = 2
var color: Color = Color(0.7, 0.7, 0.7)


static func from_dict(d: Dictionary) -> TradeDef:
    var t := TradeDef.new()
    t.id = str(d.get("id", ""))
    t.name = str(d.get("name", t.id))
    t.weekly_cost = float(d.get("weekly_cost", 0.0))
    t.crew_size = int(d.get("crew_size", 4))
    t.max_hire_per_week = int(d.get("max_hire_per_week", 2))
    var c: String = str(d.get("color", ""))
    if c.begins_with("#") and c.length() == 7:
        t.color = Color.html(c)
    return t
