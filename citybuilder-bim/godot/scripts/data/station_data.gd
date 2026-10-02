class_name StationData
extends RefCounted
## One station of a takt sequence card: selects packages by phase / trade / discipline / work_face.

var name: String = ""
var select: Dictionary = {}  # subset of {phase, trade, discipline, work_face}, AND-ed
var takt_weeks: int = 1


static func from_dict(d: Dictionary) -> StationData:
    var s := StationData.new()
    s.name = str(d.get("name", ""))
    var sel: Variant = d.get("select", {})
    if sel is Dictionary:
        for k in sel:
            s.select[str(k)] = str(sel[k])
    s.takt_weeks = maxi(1, int(d.get("takt_weeks", 1)))
    return s


func to_dict() -> Dictionary:
    return {"name": name, "select": select.duplicate(), "takt_weeks": takt_weeks}


func matches(p: PackageData) -> bool:
    for k in select:
        var want: String = str(select[k])
        match k:
            "phase":
                if p.phase != want:
                    return false
            "trade":
                if p.trade != want:
                    return false
            "discipline":
                if p.discipline != want:
                    return false
            "work_face":
                if p.work_face != want:
                    return false
    return true
