"""Rebalance-timing study: how much does *when* you run the screen matter?

Two designs:

phase  (default) Every variant forms its portfolio on the same start day and
       ends on the same day, but its rebalance anniversary is shifted by k
       trading days, k = 0 .. one rebalance period. All curves cover an
       identical window, so differences are pure timing luck. Averaging the
       curves gives the "tranche" portfolio (capital split across every
       anniversary), the usual cure for timing luck.
start  Literally start investing on day T0 + k and hold to the end. Windows
       differ, so compare excess return over the benchmark, not raw CAGR.

python -m rothery_screener.timing --screens dogs,graham_defensive --stride 1
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import backtest, metrics
from .data import Market
from .screens import Screen


@dataclass
class TimingResult:
    screen: str
    table: pd.DataFrame                 # one row per offset
    curves: pd.DataFrame                # offset -> equity curve (columns)
    tranche: pd.Series | None           # equal blend of all phases (phase mode)

    def distribution(self, cols=("CAGR", "Excess/yr", "MaxDD", "Vol", "BeatBenchYrs")) -> pd.DataFrame:
        q = self.table[list(c for c in cols if c in self.table)].describe(percentiles=[.05, .25, .5, .75, .95]).T
        q["range"] = q["max"] - q["min"]
        return q.drop(columns="count")


def study(screen: Screen, mkt: Market, cfg: backtest.Config, start=None, end=None,
          mode: str = "phase", stride: int = 1, offsets=None,
          bench: pd.Series | None = None, progress: bool = False) -> TimingResult:
    start = mkt.trading_day(start or mkt.index[0] + pd.DateOffset(months=13))
    i0 = mkt.index.get_loc(start)
    period = mkt.index.searchsorted(start + pd.DateOffset(months=cfg.rebalance_months)) - i0
    offsets = list(offsets) if offsets is not None else list(range(0, period, stride))

    rows, curves = [], {}
    for n, k in enumerate(offsets):
        if mode == "phase":
            res = backtest.run(screen, mkt, cfg, start, end, phase_days=k)
        else:
            res = backtest.run(screen, mkt, cfg, mkt.index[i0 + k], end)
        first = list(res.holdings)[1] if mode == "phase" and len(res.holdings) > 1 else list(res.holdings)[0]
        rows.append({"offset": k, "anniversary": first, **metrics.summarize(res.curve, bench),
                     "Turnover": res.turnover.iloc[1:].mean(), "Costs/yr": res.costs.sum() /
                     ((res.curve.index[-1] - res.curve.index[0]).days / 365.25)})
        curves[k] = res.curve
        if progress and n % 25 == 0:
            print(f"  {screen.name}: offset {n + 1}/{len(offsets)}", flush=True)

    curves = pd.DataFrame(curves)
    tranche = None
    if mode == "phase":
        tranche = curves.ffill().mean(axis=1)
    return TimingResult(screen.name, pd.DataFrame(rows).set_index("offset"), curves, tranche)


def report(tr: TimingResult, bench: pd.Series | None = None) -> str:
    pct = lambda x: f"{x:.1%}" if pd.notna(x) else ""
    d = tr.distribution()
    lines = [f"\n== {tr.screen}: {len(tr.table)} start offsets =="]
    lines.append(d.map(pct).to_string())
    best, worst = tr.table["CAGR"].idxmax(), tr.table["CAGR"].idxmin()
    lines.append(f"best anniversary  {tr.table.loc[best, 'anniversary']:%b %d} "
                 f"(offset {best}) CAGR {pct(tr.table.loc[best, 'CAGR'])}")
    lines.append(f"worst anniversary {tr.table.loc[worst, 'anniversary']:%b %d} "
                 f"(offset {worst}) CAGR {pct(tr.table.loc[worst, 'CAGR'])}")
    if tr.tranche is not None:
        t = metrics.summarize(tr.tranche, bench)
        lines.append("tranche blend     " + "  ".join(f"{k} {pct(v) if k != 'Sharpe' else f'{v:.2f}'}"
                                                     for k, v in t.items()))
    return "\n".join(lines)


def main(argv=None):
    from .run import add_common_args, build
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--mode", choices=["phase", "start"], default="phase")
    ap.add_argument("--stride", type=int, default=1, help="test every Nth trading day")
    ap.add_argument("--out", help="write per-offset table to this CSV")
    a = ap.parse_args(argv)
    mkt, cfg, screens = build(a)

    b = backtest.run(backtest.benchmark_screen(), mkt, cfg, a.start, a.end).curve
    tables = []
    for sc in screens:
        tr = study(sc, mkt, cfg, a.start, a.end, a.mode, a.stride, bench=b, progress=True)
        print(report(tr, b), flush=True)
        tables.append(tr.table.assign(screen=sc.name))
    if a.out:
        pd.concat(tables).to_csv(a.out)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
