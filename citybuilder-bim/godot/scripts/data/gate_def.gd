class_name GateDef
extends RefCounted
## A hold point between two phases (step_library.gates).

var id: String = ""
var name: String = ""
var after_phase: String = ""
var before_phase: String = ""
var scope: String = "zone"  # zone | storey | project
var requires_inspection_types: Array[String] = []


static func from_dict(d: Dictionary) -> GateDef:
    var g := GateDef.new()
    g.id = str(d.get("id", ""))
    g.name = str(d.get("name", g.id))
    g.after_phase = str(d.get("after_phase", ""))
    g.before_phase = str(d.get("before_phase", ""))
    g.scope = str(d.get("scope", "zone"))
    var r: Variant = d.get("requires_inspection_types", [])
    if r is Array:
        for t in r:
            g.requires_inspection_types.append(str(t))
    return g
