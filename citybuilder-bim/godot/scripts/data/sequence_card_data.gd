class_name SequenceCardData
extends RefCounted
## A takt sequence card: ordered stations (docs/05 section 3).

var id: String = ""
var name: String = ""
var description: String = ""
var applies_to_zone_tags: Array[String] = []
var stations: Array[StationData] = []
var auto_staff: String = "ideal"  # off | min | ideal


static func from_dict(d: Dictionary) -> SequenceCardData:
    var c := SequenceCardData.new()
    c.id = str(d.get("id", ""))
    c.name = str(d.get("name", c.id))
    c.description = str(d.get("description", ""))
    var tags: Variant = d.get("applies_to_zone_tags", [])
    if tags is Array:
        for t in tags:
            c.applies_to_zone_tags.append(str(t))
    var st: Variant = d.get("stations", [])
    if st is Array:
        for s in st:
            c.stations.append(StationData.from_dict(s))
    c.auto_staff = str(d.get("auto_staff", "ideal"))
    return c


func to_dict() -> Dictionary:
    var st: Array = []
    for s in stations:
        st.append(s.to_dict())
    return {"id": id, "name": name, "description": description, "applies_to_zone_tags": applies_to_zone_tags.duplicate(),
            "stations": st, "auto_staff": auto_staff}
