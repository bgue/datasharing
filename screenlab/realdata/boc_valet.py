"""Bank of Canada Valet API adapter (FX, rates, CPI).

    GET https://www.bankofcanada.ca/valet/observations/{SERIES}/json?start_date=&end_date=
    {"observations": [{"d": "2024-01-02", "FXUSDCAD": {"v": "1.3316"}, ...}]}

`fetch` saves the raw JSON (needs network); `load` parses a saved file offline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE = "https://www.bankofcanada.ca/valet/observations"
SERIES = {"FXUSDCAD": "USD/CAD daily", "V80691311": "prime rate, daily",
          "V41690973": "CPI all-items, monthly", "V39079": "overnight target rate"}


def fetch(series, start: str | None = None, end: str | None = None, cache_path="boc_valet.json") -> Path:
    """Download one series or a list of them and save the JSON to `cache_path`."""
    from urllib.parse import urlencode
    from urllib.request import urlopen
    names = ",".join([series] if isinstance(series, str) else series)
    q = urlencode({k: v for k, v in (("start_date", start), ("end_date", end)) if v})
    with urlopen(f"{BASE}/{names}/json" + (f"?{q}" if q else ""), timeout=60) as r:
        body = r.read()
    json.loads(body)                      # fail before writing a bad file
    path = Path(cache_path)
    path.write_bytes(body)
    return path


def parse(doc: dict) -> pd.DataFrame:
    """Valet JSON dict -> DataFrame indexed by date, one float column per series."""
    rows = {}
    for ob in doc.get("observations", []):
        vals = {}
        for k, v in ob.items():
            if k == "d":
                continue
            try:
                vals[k] = float(v["v"])
            except (KeyError, TypeError, ValueError):   # missing / blank observation
                vals[k] = float("nan")
        rows[pd.Timestamp(ob["d"])] = vals
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index.name = "date"
    return df


def load(cache_path) -> pd.DataFrame:
    """Read a JSON file saved by `fetch` (or the raw API response)."""
    return parse(json.loads(Path(cache_path).read_text()))


def to_macro_csv(df: pd.DataFrame, out_path) -> Path:
    """Write long-format `date,series,value` (NaN rows dropped)."""
    long = (df.rename_axis("date").reset_index().melt("date", var_name="series", value_name="value")
            .dropna(subset=["value"]).sort_values(["series", "date"]))
    long.to_csv(out_path, index=False)
    return Path(out_path)


def builder(cache_path="boc_valet.json"):
    """Builder-style entry point: `BoCMacro` over a saved Valet json (`build()` -> MacroSeries)."""
    from ..dataset import BoCMacro
    return BoCMacro(str(cache_path))
