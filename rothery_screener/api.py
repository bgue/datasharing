"""Python SDK: one object, `Lab`, wraps data, screens, benchmark and config.

    from rothery_screener import Lab, ScreenBuilder, IndexBenchmark

    lab = Lab.synthetic()                                   # or Lab.from_csv(f, p, indices)
    lab.benchmark = IndexBenchmark("SYN_CAPWT", mer=0.0006)
    lab.define(ScreenBuilder("cheap_yield").universe(top=100)
               .where("pe < 15", "div_yield > 0.03").rank(div_yield="desc").take(10))

    lab.picks("cheap_yield")                                # today's names + key ratios
    rep = lab.backtest(["dogs", "cheap_yield"], rebalance_months=6, cost_bps=20)
    print(rep)                                              # formatted comparison table
    rep.table, rep.curves                                   # raw numbers / equity curves
    tr = lab.timing("dogs", stride=5)                       # daily-start timing study
    lab.graham("defensive", max_pe=18)                      # funnel + per-test pass table

Every call takes Config fields as keyword overrides (rebalance_months,
cost_bps, commission, capital, mer, delist_return, min_holdings, cash_rate)
without changing `lab.config`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from . import backtest, data, metrics
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top
from .screens import REGISTRY, Screen, graham_funnel, graham_report
from .spec import define_screen, graham_tests

PCT_COLS = ["CAGR", "Vol", "MaxDD", "Worst12m", "BeatBenchYrs", "Excess/yr", "Turnover", "Costs/yr"]


def format_table(tbl: pd.DataFrame) -> str:
    out = tbl.copy().astype(object)
    for c in tbl.columns:
        out[c] = tbl[c].map(lambda x: "" if pd.isna(x) else (f"{x:.1%}" if c in PCT_COLS else f"{x:.2f}"))
    return out.to_string()


@dataclass
class BacktestReport:
    table: pd.DataFrame                       # one row per screen + the benchmark
    results: dict                             # name -> backtest.Result
    benchmark: pd.Series                      # benchmark equity curve
    benchmark_label: str
    config: backtest.Config

    @property
    def curves(self) -> pd.DataFrame:
        c = {k: r.curve for k, r in self.results.items()}
        return pd.DataFrame({self.benchmark_label: self.benchmark, **c})

    def __str__(self):
        span = f"{self.benchmark.index[0]:%Y-%m-%d} to {self.benchmark.index[-1]:%Y-%m-%d}"
        head = (f"Backtest {span}, rebalance every {self.config.rebalance_months}m, "
                f"benchmark {self.benchmark_label}\n")
        return head + "\n" + format_table(self.table.sort_values("CAGR", ascending=False))


@dataclass
class Lab:
    market: data.Market
    config: backtest.Config = field(default_factory=backtest.Config)
    benchmark: IndexBenchmark | ScreenBenchmark | None = None
    screens: dict = field(default_factory=lambda: dict(REGISTRY))

    def __post_init__(self):
        if self.benchmark is None:
            idx = list(self.market.indices.columns)
            self.benchmark = IndexBenchmark(idx[0]) if idx else equal_weight_top(60)

    # ---- construction ---------------------------------------------------
    @classmethod
    def synthetic(cls, freq: str = "B", n_stocks: int = 250, years: int = 25, seed: int = 7, **kw) -> "Lab":
        fund, prices, indices = data.synthetic_market(n_stocks=n_stocks, years=years, freq=freq, seed=seed)
        return cls(data.Market(fund, prices, indices), **kw)

    @classmethod
    def from_csv(cls, fundamentals: str, prices: str, indices: str | None = None, **kw) -> "Lab":
        return cls(data.Market(*data.load_csv(fundamentals, prices, indices)), **kw)

    # ---- screens ----------------------------------------------------------
    def define(self, spec) -> Screen:
        """Register a screen from a builder, spec, dict, JSON string/path or Screen."""
        sc = define_screen(spec)
        self.screens[sc.name] = sc
        return sc

    def screen(self, s, **overrides) -> Screen:
        sc = s if isinstance(s, Screen) else (self.screens[s] if isinstance(s, str) and s in self.screens
                                               else define_screen(s))
        return sc.with_overrides(**overrides) if overrides else sc

    def _cfg(self, **kw) -> backtest.Config:
        return replace(self.config, **kw) if kw else self.config

    def _asof(self, asof=None) -> pd.Timestamp:
        return self.market.trading_day(asof, "prev") if asof else self.market.index[-1]

    def snapshot(self, asof=None) -> pd.DataFrame:
        return self.market.view(self._asof(asof))

    def picks(self, screen, asof=None, columns=("sector", "market_cap", "pe", "pb", "div_yield",
                                                  "f_score", "mom_12_1")) -> pd.DataFrame:
        s = self.snapshot(asof)
        names = self.market.picks(self.screen(screen), self._asof(asof))
        return s.loc[names, [c for c in columns if c in s]]

    # ---- analysis ---------------------------------------------------------
    def backtest(self, screens=None, start=None, end=None, benchmark=None, **cfg) -> BacktestReport:
        cfg = self._cfg(**cfg)
        bench = benchmark or self.benchmark
        screens = [self.screen(s) for s in (screens or list(self.screens))]
        start = start or self.market.index[0] + pd.DateOffset(months=13)
        start = self.market.trading_day(start)
        bcurve = bench.curve(self.market, cfg, start, end)
        rows = {bench.label: {**metrics.summarize(bcurve), "AvgN": float("nan")}}
        results = {}
        for sc in screens:
            res = backtest.run(sc, self.market, cfg, start, end)
            results[sc.name] = res
            yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
            rows[sc.name] = {**metrics.summarize(res.curve, bcurve),
                             "AvgN": pd.Series({k: len(v) for k, v in res.holdings.items()}).mean(),
                             "Turnover": res.turnover.iloc[1:].mean(),
                             "Costs/yr": res.costs.sum() / yrs}
        return BacktestReport(pd.DataFrame(rows).T, results, bcurve, bench.label, cfg)

    def timing(self, screen, mode: str = "phase", stride: int = 1, start=None, end=None,
               offsets=None, benchmark=None, progress: bool = False, **cfg):
        """Rebalance-timing study (see timing.py); returns a timing.TimingResult."""
        from . import timing          # lazy: lets `python -m rothery_screener.timing` run cleanly
        return timing.study(self.screen(screen), self.market, self._cfg(**cfg), start, end, mode,
                            stride, offsets, benchmark or self.benchmark, progress)

    def graham(self, tests="defensive", asof=None, **overrides) -> tuple[pd.DataFrame, pd.Series]:
        """(per-name pass/fail table, cumulative funnel) for a preset or GrahamTests."""
        t = graham_tests(tests)
        t = replace(t, **overrides) if overrides else t
        s = self.snapshot(asof)
        return graham_report(s, t), graham_funnel(s, t)
