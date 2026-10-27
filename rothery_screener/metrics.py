from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(curve: pd.Series, bench: pd.Series | None = None, rf: float = 0.0) -> dict:
    """Stats for an equity curve of any frequency (value series, any starting level)."""
    w = curve.dropna()
    w = w / w.iloc[0]
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    ppy = (len(w) - 1) / yrs
    r = w.pct_change().dropna()
    year_ago = w.reindex(w.index - pd.Timedelta(days=365), method="pad").to_numpy()
    roll12 = w.to_numpy() / year_ago - 1
    out = {
        "CAGR": w.iloc[-1] ** (1 / yrs) - 1,
        "Vol": r.std() * np.sqrt(ppy),
        "Sharpe": (r.mean() * ppy - rf) / (r.std() * np.sqrt(ppy)) if r.std() > 0 else np.nan,
        "MaxDD": (w / w.cummax() - 1).min(),
        "Worst12m": np.nanmin(roll12) if np.isfinite(roll12).any() else np.nan,
    }
    if bench is not None:
        b = bench.reindex(w.index, method="pad")
        b = b / b.iloc[0]
        yearly = lambda x: x.groupby(x.index.year).last().pipe(lambda y: y / y.shift(1).fillna(1.0) - 1)
        out["BeatBenchYrs"] = (yearly(w) > yearly(b)).mean()
        out["Excess/yr"] = out["CAGR"] - (b.iloc[-1] ** (1 / yrs) - 1)
    return out
