"""Screen definitions.

Each screen takes a point-in-time snapshot `s` (one row per ticker, ratios +
trailing price stats already joined) and returns an ordered list of tickers.
These are recreations of the *families* of screens Rothery regularly runs in
his Globe columns; the thresholds are sensible defaults to tune, not quotes
of any specific article's rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


@dataclass
class Screen:
    name: str
    fn: Callable[..., list[str]]
    n: int = 10                     # target number of holdings
    params: dict = field(default_factory=dict)
    note: str = ""

    def __call__(self, s: pd.DataFrame) -> list[str]:
        return self.fn(s, n=self.n, **self.params)


def _largest(s, k):  # liquidity / size filter used by most screens
    return s.nlargest(k, "market_cap")


# --- Dividend dogs: highest yielders among the biggest names -----------------
def dogs(s, n=10, universe=30):
    u = _largest(s, universe)
    return u[u["div_yield"] > 0].nlargest(n, "div_yield").index.tolist()


# --- Graham's Defensive Investor (The Intelligent Investor, ch. 14) ----------
def graham_defensive(s, n=10, min_cap_rank=150, max_pe=15, max_pe_pb=22.5,
                     min_cr=2.0, min_div_years=20):
    u = _largest(s, min_cap_rank)
    # Industrials: strong balance sheet. Utilities are exempt (Graham used debt/equity).
    strong = (u["current_ratio"] >= min_cr) & (u["lt_debt"] <= u["net_current_assets"].clip(lower=0))
    ok = strong | (u["sector"] == "Util")
    ok &= u["eps_5y_min"] > 0                       # no losses in the period
    ok &= u["div_years"] >= min_div_years           # uninterrupted dividends
    ok &= u["eps"] > u["eps_prev"]                  # stand-in for 10y EPS growth
    ok &= (u["pe"] <= max_pe) & (u["pe"] * u["pb"] <= max_pe_pb)
    return u[ok].assign(g=u["pe"] * u["pb"]).nsmallest(n, "g").index.tolist()


# --- Graham's Enterprising Investor: looser, smaller, cheaper ----------------
def graham_enterprising(s, n=10, max_pe=10, max_pb=1.2, min_cr=1.5):
    ok = ((s["current_ratio"] >= min_cr) & (s["eps_5y_min"] > 0)
          & (s["dividend"] > 0) & (s["eps"] > s["eps_prev"])
          & (s["pe"] <= max_pe) & (s["pb"] <= max_pb)
          & (s["lt_debt"] <= 1.1 * s["net_current_assets"].clip(lower=0)))
    return s[ok].nsmallest(n, "pb").index.tolist()


# --- Net-nets: price below net current asset value ---------------------------
def net_nets(s, n=15, min_ncav=1.0):
    ok = (s["ncav_to_mcap"] >= min_ncav) & (s["eps"] > 0)
    return s[ok].nlargest(n, "ncav_to_mcap").index.tolist()


# --- Piotroski F-score applied to the cheapest book-value quintile -----------
def f_score(s: pd.DataFrame) -> pd.Series:
    pts = [s["roa"] > 0, s["cfo_to_assets"] > 0, s["roa"] > s["roa_prev"],
           s["cfo_to_assets"] > s["roa"], s["leverage"] < s["leverage_prev"],
           s["current_ratio"] > s["current_ratio_prev"], s["shares"] <= s["shares_prev"],
           s["gross_margin"] > s["gross_margin_prev"],
           s["asset_turnover"] > s["asset_turnover_prev"]]
    return sum(p.astype(int) for p in pts)


def piotroski(s, n=10, pb_quantile=0.2, min_score=8):
    cheap = s[s["pb"] <= s["pb"].quantile(pb_quantile)]
    sc = f_score(cheap)
    return cheap.assign(f=sc)[sc >= min_score].sort_values(["f", "pb"], ascending=[False, True]) \
        .head(n).index.tolist()


# --- Greenblatt's Magic Formula: rank on earnings yield + return on capital --
def magic_formula(s, n=15, min_cap_rank=200, exclude=("Fin", "Util")):
    u = _largest(s, min_cap_rank)
    u = u[~u["sector"].isin(exclude) & (u["ebit"] > 0)]
    rank = u["earnings_yield"].rank(ascending=False) + u["roic"].rank(ascending=False)
    return rank.nsmallest(n).index.tolist()


# --- Cheap & strong: low P/E + low P/B, then the best 12-1 momentum ----------
def value_momentum(s, n=10, value_pool=40):
    u = s.dropna(subset=["pe", "pb", "mom_12_1"])
    v = (u["pe"].rank() + u["pb"].rank()).nsmallest(value_pool).index
    return u.loc[v].nlargest(n, "mom_12_1").index.tolist()


# --- Low-volatility dividend payers -----------------------------------------
def low_vol_dividend(s, n=15, universe=100):
    u = _largest(s, universe)
    u = u[(u["div_yield"] > 0) & (u["eps"] > 0)].dropna(subset=["vol_1y"])
    return u.nsmallest(n, "vol_1y").index.tolist()


REGISTRY: dict[str, Screen] = {sc.name: sc for sc in [
    Screen("dogs", dogs, 10, note="Top-10 yield from the 30 largest (Dogs/Beat the TSX)"),
    Screen("graham_defensive", graham_defensive, 10, note="Graham defensive investor"),
    Screen("graham_enterprising", graham_enterprising, 10, note="Graham enterprising investor"),
    Screen("net_nets", net_nets, 15, note="Price < NCAV"),
    Screen("piotroski", piotroski, 10, note="F-score >= 8 in cheapest P/B quintile"),
    Screen("magic_formula", magic_formula, 15, note="Greenblatt EY + ROIC rank"),
    Screen("value_momentum", value_momentum, 10, note="Low P/E+P/B, best 12-1 momentum"),
    Screen("low_vol_dividend", low_vol_dividend, 15, note="Least volatile dividend payers"),
]}
