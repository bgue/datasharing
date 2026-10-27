"""Walk-forward calibration study of exit screens + exit policies on the bundled real S&P 500 data.

    python -m rothery_screener.research.calibrate_sp500          # from the directory that contains rothery_screener/

What it does (see CALIBRATION_SP500.md for the write-up):
  * Entry screens momentum_12_1 and low_vol (n=5) are rebalanced MONTHLY; the exit clock is DAILY
    (or weekly, a grid dimension) and independent of that. The passive S&P 500 index is the benchmark.
  * Four candidate exit screens are calibrated with `calibrate_exit` walk-forward on three targets:
    the SP500 index itself and the two monthly portfolio curves, under two splits (8y train / 4y test and
    10y / 3y) and two objectives (Calmar, Sharpe), with max 4 switches per year.
  * The policy picked on each TRAIN window is traded on the NEXT (test) window; the test windows are
    stitched into one out-of-sample backtest and compared with no-exit and with passive SP500.
  * Block-bootstrap / Newey-West tests on the OOS exit-vs-no-exit difference; DSR and trial counts.
  * A causality check (signals/exposure recomputed on truncated data must equal the full-data values).

Trimmed: the full grid has 768 points; each calibration uses the same fixed random sample of N_POINTS=256
(seed 0, `sweep.random_search`) to keep the total run under ~15 minutes. Calibrations run in parallel processes.
Outputs go to research/results/*.csv.
"""
from __future__ import annotations

import sys
import time
import warnings
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd

from rothery_screener import exit_inputs  # noqa: F401  (registers the extra signals)
from rothery_screener import metrics, stats
from rothery_screener.backtest import schedule
from rothery_screener.calibration import _policy_for, _screen_for, calibrate_exit
from rothery_screener.exit_inputs import CorrelationSpike, DistanceFromHigh, NewLowsMinusHighs
from rothery_screener.exit_policy import policy_overlay
from rothery_screener.exit_signals import Breadth, ExitScreen, TrendBreak, VolRegime
from rothery_screener.realdata.bundled import sp500_lab
from rothery_screener.sweep import random_search, walk_forward

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "results"

GRID = dict(threshold=[0.6, 0.7, 0.8, 0.9], hysteresis=[0.05, 0.1, 0.2], check=["D", "W-FRI"],
            min_hold_days=[0, 10], exposure_when_flagged=[0.0, 0.5], reentry=["signal", "next_rebalance"],
            reentry_delay_days=[0, 10], reentry_steps=[1, 2])
GRID_SIZE = int(np.prod([len(v) for v in GRID.values()]))      # 768
N_POINTS = 256
SEED = 0
CONSTRAINTS = dict(max_switches_per_year=4)
COST_BPS = 5.0
PARK_RATE = 0.025      # sensitivity: parked money earns 2.5%/yr instead of 0% (average T-bill rate 1990-2022 was ~2.5%)
SPLITS = {"8/4": dict(train_years=8, test_years=4), "10/3": dict(train_years=10, test_years=3)}
OBJECTIVES = ["Calmar", "Sharpe"]
TARGETS = ["SP500", "momentum_12_1", "low_vol"]
# A fixed, uncalibrated reference policy: what a user would pick without any fitting.
DEFAULT_POINT = dict(threshold=0.8, hysteresis=0.1, check="W-FRI", min_hold_days=10, exposure_when_flagged=0.0,
                     reentry="signal", reentry_delay_days=0, reentry_steps=1)


def make_screens() -> dict:
    return {
        "trend": ExitScreen([TrendBreak("SP500")]),
        "trend_vol": ExitScreen([TrendBreak("SP500"), VolRegime("SP500")]),
        "trend_breadth_vol": ExitScreen([TrendBreak("SP500"), Breadth(), VolRegime("SP500")]),
        "broad": ExitScreen([TrendBreak("SP500"), Breadth(), VolRegime("SP500"), DistanceFromHigh("SP500"),
                             CorrelationSpike(), NewLowsMinusHighs()]),
    }


# ---------------------------------------------------------------------------- shared state (inherited by fork)
LAB = MKT = None
CURVES: dict = {}
REB: pd.DatetimeIndex | None = None
POINTS: list = []


def setup():
    global LAB, MKT, CURVES, REB, POINTS
    LAB = sp500_lab(n=5)
    MKT = LAB.market
    rep = LAB.backtest(["momentum_12_1", "low_vol"], rebalance_months=1, start="1991-02-01")
    CURVES = {"momentum_12_1": rep.results["momentum_12_1"].curve.astype(float),
              "low_vol": rep.results["low_vol"].curve.astype(float),
              "SP500": MKT.series("SP500").dropna().astype(float)}
    REB = pd.DatetimeIndex(schedule(MKT, CURVES["momentum_12_1"].index[0], 1))
    POINTS = random_search(GRID, N_POINTS, seed=SEED)


def _obj(curve: pd.Series, name: str) -> float:
    if len(curve) < 3:
        return np.nan
    s = metrics.summarize(curve)
    return s["CAGR"] / max(abs(s["MaxDD"]), 1e-9) if name == "Calmar" else s["Sharpe"]


def _run_policy(scr, curve, point, park="cash"):
    s, expo = _screen_for(scr, point)
    pol = _policy_for(None, s, point, expo, COST_BPS, None)
    if park != "cash":
        pol = replace(pol, park=park)
    reb = schedule(MKT, curve.index[0], 1)
    return policy_overlay(curve, pol, MKT, reb, 0.0)


def worker(task):
    skey, target, splitkey, obj = task
    t0 = time.time()
    scr = make_screens()[skey]
    curve = CURVES[target]
    splits = SPLITS[splitkey]
    res = calibrate_exit(scr, MKT, curve, POINTS, obj, splits=splits, constraints=CONSTRAINTS,
                         cost_bps=COST_BPS, rebalance_months=1)
    wins = walk_forward(curve.index, **splits)
    base_oos = [_obj(curve.loc[b:c], obj) for (_, b, c) in wins]
    tbl = res.table.copy()
    tbl.attrs = {}
    tbl["BaseOOS"] = float(np.nanmean(base_oos))
    # stitch: policy picked on each train window, traded over its test window
    cache, dcache, parts = {}, None, []
    wf = res.windows
    pcols = list(GRID)
    dov = _run_policy(scr, curve, DEFAULT_POINT)
    dcash = _run_policy(scr, curve, DEFAULT_POINT, park=PARK_RATE)
    prev_te = None
    for _, w in wf.iterrows():
        pt = {k: w[k] for k in pcols}
        key = tuple(pt.items())
        if key not in cache:
            cache[key] = _run_policy(scr, curve, pt)
        ov = cache[key]
        b, c = w["train_end"], w["test_end"]
        sel = (curve.index > b) & (curve.index <= c)
        d = curve.index[sel]
        part = pd.DataFrame({
            "exit": ov.curve.pct_change().reindex(d).to_numpy(),
            "noexit": curve.pct_change().reindex(d).to_numpy(),
            "default": dov.curve.pct_change().reindex(d).to_numpy(),
            "default_cash": dcash.curve.pct_change().reindex(d).to_numpy(),
            "expo": ov.exposure.reindex(d).to_numpy(), "expo_default": dov.exposure.reindex(d).to_numpy(),
            "base_date": b}, index=d)
        parts.append(part)
    oos = pd.concat(parts)
    wf = wf.copy()
    wf["base_oos"] = [_obj(curve.loc[b:c], obj) for (_, b, c) in
                      [(r.train_start, r.train_end, r.test_end) for r in wf.itertuples()]]
    meta = dict(screen=skey, target=target, split=splitkey, objective=obj, secs=time.time() - t0)
    return task, meta, tbl, wf, res.summary, res.best, oos


# ---------------------------------------------------------------------------- reporting helpers
def chain(r: pd.Series, start) -> pd.Series:
    c = (1 + r.fillna(0.0)).cumprod()
    return pd.concat([pd.Series([1.0], index=[start]), c])


def perf(curve: pd.Series, expo: pd.Series | None = None) -> dict:
    s = metrics.summarize(curve)
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    out = {k: s[k] for k in ("CAGR", "Vol", "Sharpe", "MaxDD", "Worst12m")}
    if expo is None:
        out.update(TimeOut=0.0, SwitchesPerYr=0.0)
    else:
        e = expo.to_numpy()
        out.update(TimeOut=float((e < 1 - 1e-9).mean()), SwitchesPerYr=float((np.abs(np.diff(e)) > 1e-9).sum() / yrs))
    return out


def causality_check(scr_key="trend_breadth_vol", cuts=("2000-06-30", "2008-12-31", "2015-03-02")) -> pd.DataFrame:
    """Recompute flag and exposure on data truncated at `cut`; they must equal the full-data values up to `cut`."""
    from rothery_screener.data import Market
    scr = make_screens()[scr_key]
    full_state = scr.state(MKT)
    s, expo = _screen_for(scr, DEFAULT_POINT)
    pol = _policy_for(None, s, DEFAULT_POINT, expo, COST_BPS, None)
    full_exp = pol.exposure(MKT, REB, state=s.state(MKT))
    rows = []
    for cut in cuts:
        cut = pd.Timestamp(cut)
        m2 = Market(MKT.fund, MKT.prices.loc[:cut], MKT.indices.loc[:cut])
        st2 = scr.state(m2)
        pol2 = _policy_for(None, s, DEFAULT_POINT, expo, COST_BPS, None)
        ex2 = pol2.exposure(m2, REB[REB <= cut], state=s.state(m2))
        a = full_state.loc[:cut]
        rows.append(dict(cut=cut.date(), days=len(a), state_mismatch=int((a != st2.reindex(a.index)).sum()),
                         exposure_mismatch=int((full_exp.loc[:cut] - ex2).abs().gt(1e-12).sum())))
    return pd.DataFrame(rows)


def stop_reference() -> pd.DataFrame:
    """Uncalibrated reference: PortfolioStop (flag when the portfolio is 15% below its peak, clear at 7.5%).
    calibrate_exit cannot tune it (it never hands the portfolio curve to the signals), so it is only run at fixed settings."""
    from rothery_screener.exit_inputs import PortfolioStop
    from rothery_screener.exit_policy import ExitPolicy
    rows = []
    for tgt in ("momentum_12_1", "low_vol"):
        cur = CURVES[tgt]
        for dd in (0.10, 0.15, 0.20):
            pol = ExitPolicy(ExitScreen([PortfolioStop(dd)], threshold=1.0, hysteresis=0.5, check="D", min_hold_days=10),
                             switch_cost_bps=COST_BPS)
            ov = policy_overlay(cur, pol, MKT, REB, 0.0, signal_curve=cur)
            for per, (a, b) in {"full": (cur.index[0], cur.index[-1]),
                                "1999-2019": (pd.Timestamp("1999-02-02"), pd.Timestamp("2019-02-01"))}.items():
                ex_, bs = ov.curve.loc[a:b], cur.loc[a:b]
                e = ov.exposure.loc[a:b].to_numpy()
                yrs = (b - a).days / 365.25
                rows.append(dict(target=tgt, stop_drawdown=dd, period=per, exit_CAGR=perf(ex_)["CAGR"], noexit_CAGR=perf(bs)["CAGR"],
                                 exit_Sharpe=perf(ex_)["Sharpe"], noexit_Sharpe=perf(bs)["Sharpe"],
                                 exit_MaxDD=perf(ex_)["MaxDD"], noexit_MaxDD=perf(bs)["MaxDD"],
                                 TimeOut=float((e < 1 - 1e-9).mean()), SwitchesPerYr=float((np.abs(np.diff(e)) > 1e-9).sum() / yrs)))
    return pd.DataFrame(rows)


def main():
    T0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading data and building monthly-rebalanced portfolio curves ...", flush=True)
    setup()
    for k, c in CURVES.items():
        print(f"  {k:14s} {c.index[0].date()} .. {c.index[-1].date()}  n={len(c)}", flush=True)
    print(f"grid: {GRID_SIZE} points in full, {N_POINTS} sampled per calibration (seed {SEED})", flush=True)

    print("\ncausality check (truncate the data, recompute, compare):", flush=True)
    cc = causality_check()
    print(cc.to_string(index=False), flush=True)
    cc.to_csv(OUT / "causality_check.csv", index=False)

    sr = stop_reference()
    sr.to_csv(OUT / "fixed_portfolio_stop_reference.csv", index=False)
    print("\nfixed-setting PortfolioStop reference (not calibrated):\n", sr.round(3).to_string(index=False), flush=True)

    tasks = [(s, t, sp, o) for s in make_screens() for t in TARGETS for sp in SPLITS for o in OBJECTIVES]
    print(f"\n{len(tasks)} calibrations on 4 worker processes ...", flush=True)
    results = {}
    ctx = get_context("fork")
    with ctx.Pool(4) as pool:
        for i, out in enumerate(pool.imap_unordered(worker, tasks), 1):
            task, meta = out[0], out[1]
            results[task] = out
            print(f"  [{i:2d}/{len(tasks)}] {task}  {meta['secs']:.0f}s  (elapsed {time.time() - T0:.0f}s)", flush=True)

    # ---------------- tables
    tables, windows, oos_rows, stab_rows, stat_rows, curves_out = [], [], [], [], [], {}
    for task in tasks:
        _, meta, tbl, wf, summ, best, oos = results[task]
        key = dict(screen=task[0], target=task[1], split=task[2], objective=task[3])
        tables.append(tbl.assign(**key))
        windows.append(wf.assign(**key))
        start = oos["base_date"].iloc[0]
        ex, ne, df, dc = (chain(oos[c], start) for c in ("exit", "noexit", "default", "default_cash"))
        row = dict(**key, windows=len(wf), oos_start=ex.index[1].date(), oos_end=ex.index[-1].date())
        for lab_, cur, e in (("exit", ex, oos["expo"]), ("noexit", ne, None), ("default_exit", df, oos["expo_default"]),
                              ("default_exit_cash", dc, oos["expo_default"])):
            for k, v in perf(cur, e).items():
                row[f"{lab_}_{k}"] = v
        sp = MKT.series("SP500").astype(float)
        spc = sp.loc[ex.index[0]:ex.index[-1]]
        for k, v in perf(spc).items():
            row[f"sp500_{k}"] = v
        # SP500 is a PRICE index here; approximate total return by adding ~2%/yr of dividends (labelled approx)
        yrs = (spc.index - spc.index[0]).days / 365.25
        for k, v in perf(spc * np.power(1.02, yrs)).items():
            row[f"sp500tr_{k}"] = v
        row.update(dsr_grid=summ["dsr"], n_points_tried=summ["points"], feasible=summ["feasible_points"],
                   mean_is=summ["mean_is"], mean_oos_picked=summ["mean_oos"], base_oos_obj=float(wf["base_oos"].mean()),
                   oos_over_is=summ["oos_over_is"], stability=summ["stability"])
        row["windows_beating_noexit"] = float((wf["oos"] > wf["base_oos"]).mean())
        row["grid_share_beating_noexit"] = float((tbl["OOS"] > tbl["BaseOOS"]).mean())
        # parameter-level stability: share of windows choosing the modal value, per parameter
        for k in GRID:
            vc = wf[k].astype(str).value_counts(normalize=True)
            stab_rows.append(dict(**key, param=k, modal_value=vc.index[0], modal_share=float(vc.iloc[0])))
        # OOS tests: exit vs no exit; exit vs passive SP500; exit vs approx TR
        for nm, other in (("noexit", ne), ("passive_sp500", spc), ("passive_sp500_approxTR", spc * np.power(1.02, yrs))):
            b = stats.bootstrap_excess(ex, other, n=2000, seed=SEED, curves=True)
            t = stats.t_stat_excess(ex, other, curves=True)
            stat_rows.append(dict(**key, versus=nm, excess_per_yr=b["mean"], ci5=b["ci_low"], ci95=b["ci_high"],
                                  p_positive=b["p_positive"], nw_t=t))
        for nm, c_ in (("default_exit", df), ("default_exit_cash", dc)):
            b = stats.bootstrap_excess(c_, ne, n=2000, seed=SEED, curves=True)
            stat_rows.append(dict(**key, versus=f"noexit[{nm}]", excess_per_yr=b["mean"], ci5=b["ci_low"], ci95=b["ci_high"],
                                  p_positive=b["p_positive"], nw_t=stats.t_stat_excess(c_, ne, curves=True)))
        r = ex.pct_change().dropna()
        row["oos_psr_vs0"] = stats.probabilistic_sharpe(r.mean() / r.std(), len(r), float(r.skew()), float(r.kurt() + 3))
        oos_rows.append(row)
        curves_out[f"{task[0]}|{task[1]}|{task[2]}|{task[3]}"] = ex
    pd.concat(tables).to_csv(OUT / "grid_tables.csv", index=False)
    pd.concat(windows).to_csv(OUT / "walk_forward_windows.csv", index=False)
    oos_df = pd.DataFrame(oos_rows)
    oos_df.to_csv(OUT / "oos_summary.csv", index=False)
    pd.DataFrame(stab_rows).to_csv(OUT / "param_stability.csv", index=False)
    pd.DataFrame(stat_rows).to_csv(OUT / "oos_tests.csv", index=False)
    pd.DataFrame({k: v for k, v in curves_out.items()}).to_csv(OUT / "oos_exit_curves.csv")
    print("\nsaved CSVs to", OUT, flush=True)

    # ---------------- console key table
    pd.set_option("display.width", 220, "display.max_columns", 50, "display.float_format", "{:.3f}".format)
    key = oos_df[(oos_df.split == "8/4") & (oos_df.objective == "Calmar") & (oos_df.target != "SP500")]
    cols = ["screen", "target", "oos_start", "exit_CAGR", "noexit_CAGR", "sp500_CAGR", "exit_Sharpe", "noexit_Sharpe",
            "exit_MaxDD", "noexit_MaxDD", "sp500_MaxDD", "exit_TimeOut", "exit_SwitchesPerYr", "stability"]
    print("\nKEY OOS TABLE (8/4, Calmar):\n", key[cols].to_string(index=False), flush=True)
    print(f"\ntotal runtime {time.time() - T0:.0f}s", flush=True)
    (OUT / "runtime_seconds.txt").write_text(f"{time.time() - T0:.0f}\n")


if __name__ == "__main__":
    main()
