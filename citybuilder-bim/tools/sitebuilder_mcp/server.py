"""SiteBuilder MCP server (FastMCP, stdio).

Env: SITEBUILDER_URL (default ws://127.0.0.1:8765), SITEBUILDER_TOKEN (optional).
The game connection is opened lazily on the first tool call and re-opened if it drops.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Literal

try:  # works both as ``python3 -m sitebuilder_mcp`` (cwd tools) and when imported from tests
    from sitebuilder_client import GameApiError, GameClient
    from sitebuilder_client import textviews as tv
except ImportError:  # pragma: no cover - cwd is not tools/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sitebuilder_client import GameApiError, GameClient
    from sitebuilder_client import textviews as tv

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from websockets.exceptions import ConnectionClosed

INSTRUCTIONS = """\
SiteBuilder is a construction-planning game driven by a BIM-derived sequence. You play the site
planner: each in-game week (5 working days) you decide which crews work in which zones, which work
packages are released or held, which takt cards/trains run, where double shift is worth it, and what
long-lead material to order. Typical loop: get_summary -> list_bottlenecks -> gantt_text ->
staff_zone / release_package / apply_card / order_due_procurement -> advance_weeks. When advance_weeks
or autopilot reports a PENDING EVENT, call resolve_event with one of the listed choices before
advancing again. Zone ids look like L01-Z3, package ids like P00042.
"""

mcp = FastMCP("sitebuilder", instructions=INSTRUCTIONS)

_lock = threading.RLock()
_client: GameClient | None = None


# ---- connection management --------------------------------------------------------------

def _new_client() -> GameClient:
    """Factory for the game client (tests monkeypatch this)."""
    return GameClient(os.environ.get("SITEBUILDER_URL", "ws://127.0.0.1:8765"),
                      os.environ.get("SITEBUILDER_TOKEN") or None,
                      timeout=float(os.environ.get("SITEBUILDER_TIMEOUT", "120")))


def get_client() -> GameClient:
    global _client
    with _lock:
        if _client is None:
            _client = _new_client()
        return _client


def reset_client() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None


def _call(method: str, **params: Any) -> Any:
    """Call a game method; reconnect once if the connection is dead. Game errors become ToolError."""
    with _lock:
        for attempt in (0, 1):
            c = get_client()
            try:
                return c.call(method, **params)
            except GameApiError as e:
                raise ToolError(f"{method} failed: {e.message}" + (f" ({e.data})" if e.data else "")) from e
            except (ConnectionClosed, ConnectionError, OSError) as e:
                reset_client()
                if attempt == 1:
                    raise ToolError(
                        f"cannot reach the SiteBuilder game at "
                        f"{os.environ.get('SITEBUILDER_URL', 'ws://127.0.0.1:8765')}: {e}. Start it with "
                        f"`python3 -m sitebuilder_mcp.launch_game --scenario minimal` or run godot with --api."
                    ) from e
            except TimeoutError as e:  # do not retry: the request may still be running in the game
                reset_client()
                raise ToolError(f"{method} timed out: {e}") from e


# ---- formatting helpers -------------------------------------------------------------------

def _json(obj: Any, limit: int = 6000) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= limit else s[:limit] + f"...[truncated {len(s) - limit} chars]"


def _with_json(text: str, obj: Any, limit: int = 4000) -> str:
    return f"{text}\n\nRaw JSON:\n{_json(obj, limit)}"


def _notifications() -> str:
    """Condense notifications collected during the last call(s) and clear them."""
    evs = get_client().take_events()
    if not evs:
        return ""
    lines, weeks = [], [e["params"].get("week") for e in evs if e["method"] == "week.advanced"]
    if weeks:
        lines.append(f"week.advanced x{len(weeks)} (last: week {weeks[-1]})")
    for e in evs:
        p = e.get("params") or {}
        if e["method"] == "event.fired":
            lines.append(f"event.fired {p.get('event_id', '')}: {p.get('text', '')}")
        elif e["method"] == "level.finished":
            lines.append(f"LEVEL FINISHED: {_json(p.get('result', p), 600)}")
        elif e["method"] == "package.state":
            lines.append(f"package {p.get('package_id')} -> {p.get('state')}")
        elif e["method"] != "week.advanced":
            lines.append(f"{e['method']} {_json(p, 200)}")
    if len(lines) > 25:
        lines = lines[:25] + [f"... {len(lines) - 25} more notifications"]
    return "Notifications:\n" + "\n".join("  " + l for l in lines)


def _sim_report(result: Any) -> str:
    """Text for any call returning a summary (advance, run_until, autopilot, resolve_event)."""
    summary = result if isinstance(result, dict) and ("week" in result or "pending_event" in result) else \
        (result.get("summary") if isinstance(result, dict) else None) or {}
    parts = [tv.summary_text(summary)]
    if isinstance(result, dict):
        extra = {k: v for k, v in result.items() if k not in ("ok", "summary") and k not in summary
                 and k not in ("week", "day", "cash", "budget", "contract_weeks", "counts", "score", "pending_event")}
        if extra:
            parts.append("Details: " + _json(extra, 1500))
    n = _notifications()
    if n:
        parts.append(n)
    return "\n".join(p for p in parts if p)


def _list_of(result: Any, *keys: str) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for k in keys:
            if isinstance(result.get(k), list):
                return result[k]
    return []


def _current_week() -> int | None:
    try:
        w = _call("state.summary").get("week")
        return int(w) if w is not None else None
    except (ToolError, AttributeError, TypeError, ValueError):
        return None


def _gantt(zone_ids: list[str] | None, from_week: int | None, to_week: int | None, width: int, group: str) -> str:
    params: dict[str, Any] = {}
    if zone_ids:
        params["zone_ids"] = zone_ids
    if from_week is not None:
        params["from_week"] = from_week
    if to_week is not None:
        params["to_week"] = to_week
    res = _call("state.gantt", **params)
    bars = _list_of(res, "bars", "gantt")
    return tv.gantt_text(bars, from_week, to_week, width=width, current_week=_current_week(), group=group)


# ---- tools ------------------------------------------------------------------------------------

@mcp.tool()
def load_scenario(id: str = "") -> str:
    """Load a scenario (level) and return its summary. Call with no id to list the available scenarios
    (id, name, sector, difficulty, task count). Loading resets the game to week 0, so do it once per run;
    'minimal' is the 2-zone tutorial block."""
    if not id:
        res = _call("scenario.list")
        rows = _list_of(res, "scenarios")
        text = "Scenarios:\n" + "\n".join(
            f"  {s.get('id')}: {s.get('name', '')} [{s.get('sector', '')}, difficulty {s.get('difficulty', '?')}, "
            f"{s.get('tasks', '?')} tasks]" for s in rows) if rows else "No scenarios."
        return text
    res = _call("scenario.load", id=id)
    return f"Loaded scenario {id}.\n" + _sim_report(res)


@mcp.tool()
def get_summary() -> str:
    """Current game status: week and day, cash vs budget, contract length, package counts by state, score so
    far, crews (id, trade, zone) and any pending event that must be resolved with resolve_event. Start every
    planning turn with this."""
    s = _call("state.summary")
    text = tv.summary_text(s)
    try:
        crews = _call("state.crews")
        rows = _list_of(crews, "crews")
        if rows:
            text += "\nCrews: " + "; ".join(
                f"{c.get('id')}={c.get('trade')}@{c.get('zone_id') or 'idle'}" for c in rows)
        if isinstance(crews, dict):
            caps = {k: v for k, v in crews.items() if k not in ("crews", "ok")}
            if caps:
                text += "\nCaps/hire-left: " + _json(caps, 600)
    except ToolError:
        pass
    return _with_json(text, s, 2500)


@mcp.tool()
def list_zones(storey_id: str | None = None) -> str:
    """Table of zones (optionally only one storey, e.g. 'L01'): crews vs max, shift mode, applied takt card and
    current station, progress and package counts. Use it to find zones that have ready work but no crews."""
    zones = _list_of(_call("state.zones", **({"storey_id": storey_id} if storey_id else {})), "zones")
    return tv.zones_table(zones)


@mcp.tool()
def list_packages(zone_id: str | None = None, state: str | None = None, limit: int = 60) -> str:
    """Table of work packages (units of work for one trade in one zone), optionally filtered by zone_id
    (e.g. 'L02-Z3') and/or state (e.g. 'ready', 'active', 'held', 'blocked', 'done'). Shows crews now vs the
    min/ideal/max profile, crew-days done/total, planned day range, blocked reason and behind-takt flag.
    limit caps the rows (default 60)."""
    params = {k: v for k, v in {"zone_id": zone_id, "state": state}.items() if v}
    pk = _list_of(_call("state.packages", **params), "packages")
    shown = pk[: max(limit, 1)]
    text = tv.packages_table(shown)
    if len(pk) > len(shown):
        text += f"\n... {len(pk) - len(shown)} more packages; filter by zone_id or state."
    return text


@mcp.tool()
def list_bottlenecks() -> str:
    """What is holding the plan back right now: zones with ready work and no crews, understaffed packages,
    packages waiting on gate/access/crane/laydown/procurement, and late procurement orders. Check this before
    deciding where to move crews."""
    b = _call("analysis.bottlenecks")
    return _with_json(tv.bottlenecks_text(b), b, 2500)


@mcp.tool()
def staff_zone(zone_id: str, level: Literal["min", "ideal", "max"] = "ideal", hire: bool = False) -> str:
    """Move idle crews into a zone so every released package meets the staffing level (min, ideal or max
    crews of its profile). With hire=true, hires extra crews if the caps allow. Cheapest way to put crews
    where the work is."""
    res = _call("zone.staff", zone_id=zone_id, level=level, hire=hire)
    return _with_json(f"Staffed {zone_id} to {level}" + (" (hiring allowed)" if hire else "") + ".", res, 2000)


@mcp.tool()
def assign_crew(crew_id: str, zone_id: str) -> str:
    """Assign one crew (id from get_summary or hire_crews) to a zone. Pass zone_id='' to unassign it
    (it becomes idle). Prefer staff_zone unless you need a specific crew."""
    cid: Any = int(crew_id) if str(crew_id).lstrip("-").isdigit() else crew_id
    res = _call("crew.assign", crew_id=cid, zone_id=zone_id)
    return _with_json(f"Crew {crew_id} -> {zone_id or 'idle'}.", res, 1500)


@mcp.tool()
def hire_crews(trade: str, count: int = 1) -> str:
    """Hire crews of a trade (e.g. 'civil', 'mechanical', 'electrical', 'finishes'); costs weekly wages and
    is limited by the scenario's caps. Returns the crew list with ids and the remaining hire allowance."""
    res = _call("crew.hire", trade=trade, count=count)
    return _with_json(f"Hired {count} x {trade}.", res, 2500)


@mcp.tool()
def release_package(package_id: str) -> str:
    """Release a package so crews in its zone may work on it (it must be ready: predecessors done, access,
    crane, laydown and procurement satisfied; otherwise see its blocked_reason). Returns the package view."""
    res = _call("package.release", package_id=package_id)
    return tv.packages_table([res.get("package", res)] if isinstance(res, dict) else []) if res else "Released."


@mcp.tool()
def hold_package(package_id: str) -> str:
    """Hold a package: crews stop working on it and any crew-days already done are kept. Use it to keep trades
    from stacking in one zone or to sequence work by hand. Returns the package view."""
    res = _call("package.hold", package_id=package_id)
    return tv.packages_table([res.get("package", res)] if isinstance(res, dict) else []) if res else "Held."


@mcp.tool()
def apply_card(card_id: str, zone_id: str, auto_staff: Literal["off", "min", "ideal"] | None = None) -> str:
    """Apply a takt sequence card (an ordered list of stations with takt weeks, e.g. 'card_or_room') to a zone:
    only the current station's packages are released, later stations stay held. auto_staff (off|min|ideal)
    moves idle crews in automatically each day; omit to use the card's default. Returns the zone status."""
    params: dict[str, Any] = {"card_id": card_id, "zone_id": zone_id}
    if auto_staff:
        params["auto_staff"] = auto_staff
    res = _call("card.apply", **params)
    return _with_json(f"Card {card_id} applied to {zone_id}.", res, 2500)


@mcp.tool()
def apply_train(card_id: str, zone_ids: list[str], stagger_weeks: int = 1) -> str:
    """Apply one takt card to several zones as a train: zone k's first station starts stagger_weeks x k weeks
    after now, so trades flow through the zones one after another (a diagonal on the Gantt)."""
    res = _call("train.apply", card_id=card_id, zone_ids=zone_ids, stagger_weeks=stagger_weeks)
    return _with_json(f"Train of card {card_id} over {', '.join(zone_ids)} (stagger {stagger_weeks} w).", res, 2500)


@mcp.tool()
def set_shift(zone_id: str, mode: Literal["single", "double"]) -> str:
    """Set a zone's shift mode. 'double' raises productivity but also weekly crew cost, incident and inspection
    failure risk; limited to a few zones at once and refused in occupied-adjacent zones. Use
    analysis what-if numbers (weeks saved vs cost added) before choosing double."""
    res = _call("zone.set_shift", zone_id=zone_id, mode=mode)
    return _with_json(f"Zone {zone_id} shift = {mode}.", res, 1500)


@mcp.tool()
def order_due_procurement(horizon_weeks: int = 8) -> str:
    """Order every long-lead item whose planned start minus lead time falls within horizon_weeks from now.
    Late orders delay the work they feed; call this at least every few weeks."""
    res = _call("procure.order_all_due", horizon_weeks=horizon_weeks)
    return _with_json(f"Ordered long-lead items due within {horizon_weeks} weeks.", res, 2500)


@mcp.tool()
def auto_layout_site(laydown: int = 4) -> str:
    """Place haul roads from the gates to every zone, laydown tiles (laydown cells per zone) and crane pads with
    cranes where needed. Do this once right after loading a scenario so packages are not blocked by
    access, laydown or crane."""
    res = _call("site.auto_layout", laydown=laydown)
    return _with_json("Site auto-layout done.", res, 2500)


@mcp.tool()
def advance_weeks(weeks: int = 1, stop_on_event: bool = True) -> str:
    """Advance the simulation by whole weeks (5 working days each). Stops early when an event with choices
    fires and then reports it as PENDING EVENT with its choices; answer with resolve_event before advancing
    again. Returns the new summary and notifications (week.advanced, level.finished...)."""
    res = _call("sim.advance", weeks=weeks, stop_on_event=stop_on_event)
    return _sim_report(res)


@mcp.tool()
def run_until(week: int | None = None, package_done: str | None = None, cash_below: float | None = None,
              state_count: dict | None = None) -> str:
    """Advance until a condition is met or an event with choices fires. Give any of: week (absolute week
    number), package_done (package id), cash_below (amount), state_count (object like
    {"state": "blocked", "at_least": 3}). A pending event is reported with its choices (see resolve_event)."""
    params = {k: v for k, v in {"week": week, "package_done": package_done, "cash_below": cash_below,
                                "state_count": state_count}.items() if v is not None}
    if not params:
        raise ToolError("run_until needs at least one condition: week, package_done, cash_below or state_count")
    return _sim_report(_call("sim.run_until", **params))


@mcp.tool()
def autopilot(weeks: int, staff_level: Literal["min", "ideal", "max"] = "ideal") -> str:
    """Let the built-in planner play for N weeks: each week it orders due procurement, staffs zones that have
    ready work round-robin at staff_level, and advances. Stops early at an event with choices (reported as
    PENDING EVENT; call resolve_event, then autopilot again). Good baseline or for boring stretches."""
    return _sim_report(_call("sim.autopilot", weeks=weeks, staff_level=staff_level))


@mcp.tool()
def resolve_event(choice: str) -> str:
    """Answer the pending event with one of its choice ids (shown in brackets in the PENDING EVENT text,
    e.g. '0' or 'expedite'). Returns the new summary; there may be another pending event."""
    ch: Any = int(choice) if str(choice).lstrip("-").isdigit() else choice
    return _sim_report(_call("sim.resolve_event", choice=ch))


@mcp.tool()
def export_plan(path: str | None = None) -> str:
    """Export the plan as played (per-package actual/planned dates, crews, S-curve) to files. Optional path
    on the game host; omit for the default location. Returns the paths written."""
    res = _call("plan.export", **({"path": path} if path else {}))
    return _with_json("Plan exported.", res, 1500)


@mcp.tool()
def gantt_text(zone_ids: list[str] | None = None, from_week: int | None = None, to_week: int | None = None,
               width: int = 100, group_by: Literal["package", "zone"] = "package") -> str:
    """ASCII Gantt of the selected zones (all if omitted) for weeks from_week..to_week (default: whole plan).
    One row per package or per zone. '=' planned, '#' done, '-' remaining, '|' current week, 'H' held,
    '!' understaffed. Select few zones or a short window to keep it readable (rows are capped at 80)."""
    text = _gantt(zone_ids, from_week, to_week, width, group_by)
    lines = text.split("\n")
    if len(lines) > 84:
        lines = lines[:82] + [f"... {len(lines) - 83} more rows; narrow zone_ids", lines[-1]]
    return "\n".join(lines)


# ---- resources --------------------------------------------------------------------------------

@mcp.resource("sitebuilder://summary", mime_type="text/plain")
def summary_resource() -> str:
    """Current game summary."""
    return tv.summary_text(_call("state.summary"))


@mcp.resource("sitebuilder://zones", mime_type="text/plain")
def zones_resource() -> str:
    """Table of all zones."""
    return tv.zones_table(_list_of(_call("state.zones"), "zones"))


@mcp.resource("sitebuilder://zone/{zone_id}", mime_type="application/json")
def zone_resource(zone_id: str) -> str:
    """One zone's full status (JSON) as returned by state.zones."""
    for z in _list_of(_call("state.zones"), "zones"):
        if z.get("zone_id", z.get("id")) == zone_id:
            return json.dumps(z, indent=1, ensure_ascii=False)
    raise ValueError(f"unknown zone {zone_id}")


@mcp.resource("sitebuilder://packages/{zone_id}", mime_type="text/plain")
def packages_resource(zone_id: str) -> str:
    """Packages of one zone."""
    return tv.packages_table(_list_of(_call("state.packages", zone_id=zone_id), "packages"))


@mcp.resource("sitebuilder://gantt", mime_type="text/plain")
def gantt_resource() -> str:
    """ASCII Gantt of all zones, one row per zone."""
    return _gantt(None, None, None, 100, "zone")


# ---- prompt -----------------------------------------------------------------------------------

@mcp.prompt()
def plan_next_week(focus: str = "") -> str:
    """Plan and apply one week of actions."""
    return (
        "You are the site planner in SiteBuilder. Plan the coming week, apply it, then stop before advancing "
        "unless asked.\n\n"
        "1. Call get_summary (resolve any PENDING EVENT with resolve_event first).\n"
        "2. Call list_bottlenecks, then gantt_text (group_by='zone' first, then package rows for problem zones).\n"
        "3. Propose a short plan: which zones get crews (staff_zone level min/ideal/max, hire_crews only if cash "
        "and caps allow), which packages to release or hold to avoid trade stacking, procurement that must be "
        "ordered now (order_due_procurement), and whether a takt card (apply_card/apply_train) or double shift "
        "(set_shift, only where weeks saved justify the cost) helps. Give one-line reasons.\n"
        "4. Apply the actions with the tools. Check cash against budget before hiring.\n"
        "5. Call advance_weeks(1) only if the user asked to continue, and report what changed."
        + (f"\n\nFocus: {focus}" if focus else "")
    )


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
