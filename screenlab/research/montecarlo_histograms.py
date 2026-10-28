"""Histograms of Monte Carlo outcomes vs the passive index, from the study's per-path table.

python -m screenlab.research.montecarlo_histograms      # reads research/montecarlo/per_path.csv, writes 4 PNGs

Figure 5: each portfolio's 10-year CAGR distribution overlaid on the S&P 500's, on the same paths.
Figure 6: the paired difference (portfolio minus index, same path) for every portfolio and horizon:
          the honest "vs index" view, because both sides saw identical markets.
Figure 7: maximum-drawdown distribution of each portfolio overlaid on the S&P 500's, same paths, 10 years.
Figure 7b: the paired drawdown difference (portfolio minus index, same path): how often the portfolio's worst
          fall is deeper than the index's. It adds what 7 cannot: 7 compares two marginal distributions, 7b says
          whether the portfolio is hurt in the SAME markets in which the index is.

Each function takes the per-path DataFrame (columns of per_path.csv, or `MonteCarloResult.per_path` with
`with_tr`) and returns the matplotlib Figure; with `path=` it saves the PNG, closes the figure and returns the path.
The notebook notebooks/montecarlo.ipynb imports these.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .montecarlo_sp500 import COL, INDEX, INDEX_TR, INK, INK2, NICE, SURFACE, _style, with_tr

OUT = Path(__file__).parent / "montecarlo"
PORTFOLIOS = ["momentum_12_1", "momentum_12_1+exit", "low_vol", "low_vol+exit"]
TR_BUMP = 0.02          # the study's dividend approximation for the price-only index


def load() -> pd.DataFrame:
    return pd.read_csv(OUT / "per_path.csv")


def prepare(per_path: pd.DataFrame) -> pd.DataFrame:
    """A `MonteCarloResult.per_path` table with the approximate total-return CAGR column the figures use."""
    return per_path if "index_tr_cagr" in per_path else with_tr(per_path)


def _finish(fig, path):
    import matplotlib.pyplot as plt
    if path is None:
        return fig
    fig.savefig(path)
    plt.close(fig)
    return Path(path)


def _n_paths(h: pd.DataFrame) -> int:
    return int(h["path"].nunique())


def fig5_cagr_vs_index(d: pd.DataFrame, horizon: float = 10.0, path=None):
    import matplotlib.pyplot as plt
    _style()
    d = prepare(d)
    h = d[d.horizon_years == horizon]
    n = _n_paths(h)
    idx = h[h.portfolio == INDEX].set_index("path")["cagr"] * 100
    lo, hi = -5, 40
    bins = np.arange(lo, hi + 1, 1.0)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for ax, p in zip(axes.ravel(), PORTFOLIOS):
        g = h[h.portfolio == p].set_index("path")
        x = g["cagr"] * 100
        trail = float((g["excess_cagr"] < 0).mean()) * 100
        trail_tr = float((g["cagr"] - g["index_tr_cagr"] < 0).mean()) * 100
        ax.hist(idx.clip(lo, hi), bins=bins, color=COL[INDEX], alpha=0.45, lw=0, label=NICE[INDEX])
        ax.hist(x.clip(lo, hi), bins=bins, color=COL[p], alpha=0.65, lw=0, label=NICE[p],
                hatch="//" if p.endswith("+exit") else None, edgecolor=SURFACE)
        ax.axvline(np.median(idx), color=COL[INDEX], lw=2)
        ax.axvline(np.median(x), color=COL[p], lw=2.4)
        ax.set_title(f"{NICE[p]}: median {np.median(x):.1f}% vs index {np.median(idx):.1f}%")
        ax.text(0.98, 0.95, f"trails index in {trail:.0f}% of paths\n({trail_tr:.0f}% vs index + ~2% div)",
                transform=ax.transAxes, ha="right", va="top", fontsize=10, color=INK2)
        ax.legend(loc="upper left", fontsize=9)
    for ax in axes[1]:
        ax.set_xlabel(f"{horizon:g}-year CAGR (% per year)")
    for ax in axes[:, 0]:
        ax.set_ylabel(f"Number of paths (of {n:,})")
    fig.suptitle(f"{horizon:g}-year growth: each portfolio's distribution laid over the S&P 500's on the same {n:,} paths",
                 x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, f"{n:,} block-bootstrap paths of the bundled 20-name S&P 500 sample (survivor-biased, "
             "price-only index); 1-point bins, values clipped to the axis. Vertical lines: medians.",
             fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    return _finish(fig, path)


def fig6_paired_excess(d: pd.DataFrame, path=None):
    import matplotlib.pyplot as plt
    _style()
    d = prepare(d)
    horizons = [5.0, 10.0, 20.0]
    lo, hi = -20, 30
    bins = np.arange(lo, hi + 1, 1.0)
    fig, axes = plt.subplots(len(PORTFOLIOS), len(horizons), figsize=(13, 11), sharex=True, sharey="row")
    for i, p in enumerate(PORTFOLIOS):
        for j, hz in enumerate(horizons):
            ax = axes[i, j]
            g = d[(d.portfolio == p) & (d.horizon_years == hz)]
            ex = (g["excess_cagr"] * 100).clip(lo, hi)
            below = ex < 0
            ax.hist(ex[~below], bins=bins, color=COL[p], alpha=0.75, lw=0, edgecolor=SURFACE,
                    hatch="//" if p.endswith("+exit") else None)
            ax.hist(ex[below], bins=bins, color=COL[INDEX], alpha=0.6, lw=0)
            ax.axvline(0, color=INK, lw=1.2)
            ax.axvline(TR_BUMP * 100, color=COL[INDEX_TR], lw=1.4, ls=":")
            trail = float(below.mean()) * 100
            trail_tr = float((ex < TR_BUMP * 100).mean()) * 100
            box = dict(facecolor=SURFACE, alpha=0.85, edgecolor="none", pad=2)
            ax.text(0.98, 0.95, f"behind index: {trail:.0f}%\nbehind index + ~2% div: {trail_tr:.0f}%",
                    transform=ax.transAxes, ha="right", va="top", fontsize=9.5, color=INK2, bbox=box)
            ax.text(0.02, 0.95, f"median +{np.median(ex):.1f} pts/yr", transform=ax.transAxes,
                    ha="left", va="top", fontsize=9.5, color=INK, bbox=box)
            if i == 0:
                ax.set_title(f"{int(hz)}-year horizon")
            if j == 0:
                ax.set_ylabel(f"{NICE[p]}\n\nnumber of paths", fontsize=10)
    fig.supxlabel("Portfolio CAGR minus S&P 500 CAGR on the same path (points per year)", fontsize=11, color=INK2,
                  y=0.045)
    fig.suptitle("Same path, two outcomes: how far each portfolio finishes ahead of (colour) or behind (grey) the S&P 500",
                 x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, "Grey bars: paths where the portfolio ended behind the price index. Dotted line at +2 pts/yr: "
             "roughly where it would be behind a total-return index instead. Survivor-biased 20 names.",
             fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    return _finish(fig, path)


def fig7_drawdown_vs_index(d: pd.DataFrame, horizon: float = 10.0, path=None):
    """Worst peak-to-trough fall within the horizon: each portfolio (colour) over the index (grey), same paths."""
    import matplotlib.pyplot as plt
    _style()
    h = d[d.horizon_years == horizon]
    n = _n_paths(h)
    idx = h[h.portfolio == INDEX]["max_drawdown"] * 100
    bins = np.arange(-100, 1, 2.5)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for ax, p in zip(axes.ravel(), PORTFOLIOS):
        x = h[h.portfolio == p]["max_drawdown"] * 100
        ax.hist(idx, bins=bins, color=COL[INDEX], alpha=0.45, lw=0, label=NICE[INDEX])
        ax.hist(x, bins=bins, color=COL[p], alpha=0.65, lw=0, label=NICE[p],
                hatch="//" if p.endswith("+exit") else None, edgecolor=SURFACE)
        for lv in (-30, -50):
            ax.axvline(lv, color=INK2, lw=0.9, ls=(0, (2, 3)))
        ax.axvline(np.median(idx), color=COL[INDEX], lw=2)
        ax.axvline(np.median(x), color=COL[p], lw=2.4)
        ax.set_title(f"{NICE[p]}: median {np.median(x):.0f}% vs index {np.median(idx):.0f}%")
        box = dict(facecolor=SURFACE, alpha=0.85, edgecolor="none", pad=2)
        rows = [("Portfolio", COL[p], (x < -30).mean() * 100, (x < -50).mean() * 100),
                ("S&P 500", INK2, (idx < -30).mean() * 100, (idx < -50).mean() * 100)]
        ax.text(0.02, 0.80, "paths with a fall worse than\n"
                "             -30%   -50%\n"
                + "\n".join(f"{nm:<9}{a:>6.0f}%{b:>6.0f}%" for nm, _, a, b in rows),
                transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color=INK, family="monospace", bbox=box)
        ax.legend(loc="upper left", fontsize=9)
    for ax in axes[1]:
        ax.set_xlabel(f"Worst peak-to-trough fall within {horizon:g} years (%)")
    for ax in axes[:, 0]:
        ax.set_ylabel(f"Number of paths (of {n:,})")
    fig.suptitle(f"Worst fall within {horizon:g} years: each portfolio's distribution laid over the S&P 500's on the same "
                 f"{n:,} paths", x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, "Monthly steps (falls inside a month are not seen); dotted lines at -30% and -50%; "
             "vertical lines: medians. Survivor-biased 20 names, price-only index.", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    return _finish(fig, path)


def fig7b_paired_drawdown(d: pd.DataFrame, horizon: float = 10.0, path=None):
    """Portfolio max drawdown minus the index's on the same path (points; positive = portfolio fell LESS)."""
    import matplotlib.pyplot as plt
    _style()
    h = d[d.horizon_years == horizon]
    n = _n_paths(h)
    idx = h[h.portfolio == INDEX].set_index("path")["max_drawdown"] * 100
    lo, hi = -60, 60
    bins = np.arange(lo, hi + 1, 2.5)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for ax, p in zip(axes.ravel(), PORTFOLIOS):
        x = h[h.portfolio == p].set_index("path")["max_drawdown"] * 100
        diff = (x - idx).clip(lo, hi)
        worse = diff < 0
        ax.hist(diff[~worse], bins=bins, color=COL[p], alpha=0.75, lw=0, edgecolor=SURFACE,
                hatch="//" if p.endswith("+exit") else None)
        ax.hist(diff[worse], bins=bins, color=COL[INDEX], alpha=0.6, lw=0)
        ax.axvline(0, color=INK, lw=1.2)
        box = dict(facecolor=SURFACE, alpha=0.85, edgecolor="none", pad=2)
        ax.text(0.98, 0.95, f"deeper fall than index: {worse.mean() * 100:.0f}% of paths", transform=ax.transAxes,
                ha="right", va="top", fontsize=9.5, color=INK2, bbox=box)
        ax.text(0.02, 0.95, f"median {np.median(diff):+.1f} pts", transform=ax.transAxes, ha="left", va="top",
                fontsize=9.5, color=INK, bbox=box)
        ax.set_title(NICE[p])
    fig.supxlabel("Portfolio worst fall minus S&P 500 worst fall, same path (points; right = portfolio fell less)",
                  fontsize=11, color=INK2, y=0.045)
    for ax in axes[:, 0]:
        ax.set_ylabel(f"Number of paths (of {n:,})")
    fig.suptitle(f"Same path, two falls: how often each portfolio's worst {horizon:g}-year fall is milder (colour) or "
                 "deeper (grey) than the S&P 500's", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.text(0.01, 0.005, "Grey bars: paths where the portfolio fell further than the index. Monthly steps; "
             "survivor-biased 20 names, price-only index.", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    return _finish(fig, path)


def main():
    d = load()
    for fn, name in ((fig5_cagr_vs_index, "5_cagr_histograms_vs_index"), (fig6_paired_excess, "6_paired_excess_histograms"),
                     (fig7_drawdown_vs_index, "7_drawdown_histograms_vs_index"),
                     (fig7b_paired_drawdown, "7b_paired_drawdown_histograms")):
        print(fn(d, path=OUT / f"{name}.png"))


if __name__ == "__main__":
    main()
