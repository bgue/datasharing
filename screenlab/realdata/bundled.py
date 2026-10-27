"""Datasets that ship inside installed packages (skfolio, statsmodels): no network needed.

Adjusted close stands in for a total-return index. `sp500_lab()` wires the 20 bundled
names into a `Lab` with a price-only fundamentals table and four price-only screens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..api import Lab
from ..data import Market
from ..screens import Screen

SECTORS = {
    "AAPL": "Tech", "AMD": "Tech", "BAC": "Fin", "BBY": "Cons", "CVX": "Energy", "GE": "Ind",
    "HD": "Cons", "JNJ": "Health", "JPM": "Fin", "KO": "Staples", "LLY": "Health", "MRK": "Health",
    "MSFT": "Tech", "PEP": "Staples", "PFE": "Health", "PG": "Staples", "RRC": "Energy",
    "UNH": "Health", "WMT": "Staples", "XOM": "Energy",
}


def _read(loader: str, path):
    """Load via skfolio, or read the csv.gz directly from `path` when skfolio is absent."""
    if path is None:
        try:
            from skfolio import datasets
        except ImportError as e:
            raise ImportError("skfolio is not installed; pass path= to the bundled csv.gz") from e
        return getattr(datasets, loader)()
    return pd.read_csv(path, index_col=0, parse_dates=True)


def sp500_prices(path=None) -> pd.DataFrame:
    """Daily adjusted close, 20 US large caps, 1990-2022 (date x ticker)."""
    df = _read("load_sp500_dataset", path)
    df.index = pd.DatetimeIndex(df.index, name="date").astype("datetime64[ns]")
    return df.sort_index()


def sp500_index(path=None) -> pd.DataFrame:
    """Daily S&P 500 index level (price index, one column `SP500`)."""
    df = _read("load_sp500_index", path)
    df.index = pd.DatetimeIndex(df.index, name="date").astype("datetime64[ns]")
    return df.sort_index()


def factor_etfs(path=None) -> pd.DataFrame:
    """Daily prices of five factor ETFs (MTUM QUAL SIZE USMV VLUE), 2014-2022."""
    df = _read("load_factors_dataset", path)
    df.index = pd.DatetimeIndex(df.index, name="date").astype("datetime64[ns]")
    return df.sort_index()


def macro_us() -> pd.DataFrame:
    """US quarterly cpi and 3m T-bill rate (%), indexed by quarter-end, 1959Q1-2009Q3."""
    from statsmodels.datasets import macrodata
    d = macrodata.load_pandas().data
    q = pd.PeriodIndex([f"{int(y)}Q{int(k)}" for y, k in zip(d["year"], d["quarter"])], freq="Q")
    out = d[["cpi", "tbilrate"]].copy()
    out.index = pd.DatetimeIndex(q.end_time.normalize(), name="date")
    return out


# --- price-only screens ------------------------------------------------------
def momentum_12_1(s, n=10):
    """Top n by 12-1 month momentum among names with a full 12 months of history."""
    return s["mom_12_1"].dropna().nlargest(n).index.tolist()


def momentum_6m(s, n=10):
    """Top n by plain prior 6-month return (no skipped month)."""
    return s["mom_6m"].dropna().nlargest(n).index.tolist()


def momentum_12m(s, n=10):
    """Top n by plain prior 12-month return (no skipped month)."""
    return s["mom_12m"].dropna().nlargest(n).index.tolist()


def low_vol(s, n=10):
    """Bottom n by one-year annualized volatility."""
    return s["vol_1y"].dropna().nsmallest(n).index.tolist()


def price_screens(n: int = 5) -> dict:
    return {
        "momentum_12_1": Screen("momentum_12_1", momentum_12_1, n,
                                note="top n by 12-1 momentum (price only)"),
        "momentum_6m": Screen("momentum_6m", momentum_6m, n,
                              note="price-only version of the Globe's 6-month momentum portfolio (no size cut)"),
        "momentum_12m": Screen("momentum_12m", momentum_12m, n,
                               note="price-only version of the Globe's 12-month momentum portfolio (no size cut)"),
        "low_vol": Screen("low_vol", low_vol, n, note="lowest n by 1y volatility (price only)"),
    }


def price_only_fundamentals(prices: pd.DataFrame, sectors: dict | None = None) -> pd.DataFrame:
    """One row per ticker at its first valid price: ticker, date, price, sector, market_cap=NaN."""
    first = prices.apply(lambda c: c.first_valid_index())
    px = pd.Series({t: prices.at[d, t] for t, d in first.items() if pd.notna(d)})
    f = pd.DataFrame({"date": first.dropna(), "price": px}).rename_axis("ticker").reset_index()
    f["sector"] = f["ticker"].map(sectors or {}).fillna("Unknown")
    f["market_cap"] = np.nan
    return f


def sp500_lab(path_prices=None, path_index=None, n: int = 5, **kw) -> Lab:
    """Lab on the 20 bundled S&P 500 names, benchmarked on the S&P 500 index."""
    from ..dataset import BundledSP500Builder
    return Lab.from_dataset(BundledSP500Builder(path_prices, path_index).build(), screens=price_screens(n), **kw)
