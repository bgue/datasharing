class_name Cards
extends RefCounted
## Takt sequence cards and trains (docs/05 section 3).


static func apply(gs: SimState, zone_id: String, card_id: String, auto_staff: String = "") -> bool:
    var card: SequenceCardData = gs.bundle.cards_by_id.get(card_id, null)
    if card == null:
        gs.last_error = "unknown card %s" % card_id
        return false
    if not gs.bundle.zones_by_id.has(zone_id):
        gs.last_error = "no such zone"
        return false
    var zr: ZoneRuntime = gs.zone_runtime[zone_id]
    zr.card_id = card_id
    zr.auto_staff = auto_staff if auto_staff != "" else card.auto_staff
    zr.station_index = 0
    zr.station_start_week = gs.week
    zr.behind_takt = false
    zr.hold_until_week = 0
    zr.card_done = false
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        var pkg: PackageData = p
        var idx: int = card.stations.size()  # implicit trailing station "Other"
        for i in card.stations.size():
            if card.stations[i].matches(pkg):
                idx = i
                break
        (gs.package_runtime[pkg.package_id] as PackageRuntime).station_index = idx
    sync_zone(gs, zone_id)
    gs.refresh_states()
    return true


static func clear(gs: SimState, zone_id: String) -> bool:
    if not gs.zone_runtime.has(zone_id):
        gs.last_error = "no such zone"
        return false
    var zr: ZoneRuntime = gs.zone_runtime[zone_id]
    zr.card_id = ""
    zr.auto_staff = "off"
    zr.behind_takt = false
    zr.hold_until_week = 0
    zr.card_done = false
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        var rt: PackageRuntime = gs.package_runtime[(p as PackageData).package_id]
        rt.station_index = -1
        rt.released = true
    gs.refresh_states()
    return true


## Applies a card to several zones; zone k's first station is held until week + k * stagger.
static func train(gs: SimState, card_id: String, zone_ids: Array, stagger_weeks: int) -> bool:
    for k in zone_ids.size():
        var zid: String = str(zone_ids[k])
        if not apply(gs, zid, card_id):
            return false
        var zr: ZoneRuntime = gs.zone_runtime[zid]
        zr.hold_until_week = gs.week + k * maxi(0, stagger_weeks)
        zr.station_start_week = zr.hold_until_week
        sync_zone(gs, zid)
    gs.refresh_states()
    return true


static func station_count(gs: SimState, zr: ZoneRuntime) -> int:
    var card: SequenceCardData = gs.bundle.cards_by_id.get(zr.card_id, null)
    return (card.stations.size() + 1) if card != null else 0  # + implicit "Other"


static func station_name(gs: SimState, zr: ZoneRuntime, index: int) -> String:
    var card: SequenceCardData = gs.bundle.cards_by_id.get(zr.card_id, null)
    if card == null:
        return ""
    if index < card.stations.size():
        return card.stations[index].name
    return "Other"


static func station_takt(gs: SimState, zr: ZoneRuntime) -> int:
    var card: SequenceCardData = gs.bundle.cards_by_id.get(zr.card_id, null)
    if card == null or zr.station_index >= card.stations.size():
        return 1
    return card.stations[zr.station_index].takt_weeks


## Releases the packages of the current (and earlier unfinished) stations; holds later ones.
static func sync_zone(gs: SimState, zone_id: String) -> void:
    var zr: ZoneRuntime = gs.zone_runtime[zone_id]
    if zr.card_id == "":
        return
    var total: int = station_count(gs, zr)
    var held_by_train: bool = gs.week < zr.hold_until_week
    if not held_by_train:
        # advance past stations whose packages are all done (or empty)
        while zr.station_index < total:
            var open: bool = false
            for p in gs.bundle.packages_by_zone.get(zone_id, []):
                var pkg: PackageData = p
                if (gs.package_runtime[pkg.package_id] as PackageRuntime).station_index == zr.station_index \
                        and not Packages.is_done(gs, pkg):
                    open = true
                    break
            if open:
                break
            zr.station_index += 1
            zr.station_start_week = gs.week
        zr.card_done = zr.station_index >= total
    zr.behind_takt = (not zr.card_done) and (not held_by_train) \
            and (gs.week - zr.station_start_week) > station_takt(gs, zr)
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        var pkg2: PackageData = p
        var rt: PackageRuntime = gs.package_runtime[pkg2.package_id]
        rt.released = (not held_by_train) and rt.station_index <= zr.station_index


## Daily: advance stations, release / hold, then auto-staff.
static func update(gs: SimState) -> void:
    for zid in gs.card_zones():
        sync_zone(gs, zid)
    for zid in gs.card_zones():
        var zr: ZoneRuntime = gs.zone_runtime[zid]
        if zr.auto_staff == "min" or zr.auto_staff == "ideal":
            Planner.staff_zone(gs, zid, zr.auto_staff, false)
            Planner.release_idle_crews(gs, zid)
