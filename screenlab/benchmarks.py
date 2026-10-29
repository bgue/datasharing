"""Benchmarks: what a screen portfolio is measured against.

IndexBenchmark   buy and hold an index (or an index fund/ETF) from the
                 portfolio's start date: one purchase, its own MER and
                 trading cost (both 0 by default, i.e. a frictionless index).
                 It knows its `kind`: "total_return", "price" (dividends left out) or, with
                 `dividend_yield`, "approx_total_return" (price x (1+y)^years). Portfolios are total
                 return, so a "price" benchmark is refused by the Lab (see `Lab.passive`).
ScreenBenchmark  run a screen as the benchmark (e.g. equal-weight top 60),
                 paying the same costs as the portfolio under test.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import backtest
from .data import Market
from .dataset import ALL_KINDS, approx_total_return
from .screens import Screen
from .serial import TAG, from_dict as _from, register_tag, serializable, to_dict as _to


@serializable("IndexBenchmark")
@dataclass
class IndexBenchmark:
    index: str                        # name in Market.indices, or a ticker in prices
    mer: float = 0.0                  # annual expense ratio of the fund tracking it
    cost_bps: float = 0.0             # one-time purchase cost
    name: str | None = None
    kind: str | None = None           # None: taken from the dataset; "total_return" | "price" | "approx_total_return"
    dividend_yield: float | None = None   # annual yield added back to a PRICE index (0.02 = 2%): approx. total return

    def __post_init__(self):
        if self.kind is not None and self.kind not in ALL_KINDS:
            raise ValueError(f"IndexBenchmark kind must be one of {ALL_KINDS} (or None), got {self.kind!r}")
        if self.dividend_yield is not None and not 0.0 <= float(self.dividend_yield) < 1.0:
            raise ValueError(f"dividend_yield must be a yearly rate in [0, 1), e.g. 0.02, got {self.dividend_yield!r}")
        if self.kind == "approx_total_return" and self.dividend_yield is None:
            raise ValueError("kind='approx_total_return' needs dividend_yield, e.g. dividend_yield=0.02")
        if self.kind == "total_return" and self.dividend_yield is not None:
            raise ValueError("dividend_yield is for a price index; kind='total_return' already includes dividends")

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.dividend_yield is not None:
            return f"{self.index} +{self.dividend_yield:.1%} div (approx. TR)"
        return self.index

    def source_kind(self, mkt: Market) -> str:
        """What the underlying series is: the explicit `kind` ("approx_total_return" means a price series),
        else the dataset's record for this index ("total_return" for a ticker or an unrecorded index)."""
        if self.kind in ("price", "total_return"):
            return self.kind
        if self.kind == "approx_total_return":
            return "price"
        return mkt.dataset.kind_of(self.index)

    def effective_kind(self, mkt: Market) -> str:
        """The kind of the curve this benchmark produces: approx_total_return with a dividend yield."""
        base = self.source_kind(mkt)
        if self.dividend_yield is None:
            return base
        if base != "price":
            raise ValueError(f"dividend_yield={self.dividend_yield} given for {self.index!r}, which is already "
                             f"a total-return series: adding dividends would count them twice")
        return "approx_total_return"

    def curve(self, mkt: Market, cfg: backtest.Config, start, end=None) -> pd.Series:
        s = mkt.series(self.index).loc[pd.Timestamp(start):pd.Timestamp(end) if end else None].dropna()
        if self.dividend_yield is not None:
            self.effective_kind(mkt)                 # raises when the series already includes dividends
            s = approx_total_return(s, float(self.dividend_yield))
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

    def to_dict(self) -> dict:
        sc = self.screen
        if sc.fn is backtest.bench_top and sc.name == f"bench_top{sc.n}":
            out = {TAG: "ScreenBenchmark", "top": int(sc.n)}
        else:
            out = {TAG: "ScreenBenchmark", "screen": _to(sc)}
        if self.name:
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenBenchmark":
        b = equal_weight_top(d["top"]) if "top" in d else cls(_from(d["screen"]))
        if d.get("name"):
            b.name = d["name"]
        return b

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


ScreenBenchmark._serial_tag = "ScreenBenchmark"
register_tag("ScreenBenchmark", ScreenBenchmark.from_dict)


def equal_weight_top(n: int = 60) -> ScreenBenchmark:
    """Equal-weight the `n` largest names: a stand-in when no index series is loaded."""
    return ScreenBenchmark(backtest.benchmark_screen(n))


def price_benchmark_message(index: str, what: str = "this comparison") -> str:
    """The plain-language refusal for comparing total-return portfolios with a price index."""
    return (f"Refused: {what} would compare total-return portfolios with {index!r}, a price index (dividends left "
            f"out), which overstates excess return by about the index's dividend yield (roughly 2 points a year "
            f"for the S&P 500). Fix it one of three ways: add the dividends back with "
            f"IndexBenchmark({index!r}, dividend_yield=0.02) (REST/MCP: benchmark={{\"index\": \"{index}\", "
            f"\"dividend_yield\": 0.02}}); use a total-return series as the benchmark; or pass "
            f"allow_price_benchmark=True (Lab(allow_price_benchmark=True), REST/MCP `allow_price_benchmark`) to "
            f"keep the price index, knowing the result is flattered.")


def parse_benchmark(b):
    """A benchmark from a request-style string: "topN" -> equal-weight top-N ScreenBenchmark, any other
    string -> IndexBenchmark of that index / ticker, a dict {"index", "dividend_yield", "mer", "cost_bps",
    "kind", "name"} -> IndexBenchmark. Benchmark objects pass through."""
    if isinstance(b, dict):
        extra = sorted(set(b) - {"index", "dividend_yield", "mer", "cost_bps", "kind", "name"})
        if extra or not isinstance(b.get("index"), str) or not b["index"].strip():
            raise ValueError("a benchmark object needs a string `index` and may have dividend_yield, mer, cost_bps, "
                             "kind, name" + (f"; unknown keys {extra}" if extra else ""))
        return IndexBenchmark(**{**b, "index": b["index"].strip()})
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
