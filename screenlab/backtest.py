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
    harvest: pd.DataFrame | None = None        # P2: loss-harvest records (harvesting.HARVEST_COLS); None without Config.tax.harvest
    flows: pd.DataFrame | None = None          # PA: external flows executed (index = date; columns amount, cost); None without flows
    cash_curve: pd.Series | None = None        # PA: uninvested cash per bar (cash_timing="hold"); None without flows

    @property
    def returns(self) -> pd.Series:
        return self.curve.pct_change().dropna()


class CapTiered:
    """Per-security bps by market cap: {min market cap ($M): bps}. Smaller companies cost more to trade.

    A picklable callable (snapshot -> Series) that serializes as its tiers."""
    def __init__(self, tiers: Mapping[float, float]):
        self.tiers = tuple(sorted((float(k), float(v)) for k, v in dict(tiers).items()))

    def bps_for_caps(self, cap) -> np.ndarray:
        """bps for an array of market caps (any shape; NaN counts as 0, i.e. the lowest tier)."""
        cap = np.nan_to_num(np.asarray(cap, dtype=float), nan=0.0)
        lows = np.array([k for k, _ in self.tiers])
        bps = np.array([v for _, v in self.tiers])
        return bps[np.maximum(np.searchsorted(lows, cap, side="right") - 1, 0)]

    def __call__(self, snap: pd.DataFrame) -> pd.Series:
        return pd.Series(self.bps_for_caps(snap["market_cap"].to_numpy(float)), index=snap.index, dtype=float)

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
             selection=None, flows=None, flow_mode: str = "invest") -> Result:
    """Run `screen` on the schedule `rebal`: an orchestrator over the pipeline stages (pipeline.py).
    `exposure`: precomputed (lagged) exposure path of the exit policy; by default it is computed from
    `cfg.exit` on this schedule. `selection`: a `pipeline.Selection` (default: the screen's picks).

    PA: `flows` (default None = unchanged behaviour): a DataFrame indexed by EXECUTED trading day with a column
    `amount` (dollars; > 0 contribution, < 0 withdrawal). The first formation date must carry a positive flow: it is the
    starting value, and the curve is then in dollars (not per $1). A later flow, `flow_mode="invest"`: a contribution
    buys the target weights of the last rebalance pro rata (CostModel per name, cost base = what was spent), a
    withdrawal sells every holding pro rata (the cost is paid out of the sale: the investor receives `amount`); on a
    rebalance date the contribution joins the rebalance trade. `"hold"`: contributions wait in cash (Accrual rate, taxed
    as interest) until the next rebalance date; withdrawals take the cash first. Flows on or after `end` are ignored.
    See plan.py for the arithmetic. TaxStage sees the flows through `external_flow`."""
    end = pd.Timestamp(end) if end else mkt.index[-1]
    tri = mkt.prices
    value, prev = 1.0, pd.Series(dtype=float)        # prev = drifted weights of live holdings
    pieces = [pd.Series([1.0], index=[rebal[0]])]
    holdings, turn, costs = {}, {}, {}
    selection = selection or ScreenSelection()
    weighting, cost_model, accrual = cfg.construction, cfg.costs, cfg.cash
    taxer = TaxStage(cfg.tax, cfg.tax.capital if cfg.tax.capital is not None else getattr(cfg, "capital", 100_000)) \
        if cfg.tax is not None else None
    if taxer is not None:
        taxer.bind(mkt)                                                                # P3: security_meta + FX series
    ov = as_overlay(cfg.exit)
    if ov is not None:
        held = exposure if exposure is not None else ov.exposure(mkt, rebal)
        dheld = held.diff().abs().fillna(0.0)
        park_pieces = []
    # P2: tax-loss harvesting (harvesting.py): extra steps at year ends / swap-back dates, and a hook after Weighting
    steps, hv, last_reb = list(rebal), None, rebal[0]
    if taxer is not None and cfg.tax.harvest is not None:
        hv = cfg.tax.harvest.start(mkt, taxer, screen, weighting, cfg.min_holdings)
        steps = sorted(set(steps) | set(hv.extra_dates(mkt.index, rebal[0], end, held if ov is not None else None)))
        if ov is not None:                                                             # Q3: exit-switch losses and re-entry steps
            taxer.on_switch = hv.exit_sale
            held_prev = held.shift(1).fillna(1.0)
    rebal_set = set(rebal)
    # PA: external flows (plan.py)
    fl, cash, tgt_keep, flog, cash_pieces, hv_set = None, 0.0, pd.Series(dtype=float), [], [], set(steps) - set(rebal)
    if flows is not None:
        if flow_mode not in ("invest", "hold"):
            raise ValueError(f"flow_mode must be 'invest' or 'hold', got {flow_mode!r}")
        fl = (flows["amount"] if isinstance(flows, pd.DataFrame) else flows).astype(float).groupby(level=0).sum()
        first = float(fl.get(rebal[0], 0.0))
        if first <= 0:
            raise ValueError("flows: the first formation date needs a positive flow (the starting value)")
        value = first
        pieces = [pd.Series([value], index=[rebal[0]])]
        fl = fl[(fl.index > rebal[0]) & (fl.index < end) & (fl != 0)]
        steps = sorted(set(steps) | set(fl.index))
        flog.append((rebal[0], first, 0.0))

    def flow_step(d, snap, A, value, cash, prev):
        """PA: apply the flow `A` (and join held cash at a rebalance) at the start of step `d`.
        Returns value, cash, prev (sleeve weights), pure (a flow-only step: nothing else is traded)."""
        pure = A != 0.0 and d not in rebal_set and (hv is None or d not in hv_set)
        e = float(held.get(d, 1.0)) if ov is not None else 1.0
        ext = cost_tot = 0.0
        if A < 0:                                                       # withdrawal: cash first, then pro rata
            R = -A
            take = min(cash, R)
            cash -= take
            R -= take
            received = take
            if R > 1e-12 and value > 1e-15:
                cost_i = pd.Series(dtype=float)
                G = min(R, value)
                if len(prev):
                    ws = prev * e
                    for _ in range(12):                                 # the sale cost comes out of the proceeds
                        G = min(R + float(cost_model.trade_cost(ws * (G / value), snap, value)) * value, value)
                    dw = ws * (G / value)
                    cost_i = pd.Series({n: float(cost_model.trade_cost(dw[[n]], snap, value)) * value for n in dw.index})
                cost_tot = float(cost_i.sum())
                received += G - cost_tot
                if taxer is not None:
                    taxer.external_flow(d, -G, value, e, prev, tgt_keep, cost_i)
                value -= G
            ext = -received
        join = 0.0
        if d in rebal_set:                                              # held cash joins the rebalance
            join, cash = cash, 0.0
        if A > 0:
            ext = A
            if pure and flow_mode == "invest":                          # buy the last target weights now
                tg = tgt_keep
                if len(tg):
                    tg = tg[tri.loc[d, tg.index].notna().to_numpy()]
                names = prev.index.union(tg.index)
                cost_i = pd.Series(dtype=float)
                if len(tg) and e > 1e-12:
                    dw = tg * (A / (value + A))
                    cost_i = pd.Series({n: float(cost_model.trade_cost(dw[[n]], snap, value + A)) * (value + A) * e
                                        for n in tg.index})
                    cost_tot = float(cost_i.sum())
                    hold_ = prev.reindex(names, fill_value=0.0) * value * e + tg.reindex(names, fill_value=0.0) * A * e \
                        - cost_i.reindex(names, fill_value=0.0)
                    new_prev = hold_ / (value * e + A * e - cost_tot)
                    new_prev = new_prev[new_prev > 0]
                else:                                                   # no target (cash) or all parked
                    new_prev = prev if e <= 1e-12 or not len(prev) else prev * (value / (value + A))
                if taxer is not None:
                    taxer.external_flow(d, A, value, e, prev, tg, cost_i)
                value, prev = value + A - cost_tot, new_prev
            elif d in rebal_set or flow_mode == "invest":               # joins the rebalance trade
                join += A
            else:                                                       # "hold": waits for the next rebalance
                cash += A
        if join > 0:
            if taxer is not None:
                taxer.external_flow(d, join, value, e, prev, tgt_keep, trade_follows=True)
            if value > 0 and len(prev):
                prev = prev * (value / (value + join))
            value += join
        if A != 0.0:
            flog.append((d, ext, cost_tot))
        return value, cash, prev, pure

    for j, d in enumerate(steps):
        progress.report(j / len(steps), f"{screen.name}: rebalance {j + 1}/{len(steps)}")
        snap = mkt.view(d)
        pure = False
        if fl is not None:                                                             # PA: external flows
            value, cash, prev, pure = flow_step(d, snap, float(fl.get(d, 0.0)), value, cash, prev)
        if pure:                                                                       # PA: flow-only step: no screen, no rebalance
            target = prev
        else:
            e_now = float(held.get(d, 1.0)) if ov is not None else 1.0                     # Q3: equity share held at d
            e_prev = float(held_prev.get(d, 1.0)) if hv is not None and ov is not None else e_now
            vv = value * e_now if hv is not None else value                                # Q3: the equity money the stage sees
            if hv is None or d in rebal_set:
                picks = raw = selection.picks(mkt, screen, d)                              # Selection
                picks = weighting.retain(picks, screen, snap, prev, cfg.min_holdings)      # Weighting
                target = weighting.target(snap, picks, prev, cfg.min_holdings)
                if hv is not None:                                                         # P2: harvest after Weighting
                    picks, target = hv.on_rebalance(d, snap, raw, picks, prev, vv, target)
                    last_reb = d
            else:                                                                          # P2: year end / swap back: no screen run
                target = hv.on_substep(d, snap, prev, vv, reentry=e_now > e_prev + 1e-12)       # Q3: exit re-entry
                picks = list(target.index)
            if hv is not None:                                                             # P2: new dates (swap back after a window)
                for nd in hv.take_new_dates():
                    if d < nd < end and nd not in steps:
                        steps.insert(int(np.searchsorted(steps, nd)), nd)
                        hv_set.add(nd)
            if fl is not None and (hv is None or d in rebal_set):                  # PA: the strategy target new money buys
                tgt_keep = target

            dw = target.sub(prev, fill_value=0).abs()
            dw = dw[dw > 1e-12]
            trade_cost = float(cost_model.trade_cost(dw, snap, value))                     # CostModel
            if ov is not None:
                trade_cost *= float(held.get(d, 1.0))    # only the invested share of the sleeve is traded
            if taxer is not None:                                                          # TaxStage: gains realized by the trade
                ev_ = float(held.get(d, 1.0)) if ov is not None else 1.0
                # P1: each name's own trading cost (cost models are linear in the names), for the cost base / proceeds
                tcost = pd.Series({n: float(cost_model.trade_cost(dw[[n]], snap, value)) * ev_ * value for n in dw.index}, dtype=float)
                if hv is not None:
                    hv.pre_trade()                                                         # P2
                taxer.trade(d, value, ev_, prev, target, tcost)
                if hv is not None:
                    hv.post_trade(d, vv, prev, target, min(e_prev / e_now, 1.0) if e_now > 0 else 1.0)   # P2: superficial-loss bookkeeping (Q3: exit re-entry)
            value *= 1 - trade_cost
            if hv is None or d in rebal_set:
                holdings[d], turn[d], costs[d] = picks, dw.sum() / 2, trade_cost
            else:                                                                          # P2: a sub-step adds to its rebalance period
                turn[last_reb] += dw.sum() / 2
                costs[last_reb] += trade_cost

        nxt = steps[j + 1] if j + 1 < len(steps) else end
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
                          dead, snap, cash_factor=accrual.cash_factor(yrs),                          # P1: interest events
                          policy=getattr(ov, "policy", None), park_r=park_r if ov is not None else None)
        elif taxer is not None:                                                        # P1: all cash: interest only
            taxer.cash_segment(seg.index, value, accrual.cash_factor(yrs))
        if cash > 0:                                                                   # PA: "hold" cash accrues alongside
            cfs = np.asarray(accrual.cash_factor(yrs), float)
            pieces.append(value * path.iloc[1:] + cash * cfs[1:])
            cash_pieces.append(pd.Series(cash * cfs[1:], index=seg.index[1:]))
            if taxer is not None:
                taxer.hold_cash_interest(seg.index, cash, cfs)
            cash *= cfs[-1]
        else:
            pieces.append(value * path.iloc[1:])
            if fl is not None:
                cash_pieces.append(pd.Series(0.0, index=seg.index[1:]))
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
    flow_df = cash_curve = None
    if fl is not None:                                                                 # PA
        flow_df = pd.DataFrame(flog, columns=["date", "amount", "cost"]).set_index("date")
        cc = pd.concat([pd.Series([0.0], index=[rebal[0]])] + cash_pieces)
        cash_curve = cc[~cc.index.duplicated(keep="last")].reindex(curve.index).fillna(0.0).rename("cash")
    return Result(screen.name, curve, holdings, pd.Series(turn), pd.Series(costs), expo, switches,
                  time_out, park_curve, ov.policy if ov is not None else None, tax_table, after_curve,
                  harvest=hv.finish(curve, after_curve) if hv is not None else None,         # P2: harvest records
                  flows=flow_df, cash_curve=cash_curve)                                      # PA


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
