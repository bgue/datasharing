"""Monte Carlo study on the bundled real S&P 500 data: how wide is the range of outcomes of two simple
screen portfolios, with and without the fixed trend exit, and how often do they trail the passive index?

    python -m screenlab.research.montecarlo_sp500          # from the directory that contains screenlab/

Setup (see MONTECARLO_SP500.md for the write-up):
  * Base: 20 surviving S&P 500 names + the S&P 500 PRICE index, 1990-2022, cut to month-end steps.
  * Portfolios: momentum_12_1 and low_vol (n=5, monthly rebalance, 25 bps one-way costs), each without an
    exit and with the fixed trend rule (TrendBreak("SP500"), threshold 0.8, hysteresis 0.1, weekly check,
    10-day minimum hold, fully to cash, re-enter on signal, 5 bps; research/CALIBRATION_SP500.md). The
    passive S&P 500 index runs on the same paths.
  * Main generator BlockBootstrap(12 months), 1000 paths, horizons 5/10/20 years, start wealth 100,000.
    Sensitivity (10-year summary rows only): StationaryBootstrap(12), RegimeBootstrap (volatility regimes),
    IIDBootstrap.
  * The index is a price index: P(trail) is shown against it as-is and against it plus ~2%/yr of dividends
    ("SP500 + ~2% div (approx. TR)").
Fixed seeds; about 1 minute. Outputs: research/montecarlo/ (CSV tables are git-ignored; PNGs and the .md are tracked).
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from screenlab.exit_policy import ExitPolicy
from screenlab.exit_signals import ExitScreen, TrendBreak
from screenlab.montecarlo import BlockBootstrap, IIDBootstrap, RegimeBootstrap, StationaryBootstrap
from screenlab.realdata.bundled import sp500_lab

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "montecarlo"

SEED = 20240
N_PATHS = 1000
HORIZONS = [5, 10, 20]
START_WEALTH = 100_000.0
COST_BPS = 25.0
DIV_YIELD = 0.02
SCREENS = ["momentum_12_1", "low_vol"]
INDEX = "SP500"
INDEX_TR = "SP500 + ~2% div (approx. TR)"
PORTFOLIOS = ["momentum_12_1", "momentum_12_1+exit", "low_vol", "low_vol+exit"]
NICE = {"momentum_12_1": "Momentum", "momentum_12_1+exit": "Momentum + trend exit",
        "low_vol": "Low volatility", "low_vol+exit": "Low volatility + trend exit",
        INDEX: "S&P 500 (price index)", INDEX_TR: "S&P 500 + ~2% div (approx. TR)"}
GENERATORS = {"BlockBootstrap(12)": BlockBootstrap(12), "StationaryBootstrap(12)": StationaryBootstrap(12),
              "RegimeBootstrap (volatility)": RegimeBootstrap(), "IIDBootstrap": IIDBootstrap()}

# colour-blind-safe (Okabe-Ito): hue = strategy family, lighter + dashed = with the trend exit; neutral grey = index
COL = {"momentum_12_1": "#0072B2", "momentum_12_1+exit": "#56B4E9", "low_vol": "#D55E00",
       "low_vol+exit": "#E69F00", INDEX: "#6B6B6B", INDEX_TR: "#9A9A9A"}
LS = {"momentum_12_1": "-", "momentum_12_1+exit": "--", "low_vol": "-", "low_vol+exit": "--", INDEX: "-",
      INDEX_TR: ":"}
M_DD30, M_DD50 = "P(MaxDD<-30%)", "P(MaxDD<-50%)"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e3e2dd"


def policy() -> ExitPolicy:
    return ExitPolicy(ExitScreen([TrendBreak("SP500")], threshold=0.8, hysteresis=0.1, check="W-FRI",
                                 min_hold_days=10), switch_cost_bps=5.0)


def run(lab, generator, horizons):
    return lab.montecarlo(SCREENS, generator=generator, n_paths=N_PATHS, horizon_years=horizons,
                          start_wealth=START_WEALTH, exit=policy(), with_and_without=True, seed=SEED,
                          rebalance_months=1, cost_bps=COST_BPS)


# ----------------------------------------------------------------------------------------------- tables
def with_tr(per_path: pd.DataFrame) -> pd.DataFrame:
    """Add the approximate total-return index (price index + ~2%/yr of dividends) to the per-path table:
    its CAGR is (1 + price CAGR) * 1.02 - 1 on every path, and each portfolio's `index_tr_cagr` likewise."""
    pp = per_path.copy()
    pp["index_tr_cagr"] = (1 + pp["index_cagr"]) * (1 + DIV_YIELD) - 1
    return pp


def summary_tables(res) -> dict:
    pp = with_tr(res.per_path)
    t = res.table.reset_index()
    t["portfolio"] = t["portfolio"].astype(str)
    out = {"main_table": t}
    rows = []
    for (name, h), g in pp.groupby(["portfolio", "horizon_years"], sort=False):
        if name == INDEX:
            continue
        rows.append(dict(portfolio=name, horizon_years=h,
                         P_trail_price_index=float((g["cagr"] < g["index_cagr"]).mean()),
                         P_trail_approx_TR_index=float((g["cagr"] < g["index_tr_cagr"]).mean()),
                         median_excess_vs_price=float((g["cagr"] - g["index_cagr"]).median()),
                         median_excess_vs_approx_TR=float((g["cagr"] - g["index_tr_cagr"]).median())))
    out["trail_table"] = pd.DataFrame(rows)
    idx = pp[pp.portfolio == INDEX]
    tr = idx.assign(portfolio=INDEX_TR, cagr=idx["index_tr_cagr"])
    dist = pd.concat([pp, tr], ignore_index=True)
    out["cagr_distribution"] = (dist.groupby(["portfolio", "horizon_years"], sort=False)["cagr"]
                                .quantile([.05, .25, .5, .75, .95]).unstack().rename(columns=lambda q: f"p{int(q * 100)}")
                                .reset_index())
    out["per_path"] = dist.drop(columns=["recovered"])
    return out


def sensitivity(lab, main_res) -> pd.DataFrame:
    rows = []
    sets = {"BlockBootstrap(12)": main_res}
    for name, g in GENERATORS.items():
        if name not in sets:
            sets[name] = run(lab, g, [10])
    for name, res in sets.items():
        pp = with_tr(res.per_path)
        pp = pp[pp.horizon_years == 10]
        for p in PORTFOLIOS + [INDEX]:
            g = pp[pp.portfolio == p]
            rows.append(dict(generator=name, portfolio=p, median_cagr_10y=float(g["cagr"].median()),
                             P_trail_price_index=float((g["cagr"] < g["index_cagr"]).mean()) if p != INDEX else np.nan,
                             P_trail_approx_TR_index=float((g["cagr"] < g["index_tr_cagr"]).mean()) if p != INDEX else np.nan,
                             P_maxdd_worse_30=float((g["max_drawdown"] < -0.30).mean())))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------- charts
def _style():
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.titlecolor": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 11, "axes.titlesize": 12,
        "axes.titleweight": "bold", "legend.frameon": False, "figure.dpi": 100, "savefig.dpi": 100})


def _tick(x, _=None):
    return f"{x / 1e6:g}M" if x >= 1e6 else f"{x / 1e3:g}k"


def _money(x):
    """Wealth for titles: 2.9M, 640k."""
    return f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.0f}k"


def chart_fan(res, path=None):
    """Fan chart of the four portfolios vs the index; with `path=None` returns the Figure (notebook use)."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    fan = res.fan
    yrs = fan.index.to_numpy()
    tr_scale = (1 + DIV_YIELD) ** yrs
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for ax, p in zip(axes.flat, PORTFOLIOS):
        ax.fill_between(yrs, fan[f"{INDEX}|p5"], fan[f"{INDEX}|p95"], color=COL[INDEX], alpha=0.14, lw=0)
        ax.plot(yrs, fan[f"{INDEX}|p50"], color=COL[INDEX], lw=2, label=NICE[INDEX] + ", median (band 5-95%)")
        ax.plot(yrs, fan[f"{INDEX}|p50"] * tr_scale, color=COL[INDEX_TR], lw=1.6, ls=":",
                label=NICE[INDEX_TR] + ", median")
        ax.fill_between(yrs, fan[f"{p}|p5"], fan[f"{p}|p95"], color=COL[p], alpha=0.16, lw=0)
        ax.fill_between(yrs, fan[f"{p}|p25"], fan[f"{p}|p75"], color=COL[p], alpha=0.30, lw=0)
        ax.plot(yrs, fan[f"{p}|p50"], color=COL[p], lw=2.4, label=NICE[p] + ", median")
        end = fan.iloc[-1]
        ax.set_title(f"{NICE[p]}: median {_money(end[f'{p}|p50'])} (5-95%: {_money(end[f'{p}|p5'])} to "
                     f"{_money(end[f'{p}|p95'])})", loc="left", fontsize=10.5)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(_tick))
        ax.set_ylim(30_000, 30_000_000)
        ax.legend(loc="upper left", fontsize=8.5)
    for ax in axes[1]:
        ax.set_xlabel("Years invested")
    for ax in axes[:, 0]:
        ax.set_ylabel("Wealth (log scale), start 100k")
    m, lv = fan.iloc[-1], "low_vol"
    fig.suptitle(f"After 20 years 100k grows to a median {_money(m['momentum_12_1|p50'])} (momentum) or {_money(m[lv + '|p50'])} "
                 f"(low volatility) vs {_money(m[INDEX + '|p50'])} for the S&P 500 price index, with a wide range around each",
                 x=0.01, ha="left", fontsize=12.5, fontweight="bold", y=0.995, wrap=True)
    fig.text(0.01, 0.005, f"Bands: 25-75% (dark) and 5-95% (light) of {res.n_paths:,} block-bootstrap paths; survivor-biased 20 names, "
             "price index. Same paths for every panel.", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    if path is None:
        return fig
    fig.savefig(path)
    plt.close(fig)


def chart_cagr(dist, path, h=10):
    import matplotlib.pyplot as plt
    order = PORTFOLIOS + [INDEX, INDEX_TR]
    d = dist[dist.horizon_years == h]
    data = [d[d.portfolio == p]["cagr"].to_numpy() * 100 for p in order]
    fig, ax = plt.subplots(figsize=(12, 8))
    bp = ax.boxplot(data, whis=(5, 95), showfliers=False, patch_artist=True, widths=0.55, vert=True,
                    medianprops=dict(color=INK, lw=2.2), whiskerprops=dict(color=INK2), capprops=dict(color=INK2))
    for patch, p in zip(bp["boxes"], order):
        patch.set(facecolor=COL[p], alpha=0.75 if p not in (INDEX, INDEX_TR) else 0.55, edgecolor=INK2)
    for i, (p, x) in enumerate(zip(order, data), start=1):
        ax.text(i, np.percentile(x, 95) + 0.8, f"median {np.median(x):.1f}%", ha="center", fontsize=9.5, color=INK)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(range(1, len(order) + 1), [NICE[p].replace(" (", "\n(").replace(" + ", "\n+ ") for p in order], fontsize=9.5)
    ax.set_ylabel(f"{h}-year CAGR (% per year)")
    med = {p: np.median(x) for p, x in zip(order, data)}
    wid = [np.percentile(x, 95) - np.percentile(x, 5) for p, x in zip(order, data) if p in PORTFOLIOS]
    ax.set_title(f"Typical {h}-year growth is {med['low_vol+exit']:.0f}-{med['momentum_12_1']:.0f}% a year for the portfolios vs "
                 f"{med[INDEX]:.0f}% for the index, but each 5-95% range spans {min(wid):.0f}-{max(wid):.0f} points",
                 loc="left", fontsize=12.5)
    fig.text(0.01, 0.005, "Box: 25-75%, whiskers: 5-95%, bar: median; 1000 block-bootstrap paths. Survivor-biased 20 names; "
             "index is a price index (right-most box adds ~2%/yr dividends).", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def chart_trail(trail, path):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 8), sharey=True)
    hs = sorted(trail.horizon_years.unique())
    w = 0.2
    for ax, col, ttl in zip(axes, ["P_trail_price_index", "P_trail_approx_TR_index"],
                            ["vs S&P 500 price index", "vs S&P 500 + ~2% div (approx. TR)"]):
        for k, p in enumerate(PORTFOLIOS):
            v = [trail[(trail.portfolio == p) & (trail.horizon_years == h)][col].iloc[0] * 100 for h in hs]
            xs = np.arange(len(hs)) + (k - 1.5) * w
            ax.bar(xs, v, width=w - 0.02, color=COL[p], label=NICE[p], hatch="//" if p.endswith("+exit") else None,
                   edgecolor=SURFACE if not p.endswith("+exit") else INK2, lw=0.6)
            for x, y in zip(xs, v):
                ax.text(x, y + 1, f"{y:.0f}", ha="center", fontsize=9, color=INK)
        ax.set_xticks(range(len(hs)), [f"{int(h)} years" for h in hs])
        ax.set_title(ttl, loc="left")
        ax.set_xlabel("Investment horizon")
        ax.set_ylim(0, 100)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Share of paths where the portfolio ends behind the index (%)")
    axes[0].legend(loc="upper right", fontsize=9)
    fig.suptitle("Momentum rarely trails the index over long horizons; low volatility with the exit trails in a quarter to "
                 "half of paths, and a total-return index raises every bar", x=0.01, ha="left", fontsize=12.5,
                 fontweight="bold", wrap=True)
    fig.text(0.01, 0.005, "1000 block-bootstrap paths, same paths for portfolios and index. Survivor-biased 20 names. "
             "Hatched bars: with the trend exit.", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    fig.savefig(path)
    plt.close(fig)


def chart_drawdown(dist, path, h=10):
    import matplotlib.pyplot as plt
    d = dist[(dist.horizon_years == h) & (dist.portfolio != INDEX_TR)]
    xs = np.linspace(0.05, 0.85, 81)
    fig, ax = plt.subplots(figsize=(12, 8))
    labels = {}
    for p in PORTFOLIOS + [INDEX]:
        mdd = -d[d.portfolio == p]["max_drawdown"].to_numpy()
        ys = np.array([(mdd > x).mean() for x in xs]) * 100
        ax.plot(xs * 100, ys, color=COL[p], ls=LS[p], lw=2.6 if p != INDEX else 2.6, label=NICE[p])
        labels[p] = ys
    for x in (30, 50):
        ax.axvline(x, color=INK2, lw=0.9, ls=(0, (2, 3)))
    for x in (30, 50):
        i = int(round((x / 100 - 0.05) / 0.01))
        ax.annotate(f"{x}% fall\nindex {labels[INDEX][i]:.0f}%\nmomentum {labels['momentum_12_1'][i]:.0f}%\n"
                    f"low vol {labels['low_vol'][i]:.0f}%", (x, 97), xytext=(4, -4), textcoords="offset points",
                    va="top", fontsize=9, color=INK)
    ax.set_xlabel("Worst peak-to-trough fall within 10 years, at least this large (%)")
    ax.set_ylabel("Share of paths with such a fall (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="center right", fontsize=10)
    ax.set_title("The trend exit sharply cuts the chance of a deep fall; the index suffers deep falls more often "
                 "than low volatility", loc="left", fontsize=12.5)
    fig.text(0.01, 0.005, f"{h}-year horizon, 1000 block-bootstrap paths, monthly steps (intra-month falls are not seen). "
             "Survivor-biased 20 names.", fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(cols))) + "|"]
    out += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False, name=None)]
    return "\n".join(out)


def _pc(x, d=0):
    return "" if pd.isna(x) else f"{x * 100:.{d}f}%"


def _wealth(x):
    return f"{x / 1e6:.2f}M" if x >= 1e6 else f"{x / 1e3:.0f}k"


def write_report(res, tabs, sens, path):
    t = tabs["main_table"].set_index(["portfolio", "horizon_years"])
    tr = tabs["trail_table"].set_index(["portfolio", "horizon_years"])
    dist = tabs["cagr_distribution"].set_index(["portfolio", "horizon_years"])
    card = res.data_card
    rows = []
    for p in PORTFOLIOS + [INDEX, INDEX_TR]:
        d10 = dist.loc[(p, 10.0)]
        r = {"Portfolio": NICE[p], "Median 10y CAGR": _pc(d10["p50"], 1),
             "5-95% range of 10y CAGR": f"{_pc(d10['p5'], 1)} to {_pc(d10['p95'], 1)}"}
        for h in (5, 10, 20):
            r[f"P(trail price idx) {h}y"] = _pc(tr.loc[(p, float(h)), "P_trail_price_index"]) if p in PORTFOLIOS else "-"
        for h in (5, 10, 20):
            r[f"P(trail approx-TR idx) {h}y"] = _pc(tr.loc[(p, float(h)), "P_trail_approx_TR_index"]) if p in PORTFOLIOS else "-"
        r["P(MaxDD worse than -30%) 10y"] = _pc(t.loc[(p, 10.0), M_DD30]) if p in t.index.get_level_values(0) else \
            _pc(t.loc[(INDEX, 10.0), M_DD30])
        rows.append(r)
    main = _md(pd.DataFrame(rows))
    wrows = []
    for p in PORTFOLIOS + [INDEX]:
        for h in (10, 20):
            x = t.loc[(p, float(h))]
            wrows.append({"Portfolio": NICE[p], "Years": h, "5th pct": _wealth(x["TermWealth_p5"]),
                          "25th": _wealth(x["TermWealth_p25"]), "Median": _wealth(x["TermWealth_p50"]),
                          "75th": _wealth(x["TermWealth_p75"]), "95th": _wealth(x["TermWealth_p95"])})
    wealth = _md(pd.DataFrame(wrows))
    drows = []
    for p in PORTFOLIOS + [INDEX]:
        x = t.loc[(p, 10.0)]
        drows.append({"Portfolio": NICE[p], "P(fall worse than 30%)": _pc(x[M_DD30]), "P(fall worse than 50%)": _pc(x[M_DD50]),
                      "Median years to recover": "more than half not recovered" if pd.isna(x["RecoverYrs_med"]) else f"{x['RecoverYrs_med']:.1f}",
                      "P(not recovered by year 10)": _pc(x["P(unrecovered)"])})
    dd = _md(pd.DataFrame(drows))
    srows = []
    for g, grp in sens.groupby("generator", sort=False):
        for _, x in grp.iterrows():
            if x.portfolio == INDEX:
                continue
            srows.append({"Generator": g, "Portfolio": NICE[x.portfolio], "Median 10y CAGR": _pc(x.median_cagr_10y, 1),
                          "P(trail price idx) 10y": _pc(x.P_trail_price_index), "P(trail approx-TR idx) 10y": _pc(x.P_trail_approx_TR_index)})
    sensm = _md(pd.DataFrame(srows))
    idx_med = {g: grp[grp.portfolio == INDEX]["median_cagr_10y"].iloc[0] for g, grp in sens.groupby("generator", sort=False)}
    lo_trail = sens[(sens.portfolio == "momentum_12_1")]
    mom, lv, lvx, mox = "momentum_12_1", "low_vol", "low_vol+exit", "momentum_12_1+exit"
    text = f"""# How wide is the range of outcomes? A Monte Carlo study on S&P 500 data

*Data: {card.n_names} large S&P 500 stocks plus the S&P 500 index, {card.start[:4]}-{card.end[:4]} (bundled with the package). Script: `research/montecarlo_sp500.py` (under a minute, fixed seeds). Tables: `research/montecarlo/*.csv` (regenerated by the script, not tracked in git).*

## Bottom line

* **In this data the stock-picking portfolios beat the S&P 500 price index in most simulated futures, but the edge is about the size of the data's survivorship bias, so do not read it as skill or as a forecast.** Momentum's median 10-year growth is {_pc(dist.loc[(mom, 10.0), 'p50'], 1)} a year against {_pc(dist.loc[(INDEX, 10.0), 'p50'], 1)} for the index, and it trails the index in only {_pc(tr.loc[(mom, 10.0), 'P_trail_price_index'])} of 10-year paths. But the 20 stocks are survivors picked with hindsight: an equal-weight basket of them beat the index by {card.survivorship_score * 100:.0f} percentage points a year, and the portfolios' median edge over the index here is {_pc(tr.loc[(mom, 10.0), 'median_excess_vs_price'], 0)} (momentum) and {_pc(tr.loc[(lv, 10.0), 'median_excess_vs_price'], 0)} (low volatility). The data cannot tell skill from that bias.
* **The range of outcomes is wide even in this friendly setting.** A 10-year investor in the momentum portfolio ends between {_wealth(t.loc[(mom, 10.0), 'TermWealth_p5'])} (5th percentile) and {_wealth(t.loc[(mom, 10.0), 'TermWealth_p95'])} (95th) from 100k; the median is {_wealth(t.loc[(mom, 10.0), 'TermWealth_p50'])}. In {_pc(t.loc[(mom, 10.0), M_DD30])} of those paths there is a fall of more than 30% on the way, and in {_pc(t.loc[(mom, 10.0), M_DD50])} a fall of more than 50% (month-end data, so real intra-month falls are deeper).
* **The price-index benchmark flatters the portfolios.** Adding roughly 2% a year of dividends to the index (an approximation, not a real total-return series) raises the chance of trailing it at 10 years from {_pc(tr.loc[(mom, 10.0), 'P_trail_price_index'])} to {_pc(tr.loc[(mom, 10.0), 'P_trail_approx_TR_index'])} (momentum), {_pc(tr.loc[(lv, 10.0), 'P_trail_price_index'])} to {_pc(tr.loc[(lv, 10.0), 'P_trail_approx_TR_index'])} (low volatility) and {_pc(tr.loc[(lvx, 10.0), 'P_trail_price_index'])} to {_pc(tr.loc[(lvx, 10.0), 'P_trail_approx_TR_index'])} (low volatility with the exit).
* **The fixed trend exit trades return for protection, and the trade is not free.** It cuts the chance of a fall deeper than 30% over 10 years from {_pc(t.loc[(mom, 10.0), M_DD30])} to {_pc(t.loc[(mox, 10.0), M_DD30])} for momentum and from {_pc(t.loc[(lv, 10.0), M_DD30])} to {_pc(t.loc[(lvx, 10.0), M_DD30])} for low volatility, but lowers the median 10-year growth by {(dist.loc[(mom, 10.0), 'p50'] - dist.loc[(mox, 10.0), 'p50']) * 100:.1f} and {(dist.loc[(lv, 10.0), 'p50'] - dist.loc[(lvx, 10.0), 'p50']) * 100:.1f} points a year. Low volatility with the exit ends level with the approximate total-return index about half the time (it trails it in {_pc(tr.loc[(lvx, 10.0), 'P_trail_approx_TR_index'])} of 10-year paths).
* **The answer does not hinge on the resampling method.** Four generators (12-month blocks, random-length blocks, volatility regimes, single months) give the same ordering of portfolios and similar numbers (sensitivity table below).

## Two warnings that apply to everything below

1. **Survivor-biased stocks.** The 20 names are today's large, successful companies that were already listed in 1990. Companies that failed or shrank are missing. The package's survivorship score for this data is **{card.survivorship_score * 100:+.1f} percentage points a year**: an equal-weight basket of the 20 names beat the S&P 500 by that much. Resampling cannot remove this; every simulated path inherits it. Treat the portfolios' results as an upper bound on what picking from the full market would have delivered.
2. **A price index as the yardstick.** The `SP500` series leaves dividends out, while the portfolios use adjusted prices that include them. Against the plain index the portfolios look better than they should, by about the index's dividend yield. We therefore show every comparison twice: against the index as it is, and against **"SP500 + ~2% div (approx. TR)"**, which adds 2% a year to the index on every path. The 2% is a rough long-run dividend yield, not data.

The software prints the same two warnings on every result (`str(result)`), together with the note that bootstrap paths inherit the data's biases.

## What we did, in plain words

1. **The history.** Monthly closing prices of 20 stocks and the S&P 500 index from {card.start[:7]} to {card.end[:7]} (33 years, 395 monthly returns).
2. **Making 1,000 alternative futures.** We build each future by cutting the history into 12-month blocks (a "block bootstrap"), drawing blocks at random and gluing them together. Within a block all 20 stocks and the index keep their real joint moves, so crashes hit everything together, as they do in reality. Each future is 21 years long: the first year is a warm-up (the strategies need 12 months of history to rank stocks), and then 5, 10 or 20 years are measured. All portfolios and the index see exactly the same 1,000 futures, so the comparison is like for like.
3. **The portfolios.** Each month the portfolio holds the 5 stocks with the best 12-month-minus-1-month price momentum (**Momentum**) or the 5 least volatile over the past year (**Low volatility**), equal weight, rebalanced monthly, paying 25 basis points (0.25%) on every unit traded. "+ trend exit" adds the fixed rule written down in advance in `CALIBRATION_SP500.md`: leave for cash when the S&P 500 is weak against its own 10-month average (score threshold 0.8, hysteresis 0.1, weekly check, 10-day minimum hold, 5 basis points per switch) and return when the signal clears. The rule was never tuned on this data. Because the paths are monthly, the rule is checked once a month rather than weekly.
4. **The numbers.** For every future we measure terminal wealth from 100,000, compound annual growth (CAGR), the worst peak-to-trough fall and the time to recover from it, then read off percentiles across the 1,000 futures.
5. **Sensitivity.** We repeat the 10-year summary with three other ways of making futures: random-length blocks averaging 12 months (stationary bootstrap), blocks drawn from calm or stormy volatility periods (regime bootstrap), and single months drawn independently (no memory at all).

## Results

**Main table.** 12-month block bootstrap, 1,000 paths. "P(trail ...)" is the share of paths in which the portfolio ends the horizon behind the index on the same path. CAGR here is the portfolio's own growth with no deposits or withdrawals.

{main}

**Terminal wealth** from 100,000 (percentiles across paths):

{wealth}

**Falls and recovery over 10 years** (worst peak-to-trough fall on each path, month-end data):

{dd}

**Sensitivity to the way futures are generated** (10-year horizon, 1,000 paths each; the block-bootstrap rows are the main run, whose random draws differ from the other three, so differences of a few points are partly noise). Median 10-year CAGR of the price index: {", ".join(f"{g} {_pc(v, 1)}" for g, v in idx_med.items())}.

{sensm}

## Charts

![Fan chart of wealth over 20 years](1_fan_chart_20y.png)

![Distribution of 10-year CAGR](2_cagr_10y_distribution.png)

![Probability of trailing the passive index](3_probability_trailing_index.png)

![Probability of a deep fall within 10 years](4_drawdown_probability.png)

## What these numbers can and cannot support

**They can support:**
* The *width* of outcomes: even a portfolio that beats the index on average has bad paths (5th-percentile 10-year growth is only {_pc(dist.loc[(lvx, 10.0), 'p5'], 0)} to {_pc(dist.loc[(mom, 10.0), 'p5'], 0)} a year across the four portfolios), and a fall deeper than 30% in roughly one path in five (low volatility) to six in ten (momentum) over 10 years.
* The *relative* picture: low volatility has shallower falls and a narrower range than momentum; the trend exit shortens and shallows falls at the cost of return; and any claim of "beats the index" depends heavily on which index (price or total return) is used.
* The statement that, conditional on this history repeating its building blocks, a 10-year horizon is far from enough to be sure of anything about the index gap for the weaker portfolios.

**They cannot support:**
* A forecast. All futures are rearrangements of the same 33 years (395 monthly returns). The 1,000 paths look like a lot, but they carry no more information than the one history they are drawn from: a crisis that is not in 1990-2022, or a decade of a very different regime, cannot appear. Block bootstrap also cuts the history at block edges, so dependence longer than a year is lost.
* The size of any edge. Survivorship (+{card.survivorship_score * 100:.0f} points a year) is of the same size as the momentum "edge" over the index; the true edge from this kind of selection in the full market is likely much smaller, and may be zero or negative.
* Real-world costs and taxes beyond the 25 and 5 basis points modelled, the 2% dividend yield as a precise number, intra-month falls (month-end data understates them), or the exit's behaviour with daily/weekly timing.
* Probabilities to the percentage point. With 1,000 paths a probability near 50% has a sampling error of about 1.5 points, and the paths share much of their content, so the real uncertainty is larger.

## Reproduce

    python -m screenlab.research.montecarlo_sp500      # from the directory that contains screenlab/

Seed {SEED}, {N_PATHS} paths, month-end steps, {COST_BPS:.0f} bps one-way costs. Runtime under a minute. The operation behind it is `Lab.montecarlo` (see `docs/REFERENCE.md`).
"""
    Path(path).write_text(text)


# ----------------------------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    lab = sp500_lab(n=5)
    res = run(lab, BlockBootstrap(12), HORIZONS)
    print(res)
    tabs = summary_tables(res)
    sens = sensitivity(lab, res)
    for name, df in {**tabs, "sensitivity": sens}.items():
        df.to_csv(OUT / f"{name}.csv", index=False)
    res.fan.to_csv(OUT / "fan_quantiles.csv")
    _style()
    chart_fan(res, OUT / "1_fan_chart_20y.png")
    chart_cagr(tabs["per_path"], OUT / "2_cagr_10y_distribution.png")
    chart_trail(tabs["trail_table"], OUT / "3_probability_trailing_index.png")
    chart_drawdown(tabs["per_path"], OUT / "4_drawdown_probability.png")
    write_report(res, tabs, sens, OUT / "MONTECARLO_SP500.md")
    secs = time.time() - t0
    (OUT / "runtime_seconds.txt").write_text(f"{secs:.0f}\n")
    print(f"done in {secs:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
