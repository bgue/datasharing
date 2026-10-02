"""GameClient: a blocking JSON-RPC 2.0 over WebSocket client for the SiteBuilder game.

One Python method per API method of docs/05 section 6.2 (``crew.hire`` becomes
``crew_hire``). Server notifications (messages without an ``id``) are appended to
``client.events`` as dicts: ``{"method": "week.advanced", "params": {...}}``.
"""
from __future__ import annotations

import itertools
import json
import time
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

DEFAULT_URL = "ws://127.0.0.1:8765"

# Python method name -> wire name is "replace the first underscore with a dot".
API_METHODS = (
    "scenario_list", "scenario_load",
    "state_summary", "state_zones", "state_packages", "state_tasks", "state_crews",
    "state_tiles", "state_procurement", "state_gantt",
    "sim_advance", "sim_resolve_event", "sim_set_speed",
    "crew_hire", "crew_fire", "crew_assign",
    "tile_place", "tile_remove", "equipment_place", "equipment_remove",
    "procure_order", "package_release", "package_hold", "package_priority",
    "zone_set_shift",
    "card_list", "card_get", "card_apply", "card_clear", "card_save", "train_apply",
    "plan_export", "save_write", "save_read",
    "zone_staff", "zone_clear_crews", "site_auto_layout", "procure_order_all_due",
    "sim_run_until", "sim_autopilot",
    "analysis_bottlenecks", "analysis_critical", "analysis_s_curve", "analysis_what_if_shift",
    # manual sequencing and the construction logic library (docs/06 track A)
    "manual_set_mode", "manual_add_task", "manual_update_task", "manual_remove_task", "manual_link",
    "manual_unlink", "manual_apply_recipe", "manual_export", "manual_tasks",
    "logic_list", "logic_get", "logic_explain", "logic_apply",
)


def method_to_wire(name: str) -> str:
    """``crew_hire`` -> ``crew.hire``; names that already contain a dot are returned unchanged."""
    if "." in name:
        return name
    head, sep, tail = name.partition("_")
    return f"{head}.{tail}" if sep else name


class GameApiError(Exception):
    """A JSON-RPC error response (game errors use code -32000, message = the game's last_error)."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


def _clean(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}


class GameClient:
    def __init__(self, url: str = DEFAULT_URL, token: str | None = None, timeout: float = 30,
                 connect_timeout: float | None = None):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.connect_timeout = connect_timeout if connect_timeout is not None else min(timeout, 10)
        self.events: list[dict] = []
        self._ids = itertools.count(1)
        self._ws = None

    # ---- connection -------------------------------------------------------------------
    def connect(self) -> "GameClient":
        if self._ws is None:
            kw = {"open_timeout": self.connect_timeout, "max_size": None}
            try:  # websockets >= 16: connect() returns a reconnect helper unless legacy=True; no proxy for localhost
                self._ws = connect(self.url, legacy=True, proxy=None, **kw)
            except TypeError:  # older websockets
                self._ws = connect(self.url, **kw)
        return self

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def __enter__(self) -> "GameClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ---- transport --------------------------------------------------------------------
    def _request(self, method: str, params: dict) -> dict:
        params = dict(params)
        if self.token is not None and "token" not in params:
            params["token"] = self.token
        return {"jsonrpc": "2.0", "id": next(self._ids), "method": method_to_wire(method), "params": params}

    def _send(self, payload: Any) -> None:
        self.connect()
        try:
            self._ws.send(json.dumps(payload))
        except ConnectionClosed:
            self.close()
            raise

    def _handle_notification(self, msg: dict) -> None:
        self.events.append({"method": msg.get("method"), "params": msg.get("params", {})})

    def _recv_one(self, deadline: float) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no response from {self.url} within {self.timeout}s")
        try:
            raw = self._ws.recv(timeout=remaining)
        except TimeoutError:
            raise TimeoutError(f"no response from {self.url} within {self.timeout}s") from None
        except ConnectionClosed:
            self.close()
            raise
        return json.loads(raw)

    def _await(self, ids: set[int]) -> dict[int, dict]:
        """Read frames until a response for every id arrived; notifications go to ``events``."""
        deadline = time.monotonic() + self.timeout
        got: dict[int, dict] = {}
        while len(got) < len(ids):
            msg = self._recv_one(deadline)
            for m in msg if isinstance(msg, list) else [msg]:
                if not isinstance(m, dict):
                    continue
                if "id" not in m or m.get("id") is None:
                    if "method" in m:
                        self._handle_notification(m)
                    elif "error" in m:  # id-less error (e.g. parse error)
                        err = m["error"]
                        raise GameApiError(err.get("code", -32000), err.get("message", ""), err.get("data"))
                    continue
                if m["id"] in ids:
                    got[m["id"]] = m
        return got

    @staticmethod
    def _unwrap(resp: dict) -> Any:
        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise GameApiError(err.get("code", -32000), err.get("message", ""), err.get("data"))
        return resp.get("result")

    def poll_events(self, wait: float = 0.0) -> list[dict]:
        """Collect notifications that arrived outside a call (non-blocking by default)."""
        self.connect()
        deadline = time.monotonic() + max(wait, 0.0)
        while True:
            try:
                raw = self._ws.recv(timeout=max(deadline - time.monotonic(), 0))
            except TimeoutError:
                break
            except ConnectionClosed:
                self.close()
                break
            msg = json.loads(raw)
            for m in msg if isinstance(msg, list) else [msg]:
                if isinstance(m, dict) and m.get("id") is None and "method" in m:
                    self._handle_notification(m)
        return list(self.events)

    def take_events(self, method: str | None = None) -> list[dict]:
        """Return and clear collected notifications (optionally only those of one ``method``)."""
        if method is None:
            out, self.events = self.events, []
            return out
        out = [e for e in self.events if e["method"] == method]
        self.events = [e for e in self.events if e["method"] != method]
        return out

    # ---- public calls -----------------------------------------------------------------
    def call(self, method: str, **params: Any) -> Any:
        """Call any API method by wire name (``sim.advance``) or Python name (``sim_advance``)."""
        req = self._request(method, params)
        self._send(req)
        return self._unwrap(self._await({req["id"]})[req["id"]])

    def batch(self, calls: list) -> list:
        """Send several calls in one frame. Each item is ``(method, params_dict)``, ``(method,)``,
        ``method`` or a ``{"method":..., "params":...}`` dict. Returns results in order; a failed call
        yields a ``GameApiError`` instance in its slot (not raised)."""
        reqs = []
        for c in calls:
            if isinstance(c, str):
                m, p = c, {}
            elif isinstance(c, dict):
                m, p = c["method"], c.get("params", {})
            else:
                m, p = c[0], (c[1] if len(c) > 1 else {})
            reqs.append(self._request(m, p or {}))
        if not reqs:
            return []
        self._send(reqs)
        got = self._await({r["id"] for r in reqs})
        out = []
        for r in reqs:
            try:
                out.append(self._unwrap(got[r["id"]]))
            except GameApiError as e:
                out.append(e)
        return out

    # ---- scenarios ----------------------------------------------------------------------
    def scenario_list(self): return self.call("scenario.list")
    def scenario_load(self, id: str): return self.call("scenario.load", id=id)

    # ---- state --------------------------------------------------------------------------
    def state_summary(self): return self.call("state.summary")
    def state_zones(self, storey_id=None): return self.call("state.zones", **_clean({"storey_id": storey_id}))

    def state_packages(self, zone_id=None, state=None):
        return self.call("state.packages", **_clean({"zone_id": zone_id, "state": state}))

    def state_tasks(self, zone_id=None, package_id=None):
        return self.call("state.tasks", **_clean({"zone_id": zone_id, "package_id": package_id}))

    def state_crews(self): return self.call("state.crews")
    def state_tiles(self): return self.call("state.tiles")
    def state_procurement(self): return self.call("state.procurement")

    def state_gantt(self, zone_ids=None, from_week=None, to_week=None):
        return self.call("state.gantt", **_clean({"zone_ids": zone_ids, "from_week": from_week, "to_week": to_week}))

    # ---- simulation ---------------------------------------------------------------------
    def sim_advance(self, weeks: int = 1, stop_on_event: bool = True):
        return self.call("sim.advance", weeks=weeks, stop_on_event=stop_on_event)

    def sim_resolve_event(self, choice): return self.call("sim.resolve_event", choice=choice)
    def sim_set_speed(self, speed): return self.call("sim.set_speed", speed=speed)

    # ---- crews --------------------------------------------------------------------------
    def crew_hire(self, trade: str, count: int = 1): return self.call("crew.hire", trade=trade, count=count)
    def crew_fire(self, crew_id): return self.call("crew.fire", crew_id=crew_id)
    def crew_assign(self, crew_id, zone_id: str): return self.call("crew.assign", crew_id=crew_id, zone_id=zone_id)

    # ---- site ---------------------------------------------------------------------------
    def tile_place(self, cell, tile: str, orientation: int = 0):
        return self.call("tile.place", cell=list(cell), tile=tile, orientation=orientation)

    def tile_remove(self, cell): return self.call("tile.remove", cell=list(cell))

    def equipment_place(self, equipment_id: str, cell):
        return self.call("equipment.place", equipment_id=equipment_id, cell=list(cell))

    def equipment_remove(self, index: int): return self.call("equipment.remove", index=index)

    def procure_order(self, task_id=None, package_id=None):
        return self.call("procure.order", **_clean({"task_id": task_id, "package_id": package_id}))

    # ---- packages / zones / cards -------------------------------------------------------
    def package_release(self, package_id: str): return self.call("package.release", package_id=package_id)
    def package_hold(self, package_id: str): return self.call("package.hold", package_id=package_id)

    def package_priority(self, package_id: str, priority: int):
        return self.call("package.priority", package_id=package_id, priority=priority)

    def zone_set_shift(self, zone_id: str, mode: str): return self.call("zone.set_shift", zone_id=zone_id, mode=mode)
    def card_list(self): return self.call("card.list")
    def card_get(self, card_id=None): return self.call("card.get", **_clean({"card_id": card_id}))

    def card_apply(self, card_id: str, zone_id: str, auto_staff=None):
        return self.call("card.apply", **_clean({"card_id": card_id, "zone_id": zone_id, "auto_staff": auto_staff}))

    def card_clear(self, zone_id: str): return self.call("card.clear", zone_id=zone_id)

    def card_save(self, card: dict):
        """``card.save`` takes the card object itself as params."""
        return self.call("card.save", **card)

    def train_apply(self, card_id: str, zone_ids: list, stagger_weeks: int = 1):
        return self.call("train.apply", card_id=card_id, zone_ids=list(zone_ids), stagger_weeks=stagger_weeks)

    # ---- files --------------------------------------------------------------------------
    def plan_export(self, path=None): return self.call("plan.export", **_clean({"path": path}))
    def save_write(self, slot=None): return self.call("save.write", **_clean({"slot": slot}))
    def save_read(self, slot=None): return self.call("save.read", **_clean({"slot": slot}))

    # ---- planner (high level) -----------------------------------------------------------
    def zone_staff(self, zone_id: str, level: str = "ideal", hire: bool = False):
        return self.call("zone.staff", zone_id=zone_id, level=level, hire=hire)

    def zone_clear_crews(self, zone_id: str): return self.call("zone.clear_crews", zone_id=zone_id)
    def site_auto_layout(self, laydown: int = 4): return self.call("site.auto_layout", laydown=laydown)

    def procure_order_all_due(self, horizon_weeks: int = 8):
        return self.call("procure.order_all_due", horizon_weeks=horizon_weeks)

    def sim_run_until(self, week=None, package_done=None, cash_below=None, state_count=None):
        return self.call("sim.run_until", **_clean({"week": week, "package_done": package_done,
                                                    "cash_below": cash_below, "state_count": state_count}))

    def sim_autopilot(self, weeks: int, staff_level: str = "ideal"):
        return self.call("sim.autopilot", weeks=weeks, staff_level=staff_level)

    def analysis_bottlenecks(self): return self.call("analysis.bottlenecks")
    def analysis_critical(self, top: int = 20): return self.call("analysis.critical", top=top)
    def analysis_s_curve(self): return self.call("analysis.s_curve")
    def analysis_what_if_shift(self, zone_id: str): return self.call("analysis.what_if_shift", zone_id=zone_id)

    # ---- manual sequencing (docs/06 track A.3) ------------------------------------------
    def manual_set_mode(self, zone_id: str, on: bool = True):
        return self.call("manual.set_mode", zone_id=zone_id, on=on)

    def manual_add_task(self, step=None, zone_id=None, elements=None, virtual=None, quantity=None, unit=None,
                        duration_days=None, after=None, link_type=None, lag_days=None, marker=None, name=None,
                        note=None, hold_point=None, step_def=None):
        """Returns ``{ok, task_id, task}``. ``after`` is a list of task ids (or ``{task_id, type, lag_days}``)."""
        return self.call("manual.add_task", **_clean({
            "step": step, "zone_id": zone_id, "elements": elements, "virtual": virtual, "quantity": quantity,
            "unit": unit, "duration_days": duration_days, "after": after, "link_type": link_type,
            "lag_days": lag_days, "marker": marker, "name": name, "note": note, "hold_point": hold_point,
            "step_def": step_def}))

    def manual_update_task(self, task_id: str, fields: dict):
        return self.call("manual.update_task", task_id=task_id, fields=fields)

    def manual_remove_task(self, task_id: str, bridge: bool = True):
        return self.call("manual.remove_task", task_id=task_id, bridge=bridge)

    def manual_link(self, from_id: str, to_id: str, type: str = "FS", lag_days: int = 0):
        return self.call("manual.link", from_id=from_id, to_id=to_id, type=type, lag_days=lag_days)

    def manual_unlink(self, from_id: str, to_id: str):
        return self.call("manual.unlink", from_id=from_id, to_id=to_id)

    def manual_apply_recipe(self, recipe_id: str, zone_id=None, element_guid=None, include_optional: bool = False):
        return self.call("manual.apply_recipe", **_clean({"recipe_id": recipe_id, "zone_id": zone_id,
                                                          "element_guid": element_guid,
                                                          "include_optional": include_optional}))

    def manual_export(self, path=None): return self.call("manual.export", **_clean({"path": path}))
    def manual_tasks(self, zone_id=None): return self.call("manual.tasks", **_clean({"zone_id": zone_id}))

    # ---- construction logic library (docs/06 track A) -----------------------------------
    def logic_list(self, sector=None): return self.call("logic.list", **_clean({"sector": sector}))
    def logic_get(self, id: str): return self.call("logic.get", id=id)

    def logic_explain(self, element_guid=None, zone_id=None):
        """Give ``element_guid`` or ``zone_id`` (element wins when both are given)."""
        return self.call("logic.explain", **_clean({"element_guid": element_guid, "zone_id": zone_id}))

    def logic_apply(self, recipe_id: str, zone_id=None, element_guid=None, include_optional: bool = False):
        """Alias of ``manual_apply_recipe`` on the wire name ``logic.apply``."""
        return self.call("logic.apply", **_clean({"recipe_id": recipe_id, "zone_id": zone_id,
                                                  "element_guid": element_guid,
                                                  "include_optional": include_optional}))
