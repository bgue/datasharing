"""Backtest screens side by side and print today's picks (CLI over the Lab API).

python -m rothery_screener.run
python -m rothery_screener.run --only dogs,graham_defensive --rebalance 6 --cost-bps 20 --mer 0.002
python -m rothery_screener.run --benchmark SYN_CAPWT --bench-mer 0.0006 --bench-cost-bps 5
python -m rothery_screener.run --benchmark top60          # equal-weight 60 largest instead
python -m rothery_screener.run --screen-file my_screens.json --only cheap_yield,dogs
python -m rothery_screener.run --set graham_defensive.max_pe=18 --set graham_defensive.min_passes=6
python -m rothery_screener.run --graham-report
"""
from __future__ import annotations

import argparse
import ast

import pandas as pd

from . import backtest
from .api import Lab
from .benchmarks import IndexBenchmark, equal_weight_top
from .spec import load_screens


def _value(v: str):
    try:
        return ast.literal_eval(v)
    except (ValueError, SyntaxError):
        return v


def add_common_args(ap: argparse.ArgumentParser):
    g = ap.add_argument_group("data")
    g.add_argument("--csv", nargs="+", metavar="FILE", help="FUNDAMENTALS PRICES [INDICES]")
    g.add_argument("--freq", choices=["daily", "monthly"], default="daily", help="synthetic data frequency")
    g.add_argument("--start")
    g.add_argument("--end")
    g = ap.add_argument_group("portfolio")
    g.add_argument("--only", help="comma-separated screen names")
    g.add_argument("--screens", dest="only", help=argparse.SUPPRESS)
    g.add_argument("--screen-file", action="append", default=[], help="JSON screen spec(s), repeatable")
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
    g = ap.add_argument_group("benchmark")
    g.add_argument("--benchmark", help="index name / ticker to buy and hold, or 'topN' for equal-weight N largest")
    g.add_argument("--bench-mer", type=float, default=0.0, help="benchmark fund's annual MER")
    g.add_argument("--bench-cost-bps", type=float, default=0.0, help="benchmark one-time purchase cost")


def build_config(a) -> backtest.Config:
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
    return backtest.Config(rebalance_months=a.rebalance, cost_bps=cost, commission=a.commission,
                           capital=a.capital, mer=mer)


def build_lab(a) -> tuple[Lab, list]:
    cfg = build_config(a)
    lab = (Lab.from_csv(*a.csv, config=cfg) if a.csv else
           Lab.synthetic(freq="B" if a.freq == "daily" else "ME", config=cfg))
    if a.benchmark:
        b = a.benchmark
        lab.benchmark = (equal_weight_top(int(b[3:])) if b.lower().startswith("top") and b[3:].isdigit()
                         else IndexBenchmark(b, mer=a.bench_mer, cost_bps=a.bench_cost_bps))
    elif isinstance(lab.benchmark, IndexBenchmark):
        lab.benchmark = IndexBenchmark(lab.benchmark.index, a.bench_mer, a.bench_cost_bps)
    for path in a.screen_file:
        for sc in load_screens(path):
            lab.define(sc)

    overrides: dict[str, dict] = {}
    for s in a.set:
        key, v = s.split("=", 1)
        name, param = key.split(".", 1)
        overrides.setdefault(name, {})[param] = _value(v)
    names = a.only.split(",") if a.only else list(lab.screens)
    return lab, [lab.screen(k, **overrides.get(k, {})) for k in names]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--graham-report", action="store_true", help="show Graham test funnel as of the last date")
    a = ap.parse_args(argv)
    lab, screens = build_lab(a)
    asof = lab._asof(a.end)

    if a.graham_report:
        for sc in screens:
            tests = sc.params.get("tests")
            if tests is None:
                continue
            rep, funnel = lab.graham(tests, asof)
            rate = rep.drop(columns="passes").mean().map("{:.0%}".format)
            print(f"\n{sc.name} as of {asof:%Y-%m-%d}: cumulative survivors / pass rate per test")
            print(pd.DataFrame({"survivors": funnel, "pass_rate": rate.reindex(funnel.index)})
                  .fillna("").to_string())
            print("names by tests passed:", rep["passes"].value_counts().sort_index(ascending=False).to_dict())
        return

    rep = lab.backtest(screens, a.start, a.end)
    print("\n" + str(rep))
    print(f"\nCurrent picks as of {asof:%Y-%m-%d}")
    for sc in screens:
        print(f"  {sc.name:<20} {', '.join(lab.picks(sc, asof).index) or '(no passes)'}")
    return rep


if __name__ == "__main__":
    main()
