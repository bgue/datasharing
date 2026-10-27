from __future__ import annotations

import numpy as np
import pandas as pd

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
