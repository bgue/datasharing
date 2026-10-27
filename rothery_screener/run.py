"""Backtest screens side by side and print today's picks.

python -m rothery_screener.run
python -m rothery_screener.run --only dogs,graham_defensive --rebalance 6 --cost-bps 20 --mer 0.002
python -m rothery_screener.run --set graham_defensive.max_pe=18 --set graham_defensive.min_passes=6
python -m rothery_screener.run --graham-report
"""
from __future__ import annotations

import argparse
import ast

import pandas as pd

from . import backtest, data, metrics
from .screens import DEFENSIVE, ENTERPRISING, REGISTRY, graham_funnel, graham_report


def _value(v: str):
    try:
        return ast.literal_eval(v)
    except (ValueError, SyntaxError):
        return v


def add_common_args(ap: argparse.ArgumentParser):
    g = ap.add_argument_group("data")
    g.add_argument("--csv", nargs=2, metavar=("FUNDAMENTALS", "PRICES"))
    g.add_argument("--freq", choices=["daily", "monthly"], default="daily", help="synthetic data frequency")
    g.add_argument("--start")
    g.add_argument("--end")
    g = ap.add_argument_group("portfolio")
    g.add_argument("--only", help="comma-separated screen names")
    g.add_argument("--screens", dest="only", help=argparse.SUPPRESS)
    g.add_argument("--set", action="append", default=[], metavar="SCREEN.PARAM=VALUE",
                   help="override a screen or Graham-test parameter, repeatable")
    g.add_argument("--rebalance", type=int, default=12, help="months between rebalances")
    g = ap.add_argument_group("costs")
    g.add_argument("--cost-bps", type=float, default=25, help="one-way cost per security, bps")
    g.add_argument("--cost-tiers", help="cap-tiered bps, e.g. '0:75,1000:30,10000:10' ($M:bps)")
    g.add_argument("--commission", type=float, default=0.0, help="flat $ per trade")
    g.add_argument("--capital", type=float, default=100_000)
    g.add_argument("--mer", type=float, default=0.0, help="annual expense ratio applied to every holding")
    g.add_argument("--security-file", help="CSV with ticker and optional cost_bps, mer columns (overrides)")


def build(a):
    fund, prices = (data.load_csv(*a.csv) if a.csv else
                    data.synthetic_market(freq="B" if a.freq == "daily" else "ME"))
    mkt = data.Market(fund, prices)

    cost, mer = a.cost_bps, a.mer
    if a.cost_tiers:
        cost = backtest.cap_tiered({float(k): float(v) for k, v in
                                    (p.split(":") for p in a.cost_tiers.split(","))})
    if a.security_file:
        sec = pd.read_csv(a.security_file).set_index("ticker")
        if "cost_bps" in sec:
            base = cost
            cost = lambda s, base=base, o=sec["cost_bps"].dropna(): \
                backtest.resolve(base, s.index, s).where(~s.index.isin(o.index), o.reindex(s.index))
        if "mer" in sec:
            mer = {"_default": a.mer, **sec["mer"].dropna().to_dict()}
    cfg = backtest.Config(rebalance_months=a.rebalance, cost_bps=cost, commission=a.commission,
                          capital=a.capital, mer=mer)

    overrides: dict[str, dict] = {}
    for s in a.set:
        key, v = s.split("=", 1)
        name, param = key.split(".", 1)
        overrides.setdefault(name, {})[param] = _value(v)
    names = a.only.split(",") if a.only else list(REGISTRY)
    screens = [REGISTRY[k].with_overrides(**overrides.get(k, {})) for k in names]
    return mkt, cfg, screens


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--graham-report", action="store_true", help="show Graham test funnel as of the last date")
    a = ap.parse_args(argv)
    mkt, cfg, screens = build(a)
    asof = mkt.trading_day(a.end, "prev") if a.end else mkt.index[-1]

    if a.graham_report:
        s = mkt.view(asof)
        for sc in screens:
            tests = sc.params.get("tests")
            if tests is None:
                continue
            print(f"\n{sc.name} as of {asof:%Y-%m-%d}: cumulative survivors / pass rate per test")
            rep = graham_report(s, tests)
            funnel = graham_funnel(s, tests)
            rate = rep.drop(columns="passes").mean().map("{:.0%}".format)
            print(pd.DataFrame({"survivors": funnel, "pass_rate": rate.reindex(funnel.index)})
                  .fillna("").to_string())
            print("names by tests passed:", rep["passes"].value_counts().sort_index(ascending=False).to_dict())
        return

    bench = backtest.run(backtest.benchmark_screen(), mkt, cfg, a.start, a.end)
    rows = {"benchmark": {**metrics.summarize(bench.curve), "AvgN": 60.0, "Turnover": bench.turnover.mean()}}
    results = {}
    for sc in screens:
        res = backtest.run(sc, mkt, cfg, a.start, a.end)
        results[sc.name] = res
        yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
        rows[sc.name] = {**metrics.summarize(res.curve, bench.curve),
                         "AvgN": pd.Series({k: len(v) for k, v in res.holdings.items()}).mean(),
                         "Turnover": res.turnover.iloc[1:].mean(), "Costs/yr": res.costs.sum() / yrs}

    tbl = pd.DataFrame(rows).T
    pct = ["CAGR", "Vol", "MaxDD", "Worst12m", "BeatBenchYrs", "Excess/yr", "Turnover", "Costs/yr"]
    fmt = tbl.copy()
    for c in tbl.columns:
        fmt[c] = tbl[c].map(lambda x: "" if pd.isna(x) else (f"{x:.1%}" if c in pct else f"{x:.2f}"))
    span = f"{bench.curve.index[0]:%Y-%m-%d} to {bench.curve.index[-1]:%Y-%m-%d}"
    print(f"\nBacktest {span}, rebalance every {cfg.rebalance_months}m\n")
    print(fmt.sort_values("CAGR", ascending=False).to_string())

    print(f"\nCurrent picks as of {asof:%Y-%m-%d}")
    for sc in screens:
        print(f"  {sc.name:<20} {', '.join(mkt.picks(sc, asof)) or '(no passes)'}")
    return tbl, results


if __name__ == "__main__":
    main()
