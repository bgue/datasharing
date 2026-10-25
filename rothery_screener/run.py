"""python -m rothery_screener.run [--csv fund.csv prices.csv] [--only dogs,piotroski]"""
from __future__ import annotations

import argparse

import pandas as pd

from . import backtest, data, metrics
from .screens import REGISTRY


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs=2, metavar=("FUNDAMENTALS", "PRICES"))
    ap.add_argument("--only", help="comma-separated screen names")
    ap.add_argument("--rebalance", type=int, default=12, help="months between rebalances")
    ap.add_argument("--cost-bps", type=float, default=50)
    ap.add_argument("--start")
    a = ap.parse_args(argv)

    fund, prices = data.load_csv(*a.csv) if a.csv else data.synthetic_market()
    cfg = backtest.Config(rebalance_months=a.rebalance, cost_bps=a.cost_bps)
    screens = [REGISTRY[k] for k in a.only.split(",")] if a.only else REGISTRY.values()

    bench = backtest.equal_weight_benchmark(prices, fund, start=a.start, cfg=cfg)
    rows = {"benchmark": {**metrics.summarize(bench.returns), "AvgN": 60, "Turnover": bench.turnover.mean()}}
    results = {}
    for sc in screens:
        res = backtest.run(sc, fund, prices, cfg, a.start)
        results[sc.name] = res
        rows[sc.name] = {**metrics.summarize(res.returns, bench.returns),
                         "AvgN": pd.Series({k: len(v) for k, v in res.holdings.items()}).mean(),
                         "Turnover": res.turnover.mean()}

    tbl = pd.DataFrame(rows).T
    pct = ["CAGR", "Vol", "MaxDD", "Worst12m", "BeatBenchYrs", "Excess/yr", "Turnover"]
    fmt = tbl.copy()
    for c in tbl.columns:
        fmt[c] = tbl[c].map(lambda x: "" if pd.isna(x) else (f"{x:.1%}" if c in pct else f"{x:.2f}"))
    span = f"{bench.returns.index[0]:%Y-%m} to {bench.returns.index[-1]:%Y-%m}"
    print(f"\nBacktest {span}, rebalance every {cfg.rebalance_months}m, {cfg.cost_bps:.0f}bp costs\n")
    print(fmt.sort_values("CAGR", ascending=False).to_string())

    # Current picks: what each screen would buy today
    asof = prices.index[-1]
    s = data.snapshot(fund, asof).join(data.trailing_stats(prices, asof))
    s = s[prices.loc[asof].reindex(s.index).notna()]
    print(f"\nCurrent picks as of {asof:%Y-%m-%d}")
    for sc in screens:
        print(f"  {sc.name:<20} {', '.join(sc(s)) or '(no passes)'}")
    return tbl, results


if __name__ == "__main__":
    main()
