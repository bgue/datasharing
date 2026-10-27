"""Benchmarks: what a screen portfolio is measured against.

IndexBenchmark   buy and hold an index (or an index fund/ETF) from the
                 portfolio's start date: one purchase, its own MER and
                 trading cost (both 0 by default, i.e. a frictionless index).
ScreenBenchmark  run a screen as the benchmark (e.g. equal-weight top 60),
                 paying the same costs as the portfolio under test.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import backtest
from .data import Market
from .screens import Screen


@dataclass
class IndexBenchmark:
    index: str                        # name in Market.indices, or a ticker in prices
    mer: float = 0.0                  # annual expense ratio of the fund tracking it
    cost_bps: float = 0.0             # one-time purchase cost
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.index

    def curve(self, mkt: Market, cfg: backtest.Config, start, end=None) -> pd.Series:
        s = mkt.series(self.index).loc[pd.Timestamp(start):pd.Timestamp(end) if end else None].dropna()
        yrs = ((s.index - s.index[0]).days / 365.25).to_numpy()
        g = s / s.iloc[0] * np.power(1 - self.mer, yrs)
        g.iloc[1:] *= 1 - self.cost_bps / 1e4      # bought at the start-day close, like a screen
        return g


@dataclass
class ScreenBenchmark:
    screen: Screen
    name: str | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        return self.name or self.screen.name

    def curve(self, mkt: Market, cfg: backtest.Config, start, end=None) -> pd.Series:
        # Runs once per (config, end); later starts slice and rebase that run.
        # Exact for same-window comparisons, approximate for timing --mode start.
        key = (id(mkt), repr(cfg), end)
        start = pd.Timestamp(start)
        hit = self._cache.get(key)
        if hit is None or hit[0] is not mkt or hit[1].index[0] > start:
            hit = self._cache[key] = (mkt, backtest.run(self.screen, mkt, cfg, start, end).curve)
        c = hit[1].loc[start:]
        return c / c.iloc[0]


def equal_weight_top(n: int = 60) -> ScreenBenchmark:
    """Equal-weight the `n` largest names: a stand-in when no index series is loaded."""
    return ScreenBenchmark(backtest.benchmark_screen(n))
