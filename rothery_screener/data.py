"""Point-in-time data model.

Two long-format tables drive everything:

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

import warnings

import numpy as np
import pandas as pd


def add_ratios(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["pe"] = f["price"] / f["eps"].where(f["eps"] > 0)
    f["pe_avg3"] = f["price"] / f["eps_avg3"].where(f["eps_avg3"] > 0)
    f["pb"] = f["price"] / f["bvps"].where(f["bvps"] > 0)
    f["div_yield"] = f["dividend"] / f["price"]
    f["earnings_yield"] = f["ebit"] / f["enterprise_value"]
    f["roic"] = f["ebit"] / f["invested_capital"]
    f["ncav_to_mcap"] = f["net_current_assets"] / f["market_cap"]
    f["book_equity"] = f["bvps"] * f["market_cap"] / f["price"]
    return f


def load_csv(fundamentals_path: str, prices_path: str):
    f = pd.read_csv(fundamentals_path, parse_dates=["date"])
    p = pd.read_csv(prices_path, parse_dates=["date"])
    return add_ratios(f), p.pivot(index="date", columns="ticker", values="tri").sort_index()


def periods_per_year(index: pd.DatetimeIndex) -> float:
    yrs = (index[-1] - index[0]).days / 365.25
    return (len(index) - 1) / yrs


class Market:
    """Fundamentals + prices with cached per-day views for the screens."""

    def __init__(self, fund: pd.DataFrame, prices: pd.DataFrame):
        self.fund = fund.sort_values("date")
        self.prices = prices.sort_index()
        self.index = self.prices.index
        self.ppy = periods_per_year(self.index)
        self._tri = self.prices.to_numpy()
        self._fund_dates = np.sort(self.fund["date"].unique())
        self._snaps: dict = {}
        self._views: dict = {}
        self._picks: dict = {}

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
        """12-1 momentum and 1y annualized volatility, calendar based so any frequency works."""
        i = self.index.get_loc(asof)
        i12 = self.index.searchsorted(asof - pd.DateOffset(months=12))
        i1 = self.index.searchsorted(asof - pd.DateOffset(months=1), side="right") - 1
        if asof - pd.DateOffset(months=12) < self.index[0]:
            nan = np.full(len(self.prices.columns), np.nan)
            return pd.DataFrame({"mom_12_1": nan, "vol_1y": nan}, index=self.prices.columns)
        win = self._tri[i12:i + 1]
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # names with < 2 returns -> NaN vol
            mom = self._tri[i1] / self._tri[i12] - 1
            vol = np.nanstd(np.diff(np.log(win), axis=0), axis=0, ddof=1) * np.sqrt(self.ppy)
        return pd.DataFrame({"mom_12_1": mom, "vol_1y": vol}, index=self.prices.columns)

    def view(self, asof) -> pd.DataFrame:
        """Snapshot for screening on trading day `asof`: tradable names only, marked to market."""
        asof = pd.Timestamp(asof)
        if asof in self._views:
            return self._views[asof]
        s = self._snapshot(asof)
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
        s = add_ratios(s).join(self.trailing_stats(asof))
        self._views[asof] = s
        return s

    def picks(self, screen, asof) -> list[str]:
        key = (id(screen), pd.Timestamp(asof))
        if key not in self._picks:
            self._picks[key] = screen(self.view(asof))
        return self._picks[key]


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
    return add_ratios(pd.concat(rows, ignore_index=True)), prices
