"""D3: the point-in-time S&P 500 panel, 1968-2026, with per-stock cash dividends (LOCAL USE ONLY).

    screenlab-data panel            # or: make data-panel   -> data/panel/{prices.parquet, membership_intervals.parquet, no_trade_days.csv}
    lab = Lab.real_sp500(start="1990-01-01", freq="D")

Source: github.com/Johnbrick123/sp500-data release assets (CRSP 1968-2006 spliced to Yahoo / Tiingo / Quandl WIKI). No licence
is stated and part of it is CRSP-derived: the files are downloaded on demand into the git-ignored `data/panel/`, never
committed and never redistributed. `load()` cleans and reshapes them; `SP500PanelBuilder` turns the result into a `Dataset`.

What the panel has: close (split-adjusted, not dividend-adjusted), adj_close (dividend- and split-adjusted), cash dividends
per share on the ex-date, and S&P 500 membership intervals. What it lacks: shares outstanding, market cap, fundamentals.
So `market_cap` is NaN: size cuts (`TopN`) cannot rank and the value / buyback screens cannot run; `Lab.real_sp500` ships
panel versions of the registry screens whose size cut is dropped (the universe is already the S&P 500).

Cleaning (see `clean_rows`): rows with close <= 0 or adj_close <= 0 are dropped; a dividend above 25% of the close is dropped
(counted); a dividend identical to the previous dividend of the same ticker within 20 days is dropped (counted, first kept).
`dividend` = trailing-365-day sum of per-share dividends, `price` = close, `div_yield` = dividend / price; `prices` (the
total-return frame the library compounds) = adj_close, scaled to 100 at each name's first date.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

BASE_URL = "https://github.com/Johnbrick123/sp500-data/releases/download/data/"
FILES = ("prices.parquet", "membership_intervals.parquet", "no_trade_days.csv")
HOSTS = ["github.com", "release-assets.githubusercontent.com"]
PANEL_DIR = Path(__file__).resolve().parents[1] / "data" / "panel"
MAX_DIV_YIELD = 0.25                 # a dividend above this share of the close is a data error
DEDUPE_DAYS = 20                     # an identical dividend this close to the previous one is a duplicate row
TTM_DAYS = 365
NOTE_LOCAL = ("WARNING: local use only: unlicensed mixed-source panel (CRSP-derived 1968-2006, then Yahoo / Tiingo / "
              "Quandl WIKI); do not commit or redistribute the files or results that embed them.")
NOTE_NOFUND = ("WARNING: no market cap / fundamentals: the panel has prices and dividends only, so size cuts, value and "
               "buyback screens cannot run (use div_yield, price, momentum, volatility).")
NOTE_DELIST = ("NOTE: a name that leaves the index but keeps trading leaves the universe at the next rebalance and can still "
               "be sold at its price; a name whose prices end (delisted, merged) takes the library's delisting haircut "
               "(Config.delist_return, default -30%), which is harsh for acquisitions paid at a premium.")


class PanelMissing(FileNotFoundError):
    pass


def panel_dir(path=None) -> Path:
    return Path(path) if path else PANEL_DIR


# ------------------------------------------------------------------------------------------ download
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(dest=None, opener=None, progress=None, force: bool = False) -> dict:
    """Stream the three files into `dest` (default `data/panel/`), skipping files already there unless `force`;
    write `MANIFEST.json` with the SHA-256 of each. Returns the manifest dict. Raises on a network failure
    (`fetch_free` catches it per source). `opener(request, timeout=)` replaces urllib.request.urlopen (tests)."""
    root = panel_dir(dest)
    root.mkdir(parents=True, exist_ok=True)
    op = opener or urllib.request.urlopen
    rep = progress or (lambda f, msg="": None)
    entries = []
    for k, name in enumerate(FILES):
        path, url = root / name, BASE_URL + name
        rep(k / len(FILES), name)
        if force or not path.exists() or path.stat().st_size == 0:
            tmp = path.with_suffix(path.suffix + ".part")
            try:
                with op(urllib.request.Request(url), timeout=300) as r, open(tmp, "wb") as out:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                tmp.replace(path)
            except Exception as e:                      # blocked host, 403, DNS, timeout, proxy refusal ...
                tmp.unlink(missing_ok=True)
                from .fetch import NetworkError
                raise NetworkError(f"{type(e).__name__}: {e}", urlparse(url).hostname or "") from e
        entries.append({"file": name, "bytes": path.stat().st_size, "sha256": _sha256(path), "url": url})
    man = {"source": "Johnbrick123/sp500-data release `data`", "licence": "none stated; partly CRSP-derived: LOCAL USE ONLY, "
           "never commit or redistribute", "fetched": pd.Timestamp.now("UTC").isoformat(timespec="seconds"), "files": entries}
    (root / "MANIFEST.json").write_text(json.dumps(man, indent=2))
    rep(1.0, "done")
    return man


def fetch_source(m, root, opener, report) -> None:
    """Hook for `fetch.fetch_free(sources=("panel",))`: download into `<root>/panel/` and record the files on `m`."""
    from .fetch import _record
    man = download(Path(root) / "panel", opener=opener, progress=report)
    for e in man["files"]:
        _record(m, "panel", Path(root) / "panel" / e["file"], "ok")


def available(dest=None) -> bool:
    root = panel_dir(dest)
    return all((root / f).exists() for f in ("prices.parquet", "membership_intervals.parquet"))


# ------------------------------------------------------------------------------------------ cleaning
def clean_rows(df: pd.DataFrame, max_div_yield: float = MAX_DIV_YIELD, dedupe_days: int = DEDUPE_DAYS) -> tuple:
    """Clean a long frame (date, ticker, close, adj_close, dividends, ...): returns (frame, stats).

    * rows with close <= 0 / NaN or adj_close <= 0 / NaN are dropped (`rows_bad_price`);
    * a dividend > `max_div_yield` x close is set to 0 (`div_dropped_outlier`);
    * a dividend equal (to 1e-9 relative) to the previous dividend of the same ticker within `dedupe_days` days is set to 0,
      first kept (`div_dropped_duplicate`)."""
    n0 = len(df)
    ok = (df["close"] > 0) & (df["adj_close"] > 0)
    df = df if ok.all() else df[ok].copy()
    stats = {"rows_in": int(n0), "rows_bad_price": int(n0 - len(df))}
    div = df["dividends"].fillna(0.0).to_numpy(dtype=float)
    out = (div > max_div_yield * df["close"].to_numpy()) & (div > 0)
    stats["div_rows_in"] = int((div > 0).sum())
    stats["div_dropped_outlier"] = int(out.sum())
    div = np.where(out, 0.0, div)
    df["dividends"] = div
    d = df.loc[div > 0, ["ticker", "date", "dividends"]].sort_values(["ticker", "date"], kind="stable")
    prev_t, prev_d, prev_v = d["ticker"].shift(), d["date"].shift(), d["dividends"].shift()
    dup = (d["ticker"] == prev_t) & ((d["date"] - prev_d).dt.days <= dedupe_days) \
        & np.isclose(d["dividends"], prev_v, rtol=1e-9, atol=0.0)
    stats["div_dropped_duplicate"] = int(dup.sum())
    df.loc[dup.index[dup.to_numpy()], "dividends"] = 0.0
    stats["div_rows_kept"] = int((df["dividends"] > 0).sum())
    return df, stats


def ttm_dividends(div: pd.DataFrame, ttm_days: int = TTM_DAYS) -> pd.DataFrame:
    """Trailing `ttm_days`-calendar-day sum of per-share dividends on every date of the wide frame `div`
    (date x ticker, zeros where nothing was paid): cumulative sum minus the cumulative sum `ttm_days` earlier."""
    cum = div.fillna(0.0).cumsum().to_numpy()
    idx = div.index
    j = idx.searchsorted(idx - pd.Timedelta(days=ttm_days), side="right") - 1      # last bar on/before t - ttm_days
    base = np.where((j >= 0)[:, None], cum[np.clip(j, 0, None)], 0.0)
    return pd.DataFrame(cum - base, index=idx, columns=div.columns)


def _fam_name(x: str) -> str:
    if x.startswith("crsp"):
        return "crsp"
    if x.startswith("tiingo"):
        return "tiingo"
    if x in ("yahoo",):
        return "yahoo"
    return "other"


def _family(s: pd.Series) -> pd.Series:
    """Source label -> family (crsp / yahoo / tiingo / other), computed on the categories so 7M rows stay small."""
    s = s.astype("category")
    names = np.array([_fam_name(str(c)) for c in s.cat.categories], dtype=object)
    return pd.Series(names[s.cat.codes.to_numpy()], index=s.index)


def _era_mix(fam: pd.Series, dates: pd.Series) -> str:
    era = (dates.dt.year // 5 * 5)
    tab = pd.crosstab(era, fam, normalize="index")
    parts = []
    for e, row in tab.iterrows():
        top = row[row > 0.02].sort_values(ascending=False)
        parts.append(f"{e}-{e + 4}: " + ", ".join(f"{k} {v:.0%}" for k, v in top.items()))
    return "; ".join(parts)


def membership_table(path_dir=None) -> pd.DataFrame:
    t = pd.read_parquet(panel_dir(path_dir) / "membership_intervals.parquet")
    t = t.rename(columns=str.lower)[["ticker", "start", "end"]].copy()
    t["start"], t["end"] = pd.to_datetime(t["start"]), pd.to_datetime(t["end"])
    return t.reset_index(drop=True)


def member_mask(table: pd.DataFrame, index: pd.DatetimeIndex, columns) -> np.ndarray:
    """Boolean (dates x columns): the name is an S&P 500 member on that date (start inclusive, end exclusive)."""
    col = {c: i for i, c in enumerate(columns)}
    m = np.zeros((len(index), len(columns)), dtype=bool)
    for tk, s, e in zip(table["ticker"], table["start"], table["end"]):
        j = col.get(tk)
        if j is None:
            continue
        a = index.searchsorted(s, side="left")
        b = len(index) if pd.isna(e) else index.searchsorted(e, side="left")
        m[a:b, j] = True
    return m


# ------------------------------------------------------------------------------------------ load
@dataclass(frozen=True, eq=False)
class Panel:
    prices: pd.DataFrame            # total-return index (adj_close scaled to 100 at the first date), date x ticker
    close: pd.DataFrame             # unadjusted-for-dividends close
    fundamentals: pd.DataFrame      # month-end rows: date, ticker, price, dividend (TTM per share), sector, market_cap(NaN) + ratios
    membership: pd.DataFrame        # ticker, start, end
    source_mix: pd.DataFrame        # ticker -> share of rows by source family
    stats: dict = field(default_factory=dict)


def _cache_key(root: Path, start, end, freq) -> str:
    st = (root / "prices.parquet").stat()
    return hashlib.sha1(f"{start}|{end}|{freq}|{st.st_size}|{int(st.st_mtime)}|{TTM_DAYS}|{MAX_DIV_YIELD}|{DEDUPE_DAYS}|v1".encode()).hexdigest()[:12]


def load(start="1990-01-01", end=None, freq="D", dir=None, cache: bool = True,
         max_div_yield: float = MAX_DIV_YIELD, dedupe_days: int = DEDUPE_DAYS) -> Panel:
    """Load, clean and reshape the panel for `start`..`end` (`freq` "D" daily or "ME" month-end bars).
    Reads only the needed columns and dates with pyarrow; the cleaned wide frames are cached under `data/panel/cache/`."""
    if freq not in ("D", "ME"):
        raise ValueError(f"freq must be 'D' or 'ME', got {freq!r}")
    root = panel_dir(dir)
    if not available(root):
        raise PanelMissing(f"{root} has no panel files; download them with `screenlab-data panel` (make data-panel; "
                           f"needs github.com and release-assets.githubusercontent.com)")
    lo, hi = pd.Timestamp(start), (pd.Timestamp(end) if end else None)
    cdir = root / "cache" / _cache_key(root, lo.date(), hi.date() if hi is not None else None, freq)
    if cache and (cdir / "meta.json").exists():
        try:
            return _read_cache(cdir, root)
        except Exception:                                    # corrupt cache: rebuild
            pass
    t0 = time.time()
    import pyarrow.parquet as pq
    table = membership_table(root)
    table = table[(table["end"].isna() | (table["end"] > lo)) & ((table["start"] <= hi) if hi is not None else True)]
    flt = [("date", ">=", lo - pd.Timedelta(days=TTM_DAYS + 7))]
    if hi is not None:
        flt.append(("date", "<=", hi))
    tb = pq.read_table(root / "prices.parquet", columns=["date", "ticker", "close", "adj_close", "dividends", "source"], filters=flt)
    df = tb.to_pandas(strings_to_categorical=True)
    del tb
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    df = df[df["ticker"].isin(table["ticker"].unique())]
    df["ticker"] = df["ticker"].cat.remove_unused_categories()
    df, stats = clean_rows(df, max_div_yield, dedupe_days)
    inwin = df["date"] >= lo
    # source mix (window rows only)
    w = df[inwin]
    fam = _family(w["source"])
    mix = pd.crosstab(w["ticker"], fam, normalize="index").round(4)
    mix.index = mix.index.astype(str)
    era = _era_mix(fam, w["date"])
    # wide frames
    dates = pd.DatetimeIndex(np.unique(df["date"].to_numpy()))
    di, ti = dates.searchsorted(df["date"]), df["ticker"].cat.codes.to_numpy()
    cols = pd.Index(df["ticker"].cat.categories.astype(str), name="ticker")

    def wide(col, fill=np.nan):
        a = np.full((len(dates), len(cols)), fill)
        a[di, ti] = df[col].to_numpy()
        return pd.DataFrame(a, index=dates.rename("date"), columns=cols)
    adj, close, divw = wide("adj_close"), wide("close"), wide("dividends", 0.0)
    del df, w, di, ti
    last = adj.apply(pd.Series.last_valid_index)
    after = adj.index.to_numpy()[:, None] > last.to_numpy(dtype="datetime64[ns]")[None, :]
    adj, close = adj.ffill().mask(after), close.ffill().mask(after)         # fill interior gaps only, never past a name's last bar
    ttm = ttm_dividends(divw.fillna(0.0))
    sl = adj.index >= lo
    # month-end report rows (last trading day of each month) with price and TTM dividend
    adj, close, ttm = adj[sl], close[sl], ttm[sl]
    me = pd.Series(adj.index, index=adj.index).groupby(adj.index.to_period("M")).last().to_numpy()
    fr = pd.DataFrame({"price": close.loc[me].stack(), "dividend": ttm.loc[me].stack()}).reset_index()
    fr.columns = ["date", "ticker", "price", "dividend"]
    fr["sector"], fr["market_cap"] = "Unknown", np.nan
    from ..data import add_ratios
    fund = add_ratios(fr)
    # prices: scaled to 100 at the first valid bar
    first = adj.apply(lambda c: c.dropna().iloc[0] if c.notna().any() else np.nan)
    tri = adj / first * 100.0
    tri = tri.loc[:, tri.notna().any()]
    close = close[tri.columns]
    if freq == "ME":
        tri, close = tri.loc[me], close.loc[me]
    stats.update(seconds_load=round(time.time() - t0, 1), start=str(tri.index[0].date()), end=str(tri.index[-1].date()),
                 n_tickers=int(tri.shape[1]), source_eras=era, freq=freq)
    p = Panel(tri, close, fund, table, mix.reindex(tri.columns), stats)
    if cache:
        try:
            _write_cache(cdir, p)
        except Exception:                                    # read-only disk: just no cache
            pass
    return p


def _write_cache(cdir: Path, p: Panel) -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    p.prices.to_parquet(cdir / "prices.parquet")
    p.close.to_parquet(cdir / "close.parquet")
    p.fundamentals.to_parquet(cdir / "fundamentals.parquet")
    p.source_mix.to_parquet(cdir / "source_mix.parquet")
    (cdir / "meta.json").write_text(json.dumps(p.stats))


def _read_cache(cdir: Path, root: Path) -> Panel:
    t0 = time.time()
    st = json.loads((cdir / "meta.json").read_text())
    p = Panel(pd.read_parquet(cdir / "prices.parquet"), pd.read_parquet(cdir / "close.parquet"),
              pd.read_parquet(cdir / "fundamentals.parquet"), membership_table(root),
              pd.read_parquet(cdir / "source_mix.parquet"), {**st, "from_cache": True})
    p.stats["seconds_cache_read"] = round(time.time() - t0, 1)
    return p


# ------------------------------------------------------------------------------------------ benchmark proxy
def equal_weight_pit(panel: Panel) -> pd.Series:
    """Cap-less proxy for the S&P 500 total return: each day the mean adj_close return of the names that were members at
    the previous bar (equal weight, rebalanced every bar), compounded from 100. Survivorship-free; NOT cap-weighted."""
    px = panel.prices
    mask = member_mask(panel.membership, px.index, px.columns)
    r = px.pct_change().to_numpy()
    prev = np.vstack([np.zeros((1, mask.shape[1]), dtype=bool), mask[:-1]])
    use = prev & np.isfinite(r)
    n = use.sum(axis=1)
    mean = np.where(n > 0, np.where(use, r, 0.0).sum(axis=1) / np.maximum(n, 1), 0.0)
    mean[0] = 0.0
    return pd.Series(100.0 * np.cumprod(1.0 + mean), index=px.index, name="SP500_EW_PIT")


def universe_size(panel: Panel, date) -> int:
    """Number of members of the point-in-time universe with a price on `date` (last bar on or before it)."""
    px = panel.prices
    i = px.index.searchsorted(pd.Timestamp(date), side="right") - 1
    m = member_mask(panel.membership, px.index[i:i + 1], px.columns)[0]
    return int((m & px.iloc[i].notna().to_numpy()).sum())


# ------------------------------------------------------------------------------------------ screens
_NEEDS_FUNDAMENTALS = frozenset({"eps", "eps_prev", "eps_avg3", "pe", "pe_avg3", "pb", "bvps", "ebit", "ev_ebit", "enterprise_value",
                                 "earnings_yield", "roic", "invested_capital", "net_current_assets", "ncav_to_mcap", "book_equity",
                                 "sales", "shares", "shares_prev", "buyback_yield", "f_score", "div_years", "market_cap", "sector",
                                 "roa", "leverage"})
def panel_screens() -> dict:
    """The registry screens with the market-cap size cut removed (the panel has no market cap; its universe is already the
    S&P 500). Screens that need fundamentals the panel lacks are left out."""
    import copy
    from ..screens import REGISTRY
    from ..screens import Screen
    out = {}
    for name, s in REGISTRY.items():
        p = copy.deepcopy(s.params)
        if s.fn.__name__ != "rule_screen":
            continue
        for st in ([p] + list(p.get("stages") or [])):
            u = dict(st.get("universe") or {}) if isinstance(st, dict) else {}
            if u.get("top"):
                u.pop("top")
                u.pop("by", None)
                st["universe"] = u
        if any(st.get("graham") for st in ([p] + list(p.get("stages") or []))):
            continue
        if set(re.findall(r"[a-z_0-9]+", json.dumps(p, default=str).lower())) & _NEEDS_FUNDAMENTALS:
            continue
        out[name] = Screen(name, s.fn, s.n, p, (s.note + " [S&P 500 panel: no size cut]").strip())
    return out


# ------------------------------------------------------------------------------------------ builder
@dataclass(frozen=True)
class SP500PanelBuilder:
    """`Dataset` from the point-in-time S&P 500 panel. Universe = `Membership` of the S&P 500 intervals (a name is eligible
    while it is a member and trades); benchmark `SP500_TR` (real, cap-weighted: D1's skfolio daily index + Shiller yield) when
    the window lies within 1990-01-02..2022-12-28, plus the cap-less `SP500_EW_PIT` over the whole window."""
    start: str = "1990-01-01"
    end: str | None = None
    freq: str = "D"
    dir: str | None = None
    cache: bool = True

    def panel(self) -> Panel:
        return load(self.start, self.end, self.freq, self.dir, self.cache)

    def build(self, **opts) -> Dataset:  # noqa: F821
        o = replace(self, **opts)
        from ..data import security_meta
        from ..dataset import Dataset
        from ..universe import Membership
        p = o.panel()
        px = p.prices
        ew = equal_weight_pit(p)
        idx = pd.DataFrame({"SP500_EW_PIT": ew})
        kinds = {"SP500_EW_PIT": "total_return"}
        info = {"SP500_EW_PIT": {"yield": p.fundamentals.groupby(p.fundamentals["date"].dt.year)["div_yield"].median(),
                                 "foreign_share": 1.0}}
        notes = [NOTE_LOCAL, NOTE_NOFUND, NOTE_DELIST,
                 f"WARNING: dividend outliers filtered: {p.stats.get('div_dropped_outlier', 0)} rows (dividend > 25% of close), "
                 f"{p.stats.get('div_dropped_duplicate', 0)} duplicate dividend rows within 20 days dropped; "
                 f"{p.stats.get('rows_bad_price', 0)} rows with non-positive price dropped.",
                 "NOTE: source mix by era: " + str(p.stats.get("source_eras", "n/a")) + ".",
                 "NOTE: benchmark SP500_EW_PIT is a cap-less proxy (equal-weight total return of the point-in-time members, daily "
                 "rebalanced), not the cap-weighted S&P 500; its yield for the tax of the passive index is the median member yield."]
        try:
            tr = _real_benchmark(px.index)
            if tr is not None:
                tr_b, yld = tr
                idx.insert(0, "SP500_TR", tr_b.reindex(px.index, method="ffill"))
                kinds["SP500_TR"] = "total_return"
                info["SP500_TR"] = {"yield": yld.groupby(yld.index.year).mean(), "foreign_share": 1.0}
                notes.append("NOTE: benchmark SP500_TR is the real S&P 500 total return (daily price index plus Shiller's monthly "
                             "dividend yield, as in Lab.real_us); SP500_EW_PIT is the cap-less alternative.")
        except Exception:                                    # skfolio / mirrors missing: the proxy stays the benchmark
            pass
        meta = security_meta(px.columns, domicile="US", currency="USD", wrapper="stock", eligible_dividend=False)
        return Dataset.from_frames(
            p.fundamentals, px, idx, Membership(p.membership), name="sp500_panel",
            source="Johnbrick123/sp500-data prices + membership_intervals (CRSP 1968-2006 / Yahoo / Tiingo / Quandl WIKI)",
            index_kinds=kinds, pit=True, currency="USD",
            licence="none stated, partly CRSP-derived: LOCAL USE ONLY, never commit or redistribute",
            security_meta=meta, index_info=info, notes=tuple(notes))


def _real_benchmark(index: pd.DatetimeIndex):
    """D1's real total-return benchmark if the window fits inside the skfolio daily index (else None)."""
    from ..dataset import real_us_total_return
    from . import bundled
    from .fetch import mirror_path
    pidx = bundled.sp500_index()["SP500"]
    slack = pd.Timedelta(days=7)                         # the panel's last bar may be a day or two past the skfolio index
    if index[0] < pidx.index[0] - slack or index[-1] > pidx.index[-1] + slack:
        return None
    sh = pd.read_csv(mirror_path("shiller_sp500_monthly.csv"))
    tr, yld = real_us_total_return(pidx, sh)
    return tr, yld


def real_sp500(start="1990-01-01", end=None, freq="D", dir=None, **kw):
    """`Lab.real_sp500` body: a Lab on the point-in-time panel (imported lazily by api.py)."""
    from ..api import Lab
    from ..dataset import RealUSBuilder
    b = SP500PanelBuilder(start=start, end=end, freq=freq, dir=dir)
    kw.setdefault("screens", panel_screens())
    if "macro" not in kw:
        try:
            kw["macro"] = RealUSBuilder().macro()
        except Exception:
            kw["macro"] = None
    return Lab.from_dataset(b.build(), **kw)
