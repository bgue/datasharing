"""Pure functions rendering control API results as compact text for humans and LLMs.

All functions tolerate missing keys. Days are working days, ``week = day // 5`` (docs/04).
"""
from __future__ import annotations

from typing import Any, Iterable

DAYS_PER_WEEK = 5


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
        lines.append("Packages: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    score = _g(s, "score")
    if isinstance(score, dict):
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
        cnt = " ".join(f"{k[:1]}{v}" for k, v in counts.items()) if isinstance(counts, dict) else ""
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
