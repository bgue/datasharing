"""Datasets: prices + fundamentals + indices + universe, with a data card that says what they are.

`Dataset` is frozen and built by a `DatasetBuilder` (`build(**opts) -> Dataset`):

SyntheticBuilder     the synthetic market (`data.synthetic_market`).
BundledSP500Builder  20 S&P 500 names from the installed skfolio wheel (benchmark is a PRICE index).
CSVBuilder           fundamentals / prices / indices csv files (`data.load_csv`).
SECBuilder           SEC Financial Statement Data Sets zips (+ a prices frame) -> fundamentals.
BoCMacro             Bank of Canada Valet json -> `MacroSeries` (macro data, not a Dataset).

`DataCard` is the manifest every report prints: name, source, span, names, benchmark kind (price vs
total_return), point-in-time flag, delisted names present, and a survivorship score.

Survivorship score = CAGR of an equal-weight buy-and-hold of the universe's names on the first date
(a delisted name stays at its last price) minus the CAGR of the benchmark over the span, per year as a
decimal. Near zero for a complete dataset; clearly positive when the names are survivors picked with
hindsight (the equal-weight basket beats the index it came from).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .universe import AllListed, Universe

SURVIVORSHIP_WARN = 0.02          # per year: above this the report warns
KINDS = ("price", "total_return")


def _cagr(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 2 or s.iloc[0] <= 0:
        return float("nan")
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else float("nan")


def survivorship_score(prices: pd.DataFrame, benchmark: pd.Series | None, names=None) -> float:
    """CAGR(equal-weight buy-and-hold of `names`, default all names trading on the first date)
    minus CAGR(benchmark), over the dates where the benchmark exists. NaN without a benchmark."""
    if benchmark is None or not len(prices):
        return float("nan")
    b = benchmark.reindex(benchmark.index.union(prices.index)).ffill().reindex(prices.index).dropna()
    if len(b) < 2:
        return float("nan")
    px = prices.loc[b.index[0]:]
    held = px.iloc[0].notna()
    if names is not None:
        held &= px.columns.isin(names)
    if not held.any():
        return float("nan")
    rel = px.loc[:, held].ffill() / px.loc[:, held].iloc[0]
    ew = rel.mean(axis=1)
    return _cagr(ew) - _cagr(b)


@dataclass(frozen=True)
class DataCard:
    name: str
    source: str
    start: str
    end: str
    n_names: int
    benchmark: str | None                 # the dataset's default benchmark (first index) or None
    benchmark_kind: str | None            # "price" | "total_return" (None: no benchmark)
    pit: bool | None                      # fundamentals point in time; None = not applicable (no fundamentals)
    delisted_included: bool
    n_delisted: int
    survivorship_score: float             # per year, decimal; NaN without a benchmark
    currency: str = "n/a"
    licence: str = ""
    universe: str = "AllListed()"
    index_kinds: dict = field(default_factory=dict, compare=False)   # index name -> kind

    @property
    def span(self) -> str:
        return f"{self.start[:10]}..{self.end[:10]}"

    def for_benchmark(self, name: str | None, kind: str | None = None, score: float | None = None) -> "DataCard":
        kind = kind or self.index_kinds.get(name, "total_return" if name else None)
        return replace(self, benchmark=name, benchmark_kind=kind,
                       survivorship_score=self.survivorship_score if score is None else score)

    def line(self) -> str:
        pit = {True: "yes", False: "no", None: "n/a"}[self.pit]
        sc = "n/a" if self.survivorship_score != self.survivorship_score else f"{self.survivorship_score:+.1%}/yr"
        dl = f"yes ({self.n_delisted})" if self.delisted_included else "no"
        bm = f"{self.benchmark} ({self.benchmark_kind})" if self.benchmark else "none"
        return (f"Data: {self.name}, {self.span}, {self.n_names} names, benchmark {bm}, PIT {pit}, "
                f"delisted {dl}, survivorship {sc}")

    def warnings(self) -> list[str]:
        out = []
        if self.benchmark_kind == "price":
            out.append(f"WARNING: benchmark {self.benchmark} is a price index (dividends left out) but the "
                       f"portfolios are total return, so excess return is overstated by about the index's "
                       f"dividend yield. Use a total-return benchmark.")
        sc = self.survivorship_score
        if sc == sc and sc > SURVIVORSHIP_WARN:
            out.append(f"WARNING: survivorship score {sc:+.1%}/yr: an equal-weight basket of this dataset's "
                       f"names beat its benchmark by that much, so it probably holds survivors chosen with "
                       f"hindsight and results are likely flattered.")
        return out

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("name", "source", "start", "end", "n_names", "benchmark",
                                            "benchmark_kind", "pit", "delisted_included", "n_delisted",
                                            "survivorship_score", "currency", "licence", "universe")}
        if d["survivorship_score"] != d["survivorship_score"]:
            d["survivorship_score"] = None
        d["warnings"] = self.warnings()
        return d


@dataclass(frozen=True, eq=False)
class Dataset:
    prices: pd.DataFrame                  # total-return index, wide (date x ticker)
    fundamentals: pd.DataFrame            # long, point in time
    indices: pd.DataFrame                 # benchmark series by name (may have no columns)
    universe: Universe                    # bound to this dataset's data on construction
    manifest: DataCard

    def __post_init__(self):
        object.__setattr__(self, "universe", self.universe.bind(self.fundamentals, self.prices))

    @property
    def card(self) -> DataCard:
        return self.manifest

    @classmethod
    def from_frames(cls, fundamentals, prices, indices=None, universe: Universe | None = None, *,
                    name="custom", source="in-memory frames", index_kinds: dict | None = None,
                    pit: bool | None = True, currency="n/a", licence="") -> "Dataset":
        """Build a Dataset and compute its card. `index_kinds` maps index name -> "price"|"total_return"
        (unlisted indices count as total_return)."""
        if not prices.index.is_monotonic_increasing:
            prices = prices.sort_index()
        idx = indices if indices is not None else pd.DataFrame(index=prices.index)
        kinds = {c: (index_kinds or {}).get(c, "total_return") for c in idx.columns}
        bad = sorted(set(kinds.values()) - set(KINDS))
        if bad:
            raise ValueError(f"index kind must be one of {KINDS}, got {bad}")
        uni = universe if universe is not None else AllListed()
        n_del = int((prices.index[-1] > prices.apply(lambda c: c.last_valid_index() or prices.index[0])).sum()) \
            if len(prices) else 0
        card = DataCard(name=name, source=source, start=str(prices.index[0].date()), end=str(prices.index[-1].date()),
                        n_names=int(prices.shape[1]), benchmark=None, benchmark_kind=None, pit=pit,
                        delisted_included=n_del > 0, n_delisted=n_del, survivorship_score=float("nan"),
                        currency=currency, licence=licence, universe=uni.fingerprint(), index_kinds=kinds)
        ds = cls(prices, fundamentals, idx, uni, card)
        first = idx.columns[0] if len(idx.columns) else None
        return ds._with_card(ds.card_for(first)) if first else ds

    def _with_card(self, card: DataCard) -> "Dataset":
        return Dataset(self.prices, self.fundamentals, self.indices, self.universe, card)

    def _series(self, name: str) -> pd.Series | None:
        if name in self.indices:
            return self.indices[name]
        if name in self.prices:
            return self.prices[name]
        return None

    def card_for(self, benchmark: str | None) -> DataCard:
        """The card with `benchmark` as the reference: its kind and the survivorship score against it
        (names in the universe on the first date)."""
        s = self._series(benchmark) if benchmark else None
        if s is None:
            return self.manifest.for_benchmark(benchmark)
        px = self.prices
        start = max(px.index[0], s.first_valid_index() or px.index[0])
        try:
            names = self.universe.members(start)
        except Exception:                      # a universe that cannot answer: fall back to all names
            names = None
        score = survivorship_score(px, s, names)
        kind = self.manifest.index_kinds.get(benchmark, "total_return")
        return self.manifest.for_benchmark(benchmark, kind, score)

    def with_index(self, name: str, series: pd.Series, kind: str = "total_return") -> "Dataset":
        """A copy with one more index series (e.g. the built passive index) and the card recomputed
        against it when the dataset had no benchmark."""
        idx = self.indices.copy()
        idx[name] = series
        card = replace(self.manifest, index_kinds={**self.manifest.index_kinds, name: kind})
        ds = Dataset(self.prices, self.fundamentals, idx, self.universe, card)
        return ds._with_card(ds.card_for(card.benchmark or name))

    def with_universe(self, universe: Universe) -> "Dataset":
        card = replace(self.manifest, universe=universe.fingerprint())
        ds = Dataset(self.prices, self.fundamentals, self.indices, universe, card)
        return ds._with_card(ds.card_for(card.benchmark))


@runtime_checkable
class DatasetBuilder(Protocol):
    def build(self, **opts) -> Dataset: ...


# --------------------------------------------------------------------------- builders
@dataclass(frozen=True)
class SyntheticBuilder:
    n_stocks: int = 250
    years: int = 25
    freq: str = "B"
    seed: int = 7
    start: str = "2000-01-03"

    def build(self, **opts) -> Dataset:
        o = replace(self, **opts)
        from .data import synthetic_market
        fund, prices, indices = synthetic_market(n_stocks=o.n_stocks, start=o.start, years=o.years,
                                                 freq=o.freq, seed=o.seed)
        return Dataset.from_frames(
            fund, prices, indices, name="synthetic",
            source=f"data.synthetic_market(n_stocks={o.n_stocks}, years={o.years}, freq={o.freq!r}, seed={o.seed})",
            index_kinds={"SYN_CAPWT": "total_return"}, pit=True, currency="n/a",
            licence="synthetic, no restrictions")


@dataclass(frozen=True)
class BundledSP500Builder:
    path_prices: str | None = None
    path_index: str | None = None

    def build(self, **opts) -> Dataset:
        o = replace(self, **opts)
        from .realdata import bundled
        prices = bundled.sp500_prices(o.path_prices)
        idx = bundled.sp500_index(o.path_index)
        fund = bundled.price_only_fundamentals(prices, bundled.SECTORS)
        return Dataset.from_frames(
            fund, prices, idx, name="sp500_bundled", source="skfolio sp500_dataset.csv.gz + sp500_index.csv.gz",
            index_kinds={c: "price" for c in idx.columns}, pit=None, currency="USD",
            licence="sample data shipped with skfolio; check its licence before redistributing")


@dataclass(frozen=True)
class CSVBuilder:
    fundamentals: str
    prices: str
    indices: str | None = None
    name: str = "csv"
    index_kind: str = "total_return"      # indices.csv holds a `tri` column: total return unless you say otherwise
    pit: bool | None = True               # you promise the fundamentals already carry their reporting lag
    currency: str = "n/a"
    licence: str = ""

    def build(self, **opts) -> Dataset:
        o = replace(self, **opts)
        from .data import load_csv
        fund, prices, idx = load_csv(o.fundamentals, o.prices, o.indices)
        return Dataset.from_frames(
            fund, prices, idx, name=o.name, source=f"csv: {o.fundamentals}, {o.prices}"
            + (f", {o.indices}" if o.indices else ""),
            index_kinds={c: o.index_kind for c in (idx.columns if idx is not None else [])},
            pit=o.pit, currency=o.currency, licence=o.licence)


@dataclass(frozen=True)
class SECBuilder:
    """SEC FSDS quarterly zips -> point-in-time fundamentals (date = filing date), joined to `prices`
    (date x ticker total-return frame you supply; the SEC data has no prices)."""
    zips: tuple
    tickers_map: dict                     # cik -> ticker
    prices: pd.DataFrame
    indices: pd.DataFrame | None = None
    forms: tuple = ("10-K",)
    name: str = "sec_fsds"
    index_kind: str = "total_return"

    def fundamentals(self) -> pd.DataFrame:
        from .data import add_ratios
        from .realdata import sec_fsds
        subs, nums = zip(*(sec_fsds.read_zip(z) for z in self.zips))
        return add_ratios(sec_fsds.to_fundamentals(pd.concat(subs, ignore_index=True),
                                                   pd.concat(nums, ignore_index=True),
                                                   self.tickers_map, self.forms))

    def build(self, **opts) -> Dataset:
        o = replace(self, **opts)
        idx = o.indices
        return Dataset.from_frames(
            o.fundamentals(), o.prices, idx, name=o.name, source=f"SEC FSDS {', '.join(map(str, o.zips))}",
            index_kinds={c: o.index_kind for c in (idx.columns if idx is not None else [])},
            pit=True, currency="USD", licence="SEC public data; prices supplied by the caller")


@dataclass(frozen=True)
class BoCMacro:
    """Bank of Canada Valet json saved by `boc_valet.fetch` -> `MacroSeries` (fx, prime as a decimal, cpi).
    Macro series are not a Dataset, so `build` returns the MacroSeries."""
    path: str

    def build(self, index: pd.DatetimeIndex | None = None, **opts):
        from .macro import BOC_SERIES, MacroSeries, _PCT_BOC
        from .realdata import boc_valet
        f = boc_valet.load(replace(self, **opts).path).rename(columns=BOC_SERIES)
        for c in _PCT_BOC & set(f.columns):
            f[c] = f[c] / 100.0
        return MacroSeries.from_frame(f, index)
