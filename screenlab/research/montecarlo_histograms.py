"""Histograms of Monte Carlo outcomes vs the passive index, from the study's per-path table.

python -m screenlab.research.montecarlo_histograms      # reads research/montecarlo/per_path.csv, writes 2 PNGs

Figure 5: each portfolio's 10-year CAGR distribution overlaid on the S&P 500's, on the same paths.
Figure 6: the paired difference (portfolio minus index, same path) for every portfolio and horizon:
          the honest "vs index" view, because both sides saw identical markets.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .montecarlo_sp500 import COL, INDEX, INDEX_TR, INK, INK2, NICE, SURFACE, _style

OUT = Path(__file__).parent / "montecarlo"
PORTFOLIOS = ["momentum_12_1", "momentum_12_1+exit", "low_vol", "low_vol+exit"]
TR_BUMP = 0.02          # the study's dividend approximation for the price-only index


def load() -> pd.DataFrame:
    return pd.read_csv(OUT / "per_path.csv")


def fig5_cagr_vs_index(d: pd.DataFrame, horizon: float = 10.0) -> Path:
    import matplotlib.pyplot as plt
    _style()
    h = d[d.horizon_years == horizon]
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
        ax.set_xlabel("10-year CAGR (% per year)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Number of paths (of 1000)")
    fig.suptitle("10-year growth: each portfolio's distribution laid over the S&P 500's on the same 1,000 paths",
                 x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, "1000 block-bootstrap paths of the bundled 20-name S&P 500 sample (survivor-biased, "
             "price-only index); 1-point bins, values clipped to the axis. Vertical lines: medians.",
             fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    path = OUT / "5_cagr_histograms_vs_index.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig6_paired_excess(d: pd.DataFrame) -> Path:
    import matplotlib.pyplot as plt
    _style()
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
    path = OUT / "6_paired_excess_histograms.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main():
    d = load()
    for f in (fig5_cagr_vs_index(d), fig6_paired_excess(d)):
        print(f)


if __name__ == "__main__":
    main()
