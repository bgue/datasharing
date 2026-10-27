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


def parse_benchmark(b):
    """A benchmark from a request-style string: "topN" -> equal-weight top-N ScreenBenchmark, any other
    string -> IndexBenchmark of that index / ticker. Benchmark objects pass through."""
    if isinstance(b, str):
        t = b.strip()
        if t.lower().startswith("top") and t[3:].isdigit():
            return equal_weight_top(int(t[3:]))
        return IndexBenchmark(t)
    return b


def passive_index(mkt: Market) -> tuple[str, pd.Series]:
    """Build the passive buy-and-hold index of a market that ships none: (name, series from 1.0).

    PASSIVE_CAPWT  cap-weighted total-return index of every live name, when market caps exist. A name's
                   cap is its latest reported market_cap carried along its own total-return index, so
                   weights drift like a real index; a delisted name drops out at its last price (its
                   weight is spread over the survivors) and new listings join when they first trade.
    PASSIVE_EW     otherwise: equal-weight buy and hold of the names trading on the first date, never
                   rebalanced and never adding new listings; a delisted name stays at its last price.
    """
    px = mkt.prices
    cap = _latest_caps(mkt)
    if cap is not None:
        ref = px.ffill().bfill()
        pos = np.clip(ref.index.searchsorted(cap["date"].to_numpy(), side="right") - 1, 0, None)
        at = pd.Series([ref[t].iloc[p] for t, p in zip(cap.index, pos)], index=cap.index)
        scale = (cap["market_cap"] / at).reindex(px.columns)      # NaN: no cap, so no weight
        c = px.mul(scale, axis=1)
        w = c.shift(1).fillna(0.0)
        r = px.pct_change(fill_method=None).fillna(0.0)
        tot = w.sum(axis=1).replace(0.0, np.nan)
        ret = ((w * r).sum(axis=1) / tot).fillna(0.0)
        return "PASSIVE_CAPWT", (1 + ret).cumprod().rename("PASSIVE_CAPWT")
    held = px.iloc[0].notna()
    rel = px.loc[:, held].ffill() / px.loc[:, held].iloc[0]
    return "PASSIVE_EW", rel.mean(axis=1).rename("PASSIVE_EW")


def _latest_caps(mkt: Market) -> pd.DataFrame | None:
    """Latest positive market_cap per ticker present in prices (with its report date), or None."""
    f = mkt.fund
    if "market_cap" not in f or "ticker" not in f or not len(f):
        return None
    last = f.dropna(subset=["market_cap"]).groupby("ticker").tail(1).set_index("ticker")
    last = last[(last["market_cap"] > 0) & last.index.isin(mkt.prices.columns)]
    return last[["date", "market_cap"]] if len(last) else None
