"""Periodic-rebalance, equal-weight backtester shared by every screen.

Works on whatever frequency the price index has (daily or monthly). Between
rebalances weights drift with prices; nothing is traded until the next
rebalance date.

Per-security inputs (`cost_bps`, `mer`) accept:
    float                       same value for every security
    dict / Series               {ticker: value}, with an optional "_default" key
    callable(snapshot)->Series  e.g. cap_tiered({0: 75, 1_000: 30, 10_000: 10})
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Union

import numpy as np
import pandas as pd

from .construction import HoldRule, target_weights
from .data import Market
from .screens import Screen

PerSecurity = Union[float, Mapping, pd.Series, Callable[[pd.DataFrame], pd.Series]]


@dataclass
class Config:
    rebalance_months: int = 12        # Rothery's screen portfolios are mostly annual
    cost_bps: PerSecurity = 25.0      # one-way cost per security (spread + slippage), on |Δweight|
    commission: float = 0.0           # flat $ per trade (per security, per side)
    capital: float = 100_000          # starting $; only matters for flat commissions
    mer: PerSecurity = 0.0            # annual expense ratio per security (ETFs/funds), e.g. 0.0025
    delist_return: float = -0.30      # applied when a holding vanishes mid-period
    min_holdings: int = 3             # fewer passes than this -> hold cash
    cash_rate: float = 0.0            # annual return on cash
    weighting: str | Callable = "equal"   # "equal" | "cap" | "inv_vol" | f(snapshot.loc[picks]) -> weights
    max_weight: float | None = None   # per-name cap; excess redistributed pro rata, shortfall held in cash
    sector_cap: float | None = None   # per-sector cap, same treatment
    hold_rule: HoldRule | None = None  # turnover control (keep_within / band)


@dataclass
class Result:
    name: str
    curve: pd.Series                  # portfolio value, 1.0 at the first formation date
    holdings: dict = field(default_factory=dict)   # rebalance date -> tickers
    turnover: pd.Series = None        # one-way turnover per rebalance
    costs: pd.Series = None           # fraction of value lost to trading per rebalance

    @property
    def returns(self) -> pd.Series:
        return self.curve.pct_change().dropna()


def resolve(spec: PerSecurity, tickers, snap: pd.DataFrame | None = None) -> pd.Series:
    tickers = pd.Index(tickers)
    if callable(spec) and not isinstance(spec, Mapping):
        return spec(snap.reindex(tickers)).reindex(tickers).fillna(0.0)
    if isinstance(spec, (Mapping, pd.Series)):
        spec = pd.Series(spec, dtype=float)
        return spec.reindex(tickers).fillna(spec.get("_default", 0.0))
    return pd.Series(float(spec), index=tickers)


def cap_tiered(tiers: Mapping[float, float]) -> Callable[[pd.DataFrame], pd.Series]:
    """{min market cap ($M): bps}. Smaller companies cost more to trade."""
    lo = sorted(tiers)

    def f(snap):
        cap = snap["market_cap"].fillna(0)
        out = pd.Series(tiers[lo[0]], index=snap.index, dtype=float)
        for t in lo:
            out[cap >= t] = tiers[t]
        return out
    return f


def schedule(mkt: Market, start, months: int, phase_days: int = 0, end=None) -> list[pd.Timestamp]:
    """Rebalance dates.

    phase_days = 0: form at `start`, then every `months` calendar months.
    phase_days = k: form at `start`, rebalance k trading days later, then every
    `months` from that new anniversary. Every phase covers the same window, so
    results line up date for date (the "rebalance timing luck" design).
    """
    idx = mkt.index
    end = pd.Timestamp(end) if end else idx[-1]
    start = mkt.trading_day(start)
    i0 = idx.get_loc(start)
    anchor = idx[min(i0 + phase_days, len(idx) - 1)]
    dates, j = [start], 0
    while True:
        d = mkt.trading_day(anchor + pd.DateOffset(months=months * j))
        if d >= end or (j > 0 and d <= dates[-1]):
            break
        if d > dates[-1]:
            dates.append(d)
        j += 1
    return dates


def simulate(screen: Screen, mkt: Market, cfg: Config, rebal: list, end=None) -> Result:
    end = pd.Timestamp(end) if end else mkt.index[-1]
    tri = mkt.prices
    value, prev = 1.0, pd.Series(dtype=float)        # prev = drifted weights of live holdings
    pieces = [pd.Series([1.0], index=[rebal[0]])]
    holdings, turn, costs = {}, {}, {}

    for j, d in enumerate(rebal):
        nxt = rebal[j + 1] if j + 1 < len(rebal) else end
        snap = mkt.view(d)
        picks = mkt.picks(screen, d)
        hr = cfg.hold_rule
        if hr and hr.keep_within and len(picks) >= cfg.min_holdings and len(prev):
            picks = hr.keep(picks, screen.ranked(snap), prev.index, screen.n)
        if len(picks) < cfg.min_holdings:
            target = pd.Series(dtype=float)
        elif (cfg.weighting == "equal" and cfg.max_weight is None and cfg.sector_cap is None):
            target = pd.Series(1 / len(picks), index=picks)
        else:
            target = target_weights(snap, picks, cfg.weighting, cfg.max_weight, cfg.sector_cap)
        if hr and hr.band:
            target = hr.apply_band(target, prev)

        dw = target.sub(prev, fill_value=0).abs()
        dw = dw[dw > 1e-12]
        trade_cost = float((dw * resolve(cfg.cost_bps, dw.index, snap)).sum()) / 1e4
        trade_cost += cfg.commission * len(dw) / (cfg.capital * value)
        value *= 1 - trade_cost
        holdings[d], turn[d], costs[d] = picks, dw.sum() / 2, trade_cost

        seg = tri.loc[d:nxt]
        yrs = ((seg.index - d).days / 365.25).to_numpy()
        if target.empty:
            path = pd.Series((1 + cfg.cash_rate) ** yrs, index=seg.index)
            prev = pd.Series(dtype=float)
        else:
            g = seg[target.index] / seg[target.index].iloc[0]
            last = g.ffill()
            dead = g.isna() & last.notna()
            g = g.where(~dead, last * (1 + cfg.delist_return))     # delisted -> haircut cash
            mer = resolve(cfg.mer, target.index, snap).to_numpy()
            g = g * np.power(1 - mer[None, :], yrs[:, None])         # expense drag accrues daily
            path = (g * target).sum(axis=1)
            if target.sum() < 1 - 1e-9:                              # unallocated remainder sits in cash
                path = path + (1 - target.sum()) * (1 + cfg.cash_rate) ** yrs
            alive = ~dead.iloc[-1]
            prev = (g.iloc[-1] * target / path.iloc[-1])[alive]    # dead weight is uninvested cash
        pieces.append(value * path.iloc[1:])
        value *= path.iloc[-1]

    curve = pd.concat(pieces)
    return Result(screen.name, curve[~curve.index.duplicated(keep="last")],
                  holdings, pd.Series(turn), pd.Series(costs))


def run(screen: Screen, mkt: Market, cfg: Config = Config(), start=None, end=None,
        phase_days: int = 0) -> Result:
    start = start or mkt.index[0] + pd.DateOffset(months=13)    # room for 12-1 momentum
    return simulate(screen, mkt, cfg, schedule(mkt, start, cfg.rebalance_months, phase_days, end), end)


def benchmark_screen(top: int = 60) -> Screen:
    """Equal-weight the `top` largest names: a stand-in for the TSX 60."""
    return Screen(f"bench_top{top}", lambda s, n: s.nlargest(n, "market_cap").index.tolist(), top)
