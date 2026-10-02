class_name StoreyData
extends RefCounted

var id: String = ""
var name: String = ""
var index: int = 0
var elevation_m: float = 0.0


static func from_dict(d: Dictionary) -> StoreyData:
    var s := StoreyData.new()
    s.id = str(d.get("id", ""))
    s.name = str(d.get("name", s.id))
    s.index = int(d.get("index", 0))
    s.elevation_m = float(d.get("elevation_m", 0.0))
    return s
