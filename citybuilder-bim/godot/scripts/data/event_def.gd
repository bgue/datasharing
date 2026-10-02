class_name EventDef
extends RefCounted
## A weekly-deck event (scenario.events).

var id: String = ""
var name: String = ""
var text: String = ""
var weight: float = 1.0
var min_week: int = 0
var max_week: int = -1  # -1 = no limit
var once: bool = false
var trigger: Dictionary = {}
var effect: Dictionary = {}
## Array of {label: String, effect: Dictionary}
var choices: Array[Dictionary] = []


static func from_dict(d: Dictionary) -> EventDef:
    var e := EventDef.new()
    e.id = str(d.get("id", ""))
    e.name = str(d.get("name", e.id))
    e.text = str(d.get("text", ""))
    e.weight = float(d.get("weight", 1.0))
    e.min_week = int(d.get("min_week", 0))
    var mw: Variant = d.get("max_week", null)
    e.max_week = -1 if mw == null else int(mw)
    e.once = bool(d.get("once", false))
    e.trigger = d.get("trigger", {})
    e.effect = d.get("effect", {})
    var ch: Variant = d.get("choices", [])
    if ch is Array:
        for c in ch:
            var cd: Dictionary = c
            e.choices.append({"label": str(cd.get("label", "")), "effect": cd.get("effect", {})})
    return e
