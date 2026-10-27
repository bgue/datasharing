"""JSON-safe conversion of results: tables as {"columns": [...], "rows": [[...], ...]}, curves as
[[iso_date, value], ...] downsampled to at most `max_points`, NaN/inf as null. Used by every operation's
`to_json` and by the servers."""
from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

DEFAULT_MAX_POINTS = 500


# ------------------------------------------------------------------ JSON serializers
def scalar(x):
    """One value -> JSON-safe Python (NaN/inf/NaT -> None, timestamps -> ISO strings)."""
    if x is None or x is pd.NaT:
        return None
    if isinstance(x, (pd.Timestamp, datetime, date, np.datetime64)):
        t = pd.Timestamp(x)
        return None if pd.isna(t) else t.isoformat()[:10] if (t == t.normalize()) else t.isoformat()
    if isinstance(x, (pd.Timedelta, np.timedelta64)):
        return None if pd.isna(x) else pd.Timedelta(x).days
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, (str,)):
        return x
    return str(x)


def table(df: pd.DataFrame, index: bool = True) -> dict:
    """DataFrame -> {"columns", "rows"}; the index (or each level of a MultiIndex) becomes leading column(s)."""
    d = df
    if index:
        names = [n if n is not None else "index" for n in d.index.names]
        d = d.reset_index()
        d.columns = [str(c) for c in names] + [str(c) for c in d.columns[len(names):]]
    else:
        d = d.copy()
        d.columns = [str(c) for c in d.columns]
    rows = [[scalar(v) for v in r] for r in d.astype(object).itertuples(index=False, name=None)]
    return {"columns": list(d.columns), "rows": rows}


def series_table(s: pd.Series, key: str = "key", value: str = "value") -> dict:
    return {"columns": [key, value], "rows": [[scalar(k), scalar(v)] for k, v in s.items()]}


def downsample_index(n: int, max_points: int) -> np.ndarray:
    if max_points is None or max_points <= 0 or n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def curve(s: pd.Series, max_points: int = DEFAULT_MAX_POINTS) -> list:
    """Series -> [[iso_date, value], ...], at most `max_points` evenly spaced (first and last kept)."""
    s = s.dropna() if s.dtype != object else s
    pos = downsample_index(len(s), max_points)
    return [[scalar(s.index[i]), scalar(s.iloc[i])] for i in pos]


def curves(df, max_points: int = DEFAULT_MAX_POINTS) -> dict:
    if isinstance(df, pd.Series):
        df = df.to_frame(df.name or "value")
    return {str(c): curve(df[c], max_points) for c in df.columns}


def jsonify(x, max_points: int = DEFAULT_MAX_POINTS):
    """Best-effort conversion of results (DataFrames, Series, dataclasses, dicts, scalars) to JSON."""
    if isinstance(x, pd.DataFrame):
        return table(x)
    if isinstance(x, pd.Series):
        if isinstance(x.index, pd.DatetimeIndex) and len(x) > 50:
            return curve(x, max_points)
        return series_table(x)
    if isinstance(x, dict):
        return {str(k): jsonify(v, max_points) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonify(v, max_points) for v in x]
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: jsonify(getattr(x, f.name), max_points) for f in dataclasses.fields(x)}
    return scalar(x)

