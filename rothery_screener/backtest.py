"""Periodic-rebalance, equal-weight backtester shared by every screen."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import snapshot, trailing_stats
from .screens import Screen


@dataclass
class Config:
    rebalance_months: int = 12        # Rothery's screen portfolios are mostly annual
    cost_bps: float = 50              # round-trip cost charged on turnover
    delist_return: float = -0.30      # applied when a holding vanishes mid-period
    min_holdings: int = 3             # fewer passes than this -> hold cash
    cash_return_m: float = 0.0


@dataclass
class Result:
    name: str
    returns: pd.Series                # monthly portfolio returns
    holdings: dict                    # rebalance date -> list of tickers
    turnover: pd.Series


def run(screen: Screen, fund: pd.DataFrame, prices: pd.DataFrame, cfg: Config = Config(),
        start: str | None = None) -> Result:
    mrets = prices.pct_change(fill_method=None)
    dates = prices.index[prices.index >= (pd.Timestamp(start) if start else prices.index[12])]
    rebal = set(dates[::cfg.rebalance_months])

    out, holdings, turn = {}, {}, {}
    weights = pd.Series(dtype=float)
    for i, d in enumerate(dates[:-1]):
        if d in rebal:
            s = snapshot(fund, d).join(trailing_stats(prices, d))
            s = s[prices.loc[d].reindex(s.index).notna()]       # tradable today
            picks = screen(s)
            new = (pd.Series(1 / len(picks), index=picks) if len(picks) >= cfg.min_holdings
                   else pd.Series(dtype=float))
            t = new.sub(weights, fill_value=0).abs().sum() / 2
            holdings[d], turn[d] = picks, t
            cost = t * cfg.cost_bps / 1e4
            weights = new
        else:
            cost = 0.0

        nxt = dates[i + 1]
        if weights.empty:
            out[nxt] = cfg.cash_return_m - cost
            continue
        r = mrets.loc[nxt, weights.index]
        r = r.where(r.notna(), cfg.delist_return)               # survivorship haircut
        out[nxt] = float((weights * r).sum()) - cost
        # drift weights; drop delisted names (cash proceeds re-spread pro rata)
        weights = (weights * (1 + r))[mrets.loc[nxt, weights.index].notna()]
        weights = weights / weights.sum() if weights.sum() > 0 else pd.Series(dtype=float)

    return Result(screen.name, pd.Series(out), holdings, pd.Series(turn))


def equal_weight_benchmark(prices: pd.DataFrame, fund: pd.DataFrame, top: int = 60,
                           start=None, cfg: Config = Config()) -> Result:
    """Equal-weight the `top` largest names: a stand-in for the TSX 60."""
    bench = Screen("bench_top%d" % top, lambda s, n: s.nlargest(n, "market_cap").index.tolist(), top)
    return run(bench, fund, prices, cfg, start)
