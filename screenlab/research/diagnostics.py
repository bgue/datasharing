"""Phase 1.5 diagnostics on the synthetic market: make the existing studies honest.

    python -m screenlab.research.diagnostics            # all three parts, from the directory that contains screenlab/
    python -m screenlab.research.diagnostics 1 3        # only parts 1 and 3

Part 1  Realistic costs. The all-portfolio Monte Carlo (research/montecarlo_all.py: same seed 20241, same 1000
        BlockBootstrap(12) paths, same 25 portfolios) re-run under flat 25 bps, cap-tiered {0: 100, 1000: 50,
        10000: 25} bps, and a stressed tiering {0: 150, 1000: 75, 10000: 35} bps.
        `simulate_paths` has no per-path market-cap snapshot, so a callable `cap_tiered` is refused there; the tiers
        are applied PER TICKER instead, from each ticker's median market cap over the history (a dict {ticker: bps}).
Part 2  Timing luck on the Globe screens: `lab.timing` phase mode, eight Globe portfolios at their published rebalance
        (the seven monthly ones on daily data, stride 3 = intra-month shifts; bluechip on month-end data, all 12
        offsets), plus the seven monthly screens at a 12-month rebalance (month-end data, 12 offsets) for contrast.
Part 3  Basic Buyback check: turnover, Costs/yr and names replaced each month in a plain backtest, and Monte Carlo
        P(trail) with costs 25 bps and 0 bps, monthly and annual.

Outputs: research/diagnostics/ (CSVs are git-ignored; PNGs and DIAGNOSTICS.md are tracked). Progress is printed and
written to research/diagnostics/progress.txt.
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from screenlab import Lab, backtest, metrics
from screenlab.backtest import CapTiered
from screenlab.metrics import M
from screenlab.research import montecarlo_all as mca
from screenlab.research.montecarlo_sp500 import INK, INK2, SURFACE, GRID, _md, _pc, _style
from screenlab.screens import GLOBE_PORTFOLIOS, REGISTRY

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "diagnostics"

N_PATHS = 1000                       # same as the all-portfolio study; ~3-4 minutes per cost model
H = 10.0
TIERS = {"flat 25 bps": 25.0,
         "tiered": {0: 100, 1000: 50, 10000: 25},
         "stressed": {0: 150, 1000: 75, 10000: 35}}
MODELS = list(TIERS)
# one hue (blue), light -> dark = cheap -> expensive trading; second hue (orange) = the annual-rebalance version
SHADE = {"flat 25 bps": "#9CC9E6", "tiered": "#3E8CC4", "stressed": "#0B4F83"}
COL_PUB, COL_ALT = "#0072B2", "#D55E00"
MONTHLY = [n for n, p in GLOBE_PORTFOLIOS.items() if p.config_overrides.get("rebalance_months") == 1]
STRIDE_MONTHLY = 3
ANNUAL_OFFSETS = list(range(0, 252, 21))   # 12 offsets, one per month of the cycle
STRIDE_ANNUAL = 21                   # one offset per month of the 12-month cycle
T0 = time.time()


def log(msg):
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')} +{(time.time() - T0) / 60:4.1f}m] {msg}"
    print(line, flush=True)
    with open(OUT / "progress.txt", "a") as f:
        f.write(line + "\n")


def _foot(fig, text):
    fig.text(0.01, 0.006, text, fontsize=8.5, color=INK2)


def _short(p):
    return p.replace("_dividend", "_div")


# ================================================================================================= part 1
def cost_dict(lab, tiers) -> dict:
    """Per-ticker bps from market-cap tiers, using each ticker's median market cap over the history."""
    med = lab.market.dataset.fundamentals.groupby("ticker").market_cap.median()
    bps = CapTiered(tiers)(pd.DataFrame({"market_cap": med})).to_dict()
    bps["_default"] = float(CapTiered(tiers).tiers[0][1])
    return bps


def part1():
    log(f"PART 1: realistic costs, {N_PATHS} paths x {len(MODELS)} cost models")
    lab = Lab.synthetic()
    mca.N_PATHS = N_PATHS
    mca.log = lambda m: log("  " + m)
    out, mode, idx = {}, None, None
    for name, spec in MODELS_SPEC(lab).items():
        t = time.time()
        mca.COST_BPS = spec
        log(f"cost model '{name}'")
        pp, fan, tbl, mode, order, ra = mca.run_all(lab)
        idx = ra.index_label
        out[name] = mca.summary(pp, mode, idx)
        out[name].insert(0, "model", name)
        log(f"  '{name}' done in {time.time() - t:.0f}s")
    S = pd.concat(out.values(), ignore_index=True)
    S.to_csv(OUT / "costs_summary.csv", index=False)
    pd.Series(mode, name="selection_mode").to_csv(OUT / "costs_selection_mode.csv")
    # how many names fall in each tier (per-ticker median cap)
    med = lab.market.dataset.fundamentals.groupby("ticker").market_cap.median()
    cut = pd.cut(med, [-np.inf, 1000, 10000, np.inf], labels=["< $1B", "$1B-10B", ">= $10B"]).value_counts()
    cut.to_csv(OUT / "cost_tier_counts.csv")
    return S, mode, cut


def MODELS_SPEC(lab):
    return {"flat 25 bps": 25.0, "tiered": cost_dict(lab, TIERS["tiered"]), "stressed": cost_dict(lab, TIERS["stressed"])}


def costs_table(S):
    p = S[S.horizon_years == H].pivot(index="portfolio", columns="model")
    rows = []
    for port in p.index:
        r = {"portfolio": port}
        for m in MODELS:
            r[f"cagr {m}"] = p.loc[port, ("median_cagr", m)]
            r[f"P(trail) {m}"] = p.loc[port, ("P_trail", m)]
            r[f"excess {m}"] = p.loc[port, ("median_excess", m)]
        r["drag tiered"] = r["cagr flat 25 bps"] - r["cagr tiered"]
        r["drag stressed"] = r["cagr flat 25 bps"] - r["cagr stressed"]
        rows.append(r)
    T = pd.DataFrame(rows).sort_values("P(trail) flat 25 bps").reset_index(drop=True)
    T["flip tiered"] = (T["P(trail) flat 25 bps"] <= .5) & (T["P(trail) tiered"] > .5)
    T["flip stressed"] = (T["P(trail) flat 25 bps"] <= .5) & (T["P(trail) stressed"] > .5)
    T.to_csv(OUT / "costs_table.csv", index=False)
    return T


def chart_costs(T, path):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    globe = set(MONTHLY) | {"bluechip_dividend_growth"}
    names = [p for p in T.portfolio if p.replace(" (monthly)", "").replace(" (annual)", "") in globe]
    mon = sorted([p for p in names if p.endswith("(monthly)")], key=lambda p: T.set_index("portfolio").loc[p, "P(trail) flat 25 bps"])
    ann = sorted([p for p in names if not p.endswith("(monthly)")], key=lambda p: T.set_index("portfolio").loc[p, "P(trail) flat 25 bps"])
    rows = [("MONTHLY (published rebalance)", None)] + [(p, p) for p in mon] + [("", None), ("ANNUAL (12 months)", None)] + [(p, p) for p in ann]
    t = T.set_index("portfolio")
    fig, ax = plt.subplots(figsize=(12, 8))
    h = 0.26
    yl, ylab = [], []
    for i, (lab_, p) in enumerate(rows):
        y = len(rows) - 1 - i
        if p is None:
            if lab_:
                ax.text(0.5, y, lab_, fontsize=10, fontweight="bold", color=INK, va="center")
            continue
        for k, m in enumerate(MODELS):
            v = t.loc[p, f"P(trail) {m}"] * 100
            ax.barh(y + (1 - k) * h, v, height=h * 0.92, color=SHADE[m])
            ax.text(v + 0.8, y + (1 - k) * h, f"{v:.0f}%", va="center", fontsize=7.5, color=INK2)
        yl.append(y)
        ylab.append(_short(p).replace(" (monthly)", "").replace(" (annual)", ""))
    ax.axvline(50, color=INK2, lw=1, ls=(0, (3, 3)))
    ax.set_yticks(yl, ylab, fontsize=9)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Share of simulated 10-year futures where the portfolio ends behind the passive index (%)")
    ax.grid(axis="y", visible=False)
    mon_flat = sum(t.loc[p, "P(trail) flat 25 bps"] > .5 for p in mon)
    mon_tier = sum(t.loc[p, "P(trail) tiered"] > .5 for p in mon)
    mon_str = sum(t.loc[p, "P(trail) stressed"] > .5 for p in mon)
    ax.set_title(f"Realistic costs: {mon_tier} of {len(mon)} monthly Globe portfolios trail the index in most futures "
                 f"(flat 25 bps: {mon_flat}; stressed: {mon_str})", loc="left", fontsize=12.5)
    ax.legend(handles=[Patch(color=SHADE[m], label={"flat 25 bps": "flat 25 bps (reference)",
                                                   "tiered": "cap-tiered 100 / 50 / 25 bps",
                                                   "stressed": "stressed 150 / 75 / 35 bps"}[m]) for m in MODELS],
              loc="lower right", fontsize=9)
    _foot(fig, "1000 block-bootstrap paths, synthetic data, same paths in every bar. Cost tiers by market cap: < $1B / $1B-10B / >= $10B, "
               "applied per ticker (its median cap). Dashed line: 50%. Lower = better.")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    return mon_flat, mon_tier, mon_str, len(mon)


# ================================================================================================= part 2
def _timing_one(lab, name, rebalance_months, stride, offsets=None, **cfg):
    t = time.time()
    tr = lab.timing(name, stride=stride, offsets=offsets, rebalance_months=rebalance_months, **cfg)
    d0 = tr.curves.index[0]
    bench = lab.passive().curve(lab.market, lab.config, d0, None)
    cg = tr.table[M.CAGR]
    tb = metrics.summarize(tr.tranche, bench)
    ib = metrics.summarize(bench, None)
    log(f"  {name} @{rebalance_months}m: {len(tr.table)} offsets in {time.time() - t:.0f}s, CAGR {cg.min():.2%}..{cg.max():.2%}")
    return dict(screen=name, rebalance=f"{rebalance_months}m", n_offsets=len(tr.table), cagr_min=cg.min(),
                cagr_median=cg.median(), cagr_max=cg.max(), cagr_range=cg.max() - cg.min(),
                tranche_cagr=tb[M.CAGR], index_cagr=ib[M.CAGR], tranche_sharpe=tb[M.SHARPE])


def part2():
    log("PART 2: timing luck on the Globe screens")
    rows = []
    daily = Lab.synthetic()
    log(f"monthly-rebalance Globe portfolios, daily data, stride {STRIDE_MONTHLY} (intra-month shifts)")
    for n in MONTHLY:
        r = _timing_one(daily, n, 1, STRIDE_MONTHLY)
        r["kind"] = "published"
        rows.append(r)
    log("bluechip_dividend_growth (published 12 months, hold rule), daily data, stride 21 = 12 monthly offsets")
    hold = GLOBE_PORTFOLIOS["bluechip_dividend_growth"].config_overrides["hold_rule"]
    r = _timing_one(daily, "bluechip_dividend_growth", 12, STRIDE_ANNUAL, offsets=ANNUAL_OFFSETS, hold_rule=hold)
    r["kind"] = "published"
    rows.append(r)
    log("the seven monthly screens at a 12-month rebalance, daily data, stride 21 = 12 monthly offsets")
    for n in MONTHLY:
        r = _timing_one(daily, n, 12, STRIDE_ANNUAL, offsets=ANNUAL_OFFSETS)
        r["kind"] = "annual version"
        rows.append(r)
    T = pd.DataFrame(rows)
    T["spans_index"] = (T.cagr_min < T.index_cagr) & (T.cagr_max > T.index_cagr)
    T.to_csv(OUT / "timing_table.csv", index=False)
    return T


def chart_timing(T, path):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    pub = T[T.kind == "published"].sort_values("cagr_range", ascending=False)
    alt = T[T.kind == "annual version"].set_index("screen")
    fig, ax = plt.subplots(figsize=(12, 7.4))
    n = len(pub)
    for i, (_, r) in enumerate(pub.iterrows()):
        y = n - 1 - i
        ax.plot([r.cagr_min * 100, r.cagr_max * 100], [y + .13, y + .13], color=COL_PUB, lw=7, solid_capstyle="butt", alpha=.8)
        ax.plot(r.cagr_median * 100, y + .13, "o", color="white", mec=COL_PUB, ms=6, zorder=3)
        ax.plot(r.tranche_cagr * 100, y + .13, "|", color=INK, ms=14, mew=2, zorder=4)
        ax.text(r.cagr_max * 100 + .12, y + .13, f"{r.cagr_range * 100:.1f} pts", va="center", fontsize=8.5, color=INK2)
        if r.screen in alt.index:
            a = alt.loc[r.screen]
            ax.plot([a.cagr_min * 100, a.cagr_max * 100], [y - .17, y - .17], color=COL_ALT, lw=7, solid_capstyle="butt", alpha=.8)
            ax.plot(a.cagr_median * 100, y - .17, "o", color="white", mec=COL_ALT, ms=6, zorder=3)
            ax.plot(a.tranche_cagr * 100, y - .17, "|", color=INK, ms=14, mew=2, zorder=4)
            ax.text(a.cagr_max * 100 + .12, y - .17, f"{a.cagr_range * 100:.1f} pts", va="center", fontsize=8.5, color=INK2)
    ax.axvline(pub.index_cagr.iloc[0] * 100, color="#6B6B6B", lw=1.2, ls=(0, (3, 3)))
    ax.text(pub.index_cagr.iloc[0] * 100 + .08, -.62, "passive index", fontsize=8.5, color="#6B6B6B", ha="left")
    ax.set_ylim(-.8, n - .4)
    ax.set_yticks(range(n), [_short(s) + (" (12m)" if rb == "12m" else " (monthly)") for s, rb in zip(pub.screen[::-1], pub.rebalance[::-1])], fontsize=9)
    ax.set_xlabel("CAGR (% per year) across the rebalance-date variants, same start and end day")
    ax.grid(axis="y", visible=False)
    worst = pub.iloc[0]
    ax.set_title(f"Rebalance-date luck moves CAGR by up to {pub.cagr_range.max() * 100:.1f} points a year "
                 f"({_short(worst.screen)}); the median portfolio by {pub.cagr_range.median() * 100:.1f}", loc="left", fontsize=12.5)
    ax.legend(handles=[Line2D([], [], color=COL_PUB, lw=7, label="published rebalance: min-max"),
                       Line2D([], [], color=COL_ALT, lw=7, label="same screen at 12 months: min-max"),
                       Line2D([], [], marker="o", ls="", mfc="white", mec=INK, label="median variant"),
                       Line2D([], [], marker="|", ls="", color=INK, ms=12, mew=2, label="tranche blend (all variants averaged)")],
              loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=4, fontsize=9)
    _foot(fig, "Synthetic market, 25 bps. Monthly portfolios: rebalance day shifted 0-20 trading days (every 3rd day, 7 variants); "
               "12-month rebalance: anniversary shifted through 12 months.")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


# ================================================================================================= part 3
def _bt_stats(lab, name, months, cost):
    cfg = lab._cfg(rebalance_months=months, cost_bps=cost)
    res = backtest.run(lab.screen(name), lab.market, cfg)
    d0, d1 = res.curve.index[0], res.curve.index[-1]
    yrs = (d1 - d0).days / 365.25
    bench = lab.passive().curve(lab.market, cfg, d0, d1)
    s, b = metrics.summarize(res.curve, bench), metrics.summarize(bench, None)
    hs = list(res.holdings.values())
    repl = [1 - len(set(a) & set(b_)) / max(len(a), 1) for a, b_ in zip(hs[:-1], hs[1:]) if len(a)]
    return dict(screen=name, rebalance=f"{months}m", cost_bps=cost, cagr=s[M.CAGR], index_cagr=b[M.CAGR],
                turnover=res.turnover.iloc[1:].mean(), costs_yr=res.costs.sum() / yrs, replaced=float(np.mean(repl)),
                n_holdings=float(np.mean([len(h) for h in hs])))


def part3():
    log("PART 3: Basic Buyback check")
    lab = Lab.synthetic()
    rows = []
    for name in ["basic_buyback", *[m for m in MONTHLY if m != "basic_buyback"]]:
        for months in (1, 12):
            rows.append(_bt_stats(lab, name, months, 25.0))
            log(f"  backtest {name} @{months}m: turnover {rows[-1]['turnover']:.1%}, replaced {rows[-1]['replaced']:.0%}, costs/yr {rows[-1]['costs_yr']:.2%}")
    for months in (1, 12):
        rows.append(_bt_stats(lab, "basic_buyback", months, 0.0))
    B = pd.DataFrame(rows)
    B.to_csv(OUT / "buyback_backtest.csv", index=False)
    mc = []
    for months in (1, 12):
        for cost in (25.0, 0.0):
            r = lab.montecarlo(["basic_buyback"], rebalance_months=months, generator=mca.BlockBootstrap(12), n_paths=N_PATHS,
                               horizon_years=mca.HORIZONS, start_wealth=mca.START_WEALTH, seed=mca.SEED, cost_bps=cost, selection="auto")
            idx = r.index_label
            g = r.per_path[(r.per_path.portfolio == "basic_buyback") & (r.per_path.horizon_years == H)]
            gi = r.per_path[(r.per_path.portfolio == idx) & (r.per_path.horizon_years == H)]
            mc.append(dict(rebalance=f"{months}m", cost_bps=cost, median_cagr=g.cagr.median(), index_median_cagr=gi.cagr.median(),
                           P_trail=float((g.cagr < g.index_cagr).mean()), median_excess=g.excess_cagr.median()))
            log(f"  MC basic_buyback @{months}m cost {cost}: P(trail) {mc[-1]['P_trail']:.1%}")
    MC = pd.DataFrame(mc)
    MC.to_csv(OUT / "buyback_montecarlo.csv", index=False)
    return B, MC


# ================================================================================================= report
def write_report(S, T1, cut, flips, T2, B, MC, secs):
    t = T1.set_index("portfolio")
    pc = lambda x: _pc(x, 0)
    # ---- part 1 table
    show = T1.copy()
    rows = []
    for _, r in show.iterrows():
        flag = ("flips (tiered)" if r["flip tiered"] else "") if not r["flip stressed"] or r["flip tiered"] else "flips (stressed)"
        rows.append({"Portfolio": r.portfolio,
                     "CAGR flat": _pc(r["cagr flat 25 bps"], 1), "CAGR tiered": _pc(r["cagr tiered"], 1), "CAGR stressed": _pc(r["cagr stressed"], 1),
                     "P(trail) flat": pc(r["P(trail) flat 25 bps"]), "P(trail) tiered": pc(r["P(trail) tiered"]),
                     "P(trail) stressed": pc(r["P(trail) stressed"]),
                     "Extra drag tiered / stressed (pts/yr)": f"{r['drag tiered'] * 100:.2f} / {r['drag stressed'] * 100:.2f}",
                     "Flips to trailing": flag})
    tab1 = _md(pd.DataFrame(rows))
    flipT = list(T1[T1["flip tiered"]].portfolio)
    flipS = list(T1[T1["flip stressed"] & ~T1["flip tiered"]].portfolio)
    mon = T1[T1.portfolio.str.endswith("(monthly)")]
    ann_g = T1[T1.portfolio.str.endswith("(annual)") | (T1.portfolio == "bluechip_dividend_growth")]
    mon_drag = mon["drag tiered"].mean() * 100
    ann_drag = ann_g["drag tiered"].mean() * 100
    fl = lambda xs: ", ".join(f"`{x}`" for x in xs) if xs else "none"
    mflat, mtier, mstr, nmon = flips
    # ---- part 2 table
    pub = T2[T2.kind == "published"]
    rows2 = []
    for _, r in T2.iterrows():
        rows2.append({"Screen": _short(r.screen), "Rebalance": r.rebalance + (" (published)" if r.kind == "published" else " (annual version)"),
                      "Variants": int(r.n_offsets), "CAGR min": _pc(r.cagr_min, 1), "CAGR median": _pc(r.cagr_median, 1),
                      "CAGR max": _pc(r.cagr_max, 1), "Range (pts)": f"{r.cagr_range * 100:.1f}", "Tranche blend": _pc(r.tranche_cagr, 1),
                      "Index": _pc(r.index_cagr, 1), "Luck alone flips beat/trail the index": "yes" if r.spans_index else "no"})
    tab2 = _md(pd.DataFrame(rows2))
    # ---- part 3 table
    bb = B[B.screen == "basic_buyback"]
    peers = B[(B.screen != "basic_buyback") & (B.cost_bps == 25.0)]
    def mcv(m, c, col="P_trail"):
        return MC[(MC.rebalance == m) & (MC.cost_bps == c)][col].iloc[0]
    rows3 = []
    for m in ("1m", "12m"):
        b = bb[(bb.rebalance == m) & (bb.cost_bps == 25.0)].iloc[0]
        b0 = bb[(bb.rebalance == m) & (bb.cost_bps == 0.0)].iloc[0]
        pr = peers[peers.rebalance == m]
        rows3.append({"basic_buyback": "monthly" if m == "1m" else "annual",
                      "Turnover per rebalance": _pc(b.turnover, 0), "Names replaced per rebalance": _pc(b.replaced, 0),
                      "Costs/yr (25 bps)": _pc(b.costs_yr, 2), "CAGR 25 bps": _pc(b.cagr, 1), "CAGR 0 bps": _pc(b0.cagr, 1),
                      "Index CAGR": _pc(b.index_cagr, 1),
                      "P(trail) 10y, 25 bps": pc(mcv(m, 25.0)), "P(trail) 10y, 0 bps": pc(mcv(m, 0.0)),
                      "Peer monthly screens: turnover / replaced / Costs/yr": f"{pr.turnover.median():.0%} / {pr.replaced.median():.0%} / {pr.costs_yr.median():.2%}"})
    tab3 = _md(pd.DataFrame(rows3))
    bm = bb[(bb.rebalance == "1m") & (bb.cost_bps == 25.0)].iloc[0]
    p25, p0 = mcv("1m", 25.0), mcv("1m", 0.0)
    pa25, pa0 = mcv("12m", 25.0), mcv("12m", 0.0)
    pt_peer = peers[peers.rebalance == "1m"]
    g25 = bb[(bb.rebalance == "1m") & (bb.cost_bps == 25.0)].iloc[0]
    g0 = bb[(bb.rebalance == "1m") & (bb.cost_bps == 0.0)].iloc[0]
    if p0 > 0.5:
        verdict = (f"**Mostly a return effect in the simulated futures, with costs as the lever that makes it near-certain:** at zero cost basic_buyback (monthly) still trails the index in "
                   f"{pc(p0)} of 10-year futures (annual: {pc(pa0)}), costs lift that to {pc(p25)}, and in the single history the {_pc(bm.costs_yr, 1)} a year of costs turn a gross "
                   f"{_pc(g0.cagr, 1)} against the index's {_pc(g0.index_cagr, 1)} into a net {_pc(g25.cagr, 1)}; but its turnover ({_pc(bm.turnover, 0)} a month, "
                   f"{_pc(bm.replaced, 0)} of names replaced) is in line with the other monthly screens ({pt_peer.turnover.median():.0%}), so buyback yield is not reshuffling the portfolio more than its peers do.")
    else:
        verdict = (f"**A cost artefact:** with costs set to zero basic_buyback (monthly) trails in only {pc(p0)} of 10-year futures (against "
                   f"{pc(p25)} at 25 bps); the buyback ranking replaces {_pc(bm.replaced, 0)} of the portfolio each month "
                   f"({_pc(bm.turnover, 0)} one-way turnover, {_pc(bm.costs_yr, 1)} a year in costs).")
    # ---- part 2 prose
    mp, bp = pub[pub.rebalance == "1m"], pub[pub.rebalance == "12m"]
    alt = T2[T2.kind == "annual version"]
    n_span = int(pub.spans_index.sum())
    span_names = ", ".join(f"`{_short(x)}`" for x in pub[pub.spans_index].screen)
    tr_in = int(((pub.tranche_cagr >= pub.cagr_min) & (pub.tranche_cagr <= pub.cagr_max)).sum())
    lines2 = (f"* **Monthly portfolios: {mp.cagr_range.min() * 100:.1f} to {mp.cagr_range.max() * 100:.1f} points of CAGR** between the best and worst rebalance day "
              f"(median {mp.cagr_range.median() * 100:.1f}), from shifting the rebalance day within the month alone.\n"
              f"* **Annual rebalance: {alt.cagr_range.min() * 100:.1f} to {alt.cagr_range.max() * 100:.1f} points** for the seven 12-month versions "
              f"(median {alt.cagr_range.median() * 100:.1f}) and **{bp.cagr_range.iloc[0] * 100:.1f} points for `bluechip_dividend_growth`** "
              f"({_pc(bp.cagr_min.iloc[0], 1)} to {_pc(bp.cagr_max.iloc[0], 1)}). A 12-month rebalance has roughly twice the date-luck of a monthly one: "
              f"it commits the whole portfolio to one set of picks for a year.\n"
              f"* **All of that range is start-date luck**: same rules, same window, same costs, only the day of the rebalance differs. "
              f"For the {n_span} of {len(pub)} published portfolios whose min-max range straddles the index ({span_names or 'none'}), luck alone decides whether the portfolio beats or trails the index.\n"
              f"* The tranche blend (capital split across every rebalance date) sits inside the min-max range for {tr_in} of {len(pub)} published portfolios, close to the median variant, "
              f"and removes the choice: judge a screen by the blend, or split a deposit across months.")
    text = f"""# Phase 1.5 diagnostics: what realistic costs, rebalance-date luck and one suspicious number change

*Script: `research/diagnostics.py` (about 14 minutes in all: part 1 ~9, part 2 ~3, part 3 ~1; seed {mca.SEED}, {N_PATHS} block-bootstrap paths where Monte Carlo is used). Tables: `research/diagnostics/*.csv` (regenerated, not tracked in git).*

**Read this first.** Everything here runs on the **synthetic market** (250 generated stocks, 25 years, a cap-weighted total-return index). It tests
the machinery and the arithmetic of trading costs and timing, not the real-world edge of any screen. A screen that beats or trails the index here
says nothing about what it does on real data; the *size of the cost drag* and the *size of the timing spread* are the transferable lessons.

## 1. Realistic costs: which monthly Globe portfolios survive real trading?

The all-portfolio Monte Carlo (same seed, same {N_PATHS} paths, same 25 portfolios) re-run with trading cost depending on company size:
**tiered** = 100 bps below $1B market cap, 50 bps from $1B, 25 bps from $10B; **stressed** = 150 / 75 / 35 bps. The reference is the original flat 25 bps.
Of the 250 names, {int(cut.get('< $1B', 0))} are under $1B, {int(cut.get('$1B-10B', 0))} between $1B and $10B and {int(cut.get('>= $10B', 0))} above $10B
(by each name's median market cap over the history). Table: 10-year horizon; CAGR is the median over futures; P(trail) = share of futures
ending behind the index; "extra drag" = median CAGR at flat 25 bps minus median CAGR under the model.

{tab1}

![P(trail) at 10 years under three cost models](1_costs_p_trail.png)

**Takeaway.** Under the tiered costs {mtier} of {nmon} monthly Globe portfolios trail the index in most 10-year futures (flat 25 bps: {mflat}; stressed: {mstr}).
Tiered costs take on average {mon_drag:.2f} points a year off a monthly portfolio's median CAGR (against {ann_drag:.2f} for the annual ones),
because monthly rebalancing pays the small-cap tier twelve times as often. **Flip from beating to trailing (P(trail) crosses 50%):** tiered: {fl(flipT)};
additionally under stressed: {fl(flipS)}.

*Method note.* `simulate_paths` carries no per-path market-cap snapshot, so a callable `cap_tiered` cannot run inside Monte Carlo (it raises `NotImplementedError`).
Here the same tiers are applied **per ticker** from its median market cap over the history. A stock that grows or shrinks across a tier boundary keeps its tier, so this
is an approximation of the time-varying tiering that a plain backtest applies.

## 2. Timing luck on the Globe screens

`lab.timing` phase mode: every variant forms its portfolio on the same day and ends on the same day, but its rebalance anniversary is shifted, so the spread of
CAGR across variants is pure rebalance-date luck. All runs use the daily synthetic market (25 bps flat; the monthly `Lab.synthetic(freq="ME")` market is a different random draw, so it was not mixed in).
The seven monthly portfolios shift the rebalance day within the month by 0-20 trading days (every {STRIDE_MONTHLY}rd day: {int(mp.n_offsets.iloc[0])} variants);
`bluechip_dividend_growth` (12-month, with its hold rule) and the 12-month versions of the seven screens shift the anniversary through every month of the year
(every {STRIDE_ANNUAL} trading days: 12 variants). "Luck alone flips" = the min-max range straddles the index's CAGR over the same window.

{tab2}

![Spread of CAGR across rebalance dates](2_timing_luck.png)

**Takeaway.**

{lines2}

## 3. Basic Buyback check: cost artefact or return effect?

`basic_buyback` (20 names with the highest buyback yield) trails the index in 92% of 10-year futures when rebalanced monthly at 25 bps. Plain backtest on the daily synthetic
history (25 bps unless stated) and Monte Carlo P(trail) at 10 years with costs 25 and 0 bps:

{tab3}

{verdict}

## What this does and does not show

* Synthetic market only: the machinery and the cost and timing mechanics are exercised, but no real-world edge is measured. Cost tiers are applied per ticker in Monte Carlo (see above).
* Probabilities carry about 1.5 points of sampling error at {N_PATHS} paths; paths share content.
* Timing spreads are for one synthetic history (one window), not a distribution across histories.

## Reproduce

    python -m screenlab.research.diagnostics          # from the directory that contains screenlab/
"""
    (OUT / "DIAGNOSTICS.md").write_text(text)


def main(argv=None):
    global T0
    T0 = time.time()
    parts = {int(a) for a in (argv if argv is not None else sys.argv[1:])} or {1, 2, 3}
    OUT.mkdir(parents=True, exist_ok=True)
    _style()
    if 1 in parts:
        S, mode, cut = part1()
        T1 = costs_table(S)
        flips = chart_costs(T1, OUT / "1_costs_p_trail.png")
        cut.to_csv(OUT / "cost_tier_counts.csv")
    else:
        S = pd.read_csv(OUT / "costs_summary.csv"); T1 = pd.read_csv(OUT / "costs_table.csv")
        cut = pd.read_csv(OUT / "cost_tier_counts.csv", index_col=0).iloc[:, 0]
        flips = chart_costs(T1, OUT / "1_costs_p_trail.png")
    if 2 in parts:
        T2 = part2()
    else:
        T2 = pd.read_csv(OUT / "timing_table.csv")
    chart_timing(T2, OUT / "2_timing_luck.png")
    if 3 in parts:
        B, MC = part3()
    else:
        B = pd.read_csv(OUT / "buyback_backtest.csv"); MC = pd.read_csv(OUT / "buyback_montecarlo.csv")
    write_report(S, T1, cut, flips, T2, B, MC, time.time() - T0)
    log(f"done in {time.time() - T0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
