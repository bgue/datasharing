from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(rets: pd.Series, bench: pd.Series | None = None, rf: float = 0.0) -> dict:
    """Stats for a monthly return series."""
    r = rets.dropna()
    yrs = len(r) / 12
    wealth = (1 + r).cumprod()
    out = {
        "CAGR": wealth.iloc[-1] ** (1 / yrs) - 1,
        "Vol": r.std() * np.sqrt(12),
        "Sharpe": (r.mean() * 12 - rf) / (r.std() * np.sqrt(12)) if r.std() > 0 else np.nan,
        "MaxDD": (wealth / wealth.cummax() - 1).min(),
        "Worst12m": (1 + r).rolling(12).apply(np.prod, raw=True).min() - 1,
    }
    if bench is not None:
        b = bench.reindex(r.index)
        ann = (1 + r).groupby(r.index.year).prod() - (1 + b).groupby(b.index.year).prod()
        out["BeatBenchYrs"] = (ann > 0).mean()
        out["Excess/yr"] = out["CAGR"] - ((1 + b).prod() ** (1 / yrs) - 1)
    return out
