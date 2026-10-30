"""Plotting functions for the tax notebooks (where to hold it, foreign holdings, loss harvesting). Pure matplotlib (Agg-safe).

Every function takes tables the Lab already returns, draws on `ax` (a new figure when None) and returns the Axes.
Nothing here reads or writes a file. Palette: Okabe-Ito (colour-blind safe), the passive index always grey and dashed,
the amber of "lost" tax always the same amber. Percent axes take fractions (0.05 = 5%).

    after_tax_curves(results)        pre-tax (thin, light) vs after-tax (thick) wealth per label; log scale; index dashed
    tax_drag_bars(table)             CAGR vs AfterTaxCAGR per row, the drag written over each pair
    tax_paid_stack(result)           tax paid each year, stacked by kind
    marginal_rate_curves(...)        marginal rate vs income, AB / BC, four kinds of income
    withholding_matrix(foreign)      account x kind of holding -> lost / credited / exempt / none
    harvest_timeline(result)         wealth line, loss sales, second-pick spans, blocked windows, denied losses
    sensitivity_lines(df, x, ys)     generic lines of columns of `df` against column `x`
    index_result(report)             the passive index of a BacktestReport as a Result, for `after_tax_curves`

R1: every chart takes `legend=True` (False removes it). `after_tax_curves` and `harvest_timeline` take `baseline=` (an
`index_result(...)`: buy and hold the passive index, taxed the same way, grey dashed, named "Buy and hold the index (after tax)").
`tax_paid_stack` draws the `refund` column as negative bars.
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
import pandas as pd

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e3e2dd"
INDEX_COL = "#6B6B6B"
AMBER = "#E69F00"
# fixed order, never cycled (Okabe-Ito)
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
REFUND_COL = "#CC79A7"              # R1: refunds are negative bars in tax_paid_stack
STACK = {"tax_dividends": ("Dividends", "#0072B2"), "tax_interest": ("Interest", "#56B4E9"),
         "tax_capital_gains": ("Capital gains", "#009E73"), "tax_foreign_dividends": ("Foreign dividends", AMBER),
         "tax_rrsp_withdrawal": ("RRSP withdrawal", "#D55E00")}
KIND_COL = {"ordinary": "#0072B2", "eligible_dividend": "#009E73", "non_eligible_dividend": "#CC79A7",
            "capital_gain": "#D55E00"}
KIND_NAME = {"ordinary": "Ordinary income", "eligible_dividend": "Eligible dividends",
             "non_eligible_dividend": "Non-eligible dividends", "capital_gain": "Capital gains"}
ACCOUNT_NAME = {"taxable": "Taxable", "tfsa": "TFSA", "rrsp": "RRSP"}


def _ax(ax, size=(8, 4.4)):
    if ax is None:
        import matplotlib.pyplot as plt
        _, ax = plt.subplots(figsize=size)
    ax.figure.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2)
    ax.xaxis.label.set_color(INK2)
    ax.yaxis.label.set_color(INK2)
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    return ax


def _title(ax, text, title=None):
    ax.set_title(title or text, loc="left", fontsize=12, fontweight="bold", color=INK)


def _pct(x, _=None):
    return f"{100 * x:g}%"


# R1: the legend switch shared by every chart (`legend=False` removes the legend), and the label of the common base case
BASELINE_LABEL = "Buy and hold the index (after tax)"


def _legend(ax, legend=True, **kw):
    if not legend:
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        return None
    if "handles" not in kw and not ax.get_legend_handles_labels()[0]:
        return None
    return ax.legend(**kw)


def _is_index(label, value) -> bool:
    return isinstance(value, _IndexResult) or str(label).lower().startswith(("index", "passive", "syn_", "sp500", "s&p"))


class _IndexResult:
    """Marker mixin: a Result built by `index_result` is drawn as the passive index."""


def index_result(report, label=None):
    """The passive index of a `BacktestReport` (or the `reports[account]` of an `AccountReport`) as a Result-like object
    (`curve`, `after_tax_curve`, `tax`) that `after_tax_curves` and `tax_paid_stack` accept."""
    from ..backtest import Result
    label = label or report.benchmark_label
    curve = report.benchmark.astype(float)
    curve = curve / curve.iloc[0]
    tab = report.tax[label]
    ratio = pd.Series(tab["wealth_after_tax"].to_numpy() / tab["wealth_pre_tax"].to_numpy(),
                      index=pd.to_datetime([f"{y}-12-31" for y in tab.index]))
    ratio = ratio.reindex(ratio.index.union(curve.index)).sort_index().ffill().fillna(1.0).reindex(curve.index)   # a step at each year end
    cls = type("IndexResult", (_IndexResult, Result), {})
    return cls(name=label, curve=curve, tax=tab, after_tax_curve=(curve * ratio).rename("after_tax"))


# ---------------------------------------------------------------------------------------------- 1
def after_tax_curves(results: dict, ax=None, title=None, baseline=None, legend=True, baseline_label=BASELINE_LABEL):
    """Pre-tax (thin, light) and after-tax (thick) wealth per label on a log scale; growth of 1 at the start.
    Values are `Result`s; the passive index (`index_result(...)`, or a label starting with "index" / "passive") is grey, dashed."""
    from matplotlib.lines import Line2D
    ax = _ax(ax)
    n = 0
    if baseline is not None:                                         # R1: buy and hold the index (an `index_result`), drawn grey and dashed
        results = {**results, baseline_label: baseline}
    for label, r in results.items():
        idx = _is_index(label, r)
        col = INDEX_COL if idx else PALETTE[n % len(PALETTE)]
        n += 0 if idx else 1
        ls = "--" if idx else "-"
        ax.plot(r.curve.index, r.curve.to_numpy(), color=col, lw=1.0, alpha=0.45, ls=ls)
        if r.after_tax_curve is not None:
            a = r.after_tax_curve
            ax.plot(a.index, a.to_numpy(), color=col, lw=2.4, ls=ls, label=str(label))
            ax.annotate(f"{a.iloc[-1]:.1f}x", (a.index[-1], a.iloc[-1]), xytext=(4, 0), textcoords="offset points",
                        fontsize=9, color=INK2, va="center")
    ax.set_yscale("log")
    ax.set_ylabel("growth of 1 (log scale)")
    from matplotlib.ticker import FixedLocator, NullLocator
    lo, hi = ax.get_ylim()
    ax.yaxis.set_major_locator(FixedLocator([v for v in (0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64) if lo <= v <= hi]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:g}x")
    ax.margins(x=0.04)
    h, _l = ax.get_legend_handles_labels()
    h += [Line2D([], [], color=INK2, lw=2.4, label="after tax (thick line)"), Line2D([], [], color=INK2, lw=1.0, alpha=0.45, label="before tax (thin line)")]
    _legend(ax, legend, handles=h, loc="upper left", fontsize=9)
    _title(ax, "Growth of 1 before and after tax", title)
    return ax


# ---------------------------------------------------------------------------------------------- 2
def tax_drag_bars(table: pd.DataFrame, ax=None, title=None, baseline=None, baseline_label="Buy and hold the index (after tax)", legend=True):
    """Grouped bars, one group per row: `CAGR` (light) beside `AfterTaxCAGR` (dark), the drag in points written above.
    Group labels come from a `label` column if there is one, else from the index. Columns hold fractions.
    # R2: `baseline` (one after-tax CAGR per row, a fraction) draws a grey dashed bar-wide mark in each group, legend `baseline_label`."""
    ax = _ax(ax)
    labels = [str(x) for x in (table["label"] if "label" in table else table.index)]
    pre, post = table["CAGR"].to_numpy(float), table["AfterTaxCAGR"].to_numpy(float)
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2, pre, w * 0.94, color="#9CC3DE", label="before tax")
    ax.bar(x + w / 2, post, w * 0.94, color="#0072B2", label="after tax")
    base = None if baseline is None else np.asarray(baseline, float)
    top = np.nanmax(np.r_[pre, post, 0.0] if base is None else np.r_[pre, post, base, 0.0])
    if base is not None:                                                                                    # R2
        for i in range(len(x)):
            ax.plot([x[i] - w, x[i] + w], [base[i], base[i]], color=INDEX_COL, ls="--", lw=2.4, zorder=4,
                    label=baseline_label if i == 0 else None)
    for i in range(len(x)):
        ax.text(x[i] - w / 2, pre[i], f"{100 * pre[i]:.1f}%", ha="center", va="bottom", fontsize=8.5, color=INK2)
        ax.text(x[i] + w / 2, post[i], f"{100 * post[i]:.1f}%", ha="center", va="bottom", fontsize=8.5, color=INK2)
        ax.text(x[i], max(pre[i], post[i], base[i] if base is not None else -1) + top * 0.1, f"drag {100 * (pre[i] - post[i]):.1f} pts", ha="center",
                va="bottom", fontsize=9, color=INK, fontweight="bold")
    ax.set_xticks(x, labels, rotation=0 if len(labels) <= 6 else 30, ha="center" if len(labels) <= 6 else "right")
    ax.yaxis.set_major_formatter(_pct)
    ax.set_ylim(min(0, np.nanmin(np.r_[pre, post] if base is None else np.r_[pre, post, base]) * 1.2), top * 1.45)
    ax.set_ylabel("growth per year (CAGR)")
    _legend(ax, legend, loc="upper left", fontsize=9, ncol=2 if base is None else 1)      # R1
    _title(ax, "Growth per year before and after tax", title)
    return ax


# ---------------------------------------------------------------------------------------------- 3
def tax_paid_stack(result, ax=None, title=None, baseline=None, baseline_label="Buy and hold the index (tax paid)", legend=True):
    """Tax paid each year as stacked bars by kind (dividends, interest, capital gains, foreign dividends, RRSP withdrawal),
    in dollars, from `Result.tax`. A negative stack is a refund (a loss carried back).
    # R2: `baseline` (a Result, e.g. `index_result(report)`) draws its total tax per year as a grey dashed line with markers."""
    ax = _ax(ax)
    t = result.tax
    if t is None or not len(t):
        ax.text(0.5, 0.5, "no tax table", transform=ax.transAxes, ha="center")
        return ax
    years = [str(y) for y in t.index]
    x = np.arange(len(years))
    pos = np.zeros(len(x))
    neg = np.zeros(len(x))
    for col, (name, color) in STACK.items():
        if col not in t or not np.any(np.abs(t[col].to_numpy(float)) > 1e-9):
            continue
        v = t[col].to_numpy(float)
        base = np.where(v >= 0, pos, neg)
        ax.bar(x, v, 0.78, bottom=base, color=color, label=name, edgecolor=SURFACE, linewidth=1.2)
        pos += np.where(v >= 0, v, 0)
        neg += np.where(v < 0, v, 0)
    if "refund" in t and np.any(np.abs(t["refund"].to_numpy(float)) > 1e-9):      # R1: a carried-back loss refunds tax: negative bars
        v = -np.abs(t["refund"].to_numpy(float))
        ax.bar(x, v, 0.78, bottom=neg, color=REFUND_COL, label="Refund (loss carried back)", edgecolor=SURFACE, linewidth=1.2)
        neg += v
    ax.axhline(0, color=INK2, lw=0.8)
    bt = None if baseline is None else baseline.tax                                                         # R2
    if bt is not None and len(bt):
        tot = sum(bt[c].to_numpy(float) for c in STACK if c in bt)
        xb = [years.index(str(y)) for y in bt.index if str(y) in years]
        ax.plot(xb, [tot[k] for k, y in enumerate(bt.index) if str(y) in years], color=INDEX_COL, ls="--", lw=2.2, marker="D", ms=4.5,
                zorder=5, label=baseline_label)
    if not pos.any() and not neg.any():
        ax.text(0.5, 0.5, "no tax paid", transform=ax.transAxes, ha="center", va="center", color=INK2)
    ax.set_xticks(x, years, rotation=0 if len(x) <= 8 else 60, fontsize=9)
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.set_ylabel("tax paid in the year ($)")
    if pos.any() or neg.any() or bt is not None:
        _legend(ax, legend, loc="upper left", fontsize=9, ncol=1 if bt is not None else 2)      # R1
    _title(ax, "Tax paid each year, by kind", title)
    return ax


# ---------------------------------------------------------------------------------------------- 4
@lru_cache(maxsize=None)
def _marginals(province: str, year: int, incomes: tuple) -> pd.DataFrame:
    from ..tax_ca.api import marginal_rates
    return pd.DataFrame([marginal_rates(province, year, float(i)) for i in incomes], index=list(incomes))


def marginal_rate_curves(provinces=("AB", "BC"), year=2025, incomes=range(20_000, 300_001, 5_000), ax=None, title=None,
                         baseline=None, baseline_label="Buy and hold the index (its income)", legend=True):
    """Marginal rate on the next dollar of ordinary income, eligible dividends, non-eligible dividends and capital gains,
    against other income. Colour = kind of income, line style = province. The eligible-dividend rate can be negative
    at low income (the credit exceeds the tax), so the zero line is drawn.
    # R2: `baseline` = the key of the kind of income a buy-and-hold index mostly produces (e.g. "eligible_dividend"); its curves are
    drawn again as a wide grey dashed line under the coloured ones, legend `baseline_label`."""
    ax = _ax(ax, (9, 4.8))
    styles = ["-", "--", ":", "-."]
    inc = tuple(int(i) for i in incomes)
    for k, prov in enumerate(provinces):
        m = _marginals(prov, year, inc)
        for kind in KIND_COL:
            ax.plot(m.index, m[kind].to_numpy(), color=KIND_COL[kind], ls=styles[k % 4], lw=2.0,
                    label=f"{KIND_NAME[kind]}, {prov}")
    if baseline in KIND_COL:                                                                                # R2
        for k, prov in enumerate(provinces):
            m = _marginals(prov, year, inc)
            ax.plot(m.index, m[baseline].to_numpy(), color=INDEX_COL, ls="--", lw=4.2, alpha=0.55, zorder=1,
                    label=f"{baseline_label}, {prov}")
    allv = pd.concat([_marginals(p_, year, inc) for p_ in provinces])
    ok = allv[allv.index >= 30_000].to_numpy(float)
    ax.set_ylim(min(-0.02, ok.min()) - 0.04, ok.max() + 0.05)       # the lowest incomes (little tax owed) give meaningless extremes
    ax.axhline(0, color=INK, lw=1.0)
    ax.text(inc[-1], 0.004, "zero", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.xaxis.set_major_formatter(lambda v, _: f"${v / 1e3:g}k")
    ax.yaxis.set_major_formatter(_pct)
    ax.set_xlabel("other income (a year)")
    ax.set_ylabel("tax on the next $1")
    _legend(ax, legend, fontsize=8, ncol=len(provinces), loc="upper left", bbox_to_anchor=(0, -0.16))      # R1
    _title(ax, f"Marginal tax rates by kind of income, {year}", title)
    return ax


# ---------------------------------------------------------------------------------------------- 5
TREATMENT_COL = {"lost": ("#F2C14E", "lost"), "credited": ("#9CC3DE", "credited"), "exempt": ("#A8D5BA", "exempt"),
                 "sheltered": ("#ECEBE6", "no foreign tax"), "taxed": ("#ECEBE6", "taxed")}


def _treatment_kind(text: str) -> str:
    t = text.upper()
    if "GROSS-UP" in t:
        return "taxed"
    if "LOST" in t:
        return "lost"
    if "EXEMPT" in t:
        return "exempt"
    if "CREDIT" in t:
        return "credited"
    if "SHELTERED" in t:
        return "sheltered"
    return "taxed"


def withholding_matrix(foreign_table: pd.DataFrame, ax=None, title=None):
    """Table of kind of holding (rows) by account (columns): what happens to the foreign withholding. Amber = lost,
    blue = credited back, green = exempt, grey = no foreign tax at stake. The word is written in every cell.
    `foreign_table` is `AccountReport.foreign` (class, taxable, tfsa, rrsp)."""
    from matplotlib.patches import Rectangle
    ft = foreign_table.drop_duplicates(subset=["class"]) if "class" in foreign_table else foreign_table
    accts = [a for a in ("taxable", "tfsa", "rrsp") if a in ft]
    n = len(ft)
    ax = _ax(ax, (8.5, 0.6 + 0.65 * max(n, 1)))
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    for i, (_, r) in enumerate(ft.iterrows()):
        for j, a in enumerate(accts):
            kind = _treatment_kind(str(r[a]))
            col, word = TREATMENT_COL[kind]
            ax.add_patch(Rectangle((j, n - 1 - i), 1, 1, facecolor=col, edgecolor=SURFACE, lw=3))
            txt = str(r[a])
            pc = re.search(r"(\d+(?:\.\d+)?%)", txt)
            word = {"taxed": "dividend tax credit", "sheltered": "sheltered"}.get(kind, f"{pc.group(1)} {word}" if pc else word)
            ax.text(j + 0.5, n - 1 - i + 0.5, word,
                    ha="center", va="center", fontsize=9.5, color=INK, fontweight="bold" if kind == "lost" else None)
    ax.set_xlim(0, len(accts))
    ax.set_ylim(0, n)
    ax.set_xticks([j + 0.5 for j in range(len(accts))], [ACCOUNT_NAME[a] for a in accts], fontsize=10.5)
    ax.xaxis.tick_top()
    ax.set_yticks([n - 1 - i + 0.5 for i in range(n)], [str(c) for c in ft["class"]] if "class" in ft else ft.index, fontsize=9.5)
    ax.tick_params(length=0)
    ax.set_title(title or "What happens to foreign withholding tax, by account", loc="left", pad=28, fontsize=12,
                 fontweight="bold", color=INK)
    return ax


# ---------------------------------------------------------------------------------------------- 6
def harvest_timeline(result, ax=None, title=None, baseline=None, legend=True, after_tax=False):
    """The wealth line with the loss sales of `Result.harvest` on it: a marker per sale (by `source`), a green span for
    each second pick held while the name is blocked (`date` to `window_end`), amber shading for the blocked window
    (`date` to `blocked_until`), and a red cross where part of the loss was denied (`denied` > 0)."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    ax = _ax(ax, (9, 4.4))
    c = result.curve
    basis = "before tax"
    if after_tax and result.after_tax_curve is not None:             # R1: draw the after-tax curve (and the baseline's) instead of the pre-tax one
        c, basis = result.after_tax_curve, "after tax"
    ax.plot(c.index, c.to_numpy(), color="#0072B2", lw=1.8)
    h = result.harvest
    handles = [Line2D([], [], color="#0072B2", lw=1.8, label=f"portfolio value {basis}")]
    if baseline is not None:                                         # R1: buy and hold the index on the same basis
        b = baseline.after_tax_curve if basis == "after tax" and baseline.after_tax_curve is not None else baseline.curve
        ax.plot(b.index, b.to_numpy(), color=INDEX_COL, ls="--", lw=1.8, zorder=3)
        handles.append(Line2D([], [], color=INDEX_COL, ls="--", lw=1.8, label=f"Buy and hold the index ({basis})"))
    if h is not None and len(h):
        def y_at(d):
            return float(c.iloc[min(c.index.searchsorted(pd.Timestamp(d), side="right") - 1, len(c) - 1) if c.index.searchsorted(pd.Timestamp(d), side="right") > 0 else 0])
        for _, r in h.iterrows():
            d = pd.Timestamp(r["date"])
            bu = r.get("blocked_until")
            if pd.notna(bu):
                ax.axvspan(d, pd.Timestamp(bu), color=AMBER, alpha=0.18, lw=0)
            rn = r.get("replacement_name")
            if isinstance(rn, str) and rn and pd.notna(r.get("window_end")):
                ax.axvspan(d, pd.Timestamp(r["window_end"]), ymin=0, ymax=0.05, color="#009E73", alpha=0.9, lw=0)
        mk = {"harvest": ("v", "#D55E00", "harvest sale"), "screen": ("s", "#52514e", "sale at rebalance"),
              "exit": ("^", "#CC79A7", "sale on exit signal")}
        for src, (m, col, name) in mk.items():
            g = h[h["source"] == src]
            if len(g):
                ax.scatter(g["date"], [y_at(d) for d in g["date"]], marker=m, s=46, color=col, zorder=4, edgecolor=SURFACE, lw=0.8)
                handles.append(Line2D([], [], marker=m, color=col, lw=0, label=name))
        den = h[h["denied"].fillna(0) > 0] if "denied" in h else h.iloc[:0]
        if len(den):
            ax.scatter(den["date"], [y_at(d) for d in den["date"]], marker="x", s=70, color="#C0392B", zorder=5, lw=2)
            handles.append(Line2D([], [], marker="x", color="#C0392B", lw=0, label="loss partly denied"))
        handles.append(Patch(color=AMBER, alpha=0.18, label="blocked window (cannot buy back)"))
        if h.get("replacement_name") is not None and h["replacement_name"].notna().any():
            handles.append(Patch(color="#009E73", label="second pick held instead"))
    ax.set_ylabel("growth of $1")
    _legend(ax, legend, handles=handles, loc="upper left", fontsize=8.5)
    _title(ax, "Loss sales, blocked windows and second picks", title)
    return ax


# ---------------------------------------------------------------------------------------------- 7
def sensitivity_lines(df: pd.DataFrame, x: str, ys, ax=None, title=None, xlabel=None, ylabel=None, baseline=(), legend=True):
    """Lines of the columns `ys` (a list, or a dict column -> legend label) of `df` against column `x`.
    Percent-looking y values (fractions below 1.5 in size) are shown as percent. Use `ax=` of a subplots grid for small multiples.
    # R2: `baseline` lists the columns that are the buy-and-hold base case; they are drawn grey and dashed whatever their label says."""
    ax = _ax(ax)
    names = dict(ys) if isinstance(ys, dict) else {y: y for y in ys}
    d = df.sort_values(x)
    vals = d[list(names)].to_numpy(float)
    pct = np.nanmax(np.abs(vals)) < 1.5
    k = m = 0                                   # index lines are grey and dashed (a different dash for each), others take the palette in order
    for col, lab in names.items():
        if col in tuple(baseline) or str(lab).lower().startswith(("index", "passive")):
            style, color, mk = ("--", ":", "-.")[m % 3], INDEX_COL, ("s", "D", "^")[m % 3]
            m += 1
        else:
            style, color, mk = "-", PALETTE[k % len(PALETTE)], "o"
            k += 1
        ax.plot(d[x].to_numpy(), d[col].to_numpy(float), color=color, ls=style, lw=2.2, marker=mk, ms=4.5, label=str(lab))
    if pct:
        ax.yaxis.set_major_formatter(_pct)
    ax.set_xlabel(xlabel or x)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        _title(ax, title)
    if len(names) > 1:
        _legend(ax, legend, fontsize=9)      # R1
    return ax
