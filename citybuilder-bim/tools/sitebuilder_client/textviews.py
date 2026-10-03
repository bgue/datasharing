"""Pure functions rendering control API results as compact text for humans and LLMs.

All functions tolerate missing keys. Days are working days, ``week = day // 5`` (docs/04).
"""
from __future__ import annotations

from typing import Any, Iterable

DAYS_PER_WEEK = 5
# one-letter chips for task state counts (zone views): r ready, b blocked, a active, d done ...
_STATE_ABBR = {"READY": "r", "BLOCKED": "b", "ACTIVE": "a", "DONE": "d", "NOT_STARTED": "n",
               "AWAITING_INSPECTION": "i", "INSPECTED": "v", "REWORK": "w"}


def _g(d: Any, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _num(v: Any, nd: int = 1) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd else str(round(v))
    return str(v)


def _money(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    return f"{v:,.0f}"


def _trunc(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: max(n - 1, 1)] + "…"


def _table(headers: list[str], rows: list[list[str]], maxw: dict[int, int] | None = None) -> str:
    maxw = maxw or {}
    rows = [[_trunc(c, maxw.get(i, 60)) for i, c in enumerate(r)] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def format_event(ev: dict | None) -> str:
    """Render a pending event with numbered choices so the caller can answer with resolve_event."""
    if not ev:
        return ""
    lines = [f"PENDING EVENT {_g(ev, 'event_id', _g(ev, 'id', '?'))}: {_g(ev, 'text', '')}"]
    for i, ch in enumerate(_g(ev, "choices", []) or []):
        if isinstance(ch, dict):
            cid = ch.get("id", ch.get("choice", i))
            label = ch.get("text", ch.get("label", ch.get("name", "")))
            extra = ch.get("effect") or ch.get("cost")
            lines.append(f"  [{cid}] {label}" + (f" ({extra})" if extra else ""))
        else:
            lines.append(f"  [{i}] {ch}")
    lines.append("Answer with resolve_event(choice=<id above>).")
    return "\n".join(lines)


def summary_text(summary: dict | None) -> str:
    s = summary or {}
    week, day = _g(s, "week"), _g(s, "day")
    contract = _g(s, "contract_weeks")
    head = f"Week {_num(week)}" + (f"/{_num(contract)}" if contract is not None else "")
    if day is not None:
        head += f" (day {_num(day)})"
    parts = [head, f"cash {_money(_g(s, 'cash'))}"]
    if _g(s, "budget") is not None:
        parts.append(f"budget {_money(_g(s, 'budget'))}")
    lines = ["  ".join(parts)]
    name = _g(s, "scenario") or _g(s, "scenario_id") or _g(s, "name")
    if name:
        lines.insert(0, f"Scenario {name}")
    counts = _g(s, "counts") or _g(s, "counts_by_state") or _g(s, "packages_by_state")
    if isinstance(counts, dict) and counts:
        lines.append("Counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    score = _g(s, "score")
    if isinstance(score, dict) and "total" in score:
        comps = score.get("components")
        lines.append(f"Score: {_num(score['total'])}" + (f" (grade {score['grade']})" if score.get("grade") else "")
                     + (", " + ", ".join(f"{k} {_num(v, 2)}" for k, v in comps.items()) if isinstance(comps, dict) else ""))
    elif isinstance(score, dict):
        lines.append("Score: " + ", ".join(f"{k} {_num(v)}" for k, v in score.items()))
    elif score is not None:
        lines.append(f"Score: {_num(score)}")
    if _g(s, "finished") or _g(s, "level_finished"):
        lines.append(f"LEVEL FINISHED: {_g(s, 'result', '')}")
    ev = _g(s, "pending_event")
    if ev:
        lines.append(format_event(ev))
    return "\n".join(lines)


def zones_table(zones: Iterable[dict] | None) -> str:
    rows = []
    for z in zones or []:
        counts = _g(z, "counts") or {}
        cnt = " ".join(f"{_STATE_ABBR.get(str(k).upper(), str(k)[:1].lower())}{v}"
                       for k, v in counts.items() if v) if isinstance(counts, dict) else ""
        if isinstance(counts, dict) and _g(z, "progress") is None and _g(z, "total"):
            done_share = counts.get("DONE", counts.get("done", 0)) / max(z["total"], 1)
            z = {**z, "progress": done_share}
        crews = _g(z, "crews", _g(z, "crews_now"))
        if isinstance(crews, list):
            crews = len(crews)
        mx = _g(z, "max_crews")
        crew_s = f"{_num(crews)}/{_num(mx)}" if mx is not None else _num(crews)
        done = _g(z, "progress", _g(z, "percent_done"))
        rows.append([
            str(_g(z, "zone_id", _g(z, "id", "?"))), str(_g(z, "name", "")), crew_s,
            str(_g(z, "shift_mode", "single")), str(_g(z, "card") or "-"), str(_g(z, "station") or "-"),
            f"{done * 100:.0f}%" if isinstance(done, (int, float)) and done <= 1 else _num(done), cnt,
        ])
    if not rows:
        return "(no zones)"
    return _table(["zone", "name", "crews", "shift", "card", "station", "done", "packages"], rows, {1: 28, 4: 18, 5: 18})


def packages_table(packages: Iterable[dict] | None) -> str:
    rows = []
    for p in packages or []:
        prof = _g(p, "crew_profile") or {}
        profile = f"{_num(_g(prof, 'min'))}/{_num(_g(prof, 'ideal'))}/{_num(_g(prof, 'max'))}" if prof else "-"
        done, total = _g(p, "crew_days_done"), _g(p, "total_crew_days")
        flags = ""
        if _g(p, "behind_takt"):
            flags += " behind-takt"
        reason = _g(p, "blocked_reason")
        rows.append([
            str(_g(p, "package_id", "?")), str(_g(p, "zone_id", "")), str(_g(p, "name", "")),
            str(_g(p, "state", "?")), f"{_num(_g(p, 'crews_now', 0))} ({profile})",
            f"{_num(done)}/{_num(total)}", _num(_g(p, "remaining_crew_days")),
            f"{_num(_g(p, 'planned_start_day'))}-{_num(_g(p, 'planned_finish_day'))}",
            (str(reason) if reason else "") + flags,
        ])
    if not rows:
        return "(no packages)"
    return _table(["package", "zone", "name", "state", "crews (min/ideal/max)", "done/total cd", "left cd", "plan days",
                   "blocked/flags"], rows, {2: 40, 8: 40})


def bottlenecks_text(b: Any) -> str:
    """Render analysis.bottlenecks. Unknown keys are listed generically, so new categories still show."""
    if not b:
        return "No bottlenecks."
    if isinstance(b, list):
        b = {"bottlenecks": b}
    titles = {
        "idle_zones": "Zones with ready work and no crews",
        "zones_without_crews": "Zones with ready work and no crews",
        "understaffed": "Understaffed packages",
        "understaffed_packages": "Understaffed packages",
        "waiting": "Packages waiting (gate/access/crane/laydown/procurement)",
        "blocked": "Blocked packages",
        "late_orders": "Late procurement orders",
    }
    out, empty = [], True
    for key, val in b.items():
        if key == "ok" or val in (None, [], {}):
            continue
        empty = False
        out.append(f"{titles.get(key, key.replace('_', ' ').capitalize())} ({len(val) if hasattr(val, '__len__') else val}):")
        if isinstance(val, dict):
            for k, v in val.items():
                items = v if isinstance(v, list) else [v]
                out.append(f"  - {k}: " + ", ".join(_item_text(i) for i in items))
        elif isinstance(val, list):
            for it in val[:40]:
                out.append("  - " + _item_text(it))
            if len(val) > 40:
                out.append(f"  ... and {len(val) - 40} more")
        else:
            out.append(f"  {val}")
    return "No bottlenecks." if empty else "\n".join(out)


def _item_text(it: Any) -> str:
    if not isinstance(it, dict):
        return str(it)
    ident = it.get("package_id") or it.get("zone_id") or it.get("task_id") or it.get("id") or ""
    rest = [f"{k}={_num(v)}" for k, v in it.items() if k not in ("package_id", "zone_id", "task_id", "id")
            and not isinstance(v, (dict, list))]
    return (str(ident) + " " + " ".join(rest)).strip()


# ---- Gantt ------------------------------------------------------------------------------

def _row_flags(bars: list[dict]) -> str:
    held = any(_g(b, "state") == "held" or _g(b, "held") for b in bars)
    under = any(_g(b, "state") == "understaffed" or _g(b, "understaffed") for b in bars)
    return "H" if held else ("!" if under else " ")


def _merge_zone(zone_id: str, bars: list[dict]) -> dict:
    starts = [b["planned_start_day"] for b in bars if b.get("planned_start_day") is not None]
    ends = [b["planned_finish_day"] for b in bars if b.get("planned_finish_day") is not None]
    wsum = sum(max((b.get("planned_finish_day") or 0) - (b.get("planned_start_day") or 0), 1) for b in bars) or 1
    prog = sum((b.get("progress") or 0) * max((b.get("planned_finish_day") or 0) - (b.get("planned_start_day") or 0), 1)
               for b in bars) / wsum
    actual = [b["actual_start_day"] for b in bars if b.get("actual_start_day") is not None]
    flags = {"held": any(_g(b, "state") == "held" for b in bars),
             "understaffed": any(_g(b, "state") == "understaffed" for b in bars)}
    return {"name": zone_id, "planned_start_day": min(starts) if starts else None,
            "planned_finish_day": max(ends) if ends else None, "progress": prog,
            "actual_start_day": min(actual) if actual else None,
            "state": "held" if flags["held"] else ("understaffed" if flags["understaffed"] else "active"),
            "zone_id": zone_id}


def gantt_text(gantt_bars: Iterable[dict] | None, from_week: int | None = None, to_week: int | None = None,
               width: int = 80, current_week: int | None = None, group: str = "package",
               label_width: int = 22) -> str:
    """ASCII Gantt, one row per package (``group="package"``) or per zone (``group="zone"``).

    The week window ``[from_week, to_week]`` (inclusive) is scaled to the chart area.
    Legend: ``=`` planned (no progress yet), ``#`` done share, ``-`` remaining share of a started bar,
    ``|`` current week, flag column: ``H`` held, ``!`` understaffed.
    ``current_week`` marks the current week; if omitted no marker is drawn.
    """
    bars = [b for b in (gantt_bars or []) if isinstance(b, dict)]
    if not bars:
        return "(no gantt bars)"
    starts = [b["planned_start_day"] for b in bars if b.get("planned_start_day") is not None]
    ends = [b["planned_finish_day"] for b in bars if b.get("planned_finish_day") is not None]
    if from_week is None:
        from_week = (min(starts) // DAYS_PER_WEEK) if starts else 0
    if to_week is None:
        to_week = (max(ends) // DAYS_PER_WEEK) if ends else from_week + 1
    to_week = max(to_week, from_week)
    span_days = (to_week - from_week + 1) * DAYS_PER_WEEK
    origin = from_week * DAYS_PER_WEEK
    label_width = max(8, min(label_width, width // 3))
    cw = max(width - label_width - 3, 10)  # chart columns; 3 = flag column and separators

    def col(day: float) -> int:
        return int(max(0, min(cw, (day - origin) / span_days * cw)))

    rows: list[tuple[str, str, list[dict]]] = []
    if group == "zone":
        order: dict[str, list[dict]] = {}
        for b in bars:
            order.setdefault(str(b.get("zone_id", "?")), []).append(b)
        for z, bs in order.items():
            rows.append((z, "", bs))
    else:
        for b in bars:
            rows.append((str(b.get("package_id") or b.get("name") or "?"), str(b.get("name") or ""), [b]))

    cur_col = None
    if current_week is not None and from_week <= current_week <= to_week:
        cur_col = min(col((current_week + 0.5) * DAYS_PER_WEEK), cw - 1)

    # header with week ticks
    hdr = [" "] * cw
    nticks = max(cw // 10, 1)
    step_weeks = max(round((to_week - from_week + 1) / nticks), 1)
    for w in range(from_week, to_week + 1, step_weeks):
        c = col(w * DAYS_PER_WEEK)
        lab = f"w{w}"
        if c + len(lab) <= cw:
            hdr[c:c + len(lab)] = lab
    header = " " * (label_width + 3) + "".join(hdr)
    lines = [header.rstrip()]
    if cur_col is not None:
        lines.append(" " * (label_width + 3) + " " * cur_col + "|" + f" now (w{current_week})")

    for ident, name, bs in rows:
        if group == "zone":
            b = _merge_zone(ident, bs)
            label, flag = ident, _row_flags(bs)
        else:
            b, flag = bs[0], _row_flags(bs)
            label = ident
            if name:
                tail = name.split("·")[-2:] if "·" in name else [name]
                label = f"{ident} {' '.join(t.strip() for t in tail)}"
        cells = [" "] * cw
        ps, pf = b.get("planned_start_day"), b.get("planned_finish_day")
        if ps is not None and pf is not None:
            c0, c1 = col(ps), max(col(pf), col(ps) + 1)
            c1 = min(c1, cw)
            progress = b.get("progress") or 0
            started = progress > 0 or b.get("actual_start_day") is not None or b.get("state") in ("active", "done")
            n = max(c1 - c0, 0)
            if started or b.get("state") == "done":
                done_n = n if b.get("state") == "done" else int(round(progress * n))
                for i in range(c0, c1):
                    cells[i] = "#" if i - c0 < done_n else "-"
            else:
                for i in range(c0, c1):
                    cells[i] = "="
        if cur_col is not None and cells[cur_col] == " ":
            cells[cur_col] = "|"
        lines.append(f"{_trunc(label, label_width).ljust(label_width)} {flag} {''.join(cells)}".rstrip())
    lines.append("= planned  # done  - remaining  | current week  H held  ! understaffed")
    return "\n".join(lines)


# ---- logic library and manual chains (docs/06 track A) -----------------------------------------

STATUS_CHIPS = {"covered": "[x] covered", "virtual_present": "[v] virtual in place", "missing": "[ ] missing"}


def _weeks(v: Any) -> str:
    if isinstance(v, (list, tuple)) and v:
        lo, hi = v[0], v[-1]
        return _num(lo) if lo == hi else f"{_num(lo)}-{_num(hi)}"
    return "-" if v in (None, [], "") else _num(v)


def recipes_table(recipes: Iterable[dict] | None) -> str:
    """Recipe index (logic.list): id, name, sector, typical weeks, steps, tags."""
    rows = []
    for r in recipes or []:
        steps = _g(r, "steps")
        rows.append([str(_g(r, "id", "?")), str(_g(r, "name", "")), str(_g(r, "sector", "")),
                     _weeks(_g(r, "typical_duration_weeks")),
                     str(len(steps) if isinstance(steps, list) else _num(steps)),
                     ",".join(str(t) for t in _g(r, "tags", []) or [])])
    if not rows:
        return "(no recipes)"
    return _table(["recipe", "name", "sector", "weeks", "steps", "tags"], rows, {1: 44, 5: 40})


def explain_rows_table(rows: Iterable[dict] | None) -> str:
    """Step coverage table of one recipe entry: step, status chip, task id."""
    out = []
    for r in rows or []:
        chip = STATUS_CHIPS.get(_g(r, "status"), str(_g(r, "status", "?")))
        flags = []
        if _g(r, "virtual"):
            flags.append("virtual")
        if _g(r, "optional"):
            flags.append("optional")
        if _g(r, "hold_point"):
            flags.append("hold:" + str(r["hold_point"]))
        if _g(r, "frozen_task_ids"):
            flags.append("frozen")
        ids = _g(r, "task_ids") or ([r["task_id"]] if _g(r, "task_id") else [])
        tid = str(ids[0]) if ids else "-"
        if len(ids) > 1:
            tid += f" +{len(ids) - 1}"
        out.append([str(_g(r, "ref") or _g(r, "key", "?")), str(_g(r, "name", "")), chip, tid, " ".join(flags)])
    if not out:
        return "(no steps)"
    return _table(["step", "name", "status", "task", "flags"], out, {1: 36})


def explain_text(res: dict | None) -> str:
    """Render logic.explain (element or zone scope): one coverage table per applicable recipe."""
    res = res or {}
    scope = _g(res, "scope", "zone")
    target = _g(res, "element_guid") if scope == "element" else _g(res, "zone_id")
    head = f"{scope} {target or '?'}" + (f" ({res['name']})" if _g(res, "name") else "")
    if scope == "zone" and _g(res, "manual_mode"):
        head += " [manual mode]"
    if _g(res, "none") or not _g(res, "recipes"):
        return f"{head}: no recipe applies."
    lines = [f"What is needed for {head}:"]
    for r in res["recipes"]:
        cov = _g(r, "coverage") or {}
        inplace = _g(cov, "covered", 0) + _g(cov, "virtual_present", 0)
        lines.append("")
        lines.append(f"* {r.get('recipe_id')} - {r.get('name', '')}: {inplace} of {cov.get('total', '?')} required steps in place"
                     + (f" (matched by {', '.join(r['matched_by'])})" if _g(r, "matched_by") else "")
                     + (f", {r['matching_elements']} matching elements" if _g(r, "matching_elements") else ""))
        lines.append(explain_rows_table(r.get("steps")))
    return "\n".join(lines)


def recipe_text(r: dict | None) -> str:
    """Render logic.get: summary, prerequisites, steps, ordering logic, checks, references."""
    r = r or {}
    lines = [f"{_g(r, 'id', '?')} - {_g(r, 'name', '')} [{_g(r, 'sector', '')}]"]
    if _g(r, "typical_duration_weeks"):
        lines[0] += f", typically {_weeks(r['typical_duration_weeks'])} weeks"
    if _g(r, "summary"):
        lines.append(str(r["summary"]))
    pre = _g(r, "prerequisites") or {}
    if pre:
        lines.append("Prerequisites:")
        for k, v in pre.items():
            if v:
                lines.append(f"  {k}: " + (", ".join(map(str, v)) if isinstance(v, list) else str(v)))
    lines.append("Steps:")
    for i, st in enumerate(_g(r, "steps", []) or [], 1):
        if isinstance(st, str):
            lines.append(f"  {i}. {st}")
            continue
        label = st.get("ref") or ("recipe " + str(st.get("recipe", "?")))
        flags = []
        for k in ("virtual", "optional"):
            if st.get(k):
                flags.append(k)
        if st.get("hold_point"):
            flags.append("hold point: " + str(st["hold_point"]))
        if st.get("parallel_with"):
            flags.append("parallel with " + str(st["parallel_with"]))
        if st.get("from_element"):
            flags.append("from " + str(st["from_element"]))
        if st.get("duration_days"):
            flags.append(f"{st['duration_days']} d")
        if st.get("lag_days"):
            flags.append(f"lag {st['lag_days']} d")
        if st.get("key") and st.get("key") != label:
            flags.append("key " + str(st["key"]))
        note = f" - {st['note']}" if st.get("note") else ""
        lines.append(f"  {i}. {label}" + (f" ({', '.join(flags)})" if flags else "") + note)
    if _g(r, "logic"):
        lines.append("Extra ordering logic:")
        for lk in r["logic"]:
            lines.append(f"  {lk.get('after')} -> {lk.get('before')}" + (f" ({lk.get('reason')})" if lk.get("reason") else "")
                         + (f" lag {lk['lag_days']} d" if lk.get("lag_days") else ""))
    for key, title in (("checks", "Checks"), ("references", "References")):
        if _g(r, key):
            lines.append(f"{title}:")
            lines += [f"  - {x}" for x in r[key]]
    return "\n".join(lines)


def _chain_order(tasks: list[dict]) -> list[dict]:
    """Order tasks so predecessors come first (stable: by planned start, then input order)."""
    ids = {t.get("task_id"): t for t in tasks}
    indeg = {t.get("task_id"): [p["task_id"] for p in (t.get("predecessors") or []) if p.get("task_id") in ids]
             for t in tasks}
    order, placed = [], set()
    pending = sorted(range(len(tasks)), key=lambda i: (tasks[i].get("planned_start_day") or 0, i))
    while pending:
        progress = False
        for i in list(pending):
            tid = tasks[i].get("task_id")
            if all(p in placed for p in indeg[tid]):
                order.append(tasks[i])
                placed.add(tid)
                pending.remove(i)
                progress = True
        if not progress:  # cycle (should not happen): append the rest
            order += [tasks[i] for i in pending]
            break
    return order


def manual_chain_text(tasks: Iterable[dict] | None, zone_id: str | None = None) -> str:
    """Ordered manual chain: id, step, bound elements ('virtual' or '3 el'), after (with link type and lag),
    lag, duration, state, planned days."""
    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    if not tasks:
        return f"(no manual tasks{' in ' + zone_id if zone_id else ''})"
    rows = []
    for t in _chain_order(tasks):
        if _g(t, "virtual"):
            bound = "virtual"
        else:
            n = len(t.get("element_guids") or ([t["element_guid"]] if t.get("element_guid") else []))
            bound = f"{n} el"
        preds = t.get("predecessors") or []
        after = ",".join(str(p.get("task_id")) + ("" if p.get("type", "FS") == "FS" else f":{p['type']}") for p in preds) or "-"
        lags = sorted({p.get("lag_days", 0) for p in preds if p.get("lag_days")})
        hold = (t.get("flags") or {}).get("inspection_type")
        step = str(_g(t, "step_id", _g(t, "step", "?"))) + (f" [hold:{hold}]" if hold else "")
        tid = str(t.get("task_id", "?"))
        if t.get("manual_id") and t["manual_id"] != tid:
            tid += f" ({t['manual_id']})"
        rows.append([tid, step, bound, after, ",".join(map(str, lags)) or "0",
                     _num(t.get("duration_days")) if t.get("duration_days") else "-",
                     str(t.get("state", "-")) + (" (frozen)" if t.get("frozen") else ""),
                     f"{_num(t.get('planned_start_day'))}-{_num(t.get('planned_finish_day'))}"])
    head = f"Manual chain{' for ' + zone_id if zone_id else ''} ({len(rows)} tasks):\n"
    return head + _table(["task", "step", "bound", "after", "lag", "dur d", "state", "plan days"], rows, {1: 40})


# ---- 3D view: heat grid, installations, areas -------------------------------------------------

def heat_grid_text(heat: dict | None, max_width: int = 100) -> str:
    """Compact grid of done shares per cell for ``view.heat``.

    One character per cell (x across, z down): ``.`` no task touches the cell, ``0``-``9`` done share in tenths
    (``9`` = 90-99 %), ``#`` finished (100 %), ``R`` rework. Grids wider than ``max_width`` are binned (mean share,
    ``R`` if any cell reworks) and the bin size is stated."""
    heat = heat or {}
    cells = [c for c in (heat.get("cells") or []) if isinstance(c, dict) and isinstance(c.get("cell"), (list, tuple))]
    sid = heat.get("storey_id", "?")
    if not cells:
        return f"Heat {sid}: no cells with tasks" + (f" ({heat['empty_cells']} empty cells)" if heat.get("empty_cells") else "")
    xs = [int(c["cell"][0]) for c in cells]
    zs = [int(c["cell"][1]) for c in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    width = x1 - x0 + 1
    bin_ = max(1, -(-width // max_width))
    gw, gh = -(-width // bin_), -(-(z1 - z0 + 1) // bin_)
    acc: dict[tuple[int, int], list] = {}
    for c in cells:
        key = ((int(c["cell"][0]) - x0) // bin_, (int(c["cell"][1]) - z0) // bin_)
        a = acc.setdefault(key, [0.0, 0, False])
        a[0] += float(c.get("share") or 0.0)
        a[1] += 1
        a[2] = a[2] or bool(c.get("rework"))
    shares = [a[0] / a[1] for a in acc.values()]
    lines = []
    for gz in range(gh):
        row = []
        for gx in range(gw):
            a = acc.get((gx, gz))
            if a is None:
                row.append(".")
            elif a[2]:
                row.append("R")
            else:
                sh = a[0] / a[1]
                row.append("#" if sh >= 0.9995 else str(min(int(sh * 10), 9)))
        lines.append(f"{z0 + gz * bin_:>4} " + "".join(row))
    avg = sum(float(c.get("share") or 0) for c in cells) / len(cells)
    head = (f"Heat {sid}: x {x0}..{x1}, z {z0}..{z1}, {len(cells)} cells with tasks, mean done {avg * 100:.0f}%"
            + (f", {heat['empty_cells']} empty zone cells" if heat.get("empty_cells") else "")
            + (f" (binned {bin_}x{bin_})" if bin_ > 1 else ""))
    legend = ". none  0-9 tenths done  # finished  R rework"
    return "\n".join([head] + lines + [legend])


def installations_table(res: dict | list | None, limit: int | None = None) -> str:
    """Kit installations (view.installations): index, kit, variant, zone, elements, fill and layer fills."""
    rows_in = res.get("installations", []) if isinstance(res, dict) else (res or [])
    rows = []
    for r in rows_in[: limit or None]:
        layers = " ".join(f"{k} {v * 100:.0f}%" for k, v in (_g(r, "layer_fills") or {}).items())
        rows.append([_num(_g(r, "index")), str(_g(r, "kit", "?")), str(_g(r, "variant") or "-"),
                     str(_g(r, "zone_id") or _g(r, "storey_id") or "-"), _num(_g(r, "element_count")),
                     f"{(_g(r, 'overall_fill') or 0) * 100:.0f}%" + (" done" if _g(r, "complete") else ""), layers])
    if not rows:
        return "(no installations)"
    total = len(rows_in)
    done = res.get("complete") if isinstance(res, dict) and "complete" in res else sum(1 for r in rows_in if _g(r, "complete"))
    out = _table(["#", "kit", "variant", "zone", "elems", "fill", "layers"], rows, {6: 60})
    out += f"\n{total} installations, {done} complete"
    if limit and total > limit:
        out += f" (showing {limit})"
    return out


def element_layers_text(res: dict | None) -> str:
    res = res or {}
    if not res.get("kit"):
        return f"Element {res.get('guid', '?')}: not part of a kit installation."
    layers = ", ".join(f"{k} {v * 100:.0f}%" for k, v in (res.get("layers") or {}).items())
    return (f"Element {res.get('guid')}: kit {res['kit']} ({res.get('variant') or '-'}), installation #{res.get('installation')}, "
            f"overall {(res.get('overall_fill') or 0) * 100:.0f}%; layers: {layers or '-'}")


def areas_text(areas: Any) -> str:
    """List of areas (state.areas): id, name and whatever scalar facts the game reports (zones, storeys, tasks, progress)."""
    rows_in = areas.get("areas", []) if isinstance(areas, dict) else (areas or [])
    rows_in = [a for a in rows_in if isinstance(a, dict)]
    if not rows_in:
        return "(no areas)"
    skip = {"id", "name", "cells", "zone_ids", "storey_ids"}
    extra_keys: list[str] = []
    for a in rows_in:
        for k, v in a.items():
            if k not in skip and not isinstance(v, (dict, list)) and k not in extra_keys:
                extra_keys.append(k)
    extra_keys = extra_keys[:6]
    rows = []
    for a in rows_in:
        cells = [str(a.get("id", "?")), str(a.get("name", ""))]
        for k in extra_keys:
            v = a.get(k)
            pct = isinstance(v, float) and 0 <= v <= 1 and any(w in k for w in ("prog", "share", "fill", "done"))
            cells.append(f"{v * 100:.0f}%" if pct else _num(v))
        if isinstance(a.get("zone_ids"), list):
            cells.append(f"{len(a['zone_ids'])} zones")
        rows.append(cells)
    headers = ["area", "name"] + extra_keys + (["zones"] if any(isinstance(a.get("zone_ids"), list) for a in rows_in) else [])
    width = len(headers)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return _table(headers, rows, {1: 40})
