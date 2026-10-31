"""Plan tab: a dated investment plan on the selected scenario's strategy against the index (`plan` / `plan_montecarlo`,
ops/plan.py; contract and approximations in docs/PLAN.md).

# PC: The session side (`new_form`, `update_form`, `args_for`, `store`, `view_text`) and the browser chart (`chart_data`) import
no Textual, so `session.py` and `web.py` can use them; the Textual pane is built by `pane(app)`.

Wiring, all in one place: ui.py mounts `pane(app)` after the Accounts tab and `pane()` binds key `P` and tells the app how
to draw the tab (`app._draw_plan`). `Session.cmd_plan` / `Session.cmd_plan_mc` submit the `plan` and `plan_montecarlo` jobs
with the arguments of `args_for` (the scenario's screen, config and Tax section + the plan form kept in
`analyses["plan_form"]`); `Session._finish` keeps the results under `analyses["plan"][<scenario label>]` = `{"plan", "mc"}`;
this module registers the chart builder with `web.BUILDERS`. `tui_command("plan", {...})` and `("plan_mc", {...})` drive it.
"""
from __future__ import annotations

KIND, KIND_MC, TAB = "plan", "plan_montecarlo", "plan"
# V2: the owner's illustrative phase plan (what is available to invest by phase of life): years 0-10 $500, 10-13 $3,000,
# 13-20 $1,000 a month; the form starts with it and "Use phases" ON (docs/DECISIONS.md)
BASE_PHASES = ({"years_from": 0.0, "years_to": 10.0, "amount": 500.0, "note": "early years"},
               {"years_from": 10.0, "years_to": 13.0, "amount": 3000.0, "note": "peak saving"},
               {"years_from": 13.0, "years_to": 20.0, "amount": 1000.0, "note": "later years"})
FORM_DEFAULT = {"start": None, "end": None, "initial": 10000.0, "periodic": 500.0, "frequency": "monthly", "growth": 0.0,
                "use_phases": True, "phases": [dict(p) for p in BASE_PHASES], "phase_growth": "indexed",
                "extra": [], "withdrawals": 0.0, "withdraw_from": None, "withdraw_growth": 0.0, "cash_timing": "invest",
                "index_mer": 0.0005, "goal": None, "n_paths": 500, "generator": "block", "seed": 0}
FREQUENCIES = ("biweekly", "monthly", "quarterly", "annual")
CASH_TIMINGS = ("invest", "hold")
GENERATORS = ("block", "stationary", "regime", "iid")
NUMBERS = ("initial", "periodic", "withdrawals", "index_mer")
OPT_NUMBERS = ("goal",)
OPT_DATES = ("start", "end", "withdraw_from")
GROWTHS = ("growth", "withdraw_growth")
# metric -> (format, which direction is better for the strategy: "high" | "low" | None = no colour)
METRIC_ROWS = (("Contributed", "money", None), ("FinalPreTax", "money", "high"), ("FinalAfterTax", "money", "high"),
               ("FinalAfterTaxReal", "money", "high"), ("Gain", "money", "high"), ("XIRR", "pct", "high"),
               ("XIRRAfterTax", "pct", "high"), ("XIRRReal", "pct", "high"), ("XIRRAfterTaxReal", "pct", "high"),
               ("TWR", "pct", "high"), ("TWRAfterTax", "pct", "high"), ("Alpha", "pct", "high"), ("Beta", "num", None),
               ("Sharpe", "num", "high"), ("Vol", "pct", None), ("MaxDD", "pct", "high"), ("TrackingError", "pct", None),
               ("InfoRatio", "num", "high"), ("TaxPaid", "money", "low"), ("TaxDrag/yr", "pct", "low"),
               ("CostDrag/yr", "pct", "low"), ("MERDrag/yr", "pct", "low"), ("YearsToGoal", "num", "low"),
               ("GoalShortfall", "money", "low"))
SERIES = (("contributed", "contributed (net)"), ("strategy_pre", "strategy, pre-tax"),
          ("strategy_after_tax", "strategy, after tax"), ("index_pre", "index, pre-tax"),
          ("index_after_tax", "index, after tax"))
BLOCKS = " ▁▂▃▄▅▆▇█"


# ------------------------------------------------------------------ session side (no Textual)
def new_form() -> dict:
    return {k: ([dict(x) if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v) for k, v in FORM_DEFAULT.items()}


def _num(name, v):
    try:
        return float(v)
    except (TypeError, ValueError):
        from .session import SessionError
        raise SessionError(400, f"plan {name} must be a number, got {v!r}") from None


def update_form(form: dict, fields: dict, add_extra=None, remove_extra=None, add_phase=None, remove_phase=None,
                set_phase=None, flat_phase=False) -> dict:
    """Merge `fields` (any of FORM_DEFAULT's keys) into the plan form in place, validated; `add_extra` is one
    `{date, amount, note}` (or a list of them), `remove_extra` an index (or a list) into the extras table. V2: the
    phases table has the same edits: `add_phase` `{years_from, years_to, amount, note}`, `remove_phase` an index (or a list),
    `set_phase` `{index, ...fields}` edits one row, `flat_phase` replaces the table by one phase 0..end of the flat periodic."""
    from .session import SessionError
    bad = [k for k in fields if k not in FORM_DEFAULT]
    if bad:
        raise SessionError(400, f"unknown plan field {bad[0]!r}; use {list(FORM_DEFAULT)}")
    for k, v in fields.items():
        if k in NUMBERS:
            form[k] = _num(k, v)
        elif k in OPT_NUMBERS:
            form[k] = None if v in (None, "") else _num(k, v)
        elif k in OPT_DATES:
            form[k] = None if v in (None, "") else str(v)[:10]
        elif k in GROWTHS:
            form[k] = "cpi" if v == "cpi" else _num(k, v)
        elif k == "frequency" and v not in FREQUENCIES:
            raise SessionError(400, f"plan frequency must be one of {list(FREQUENCIES)}, got {v!r}")
        elif k == "cash_timing" and v not in CASH_TIMINGS:
            raise SessionError(400, f"plan cash_timing must be one of {list(CASH_TIMINGS)}, got {v!r}")
        elif k == "generator" and v not in GENERATORS:
            raise SessionError(400, f"plan generator must be one of {list(GENERATORS)}, got {v!r}")
        elif k in ("n_paths", "seed"):
            try:
                form[k] = int(v)
            except (TypeError, ValueError):
                raise SessionError(400, f"plan {k} must be an integer, got {v!r}") from None
        elif k == "use_phases":
            form[k] = bool(v) and v not in ("false", "False", "0", "off")
        elif k == "phase_growth":
            if v not in PHASE_GROWTHS:
                raise SessionError(400, f"plan phase_growth must be one of {list(PHASE_GROWTHS)}, got {v!r}")
            form[k] = v
        elif k == "phases":                                     # V2
            form[k] = [_phase(x) for x in (v or [])]
        elif k == "extra":
            form[k] = []
            for x in (v or []):
                form[k].append(_extra(x))
        else:
            form[k] = v
    for x in ([add_extra] if isinstance(add_extra, dict) else (add_extra or [])):
        form["extra"].append(_extra(x))
    for i in sorted({remove_extra} if isinstance(remove_extra, int) else set(remove_extra or ()), reverse=True):
        if not 0 <= i < len(form["extra"]):
            raise SessionError(400, f"remove_extra {i}: the extras table has {len(form['extra'])} rows")
        del form["extra"][i]
    form["extra"].sort(key=lambda x: x["date"])
    # V2: phases table edits
    if flat_phase:
        form["use_phases"] = True
        form["phases"] = [{"years_from": 0.0, "years_to": None, "amount": float(form["periodic"]), "note": "flat"}]
    for x in ([add_phase] if isinstance(add_phase, dict) else (add_phase or [])):
        form["phases"].append(_phase(x))
    if set_phase is not None:
        if not isinstance(set_phase, dict) or not isinstance(set_phase.get("index"), int) or not 0 <= set_phase["index"] < len(form["phases"]):
            raise SessionError(400, f"set_phase needs an `index` into the phases table ({len(form['phases'])} rows)")
        form["phases"][set_phase["index"]] = _phase({**form["phases"][set_phase["index"]], **{k: v for k, v in set_phase.items() if k != "index"}})
    for i in sorted({remove_phase} if isinstance(remove_phase, int) else set(remove_phase or ()), reverse=True):
        if not 0 <= i < len(form["phases"]):
            raise SessionError(400, f"remove_phase {i}: the phases table has {len(form['phases'])} rows")
        del form["phases"][i]
    form["phases"].sort(key=lambda x: (x["years_from"], 1e9 if x["years_to"] is None else x["years_to"]))
    return form


PHASE_GROWTHS = ("indexed", "nominal")


def _phase(x) -> dict:  # V2
    from .session import SessionError
    if not isinstance(x, dict) or "years_from" not in x or "amount" not in x:
        raise SessionError(400, "a contribution phase is an object {years_from, years_to, amount, note}")
    lo = _num("phase years_from", x["years_from"])
    hi = None if x.get("years_to") in (None, "") else _num("phase years_to", x["years_to"])
    amount = _num("phase amount", x["amount"])
    if lo < 0 or amount < 0 or (hi is not None and hi <= lo):
        raise SessionError(400, f"phase [{lo:g}, {hi}) amount {amount:g}: years_from >= 0, years_to after years_from, amount >= 0")
    return {"years_from": lo, "years_to": hi, "amount": amount, "note": str(x.get("note") or "")}


def check_phases(phases: list) -> None:  # V2
    """Phases must not overlap (half-open [from, to), sorted by years_from)."""
    from .session import SessionError
    for a, b in zip(phases, phases[1:]):
        if a["years_to"] is None or b["years_from"] < a["years_to"]:
            raise SessionError(400, f"plan phases overlap: [{a['years_from']:g}, {a['years_to']}) and [{b['years_from']:g}, {b['years_to']})")


def phase_summary(plan: dict) -> list:  # V2
    """[{years_from, years_to, amount, note, count, total}] from a plan result's flows: the periodic contributions
    made (applied) in each phase of the spec. Empty when the plan has no phases."""
    import pandas as pd
    phases = plan["spec"].get("phases") or []
    if not phases:
        return []
    fl = plan["flows"]
    c = {k: fl["columns"].index(k) for k in ("date", "kind", "amount", "applied")}
    start = pd.Timestamp(plan["spec"]["start"])
    out = []
    for ph in phases:
        lo = start + pd.DateOffset(months=round(12 * ph["years_from"]))
        hi = None if ph["years_to"] is None else start + pd.DateOffset(months=round(12 * ph["years_to"]))
        rows = [r for r in fl["rows"] if r[c["kind"]] == "periodic" and r[c["applied"]] and pd.Timestamp(r[c["date"]]) >= lo
                and (hi is None or pd.Timestamp(r[c["date"]]) < hi)]
        out.append({**ph, "count": len(rows), "total": float(sum(r[c["amount"]] for r in rows))})
    return out


def phase_line(plan: dict) -> str:  # V2
    return "; ".join(f"years {x['years_from']:g}-{'end' if x['years_to'] is None else format(x['years_to'], 'g')}: "
                     f"{x['count']} x -> {x['total']:,.0f}" for x in phase_summary(plan))


def _extra(x) -> dict:
    from .session import SessionError
    if not isinstance(x, dict) or "date" not in x or "amount" not in x:
        raise SessionError(400, "an extra flow is an object {date, amount, note}")
    return {"date": str(x["date"])[:10], "amount": _num("extra amount", x["amount"]), "note": str(x.get("note") or "")}


def args_for(session, sc, form: dict, mc: bool = False) -> dict:
    """`plan` (or `plan_montecarlo`) arguments for scenario `sc`: its screen, config and Tax section, and the plan form."""
    from .session import CONFIG_KEYS, SessionError
    p = sc.params
    if p.get("exit"):
        raise SessionError(400, f"{sc.label} has an exit policy; the plan operation takes the strategy without one")
    start = form.get("start") or p.get("start")
    if not start:
        import pandas as pd
        card = session.card()
        start = str((pd.Timestamp(card.start) + pd.DateOffset(months=13)).date())
    a = {"screen": session._screen_ref(p), "start": str(start)[:10]}
    for k in ("end", "withdraw_from", "goal"):
        if form.get(k) not in (None, ""):
            a[k] = form[k]
    if not form.get("end") and p.get("end"):
        a["end"] = p["end"]
    for k in ("initial", "periodic", "frequency", "growth", "withdrawals", "withdraw_growth", "cash_timing", "index_mer"):
        a[k] = form[k]
    if form.get("use_phases") and form.get("phases"):             # V2: the phases replace the flat periodic amount
        check_phases(form["phases"])
        a["periodic"] = 0.0
        a["phases"] = [dict(x) for x in form["phases"]]
        a["phase_growth"] = form.get("phase_growth", "indexed")
    if form["extra"]:
        a["extra"] = [dict(x) for x in form["extra"]]
    cfg = {k: p[k] for k in CONFIG_KEYS if p.get(k) is not None}
    if cfg:
        a["config"] = cfg
    if p.get("tax"):
        a["tax"] = dict(p["tax"])
    if mc:
        a.update(n_paths=int(form["n_paths"]), generator=form["generator"], seed=int(form["seed"]))
    return a


def store(analyses: dict, label: str, kind: str, res: dict) -> None:
    """Keep the parts of a `plan` / `plan_montecarlo` result the tab and the chart need. A new plan result drops a Monte
    Carlo result of a different plan."""
    slot = analyses.setdefault(TAB, {}).setdefault(label, {"plan": None, "mc": None})
    if kind == KIND:
        slot["plan"] = {k: res.get(k) for k in ("spec", "metrics", "series", "flows", "tax", "goal", "card", "daily")}
        if slot["mc"] is not None and slot["mc"].get("spec") != res.get("spec"):
            slot["mc"] = None
    else:
        slot["mc"] = {k: res.get(k) for k in ("spec", "n_paths", "seed", "quantiles", "p_beat_index", "p_shortfall",
                                              "p_shortfall_real", "p_shortfall_index", "goal", "sequence", "paired",
                                              "reversal", "fan", "histograms", "card")}


def fmt(kind: str, v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return f"{100 * v:.2f}%" if kind == "pct" else f"{v:,.0f}" if kind == "money" else f"{v:.2f}"


def metric_records(plan: dict) -> list:
    """[{metric, kind, strategy, index, difference, better}] for the metrics table; `better` True / False / None."""
    m = plan["metrics"]
    cols = m["columns"]
    rows = {r[0]: dict(zip(cols[1:], r[1:])) for r in m["rows"]}
    out = []
    for name, kind, good in METRIC_ROWS:
        if name not in cols:
            continue
        s, i, d = (rows.get(r, {}).get(name) for r in ("strategy", "index", "difference"))
        if s is None and i is None:
            continue
        better = None
        if good and s is not None and i is not None and s != i:
            better = (s > i) if good == "high" else (s < i)
        out.append({"metric": name, "kind": kind, "strategy": s, "index": i, "difference": d, "better": better})
    return out


def series_columns(plan: dict) -> dict:
    s = plan["series"]
    cols = s["columns"]
    return {c: [r[j] for r in s["rows"]] for j, c in enumerate(cols)}


def sparkline(values, width: int = 60) -> str:
    """A one-line text chart of `values` (block characters, `width` columns)."""
    v = [x for x in values if x is not None and x == x]
    if not v:
        return ""
    if len(v) > width:
        v = [v[round(i * (len(v) - 1) / (width - 1))] for i in range(width)]
    lo, hi = min(v), max(v)
    span = (hi - lo) or 1.0
    return "".join(BLOCKS[1 + int((x - lo) / span * 7.999)] for x in v)


def text_hist(h: dict, width: int = 36, label: str = "") -> list:
    """Lines of a text histogram from `{"edges", "counts"}` (one row per bin, a bar scaled to `width`)."""
    e, c = h["edges"], h["counts"]
    if not c:
        return []
    top, tot = max(c) or 1, sum(c) or 1
    money = lambda x: f"{x / 1e6:.2f}M" if abs(x) >= 1e6 else f"{x / 1e3:.0f}K" if abs(x) >= 1e3 else f"{x:.0f}"
    out = [label] if label else []
    out += [f"{money(e[i]):>8} {BLOCKS[8] * round(width * c[i] / top):<{width}} {100 * c[i] / tot:4.1f}%" for i in range(len(c))]
    return out


def goal_line(plan: dict) -> str:
    g, spec = plan.get("goal"), plan["spec"]
    if not g:
        return ""
    if g.get("reached"):
        return f"goal {spec['goal']:,.0f} (after tax) reached {g['reached']} after {g['years']:.1f} years; final {g['final_vs_goal']:+,.0f} against it"
    return f"goal {spec['goal']:,.0f} (after tax) not reached; final {g['final_vs_goal']:+,.0f} against it"


def view_text(analyses: dict, grid) -> str:
    """Plain text of the tab for `Session.view`; `grid(columns, rows)` formats a table."""
    a = analyses.get(TAB) or {}
    f = analyses.get("plan_form") or {}
    head = ("plan form: " + ", ".join(f"{k}={v}" for k, v in f.items() if k not in ("extra", "phases") and v not in (None, ""))
            + (f"; extras {f['extra']}" if f.get("extra") else ""))
    if f.get("phases"):
        head += "\ncontribution phases (" + ("in use" if f.get("use_phases") else "OFF, flat periodic used") + "): " + "; ".join(
            f"years {x['years_from']:g}-{'end' if x['years_to'] is None else format(x['years_to'], 'g')} {x['amount']:,.0f}"
            + (f" ({x['note']})" if x["note"] else "") for x in f["phases"])
    if not a:
        return head + "\nno plan yet: run the `plan` command (key P in the TUI)"
    out = [head]
    for label, r in a.items():
        p, mc = r.get("plan"), r.get("mc")
        if p is None:
            continue
        sp = p["spec"]
        out.append(f"{label}: {sp['screen'] if len(str(sp['screen'])) < 40 else 'screen'} from {sp['start']} to {sp['end'] or 'the last bar'}, "
                   f"account {sp['account'] or 'none (pre-tax)'}")
        recs = metric_records(p)
        out.append(grid(["metric", "strategy", "index", "difference", "strategy wins"],
                        [[x["metric"], fmt(x["kind"], x["strategy"]), fmt(x["kind"], x["index"]), fmt(x["kind"], x["difference"]),
                          {True: "yes", False: "no", None: ""}[x["better"]]] for x in recs]))
        s = series_columns(p)
        for c, name in SERIES:
            if c in s:
                out.append(f"{name:>22} {sparkline(s[c])} {s[c][-1]:,.0f}")
        if phase_summary(p):                                               # V2
            out.append("contributed by phase: " + phase_line(p))
        if p.get("goal"):
            out.append(goal_line(p))
        out += [w for w in p["card"]["warnings"] if w.startswith("WARNING")][:6]
        if mc:
            q = mc["quantiles"]
            ci = {c: q["columns"].index(c) for c in ("strategy_after_tax", "index_after_tax") if c in q["columns"]}
            out.append(f"Monte Carlo: {mc['n_paths']} paths, seed {mc['seed']}; after-tax terminal wealth quantiles")
            out.append(grid(["pct", "strategy", "index"], [[r[0], *[f"{r[j]:,.0f}" for j in ci.values()]] for r in q["rows"]]))
            out.append(f"P(strategy beats index, after tax) = {100 * mc['p_beat_index']:.1f}%"
                       + (f"; P(shortfall vs goal {mc['goal']:,.0f}): strategy {100 * mc['p_shortfall']:.1f}%, "
                          f"index {100 * mc['p_shortfall_index']:.1f}%" if mc.get("p_shortfall") is not None else ""))
            sq = mc["sequence"]
            out.append("sequence of returns (where the worst 12 months fall)")
            out.append(grid(sq["columns"], [[("" if v is None else (f"{v:,.0f}" if isinstance(v, float) and abs(v) > 5 else
                                                                        f"{v:.2f}" if isinstance(v, float) else v)) for v in r]
                                            for r in sq["rows"]]))
            for k in ("strategy_after_tax", "index_after_tax"):
                out += text_hist(mc["histograms"][k], 30, k)
            out += [w for w in mc["card"]["warnings"] if w.startswith("WARNING")][:4]
    return "\n".join(out)


# ------------------------------------------------------------------ browser chart data (no Textual)
def chart_data(session) -> dict:
    """`/charts/data?tab=plan`: per scenario with a plan, the wealth series (contributed, strategy and index, pre- and
    after-tax), the extraordinary flows, the metric rows, and the Monte Carlo fan and histograms."""
    from . import web
    a = session.analyses.get(TAB) or {}
    out = web._base(session, TAB, not any(r.get("plan") for r in a.values()),
                    "No plan yet: open the Plan tab in the TUI (key P) or run the `plan` command.")
    plans = []
    for label, r in a.items():
        p, mc = r.get("plan"), r.get("mc")
        if p is None:
            continue
        s = series_columns(p)
        item = {"label": label, "color": web._color_of(session, label), "spec": p["spec"], "x": s["date"],
                "series": {c: s[c] for c, _ in SERIES if c in s}, "goal": p["spec"].get("goal"),
                "goal_result": p.get("goal"), "metrics": metric_records(p),
                "flows": [{"date": row[p["flows"]["columns"].index("executed")], "amount": row[p["flows"]["columns"].index("amount")],
                           "kind": row[p["flows"]["columns"].index("kind")], "note": row[p["flows"]["columns"].index("note")]}
                          for row in p["flows"]["rows"] if row[p["flows"]["columns"].index("kind")] in ("extra", "withdrawal")][:200],
                "warnings": [w for w in p["card"]["warnings"] if w.startswith("WARNING")], "mc": None}
        if mc:
            fan = mc.get("fan")
            fc = fan["columns"] if fan else []
            item["mc"] = {"n_paths": mc["n_paths"], "seed": mc["seed"], "p_beat_index": mc["p_beat_index"],
                          "p_shortfall": mc["p_shortfall"], "p_shortfall_index": mc["p_shortfall_index"],
                          "p_shortfall_real": mc.get("p_shortfall_real"), "goal": mc.get("goal"),
                          "fan": ({"x": [row[0] for row in fan["rows"]],
                                   **{c: [row[j] for row in fan["rows"]] for j, c in enumerate(fc) if j}} if fan else None),
                          "histograms": mc["histograms"], "quantiles": mc["quantiles"], "sequence": mc["sequence"],
                          "paired": mc["paired"], "notes": mc["card"].get("notes", [])}
        plans.append(item)
    out.update(plans=plans, index_label=out["passive"]["label"], selected=session.selected)
    return out


def register_chart() -> None:
    """Make `/charts/data?tab=plan` work (web.py has no knowledge of this tab)."""
    from . import web
    if TAB not in web.CHART_TABS:
        web.CHART_TABS = (*web.CHART_TABS, TAB)
    web.BUILDERS.setdefault(TAB, chart_data)


register_chart()


# ------------------------------------------------------------------ Textual pane
def pane(app):
    """The Plan `TabPane` (mounted in ScreenlabApp.compose). Also binds key `P` and registers `app._draw_plan`."""
    from datetime import datetime as dt

    from rich.text import Text
    from textual import on
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, DataTable, Input, Label, Select, Static, Switch, TabPane
    from textual_plotext import PlotextPlot

    from .session import PASSIVE_COLOR, SessionError

    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def field(label, wid, **kw):
        return Vertical(Label(label), Input(id=wid, **kw), classes="pl-field")

    class PlanPane(TabPane):
        DEFAULT_CSS = """
        PlanPane .pl-row { height: auto; }
        PlanPane .pl-field { width: 1fr; height: auto; padding: 0 1; }
        PlanPane .pl-field Label { color: $text-muted; }
        PlanPane Input { height: 3; }
        PlanPane Select { height: 3; }
        PlanPane #pl-note { height: auto; padding: 0 1; color: $text-muted; }
        PlanPane #pl-metrics { height: 16; }
        PlanPane #pl-extras { height: 6; }
        PlanPane #pl-phases { height: 6; }                                                     /* V2 */
        PlanPane #pl-phase-sum { height: auto; padding: 0 1; }
        PlanPane #pl-chart { height: 20; }
        PlanPane #pl-fan { height: 18; }
        PlanPane #pl-q { height: 8; }
        PlanPane #pl-seq { height: 6; }
        PlanPane .pl-text { height: auto; padding: 0 1; }
        """

        def __init__(self):
            super().__init__("Plan", id=TAB)
            self._seen = None

        def compose(self):
            with VerticalScroll():
                yield Static("", id="pl-note")
                with Horizontal(classes="pl-row"):
                    yield field("Start date", "pl-start", placeholder="default: scenario start")
                    yield field("End date", "pl-end", placeholder="default: last bar")
                    yield field("Lump sum $", "pl-initial")
                    yield field("Periodic $", "pl-periodic")
                with Horizontal(classes="pl-row"):
                    yield Vertical(Label("Frequency"), Select([(f, f) for f in FREQUENCIES], id="pl-frequency", allow_blank=False,
                                                              value="monthly"), classes="pl-field")
                    yield field("Growth /yr (0.03 or cpi)", "pl-growth")
                    yield field("Withdrawal $ per period", "pl-withdrawals")
                    yield field("Withdraw from (date)", "pl-withdraw-from")
                with Horizontal(classes="pl-row"):
                    yield field("Withdrawal growth (number or cpi)", "pl-withdraw-growth")
                    yield field("Goal $ (nominal, after tax)", "pl-goal")
                    yield Vertical(Label("Cash timing"), Select([(f, f) for f in CASH_TIMINGS], id="pl-cash", allow_blank=False,
                                                                value="invest"), classes="pl-field")
                    yield field("Index MER /yr", "pl-mer")
                # V2: contribution phases (what is available to invest by phase of life)
                yield Static("Contribution phases: amount per period by plan year (from, to; blank to = until withdrawals or the end)",
                             classes="pane-title")
                with Horizontal(classes="pl-row"):
                    yield Vertical(Label("Use phases"), Switch(value=True, id="pl-use-phases"), classes="pl-field")
                    yield Vertical(Label("Phase growth"), Select([(f, f) for f in PHASE_GROWTHS], id="pl-phase-growth",
                                                                 allow_blank=False, value="indexed"), classes="pl-field")
                yield DataTable(id="pl-phases", cursor_type="row")
                with Horizontal(classes="pl-row"):
                    yield field("From year", "pl-p-from")
                    yield field("To year", "pl-p-to")
                    yield field("Amount $ per period", "pl-p-amount")
                    yield field("Note", "pl-p-note")
                    yield Vertical(Button("Add row", id="pl-p-add"), Button("Update selected", id="pl-p-set"),
                                   Button("Remove selected", id="pl-p-del"), Button("Flat", id="pl-p-flat"), classes="pl-field")
                yield Static("", id="pl-phase-sum")
                yield Static("Extraordinary investments (negative = withdrawal)", classes="pane-title")
                yield DataTable(id="pl-extras", cursor_type="row")
                with Horizontal(classes="pl-row"):
                    yield field("Date", "pl-x-date")
                    yield field("Amount $", "pl-x-amount")
                    yield field("Note", "pl-x-note")
                    yield Vertical(Button("Add row", id="pl-x-add"), Button("Remove selected", id="pl-x-del"), classes="pl-field")
                with Horizontal(classes="pl-row"):
                    yield field("MC paths", "pl-npaths")
                    yield Vertical(Label("MC resampler"), Select([(f, f) for f in GENERATORS], id="pl-gen", allow_blank=False,
                                                                 value="block"), classes="pl-field")
                    yield field("MC seed", "pl-seed")
                    yield Vertical(Button("Run plan", id="pl-run", variant="primary"),
                                   Button("Run MC", id="pl-run-mc", variant="warning"), classes="pl-field")
                yield Static("Strategy against the index (green = the strategy wins)", classes="pane-title")
                yield DataTable(id="pl-metrics", cursor_type="row")
                yield PlotextPlot(id="pl-chart")
                yield Static("", id="pl-spark", classes="pl-text")
                yield Static("", id="pl-goalline", classes="pl-text")
                yield Static("Monte Carlo: after-tax terminal wealth", classes="pane-title")
                yield DataTable(id="pl-q", cursor_type="row")
                yield Static("", id="pl-mc", classes="pl-text")
                yield PlotextPlot(id="pl-fan")
                yield DataTable(id="pl-seq", cursor_type="row")
                yield Static("", id="pl-hist", classes="pl-text")

        # ---- form <-> session
        def _form_values(self) -> dict:
            q = lambda i: self.query_one(i, Input).value.strip()
            v = {"start": q("#pl-start") or None, "end": q("#pl-end") or None, "initial": q("#pl-initial") or 0,
                 "periodic": q("#pl-periodic") or 0, "frequency": self.query_one("#pl-frequency", Select).value,
                 "growth": q("#pl-growth") or 0, "withdrawals": q("#pl-withdrawals") or 0,
                 "withdraw_from": q("#pl-withdraw-from") or None, "withdraw_growth": q("#pl-withdraw-growth") or 0,
                 "goal": q("#pl-goal") or None, "cash_timing": self.query_one("#pl-cash", Select).value,
                 "use_phases": self.query_one("#pl-use-phases", Switch).value,
                 "phase_growth": self.query_one("#pl-phase-growth", Select).value,
                 "index_mer": q("#pl-mer") or 0, "n_paths": q("#pl-npaths") or 500,
                 "generator": self.query_one("#pl-gen", Select).value, "seed": q("#pl-seed") or 0}
            return v

        def _fill_form(self, f: dict) -> None:
            s = lambda i, v: setattr(self.query_one(i, Input), "value", "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)))
            for i, k in (("#pl-start", "start"), ("#pl-end", "end"), ("#pl-initial", "initial"), ("#pl-periodic", "periodic"),
                         ("#pl-growth", "growth"), ("#pl-withdrawals", "withdrawals"), ("#pl-withdraw-from", "withdraw_from"),
                         ("#pl-withdraw-growth", "withdraw_growth"), ("#pl-goal", "goal"), ("#pl-mer", "index_mer"),
                         ("#pl-npaths", "n_paths"), ("#pl-seed", "seed")):
                s(i, f.get(k))
            for i, k in (("#pl-frequency", "frequency"), ("#pl-cash", "cash_timing"), ("#pl-gen", "generator"),
                         ("#pl-phase-growth", "phase_growth")):
                self.query_one(i, Select).value = f[k]
            self.query_one("#pl-use-phases", Switch).value = bool(f["use_phases"])
            self.query_one("#pl-periodic", Input).disabled = bool(f["use_phases"])           # V2: greyed out while phases are used

        def _send(self, cmd: str) -> None:
            self.app._cmd(cmd, self._form_values(), cmd)

        @on(Button.Pressed, "#pl-run")
        def _run(self) -> None:
            self._send("plan")

        @on(Button.Pressed, "#pl-run-mc")
        def _run_mc(self) -> None:
            self._send("plan_mc")

        # V2: phases table, same pattern as the extras table
        def _phase_inputs(self) -> dict:
            q = lambda i: self.query_one(i, Input).value.strip()
            return {"years_from": q("#pl-p-from"), "years_to": q("#pl-p-to") or None, "amount": q("#pl-p-amount"), "note": q("#pl-p-note")}

        @on(Switch.Changed, "#pl-use-phases")
        def _toggle(self, ev) -> None:
            self.query_one("#pl-periodic", Input).disabled = bool(ev.value)
            if bool(ev.value) != bool((self.app.session.analyses.get("plan_form") or {}).get("use_phases")):
                self.app._cmd("plan", {"run": False, "use_phases": bool(ev.value)}, "plan")

        @on(Button.Pressed, "#pl-p-add")
        def _padd(self) -> None:
            if self.app._cmd("plan", {"run": False, "add_phase": self._phase_inputs()}, "plan"):
                for i in ("#pl-p-from", "#pl-p-to", "#pl-p-amount", "#pl-p-note"):
                    self.query_one(i, Input).value = ""

        @on(Button.Pressed, "#pl-p-set")
        def _pset(self) -> None:
            t = self.query_one("#pl-phases", DataTable)
            if t.row_count and t.cursor_row is not None:
                new = {k: v for k, v in self._phase_inputs().items() if v not in (None, "")}
                if not self._phase_inputs()["years_to"] and self.query_one("#pl-p-from", Input).value.strip():
                    new["years_to"] = None
                self.app._cmd("plan", {"run": False, "set_phase": {"index": int(t.cursor_row), **new}}, "plan")

        @on(Button.Pressed, "#pl-p-del")
        def _pdel(self) -> None:
            t = self.query_one("#pl-phases", DataTable)
            if t.row_count and t.cursor_row is not None:
                self.app._cmd("plan", {"run": False, "remove_phase": int(t.cursor_row)}, "plan")

        @on(Button.Pressed, "#pl-p-flat")
        def _pflat(self) -> None:
            self.app._cmd("plan", {"run": False, "periodic": self.query_one("#pl-periodic", Input).value.strip() or 0,
                                   "flat_phase": True}, "plan")

        @on(DataTable.RowHighlighted, "#pl-phases")
        def _pick(self, ev) -> None:
            f = self.app.session.analyses.get("plan_form") or {}
            if ev.cursor_row is not None and 0 <= ev.cursor_row < len(f.get("phases", [])):
                x = f["phases"][ev.cursor_row]
                for i, v in (("#pl-p-from", x["years_from"]), ("#pl-p-to", x["years_to"]), ("#pl-p-amount", x["amount"]), ("#pl-p-note", x["note"])):
                    self.query_one(i, Input).value = "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))

        @on(Button.Pressed, "#pl-x-add")
        def _add(self) -> None:
            d = self.query_one("#pl-x-date", Input).value.strip()
            a = self.query_one("#pl-x-amount", Input).value.strip()
            if self.app._cmd("plan", {"run": False, "add_extra": {"date": d, "amount": a,
                                                                  "note": self.query_one("#pl-x-note", Input).value.strip()}}, "plan"):
                for i in ("#pl-x-date", "#pl-x-amount", "#pl-x-note"):
                    self.query_one(i, Input).value = ""

        @on(Button.Pressed, "#pl-x-del")
        def _del(self) -> None:
            t = self.query_one("#pl-extras", DataTable)
            if t.row_count and t.cursor_row is not None:
                self.app._cmd("plan", {"run": False, "remove_extra": int(t.cursor_row)}, "plan")

        # ---- results
        def draw(self) -> None:
            s = self.app.session
            f = s.analyses.get("plan_form") or new_form()
            if self._seen != f:
                self._seen = {k: ([dict(y) if isinstance(y, dict) else y for y in v] if isinstance(v, list) else v) for k, v in f.items()}
                self._fill_form(f)
            pt = self.query_one("#pl-phases", DataTable)                                        # V2
            pt.clear(columns=True)
            pt.add_columns("From year", "To year", "Amount $ / period", "Note")
            for x in f["phases"]:
                pt.add_row(Text(f"{x['years_from']:g}", justify="right"), Text("until end" if x["years_to"] is None else f"{x['years_to']:g}", justify="right"),
                           Text(f"{x['amount']:,.0f}", justify="right"), x["note"])
            ex = self.query_one("#pl-extras", DataTable)
            ex.clear(columns=True)
            ex.add_columns("Date", "Amount $", "Note")
            for x in f["extra"]:
                ex.add_row(x["date"], Text(f"{x['amount']:,.0f}", justify="right"), x["note"])
            sc = next((x for x in s.scenarios if x.id == s.selected), None)
            note = self.query_one("#pl-note", Static)
            m, c, spark, goal = (self.query_one(i) for i in ("#pl-metrics", "#pl-chart", "#pl-spark", "#pl-goalline"))
            psum = self.query_one("#pl-phase-sum", Static)
            psum.update("")
            c.plt.clear_figure()
            m.clear(columns=True)
            if sc is None:
                note.update("Add a scenario (a): the plan invests in its strategy; its Tax section sets the account")
                spark.update("")
                goal.update("")
                self._draw_mc(None, None)
                c.refresh()
                return
            tx = sc.params.get("tax") or {}
            running = any(j.get("kind") in (KIND, KIND_MC) for j in s.active_jobs())
            note.update(f"Strategy: {sc.label}; account: {tx.get('account') or 'none (pre-tax)'}"
                        + ("   running..." if running else "") + "   Run plan, then Run MC for the risk study.")
            r = (s.analyses.get(TAB) or {}).get(sc.label) or {}
            p = r.get("plan")
            if p is None:
                m.add_columns("Metric")
                m.add_row("no plan yet for " + sc.label + ": press Run plan")
                spark.update("")
                goal.update("")
                c.plt.title("wealth: contributed vs strategy vs index")
                self._draw_mc(None, None)
                c.refresh()
                return
            if phase_summary(p):                                                               # V2
                psum.update("Contributed by phase: " + phase_line(p))
            m.add_columns("Metric", "Strategy", "Index", "Difference")
            for x in metric_records(p):
                st = {True: "bold green", False: "", None: ""}[x["better"]]
                m.add_row(x["metric"], Text(fmt(x["kind"], x["strategy"]), style=st, justify="right"),
                          Text(fmt(x["kind"], x["index"]), justify="right"), Text(fmt(x["kind"], x["difference"]), style=st, justify="right"))
            cols = series_columns(p)
            xs = [dt.fromisoformat(d[:10]).toordinal() / 365.25 for d in cols["date"]]
            colors = {"contributed": (150, 150, 150), "strategy_pre": rgb(sc.color), "strategy_after_tax": rgb(sc.color),
                      "index_pre": rgb(PASSIVE_COLOR), "index_after_tax": rgb(PASSIVE_COLOR)}
            for k, name in SERIES:
                if k in cols:
                    c.plt.plot(xs, cols[k], label=name, color=colors[k], marker="braille" if "after" not in k else "dot")
            c.plt.title("wealth in dollars: contributed vs strategy vs index, pre-tax and after tax")
            c.plt.xlabel("year")
            c.refresh()
            lines = [f"{name:>20} {sparkline(cols[k], 56)} {cols[k][-1]:>12,.0f}" for k, name in SERIES if k in cols]
            spark.update("\n".join(lines))
            warn = [w for w in p["card"]["warnings"] if w.startswith("WARNING")][:4]
            goal.update("\n".join(([goal_line(p)] if p.get("goal") else []) + warn))
            self._draw_mc(r.get("mc"), sc)

        def _draw_mc(self, mc, sc) -> None:
            q, txt, fan, seq, hist = (self.query_one(i) for i in ("#pl-q", "#pl-mc", "#pl-fan", "#pl-seq", "#pl-hist"))
            fan.plt.clear_figure()
            for t in (q, seq):
                t.clear(columns=True)
            if mc is None:
                txt.update("no Monte Carlo yet: press Run MC")
                hist.update("")
                fan.refresh()
                return
            qc = mc["quantiles"]["columns"]
            q.add_columns("pct", "Strategy", "Index", "Strategy real", "Index real")
            for r in mc["quantiles"]["rows"]:
                q.add_row(f"{r[0]}", *[Text(f"{r[qc.index(c)]:,.0f}", justify="right") for c in
                                       ("strategy_after_tax", "index_after_tax", "strategy_real", "index_real")])
            beat = mc["p_beat_index"]
            t = [f"{mc['n_paths']} paths, seed {mc['seed']}. P(strategy beats the index, after tax) = {100 * beat:.1f}%"]
            if mc.get("p_shortfall") is not None:
                t.append(f"P(shortfall vs goal {mc['goal']:,.0f}): strategy {100 * mc['p_shortfall']:.1f}%, "
                         f"index {100 * mc['p_shortfall_index']:.1f}%")
            txt.update("\n".join(t))
            fn = mc.get("fan")
            if fn and fn["rows"]:
                fc = fn["columns"]
                xs = [dt.fromisoformat(r[0][:10]).toordinal() / 365.25 for r in fn["rows"]]
                col = rgb(sc.color) if sc else (0, 114, 178)
                for name, cc, color in (("strategy median", "strategy_p50", col), ("strategy 5%", "strategy_p5", col),
                                        ("strategy 95%", "strategy_p95", col), ("index median", "index_p50", rgb(PASSIVE_COLOR))):
                    if cc in fc:
                        j = fc.index(cc)
                        fan.plt.plot(xs, [r[j] for r in fn["rows"]], label=name, color=color,
                                     marker="braille" if "median" in name else "dot")
                fan.plt.title("after-tax wealth: median and 5-95% band of the strategy, median of the index")
            sq = mc["sequence"]
            seq.add_columns(*sq["columns"])
            for r in sq["rows"]:
                seq.add_row(*[("" if v is None else f"{v:,.0f}" if isinstance(v, float) and abs(v) > 5 else
                               f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r])
            lines = []
            for k in ("strategy_after_tax", "index_after_tax"):
                lines += text_hist(mc["histograms"][k], 40, f"terminal wealth, {k.split('_')[0]} (after tax)")
            hist.update("\n".join(lines))
            fan.refresh()

    p = PlanPane()
    app._draw_plan = p.draw
    try:
        app.bind("P", f"tab('{TAB}')", description="Plan")
    except Exception:                                                   # noqa: BLE001  (a missing key binding is not fatal)
        pass
    return p
