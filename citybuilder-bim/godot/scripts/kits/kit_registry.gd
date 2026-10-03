class_name KitRegistry
extends RefCounted
## Visual-kit registry (docs/06 Track B): loads kits/kit_manifest.json, maps elements to kits, picks the
## variant, turns task progress into per-layer fills, and builds / caches the kit meshes.
##
## Progress contract: `runtime_progress` is a Callable(task: TaskData) -> float returning the crew-days
## done on that task (0..estimated_crew_days). A layer's fill is the crew-days done divided by the
## total crew-days of the tasks whose step discipline is in the layer's disciplines.

const MANIFEST_PATH: String = "res://kits/kit_manifest.json"
const BUILDER_DIR: String = "res://scripts/kits/"
const CACHE_MAX: int = 600
const MIN_TASK_DAYS: float = 0.01
const DEFAULT_LOD: float = 80.0
const LOD_GREY: Color = Color(0.62, 0.64, 0.7)
## Discipline tints for the LOD boxes (same palette as BimView.DISCIPLINE_COLORS).
const TINTS: Dictionary = {
    "general": Color(0.62, 0.62, 0.64), "civil": Color(0.55, 0.38, 0.22), "structure": Color(0.55, 0.56, 0.58),
    "architecture": Color(0.86, 0.78, 0.6), "mechanical": Color(0.25, 0.5, 0.95), "electrical": Color(0.98, 0.85, 0.2),
    "plumbing": Color(0.1, 0.65, 0.65), "fire": Color(0.9, 0.25, 0.2), "process": Color(0.95, 0.5, 0.1),
    "instrumentation": Color(0.6, 0.4, 0.85), "medical": Color(0.85, 0.2, 0.7), "commissioning": Color(0.35, 0.75, 0.4),
}

## Classes that are never equipment kits by name (members, services, finishes).
const NON_EQUIPMENT: Array[String] = [
    "IfcColumn", "IfcBeam", "IfcMember", "IfcSlab", "IfcFooting", "IfcPile", "IfcWall", "IfcDoor", "IfcWindow",
    "IfcRoof", "IfcCovering", "IfcRailing", "IfcStair", "IfcPipeSegment", "IfcPipeFitting", "IfcCableCarrierSegment",
    "IfcCableSegment", "IfcDuctSegment", "IfcFlowController", "IfcSensor", "IfcCurtainWall", "IfcBearing",
]
const RACK_CLASSES: Array[String] = [
    "IfcMember", "IfcColumn", "IfcBeam", "IfcPipeSegment", "IfcPipeFitting", "IfcCableCarrierSegment", "IfcCovering",
]

static var _shared: KitRegistry = null

var valid: bool = false
var errors: Array[String] = []
var manifest: Dictionary = {}
var kits: Dictionary = {}  # kit id -> kit dictionary
var markers: Dictionary = {}  # marker id -> {builder, color, anchor}
## step id -> discipline (filled by bind_bundle, or directly by tests).
var step_disciplines: Dictionary = {}
## zone id -> true for zones tagged pipe_rack (filled by bind_bundle).
var rack_zones: Dictionary = {}
var cache_hits: int = 0
var cache_builds: int = 0

var _builders: Dictionary = {}  # builder class name -> KitBuilder
var _variant_owner: Dictionary = {}  # variant id -> kit id
var _cache: Dictionary = {}  # key -> Mesh
var _regex: Dictionary = {}  # pattern -> RegEx


static func shared() -> KitRegistry:
    if _shared == null:
        _shared = KitRegistry.new()
        _shared.load_manifest()
    return _shared


func _init(path: String = "") -> void:
    if path != "":
        load_manifest(path)


func load_manifest(path: String = MANIFEST_PATH) -> bool:
    valid = false
    errors.clear()
    kits = {}
    markers = {}
    if not FileAccess.file_exists(path):
        errors.append("kit manifest not found: %s" % path)
        return false
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
    if not (parsed is Dictionary):
        errors.append("kit manifest is not a JSON object")
        return false
    manifest = parsed
    if str(manifest.get("schema_version", "")) != "1.0":
        errors.append("unsupported kit manifest schema_version")
    kits = manifest.get("kits", {})
    markers = manifest.get("markers", {})
    for id in kits:
        var k: Dictionary = kits[id]
        if not k.has("builder") or not (k.get("layers", []) is Array) or (k["layers"] as Array).is_empty():
            errors.append("kit %s needs builder and layers" % id)
            continue
        if builder_script(str(k["builder"])) == null:
            errors.append("kit %s: no builder script for %s" % [id, k["builder"]])
    _variant_owner.clear()
    for id in kits:
        for v in ((kits[id] as Dictionary).get("variants", {}) as Dictionary):
            _variant_owner[str(v)] = str(id)
    valid = errors.is_empty()
    return valid


# ------------------------------------------------------------------ manifest access

func kit_ids() -> Array[String]:
    var out: Array[String] = []
    for k in kits:
        out.append(str(k))
    out.sort()
    return out


func has_kit(kit: String) -> bool:
    return kits.has(kit)


func kit_params(kit: String) -> Dictionary:
    return (kits.get(kit, {}) as Dictionary).get("params", {})


func kit_param(kit: String, key: String, fallback: Variant) -> Variant:
    return kit_params(kit).get(key, fallback)


func kit_layers(kit: String) -> Array:
    return (kits.get(kit, {}) as Dictionary).get("layers", [])


func layer_ids(kit: String) -> Array[String]:
    var out: Array[String] = []
    for l in kit_layers(kit):
        out.append(str((l as Dictionary)["id"]))
    return out


## Display name of a kit ("Pipe rack"); the manifest `title`, else the id with spaces.
func kit_title(kit: String) -> String:
    var t: String = str((kits.get(kit, {}) as Dictionary).get("title", ""))
    return t if t != "" else kit.replace("_", " ").capitalize()


## Short display label of a layer id for tooltips: "ei" -> "EI", "ring_foundation" -> "ring foundation".
static func layer_label(id: String) -> String:
    if id.length() <= 2:
        return id.to_upper()
    return id.replace("_", " ")


func lod_distance(kit: String) -> float:
    return float((kits.get(kit, {}) as Dictionary).get("lod_collapse_distance", DEFAULT_LOD))


## "PipeRackKit" -> "pipe_rack_kit"
static func snake_case(pascal: String) -> String:
    var out: String = ""
    for i in pascal.length():
        var ch: String = pascal[i]
        if ch != ch.to_lower() and i > 0:
            out += "_"
        out += ch.to_lower()
    return out


static func builder_script(builder: String) -> GDScript:
    var path: String = BUILDER_DIR + snake_case(builder) + ".gd"
    if not ResourceLoader.exists(path):
        return null
    return load(path) as GDScript


func builder_for(kit: String) -> KitBuilder:
    var name: String = str((kits.get(kit, {}) as Dictionary).get("builder", ""))
    if name == "":
        return null
    if not _builders.has(name):
        var script: GDScript = builder_script(name)
        if script == null:
            return null
        _builders[name] = script.new()
    return _builders[name]


# ------------------------------------------------------------------ element -> kit

func bind_bundle(b: SequenceBundle) -> void:
    step_disciplines.clear()
    rack_zones.clear()
    if b == null:
        return
    for s in b.steps:
        step_disciplines[(s as StepDef).id] = (s as StepDef).discipline
    for z in b.zones:
        if (z as ZoneData).tags.has("pipe_rack"):
            rack_zones[(z as ZoneData).id] = true


static func _field(e: Variant, key: String, fallback: Variant) -> Variant:
    if e is Dictionary:
        return (e as Dictionary).get(key, fallback)
    if e is Object:
        var v: Variant = (e as Object).get(key)
        return fallback if v == null else v
    return fallback


func _re(pattern: String) -> RegEx:
    if not _regex.has(pattern):
        var r := RegEx.new()
        r.compile("(?i)" + pattern)
        _regex[pattern] = r
    return _regex[pattern]


func _m(text: String, pattern: String) -> bool:
    return _re(pattern).search(text) != null


## Kit id for an element ("" = draw it with the generic MultiMesh). Uses element.visual_kit when the
## manifest knows it, else falls back on ifc_class / name / visual / zone tags.
func kit_for_element(e: Variant) -> String:
    var hint: String = str(_field(e, "visual_kit", ""))
    if hint != "":
        if kits.has(hint):
            return hint
        if _variant_owner.has(hint):  # the pipeline may hint a variant id such as "rack_ei"
            return _variant_owner[hint]
    var cls: String = str(_field(e, "ifc_class", ""))
    var nm: String = str(_field(e, "name", ""))
    var vis: String = str(_field(e, "visual", ""))
    var found: String = _fallback_kit(e, cls, nm, vis)
    return found if kits.has(found) else ""


## The variant id an element's visual_kit hint names ("" when the hint is a plain kit id or absent).
func variant_hint(e: Variant) -> String:
    var hint: String = str(_field(e, "visual_kit", ""))
    return hint if (hint != "" and not kits.has(hint) and _variant_owner.has(hint)) else ""


## Position of a variant in its kit's variant list (0 = the plain one); -1 when unknown.
func variant_rank(kit: String, variant: String) -> int:
    var keys: Array = ((kits.get(kit, {}) as Dictionary).get("variants", {}) as Dictionary).keys()
    return keys.find(variant)


func _fallback_kit(e: Variant, cls: String, nm: String, vis: String) -> String:
    # explicit civil / building kits first (they use structural classes)
    if vis == "culvert" or _m(nm, "culvert"):
        return "culvert"
    if (vis == "pier" and not _m(nm, "abutment")) or (_m(nm, "\\bpier\\b") and not _m(nm, "abutment|pier cap|pile cap")):
        return "bridge_pier"
    if _m(nm, "switch ?room|substation|e-house|switchgear building"):
        return "switchroom"
    if cls == "IfcChimney" or (not NON_EQUIPMENT.has(cls) and _m(nm, "\\b(stack|chimney)\\b")):
        return "stack"
    if not NON_EQUIPMENT.has(cls):
        if _m(nm, "cooling tower"):
            return "cooling_tower"
        if _m(nm, "turbine"):
            return "turbine"
        if cls == "IfcTransformer" or _m(nm, "transformer"):
            return "transformer"
        if _m(nm, "\\bMRI\\b"):
            return "mri"
        if cls == "IfcChiller" or _m(nm, "chiller"):
            return "chiller"
        if _m(nm, "air handling|\\bAHU"):
            return "ahu"
        if cls == "IfcCompressor" or _m(nm, "compressor"):
            return "compressor"
        if cls == "IfcPump" or _m(nm, "\\bpump\\b"):
            return "pump_plinth"
        if cls == "IfcHeatExchanger" or _m(nm, "exchanger|reboiler|condenser"):
            return "exchanger"
        if _m(nm, "horizontal|knock.?out|accumulator|receiver"):
            return "vessel_h"
        if _m(nm, "vessel|column|reactor|scrubber|absorber|heater|furnace|\\bdrum\\b"):
            return "vessel_v"
        if cls == "IfcTank":
            return "tank"
    if bool(_field(e, "module", false)) or (not NON_EQUIPMENT.has(cls) and _m(nm, "\\bmodule\\b")):
        return "module"
    if RACK_CLASSES.has(cls):
        var zone: String = str(_field(e, "zone_id", ""))
        if rack_zones.has(zone) or _m(nm, "\\brack\\b"):
            return "rack"
    return ""


# ------------------------------------------------------------------ variants

## Variant id for the disciplines that have tasks in an instance. Rack: electrical / instrumentation ->
## rack_ei; mechanical as well (or alone) -> rack_mpei. Kits without variants return "".
func variant_for(kit: String, disciplines_present: Array) -> String:
    var variants: Dictionary = (kits.get(kit, {}) as Dictionary).get("variants", {})
    if variants.is_empty():
        return ""
    if kit == "rack":
        var has_ei: bool = disciplines_present.has("electrical") or disciplines_present.has("instrumentation")
        var has_mech: bool = disciplines_present.has("mechanical")
        if has_mech and variants.has("rack_mpei"):
            return "rack_mpei"
        if has_ei and variants.has("rack_ei"):
            return "rack_ei"
        return "rack" if variants.has("rack") else str(variants.keys()[0])
    # generic rule: the variant with the most layers that have a matching discipline
    var best: String = str(variants.keys()[0])
    var best_n: int = -1
    var layers: Array = kit_layers(kit)
    for v in variants:
        var n: int = 0
        for l in layers:
            var ld: Dictionary = l
            if (variants[v] as Array).has(ld["id"]):
                for d in ld["disciplines"]:
                    if disciplines_present.has(d):
                        n += 1
                        break
        if n > best_n:
            best_n = n
            best = str(v)
    return best


func variant_layers(kit: String, variant: String) -> Array[String]:
    var out: Array[String] = []
    var variants: Dictionary = (kits.get(kit, {}) as Dictionary).get("variants", {})
    if variants.has(variant):
        for l in variants[variant]:
            out.append(str(l))
    return out


# ------------------------------------------------------------------ progress -> layer fills

func discipline_of(task: Variant) -> String:
    return str(step_disciplines.get(str(_field(task, "step_id", "")), "general"))


func disciplines_of_tasks(tasks: Array) -> Array[String]:
    var out: Array[String] = []
    for t in tasks:
        var d: String = discipline_of(t)
        if not out.has(d):
            out.append(d)
    out.sort()
    return out


## Fills only: layer id -> 0..1 (see `analyze`).
func layer_fills(kit: String, element_tasks: Array, runtime_progress: Callable) -> Dictionary:
    return analyze(kit, element_tasks, runtime_progress)["fills"]


## Full analysis of a kit instance's tasks:
##   fills    layer id -> fill (crew-days done / total over the layer's disciplines; 0 with no tasks, or the
##            fill of the layer named by "follows" when the layer has none; layers sharing a
##            "sequence_group" split the group's progress in layer order by "weight")
##   present  layer ids to draw (layers with tasks, followers of those, and the variant's layers)
##   states   layer id -> "inspected" | "rework" | ""   (needs the optional `task_state` Callable(task) -> int)
##   overall  crew-days done / total over all tasks
func analyze(kit: String, element_tasks: Array, runtime_progress: Callable, task_state: Callable = Callable(),
        variant: String = "") -> Dictionary:
    var layers: Array = kit_layers(kit)
    var done: Dictionary = {}
    var total: Dictionary = {}
    var all_done: float = 0.0
    var all_total: float = 0.0
    var rework: Dictionary = {}
    var finished: Dictionary = {}
    var unfinished: Dictionary = {}
    var inspected: Dictionary = {}
    for t in element_tasks:
        var d: String = discipline_of(t)
        var tot: float = maxf(float(_field(t, "estimated_crew_days", 0.0)), MIN_TASK_DAYS)
        var dn: float = clampf(float(runtime_progress.call(t)), 0.0, tot) if runtime_progress.is_valid() else 0.0
        total[d] = float(total.get(d, 0.0)) + tot
        done[d] = float(done.get(d, 0.0)) + dn
        all_total += tot
        all_done += dn
        if task_state.is_valid():
            var st: int = int(task_state.call(t))
            if st == TaskRuntime.State.REWORK:
                rework[d] = true
            if TaskRuntime.is_finished(st):
                finished[d] = int(finished.get(d, 0)) + 1
                if st == TaskRuntime.State.INSPECTED:
                    inspected[d] = true
            else:
                unfinished[d] = true
    # sequence groups: union of member disciplines
    var group_discs: Dictionary = {}
    var group_members: Dictionary = {}
    for l in layers:
        var ld: Dictionary = l
        var g: String = str(ld.get("sequence_group", ""))
        if g == "":
            continue
        if not group_discs.has(g):
            group_discs[g] = []
            group_members[g] = []
        for dd in ld["disciplines"]:
            if not (group_discs[g] as Array).has(dd):
                (group_discs[g] as Array).append(dd)
        (group_members[g] as Array).append(ld)
    var fills: Dictionary = {}
    var has_tasks: Dictionary = {}
    for l in layers:
        var ld2: Dictionary = l
        if str(ld2.get("sequence_group", "")) != "":
            continue
        var r: Array = _fraction(ld2["disciplines"], done, total)
        fills[ld2["id"]] = r[0]
        has_tasks[ld2["id"]] = r[1]
    for g in group_members:
        var r2: Array = _fraction(group_discs[g], done, total)
        var wsum: float = 0.0
        for m in group_members[g]:
            wsum += maxf(0.0001, float((m as Dictionary).get("weight", 1.0)))
        var cum: float = 0.0
        for m in group_members[g]:
            var md: Dictionary = m
            var share: float = maxf(0.0001, float(md.get("weight", 1.0))) / wsum
            fills[md["id"]] = clampf((float(r2[0]) - cum) / share, 0.0, 1.0)
            has_tasks[md["id"]] = r2[1]
            cum += share
    # followers adopt the fill of the layer they follow when they have no tasks of their own
    var present: Dictionary = {}
    var states_raw: Dictionary = {}
    for l in layers:
        var ld3: Dictionary = l
        var id: String = ld3["id"]
        var resolved: String = _resolve_follow(id, layers, has_tasks)
        if resolved != "":
            if resolved != id:
                fills[id] = fills.get(resolved, 0.0)
            present[id] = true
        # layer state from its own disciplines (or the followed layer's)
        var src: Dictionary = _layer_def(layers, resolved if resolved != "" else id)
        var discs: Array = src.get("disciplines", [])
        var g2: String = str(src.get("sequence_group", ""))
        if g2 != "":
            discs = group_discs[g2]
        var st_str: String = ""
        var any_rework: bool = false
        var any_insp: bool = false
        var any_fin: bool = false
        var any_unfin: bool = false
        for dd in discs:
            any_rework = any_rework or rework.has(dd)
            any_insp = any_insp or inspected.has(dd)
            any_fin = any_fin or finished.has(dd)
            any_unfin = any_unfin or unfinished.has(dd)
        if any_rework:
            st_str = "rework"
        elif any_fin and any_insp and not any_unfin:
            st_str = "inspected"
        states_raw[id] = st_str
    var present_ids: Array[String] = []
    var var_layers: Array[String] = variant_layers(kit, variant) if variant != "" else ([] as Array[String])
    for l in layers:
        var lid: String = (l as Dictionary)["id"]
        if present.has(lid) and (var_layers.is_empty() or var_layers.has(lid)):
            present_ids.append(lid)
    if present_ids.is_empty():
        # nothing of this kit has tasks in a bound discipline: show every layer as an unbuilt ghost
        for l in layers:
            var lid2: String = (l as Dictionary)["id"]
            if var_layers.is_empty() or var_layers.has(lid2):
                present_ids.append(lid2)
        for k in fills:
            fills[k] = 0.0
    return {
        "fills": fills,
        "present": present_ids,
        "states": states_raw,
        "overall": (all_done / all_total) if all_total > 0.0 else 0.0,
    }


## Layer fill states only (see `analyze`).
func layer_states(kit: String, element_tasks: Array, runtime_progress: Callable, task_state: Callable) -> Dictionary:
    return analyze(kit, element_tasks, runtime_progress, task_state)["states"]


## [fraction, has_tasks] over a set of disciplines.
static func _fraction(discs: Array, done: Dictionary, total: Dictionary) -> Array:
    var dn: float = 0.0
    var tt: float = 0.0
    for d in discs:
        dn += float(done.get(d, 0.0))
        tt += float(total.get(d, 0.0))
    if tt <= 0.0:
        return [0.0, false]
    return [clampf(dn / tt, 0.0, 1.0), true]


static func _layer_def(layers: Array, id: String) -> Dictionary:
    for l in layers:
        if (l as Dictionary)["id"] == id:
            return l
    return {}


## The layer whose fill `id` shows: itself when it has tasks, else the layer it follows (recursively);
## "" when neither has tasks (the layer is not drawn).
static func _resolve_follow(id: String, layers: Array, has_tasks: Dictionary) -> String:
    var cur: String = id
    for _i in 6:
        if bool(has_tasks.get(cur, false)):
            return cur
        var f: String = str(_layer_def(layers, cur).get("follows", ""))
        if f == "":
            return ""
        cur = f
    return ""


# ------------------------------------------------------------------ meshes

## Marker mesh for BimView.marker_mesh_provider: `func(marker: String) -> Mesh`, sized for BimView's marker
## nodes (about 0.4 x 0.3 grid cells, centred), coloured from the manifest. Unknown ids return null.
func marker_mesh(marker_id: String) -> Mesh:
    if not markers.has(marker_id) and not MarkerKit.IDS.has(marker_id):
        return null
    var accent: Color = Color(0, 0, 0, 0)
    var col: Variant = (markers.get(marker_id, {}) as Dictionary).get("color", null)
    if col != null:
        accent = Color.html(str(col))
    return MarkerKit.world_mesh(marker_id, 0.4, 0.3, accent)


## Builds (or returns the cached) mesh. params: {footprint_cells, height_m, cell_size_m, layer_fills,
## variant, seed, present_layers, layer_states, counts, show_ghost}. The variant restricts the drawn layers.
func build(kit: String, params: Dictionary) -> Mesh:
    var builder: KitBuilder = builder_for(kit)
    if builder == null:
        return null
    var p: Dictionary = params.duplicate()
    p["kit"] = kit
    p["kit_params"] = kit_params(kit)
    p["layer_colors"] = _layer_colors(kit)
    var variant: String = str(p.get("variant", ""))
    var vl: Array[String] = variant_layers(kit, variant)
    if not vl.is_empty():
        var cur: Array = p.get("present_layers", [])
        var present: Array = []
        for l in vl:
            if cur.is_empty() or cur.has(l):
                present.append(l)
        p["present_layers"] = present if not present.is_empty() else vl
    var key: String = _cache_key(kit, p)
    if _cache.has(key):
        cache_hits += 1
        return _cache[key]
    if _cache.size() >= CACHE_MAX:
        _cache.clear()
    var mesh: ArrayMesh = builder.build(p)
    cache_builds += 1
    _cache[key] = mesh
    return mesh


## Ghost outline colour per layer: the tint of its first discipline.
func _layer_colors(kit: String) -> Dictionary:
    var out: Dictionary = {}
    for l in kit_layers(kit):
        var ld: Dictionary = l
        var discs: Array = ld.get("disciplines", [])
        out[ld["id"]] = TINTS.get(discs[0], LOD_GREY) if not discs.is_empty() else LOD_GREY
    return out


func clear_cache() -> void:
    _cache.clear()


func cache_size() -> int:
    return _cache.size()


func _cache_key(kit: String, p: Dictionary) -> String:
    var fp: Variant = p.get("footprint_cells", Vector2i(1, 1))
    var fpv: Vector2i = fp if fp is Vector2i else Vector2i(int((fp as Array)[0]), int((fp as Array)[1]))
    var parts: PackedStringArray = PackedStringArray()
    parts.append(kit)
    parts.append("%d,%d" % [fpv.x, fpv.y])
    parts.append("%.2f|%.2f" % [float(p.get("cell_size_m", 6.0)), float(p.get("height_m", 4.0))])
    parts.append(str(p.get("variant", "")))
    parts.append(str(int(p.get("seed", 0))))
    parts.append("g1" if bool(p.get("show_ghost", true)) else "g0")
    var lf: Dictionary = p.get("layer_fills", {})
    var ids: Array = lf.keys()
    ids.sort()
    for id in ids:
        parts.append("%s=%d" % [id, int(roundf(KitBuilder.quantise_fill(float(lf[id])) * KitBuilder.FILL_STEPS))])
    var pl: Array = (p.get("present_layers", []) as Array).duplicate()
    pl.sort()
    parts.append("P" + ",".join(PackedStringArray(pl)))
    var ls: Dictionary = p.get("layer_states", {})
    var lk: Array = ls.keys()
    lk.sort()
    for k in lk:
        if str(ls[k]) != "":
            parts.append("%s:%s" % [k, ls[k]])
    var cn: Dictionary = p.get("counts", {})
    var ck: Array = cn.keys()
    ck.sort()
    for k in ck:
        parts.append("%s#%d" % [k, int(cn[k])])
    return "|".join(parts)


## Tinted bounding box used beyond lod_collapse_distance (ghost surface when nothing is built).
func collapsed_mesh(kit: String, params: Dictionary, overall_fill: float) -> Mesh:
    var q: int = int(roundf(clampf(overall_fill, 0.0, 1.0) * 20.0))
    var p: Dictionary = params.duplicate()
    p["kit"] = kit
    var fp: Variant = p.get("footprint_cells", Vector2i(1, 1))
    var fpv: Vector2i = fp if fp is Vector2i else Vector2i(int((fp as Array)[0]), int((fp as Array)[1]))
    var key: String = "box|%s|%d,%d|%.2f|%.2f|%d" % [kit, fpv.x, fpv.y, float(p.get("cell_size_m", 6.0)), float(p.get("height_m", 4.0)), q]
    if _cache.has(key):
        cache_hits += 1
        return _cache[key]
    var col: Color = _kit_colour(kit)
    var f: float = float(q) / 20.0
    var tinted: Color = LOD_GREY.lerp(col, f)
    var mesh: ArrayMesh = KitBuilder.new().build_box(p, tinted if f > 0.02 else LOD_GREY, f)
    cache_builds += 1
    _cache[key] = mesh
    return mesh


func _kit_colour(kit: String) -> Color:
    var layers: Array = kit_layers(kit)
    if layers.is_empty():
        return LOD_GREY
    var discs: Array = (layers[0] as Dictionary).get("disciplines", [])
    # the most characteristic discipline of the kit: the one of its first process / mechanical / electrical layer
    for l in layers:
        for d in (l as Dictionary)["disciplines"]:
            if d == "process" or d == "mechanical" or d == "electrical" or d == "medical":
                return TINTS.get(d, LOD_GREY)
    if discs.is_empty():
        return LOD_GREY
    return TINTS.get(discs[0], LOD_GREY)
