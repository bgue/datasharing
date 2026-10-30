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

from dataclasses import dataclass, field, replace
from typing import Mapping

import numpy as np
import pandas as pd

from . import progress
from .serial import TAG, register_tag, serializable, to_dict as _to, from_dict as _from
from .construction import HoldRule, StandardWeighting
from .data import Market
from .exit_policy import ExitPolicy, as_overlay
from .pipeline import CashAccrual, PerSecurity, ScreenSelection, StandardCosts, TaxStage, resolve  # noqa: F401  (re-exported)
from .taxes import TaxConfig  # noqa: F401  (re-exported; also registers the tax serialization tags)
from .screens import Screen


# flat Config field -> the stage that holds it
_GROUP = {"cost_bps": "costs", "commission": "costs", "capital": "costs", "mer": "costs",
          "weighting": "construction", "max_weight": "construction", "sector_cap": "construction",
          "hold_rule": "construction", "cash_rate": "cash", "delist_return": "cash"}
_FLAT_ORDER = ("rebalance_months", "cost_bps", "commission", "capital", "mer", "delist_return", "min_holdings",
               "cash_rate", "weighting", "max_weight", "sector_cap", "hold_rule", "exit")
_FLAT_DEFAULT = dict(rebalance_months=12, cost_bps=25.0, commission=0.0, capital=100_000, mer=0.0,
                     delist_return=-0.30, min_holdings=3, cash_rate=0.0, weighting="equal", max_weight=None,
                     sector_cap=None, hold_rule=None, exit=None)
_STAGE_TYPE = {"costs": StandardCosts, "construction": StandardWeighting, "cash": CashAccrual}


def _is_default(v, d) -> bool:
    if isinstance(v, (pd.Series, pd.DataFrame, np.ndarray)) or type(v) is not type(d):
        return False
    try:
        return bool(v == d)
    except Exception:
        return False


@serializable("Config")
@dataclass(init=False)
class Config:
    """The backtest settings, grouped by pipeline stage (DESIGN.md J5):

        costs         CostModel     default `StandardCosts(cost_bps, commission, capital, mer)`
        construction  Weighting     default `StandardWeighting(weighting, max_weight, sector_cap, hold_rule)`
        cash          Accrual       default `CashAccrual(cash_rate, delist_return)`
        exit          ExitPolicy | None   exit policy: derisk on the exit screen's flag (None = always in)
        tax           TaxConfig | None    taxes (taxes.py, docs/TAXES.md): None = no tax, every number as before
        rebalance_months, min_holdings (fewer passes than this -> hold cash)

    Any object implementing the stage protocol (pipeline.py) may be put in a group. The flat fields of the
    default stages keep working as aliases, as constructor keywords, `replace(cfg, cost_bps=10)` overrides
    and attributes (`cfg.cost_bps`):

        cost_bps      one-way cost per security (spread + slippage), on |Δweight|
        commission    flat $ per trade (per security, per side);  capital: starting $, only matters for it
        mer           annual expense ratio per security (ETFs/funds), e.g. 0.0025
        delist_return applied when a holding vanishes mid-period;  cash_rate: annual return on cash
        weighting     "equal" | "cap" | "inv_vol" | f(snapshot.loc[picks]) -> weights
        max_weight / sector_cap  per-name / per-sector caps; excess redistributed pro rata, shortfall held in cash
        hold_rule     turnover control (keep_within / band)
    """
    rebalance_months: int = 12        # annual by default; the Globe's screen portfolios rebalance monthly
    min_holdings: int = 3             # fewer passes than this -> hold cash
    costs: object = field(default_factory=StandardCosts)
    construction: object = field(default_factory=StandardWeighting)
    exit: ExitPolicy | None = None
    cash: object = field(default_factory=CashAccrual)
    tax: TaxConfig | None = None

    def __init__(self, rebalance_months: int = 12, *, min_holdings: int = 3, costs=None, construction=None,
                 exit=None, cash=None, tax=None, **flat):
        bad = [k for k in flat if k not in _GROUP]
        if bad:
            raise TypeError(f"Config() got an unexpected keyword argument {bad[0]!r}")
        stages = {"costs": costs, "construction": construction, "cash": cash}
        for g, typ in _STAGE_TYPE.items():
            stage = typ() if stages[g] is None else stages[g]
            over = {k: v for k, v in flat.items() if _GROUP[k] == g}
            if over:
                try:
                    stage = replace(stage, **over)
                except TypeError:
                    raise TypeError(f"{type(stage).__name__} has no field(s) {sorted(over)}; "
                                    f"set them on the stage itself") from None
            object.__setattr__(self, g, stage)
        object.__setattr__(self, "rebalance_months", rebalance_months)
        object.__setattr__(self, "min_holdings", min_holdings)
        object.__setattr__(self, "exit", exit)
        object.__setattr__(self, "tax", tax)

    def __getattr__(self, name):
        g = _GROUP.get(name)
        stage = self.__dict__.get(g) if g else None
        if stage is None:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")
        return getattr(stage, name)

    def __setattr__(self, name, value):
        g = _GROUP.get(name)
        if g is None:
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, g, replace(getattr(self, g), **{name: value}))

    def flat(self) -> dict:
        """The 13 flat fields as a dict (a field a custom stage does not have is left out)."""
        out = {}
        for k in _FLAT_ORDER:
            try:
                out[k] = getattr(self, k)
            except AttributeError:
                pass
        return out

    def to_dict(self) -> dict:
        """Tagged dict of the non-default flat fields of the standard stages (so run keys of unchanged inputs
        stay as they were) and of any custom stage as `costs` / `construction` / `cash`."""
        out = {TAG: "Config"}
        custom = {g for g, typ in _STAGE_TYPE.items() if type(getattr(self, g)) is not typ}
        for k in _FLAT_ORDER:
            if _GROUP.get(k) in custom:
                continue
            v = getattr(self, k)
            if not _is_default(v, _FLAT_DEFAULT[k]):
                out[k] = _to(v)
        for g in sorted(custom):
            out[g] = _to(getattr(self, g))
        if self.tax is not None:
            out["tax"] = _to(self.tax)
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        return cls(**{k: _from(v) for k, v in d.items() if k != TAG})


@dataclass
class Result:
    name: str
    curve: pd.Series                  # portfolio value, 1.0 at the first formation date
    holdings: dict = field(default_factory=dict)   # rebalance date -> tickers
    turnover: pd.Series = None        # one-way turnover per rebalance
    costs: pd.Series = None           # fraction of value lost to trading per rebalance
    exposure: pd.Series = None        # share of the portfolio invested each bar (1.0 without an exit policy)
    switches: int = 0                 # number of exposure changes (exit policy)
    time_out: float = 0.0             # fraction of bars with exposure below 1
    park_curve: pd.Series = None      # growth of 1 in the parked asset (None without an exit policy)
    exit: ExitPolicy | None = None    # the policy that produced this result
    tax: pd.DataFrame | None = None   # tax per calendar year (taxes.TAX_COLS); None without Config.tax
    after_tax_curve: pd.Series | None = None   # the portfolio value after the tax paid so far (None without Config.tax)

    @property
    def returns(self) -> pd.Series:
        return self.curve.pct_change().dropna()


class CapTiered:
    """Per-security bps by market cap: {min market cap ($M): bps}. Smaller companies cost more to trade.

    A picklable callable (snapshot -> Series) that serializes as its tiers."""
    def __init__(self, tiers: Mapping[float, float]):
        self.tiers = tuple(sorted((float(k), float(v)) for k, v in dict(tiers).items()))

    def __call__(self, snap: pd.DataFrame) -> pd.Series:
        t = dict(self.tiers)
        lo = sorted(t)
        cap = snap["market_cap"].fillna(0)
        out = pd.Series(t[lo[0]], index=snap.index, dtype=float)
        for k in lo:
            out[cap >= k] = t[k]
        return out

    def __eq__(self, other):
        return isinstance(other, CapTiered) and self.tiers == other.tiers

    def __hash__(self):
        return hash(self.tiers)

    def __repr__(self):
        return f"CapTiered({dict(self.tiers)})"

    def to_dict(self) -> dict:
        return {TAG: "cap_tiered", "tiers": [[k, v] for k, v in self.tiers]}

    @classmethod
    def from_dict(cls, d: dict) -> "CapTiered":
        return cls({k: v for k, v in d["tiers"]})


CapTiered._serial_tag = "cap_tiered"
register_tag("cap_tiered", CapTiered.from_dict)


def cap_tiered(tiers: Mapping[float, float]) -> CapTiered:
    """{min market cap ($M): bps}. Smaller companies cost more to trade. Returns a `CapTiered`."""
    return CapTiered(tiers)


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


def simulate(screen: Screen, mkt: Market, cfg: Config, rebal: list, end=None, exposure=None,
             selection=None) -> Result:
    """Run `screen` on the schedule `rebal`: an orchestrator over the pipeline stages (pipeline.py).
    `exposure`: precomputed (lagged) exposure path of the exit policy; by default it is computed from
    `cfg.exit` on this schedule. `selection`: a `pipeline.Selection` (default: the screen's picks)."""
    end = pd.Timestamp(end) if end else mkt.index[-1]
    tri = mkt.prices
    value, prev = 1.0, pd.Series(dtype=float)        # prev = drifted weights of live holdings
    pieces = [pd.Series([1.0], index=[rebal[0]])]
    holdings, turn, costs = {}, {}, {}
    selection = selection or ScreenSelection()
    weighting, cost_model, accrual = cfg.construction, cfg.costs, cfg.cash
    taxer = TaxStage(cfg.tax, cfg.tax.capital if cfg.tax.capital is not None else getattr(cfg, "capital", 100_000)) \
        if cfg.tax is not None else None
    ov = as_overlay(cfg.exit)
    if ov is not None:
        held = exposure if exposure is not None else ov.exposure(mkt, rebal)
        dheld = held.diff().abs().fillna(0.0)
        park_pieces = []

    for j, d in enumerate(rebal):
        progress.report(j / len(rebal), f"{screen.name}: rebalance {j + 1}/{len(rebal)}")
        nxt = rebal[j + 1] if j + 1 < len(rebal) else end
        snap = mkt.view(d)
        picks = selection.picks(mkt, screen, d)                                        # Selection
        picks = weighting.retain(picks, screen, snap, prev, cfg.min_holdings)          # Weighting
        target = weighting.target(snap, picks, prev, cfg.min_holdings)

        dw = target.sub(prev, fill_value=0).abs()
        dw = dw[dw > 1e-12]
        trade_cost = float(cost_model.trade_cost(dw, snap, value))                     # CostModel
        if ov is not None:
            trade_cost *= float(held.get(d, 1.0))    # only the invested share of the sleeve is traded
        if taxer is not None:                                                          # TaxStage: gains realized by the trade
            taxer.trade(d, value, float(held.get(d, 1.0)) if ov is not None else 1.0, prev, target)
        value *= 1 - trade_cost
        holdings[d], turn[d], costs[d] = picks, dw.sum() / 2, trade_cost

        seg = tri.loc[d:nxt]
        yrs = ((seg.index - d).days / 365.25).to_numpy()
        if target.empty:
            path = pd.Series(accrual.cash_factor(yrs), index=seg.index)
            prev = pd.Series(dtype=float)
        else:
            g = seg[target.index] / seg[target.index].iloc[0]
            g, dead = accrual.delist(g)                                                # Accrual
            mer = cost_model.holding_drag(target.index, snap)
            g = g * np.power(1 - mer[None, :], yrs[:, None])         # expense drag accrues daily
            path = (g * target).sum(axis=1)
            if target.sum() < 1 - 1e-9:                              # unallocated remainder sits in cash
                path = path + (1 - target.sum()) * accrual.cash_factor(yrs)
            alive = ~dead.iloc[-1]
            prev = (g.iloc[-1] * target / path.iloc[-1])[alive]    # dead weight is uninvested cash
            sleeve = path.to_numpy()
        if ov is not None:                                                             # Overlay
            e = held.reindex(seg.index).to_numpy()
            park_r = ov.park_returns(mkt, seg.index, yrs, accrual)
            park_pieces.append(park_r[1:])
            if not target.empty:
                de = dheld.reindex(seg.index).to_numpy()
                path = pd.Series(ov.apply(path.to_numpy(), e, de, park_r), index=seg.index)
        if taxer is not None and not target.empty:                                     # TaxStage: income, switches, write-offs
            taxer.segment(seg.index, yrs, value, g, target, sleeve, path.to_numpy(), e if ov is not None else None,
                          dead, snap)
        pieces.append(value * path.iloc[1:])
        value *= path.iloc[-1]

    curve = pd.concat(pieces)
    curve = curve[~curve.index.duplicated(keep="last")]
    park_curve = None
    if ov is None:
        expo, switches, time_out = pd.Series(1.0, index=curve.index, name="exposure"), 0, 0.0
    else:
        expo = held.reindex(curve.index)
        switches = int((expo.diff().abs() > 1e-12).sum())
        time_out = float((expo < 1 - 1e-12).mean())
        pr = np.concatenate([np.zeros(1)] + park_pieces)
        park_curve = pd.Series(np.cumprod(1 + pr), index=curve.index, name="park")
    tax_table = after_curve = None
    if taxer is not None:
        tax_table, after_curve = taxer.finish(curve)
    return Result(screen.name, curve, holdings, pd.Series(turn), pd.Series(costs), expo, switches,
                  time_out, park_curve, ov.policy if ov is not None else None, tax_table, after_curve)


def run(screen: Screen, mkt: Market, cfg: Config = Config(), start=None, end=None,
        phase_days: int = 0) -> Result:
    start = start or mkt.index[0] + pd.DateOffset(months=13)    # room for 12-1 momentum
    rebal = schedule(mkt, start, cfg.rebalance_months, phase_days, end)
    pol = as_overlay(cfg.exit)
    if pol is None:
        return simulate(screen, mkt, cfg, rebal, end)
    # two passes: the fully-invested sleeve gives the curve portfolio-based signals read (only run when
    # some signal needs it), then the exposure path decides re-entry rescreens, then the real simulation
    curve = None
    if pol.needs_curve:
        with progress.scaled(0.0, 0.5):
            curve = simulate(screen, mkt, replace(cfg, exit=None, tax=None), rebal, end).curve
    expo = pol.exposure(mkt, rebal, curve)
    if pol.rescreen_on_reentry:
        stop = pd.Timestamp(end) if end else mkt.index[-1]
        rebal = sorted(set(rebal) | {d for d in pol.reentry_dates if rebal[0] < d < stop})
    with progress.scaled(0.5 if curve is not None else 0.0, 1.0):
        return simulate(screen, mkt, cfg, rebal, end, expo)


def benchmark_screen(top: int = 60) -> Screen:
    """Equal-weight the `top` largest names: a stand-in for the TSX 60."""
    return Screen(f"bench_top{top}", bench_top, top)


def bench_top(s: pd.DataFrame, n: int) -> list:
    """Screen function of `benchmark_screen`: the `n` largest names by market cap."""
    return s.nlargest(n, "market_cap").index.tolist()
