"""Point-in-time data model.

Two long-format tables drive everything (plus optional index series for
buy-and-hold benchmarks):

fundamentals  one row per (date, ticker) holding what was *knowable* on that
              date (apply your reporting lag before loading). Columns used by
              the screens:
                  market_cap, price, eps, eps_prev, eps_avg3, eps_avg3_10y_ago,
                  loss_years_5y, loss_years_10y, sales, bvps, dividend, div_years,
                  current_ratio, lt_debt, net_current_assets, roa, roa_prev,
                  cfo_to_assets, leverage, leverage_prev, current_ratio_prev,
                  shares, shares_prev, gross_margin, gross_margin_prev,
                  asset_turnover, asset_turnover_prev, ebit, enterprise_value,
                  invested_capital, sector
prices        one row per (date, ticker) of a total-return index (dividends
              reinvested). Daily data enables daily-start timing studies;
              monthly data works for everything else.

`Market` wraps both and serves a cached, marked-to-market snapshot for any
trading day: fundamentals stay as last reported, but price, market cap and EV
move with the price index, so P/E, P/B and yield are current on that day.
"""
from __future__ import annotations

import threading
import warnings
from collections import OrderedDict

import numpy as np
import pandas as pd


F_SCORE_COLS = ["roa", "roa_prev", "cfo_to_assets", "leverage", "leverage_prev", "current_ratio",
                "current_ratio_prev", "shares", "shares_prev", "gross_margin", "gross_margin_prev",
                "asset_turnover", "asset_turnover_prev"]


def f_score(s: pd.DataFrame) -> pd.Series:
    """Piotroski's nine-point F-score."""
    pts = [s["roa"] > 0, s["cfo_to_assets"] > 0, s["roa"] > s["roa_prev"],
           s["cfo_to_assets"] > s["roa"], s["leverage"] < s["leverage_prev"],
           s["current_ratio"] > s["current_ratio_prev"], s["shares"] <= s["shares_prev"],
           s["gross_margin"] > s["gross_margin_prev"],
           s["asset_turnover"] > s["asset_turnover_prev"]]
    return sum(p.astype(int) for p in pts)


def add_ratios(f: pd.DataFrame) -> pd.DataFrame:
    """Derived ratios; absent fundamental columns are filled with NaN and ratios whose inputs
    are absent are skipped, so price-only tables work."""
    f = f.copy()
    for c in ("price", "eps", "eps_avg3", "bvps", "dividend", "ebit", "enterprise_value",
              "invested_capital", "net_current_assets", "market_cap"):
        if c not in f:
            f[c] = np.nan
    f["pe"] = f["price"] / f["eps"].where(f["eps"] > 0)
    f["pe_avg3"] = f["price"] / f["eps_avg3"].where(f["eps_avg3"] > 0)
    f["pb"] = f["price"] / f["bvps"].where(f["bvps"] > 0)
    f["div_yield"] = f["dividend"] / f["price"]
    f["earnings_yield"] = f["ebit"] / f["enterprise_value"]
    f["ev_ebit"] = f["enterprise_value"] / f["ebit"].where(f["ebit"] > 0)   # NaN when EBIT <= 0
    f["roic"] = f["ebit"] / f["invested_capital"]
    f["ncav_to_mcap"] = f["net_current_assets"] / f["market_cap"]
    f["book_equity"] = f["bvps"] * f["market_cap"] / f["price"]
    if "shares" in f and "shares_prev" in f:
        # Buyback yield: 1 - shares / shares four quarters ago. `shares_prev` is the year-ago count
        # (the synthetic market and the F-score both read it that way); positive = net repurchase.
        f["buyback_yield"] = 1 - f["shares"] / f["shares_prev"].where(f["shares_prev"] > 0)
    if all(c in f for c in F_SCORE_COLS):
        f["f_score"] = f_score(f)
    return f


# SB1: the data dictionary: every column of a `Market.view` snapshot (and of the fundamentals table that feeds
# it), with a group and a plain-English one-liner. `ops/screen_builder.py` (screen_schema) and the Screen tab read it;
# tests/test_screen_builder.py fails when a column the synthetic, bundled or panel-like frames emit is missing here.
# Currency columns are in the dataset's currency, size columns ($M in the bundled and synthetic data).
COLUMN_INFO: dict = {
    # identity
    "date": {"group": "identity", "description": "date the fundamentals row became knowable (report date), not the screening date"},
    "sector": {"group": "identity", "description": "sector label of the company (text)"},
    # price and size
    "price": {"group": "price and size", "description": "share price, marked to market on the screening date (currency units per share)"},
    "market_cap": {"group": "price and size", "description": "market capitalisation on the screening date, in $M (price x shares, moved with the price since the report date)"},
    "enterprise_value": {"group": "price and size", "description": "market cap plus net debt, in $M, moved with the price since the report date"},
    "shares": {"group": "price and size", "description": "shares outstanding at the last report (millions; only ratios of shares are used)"},
    "shares_prev": {"group": "price and size", "description": "shares outstanding a year earlier (same unit as shares); shares <= shares_prev scores a Piotroski point"},
    # income and cash flow
    "sales": {"group": "income", "description": "annual revenue, in $M"},
    "eps": {"group": "income", "description": "latest annual earnings per share (currency units; negative for a loss)"},
    "eps_prev": {"group": "income", "description": "earnings per share a year earlier (currency units)"},
    "eps_avg3": {"group": "income", "description": "average of the last three years' earnings per share (currency units)"},
    "eps_avg3_10y_ago": {"group": "income", "description": "the same three-year average EPS as it was ten years ago (Graham's growth test base)"},
    "loss_years_5y": {"group": "income", "description": "number of loss-making years among the last 5 (count)"},
    "loss_years_10y": {"group": "income", "description": "number of loss-making years among the last 10 (count)"},
    "ebit": {"group": "income", "description": "operating income (earnings before interest and tax), in $M"},
    "dividend": {"group": "income", "description": "trailing 12-month dividends per share (currency units; 0 = pays none)"},
    "div_years": {"group": "income", "description": "consecutive years in which a dividend was paid (count)"},
    "roa": {"group": "income", "description": "return on assets: net income / total assets, as a fraction (0.05 = 5%)"},
    "roa_prev": {"group": "income", "description": "return on assets a year earlier, as a fraction"},
    "cfo_to_assets": {"group": "income", "description": "operating cash flow / total assets, as a fraction (Piotroski quality check)"},
    "gross_margin": {"group": "income", "description": "gross profit / sales, as a fraction (0.3 = 30%)"},
    "gross_margin_prev": {"group": "income", "description": "gross margin a year earlier, as a fraction"},
    "asset_turnover": {"group": "income", "description": "sales / total assets, a ratio (times per year)"},
    "asset_turnover_prev": {"group": "income", "description": "asset turnover a year earlier"},
    # balance sheet
    "bvps": {"group": "balance sheet", "description": "book value per share (currency units)"},
    "current_ratio": {"group": "balance sheet", "description": "current assets / current liabilities, a ratio (2.0 = twice)"},
    "current_ratio_prev": {"group": "balance sheet", "description": "current ratio a year earlier"},
    "lt_debt": {"group": "balance sheet", "description": "long-term debt, in $M"},
    "net_current_assets": {"group": "balance sheet", "description": "current assets minus total liabilities (Graham's NCAV), in $M"},
    "invested_capital": {"group": "balance sheet", "description": "invested capital (equity plus debt), in $M"},
    "leverage": {"group": "balance sheet", "description": "long-term debt / total assets, as a fraction"},
    "leverage_prev": {"group": "balance sheet", "description": "leverage a year earlier, as a fraction"},
    # valuation (derived in add_ratios)
    "pe": {"group": "valuation", "description": "price / eps: price-to-earnings on the latest annual EPS (missing when eps <= 0)"},
    "pe_avg3": {"group": "valuation", "description": "price / eps_avg3: price to the 3-year average EPS (missing when the average <= 0)"},
    "pb": {"group": "valuation", "description": "price / bvps: price-to-book (missing when book value per share <= 0)"},
    "div_yield": {"group": "valuation", "description": "trailing dividends per share / price, as a fraction (0.03 = 3%)"},
    "earnings_yield": {"group": "valuation", "description": "ebit / enterprise_value, as a fraction (Greenblatt's earnings yield)"},
    "ev_ebit": {"group": "valuation", "description": "enterprise_value / ebit (missing when ebit <= 0)"},
    "roic": {"group": "valuation", "description": "ebit / invested_capital, as a fraction (Greenblatt's return on capital)"},
    "ncav_to_mcap": {"group": "valuation", "description": "net_current_assets / market_cap, a ratio (>= 1 means price below net current asset value)"},
    "book_equity": {"group": "valuation", "description": "book value of equity, in $M: bvps x market_cap / price"},
    "buyback_yield": {"group": "valuation", "description": "1 - shares / shares_prev, as a fraction (positive = net repurchase, negative = issuance)"},
    "f_score": {"group": "quality", "description": "Piotroski F-score, 0 to 9 points: profitability, leverage/liquidity and efficiency tests"},
    # price statistics (Market.trailing_stats)
    "mom_12_1": {"group": "momentum", "description": "total return over the prior 12 months skipping the latest month, as a fraction (0.10 = +10%)"},
    "mom_6m": {"group": "momentum", "description": "total return over the prior 6 calendar months, as a fraction"},
    "mom_12m": {"group": "momentum", "description": "total return over the prior 12 calendar months, as a fraction"},
    "vol_1y": {"group": "volatility", "description": "standard deviation of the last 12 months' log returns, annualised, as a fraction (0.30 = 30%/yr)"},
    # LA: price-derived trend columns (Market.trailing_stats / Market.view), docs/SCREEN_LIBRARY.md section 2
    "high_52w": {"group": "momentum", "description": "highest close over the previous 252 trading days including the screening day, in the same price units as price (monthly data: the 12-month window); price / high_52w is 1.0 at a new high"},
    "sma_200": {"group": "momentum", "description": "simple average close over the previous 200 trading days including the screening day, in the same price units as price (monthly data: the 10-month average); price > sma_200 means an uptrend"},
    "vol_260d": {"group": "volatility", "description": "standard deviation of the last 260 trading days' log returns, annualised (monthly data: the 12-month window, same as vol_1y)"},
    # security metadata (security_meta)
    "domicile": {"group": "security", "description": "country of the issuer's domicile, ISO code such as CA or US (text)"},
    "listing": {"group": "security", "description": "country of the exchange the security trades on, ISO code (text)"},
    "currency": {"group": "security", "description": "trading currency, ISO code such as CAD or USD (text)"},
    "wrapper": {"group": "security", "description": "kind of security: stock, etf_ca or etf_us (text)"},
    "underlying_domicile": {"group": "security", "description": "for a fund, the domicile of what it holds (ISO code); blank for stocks"},
    "eligible_dividend": {"group": "security", "description": "true if its dividends are Canadian eligible dividends (only meaningful for a CA domicile)"},
}


def load_csv(fundamentals_path: str, prices_path: str, indices_path: str | None = None):
    """Returns (fundamentals, prices, indices). indices.csv has columns date,index,tri."""
    f = pd.read_csv(fundamentals_path, parse_dates=["date"])
    p = pd.read_csv(prices_path, parse_dates=["date"])
    idx = None
    if indices_path:
        i = pd.read_csv(indices_path, parse_dates=["date"])
        idx = i.pivot(index="date", columns="index", values="tri").sort_index()
    return add_ratios(f), p.pivot(index="date", columns="ticker", values="tri").sort_index(), idx


# P3: security metadata (per-ticker domicile / listing / currency / wrapper), see docs/TAXES.md "Foreign holdings"
WRAPPERS = ("stock", "etf_ca", "etf_us")
SECURITY_META_COLS = ("domicile", "listing", "currency", "wrapper", "underlying_domicile", "eligible_dividend")
COUNTRY_CURRENCY = {"CA": "CAD", "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "JP": "JPY", "CH": "CHF"}


def security_meta(tickers, rows: pd.DataFrame | None = None, *, domicile="CA", currency=None, wrapper="stock",
                  eligible_dividend=True) -> pd.DataFrame:
    """Per-ticker metadata table (index `ticker`, columns `SECURITY_META_COLS`). `rows` (optional, indexed by ticker or
    with a `ticker` column) overrides the defaults per name; a ticker or a cell it leaves blank gets the default:
    domicile `domicile`, listing = domicile, currency `currency` (else the domicile's usual one), wrapper "stock",
    underlying_domicile blank, eligible_dividend `eligible_dividend` (only meaningful for a Canadian domicile)."""
    t = pd.Index(list(tickers), name="ticker")
    r = pd.DataFrame(index=t, columns=list(SECURITY_META_COLS), dtype=object)
    if rows is not None and len(rows):
        rows = rows.set_index("ticker") if "ticker" in rows.columns else rows
        rows = rows[~rows.index.duplicated(keep="last")]
        for c in SECURITY_META_COLS:
            if c in rows.columns:
                r[c] = rows[c].reindex(t).astype(object)
    for c in ("domicile", "listing", "underlying_domicile"):
        r[c] = r[c].map(lambda v: v.strip().upper() if isinstance(v, str) and v.strip() else np.nan)
    r["domicile"] = r["domicile"].fillna(domicile)
    r["listing"] = r["listing"].fillna(r["domicile"])
    r["currency"] = r["currency"].map(lambda v: v.strip().upper() if isinstance(v, str) and v.strip() else np.nan)
    dflt = r["domicile"].map(lambda d: currency or COUNTRY_CURRENCY.get(d, currency or "USD"))
    r["currency"] = r["currency"].fillna(dflt)
    r["wrapper"] = r["wrapper"].map(lambda v: v.strip().lower() if isinstance(v, str) and v.strip() else wrapper)
    bad = sorted(set(r["wrapper"]) - set(WRAPPERS))
    if bad:
        raise ValueError(f"wrapper must be one of {WRAPPERS}, got {bad}")
    r["eligible_dividend"] = r["eligible_dividend"].map(
        lambda v: bool(eligible_dividend) if v is None or (isinstance(v, float) and v != v) else
        (str(v).strip().lower() in ("1", "true", "t", "yes", "y") if isinstance(v, str) else bool(v)))
    return r


def load_securities(path, tickers) -> pd.DataFrame:
    """`securities.csv` (columns ticker, domicile, listing, currency, wrapper, underlying_domicile, eligible_dividend; all
    but ticker optional) -> `security_meta` for `tickers`."""
    return security_meta(tickers, pd.read_csv(path, dtype=str, keep_default_na=False).replace("", np.nan))


def load_index_kinds(indices_path: str) -> dict:
    """{index name: kind} from the optional `kind` column ("price" or "total_return") of an indices csv;
    an index with no (or a blank) kind is left out."""
    i = pd.read_csv(indices_path)
    if "kind" not in i or "index" not in i:
        return {}
    k = i.dropna(subset=["kind"]).assign(kind=lambda d: d["kind"].astype(str).str.strip().str.lower())
    k = k[k["kind"] != ""].groupby("index")["kind"].agg(lambda v: v.iloc[-1])
    return k.to_dict()


def periods_per_year(index: pd.DatetimeIndex) -> float:
    yrs = (index[-1] - index[0]).days / 365.25
    return (len(index) - 1) / yrs


DEFAULT_MAX_VIEWS = 2048


class _LRU:
    """Small thread-safe least-recently-used mapping (`get`, `put`, `in`, `len`); `maxsize` None = unbounded."""

    def __init__(self, maxsize: int | None):
        self.maxsize, self._d, self._lock = maxsize, OrderedDict(), threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            try:
                self._d.move_to_end(key)
            except KeyError:
                return default
            return self._d[key]

    def put(self, key, value):
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while self.maxsize is not None and len(self._d) > self.maxsize:
                self._d.popitem(last=False)
        return value

    def __contains__(self, key):
        return key in self._d

    def __len__(self):
        return len(self._d)

    def clear(self):
        with self._lock:
            self._d.clear()


class Market:
    """Fundamentals + prices with cached per-day views for the screens.

    `max_views` bounds the per-day view cache (and the picks cache) to that many entries, least recently
    used evicted first (a monthly backtest over 25 years needs ~300; None = unbounded). Results do not
    depend on it: an evicted view is recomputed identically."""

    def __init__(self, fund, prices: pd.DataFrame | None = None, indices: pd.DataFrame | None = None,
                 max_views: int | None = DEFAULT_MAX_VIEWS):
        from .dataset import Dataset
        if isinstance(fund, Dataset):                 # Market(dataset); frames are taken from it
            if prices is not None or indices is not None:
                raise TypeError("Market(dataset) takes no prices / indices")
            ds = fund
        else:                                         # Market(fund, prices, indices): wrap in a Dataset
            ds = Dataset.from_frames(fund, prices, indices)
        self.dataset = ds
        self.universe = ds.universe
        fund, prices, indices = ds.fundamentals, ds.prices, (ds.indices if len(ds.indices.columns) else None)
        self.fund = fund.sort_values("date")
        self.prices = prices.sort_index()
        self.index = self.prices.index
        # Index total-return series (benchmarks), aligned to trading days.
        self.indices = (indices.sort_index().reindex(self.index, method="pad")
                        if indices is not None else pd.DataFrame(index=self.index))
        self.ppy = periods_per_year(self.index)
        self._tri = self.prices.to_numpy()
        self._fund_dates = np.sort(self.fund["date"].unique())
        self._snaps: dict = {}
        self.max_views = max_views
        self._views = _LRU(max_views)
        self._picks = _LRU(max_views)

    def series(self, name: str) -> pd.Series:
        """An index series by name, or a single security's total-return index."""
        if name in self.indices:
            return self.indices[name]
        if name in self.prices:
            return self.prices[name]
        raise KeyError(f"no index or ticker named {name!r}; indices: {list(self.indices)}")

    def trading_day(self, d, side="next") -> pd.Timestamp:
        """Snap a calendar date to a trading day (next by default)."""
        i = self.index.searchsorted(pd.Timestamp(d), side="left")
        if side == "prev" and (i == len(self.index) or self.index[i] != pd.Timestamp(d)):
            i -= 1
        return self.index[min(max(i, 0), len(self.index) - 1)]

    def _snapshot(self, asof) -> pd.DataFrame:
        """Latest reported row per ticker on or before `asof` (cached per report date)."""
        k = self._fund_dates.searchsorted(np.datetime64(asof), side="right") - 1
        if k < 0:
            return self.fund.iloc[:0].set_index("ticker")
        fd = self._fund_dates[k]
        if fd not in self._snaps:
            f = self.fund[self.fund["date"] <= fd]
            self._snaps[fd] = f.groupby("ticker").tail(1).set_index("ticker")
        return self._snaps[fd]

    def trailing_stats(self, asof: pd.Timestamp) -> pd.DataFrame:
        """Trailing price statistics, calendar based so any frequency works.

        mom_12_1 / vol_1y   12-1 momentum and 1y annualized volatility (pinned by golden tests).
        mom_6m / mom_12m    plain prior 6 / 12 calendar-month total returns, no skipped month.
        high_52w / sma_200  LA: highest close of the last 252 trading days and mean close of the last 200 (both
                            including `asof`; monthly data: the 12-month high and the 10-month average), in the
                            units of the total-return index; `view` rescales them to price units. NaN without the
                            full window.
        vol_260d            std of log returns over the last 260 trading days, annualized by sqrt(ppy).
                            Needs 260 daily returns; with monthly data (index spacing > 5 days)
                            260 days is not defined, so it uses the 12-month calendar window
                            (12 returns), the same window as vol_1y.
        """
        i = self.index.get_loc(asof)
        cols = self.prices.columns
        nan = np.full(len(cols), np.nan)
        out = {k: nan for k in ("mom_12_1", "vol_1y", "mom_6m", "mom_12m", "vol_260d", "high_52w", "sma_200")}
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # names with < 2 returns -> NaN vol
            if asof - pd.DateOffset(months=6) >= self.index[0]:
                i6 = self.index.searchsorted(asof - pd.DateOffset(months=6))
                out["mom_6m"] = self._tri[i] / self._tri[i6] - 1
            if asof - pd.DateOffset(months=12) >= self.index[0]:
                i12 = self.index.searchsorted(asof - pd.DateOffset(months=12))
                i1 = self.index.searchsorted(asof - pd.DateOffset(months=1), side="right") - 1
                win = self._tri[i12:i + 1]
                out["mom_12m"] = self._tri[i] / self._tri[i12] - 1
                out["mom_12_1"] = self._tri[i1] / self._tri[i12] - 1
                out["vol_1y"] = np.nanstd(np.diff(np.log(win), axis=0), axis=0, ddof=1) * np.sqrt(self.ppy)
                if self.ppy <= 100:                      # monthly (or coarser) data: calendar window
                    out["vol_260d"] = out["vol_1y"]
                    # LA: 12-month high of the monthly closes (including this one); needs the first close of the window
                    out["high_52w"] = np.where(np.isnan(win[0]), np.nan, np.nanmax(win, axis=0))
            if self.ppy <= 100 and asof - pd.DateOffset(months=10) >= self.index[0]:
                # LA: the monthly equivalent of the 200-day average is the 10-month average (11 closes)
                w10 = self._tri[self.index.searchsorted(asof - pd.DateOffset(months=10)):i + 1]
                out["sma_200"] = np.where(np.isnan(w10[0]), np.nan, np.nanmean(w10, axis=0))
            if self.ppy > 100 and i >= 260:
                win = self._tri[i - 260:i + 1]            # 260 daily returns
                out["vol_260d"] = np.nanstd(np.diff(np.log(win), axis=0), axis=0, ddof=1) * np.sqrt(self.ppy)
            if self.ppy > 100 and i >= 251:               # LA: 252 closes including today; a partial window is NaN
                w = self._tri[i - 251:i + 1]
                out["high_52w"] = np.where(np.isnan(w[0]), np.nan, np.nanmax(w, axis=0))
            if self.ppy > 100 and i >= 199:               # LA: 200 closes including today
                w = self._tri[i - 199:i + 1]
                out["sma_200"] = np.where(np.isnan(w[0]), np.nan, np.nanmean(w, axis=0))
        return pd.DataFrame(out, index=cols)

    def view(self, asof) -> pd.DataFrame:
        """Snapshot for screening on trading day `asof`: tradable names only, marked to market."""
        asof = pd.Timestamp(asof)
        hit = self._views.get(asof)
        if hit is not None:
            return hit
        s = self._snapshot(asof)
        for c in ("price", "market_cap", "enterprise_value"):
            if c not in s:
                s = s.assign(**{c: np.nan})
        cols = self.prices.columns.get_indexer(s.index)
        i_now = self.index.get_loc(asof)
        tradable = (cols >= 0)
        tradable[tradable] &= ~np.isnan(self._tri[i_now, cols[tradable]])
        s, cols = s[tradable].copy(), cols[tradable]
        # Price change since each row's report date (TRI includes dividends: a small upward bias).
        i_rep = np.clip(self.index.searchsorted(s["date"].to_numpy(), side="right") - 1, 0, None)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = self._tri[i_now, cols] / self._tri[i_rep, cols]
        m = np.where(np.isfinite(m), m, 1.0)
        d_mcap = s["market_cap"] * (m - 1)
        s["price"] *= m
        s["market_cap"] += d_mcap
        s["enterprise_value"] += d_mcap
        ts = self.trailing_stats(asof)
        s = add_ratios(s).join(ts)
        # LA: high_52w / sma_200 come out of trailing_stats in total-return-index units; express them in the price
        # units of `price` (price x level / today's level), so price / high_52w and price > sma_200 are exact.
        tri_now = pd.Series(self._tri[i_now], index=self.prices.columns).reindex(s.index)
        for c in ("high_52w", "sma_200"):
            s[c] = s["price"] * s[c] / tri_now
        meta = getattr(self.dataset, "security_meta", None)                          # P3: domicile / currency / wrapper
        if meta is not None:
            new = [c for c in meta.columns if c not in s.columns]
            s = s.join(meta[new].reindex(s.index)) if new else s
        return self._views.put(asof, self.universe.restrict(s, asof))

    def picks(self, screen, asof) -> list[str]:
        # Keep the screen in the entry: a freed screen's id() can be reused by a new one.
        key = (id(screen), pd.Timestamp(asof))
        hit = self._picks.get(key)
        if hit is None or hit[0] is not screen:
            hit = self._picks.put(key, (screen, screen(self.view(asof))))
        return hit[1]


# --------------------------------------------------------------------------
# Synthetic market so the whole pipeline runs without a data licence.
# Value, quality and momentum carry small premia so results are not pure noise;
# a quarter of names delist along the way to exercise survivorship handling.
# --------------------------------------------------------------------------
def synthetic_market(n_stocks=250, start="2000-01-03", years=25, freq="B", seed=7):
    rng = np.random.default_rng(seed)
    if freq == "B":
        dates, ppy = pd.bdate_range(start, periods=years * 252), 252
    else:
        dates, ppy = pd.date_range(start, periods=years * 12, freq="ME"), 12
    k = 12 / ppy                                   # scale monthly parameters
    tickers = [f"T{i:03d}" for i in range(n_stocks)]
    sectors = rng.choice(["Fin", "Energy", "Mat", "Ind", "Util", "Tech", "Cons", "Tel"], n_stocks)

    cheap = rng.normal(size=n_stocks)          # persistent "value" trait
    quality = rng.normal(size=n_stocks)        # persistent "quality" trait
    beta = rng.uniform(0.5, 1.5, n_stocks)
    size = np.exp(rng.normal(8, 1.5, n_stocks))  # $M market cap
    delist = np.where(rng.random(n_stocks) < 0.25,
                      rng.integers(2 * ppy, len(dates), n_stocks), 10**9)

    mkt = rng.normal(0.006 * k, 0.045 * np.sqrt(k), len(dates))
    phi = 0.9 ** k                              # momentum persistence, same half-life at any freq
    mom_sd = 0.01 / np.sqrt(1 - 0.81) * np.sqrt(1 - phi**2)
    tri = np.ones((len(dates), n_stocks))
    mom = np.zeros(n_stocks)
    for t in range(1, len(dates)):
        mom = phi * mom + rng.normal(0, mom_sd, n_stocks)
        r = (beta * mkt[t] + k * (0.0015 * cheap + 0.001 * quality + 0.3 * mom)
             + rng.normal(0, 0.07 * np.sqrt(k), n_stocks) * (1.3 - 0.2 * quality.clip(-1, 1)))
        tri[t] = tri[t - 1] * (1 + r)
    tri = np.where(np.arange(len(dates))[:, None] >= delist, np.nan, tri)
    prices = pd.DataFrame(tri, index=dates, columns=tickers)

    # Cap-weighted total-return index of every live name (delisted names drop out flat).
    cap = size * tri
    w = np.nan_to_num(cap[:-1]) / np.nansum(cap[:-1], axis=1, keepdims=True)
    r = np.nan_to_num(tri[1:] / tri[:-1] - 1)
    index_tri = np.concatenate([[1.0], np.cumprod(1 + (w * r).sum(axis=1))])
    indices = pd.DataFrame({"SYN_CAPWT": index_tri}, index=dates)

    rows = []
    for t in range(0, len(dates), ppy // 4):  # quarterly fundamentals
        alive = ~np.isnan(tri[t])
        px = 20 * tri[t]
        noise = lambda s=0.15: np.exp(rng.normal(0, s, n_stocks))
        eps = px * (0.07 + 0.02 * cheap) * noise() * np.where(rng.random(n_stocks) < 0.08, -0.5, 1)
        eps_avg3 = np.abs(eps) * noise(0.1)
        bvps = px * (0.8 + 0.3 * cheap).clip(0.2) * noise()
        roa = 0.05 + 0.02 * quality + rng.normal(0, 0.02, n_stocks)
        mcap = size * tri[t]
        losses10 = rng.poisson(np.exp(-0.3 - 0.8 * quality))
        rows.append(pd.DataFrame({
            "date": dates[t], "ticker": tickers, "sector": sectors, "price": px,
            "market_cap": mcap, "sales": mcap * rng.uniform(0.3, 2, n_stocks),
            "eps": eps, "eps_prev": eps * noise(0.2), "eps_avg3": eps_avg3,
            "eps_avg3_10y_ago": eps_avg3 / np.exp(rng.normal(0.25 + 0.15 * quality, 0.3)),
            "loss_years_10y": losses10, "loss_years_5y": rng.binomial(losses10, 0.5),
            "bvps": bvps,
            "dividend": np.where(rng.random(n_stocks) < 0.6, px * (0.03 + 0.01 * cheap).clip(0), 0),
            "div_years": rng.integers(0, 40, n_stocks) * (quality > -0.5),
            "current_ratio": (1.8 + 0.5 * quality) * noise(0.3),
            "current_ratio_prev": (1.8 + 0.5 * quality) * noise(0.3),
            "lt_debt": mcap * (0.4 - 0.1 * quality).clip(0) * noise(),
            "net_current_assets": mcap * (0.1 * cheap + rng.normal(0, 0.2, n_stocks)
                                           + (rng.random(n_stocks) < 0.03) * 1.2),
            "roa": roa, "roa_prev": roa + rng.normal(0, 0.01, n_stocks),
            "cfo_to_assets": roa + rng.normal(0.01, 0.02, n_stocks),
            "leverage": (0.3 - 0.05 * quality).clip(0) * noise(),
            "leverage_prev": (0.3 - 0.05 * quality).clip(0) * noise(),
            "shares": 100.0, "shares_prev": 100 * noise(0.03),
            "gross_margin": 0.3 + 0.05 * quality, "gross_margin_prev": (0.3 + 0.05 * quality) * noise(0.05),
            "asset_turnover": 0.8 * noise(0.1), "asset_turnover_prev": 0.8 * noise(0.1),
            "ebit": mcap * (0.08 + 0.03 * cheap) * noise(),
            "enterprise_value": mcap * 1.3,
            "invested_capital": mcap * (0.6 - 0.15 * quality).clip(0.1) * noise(),
        })[alive])
    return add_ratios(pd.concat(rows, ignore_index=True)), prices, indices
