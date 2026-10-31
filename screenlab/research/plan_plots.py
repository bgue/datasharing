"""Plots for the plan Monte Carlo (`montecarlo/plan_mc.PlanMC`). Pure matplotlib (Agg-safe); each function draws on
`ax` (a new figure when None), takes `legend=True` and returns the Axes. Nothing reads or writes a file.

# PB: whole module. Style as research/tax_plots.py: Okabe-Ito, the passive index always grey and dashed (the base
case in every figure), the goal always amber, contributions black.

    fan_chart(mc)              after-tax wealth: median and 5-95 / 25-75 bands, strategy and index, vs contributed
    terminal_hist(mc)          terminal after-tax wealth, strategy vs index overlaid, goal and contributed marked
    paired_hist(mc)            strategy minus index on the same path, share ahead / behind
    sequence_bars(mc)          terminal wealth by where the worst 12 months fall (first / middle / last third)
    reversal_bars(mc)          the historical path in order vs reversed (same returns, same flows)
    figures(mc)                dict of the five figures
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .tax_plots import AMBER, GRID, INDEX_COL, INK, INK2, PALETTE, _ax, _legend, _title

STRAT = PALETTE[0]
BEHIND = PALETTE[1]
INDEX_LABEL = "Buy and hold the index (after tax)"
STRAT_LABEL = "Strategy (after tax)"


def _money(x, _=None):
    a = abs(x)
    return f"${x / 1e6:.1f}M" if a >= 1e6 else f"${x / 1e3:.0f}k" if a >= 1e3 else f"${x:.0f}"


def _fmt(ax, axis="y"):
    from matplotlib.ticker import FuncFormatter
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(FuncFormatter(_money))


def _sub(mc) -> str:
    g = mc.card.generator.get("__t", "?")
    return f"{mc.n_paths:,} {g} paths, seed {mc.seed}"


def fan_chart(mc, ax=None, legend=True, title=None, log=False):
    ax = _ax(ax, (8.5, 4.8))
    f = mc.fan
    for nm, col, ls in (("index", INDEX_COL, "--"), ("strategy", STRAT, "-")):
        lab = INDEX_LABEL if nm == "index" else STRAT_LABEL
        ax.fill_between(f.index, f[f"{nm}_p5"], f[f"{nm}_p95"], color=col, alpha=0.12, lw=0)
        ax.fill_between(f.index, f[f"{nm}_p25"], f[f"{nm}_p75"], color=col, alpha=0.22, lw=0)
        ax.plot(f.index, f[f"{nm}_p50"], color=col, ls=ls, lw=2.2, label=f"{lab}: median")
    ax.plot(f.index, f["contributed"], color=INK, lw=1.4, label="Contributed (net)")
    if mc.goal is not None:
        ax.axhline(mc.goal, color=AMBER, lw=1.6, ls=":", label=f"Goal {_money(mc.goal)}")
    if log:
        ax.set_yscale("log")
    _fmt(ax)
    ax.set_ylabel("Wealth, after tax, nominal")
    ax.set_xlabel("Bands: 25-75% (dark) and 5-95% (light) of paths")
    _title(ax, "", title or f"Wealth fan vs money contributed ({_sub(mc)})")
    _legend(ax, legend, loc="upper left", fontsize=9, frameon=False)
    return ax


def terminal_hist(mc, ax=None, legend=True, title=None, bins=50):
    ax = _ax(ax, (8.5, 4.6))
    t = mc.terminal
    hi = float(np.nanpercentile(pd.concat([t.strategy_after_tax, t.index_after_tax]), 99.5))
    b = np.linspace(0, hi, bins + 1)
    ax.hist(t.index_after_tax.clip(upper=hi), bins=b, color=INDEX_COL, alpha=0.45, lw=0, label=INDEX_LABEL)
    ax.hist(t.strategy_after_tax.clip(upper=hi), bins=b, color=STRAT, alpha=0.6, lw=0, label=STRAT_LABEL)
    for col, c, ls in (("index_after_tax", INDEX_COL, "--"), ("strategy_after_tax", STRAT, "-")):
        ax.axvline(t[col].median(), color=c, ls=ls, lw=2, label="_nolegend_")
    if mc.goal is not None:
        ax.axvline(mc.goal, color=AMBER, lw=2.2,
                   label=f"Goal {_money(mc.goal)}: short in {mc.p_shortfall:.0%} (strategy), {mc.p_shortfall_index:.0%} (index)")
    contributed = float(mc.fan["contributed"].iloc[-1])
    ax.axvline(contributed, color=INK, lw=1.2, ls=":", label=f"Contributed {_money(contributed)}")
    _fmt(ax, "x")
    ax.set_xlabel("Terminal wealth, after tax (values above the 99.5th percentile clipped); vertical lines: medians")
    ax.set_ylabel(f"Paths (of {mc.n_paths:,})")
    _title(ax, "", title or f"Terminal wealth: strategy vs index ({_sub(mc)})")
    _legend(ax, legend, loc="upper right", fontsize=9, frameon=False)
    return ax


def paired_hist(mc, ax=None, legend=True, title=None, bins=50):
    ax = _ax(ax, (8.5, 4.4))
    d = mc.paired
    lim = float(np.nanpercentile(np.abs(d), 99)) or 1.0
    b = np.linspace(-lim, lim, bins + 1)
    x = d.clip(-lim, lim)
    ahead = float((d >= 0).mean())
    ax.hist(x[d >= 0], bins=b, color=STRAT, alpha=0.75, lw=0, label=f"Strategy ahead of the index: {ahead:.0%} of paths")
    ax.hist(x[d < 0], bins=b, color=BEHIND, alpha=0.75, lw=0, label=f"Strategy behind the index: {1 - ahead:.0%}")
    ax.axvline(0, color=INDEX_COL, ls="--", lw=1.8, label="Index (base case: difference 0)")
    ax.axvline(float(d.median()), color=INK, lw=1.6, label=f"Median {_money(float(d.median()))}")
    _fmt(ax, "x")
    ax.set_xlabel("Strategy minus index, terminal wealth after tax, same path")
    ax.set_ylabel(f"Paths (of {mc.n_paths:,})")
    _title(ax, "", title or f"Paired difference ({_sub(mc)})")
    _legend(ax, legend, loc="upper right", fontsize=9, frameon=False)
    return ax


def _bars(ax, labels, strat, index, legend, ylabel):
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2, index, w, color=INDEX_COL, alpha=0.6, label=INDEX_LABEL, edgecolor="none")
    ax.bar(x + w / 2, strat, w, color=STRAT, label=STRAT_LABEL, edgecolor="none")
    ax.set_xticks(x, labels)
    _fmt(ax)
    ax.set_ylabel(ylabel)
    _legend(ax, legend, loc="upper left", fontsize=9, frameon=False)


def sequence_bars(mc, ax=None, legend=True, title=None, stat="median"):
    ax = _ax(ax, (7.5, 4.4))
    s = mc.sequence
    names = {"first": "first third", "middle": "middle third", "last": "last third"}
    labels = [f"{names[i]}\n(n={int(s.loc[i, 'n'])})" for i in s.index]
    _bars(ax, labels, s[f"strategy_{stat}"].fillna(0), s[f"index_{stat}"].fillna(0), legend,
          f"{stat.capitalize()} terminal wealth, after tax")
    ax.set_xlabel("Where the path's worst 12 months fall in the plan")
    _title(ax, "", title or f"Sequence of returns ({_sub(mc)})")
    return ax


def reversal_bars(mc, ax=None, legend=True, title=None):
    ax = _ax(ax, (6.5, 4.4))
    r = mc.reversal.loc[["forward", "reversed"]]
    _bars(ax, ["Historical order", "Same returns, reversed"], r["strategy_after_tax"], r["index_after_tax"], legend,
          "Terminal wealth, after tax")
    c = float(r["contributed"].iloc[0])
    ax.axhline(c, color=INK, ls=":", lw=1.2, label=f"Contributed {_money(c)}")
    _legend(ax, legend, loc="upper left", fontsize=9, frameon=False)
    _title(ax, "", title or "Same returns, same flows, opposite order")
    return ax


def figures(mc) -> dict:
    """The five figures as {name: Figure}."""
    out = {}
    for name, fn in (("fan", fan_chart), ("terminal", terminal_hist), ("paired", paired_hist),
                     ("sequence", sequence_bars), ("reversal", reversal_bars)):
        ax = fn(mc)
        ax.figure.tight_layout()
        out[name] = ax.figure
    return out
