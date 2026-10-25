"""Point-in-time data model.

Two long-format tables drive everything:

fundamentals  one row per (date, ticker) holding what was *knowable* on that
              date (apply your reporting lag before loading). Columns used by
              the screens:
                  market_cap, price, eps, eps_prev, eps_5y_min, bvps,
                  dividend, div_years, current_ratio, lt_debt, net_current_assets,
                  roa, roa_prev, cfo_to_assets, leverage, leverage_prev,
                  current_ratio_prev, shares, shares_prev, gross_margin,
                  gross_margin_prev, asset_turnover, asset_turnover_prev,
                  ebit, enterprise_value, invested_capital, sector
prices        one row per (date, ticker) of a total-return index (dividends
              reinvested), monthly frequency is plenty.

Derived ratios (pe, pb, yield, ...) are computed here so screens stay terse.
Swap `load_csv` for your vendor of choice (TMX, S&P Capital IQ, Stockpup,
EODHD, a Globe screener export, ...).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_ratios(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["pe"] = f["price"] / f["eps"].where(f["eps"] > 0)
    f["pb"] = f["price"] / f["bvps"].where(f["bvps"] > 0)
    f["div_yield"] = f["dividend"] / f["price"]
    f["earnings_yield"] = f["ebit"] / f["enterprise_value"]
    f["roic"] = f["ebit"] / f["invested_capital"]
    f["ncav_to_mcap"] = f["net_current_assets"] / f["market_cap"]
    return f


def load_csv(fundamentals_path: str, prices_path: str):
    f = pd.read_csv(fundamentals_path, parse_dates=["date"])
    p = pd.read_csv(prices_path, parse_dates=["date"])
    return add_ratios(f), p.pivot(index="date", columns="ticker", values="tri").sort_index()


def snapshot(fund: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Latest known row per ticker on or before `asof`."""
    f = fund[fund["date"] <= asof]
    return f.sort_values("date").groupby("ticker").tail(1).set_index("ticker")


def trailing_stats(prices: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Momentum (12-1) and 1y volatility from monthly total-return index."""
    hist = prices.loc[:asof].tail(13)
    rets = hist.pct_change(fill_method=None).iloc[1:]
    mom = hist.iloc[-2] / hist.iloc[0] - 1 if len(hist) == 13 else np.nan
    return pd.DataFrame({"mom_12_1": mom, "vol_1y": rets.std() * np.sqrt(12)})


# --------------------------------------------------------------------------
# Synthetic market so the whole pipeline runs without a data licence.
# Value, quality and momentum carry small premia so results are not pure noise;
# a few names delist each year to exercise survivorship handling.
# --------------------------------------------------------------------------
def synthetic_market(n_stocks=250, start="2000-01-31", years=25, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=years * 12, freq="ME")
    tickers = [f"T{i:03d}" for i in range(n_stocks)]
    sectors = rng.choice(["Fin", "Energy", "Mat", "Ind", "Util", "Tech", "Cons", "Tel"], n_stocks)

    cheap = rng.normal(size=n_stocks)          # persistent "value" trait
    quality = rng.normal(size=n_stocks)        # persistent "quality" trait
    beta = rng.uniform(0.5, 1.5, n_stocks)
    size = np.exp(rng.normal(8, 1.5, n_stocks))  # $M market cap
    delist_month = np.where(rng.random(n_stocks) < 0.25,
                            rng.integers(24, len(dates), n_stocks), 10**9)

    mkt = rng.normal(0.006, 0.045, len(dates))
    tri = np.ones((len(dates), n_stocks))
    mom_shock = np.zeros(n_stocks)
    for t in range(1, len(dates)):
        mom_shock = 0.9 * mom_shock + rng.normal(0, 0.01, n_stocks)
        r = (beta * mkt[t] + 0.0015 * cheap + 0.001 * quality + mom_shock * 0.3
             + rng.normal(0, 0.07, n_stocks) * (1.3 - 0.2 * quality.clip(-1, 1)))
        tri[t] = tri[t - 1] * (1 + r)
    tri = np.where(np.arange(len(dates))[:, None] >= delist_month, np.nan, tri)
    prices = pd.DataFrame(tri, index=dates, columns=tickers)

    rows = []
    for t in range(0, len(dates), 3):  # quarterly fundamentals
        d = dates[t]
        alive = ~np.isnan(tri[t])
        px = 20 * tri[t]
        noise = lambda s=0.15: np.exp(rng.normal(0, s, n_stocks))
        eps = px * (0.07 + 0.02 * cheap) * noise() * np.where(rng.random(n_stocks) < 0.08, -0.5, 1)
        bvps = px * (0.8 + 0.3 * cheap).clip(0.2) * noise()
        roa = 0.05 + 0.02 * quality + rng.normal(0, 0.02, n_stocks)
        mcap = size * tri[t]
        rows.append(pd.DataFrame({
            "date": d, "ticker": tickers, "sector": sectors, "price": px,
            "market_cap": mcap, "eps": eps, "eps_prev": eps * noise(0.2),
            "eps_5y_min": eps * np.where(rng.random(n_stocks) < 0.7 + 0.1 * quality.clip(-2, 2), 0.6, -0.2),
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
