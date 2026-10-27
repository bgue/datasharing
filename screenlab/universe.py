"""Universes: which tickers are eligible on a date.

A `Universe` is a small immutable spec. `bind(fund, prices)` returns a copy that can answer
`members(asof)` from data; `restrict(view, asof)` cuts an already-built snapshot (what `Market.view`
and the screens' size cuts use, so a bound universe is not needed for that).

AllListed        every name that trades (today's behaviour; `restrict` returns the view untouched).
TopN(n, by)      the n largest by a column (default market_cap); ties keep the first, as DataFrame.nlargest.
Membership(t)    date ranges per ticker (index history): ticker, start, end; start inclusive, end
                 exclusive (the first day the name is no longer a member), end NaT = still a member.
Filtered(b, p)   `b` further restricted by predicate `p(frame) -> boolean mask` (e.g. common stocks only
                 via a `security_type` column).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class Universe(Protocol):
    def bind(self, fund: pd.DataFrame, prices: pd.DataFrame) -> "Universe": ...
    def members(self, asof) -> pd.Index: ...
    def restrict(self, view: pd.DataFrame, asof=None) -> pd.DataFrame: ...
    def fingerprint(self) -> str: ...


def _snapshot(fund: pd.DataFrame, asof) -> pd.DataFrame:
    """Latest reported row per ticker on or before `asof`."""
    f = fund[fund["date"] <= pd.Timestamp(asof)]
    return f.sort_values("date", kind="stable").groupby("ticker").tail(1).set_index("ticker")


def _trading(prices: pd.DataFrame | None, asof) -> pd.Index | None:
    """Tickers with a price on the last trading day on or before `asof` (None when no prices bound)."""
    if prices is None:
        return None
    i = prices.index.searchsorted(pd.Timestamp(asof), side="right") - 1
    if i < 0:
        return prices.columns[:0]
    return prices.columns[prices.iloc[i].notna().to_numpy()]


class _Base:
    _fund: pd.DataFrame | None = None
    _prices: pd.DataFrame | None = None

    def bind(self, fund, prices):
        out = replace(self) if hasattr(self, "__dataclass_fields__") else self
        object.__setattr__(out, "_fund", fund)
        object.__setattr__(out, "_prices", prices)
        return out

    def _need_data(self):
        if self._fund is None and self._prices is None:
            raise ValueError(f"{type(self).__name__} is not bound to data: call .bind(fund, prices) first")

    def restrict(self, view, asof=None):
        return view.loc[view.index.isin(self.members(asof))]

    def fingerprint(self) -> str:
        return repr(self)


@dataclass(frozen=True, repr=True)
class AllListed(_Base):
    """Every name that trades on the date."""

    def members(self, asof) -> pd.Index:
        self._need_data()
        t = _trading(self._prices, asof)
        if t is not None:
            return t
        return pd.Index(_snapshot(self._fund, asof).index)

    def restrict(self, view, asof=None):
        return view

    def fingerprint(self) -> str:
        return "AllListed()"


@dataclass(frozen=True, repr=True)
class TopN(_Base):
    """The `n` largest names by column `by` (default market_cap)."""
    n: int
    by: str = "market_cap"

    def cut(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The size cut on a snapshot frame: exactly `frame.nlargest(n, by)`."""
        return frame.nlargest(self.n, self.by)

    def restrict(self, view, asof=None):
        return self.cut(view)

    def members(self, asof) -> pd.Index:
        """Top-n of the latest reported `by` per name that trades on `asof` (reported values, not marked to market)."""
        self._need_data()
        s = _snapshot(self._fund, asof)
        t = _trading(self._prices, asof)
        if t is not None:
            s = s[s.index.isin(t)]
        return pd.Index(self.cut(s).index)

    def fingerprint(self) -> str:
        return f"TopN({self.n!r}, {self.by!r})"


@dataclass(frozen=True, repr=False, eq=False)
class Membership(_Base):
    """Index history: `table` has columns ticker, start, end (end NaT/None = still a member)."""
    table: pd.DataFrame

    def __post_init__(self):
        t = self.table.copy()
        missing = {"ticker", "start", "end"} - set(t.columns)
        if missing:
            raise ValueError(f"Membership table needs columns ticker, start, end; missing {sorted(missing)}")
        t["start"] = pd.to_datetime(t["start"])
        t["end"] = pd.to_datetime(t["end"])
        object.__setattr__(self, "table", t.reset_index(drop=True))

    def members(self, asof) -> pd.Index:
        d = pd.Timestamp(asof)
        t = self.table
        on = t[(t["start"] <= d) & (t["end"].isna() | (d < t["end"]))]
        out = pd.Index(on["ticker"].drop_duplicates())
        trading = _trading(self._prices, d)
        return out[out.isin(trading)] if trading is not None else out

    def fingerprint(self) -> str:
        t = self.table.sort_values(["ticker", "start"]).astype(str)
        return "Membership(" + hashlib.sha256(t.to_csv(index=False).encode()).hexdigest()[:16] + ")"

    def __repr__(self):
        return f"Membership({len(self.table)} ranges)"


@dataclass(frozen=True, repr=False, eq=False)
class Filtered(_Base):
    """`base` restricted by `predicate(frame) -> boolean mask` (aligned to the frame's index)."""
    base: Universe
    predicate: Callable[[pd.DataFrame], pd.Series]

    def bind(self, fund, prices):
        out = Filtered(self.base.bind(fund, prices), self.predicate)
        object.__setattr__(out, "_fund", fund)
        object.__setattr__(out, "_prices", prices)
        return out

    def members(self, asof) -> pd.Index:
        self._need_data()
        keep = self.base.members(asof)
        s = _snapshot(self._fund, asof)
        s = s[s.index.isin(keep)]
        return pd.Index(s.index[np.asarray(self.predicate(s), dtype=bool)])

    def restrict(self, view, asof=None):
        v = self.base.restrict(view, asof)
        return v[np.asarray(self.predicate(v), dtype=bool)]

    def fingerprint(self) -> str:
        p = self.predicate
        return f"Filtered({self.base.fingerprint()}, {getattr(p, '__module__', '')}.{getattr(p, '__qualname__', repr(p))})"

    def __repr__(self):
        return f"Filtered({self.base!r}, {getattr(self.predicate, '__qualname__', self.predicate)})"
