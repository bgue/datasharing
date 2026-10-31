"""Interactive charts (Altair / Vega-Lite 5) for the plan notebook: hover for values, drag to pan, wheel to zoom.

# V1: whole module. Same look as research/plan_plots.py and research/tax_plots.py (Okabe-Ito palette, the passive index
always grey and dashed, the goal amber, money put in black dotted, a legend naming every series, band and marker), but
every mark carries a tooltip with exact values. Each function takes the objects the Lab already returns and gives back an
`alt.Chart` (layered, concatenated or faceted); nothing reads or writes a file except `save_png`.

    flows_chart(result)                  dollars in and out each year by kind, and the running net money in
    wealth_chart(result, after_tax=True) money in, strategy, index (+ goal, flows as markers, first-at-goal markers)
    metrics_chart(result)                strategy vs index, bars per metric group
    tax_chart(result)                    tax paid each year by kind, strategy and index side by side
    fan_chart(mc)                        quantile bands (5-95, 25-75) and median of resampled paths, vs money put in
    terminal_hist(mc)                    terminal wealth, strategy vs index overlaid, medians, goal, contributed
    paired_hist(mc)                      strategy minus index on the same path
    sequence_chart(mc)                   terminal wealth by where the worst 12 months fall
    reversal_chart(mc)                   the historical path in order vs reversed
    invest_vs_hold_chart(r_invest, r_hold)  cash timing: wealth and the cash the hold run carries
    save_png(chart, path)                static picture via vl-convert (the fallback for viewers that cannot run Vega)
    mimebundle(chart)                    {vega-lite v5 json, image/png} for one notebook output; show(chart) displays it

X1: comparison charts for the case-study notebook (two contribution schedules, each invested in the screen and in the index fund):

    leg_names(screen)                    the four leg names in one place ("Momentum, static contributions", "Index fund, ...")
    compare_chart({schedule: result}, screen)       wealth of the legs: colour = schedule, solid = screen, dashed lighter = index fund
    compare_flows_chart({schedule: result}, screen) dollars in and out by schedule, and the running net money in per leg
    compare_bars(df, screen)             grouped bars of one value per group (an account) for the legs

Daily series are cut to month-ends (about 400 rows a line), so a notebook with these charts stays well under 5 MB.
"""
from __future__ import annotations

import base64
import json

import altair as alt
import numpy as np
import pandas as pd

from .tax_plots import AMBER, INDEX_COL, INK, INK2, PALETTE

STRAT = PALETTE[0]
BEHIND = PALETTE[1]
INDEX = INDEX_COL                          # the grey of the base case, always dashed
INDEX_LABEL = "Buy and hold the index"
STRAT_LABEL = "Strategy"
CONTRIB_LABEL = "Net money put in"
DASH_INDEX = [6, 4]
DASH_DOT = [2, 3]
DASH_DASHDOT = [8, 3, 2, 3]
MONEY_AXIS = "$~s"                         # $1.5M, $500k
MONEY_TIP = "$,.0f"
W, H = 560, 340


def _setup():
    alt.data_transformers.enable("default", max_rows=None)       # the frames below are small: month-ends, 50 bins


_setup()


def _spec(rows) -> dict:
    """rows: (name, colour, dash, opacity, width) -> the scales every layer of a chart shares, so one legend names all."""
    names = [r[0] for r in rows]
    return {"names": names,
            "color": alt.Scale(domain=names, range=[r[1] for r in rows]),
            "dash": alt.Scale(domain=names, range=[r[2] for r in rows]),
            "opacity": alt.Scale(domain=names, range=[r[3] for r in rows]),
            "width": alt.Scale(domain=names, range=[r[4] for r in rows])}


def _legend(title="Series", cols=1):
    return alt.Legend(title=title, orient="bottom", columns=cols, symbolStrokeWidth=3, symbolType="stroke", symbolOpacity=1, labelLimit=420,
                      titleColor=INK, labelColor=INK2)


def _enc(sp, legend=True, title="Series"):
    """Encodings for a layer whose rows carry a `series` column."""
    return dict(color=alt.Color("series:N", scale=sp["color"], legend=_legend(title) if legend else None, sort=sp["names"]),
                strokeDash=alt.StrokeDash("series:N", scale=sp["dash"], legend=_legend(title) if legend else None),
                opacity=alt.Opacity("series:N", scale=sp["opacity"], legend=None),
                strokeWidth=alt.StrokeWidth("series:N", scale=sp["width"], legend=None))


def style(chart, title=None, subtitle=None):
    """The shared look (grid, ink, legend and title fonts) on a top-level chart; a no-op on one already styled."""
    if title is None and getattr(chart, "config", alt.Undefined) not in (alt.Undefined, None):
        return chart
    ch = chart.configure_axis(grid=True, gridColor="#E6E6E6", domainColor=INK2, tickColor=INK2, labelColor=INK2,
                              titleColor=INK, labelFontSize=11, titleFontSize=12) \
        .configure_view(stroke=None).configure_legend(labelFontSize=11, titleFontSize=12) \
        .configure_title(anchor="start", fontSize=14, color=INK, subtitleColor=INK2)
    if title is not None:
        ch = ch.properties(title=alt.TitleParams(title, subtitle=subtitle) if subtitle else title)
    return ch


def _months(df: pd.DataFrame) -> pd.DataFrame:
    """Month-end rows of a daily frame (the last trading day of each month)."""
    d = df.copy()
    d.index = pd.DatetimeIndex(d.index)
    return d.groupby(d.index.to_period("M")).tail(1).round(0)          # whole dollars: the tooltips show no cents


def _log_axis(dom, title, extra=None):
    """Money axis on a log scale with ticks at 1 and 3 times each power of ten (labels do not collide)."""
    vals = [m * 10.0 ** k for k in range(2, 9) for m in (1, 3) if dom[0] <= m * 10.0 ** k <= dom[1]]
    return alt.Axis(format=MONEY_AXIS, values=vals, title=title)


def _col(sp, legend=True, title="Series"):
    """The colour encoding alone, with the same legend as `_enc` so layers that mix marks (bars + rules) merge into one legend."""
    return alt.Color("series:N", scale=sp["color"], legend=_legend(title) if legend else None, sort=sp["names"])


def _auto_domain(frame: pd.DataFrame, cols) -> tuple:
    v = frame[list(cols)].to_numpy(dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    lo = 10.0 ** np.floor(np.log10(max(v.min(), 1.0)))
    return (float(lo), float(v.max() * 1.15))


def _fold(wide: pd.DataFrame, names: dict, chart=None):
    """A chart over one wide frame (date + one column per series, columns c0, c1, ...) that folds the columns into
    long `series` / `dollars` rows in the browser: the data travels once. `names`: {column: series name}."""
    ch = chart if chart is not None else alt.Chart(wide)
    lut = "{" + ",".join(f"'{k}':{json.dumps(v)}" for k, v in names.items()) + "}"
    return ch.transform_fold(list(names), as_=["key", "dollars"]).transform_calculate(series=f"{lut}[datum.key]")


def _money(x) -> str:
    a = abs(x)
    return f"${x / 1e6:.1f}M" if a >= 1e6 else f"${x / 1e3:.0f}k" if a >= 1e3 else f"${x:.0f}"


def _hover(base, wide, x, y_field, tips, rule_color=INK2):
    """Nearest-point hover on x: an invisible selector point, a rule that follows the pointer, both with `tips`."""
    nearest = alt.selection_point(name="hover", fields=[x.shorthand.split(":")[0]], nearest=True, on="pointerover",
                                  empty=False, clear="pointerout")
    sel = alt.Chart(wide).mark_point(size=160, filled=True, color=rule_color, opacity=0).encode(
        x=x, y=alt.Y(f"{y_field}:Q"), tooltip=tips).add_params(nearest)
    rule = alt.Chart(wide).mark_rule(color=rule_color, strokeWidth=1.5).encode(x=x, tooltip=tips).transform_filter(nearest)
    dot = alt.Chart(wide).mark_point(size=60, filled=True, color=INK, opacity=1).encode(
        x=x, y=alt.Y(f"{y_field}:Q"), tooltip=tips).transform_filter(nearest)
    return sel, rule, dot


# ================================================================================================= phases
# V3: contribution phases as labelled background bands (wealth_chart) and as the stacks of the flows chart
PHASE_COLORS = [PALETTE[2], PALETTE[3], PALETTE[5], PALETTE[4], "#8C6D31", "#7F7F7F"]       # light bands: kept off the strategy blue and the index grey
_PER = {"biweekly": "2wk", "monthly": "mo", "quarterly": "qtr", "annual": "yr"}


def phase_bands(result) -> list:
    """One dict per phase of `result.spec.phases` (empty list when it has none): label ("Phase: early years ($500/mo)"),
    from / to (dates the band covers, to = the phase end, the start of withdrawals or the last bar, whichever is first),
    years_from / years_to, amount, note, count and contributed (the periodic dollars applied inside the phase)."""
    spec = result.spec
    if not spec.phases:
        return []
    start = pd.Timestamp(spec.start)
    last = pd.Timestamp(result.series.index[-1])
    wf = pd.Timestamp(spec.withdraw_from) if spec.withdraw_from else None
    fl = result.flows
    per = fl[(fl["kind"] == "periodic") & fl["applied"]]
    out, seen = [], {}
    for i, ph in enumerate(spec.phases):
        lo = start + pd.DateOffset(months=round(12 * ph.years_from))
        hi = None if ph.years_to is None else start + pd.DateOffset(months=round(12 * ph.years_to))
        end = min([d for d in (hi, wf, last) if d is not None])
        inside = per[(pd.to_datetime(per["date"]) >= lo) & (pd.to_datetime(per["date"]) < end)]
        name = ph.note or f"phase {i + 1}"
        label = f"Phase: {name} (${ph.amount:,.0f}/{_PER.get(spec.frequency, 'period')})"
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label} #{seen[label]}"
        out.append(dict(label=label, **{"from": lo, "to": end}, years_from=float(ph.years_from),
                        years_to=None if ph.years_to is None else float(ph.years_to), amount=float(ph.amount), note=ph.note,
                        count=int(len(inside)), contributed=float(inside["amount"].sum()), start=lo, end=end))
    return [b for b in out if b["end"] > b["start"]]


def _phase_rects(bands):
    """Background rectangles, one per phase, with their own colour scale and legend ("Contribution phase")."""
    names = [b["label"] for b in bands]
    scale = alt.Scale(domain=names, range=[PHASE_COLORS[i % len(PHASE_COLORS)] for i in range(len(names))])
    d = pd.DataFrame([dict(series=b["label"], start=b["start"], end=b["end"], years=f"{b['years_from']:g} to "
                           f"{'the end' if b['years_to'] is None else format(b['years_to'], 'g')}", amount=b["amount"],
                           note=b["note"] or "-", contributed=b["contributed"], count=b["count"]) for b in bands])
    return alt.Chart(d).mark_rect(opacity=0.16).encode(
        x=alt.X("start:T"), x2="end:T", fill=alt.Fill("series:N", scale=scale, sort=names, legend=alt.Legend(
            title="Contribution phase (shaded)", orient="bottom", columns=1, symbolType="square", symbolOpacity=0.45, labelLimit=420,
            titleColor=INK, labelColor=INK2)),
        tooltip=[alt.Tooltip("series:N", title="Phase"), alt.Tooltip("start:T", title="From", format="%d %b %Y"),
                 alt.Tooltip("end:T", title="To", format="%d %b %Y"), alt.Tooltip("years:N", title="Plan years"),
                 alt.Tooltip("amount:Q", title="Amount per period (start-date dollars)", format="$,.0f"),
                 alt.Tooltip("note:N", title="Note"), alt.Tooltip("contributed:Q", title="Total contributed in the phase", format="$,.0f"),
                 alt.Tooltip("count:Q", title="Contributions")])


# ================================================================================================= flows
def flows_chart(result, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title="Dollars in and out each year, by kind"):
    kinds = {"initial": ("Lump sum", PALETTE[0]), "periodic": ("Monthly, indexed to CPI", PALETTE[4]),
             "extra": ("Windfalls", AMBER), "withdrawal": ("Withdrawals", PALETTE[1])}
    fl = result.flows
    fl = fl[fl["applied"]].copy()
    bands = phase_bands(result)
    if bands:                                            # V3: the periodic dollars stack by phase, one colour per phase
        kinds = {"initial": kinds["initial"], **{f"phase{i}": (b["label"], PHASE_COLORS[i % len(PHASE_COLORS)]) for i, b in enumerate(bands)},
                 "extra": kinds["extra"], "withdrawal": kinds["withdrawal"]}
        dts = pd.to_datetime(fl["date"])
        fl["kind"] = [next((f"phase{i}" for i, b in enumerate(bands) if b["start"] <= d < b["end"]), "phase0") if k == "periodic" else k
                      for k, d in zip(fl["kind"], dts)]
    fl["year"] = pd.to_datetime(fl["executed"]).dt.year
    by = fl.pivot_table(index="year", columns="kind", values="amount", aggfunc="sum").reindex(columns=list(kinds)).fillna(0.0)
    cnt = fl.pivot_table(index="year", columns="kind", values="amount", aggfunc="count").reindex(columns=list(kinds)).fillna(0)
    rows = [dict(year=int(y), kind=kinds[k][0], amount=float(by.loc[y, k]), flows=int(cnt.loc[y, k]),
                 order=list(kinds).index(k)) for y in by.index for k in kinds if by.loc[y, k] != 0]
    d = pd.DataFrame(rows)
    names = [v[0] for v in kinds.values()]
    bars = alt.Chart(d).mark_bar(width=14).encode(
        x=alt.X("year:O", title="Year"), y=alt.Y("amount:Q", title="Dollars in (up) and out (down) in the year", axis=alt.Axis(format=MONEY_AXIS)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=names, range=[v[1] for v in kinds.values()]), sort=names,
                        legend=alt.Legend(title="Kind of flow", orient="bottom", columns=4)),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("year:O", title="Year"), alt.Tooltip("kind:N", title="Kind"),
                 alt.Tooltip("amount:Q", title="Dollars in the year", format=MONEY_TIP),
                 alt.Tooltip("flows:Q", title="Number of flows")]).properties(width=W, height=220)
    net = result.series["contributed"].resample("YE").last()
    nd = pd.concat([pd.DataFrame({"year": net.index.year, "series": "Net money in (running total)", "dollars": net.to_numpy()}),
                    pd.DataFrame({"year": net.index.year, "series": f"{index_label}: the same dollars, the same days",
                                  "dollars": net.to_numpy()})])
    sp = _spec([("Net money in (running total)", INK, [1, 0], 1, 2.4), (f"{index_label}: the same dollars, the same days", INDEX, [2, 6], 1, 2.4)])
    enc = dict(x=alt.X("year:O", title="Year"), y=alt.Y("dollars:Q", title="Running total of net money in, dollars", axis=alt.Axis(format=MONEY_AXIS)),
               tooltip=[alt.Tooltip("year:O", title="End of year"), alt.Tooltip("series:N", title="Series"),
                        alt.Tooltip("dollars:Q", title="Net money in so far", format=MONEY_TIP)], **_enc(sp))
    line = alt.layer(alt.Chart(nd).mark_line().encode(**enc), alt.Chart(nd).mark_point(filled=True, size=45).encode(**enc)).properties(width=W, height=160)
    return style(alt.vconcat(bars, line).resolve_scale(color="independent", strokeDash="independent"), title)


# ================================================================================================= wealth
def first_reached(s: pd.Series, level):
    hit = s[s >= level]
    return hit.index[0] if len(hit) else None


def wealth_chart(result, after_tax=True, pretax=False, goal="spec", log=True, y_domain=None, flows=True,
                 mark_goal=False, flow_legend=True, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None, width=W, height=H,
                 legend=True, phases=True):
    """Money put in, strategy and index wealth; `pretax=True` adds the before-tax lines too. `goal="spec"` takes the plan's
    goal (None for none); `mark_goal` puts a marker where each side first held it; `flows` marks every flow.
    V3: `phases=True` shades each contribution phase of `result.spec.phases` as a labelled background band (legend entry,
    tooltip with the phase's dates, amount, note and the dollars contributed in it)."""
    s = result.series
    bands = phase_bands(result) if phases else []
    g = result.spec.goal if goal == "spec" else goal
    tag = "after tax" if after_tax else "before tax"
    sl, il = f"{strategy_label} ({tag})", f"{index_label} ({tag})"
    m = _months(s)
    cs, ci = ("strategy_after_tax", "index_after_tax") if after_tax else ("strategy_pre", "index_pre")
    wide = pd.DataFrame({"date": m.index, "contributed": np.maximum(m["contributed"].astype(float).to_numpy(), 1.0),
                         "strategy": m[cs].astype(float).to_numpy(), "index": m[ci].astype(float).to_numpy()})
    keys = {"contributed": CONTRIB_LABEL}
    rows = [(CONTRIB_LABEL, INK, DASH_DOT, 1, 1.8)]
    if pretax and after_tax:
        wide["strategy_pre"], wide["index_pre"] = m["strategy_pre"].astype(float).to_numpy(), m["index_pre"].astype(float).to_numpy()
        keys |= {"strategy_pre": f"{strategy_label} (before tax)", "index_pre": f"{index_label} (before tax)"}
        rows += [(f"{strategy_label} (before tax)", STRAT, [1, 0], 0.45, 1.4), (f"{index_label} (before tax)", INDEX, DASH_INDEX, 0.55, 1.4)]
    keys |= {"strategy": sl, "index": il}
    rows += [(sl, STRAT, [1, 0], 1, 2.8), (il, INDEX, DASH_INDEX, 1, 2.8)]
    if g is not None:
        goal_name = f"Goal {_money(g)}"
        wide["goal"] = float(g)
        keys["goal"] = goal_name
        rows.append((goal_name, AMBER, DASH_DASHDOT, 1, 1.8))
    wide["gain_s"] = wide["strategy"] - wide["contributed"]
    wide["gain_i"] = wide["index"] - wide["contributed"]
    wide["ahead"] = wide["strategy"] - wide["index"]
    wide["y"] = wide["strategy"]
    sp = _spec(rows)
    if y_domain is None:
        y_domain = _auto_domain(wide, [c for c in wide.columns if c not in ("date", "y", "gain_s", "gain_i", "ahead")])
    yscale = alt.Scale(type="log", domain=list(y_domain), nice=False) if log else alt.Scale(zero=True)
    x = alt.X("date:T", title="Date", axis=alt.Axis(format="%Y", tickCount=12))
    yt = f"Wealth, dollars{' (log scale)' if log else ''}"
    y = alt.Y("dollars:Q", scale=yscale, title=yt, axis=_log_axis(y_domain, yt) if log else alt.Axis(format=MONEY_AXIS, title=yt))
    lines = _fold(wide, keys).mark_line().encode(x=x, y=y, **_enc(sp, legend=legend, title="Series")).properties(width=width, height=height)
    layers = [_phase_rects(bands), lines] if bands else [lines]       # V3: bands behind the lines
    # hover: a rule per month with every series in the tooltip
    tips = [alt.Tooltip("date:T", title="Date", format="%d %b %Y"),
            alt.Tooltip("contributed:Q", title=CONTRIB_LABEL, format=MONEY_TIP),
            alt.Tooltip("strategy:Q", title=sl, format=MONEY_TIP),
            alt.Tooltip("index:Q", title=il, format=MONEY_TIP),
            alt.Tooltip("gain_s:Q", title="Strategy gain over money put in", format="$,.0f"),
            alt.Tooltip("gain_i:Q", title="Index gain over money put in", format="$,.0f"),
            alt.Tooltip("ahead:Q", title="Strategy minus index", format="$,.0f")]
    sel, rule, dot = _hover(None, wide, alt.X("date:T"), "y", tips)
    layers += [rule, sel, dot]
    # flows as markers on the money-put-in line
    shape_names, shape_vals = [], []
    if flows:
        fl = result.flows[result.flows["applied"]].copy()
        fl["executed"] = pd.to_datetime(fl["executed"])
        kinds = {"initial": ("Lump sum", "diamond"), "periodic": ("Monthly contribution", "circle"),
                 "extra": ("Windfall", "triangle-up"), "withdrawal": ("Withdrawal", "triangle-down")}
        cont = s["contributed"].clip(lower=1.0)
        fd = pd.DataFrame({"date": fl["executed"].dt.floor("D"), "kind": fl["kind"].map(lambda k: kinds[k][0]),
                           "amount": fl["amount"].abs().astype(float), "signed": fl["amount"].astype(float),
                           "note": fl["note"].fillna("").astype(str),
                           "dollars": [float(cont.asof(d)) for d in fl["executed"]]})
        fd["note"] = fd["note"].where(fd["note"] != "", "-")
        shape_names += [v[0] for v in kinds.values()]
        shape_vals += [v[1] for v in kinds.values()]
        layers.append(alt.Chart(fd).mark_point(filled=True, color=INK, opacity=0.75).encode(
            x="date:T", y=alt.Y("dollars:Q", scale=yscale),
            shape=alt.Shape("kind:N", scale=alt.Scale(domain=shape_names, range=shape_vals),
                            legend=alt.Legend(title="Flows (marker size = dollars)", orient="bottom", columns=4) if flow_legend else None),
            size=alt.Size("amount:Q", scale=alt.Scale(domain=[0, 25000], range=[4, 320], clamp=True), legend=None),
            tooltip=[alt.Tooltip("date:T", title="Date", format="%d %b %Y"), alt.Tooltip("kind:N", title="Kind"),
                     alt.Tooltip("signed:Q", title="Amount", format="$,.0f"), alt.Tooltip("note:N", title="Note")]))
    if mark_goal and g is not None:
        pts = []
        for who, nm, col in (("strategy", sl, STRAT), ("index", il, INDEX)):
            d0 = first_reached(s[f"{who}_after_tax" if after_tax else f"{who}_pre"], g)
            if d0 is not None:
                pts.append(dict(date=d0, dollars=float(g), series=nm, marker="First at the goal", label=f"First at the goal: {nm}", when=d0.strftime("%d %b %Y"),
                                years=(d0 - s.index[0]).days / 365.25))
        if pts:
            pd_ = pd.DataFrame(pts)
            layers.append(alt.Chart(pd_).mark_point(size=170, filled=True, stroke=INK, strokeWidth=1.5, shape="circle").encode(
                x="date:T", y=alt.Y("dollars:Q", scale=yscale), color=alt.Color("series:N", scale=sp["color"], legend=None),
                shape=alt.Shape("marker:N", scale=alt.Scale(domain=["First at the goal"], range=["circle"]),
                                legend=alt.Legend(title="Marker", orient="bottom", symbolFillColor=INK2)),
                tooltip=[alt.Tooltip("label:N", title="Marker"), alt.Tooltip("when:N", title="First at the goal"),
                         alt.Tooltip("years:Q", title="Years into the plan", format=".1f"),
                         alt.Tooltip("dollars:Q", title="Goal", format=MONEY_TIP)]))
    ch = alt.layer(*layers).resolve_scale(shape="independent")
    if title:
        ch = ch.properties(title=title)
    return ch.interactive(bind_y=False)


# ================================================================================================= metrics
_MEASURES = {"XIRR": "Before tax", "XIRRAfterTax": "After tax", "XIRRReal": "After inflation",
             "XIRRAfterTaxReal": "After tax and inflation", "TWR": "TWR (time-weighted)"}


def metrics_chart(result, groups=None, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None):
    """Strategy against index, one panel per group of metrics. `groups`: {panel title: {column: label}}; rates only."""
    groups = groups or {"XIRR, four ways (money-weighted, a year)": {c: _MEASURES[c] for c in ("XIRR", "XIRRAfterTax", "XIRRReal", "XIRRAfterTaxReal")},
                        "XIRR against TWR, before tax": {"XIRR": "XIRR (money-weighted)", "TWR": "TWR (time-weighted)"}}
    mt = result.metrics
    names = [strategy_label, index_label]
    cscale = alt.Scale(domain=names, range=[STRAT, INDEX])
    panels = []
    for gi, (gt, cols) in enumerate(groups.items()):
        rows = []
        for c, lab in cols.items():
            for who, nm in (("strategy", strategy_label), ("index", index_label)):
                rows.append(dict(measure=lab, holding=nm, value=float(mt.loc[who, c]), diff=float(mt.loc["difference", c]),
                                 metric=c))
        d = pd.DataFrame(rows)
        base = alt.Chart(d).encode(
            x=alt.X("measure:N", sort=list(cols.values()), title=None, axis=alt.Axis(labelAngle=0, labelLimit=170, labelFontSize=10)),
            xOffset=alt.XOffset("holding:N", sort=names),
            y=alt.Y("value:Q", title="Yearly return" if gi == 0 else None, axis=alt.Axis(format="%")))
        bars = base.mark_bar(size=30).encode(
            color=alt.Color("holding:N", scale=cscale, sort=names, legend=alt.Legend(title="Holding", orient="bottom") if gi == 0 else None),
            opacity=alt.Opacity("holding:N", scale=alt.Scale(domain=names, range=[1, 0.6]), legend=None),
            tooltip=[alt.Tooltip("holding:N", title="Holding"), alt.Tooltip("measure:N", title="Measure"),
                     alt.Tooltip("metric:N", title="Metric column"), alt.Tooltip("value:Q", title="Value", format=".3%"),
                     alt.Tooltip("diff:Q", title="Strategy minus index", format=".3%")])
        txt = base.mark_text(dy=-6, fontSize=10, color=INK2).encode(text=alt.Text("value:Q", format=".1%"))
        panels.append(alt.layer(bars, txt).properties(width=420 if gi == 0 else 340, height=280, title=gt))
    ch = alt.hconcat(*panels).resolve_scale(color="shared", y="shared")
    return style(ch, title)


# ================================================================================================= tax
def tax_chart(result, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title="Tax paid each year, by kind"):
    kinds = {"tax_dividends": "Canadian dividends", "tax_foreign_dividends": "Foreign dividends",
             "tax_capital_gains": "Capital gains", "tax_interest": "Interest", "tax_rrsp_withdrawal": "RRSP withdrawals",
             "refund": "Loss carry-back refund"}
    cols = [PALETTE[0], PALETTE[1], PALETTE[2], PALETTE[3], PALETTE[4], AMBER]
    t = result.tax
    rows = []
    for leg, ll in (("strategy", strategy_label), ("index", index_label)):
        for k, kn in kinds.items():
            v = t[(leg, k)]
            for y, a in v.items():
                if a != 0:
                    rows.append(dict(year=int(y), leg=ll, kind=kn, dollars=float(-a if k == "refund" else a),
                                     total=float(t.loc[y, (leg, "tax_paid")])))
    d = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["year", "leg", "kind", "dollars", "total"])
    bars = alt.Chart(d).mark_bar().encode(
        x=alt.X("year:O", title="Year"), y=alt.Y("dollars:Q", title="Tax paid in the year, dollars (refunds below zero)", axis=alt.Axis(format=MONEY_AXIS)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=list(kinds.values()), range=cols), sort=list(kinds.values()),
                        legend=alt.Legend(title="Kind of tax", orient="bottom", columns=3)),
        column=alt.Column("leg:N", sort=[strategy_label, index_label], title=None, header=alt.Header(labelFontSize=13, labelFontWeight="bold")),
        tooltip=[alt.Tooltip("leg:N", title="Holding"), alt.Tooltip("year:O", title="Year"), alt.Tooltip("kind:N", title="Kind"),
                 alt.Tooltip("dollars:Q", title="Dollars", format="$,.0f"), alt.Tooltip("total:Q", title="Tax paid that year (all kinds)", format="$,.0f")]
    ).properties(width=420, height=260)
    return style(bars.resolve_scale(y="shared"), title)


# ================================================================================================= Monte Carlo
def _sub(mc) -> str:
    g = mc.card.generator.get("__t", "?")
    return f"{mc.n_paths:,} {g} paths, seed {mc.seed}"


FAN_T = "Series"
HIST_T = "Series"


def fan_chart(mc, log=True, y_domain=None, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None, width=W, height=H, legend=True,
              colors=None):
    sc_, ic_ = colors or (STRAT, INDEX)                     # X1: (strategy colour, index colour) of a comparison notebook
    f = mc.fan.round(0)
    f = f[f["contributed"] > 0].clip(lower=1.0)                      # the opening row (nothing in yet) cannot sit on a log axis
    fd = f.reset_index()
    fd = fd.rename(columns={fd.columns[0]: "date"})
    fd["date"] = pd.DatetimeIndex(fd["date"])
    sl, il = f"{strategy_label}: median", f"{index_label}: median"
    b = lambda lab, a, c: f"{lab}: {a}-{c}% of paths"
    rows = [(b(strategy_label, 5, 95), sc_, [1, 0], 0.14, 1), (b(strategy_label, 25, 75), sc_, [1, 0], 0.28, 1),
            (b(index_label, 5, 95), ic_, [1, 0], 0.14, 1), (b(index_label, 25, 75), ic_, [1, 0], 0.28, 1),
            (sl, sc_, [1, 0], 1, 2.6), (il, ic_, DASH_INDEX, 1, 2.6), (CONTRIB_LABEL, INK, DASH_DOT, 1, 1.8)]
    keys = {"index_p50": il, "strategy_p50": sl, "contributed": CONTRIB_LABEL}
    if mc.goal is not None:
        gname = f"Goal {_money(mc.goal)}"
        rows.append((gname, AMBER, DASH_DASHDOT, 1, 1.8))
        fd["goal"] = float(mc.goal)
        keys["goal"] = gname
    fd["y"] = fd["strategy_p50"]
    sp = _spec(rows)
    lo = float(fd[["strategy_p5", "index_p5"]].min().min() * 0.8)
    dom = list(y_domain) if y_domain else [10 ** np.floor(np.log10(max(lo, 1000.0))), float(fd[["strategy_p95", "index_p95"]].max().max()) * 1.1]
    yscale = alt.Scale(type="log", domain=dom, nice=False, clamp=True) if log else alt.Scale(zero=True)
    x = alt.X("date:T", title="Date", axis=alt.Axis(format="%Y", tickCount=12))
    ytitle = f"Wealth, after tax, nominal dollars{' (log scale)' if log else ''}"
    yax = _log_axis(dom, ytitle) if log else alt.Axis(format=MONEY_AXIS, title=ytitle)
    layers = []
    for who, lab in (("index", index_label), ("strategy", strategy_label)):
        for (a, c) in ((5, 95), (25, 75)):
            nm = b(lab, a, c)
            layers.append(alt.Chart(fd).transform_calculate(series=json.dumps(nm)).mark_area().encode(
                x=x, y=alt.Y(f"{who}_p{a}:Q", scale=yscale, title=ytitle, axis=yax), y2=f"{who}_p{c}:Q",
                color=_col(sp, legend, FAN_T), opacity=alt.value(0.14 if (a, c) == (5, 95) else 0.28)))
    layers.append(_fold(fd, keys).mark_line().encode(
        x=x, y=alt.Y("dollars:Q", scale=yscale), **_enc(sp, legend=legend, title=FAN_T)))
    tips = [alt.Tooltip("date:T", title="Month", format="%b %Y"), alt.Tooltip("contributed:Q", title=CONTRIB_LABEL, format=MONEY_TIP)]
    for who, lab in (("strategy", strategy_label), ("index", index_label)):
        tips += [alt.Tooltip(f"{who}_p50:Q", title=f"{lab}: median", format=MONEY_TIP),
                 alt.Tooltip(f"{who}_p25:Q", title=f"{lab}: 25th percentile", format=MONEY_TIP),
                 alt.Tooltip(f"{who}_p75:Q", title=f"{lab}: 75th percentile", format=MONEY_TIP),
                 alt.Tooltip(f"{who}_p5:Q", title=f"{lab}: 5th percentile", format=MONEY_TIP),
                 alt.Tooltip(f"{who}_p95:Q", title=f"{lab}: 95th percentile", format=MONEY_TIP)]
    sel, rule, dot = _hover(None, fd, alt.X("date:T"), "y", tips)
    layers += [rule, sel, dot]
    ch = alt.layer(*layers).properties(width=width, height=height, title=title or f"Wealth fan vs money contributed ({_sub(mc)})")
    return ch.interactive(bind_y=False)


def _hist_df(values: pd.Series, edges: np.ndarray, label: str) -> pd.DataFrame:
    v = values.clip(upper=edges[-1]).to_numpy()
    cnt, _ = np.histogram(v, bins=edges)
    return pd.DataFrame({"lo": edges[:-1], "hi": edges[1:], "count": cnt, "share": cnt / max(len(v), 1), "series": label})


def terminal_hist(mc, bins=50, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None, width=W, height=H, legend=True, colors=None):
    sc_, ic_ = colors or (STRAT, INDEX)                     # X1
    t = mc.terminal
    hi = float(np.nanpercentile(pd.concat([t.strategy_after_tax, t.index_after_tax]), 99.5))
    edges = np.linspace(0, hi, bins + 1)
    sl, il = f"{strategy_label} (after tax)", f"{index_label} (after tax)"
    contributed = float(mc.fan["contributed"].iloc[-1])
    rows = [(il, ic_, DASH_INDEX, 0.5, 2), (sl, sc_, [1, 0], 0.65, 2), (f"{index_label}: median", ic_, DASH_INDEX, 1, 2.4),
            (f"{strategy_label}: median", sc_, [1, 0], 1, 2.4)]
    gname = None
    if mc.goal is not None:
        gname = f"Goal {_money(mc.goal)}: short in {mc.p_shortfall:.0%} (strategy), {mc.p_shortfall_index:.0%} (index)"
        rows.append((gname, AMBER, [1, 0], 1, 2.6))
    cname = f"Contributed {_money(contributed)}"
    rows.append((cname, INK, DASH_DOT, 1, 1.8))
    sp = _spec(rows)
    h = pd.concat([_hist_df(t.index_after_tax, edges, il), _hist_df(t.strategy_after_tax, edges, sl)], ignore_index=True)
    h["zero"] = 0
    h["last_bin_note"] = np.where(h["hi"] >= hi - 1e-9, "includes every path above (clipped at the 99.5th percentile)", "")
    xs = alt.X("lo:Q", scale=alt.Scale(domain=[0, hi], nice=False), title="Terminal wealth, after tax (paths above the 99.5th percentile are in the last bar)", axis=alt.Axis(format=MONEY_AXIS))
    def _bars(data, hist_title, oscale, lg, tipfields):
        out = []
        for nm, op in oscale.items():
            out.append(alt.Chart(data[data["series"] == nm]).mark_bar(stroke="white", strokeWidth=0.4, orient="vertical").encode(
                x=xs, x2="hi:Q", y=alt.Y("count:Q", stack=None, title=f"Paths (of {mc.n_paths:,})"), y2="zero:Q",
                color=_col(sp, lg, hist_title), opacity=alt.value(op), tooltip=tipfields))
        return out
    tipf = [alt.Tooltip("series:N", title="Holding"), alt.Tooltip("lo:Q", title="Bin from", format=MONEY_TIP),
            alt.Tooltip("hi:Q", title="Bin to", format=MONEY_TIP), alt.Tooltip("count:Q", title="Paths in the bin", format=","),
            alt.Tooltip("share:Q", title="Share of paths", format=".1%"), alt.Tooltip("last_bin_note:N", title="")]
    bar_layers = _bars(h, HIST_T, {il: 0.5, sl: 0.65}, legend, tipf)
    vl = [(f"{index_label}: median", float(t.index_after_tax.median())), (f"{strategy_label}: median", float(t.strategy_after_tax.median()))]
    if gname:
        vl.append((gname, float(mc.goal)))
    vl.append((cname, contributed))
    vd = pd.DataFrame(vl, columns=["series", "x"])
    vd["text"] = vd["x"].map(lambda v: f"${v:,.0f}")
    vrules = alt.Chart(vd).mark_rule().encode(
        x=alt.X("x:Q"), tooltip=[alt.Tooltip("series:N", title="Marker"), alt.Tooltip("x:Q", title="Dollars", format=MONEY_TIP)],
        **_enc(sp, legend=legend, title=HIST_T))
    ch = alt.layer(*bar_layers, vrules).properties(width=width, height=height, title=title or f"Terminal wealth: strategy vs index ({_sub(mc)})")
    return ch.interactive(bind_y=False)


def paired_hist(mc, bins=50, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None, width=W, height=H, legend=True, colors=None):
    sc_, ic_ = colors or (STRAT, INDEX)                     # X1: ahead in the strategy colour, behind in a neutral grey, the base case in the index colour
    bc_ = BEHIND if colors is None else "#999999"
    d = mc.paired.astype(float)
    lim = float(np.nanpercentile(np.abs(d), 99)) or 1.0
    edges = np.linspace(-lim, lim, bins + 1)
    ahead = float((d >= 0).mean())
    an, bn = f"{strategy_label} ahead of the index: {ahead:.0%} of paths", f"{strategy_label} behind the index: {1 - ahead:.0%}"
    zn = f"{index_label} (base case: difference 0)"
    mn = f"Median {_money(float(d.median()))}"
    sp = _spec([(an, sc_, [1, 0], 0.8, 2), (bn, bc_, [1, 0], 0.8, 2), (zn, ic_, DASH_INDEX, 1, 2.2), (mn, INK, [1, 0], 1, 2)])
    h = pd.concat([_hist_df(d[d >= 0], edges, an), _hist_df(d[d < 0], edges, bn)], ignore_index=True)
    h = h[h["count"] > 0]
    h["zero"] = 0
    h["last_bin_note"] = np.where((h["hi"] >= lim - 1e-9) | (h["lo"] <= -lim + 1e-9), "includes every path beyond (clipped at the 99th percentile)", "")
    pxs = alt.X("lo:Q", scale=alt.Scale(domain=[-lim, lim], nice=False), title="Strategy minus index, terminal wealth after tax, same path", axis=alt.Axis(format=MONEY_AXIS))
    tipp = [alt.Tooltip("series:N", title="Side"), alt.Tooltip("lo:Q", title="Difference from", format="$,.0f"),
            alt.Tooltip("hi:Q", title="Difference to", format="$,.0f"), alt.Tooltip("count:Q", title="Paths in the bin", format=","),
            alt.Tooltip("share:Q", title="Share of paths", format=".1%"), alt.Tooltip("last_bin_note:N", title="")]
    bar_layers = [alt.Chart(h[h["series"] == nm]).mark_bar(stroke="white", strokeWidth=0.4, orient="vertical").encode(
        x=pxs, x2="hi:Q", y=alt.Y("count:Q", stack=None, title=f"Paths (of {mc.n_paths:,})"), y2="zero:Q",
        color=_col(sp, legend, "Series"), opacity=alt.value(0.8), tooltip=tipp) for nm in (an, bn)]
    vd = pd.DataFrame([(zn, 0.0), (mn, float(d.median()))], columns=["series", "x"])
    vr = alt.Chart(vd).mark_rule().encode(x="x:Q", tooltip=[alt.Tooltip("series:N", title="Marker"), alt.Tooltip("x:Q", title="Dollars", format="$,.0f")],
                                          **_enc(sp, legend=legend, title="Series"))
    return alt.layer(*bar_layers, vr).properties(width=width, height=height, title=title or f"Paired difference ({_sub(mc)})").interactive(bind_y=False)


def _grouped(d, xcol, xorder, ycol, ytitle, strategy_label, index_label, tips, width, height, title, legend=True, extra_rule=None, colors=None):
    names = [strategy_label, index_label]
    cs = alt.Scale(domain=names, range=list(colors or (STRAT, INDEX)))        # X1: colors = (strategy, index)
    base = alt.Chart(d).encode(x=alt.X(f"{xcol}:N", sort=xorder, title=None, axis=alt.Axis(labelAngle=0, labelLimit=200)),
                               xOffset=alt.XOffset("holding:N", sort=names), y=alt.Y(f"{ycol}:Q", title=ytitle, axis=alt.Axis(format=MONEY_AXIS)))
    bars = base.mark_bar(size=40).encode(
        color=alt.Color("holding:N", scale=cs, sort=names, legend=alt.Legend(title="Holding (after tax)", orient="bottom") if legend else None),
        opacity=alt.Opacity("holding:N", scale=alt.Scale(domain=names, range=[1, 0.6]), legend=None), tooltip=tips)
    layers = [bars]
    if extra_rule is not None:
        nm, val = extra_rule
        rd = pd.DataFrame({"series": [nm], "y": [val]})
        sp = _spec([(nm, INK, DASH_DOT, 1, 1.8)])
        layers.append(alt.Chart(rd).mark_rule().encode(
            y="y:Q", color=alt.Color("series:N", scale=sp["color"], legend=alt.Legend(title="Reference", orient="bottom")),
            strokeDash=alt.StrokeDash("series:N", scale=sp["dash"], legend=None), strokeWidth=alt.value(1.8),
            tooltip=[alt.Tooltip("series:N", title="Marker"), alt.Tooltip("y:Q", title="Dollars", format=MONEY_TIP)]))
    return alt.layer(*layers).properties(width=width, height=height, title=title).resolve_scale(color="independent")


def sequence_chart(mc, stat="median", strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title=None, width=380, height=H, legend=True, colors=None):
    s = mc.sequence
    names = {"first": "first third", "middle": "middle third", "last": "last third"}
    rows = []
    for i in s.index:
        for who, lab in (("strategy", strategy_label), ("index", index_label)):
            rows.append(dict(third=f"{names[i]} (n={int(s.loc[i, 'n'])})", holding=lab, wealth=float(s.loc[i, f"{who}_{stat}"] if s.loc[i, f"{who}_{stat}"] == s.loc[i, f"{who}_{stat}"] else 0.0),
                             n=int(s.loc[i, "n"]), short=float(s.loc[i, "p_shortfall"]) if who == "strategy" and "p_shortfall" in s else np.nan))
    d = pd.DataFrame(rows)
    d["short_txt"] = d["short"].map(lambda v: "-" if v != v else f"{v:.0%}")
    tips = [alt.Tooltip("holding:N", title="Holding"), alt.Tooltip("third:N", title="Worst 12 months fall in the"),
            alt.Tooltip("wealth:Q", title=f"{stat.capitalize()} terminal wealth, after tax", format="$,.0f"),
            alt.Tooltip("n:Q", title="Paths in the group"), alt.Tooltip("short_txt:N", title="Strategy paths below the goal")]
    return _grouped(d, "third", [f"{names[i]} (n={int(s.loc[i, 'n'])})" for i in s.index], "wealth", f"{stat.capitalize()} terminal wealth, after tax",
                    strategy_label, index_label, tips, width, height, title or f"Sequence of returns ({_sub(mc)})", legend, colors=colors)


def reversal_chart(mc, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title="Same returns, same flows, opposite order", width=380, height=H, legend=True, colors=None):
    r = mc.reversal.loc[["forward", "reversed"]]
    lab = {"forward": "Historical order", "reversed": "Same returns, reversed"}
    rows = [dict(order=lab[i], holding=h, wealth=float(r.loc[i, col])) for i in r.index
            for h, col in ((strategy_label, "strategy_after_tax"), (index_label, "index_after_tax"))]
    d = pd.DataFrame(rows)
    tips = [alt.Tooltip("holding:N", title="Holding"), alt.Tooltip("order:N", title="Order of the returns"),
            alt.Tooltip("wealth:Q", title="Terminal wealth, after tax", format="$,.0f")]
    c = float(r["contributed"].iloc[0])
    return _grouped(d, "order", list(lab.values()), "wealth", "Terminal wealth, after tax", strategy_label, index_label, tips, width, height, title,
                    legend, extra_rule=(f"Contributed {_money(c)}", c), colors=colors)


# ================================================================================================= cash timing
def invest_vs_hold_chart(r_invest, r_hold, strategy_label=STRAT_LABEL, index_label=INDEX_LABEL, title="Cash timing", y_domain=None, colors=None):
    sc_, ic_, hc_ = colors or (STRAT, INDEX, PALETTE[1])        # X1: (invest, index, hold) colours
    si, sh = r_invest.series, r_hold.series
    inv, hold_, idx = f"{strategy_label}: invest on arrival (after tax)", f"{strategy_label}: hold cash until the rebalance (after tax)", f"{index_label} (after tax)"
    sp = _spec([(CONTRIB_LABEL, INK, DASH_DOT, 1, 1.8), (inv, sc_, [1, 0], 1, 2.6), (hold_, hc_, [1, 0], 1, 2.2), (idx, ic_, DASH_INDEX, 1, 2.6)])
    mi, mh = _months(si), _months(sh)
    wide = pd.DataFrame({"date": mi.index, "contributed": np.maximum(mi["contributed"].astype(float).to_numpy(), 1.0),
                         "invest": mi["strategy_after_tax"].astype(float).to_numpy(), "index": mi["index_after_tax"].astype(float).to_numpy()})
    wide["hold"] = mh["strategy_after_tax"].astype(float).reindex(mi.index).to_numpy() if mh.index.equals(mi.index) else \
        mh["strategy_after_tax"].astype(float).reindex(mi.index, method="nearest").to_numpy()
    wide["cost"] = wide["invest"] - wide["hold"]
    wide["y"] = wide["invest"]
    keys = {"contributed": CONTRIB_LABEL, "invest": inv, "hold": hold_, "index": idx}
    if y_domain is None:
        y_domain = _auto_domain(wide, ["contributed", "invest", "hold", "index"])
    ys = alt.Scale(type="log", domain=list(y_domain), nice=False)
    x = alt.X("date:T", title="Date", axis=alt.Axis(format="%Y", tickCount=12))
    left = _fold(wide, keys).mark_line().encode(x=x, y=alt.Y("dollars:Q", scale=ys, title="Wealth, dollars (log scale)", axis=_log_axis(y_domain, "Wealth, dollars (log scale)")),
                                                **_enc(sp, title="Series")).properties(width=520, height=H, title="Invest on arrival or hold the cash")
    tips = [alt.Tooltip("date:T", title="Date", format="%d %b %Y"), alt.Tooltip("contributed:Q", title=CONTRIB_LABEL, format=MONEY_TIP),
            alt.Tooltip("invest:Q", title=inv, format=MONEY_TIP), alt.Tooltip("hold:Q", title=hold_, format=MONEY_TIP),
            alt.Tooltip("index:Q", title=idx, format=MONEY_TIP), alt.Tooltip("cost:Q", title="Cost of waiting (invest minus hold)", format="$,.0f")]
    sel, rule, dot = _hover(None, wide, alt.X("date:T"), "y", tips)
    left = alt.layer(left, rule, sel, dot).interactive(bind_y=False)
    cash_name, none_name = "Cash held, hold run", f"{index_label} and the invest run: no cash"
    sp2 = _spec([(cash_name, hc_, [1, 0], 0.5, 1.5), (none_name, ic_, DASH_INDEX, 1, 2.4)])
    cd = pd.DataFrame({"date": mh.index, "cash": mh["cash"].astype(float).to_numpy(), "series": cash_name})
    area = alt.Chart(cd).mark_area(line={"strokeWidth": 1.5}).encode(
        x=x, y=alt.Y("cash:Q", title="Uninvested cash, dollars", axis=alt.Axis(format=MONEY_AXIS)),
        color=_col(sp2, True, "Series"), opacity=alt.value(0.5),
        tooltip=[alt.Tooltip("date:T", title="Month end", format="%b %Y"), alt.Tooltip("series:N", title="Series"),
                 alt.Tooltip("cash:Q", title="Cash held", format="$,.0f")])
    zd = pd.DataFrame({"series": [none_name], "y": [0.0]})
    zero = alt.Chart(zd).mark_rule().encode(y="y:Q", **_enc(sp2, title="Series"),
                                            tooltip=[alt.Tooltip("series:N", title="Series"), alt.Tooltip("y:Q", title="Cash held", format="$,.0f")])
    right = alt.layer(area, zero).properties(width=330, height=H, title="The cash the hold run carries").interactive(bind_y=False)
    return style(alt.hconcat(left, right).resolve_scale(color="independent", strokeDash="independent"), title)


# ================================================================================================= comparison charts
# X1: the case-study notebook compares two contribution schedules, each invested twice (the screen, the passive index fund).
# Encoding in every comparison chart: colour = schedule, solid = the screen, dashed lighter tint of the same colour = the index fund.
SCHEDULES = {"static": "static contributions", "phased": "phased contributions"}
SCHED_COLORS = {"static": PALETTE[0], "phased": PALETTE[1]}
INDEX_FUND = "Index fund"
ENCODING_NOTE = ("Colour is the contribution schedule (blue for static, vermillion for phased); a solid line is the screen, "
                 "a dashed lighter line of the same colour is the index fund that received the same dollars on the same days")
SCREEN_NAMES = {"momentum_12m": "Momentum", "momentum_6m": "Momentum (6 months)", "dogs": "Dogs", "stable_high_yield": "Stable high yield",
                "stable_dividend": "Stable dividend", "screaming_value": "Screaming value", "value_momentum": "Value and momentum"}


def screen_label(strat: str) -> str:
    """The short name of a screen as it appears in a leg name ("Momentum" for momentum_12m)."""
    return SCREEN_NAMES.get(strat, strat.replace("_", " ").capitalize())


def _tint(hexcol: str, f: float = 0.45) -> str:
    """The colour mixed with white: f = the share of white."""
    c = hexcol.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(round(v + (255 - v) * f) for v in (r, g, b)))


def sched_label(sched: str) -> str:
    return SCHEDULES.get(sched, str(sched))


def leg_names(screen: str, schedules=("static", "phased")) -> dict:
    """The legs of the comparison, named in one place: {(schedule, "screen" | "index"): name}, e.g.
    ("static", "screen") -> "Momentum, static contributions", ("static", "index") -> "Index fund, static contributions"."""
    out = {}
    for sc in schedules:
        out[(sc, "screen")] = f"{screen}, {sched_label(sc)}"
        out[(sc, "index")] = f"{INDEX_FUND}, {sched_label(sc)}"
    return out


def leg_colors(sched: str) -> tuple:
    """(screen colour, index fund colour) of a schedule: the index fund is the lighter tint of the same colour."""
    c = SCHED_COLORS.get(sched, PALETTE[2])
    return c, _tint(c)


def _enc_cols(sp, legend=True, title="Series", cols=2):
    lg = alt.Legend(title=title, orient="bottom", columns=cols, symbolStrokeWidth=3, symbolType="stroke", symbolOpacity=1, labelLimit=420,
                    titleColor=INK, labelColor=INK2) if legend else None
    return dict(color=alt.Color("series:N", scale=sp["color"], legend=lg, sort=sp["names"]),
                strokeDash=alt.StrokeDash("series:N", scale=sp["dash"], legend=lg),
                opacity=alt.Opacity("series:N", scale=sp["opacity"], legend=None),
                strokeWidth=alt.StrokeWidth("series:N", scale=sp["width"], legend=None))


def compare_chart(results: dict, screen: str, after_tax=True, goal="spec", log=True, y_domain=None, mark_goal=False, bands=None,
                  title=None, width=W, height=H, legend=True, legend_cols=2):
    """Wealth of the legs of one or two schedules on one set of axes. `results`: {schedule: PlanResult}, schedule in
    ("static", "phased"); `screen`: the screen's short name. Per schedule: the screen (solid), the index fund (dashed, lighter),
    the net money put in (dotted); the plan's goal in amber. `bands`: phase_bands(result) to shade the phases behind the lines;
    `mark_goal`: a marker where each leg first holds the goal. Tooltip on every month: all legs, the money in, screen minus index fund."""
    scheds = list(results)
    legs = leg_names(screen, scheds)
    cs, ci = ("strategy_after_tax", "index_after_tax") if after_tax else ("strategy_pre", "index_pre")
    months = {k: _months(r.series) for k, r in results.items()}
    idx = months[scheds[0]].index
    wide = pd.DataFrame({"date": idx})
    keys, rows = {}, []
    for k in scheds:
        m = months[k].reindex(idx, method="nearest")
        wide[f"{k}_c"] = np.maximum(m["contributed"].astype(float).to_numpy(), 1.0)
        wide[f"{k}_s"] = m[cs].astype(float).to_numpy()
        wide[f"{k}_i"] = m[ci].astype(float).to_numpy()
        wide[f"{k}_d"] = wide[f"{k}_s"] - wide[f"{k}_i"]
        sc_, ic_ = leg_colors(k)
        keys[f"{k}_s"], keys[f"{k}_i"] = legs[k, "screen"], legs[k, "index"]
        rows += [(legs[k, "screen"], sc_, [1, 0], 1, 2.8), (legs[k, "index"], ic_, DASH_INDEX, 1, 2.8)]
    for k in scheds:
        cname = f"Net money in, {sched_label(k)}"
        keys[f"{k}_c"] = cname
        rows.append((cname, leg_colors(k)[0], DASH_DOT, 0.8, 1.6))
    g = results[scheds[0]].spec.goal if goal == "spec" else goal
    if g is not None:
        gname = f"Goal {_money(g)}"
        wide["goal"], keys["goal"] = float(g), gname
        rows.append((gname, AMBER, DASH_DASHDOT, 1, 1.8))
    wide["y"] = wide[f"{scheds[0]}_s"]
    sp = _spec(rows)
    cols = [c for c in wide.columns if c not in ("date", "y") and not c.endswith("_d")]
    dom = list(y_domain) if y_domain else _auto_domain(wide, cols)
    yscale = alt.Scale(type="log", domain=dom, nice=False) if log else alt.Scale(zero=True)
    tag = "after tax" if after_tax else "before tax"
    yt = f"Wealth {tag}, dollars{' (log scale)' if log else ''}"
    x = alt.X("date:T", title="Date", axis=alt.Axis(format="%Y", tickCount=12))
    y = alt.Y("dollars:Q", scale=yscale, title=yt, axis=_log_axis(dom, yt) if log else alt.Axis(format=MONEY_AXIS, title=yt))
    lines = _fold(wide, keys).mark_line().encode(x=x, y=y, **_enc_cols(sp, legend, "Series", legend_cols)).properties(width=width, height=height)
    layers = ([_phase_rects(bands)] if bands else []) + [lines]
    tips = [alt.Tooltip("date:T", title="Date", format="%d %b %Y")]
    for k in scheds:
        tips += [alt.Tooltip(f"{k}_c:Q", title=f"Net money in, {sched_label(k)}", format=MONEY_TIP),
                 alt.Tooltip(f"{k}_s:Q", title=f"{legs[k, 'screen']} ({tag})", format=MONEY_TIP),
                 alt.Tooltip(f"{k}_i:Q", title=f"{legs[k, 'index']} ({tag})", format=MONEY_TIP),
                 alt.Tooltip(f"{k}_d:Q", title=f"{screen} minus index fund, {sched_label(k)}", format="$,.0f")]
    sel, rule, dot = _hover(None, wide, alt.X("date:T"), "y", tips)
    layers += [rule, sel, dot]
    if mark_goal and g is not None:
        pts = []
        for k in scheds:
            for who, col in (("screen", cs), ("index", ci)):
                d0 = first_reached(results[k].series[col], g)
                if d0 is not None:
                    nm = legs[k, who]
                    pts.append(dict(date=d0, dollars=float(g), series=nm, marker="First at the goal", label=f"First at the goal: {nm}",
                                    when=d0.strftime("%d %b %Y"), years=(d0 - results[k].series.index[0]).days / 365.25))
        if pts:
            layers.append(alt.Chart(pd.DataFrame(pts)).mark_point(size=170, filled=True, stroke=INK, strokeWidth=1.5, shape="circle").encode(
                x="date:T", y=alt.Y("dollars:Q", scale=yscale), color=alt.Color("series:N", scale=sp["color"], legend=None),
                shape=alt.Shape("marker:N", scale=alt.Scale(domain=["First at the goal"], range=["circle"]),
                                legend=alt.Legend(title="Marker", orient="bottom", symbolFillColor=INK2)),
                tooltip=[alt.Tooltip("label:N", title="Marker"), alt.Tooltip("when:N", title="First at the goal"),
                         alt.Tooltip("years:Q", title="Years into the plan", format=".1f"),
                         alt.Tooltip("dollars:Q", title="Goal", format=MONEY_TIP)]))
    ch = alt.layer(*layers).resolve_scale(shape="independent")
    if title:
        ch = ch.properties(title=title)
    return ch.interactive(bind_y=False)


def compare_flows_chart(results: dict, screen: str, title="Dollars in and out each year, by schedule"):
    """Top: the dollars in and out each year, one panel per schedule, stacked by kind (the phased schedule's contributions by phase).
    Bottom: the running net money in for the legs; the index fund receives the same dollars on the same days, so each
    dashed line runs under the solid line of the same colour."""
    scheds = list(results)
    legs = leg_names(screen, scheds)
    base = {"initial": ("Lump sum", PALETTE[0]), "periodic": ("Monthly contributions, static", PALETTE[4]), "extra": ("Windfalls", AMBER),
            "withdrawal": ("Withdrawals", INK2)}
    names, colors, rows = [], [], []
    for k, r in results.items():
        bands = phase_bands(r)
        kinds = dict(base)
        if bands:
            kinds = {"initial": base["initial"], **{f"phase{i}": (b["label"], PHASE_COLORS[i % len(PHASE_COLORS)]) for i, b in enumerate(bands)},
                     "extra": base["extra"], "withdrawal": base["withdrawal"]}
        for v in kinds.values():
            if v[0] not in names:
                names.append(v[0])
                colors.append(v[1])
        fl = r.flows[r.flows["applied"]].copy()
        if bands:
            dts = pd.to_datetime(fl["date"])
            fl["kind"] = [next((f"phase{i}" for i, b in enumerate(bands) if b["start"] <= d < b["end"]), "phase0") if kk == "periodic" else kk
                          for kk, d in zip(fl["kind"], dts)]
        fl["year"] = pd.to_datetime(fl["executed"]).dt.year
        by = fl.pivot_table(index="year", columns="kind", values="amount", aggfunc="sum").reindex(columns=list(kinds)).fillna(0.0)
        cnt = fl.pivot_table(index="year", columns="kind", values="amount", aggfunc="count").reindex(columns=list(kinds)).fillna(0)
        for y_ in by.index:
            for kk in kinds:
                if by.loc[y_, kk] != 0:
                    rows.append(dict(schedule=sched_label(k).capitalize(), year=int(y_), kind=kinds[kk][0], amount=float(by.loc[y_, kk]),
                                     flows=int(cnt.loc[y_, kk]), order=names.index(kinds[kk][0])))
    d = pd.DataFrame(rows)
    bars = alt.Chart(d).mark_bar(width=9).encode(
        x=alt.X("year:O", title="Year", axis=alt.Axis(labelAngle=-90, labelOverlap=True)),
        y=alt.Y("amount:Q", title="Dollars in (up) and out (down) in the year", axis=alt.Axis(format=MONEY_AXIS)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=names, range=colors), sort=names, legend=alt.Legend(title="Kind of flow", orient="bottom", columns=3)),
        order=alt.Order("order:Q"),
        column=alt.Column("schedule:N", title=None, header=alt.Header(labelFontSize=13, labelFontWeight="bold"), sort=[sched_label(k).capitalize() for k in scheds]),
        tooltip=[alt.Tooltip("schedule:N", title="Schedule"), alt.Tooltip("year:O", title="Year"), alt.Tooltip("kind:N", title="Kind"),
                 alt.Tooltip("amount:Q", title="Dollars in the year", format=MONEY_TIP), alt.Tooltip("flows:Q", title="Number of flows")]
    ).properties(width=W // 2 + 20, height=220)
    nrows, lrows = [], []
    for k, r in results.items():
        net = r.series["contributed"].resample("YE").last()
        for who in ("index", "screen"):
            nrows.append(pd.DataFrame({"year": net.index.year, "series": legs[k, who], "dollars": net.to_numpy()}))
        sc_, ic_ = leg_colors(k)
        lrows += [(legs[k, "index"], ic_, DASH_INDEX, 1, 5.0), (legs[k, "screen"], sc_, [1, 0], 1, 2.2)]
    order = [legs[k, w] for k in scheds for w in ("screen", "index")]
    lrows.sort(key=lambda t: order.index(t[0]))
    nd = pd.concat(nrows)
    sp = _spec(lrows)
    enc = dict(x=alt.X("year:O", title="Year", axis=alt.Axis(labelAngle=-90)),
               y=alt.Y("dollars:Q", title="Running total of net money in, dollars", axis=alt.Axis(format=MONEY_AXIS)),
               tooltip=[alt.Tooltip("year:O", title="End of year"), alt.Tooltip("series:N", title="Leg"),
                        alt.Tooltip("dollars:Q", title="Net money in so far", format=MONEY_TIP)],
               **_enc_cols(sp, True, "Leg", 2))
    line = alt.layer(alt.Chart(nd).mark_line().encode(**enc), alt.Chart(nd).mark_point(filled=True, size=40).encode(**enc)).properties(width=W + 120, height=170)
    return style(alt.vconcat(bars, line).resolve_scale(color="independent", strokeDash="independent"), title)


def compare_bars(df: pd.DataFrame, screen: str, schedules=("static", "phased"), value_title="Value", fmt=MONEY_AXIS, tip_fmt=MONEY_TIP,
                 label_fmt="$,.2s", title=None, legend=True, width=300, height=300, group_title=None):
    """Grouped bars, one group per value of df["group"] (an account, say), one bar per leg. `df` columns: group, sched
    ("static" / "phased"), kind ("screen" / "index"), value. Screen bars are the schedule's colour, index fund bars its lighter
    tint with an outline in the schedule's colour. Tooltip on every bar."""
    legs = leg_names(screen, schedules)
    d = df.copy()
    d["leg"] = [legs[s_, k_] for s_, k_ in zip(d["sched"], d["kind"])]
    order = list(legs.values())
    rng = [leg_colors(s_)[0 if k_ == "screen" else 1] for (s_, k_) in legs]
    stroke = [leg_colors(s_)[0] for (s_, k_) in legs]
    tips = [alt.Tooltip("group:N", title=group_title or "Group"), alt.Tooltip("leg:N", title="Leg"), alt.Tooltip("value:Q", title=value_title, format=tip_fmt)]
    b = alt.Chart(d).encode(x=alt.X("group:N", sort=list(dict.fromkeys(d["group"])), title=group_title, axis=alt.Axis(labelAngle=0)),
                            xOffset=alt.XOffset("leg:N", sort=order), y=alt.Y("value:Q", title=value_title, axis=alt.Axis(format=fmt)))
    bars = b.mark_bar(size=18, strokeWidth=1.5).encode(
        color=alt.Color("leg:N", scale=alt.Scale(domain=order, range=rng), sort=order,
                        legend=alt.Legend(title="Leg", orient="bottom", columns=2, labelLimit=420) if legend else None),
        stroke=alt.Stroke("leg:N", scale=alt.Scale(domain=order, range=stroke), legend=None), tooltip=tips)
    txt = b.mark_text(dy=-6, fontSize=9, color=INK2).encode(text=alt.Text("value:Q", format=label_fmt))
    return alt.layer(bars, txt).properties(width=width, height=height, title=title or "")



# ================================================================================================= output
def save_png(chart, path, scale=2.0):
    """Write the chart as a PNG (vl-convert; no browser)."""
    import vl_convert as vlc
    with open(path, "wb") as fh:
        fh.write(vlc.vegalite_to_png(_vl5_json(chart), scale=scale, vl_version="5.20"))
    return path


def _vl5_json(chart) -> str:
    return json.dumps(style(chart).to_dict())


def compose(charts, title=None, columns=None, shared_legend=False):
    """Charts side by side (`columns` per row; all in one row by default) under one title; `shared_legend` merges the legends
    of panels that draw the same series (the three accounts of one plan)."""
    charts = list(charts)
    n = columns or len(charts)
    mode = "shared" if shared_legend else "independent"
    rows = [alt.hconcat(*charts[i:i + n]).resolve_scale(color=mode, strokeDash=mode) for i in range(0, len(charts), n)]
    ch = rows[0] if len(rows) == 1 else alt.vconcat(*rows).resolve_scale(color=mode, strokeDash=mode)
    return style(ch, title)


def _png(spec: dict, scale: float) -> bytes:
    import io
    import vl_convert as vlc
    raw = vlc.vegalite_to_png(json.dumps(spec), scale=scale, vl_version="5.20")
    try:                                                  # flat-colour charts: a 96-colour palette is invisible and 3x smaller
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)   # X1: 256 colours keep the thin legend swatches of tinted series true
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except ImportError:
        return raw


def mimebundle(chart, scale=1.25):
    """One notebook output: the interactive Vega-Lite 5 spec and a PNG of it (viewers without Vega show the picture)."""
    spec = style(chart).to_dict()
    return {"application/vnd.vegalite.v5+json": spec, "image/png": base64.b64encode(_png(spec, scale)).decode("ascii"),
            "text/plain": "<altair chart: " + ", ".join(series_names(spec)[:6]) + ">"}


def series_names(spec) -> list:
    """Every colour-scale domain entry in a spec (the legend entries), in order, without repeats."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            for ch in ("color", "fill"):                          # V3: fill carries the phase bands' legend
                c = o.get("encoding", {}).get(ch) if isinstance(o.get("encoding"), dict) else None
                if isinstance(c, dict) and isinstance(c.get("scale"), dict):
                    for n in c["scale"].get("domain", []):
                        if n not in out:
                            out.append(n)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(spec)
    return out


def show(chart, scale=1.25):
    """Display `chart` in a notebook: interactive in JupyterLab / VS Code, the PNG elsewhere (GitHub, nbviewer). Outside a
    notebook kernel (a plain script run) it validates the spec and prints one line naming the legend entries."""
    try:
        get_ipython()                                     # noqa: F821  (defined only inside IPython)
    except NameError:
        spec = style(chart).to_dict()
        print("[chart]", "; ".join(series_names(spec)))
        return
    from IPython.display import display
    display(mimebundle(chart, scale), raw=True)
