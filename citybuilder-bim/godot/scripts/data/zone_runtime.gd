class_name ZoneRuntime
extends RefCounted
## Mutable per-zone state: shift mode and the applied takt card / train hold.

var shift_mode: String = "single"  # single | double
var card_id: String = ""
var auto_staff: String = "off"
var station_index: int = 0
var station_start_week: int = 0
var behind_takt: bool = false
## Train stagger: nothing is released before this week.
var hold_until_week: int = 0
var card_done: bool = false


func to_dict() -> Dictionary:
    return {"sm": shift_mode, "c": card_id, "as": auto_staff, "si": station_index, "ssw": station_start_week,
            "bt": behind_takt, "h": hold_until_week, "cd": card_done}


func from_dict(d: Dictionary) -> void:
    shift_mode = str(d.get("sm", "single"))
    card_id = str(d.get("c", ""))
    auto_staff = str(d.get("as", "off"))
    station_index = int(d.get("si", 0))
    station_start_week = int(d.get("ssw", 0))
    behind_takt = bool(d.get("bt", false))
    hold_until_week = int(d.get("h", 0))
    card_done = bool(d.get("cd", false))
