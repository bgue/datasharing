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

python -m screenlab.timing --screens dogs,graham_defensive --stride 1
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd

from . import backtest, metrics
from .metrics import M
from . import progress as _progress
from .data import Market
from .screens import Screen


@dataclass
class TimingResult:
    screen: str
    table: pd.DataFrame                 # one row per offset
    curves: pd.DataFrame                # offset -> equity curve (columns)
    tranche: pd.Series | None           # equal blend of all phases (phase mode)

    def distribution(self, cols=(M.CAGR, M.EXCESS_YR, M.MAXDD, M.VOL, M.BEATBENCHYRS)) -> pd.DataFrame:
        q = self.table[list(c for c in cols if c in self.table)].describe(percentiles=[.05, .25, .5, .75, .95]).T
        q["range"] = q["max"] - q["min"]
        return q.drop(columns="count")


def phase_offsets(mkt: Market, start, rebalance_months: int, stride: int = 1) -> list[int]:
    """Phase offsets (trading days after `start`) that cover one rebalance period and no more.

    The period is the trading days in [start, start + rebalance_months) (calendar months, end exclusive), so
    offset `period` is the same schedule as offset 0 and is never a variant. Offsets are 0, stride, 2*stride...
    below the period; when the period is not a multiple of `stride` the last offset can land a few days short of
    the next period's offset 0 (e.g. 252 of 253 days, or 252 of 261 on a weekday calendar), which is a thirteenth
    near-copy of offset 0 rather than a new phase, so an offset closer than half a stride to the period's end is
    dropped. Monthly data: stride 1 gives one offset per month."""
    start = mkt.trading_day(start)
    i0 = mkt.index.get_loc(start)
    period = mkt.index.searchsorted(start + pd.DateOffset(months=rebalance_months)) - i0
    stride = max(1, int(stride))
    ks = list(range(0, period, stride))
    if len(ks) > 1 and period - ks[-1] < stride / 2:
        ks.pop()
    return ks


def study(screen: Screen, mkt: Market, cfg: backtest.Config, start=None, end=None,
          mode: str = "phase", stride: int = 1, offsets=None,
          benchmark=None, progress: bool = False) -> TimingResult:
    """`benchmark` is any object with .curve(mkt, cfg, start, end) (see benchmarks.py);
    it is bought on each variant's own start date."""
    if mode not in ("phase", "start"):
        raise ValueError(f"mode must be 'phase' or 'start', got {mode!r}")
    start = mkt.trading_day(start or mkt.index[0] + pd.DateOffset(months=13))
    i0 = mkt.index.get_loc(start)
    offsets = list(offsets) if offsets is not None else phase_offsets(mkt, start, cfg.rebalance_months, stride)

    rows, curves, bench_cache = [], {}, {}
    for n, k in enumerate(offsets):
        _progress.report(n / len(offsets), f"{screen.name}: offset {n + 1}/{len(offsets)}")
        with _progress.scaled(n / len(offsets), (n + 1) / len(offsets), f"offset {n + 1}/{len(offsets)}"):
            if mode == "phase":
                res = backtest.run(screen, mkt, cfg, start, end, phase_days=k)
            else:
                res = backtest.run(screen, mkt, cfg, mkt.index[i0 + k], end)
        d0 = res.curve.index[0]
        if benchmark is not None and d0 not in bench_cache:
            bench_cache[d0] = benchmark.curve(mkt, cfg, d0, end)
        bench = bench_cache.get(d0)
        first = list(res.holdings)[1] if mode == "phase" and len(res.holdings) > 1 else list(res.holdings)[0]
        rows.append({"offset": k, "anniversary": first, **metrics.summarize(res.curve, bench),
                     M.TURNOVER: res.turnover.iloc[1:].mean(), M.COSTS_YR: res.costs.sum() /
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
    best, worst = tr.table[M.CAGR].idxmax(), tr.table[M.CAGR].idxmin()
    lines.append(f"best anniversary  {tr.table.loc[best, 'anniversary']:%b %d} "
                 f"(offset {best}) CAGR {pct(tr.table.loc[best, M.CAGR])}")
    lines.append(f"worst anniversary {tr.table.loc[worst, 'anniversary']:%b %d} "
                 f"(offset {worst}) CAGR {pct(tr.table.loc[worst, M.CAGR])}")
    if tr.tranche is not None:
        t = metrics.summarize(tr.tranche, bench)
        lines.append("tranche blend     " + "  ".join(f"{k} {pct(v) if k != M.SHARPE else f'{v:.2f}'}"
                                                     for k, v in t.items()))
    return "\n".join(lines)


def main(argv=None):
    from .run import add_common_args, build_lab
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--mode", choices=["phase", "start"], default="phase")
    ap.add_argument("--stride", type=int, default=1, help="test every Nth trading day")
    ap.add_argument("--out", help="write per-offset table to this CSV")
    a = ap.parse_args(argv)
    lab, screens = build_lab(a)

    tables = []
    for sc in screens:
        tr = lab.timing(sc, a.mode, a.stride, a.start, a.end, progress=True)
        bench = lab.passive().curve(lab.market, lab.config, tr.curves.index[0], a.end)
        print(report(tr, bench), flush=True)
        tables.append(tr.table.assign(screen=sc.name))
    if a.out:
        pd.concat(tables).to_csv(a.out)
        print(f"\nwrote {a.out}")
    return tables


if __name__ == "__main__":
    main()
