"""Browser chart panel, server side (DESIGN.md L6). No Textual import: pure functions over a `Session`.

The control endpoint (control.py) serves `static/charts.html` (Plotly.js, tabs mirroring the TUI) and the JSON
below. Nothing is computed here that the Session does not already hold: curves, the Compare rows, the timing
table, the Monte Carlo table and fan, the exit evaluation. The one exception is binning: the Monte Carlo
per-path outcomes are binned into histograms (same bins for every portfolio and the passive index) when the job
finishes (`mc_hist`, called from `Session._finish`), so no path-level array is kept in the session or sent.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"
STATIC = Path(__file__).with_name("static")
CHART_TABS = ("curves", "compare", "timing", "montecarlo", "exit")
MAX_POINTS = 2000
HIST_BINS_MAX = 30
FALLBACK_THEMES = {
    True: {"background": "#121212", "surface": "#1E1E1E", "panel": "#242F38", "foreground": "#E0E0E0",
           "primary": "#0178D4", "secondary": "#004578", "accent": "#FEA62B", "success": "#4EBF71",
           "warning": "#FEA62B", "error": "#B93C5B"},
    False: {"background": "#E0E0E0", "surface": "#D8D8D8", "panel": "#D0D0D0", "foreground": "#1F1F1F",
            "primary": "#004578", "secondary": "#0178D4", "accent": "#FEA62B", "success": "#4EBF71",
            "warning": "#FEA62B", "error": "#B93C5B"},
}
THEME_KEYS = ("background", "surface", "panel", "foreground", "primary", "secondary", "accent", "success",
              "warning", "error")


# ------------------------------------------------------------------ pages
def plotly_setting() -> str:
    """`SCREENLAB_PLOTLY_JS` (a URL or a local file path), else the pinned CDN build."""
    return (os.environ.get("SCREENLAB_PLOTLY_JS") or "").strip() or PLOTLY_CDN


def plotly_local_file() -> Path | None:
    v = plotly_setting()
    if v.startswith(("http://", "https://", "//")):
        return None
    p = Path(v).expanduser()
    return p if p.is_file() else None


def plotly_src() -> str:
    """The `src` the pages use: the URL as given, or `/plotly.js` when a local file is served."""
    v = plotly_setting()
    if v.startswith(("http://", "https://", "//")):
        return v
    return "/plotly.js"


def page(name: str, **subst) -> bytes:
    """`static/<name>` with `__KEY__` placeholders replaced (`PLOTLY_SRC` is always available)."""
    html = (STATIC / name).read_text(encoding="utf-8")
    subst = {"PLOTLY_SRC": plotly_src(), **subst}
    for k, v in subst.items():
        html = html.replace(f"__{k}__", str(v))
    return html.encode("utf-8")


# ------------------------------------------------------------------ theme
def _is_hex(v) -> bool:
    return isinstance(v, str) and v.startswith("#") and len(v) in (4, 7, 9) and all(
        c in "0123456789abcdefABCDEF" for c in v[1:])


def _mix(fg: str, bg: str, a: float) -> str:
    def rgb(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    f, b = rgb(fg), rgb(bg)
    return "#" + "".join(f"{round(x * a + y * (1 - a)):02X}" for x, y in zip(f, b))


def theme_payload(name: str, dark: bool, css: dict | None) -> dict:
    """`/theme`: the Textual theme's colour variables (hex), with a muted text colour and a grid colour derived
    from them, so the page builds its Plotly template from the same palette as the terminal."""
    base = dict(FALLBACK_THEMES[bool(dark)])
    for k in THEME_KEYS:
        v = (css or {}).get(k)
        if _is_hex(v):
            base[k] = v[:7].upper()
    base["text_muted"] = _mix(base["foreground"], base["background"], 0.6)
    base["grid"] = _mix(base["foreground"], base["background"], 0.12)
    return {"name": name, "dark": bool(dark), "variables": base}


# ------------------------------------------------------------------ binning (Monte Carlo)
def _edges(values, nbins: int):
    lo, hi = min(values), max(values)
    if not hi > lo:
        lo, hi = lo - 0.005, hi + 0.005
    step = (hi - lo) / nbins
    return [lo + i * step for i in range(nbins + 1)]


def _counts(values, edges) -> list:
    n = len(edges) - 1
    lo, step = edges[0], (edges[-1] - edges[0]) / n
    out = [0] * n
    for v in values:
        out[min(n - 1, max(0, int((v - lo) / step)))] += 1
    return out


def mc_hist(per_path, bins: int | None = None) -> dict | None:
    """Histograms of per-path CAGR and max drawdown from a MonteCarloResult's `per_path` table (a DataFrame with
    columns portfolio, horizon_years, cagr, max_drawdown). Per horizon, the bin edges are shared by every
    portfolio and the passive index, so the bars are comparable. Returns
    `{"horizons": [h, ...], "by_horizon": {"<h>": {"cagr"|"maxdd": {"edges": [...], "n": {label: paths},
    "counts": {label: [per bin]}}}}}` or None if the table lacks what is needed."""
    try:
        cols = {"portfolio", "horizon_years", "cagr", "max_drawdown"}
        if per_path is None or not cols <= set(per_path.columns):
            return None
        out = {}
        for h, g in per_path.groupby("horizon_years", sort=True):
            entry = {}
            for key, col in (("cagr", "cagr"), ("maxdd", "max_drawdown")):
                vals = {str(p): [float(x) for x in gg[col] if x is not None and math.isfinite(float(x))]
                        for p, gg in g.groupby("portfolio", sort=False)}
                allv = [x for v in vals.values() for x in v]
                if not allv:
                    continue
                nb = bins or max(8, min(HIST_BINS_MAX, int(math.sqrt(len(allv)) * 1.5)))
                edges = _edges(allv, nb)
                entry[key] = {"edges": edges, "n": {p: len(v) for p, v in vals.items()},
                              "counts": {p: _counts(v, edges) for p, v in vals.items()}}
            out[f"{float(h):g}"] = entry
        return {"horizons": [float(h) for h in sorted(set(per_path["horizon_years"]))], "by_horizon": out}
    except Exception:                                                  # noqa: BLE001  (charts are optional)
        return None


# ------------------------------------------------------------------ data per tab
def downsample(pts: list, n: int = MAX_POINTS) -> list:
    """At most `n` points by an even stride, always keeping the first and the last."""
    if len(pts) <= n:
        return pts
    step = (len(pts) - 1) / (n - 1)
    idx = sorted({round(i * step) for i in range(n)} | {len(pts) - 1})
    return [pts[i] for i in idx]


def _xy(pts: list) -> dict:
    pts = downsample(pts)
    return {"x": [p[0] for p in pts], "y": [p[1] for p in pts]}


def _base(session, tab: str, empty: bool, message: str | None = None) -> dict:
    return {"tab": tab, "seq": session.seq, "empty": empty, "message": message,
            "passive": {"label": session.passive_label() if hasattr(session, "passive_label") else "index",
                        "color": _passive_color()}}


def _passive_color() -> str:
    from .session import PASSIVE_COLOR
    return PASSIVE_COLOR


def _color_of(session, label: str) -> str:
    for s in session.scenarios:
        if s.label == label:
            return s.color
    return _passive_color()


def data_curves(session) -> dict:
    cd = session.curves_data()
    out = _base(session, "curves", not cd, "No curves yet: add a scenario and run it (a, then r in the TUI).")
    first = next(iter(cd), None)
    series = []
    for label, d in cd.items():
        sc = next((s for s in session.scenarios if s.label == label), None)
        series.append({"id": sc.id if sc else "passive", "label": label, "color": d["color"],
                       "passive": sc is None, **_xy(d["points"])})
    out["series"] = series
    out["selected"] = session.selected
    out["benchmark"] = first
    return out


def data_compare(session) -> dict:
    c = session.cmd_compare()
    out = _base(session, "compare", len(c["rows"]) < 2, "No finished runs yet: run a scenario to compare it "
                                                        "with the passive index.")
    out.update(benchmark=c["benchmark"], columns=c["columns"], rows=c["rows"], missing=c["missing"],
               diffs=c["diffs"], trials_total=c["trials_total"],
               tax={sc.label: sc.params["tax"] for sc in session.scenarios if sc.params.get("tax")})
    return out


def data_timing(session) -> dict:
    t = session.analyses["timing"]
    out = _base(session, "timing", not t, "No timing study yet: press t in the TUI (or run the `timing` command).")
    series = []
    for label, tm in t.items():
        tb = tm.get("table") or {"columns": [], "rows": []}
        cols = tb["columns"]
        ix = {c: cols.index(c) for c in ("offset", "CAGR", "Excess/yr") if c in cols}
        rows = tb["rows"]
        series.append({
            "label": label, "color": _color_of(session, label),
            "offset": [r[ix["offset"]] for r in rows] if "offset" in ix else list(range(len(rows))),
            "cagr": [r[ix["CAGR"]] for r in rows] if "CAGR" in ix else [],
            "excess": [r[ix["Excess/yr"]] for r in rows] if "Excess/yr" in ix else [],
            "tranche": _xy(tm["tranche"]) if tm.get("tranche") else None,
            "distribution": tm.get("distribution")})
    out["series"] = series
    c = session.cmd_compare()
    out["passive_cagr"] = c["rows"][0]["values"].get("CAGR") if c["rows"] and c["rows"][0]["values"] else None
    return out


def _mc_names(session, m: dict) -> dict:
    """portfolio name in the MC table -> scenario label (the MC job names portfolios by screen)."""
    bench = m.get("benchmark")
    ports = []
    for r in m["table"]["rows"]:
        if r[0] != bench and r[0] not in ports:
            ports.append(r[0])
    labels = list(m.get("scenarios") or [])
    return dict(zip(ports, labels)) if len(ports) == len(labels) else {p: p for p in ports}


def data_montecarlo(session) -> dict:
    m = session.analyses["montecarlo"]
    out = _base(session, "montecarlo", not m, "No Monte Carlo yet: press m in the TUI (or run the `montecarlo` "
                                              "command).")
    if not m:
        return out
    bench = m.get("benchmark")
    names = _mc_names(session, m)

    def disp(p):
        return p if p == bench else names.get(p, p)

    def color(p):
        return _passive_color() if p == bench else _color_of(session, disp(p))

    fan = m.get("fan") or {"columns": [], "rows": []}
    cols, rows = fan["columns"], downsample(fan["rows"])
    series, seen = [], []
    for c in cols[1:]:
        p = c.rsplit("|", 1)[0]
        if p not in seen:
            seen.append(p)
    for p in seen:
        d = {"label": disp(p), "color": color(p), "passive": p == bench}
        for q in ("p5", "p25", "p50", "p75", "p95"):
            j = cols.index(f"{p}|{q}") if f"{p}|{q}" in cols else None
            d[q] = [r[j] for r in rows] if j is not None else None
        series.append(d)
    hist = m.get("hist")
    if hist:
        hist = {**hist, "by_horizon": {
            h: {k: {**v, "n": {disp(p): n for p, n in v["n"].items()},
                    "counts": {disp(p): c for p, c in v["counts"].items()}} for k, v in e.items()}
            for h, e in hist["by_horizon"].items()}}
    colors = {disp(p): color(p) for p in seen}
    tb = m["table"]
    out.update(benchmark=bench, scenarios=m.get("scenarios"), n_paths=m.get("n_paths"),
               horizon_years=m.get("horizon_years"), generator=m.get("generator"),
               selection_mode={disp(k): v for k, v in (m.get("selection_mode") or {}).items()},
               fan={"years": [r[0] for r in rows], "series": series}, hist=hist, colors=colors,
               hist_note=None if hist else "Histograms need a fresh run: a result reloaded from the run store "
                                           "keeps only the table and the fan (run again with force).",
               table={"columns": tb["columns"], "rows": [[disp(r[0]), *r[1:]] for r in tb["rows"]]},
               warnings=m.get("warnings") or [])
    return out


def data_exit(session) -> dict:
    e = session.analyses["exit"]
    out = _base(session, "exit", not e, "No exit evaluation yet: give a scenario a trend exit (x, then F2), then "
                                        "run `exit_eval`.")
    items = []
    for label, ev in e.items():
        fw = ev.get("forward") or {"columns": [], "rows": []}
        ov = ev.get("overlay_stats") or {"columns": [], "rows": []}
        items.append({"label": label, "color": _color_of(session, label), "frac_flagged": ev.get("frac_flagged"),
                      "switches": ev.get("switches"),
                      "forward": [dict(zip(fw["columns"], r)) for r in fw["rows"]],
                      "overlay": {"columns": ov["columns"], "rows": ov["rows"]}})
    out["items"] = items
    return out


BUILDERS = {"curves": data_curves, "compare": data_compare, "timing": data_timing, "montecarlo": data_montecarlo,
            "exit": data_exit}


def chart_data(session, tab: str) -> dict:
    from .session import SessionError
    fn = BUILDERS.get(tab)
    if fn is None:
        raise SessionError(400, f"unknown chart tab {tab!r}; one of {', '.join(CHART_TABS)}")
    with session._lock:
        return fn(session)
