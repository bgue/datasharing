"""Backtest screens side by side and print today's picks (CLI over the Lab API).

python -m screenlab.run
python -m screenlab.run --only dogs,graham_defensive --rebalance 6 --cost-bps 20 --mer 0.002
python -m screenlab.run --benchmark SYN_CAPWT --bench-mer 0.0006 --bench-cost-bps 5
python -m screenlab.run --compare top60            # extra row: equal-weight 60 largest (never primary)
python -m screenlab.run --screen-file my_screens.json --only cheap_yield,dogs
python -m screenlab.run --set graham_defensive.max_pe=18 --set graham_defensive.min_passes=6
python -m screenlab.run --graham-report
python -m screenlab.run --exit "TrendBreak:index=SYN_CAPWT,Breadth" --exit-threshold 0.7 \\
    --exit-park cash --exit-reentry signal --exit-compare      # with and without an exit policy
"""
from __future__ import annotations

import argparse
import ast
import dataclasses

import pandas as pd

from . import backtest
from .api import Lab
from .benchmarks import IndexBenchmark, equal_weight_top
from .exit_policy import ExitPolicy
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
    g.add_argument("--benchmark", help="passive index name / ticker to buy and hold (replaces the default "
                                       "passive index); 'topN' is accepted but is only a compare row")
    g.add_argument("--compare", action="append", default=[], metavar="NAME|topN",
                   help="extra comparison row: an index name or 'topN' equal-weight N largest, repeatable")
    g.add_argument("--bench-mer", type=float, default=0.0, help="benchmark fund's annual MER")
    g.add_argument("--bench-cost-bps", type=float, default=0.0, help="benchmark one-time purchase cost")


def add_exit_args(ap: argparse.ArgumentParser):
    g = ap.add_argument_group("exit policy")
    g.add_argument("--exit", metavar="SIGNALS",
                   help='exit signals, e.g. "TrendBreak:index=SYN_CAPWT,window_months=10,Breadth:days=138" '
                        '(window_months = calendar months, days = trading days; see list_signals) '
                        '(a Name:param=v,param=v group per signal, comma-separated)')
    g.add_argument("--exit-threshold", type=float, default=0.7)
    g.add_argument("--exit-hysteresis", type=float, default=0.1)
    g.add_argument("--exit-check", default="D", help="decision cadence: D, W-FRI, ME, ...")
    g.add_argument("--exit-lag", type=int, default=1, help="execution lag in bars")
    g.add_argument("--exit-min-hold-days", type=int, default=0)
    g.add_argument("--exit-expo", type=float, default=0.0, help="exposure while flagged (0 = fully out)")
    g.add_argument("--exit-park", default="cash", help="'cash', an annual rate (0.03) or an index/ticker name")
    g.add_argument("--exit-park-mer", type=float, default=0.0)
    g.add_argument("--exit-park-cost-bps", type=float, default=0.0)
    g.add_argument("--exit-switch-bps", type=float, default=5.0)
    g.add_argument("--exit-reentry", choices=["signal", "next_rebalance", "signal_or_rebalance"], default="signal")
    g.add_argument("--exit-delay", type=int, default=0, help="days the flag must stay clear before re-entry")
    g.add_argument("--exit-steps", type=int, default=1, help="scale back in over this many check periods")
    g.add_argument("--exit-rescreen", action="store_true", help="run the entry screen fresh on re-entry")
    g.add_argument("--exit-compare", action="store_true", help="run every screen with and without the exit policy")


def parse_exit_signals(text: str, lab: Lab) -> list:
    """'TrendBreak:index=X,window_months=6,Breadth:days=100' -> signal objects (items with ':' start a signal,
    bare 'k=v' items extend the previous one, other bare names start a parameterless signal)."""
    from . import exit_signals as es
    try:
        from . import exit_inputs  # noqa: F401  (registers the extra inputs)
    except ImportError:
        pass
    specs: list = []
    for item in (t.strip() for t in text.split(",") if t.strip()):
        if ":" in item:
            name, kv = item.split(":", 1)
            specs.append((name.strip(), {}))
            item = kv
        elif "=" not in item:
            specs.append((item, {}))
            continue
        if "=" in item:
            if not specs:
                raise SystemExit(f"--exit: parameter {item!r} before any signal name")
            k, v = item.split("=", 1)
            specs[-1][1][k.strip()] = _value(v.strip())
    out = []
    for name, params in specs:
        if name not in es.BUILTINS:
            raise SystemExit(f"--exit: unknown signal {name!r}; available: {sorted(es.BUILTINS)}")
        cls = es.BUILTINS[name]
        fields = {f.name for f in dataclasses.fields(cls)}
        idx_cols = list(lab.market.indices.columns)
        if "index" in fields and "index" not in params and "SP500" not in idx_cols and idx_cols:
            params["index"] = idx_cols[0]
        out.append(cls(**params))
    return out


def build_exit(a, lab: Lab) -> ExitPolicy | None:
    if not a.exit:
        return None
    park = _value(a.exit_park)
    screen = es_screen(parse_exit_signals(a.exit, lab), a)
    return ExitPolicy(screen, a.exit_expo, park=park, park_mer=a.exit_park_mer,
                      park_cost_bps=a.exit_park_cost_bps, switch_cost_bps=a.exit_switch_bps,
                      reentry=a.exit_reentry, reentry_delay_days=a.exit_delay, reentry_steps=a.exit_steps,
                      rescreen_on_reentry=a.exit_rescreen)


def es_screen(signals, a):
    from .exit_signals import ExitScreen
    return ExitScreen(signals, threshold=a.exit_threshold, hysteresis=a.exit_hysteresis, check=a.exit_check,
                      lag=a.exit_lag, min_hold_days=a.exit_min_hold_days)


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
    is_top = lambda b: b.lower().startswith("top") and b[3:].isdigit()
    extra = [a.benchmark] if a.benchmark and is_top(a.benchmark) else []
    lab.compare = tuple(equal_weight_top(int(b[3:])) if is_top(b) else IndexBenchmark(b)
                        for b in [*extra, *a.compare])
    if a.benchmark and not is_top(a.benchmark):
        lab.benchmark = IndexBenchmark(a.benchmark, mer=a.bench_mer, cost_bps=a.bench_cost_bps)
    else:                                   # the default passive index, with the fund's MER / cost if given
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
    add_exit_args(ap)
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

    rep = lab.backtest(screens, a.start, a.end, exit=build_exit(a, lab), with_and_without=a.exit_compare)
    print("\n" + str(rep))
    print(f"\nCurrent picks as of {asof:%Y-%m-%d}")
    for sc in screens:
        print(f"  {sc.name:<20} {', '.join(lab.picks(sc, asof).index) or '(no passes)'}")
    return rep


if __name__ == "__main__":
    main()
