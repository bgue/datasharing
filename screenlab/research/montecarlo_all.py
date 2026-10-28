"""Monte Carlo study for EVERY portfolio on the synthetic market (DESIGN.md M): how wide is the range of outcomes of
each screen and Globe portfolio, and how often does it trail the passive index?

    python -m screenlab.research.montecarlo_all          # from the directory that contains screenlab/

Setup (write-up: research/montecarlo_all/MONTECARLO_ALL.md):
  * Base: `Lab.synthetic()` (250 names, daily, 2000-2024, fundamentals), cut to month-ends by the operation's own
    `to_monthly` (the helper the S&P 500 study uses). Benchmark SYN_CAPWT (cap-weighted total return).
  * Every REGISTRY screen at a 12-month rebalance, plus the Globe portfolios at their published monthly rebalance
    (labelled `<name> (monthly)`; the 12-month run of the same screen is `<name> (annual)`).
  * BlockBootstrap(12), 1000 paths, horizons 5/10/20 years, 100k start wealth, 25 bps one-way costs, fixed seed.
    `selection="auto"`: price ranks re-rank on every path (per_path), every other screen is fixed.
  * The two calls (annual, monthly) use the same seed and so draw identical paths; the study asserts the index agrees.
Progress is written to research/montecarlo_all/progress.txt. About 5-10 minutes.
Outputs: research/montecarlo_all/ (CSVs are git-ignored; PNGs and the .md are tracked).
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from screenlab import Lab
from screenlab.montecarlo import BlockBootstrap
from screenlab.research.montecarlo_sp500 import (INK, INK2, SURFACE, _md, _pc, _style)
from screenlab.screens import GLOBE_PORTFOLIOS, REGISTRY

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "montecarlo_all"

SEED = 20241
N_PATHS = 1000
HORIZONS = [5, 10, 20]
START_WEALTH = 100_000.0
COST_BPS = 25.0
H = 10
# one hue per family (validated with dataviz/scripts/validate_palette.js: pass); the index is always neutral grey
COL_PATH, COL_FIXED, COL_INDEX = "#0072B2", "#D55E00", "#6B6B6B"
M_DD30 = "P(MaxDD<-30%)"
# The registry's momentum screens are multi-stage specs (top-300 size cut), so they run in fixed mode. Two price-only
# ranks (the S&P 500 study's screens, n=10) are added so that the per_path mode is represented too.
EXTRA_PRICE_RANKS = ("momentum_12_1", "low_vol")


def log(msg):
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(OUT / "progress.txt", "a") as f:
        f.write(line + "\n")


# ----------------------------------------------------------------------------------------------- runs
def labels():
    """(registry screens at 12 months, globe screens at their published monthly rebalance, label map)."""
    globe = {n: p for n, p in GLOBE_PORTFOLIOS.items()}
    monthly = [n for n, p in globe.items() if p.config_overrides.get("rebalance_months") == 1]
    annual_label = {n: (f"{n} (annual)" if n in monthly else n) for n in REGISTRY}
    annual_label.update({n: n for n in EXTRA_PRICE_RANKS})
    return list(REGISTRY) + list(EXTRA_PRICE_RANKS), monthly, annual_label


def run_all(lab):
    annual, monthly, annual_label = labels()
    common = dict(generator=BlockBootstrap(12), n_paths=N_PATHS, horizon_years=HORIZONS, start_wealth=START_WEALTH,
                  seed=SEED, cost_bps=COST_BPS, selection="auto")
    from screenlab.realdata.bundled import price_screens
    extra = price_screens(10)
    annual = [extra[n] if n in EXTRA_PRICE_RANKS else n for n in annual]
    log(f"annual run: {len(annual)} screens at 12 months")
    ra = lab.montecarlo(annual, rebalance_months=12, **common)
    log(f"monthly run: {len(monthly)} Globe portfolios at 1 month")
    rm = lab.montecarlo(monthly, rebalance_months=1, **common)
    idx = ra.index_label
    a = ra.per_path[ra.per_path.portfolio == idx].reset_index(drop=True)
    b = rm.per_path[rm.per_path.portfolio == idx].reset_index(drop=True)
    assert np.allclose(a.cagr, b.cagr), "the two runs did not share paths"
    pp, fan, mode = [], {}, {}
    for res, rename in ((ra, annual_label), (rm, {n: f"{n} (monthly)" for n in monthly})):
        for old, new in rename.items():
            g = res.per_path[res.per_path.portfolio == old].copy()
            g["portfolio"] = new
            pp.append(g)
            mode[new] = res.selection_mode[old]
            for q in (5, 25, 50, 75, 95):
                fan[f"{new}|p{q}"] = res.fan[f"{old}|p{q}"]
    gi = ra.per_path[ra.per_path.portfolio == idx]
    pp.append(gi)
    for q in (5, 25, 50, 75, 95):
        fan[f"{idx}|p{q}"] = ra.fan[f"{idx}|p{q}"]
    pp = pd.concat(pp, ignore_index=True)
    order = list(mode)
    tbl = pd.concat([ra.table.reset_index(), rm.table.reset_index()], ignore_index=True)
    ren = {**annual_label, **{n: f"{n} (monthly)" for n in monthly}}
    # the two tables share portfolio names (monthly vs annual): rename each block separately
    ta = ra.table.reset_index(); ta["portfolio"] = ta["portfolio"].map(lambda p: annual_label.get(p, p))
    tm = rm.table.reset_index(); tm = tm[tm.portfolio != idx]; tm["portfolio"] = tm["portfolio"].map(lambda p: f"{p} (monthly)")
    tbl = pd.concat([ta, tm], ignore_index=True)
    return pp, pd.DataFrame(fan, index=ra.fan.index), tbl, mode, order, ra


def summary(pp, mode, idx):
    rows = []
    for (p, h), g in pp.groupby(["portfolio", "horizon_years"], sort=False):
        if p == idx:
            continue
        rows.append(dict(portfolio=p, mode=mode[p], horizon_years=h, median_cagr=g.cagr.median(),
                         p5_cagr=g.cagr.quantile(.05), p95_cagr=g.cagr.quantile(.95),
                         P_trail=float((g.cagr < g.index_cagr).mean()), median_excess=g.excess_cagr.median(),
                         P_dd30=float((g.max_drawdown < -.30).mean()), P_dd50=float((g.max_drawdown < -.50).mean()),
                         median_terminal_wealth=g.terminal_wealth.median()))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------- charts
def _col(mode):
    return COL_PATH if mode == "per_path" else COL_FIXED


def _short(p):
    return p.replace("_dividend", "_div")


def _foot(fig, text):
    fig.text(0.01, 0.006, text, fontsize=8.5, color=INK2)


def chart_trail(S, mode, path):
    import matplotlib.pyplot as plt
    d = S.pivot(index="portfolio", columns="horizon_years", values="P_trail") * 100
    d = d.sort_values(10.0)
    fig, ax = plt.subplots(figsize=(12, 8))
    y = np.arange(len(d))
    ax.barh(y, d[10.0], color=[_col(mode[p]) for p in d.index], height=0.62)
    ax.scatter(d[5.0], y, marker="v", s=34, color=INK, zorder=3, label="5 years")
    ax.scatter(d[20.0], y, marker="D", s=26, color=INK, facecolor=SURFACE, zorder=3, label="20 years")
    for yi, v in zip(y, d[10.0]):
        ax.text(1, yi, f"{v:.0f}%", va="center", fontsize=8.5, color="white", fontweight="bold")
    ax.axvline(50, color=INK2, lw=1, ls=(0, (3, 3)))
    ax.set_yticks(y, [_short(p) for p in d.index], fontsize=9)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of simulated futures where the portfolio ends behind the passive index (%)")
    ax.grid(axis="y", visible=False)
    n_hi = int((d[10.0] > 50).sum())
    ax.set_title(f"Over 10 years {n_hi} of {len(d)} portfolios trail the index in more than half of futures; "
                 f"the best still trails in {d[10.0].min():.0f}%", loc="left", fontsize=12.5)
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    ax.legend(handles=[Patch(color=COL_PATH, label="bar = 10 years, price rank (re-ranks on every path)"),
                       Patch(color=COL_FIXED, label="bar = 10 years, fixed picks (do not react to the path)"),
                       Line2D([], [], marker="v", ls="", color=INK, label="5 years"),
                       Line2D([], [], marker="D", ls="", color=INK, mfc=SURFACE, label="20 years")],
              loc="lower right", fontsize=9)
    _foot(fig, "1000 block-bootstrap paths on synthetic data, same paths for every portfolio and the index "
               "(cap-weighted total return). Dashed line: 50%. Lower = better.")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def chart_cagr(pp, mode, idx, path):
    import matplotlib.pyplot as plt
    d = pp[pp.horizon_years == H]
    med = d.groupby("portfolio").cagr.median().drop(idx).sort_values(ascending=False)
    order = [idx] + list(med.index)
    data = [d[d.portfolio == p].cagr.to_numpy() * 100 for p in order]
    fig, ax = plt.subplots(figsize=(12, 8))
    pos = np.arange(len(order))[::-1]
    bp = ax.boxplot(data, positions=pos, whis=(5, 95), showfliers=False, patch_artist=True, widths=0.6, vert=False,
                    medianprops=dict(color=INK, lw=2), whiskerprops=dict(color=INK2), capprops=dict(color=INK2))
    for patch, p in zip(bp["boxes"], order):
        patch.set(facecolor=COL_INDEX if p == idx else _col(mode[p]), alpha=0.8 if p == idx else 0.7, edgecolor=INK2)
    ax.axvline(np.median(data[0]), color=COL_INDEX, lw=1.2, ls=(0, (3, 3)))
    ax.axvline(0, color=INK2, lw=1)
    ax.set_yticks(pos, [("PASSIVE INDEX" if p == idx else _short(p)) for p in order], fontsize=9)
    ax.set_xlabel("10-year CAGR (% per year)")
    ax.grid(axis="y", visible=False)
    beat = int((med > np.median(data[0]) / 100).sum())
    wid = [np.percentile(x, 95) - np.percentile(x, 5) for x in data]
    ax.set_title(f"Only {beat} of {len(med)} portfolios have a median 10-year CAGR above the index "
                 f"({np.median(data[0]):.1f}%); every 5-95% range spans {min(wid):.0f}-{max(wid):.0f} points",
                 loc="left", fontsize=12.5)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=COL_INDEX, label="passive index"), Patch(color=COL_PATH, label="price rank (per path)"),
                       Patch(color=COL_FIXED, label="fixed picks")], loc="lower right", fontsize=9)
    _foot(fig, "Box 25-75%, whiskers 5-95%, bar = median; dashed line = index median. 1000 block-bootstrap paths, synthetic data, "
               "25 bps costs, no flows.")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def chart_fan(fan, S, mode, order, idx, path):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    p10 = S[S.horizon_years == 10].set_index("portfolio").P_trail
    names = sorted(order, key=lambda p: p10[p])
    cols = 5
    rows = int(np.ceil(len(names) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(12, 8), sharex=True, sharey=True)
    yrs = fan.index.to_numpy()
    for ax, p in zip(axes.flat, names):
        c = _col(mode[p])
        ax.fill_between(yrs, fan[f"{idx}|p5"], fan[f"{idx}|p95"], color=COL_INDEX, alpha=0.20, lw=0)
        ax.plot(yrs, fan[f"{idx}|p50"], color=COL_INDEX, lw=1.2)
        ax.fill_between(yrs, fan[f"{p}|p5"], fan[f"{p}|p95"], color=c, alpha=0.18, lw=0)
        ax.fill_between(yrs, fan[f"{p}|p25"], fan[f"{p}|p75"], color=c, alpha=0.32, lw=0)
        ax.plot(yrs, fan[f"{p}|p50"], color=c, lw=1.8)
        ax.set_yscale("log")
        ax.set_ylim(25_000, 2_000_000)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x / 1e6:g}M" if x >= 1e6 else f"{x / 1e3:g}k"))
        ax.set_title(_short(p).replace(" (", "\n("), loc="left", fontsize=7.5, pad=2)
        ax.tick_params(labelsize=7)
        ax.minorticks_off()
    for ax in list(axes.flat)[len(names):]:
        ax.set_visible(False)
    last = axes.flat[len(names)] if len(names) < rows * cols else None
    for ax in axes[-1]:
        ax.set_xlabel("Years", fontsize=8)
    end = fan.iloc[-1]
    above = sum(end[f"{p}|p50"] > end[f"{idx}|p50"] for p in names)
    fig.suptitle(f"After 20 years {above} of {len(names)} portfolios have a median wealth above the index median, and every "
                 f"portfolio's 5-95% band is wide and overlaps the index's", x=0.01, ha="left", fontsize=12, fontweight="bold",
                 y=0.995, wrap=True)
    _foot(fig, "Wealth from 100k (log scale). Grey = passive index (median, 5-95% band), same in every panel; colour = portfolio "
               "(median, 25-75% dark,\n5-95% light; blue = price rank, orange = fixed picks). Ordered by P(trail) at 10 years.")
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    fig.savefig(path)
    plt.close(fig)


def chart_dd(pp, S, mode, order, idx, path):
    import matplotlib.pyplot as plt
    d = pp[pp.horizon_years == H]
    dd30 = S[S.horizon_years == H].set_index("portfolio").P_dd30
    names = sorted(order, key=lambda p: dd30[p])
    cols = 5
    rows = int(np.ceil(len(names) / cols))
    bins = np.linspace(0, 100, 26)
    gi = -d[d.portfolio == idx].max_drawdown.to_numpy() * 100
    fig, axes = plt.subplots(rows, cols, figsize=(12, 8), sharex=True, sharey=True)
    for ax, p in zip(axes.flat, names):
        g = -d[d.portfolio == p].max_drawdown.to_numpy() * 100
        ax.hist(gi, bins=bins, color=COL_INDEX, alpha=0.45, weights=np.ones(len(gi)) / len(gi) * 100, lw=0)
        ax.hist(g, bins=bins, color=_col(mode[p]), alpha=0.65, weights=np.ones(len(g)) / len(g) * 100, lw=0)
        ax.axvline(30, color=INK2, lw=0.7, ls=(0, (2, 2)))
        ax.set_title(_short(p).replace(" (", "\n("), loc="left", fontsize=7.5, pad=2)
        ax.text(0.97, 0.9, f"{(g > 30).mean() * 100:.0f}% > 30%", transform=ax.transAxes, ha="right", va="top", fontsize=7)
        ax.tick_params(labelsize=7)
    for ax in list(axes.flat)[len(names):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("Worst fall (%)", fontsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel("% of paths", fontsize=8)
    ndeeper = int(sum(dd30[p] > (gi > 30).mean() for p in names))
    fig.suptitle(f"Over 10 years {ndeeper} of {len(names)} portfolios suffer a fall deeper than 30% more often than the index "
                 f"({(gi > 30).mean() * 100:.0f}% of paths)", x=0.01, ha="left", fontsize=12, fontweight="bold", y=0.995, wrap=True)
    _foot(fig, "Histogram of the worst peak-to-trough fall within 10 years, 1000 paths, month-end data. Grey = passive index (same in every "
               "panel);\ncolour = portfolio (blue = price rank, orange = fixed picks). Dashed line: 30% fall. Ordered by P(fall > 30%).")
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    fig.savefig(path)
    plt.close(fig)


# ----------------------------------------------------------------------------------------------- report
def write_report(S, mode, order, idx, card, tbl, path, secs):
    s = S.set_index(["portfolio", "horizon_years"])
    tb = tbl.set_index(["portfolio", "horizon_years"])
    ix10 = tb.loc[(idx, 10.0)]
    p10 = S[S.horizon_years == 10].set_index("portfolio")
    ranked = list(p10.sort_values("P_trail").index)
    rows = []
    for p in ranked:
        r = {"Portfolio": p, "Mode": mode[p], "Median 10y CAGR": _pc(p10.loc[p, "median_cagr"], 1),
             "5-95% of 10y CAGR": f"{_pc(p10.loc[p, 'p5_cagr'], 0)} to {_pc(p10.loc[p, 'p95_cagr'], 0)}"}
        for h in (5, 10, 20):
            r[f"P(trail) {h}y"] = _pc(s.loc[(p, float(h)), "P_trail"])
        r["P(fall>30%) 10y"] = _pc(p10.loc[p, "P_dd30"])
        rows.append(r)
    rows.append({"Portfolio": idx + " (passive)", "Mode": "-", "Median 10y CAGR": _pc(ix10["CAGR_p50"], 1),
                 "5-95% of 10y CAGR": f"{_pc(ix10['CAGR_p5'], 0)} to {_pc(ix10['CAGR_p95'], 0)}", "P(trail) 5y": "-",
                 "P(trail) 10y": "-", "P(trail) 20y": "-", "P(fall>30%) 10y": _pc(ix10[M_DD30])})
    main = _md(pd.DataFrame(rows))
    n = len(ranked)
    n_fixed = sum(v == "fixed" for v in mode.values())
    n_half = int((p10.P_trail > 0.5).sum())
    n_med = int((p10.median_cagr > ix10["CAGR_p50"]).sum())
    best, worst = ranked[0], ranked[-1]
    pt = S.pivot(index="portfolio", columns="horizon_years", values="P_trail")
    longer_better = int((pt[20.0] < pt[5.0]).sum())
    grp = p10.groupby("mode")[["P_trail", "median_cagr"]].median()
    dd_more = int((p10.P_dd30 > ix10[M_DD30]).sum())
    pairs = [(p[:-10], p10.loc[p, "P_trail"], p10.loc[p[:-10] + " (annual)", "P_trail"]) for p in p10.index
             if p.endswith(" (monthly)") and p[:-10] + " (annual)" in p10.index]
    n_worse = sum(m > an for _, m, an in pairs)
    mon_vs_ann = (f"**Rebalancing monthly instead of annually raises P(trail) at 10 years for {n_worse} of {len(pairs)} "
                  f"screens** (same screen, same paths; mean {np.mean([m for _, m, _ in pairs]) * 100:.0f}% vs "
                  f"{np.mean([an for _, _, an in pairs]) * 100:.0f}%): the extra turnover costs more than it earns here.")
    text = f"""# Monte Carlo for every portfolio: how often does each screen trail the index?

*{card.line()}. Script: `research/montecarlo_all.py` (seed {SEED}, {N_PATHS} paths, {secs / 60:.0f} minutes). Tables: `research/montecarlo_all/*.csv` (regenerated by the script, not tracked in git).*

## Bottom line

* **{n_half} of {n} portfolios trail the passive index in more than half of 10-year futures; {n_med} of {n} have a median 10-year CAGR above the index's {_pc(ix10['CAGR_p50'], 1)}.** The best, `{best}`, trails in {_pc(p10.loc[best, 'P_trail'])} of 10-year futures; the worst, `{worst}`, in {_pc(p10.loc[worst, 'P_trail'])}.
* **No flattering here, by construction.** The data is synthetic: the prices are generated, so there are no hindsight-picked survivors. The package's survivorship score is {card.survivorship_score * 100:+.1f} points a year (an equal-weight basket of the universe against the index; small, and the data keeps its delisted names), and the benchmark is a total-return index, so the dividend gap that flattered the S&P 500 study is absent. What you see is the screen against the market, after {COST_BPS:.0f} bps of costs.
* **The range of outcomes is wide for everything, the index included.** The 5-95% range of 10-year growth is {_pc(ix10['CAGR_p5'], 0)} to {_pc(ix10['CAGR_p95'], 0)} a year for the index, and a fall deeper than 30% occurs in {_pc(ix10[M_DD30])} of its 10-year paths (this synthetic market is a rough one: the index's median 10-year CAGR is only {_pc(ix10['CAGR_p50'], 1)}). {dd_more} of {n} portfolios suffer such a fall more often than the index.
* {mon_vs_ann}
* **A longer horizon helps {longer_better} of {n} portfolios**: their chance of trailing is lower at 20 years than at 5. For the others the gap, if anything, widens with time.
* **Price-rank screens (re-ranked on every path) and fixed-pick screens differ**: median P(trail) at 10 years is {_pc(grp.loc['per_path', 'P_trail']) if 'per_path' in grp.index else '-'} for the {n - n_fixed} price-rank portfolios and {_pc(grp.loc['fixed', 'P_trail']) if 'fixed' in grp.index else '-'} for the {n_fixed} fixed-pick ones. Read this with the caveat below.

## Method in plain words

1. **The history.** A synthetic market of 250 stocks, daily, 2000-2024, with accounting data (earnings, book value, dividends, ...) available as of each date, and a cap-weighted total-return index. It is cut to month-end prices.
2. **{N_PATHS} alternative futures.** Cut the history into 12-month blocks (block bootstrap), draw blocks at random and glue them into 22-year paths. Within a block every stock, the index and the accounting data keep their real joint dates, so crashes hit everything together. The first year of each path is a warm-up; then 5, 10 and 20 years are measured. Every portfolio and the index see exactly the same futures.
3. **The portfolios.** Every screen in the registry at a 12-month rebalance, plus the seven monthly Globe portfolios at their published monthly rebalance (marked `(monthly)`; the 12-month run of the same screen is marked `(annual)`), plus two price-only ranks added for the per-path mode (`momentum_12_1`, `low_vol`, 10 names). Equal weight, {COST_BPS:.0f} bps one-way trading cost. The Globe portfolio `bluechip_dividend_growth` is annual in the registry run; its published hold rule (keep a holding while it ranks in the top 2n) cannot run in fixed mode, so it appears once, without the hold rule.
4. **The numbers.** For each future: terminal wealth from 100,000, CAGR, the worst fall, and whether the portfolio ended behind the index; percentiles over the {N_PATHS} futures.

## The one caveat that matters: fixed-mode picks

Only the {n - n_fixed} plain price-rank portfolios (`momentum_12_1`, `low_vol`) can be re-run on each simulated future's own prices (**per_path**). The other {n_fixed} of {n} use accounting data (earnings, book value, dividends) or a size cut, which the bootstrap does not re-create; this includes the registry's `momentum_6m` and `momentum_12m`, which are multi-stage screens (top 300 by size, then rank). For these the package decides the picks **once, on the real (synthetic) history, at each date, and carries them along when the date is reused in a simulated future (fixed mode)**. In plain words: **a fixed-mode portfolio does not react to what happens on the simulated path.** It holds the names the screen would have chosen at the matching date of the original history, whatever those names did in the new future. The result shows how those historical picks fare when their returns are rearranged, not how the screen would behave in a different world. Fixed-mode and per-path numbers are therefore not strictly like for like, and the fixed-mode ones understate how much a real screen's picks would change from future to future. The selection mode is recorded per portfolio in the result and in the table below.

## Results

**Main table** (ranked by the chance of trailing the index at 10 years; lower is better). P(trail) = share of futures where the portfolio ends the horizon behind the index on the same future. CAGR is the portfolio's own growth with no deposits or withdrawals.

{main}

## Charts

![Probability of trailing the index](1_probability_trailing_index.png)

![10-year CAGR distributions](2_cagr_10y_distribution.png)

![Wealth fans against the index](3_fan_small_multiples.png)

![Worst falls against the index](4_drawdown_histograms.png)

## What holds and what does not

**Holds, on this data:**
* The ordering of portfolios by the chance of trailing the index and by the width of outcomes. It survives a change of path draw because every portfolio sees the same paths.
* The statement that, after realistic costs and without survivorship, trailing the index is the common outcome for most of these rules over 5-20 years, and that no portfolio is safe from a 30% fall.
* The comparison of one portfolio's annual version with its monthly version (same screen, same paths): the difference is the rebalance frequency and its costs.

**Does not hold, or not proven:**
* A forecast. All futures are rearrangements of 24 years of one synthetic history; a regime absent from it cannot appear.
* Real-world edge. The synthetic market was generated with its own return rules, which is why results here can differ from the S&P 500 study; neither tells you what the screens do in the real full market.
* Fixed-mode results as behaviour. See the caveat above: those picks are history's, not the path's.
* The two Graham portfolios: their growth near 0% and shallow falls look like a mostly-in-cash portfolio (few names pass the tests in this data), which is not evidence of safety or of skill.
* Probabilities to the percentage point: with {N_PATHS} paths a probability near 50% carries about 1.5 points of sampling error, more because paths share content.

## Reproduce

    python -m screenlab.research.montecarlo_all      # from the directory that contains screenlab/

The operation is `Lab.montecarlo(..., selection="auto")` (see `docs/REFERENCE.md`).
"""
    Path(path).write_text(text)


# ----------------------------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "progress.txt").write_text("")
    log("building Lab.synthetic() (250 names, daily)")
    lab = Lab.synthetic()
    pp, fan, tbl, mode, order, ra = run_all(lab)
    idx = ra.index_label
    S = summary(pp, mode, idx)
    log("writing tables")
    S.to_csv(OUT / "summary.csv", index=False)
    tbl.to_csv(OUT / "main_table.csv", index=False)
    pp.drop(columns=["recovered"]).to_csv(OUT / "per_path.csv", index=False)
    fan.to_csv(OUT / "fan_quantiles.csv")
    pd.Series(mode, name="selection_mode").to_csv(OUT / "selection_mode.csv")
    log("drawing charts")
    _style()
    chart_trail(S, mode, OUT / "1_probability_trailing_index.png")
    chart_cagr(pp, mode, idx, OUT / "2_cagr_10y_distribution.png")
    chart_fan(fan, S, mode, order, idx, OUT / "3_fan_small_multiples.png")
    chart_dd(pp, S, mode, order, idx, OUT / "4_drawdown_histograms.png")
    secs = time.time() - t0
    write_report(S, mode, order, idx, ra.data_card, tbl, OUT / "MONTECARLO_ALL.md", secs)
    (OUT / "runtime_seconds.txt").write_text(f"{secs:.0f}\n")
    log(f"done in {secs:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
