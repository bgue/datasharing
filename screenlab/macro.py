from __future__ import annotations

import copy
import hashlib

import numpy as np
import pandas as pd

from .serial import TAG, Unpersistable, register_tag, unp

DAYS_PER_YEAR = 365.25
BOC_SERIES = {"FXUSDCAD": "fx", "V80691311": "prime", "V41690973": "cpi"}   # BoC Valet id -> name
_PCT_BOC = {"prime"}                                                         # quoted in percent


def pad_align(s: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Pad-fill `s` onto `index`: an observation dated before the index starts still carries in."""
    s = s.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.reindex(s.index.union(index)).ffill().reindex(index)


class MacroSeries:
    """Named macro series (columns of `frame`): cpi (index level), fx (home currency per unit of the
    portfolio currency), prime / rf (annual rates as decimals) plus any extras. Pad-filled by `align`."""

    def __init__(self, frame: pd.DataFrame):
        f = frame.copy()
        f.index = pd.DatetimeIndex(f.index)
        self.frame = f.sort_index()

    # ---------------------------------------------------------------- constructors
    @classmethod
    def from_frame(cls, df: pd.DataFrame, index: pd.DatetimeIndex | None = None) -> "MacroSeries":
        m = cls(df)
        return m.align(index) if index is not None else m

    @classmethod
    def from_csv(cls, path, index: pd.DatetimeIndex | None = None) -> "MacroSeries":
        """Long format `date,series,value` (one row per observation)."""
        long = pd.read_csv(path, parse_dates=["date"])
        wide = long.pivot_table(index="date", columns="series", values="value", aggfunc="last")
        wide.columns.name = None
        return cls.from_frame(wide, index)

    @classmethod
    def from_boc_csv(cls, path, index: pd.DatetimeIndex | None = None) -> "MacroSeries":
        """Bank of Canada adapter output: FXUSDCAD -> fx, V80691311 -> prime (percent -> decimal),
        V41690973 -> cpi; other series keep their id."""
        m = cls.from_csv(path)
        f = m.frame.rename(columns=BOC_SERIES)
        for c in _PCT_BOC & set(f.columns):
            f[c] = f[c] / 100.0
        return cls.from_frame(f, index)

    @classmethod
    def from_statsmodels_macrodata(cls, index: pd.DatetimeIndex | None = None) -> "MacroSeries":
        """Quarterly US macrodata (cpi level, tbilrate percent -> rf decimal), dated at quarter end."""
        from statsmodels.datasets import macrodata
        d = macrodata.load_pandas().data
        qe = pd.PeriodIndex.from_fields(year=d["year"].astype(int), quarter=d["quarter"].astype(int), freq="Q")
        f = pd.DataFrame({"cpi": d["cpi"].to_numpy(), "rf": d["tbilrate"].to_numpy() / 100.0},
                         index=qe.to_timestamp(how="end").normalize())
        return cls.from_frame(f, index)

    # ---------------------------------------------------------------- access
    @property
    def names(self) -> list[str]:
        return [c for c in self.frame.columns if self.frame[c].notna().any()]

    def get(self, name: str) -> pd.Series | None:
        return self.frame[name] if name in self.names else None

    cpi = property(lambda self: self.get("cpi"))
    fx = property(lambda self: self.get("fx"))
    prime = property(lambda self: self.get("prime"))
    rf = property(lambda self: self.get("rf"))

    def align(self, index: pd.DatetimeIndex) -> "MacroSeries":
        """Copy pad-filled onto `index` (e.g. `Market.index` or a curve's own index)."""
        index = pd.DatetimeIndex(index)
        return MacroSeries(pd.DataFrame({c: pad_align(self.frame[c], index) for c in self.names}, index=index))


    # ---------------------------------------------------------------- serialization (by dataset reference)
    _serial_tag = "MacroSeries"

    def to_dict(self) -> dict:
        ref = getattr(self, "_ref", None)
        if ref is None:
            return unp("MacroSeries has no dataset reference (tag it with runspec.macro_ref)")
        return {TAG: "MacroSeries", "ref": ref, "hash": _hash_frame(self.frame),
                **({"align": True} if getattr(self, "_align", False) else {})}

    @classmethod
    def from_dict(cls, d: dict) -> "MacroSeries":
        m = resolve_macro(d["ref"])
        if d.get("align"):
            m = copy.copy(m)
            m._align = True                                # execute_spec aligns it to the lab's market index
        return m


register_tag("MacroSeries", MacroSeries.from_dict)

# -------------------------------------------------------------------- references
MACRO_LOADERS: dict = {}


def register_macro(name: str, loader) -> None:
    """Make `name` resolvable: `loader()` returns the MacroSeries (called when a spec is rebuilt)."""
    MACRO_LOADERS[name] = loader


def macro_ref(macro, name: str):
    """Tag a MacroSeries with the reference its specs will carry (and register `macro` as its loader if
    nothing is registered under that name yet). Returns `macro`."""
    macro._ref = name
    MACRO_LOADERS.setdefault(name, lambda m=macro: m)
    return macro


def resolve_macro(ref: str):
    if ref in MACRO_LOADERS:
        return MACRO_LOADERS[ref]()
    if ref == "statsmodels":
        m = MacroSeries.from_statsmodels_macrodata()
        m._ref = ref
        return m
    raise Unpersistable(f"unknown macro reference {ref!r}; register it with runspec.register_macro")


def _hash_frame(frame) -> str:
    h = hashlib.sha256(repr(list(map(str, frame.columns))).encode())
    h.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    return h.hexdigest()[:20]


# -------------------------------------------------------------------- helpers
def accrual_curve(rate: pd.Series, spread: float = 0.0) -> pd.Series:
    """Growth of 1 at an annual rate series: step i accrues (1 + rate[i-1] + spread)**(dt/365.25).
    Works on any index frequency (daily accrual when the index is daily); leading gaps are back-filled."""
    r = rate.ffill().bfill().to_numpy(float)
    dt = np.diff(rate.index.to_numpy("datetime64[D]").astype(float)) / DAYS_PER_YEAR
    step = (1.0 + r[:-1] + spread) ** dt
    return pd.Series(np.concatenate([[1.0], np.cumprod(step)]), index=rate.index)


def cpi_deflator(cpi: pd.Series) -> pd.Series:
    """cpi[t] / cpi[t0]: the price level relative to the first observation."""
    c = cpi.dropna()
    return c / c.iloc[0]
