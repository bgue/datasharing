"""Simulation pipeline: the stages `backtest.simulate` is an orchestrator over (DESIGN.md J5).

    Selection -> Weighting -> CostModel -> Overlay -> Accrual

* `Selection`  screen picks at a rebalance date.                       default `ScreenSelection`
* `Weighting`  hold rule on the picks, then target weights (caps).      default `construction.StandardWeighting`
* `CostModel`  trade cost on weight changes + holding cost (MER).       default `StandardCosts`
* `Overlay`    exit exposure path and parked-money returns.            default `exit_policy.PolicyOverlay`
* `Accrual`    cash rate and the delisting haircut.                     default `CashAccrual`
* `TaxStage`   (optional, `Config.tax`) realized gains, dividend income, tax by year; see taxes.py

`Config` carries the settings (`cfg.costs`, `cfg.construction`, `cfg.exit`, `cfg.cash`); any object that
implements the matching protocol can be put there instead, so a new cost model, weighting or accrual is a class
in user code, not an edit of backtest.py. Stages that should survive a RunSpec / run key use `@serializable`.

`simulate_paths` runs the same stages over many price paths at once with numpy (Monte Carlo, DESIGN.md K).
Every stage method that `simulate_paths` calls works on arrays with a leading path axis; the scalar `simulate`
calls the same methods with one path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, NamedTuple, Protocol, Union, runtime_checkable

import warnings

import numpy as np
import pandas as pd

from . import progress
from .serial import serializable

PerSecurity = Union[float, Mapping, pd.Series, Callable[[pd.DataFrame], pd.Series]]


def resolve(spec: PerSecurity, tickers, snap: pd.DataFrame | None = None) -> pd.Series:
    """A per-security input (float, {ticker: value} with optional "_default", or callable(snapshot)) as a Series."""
    tickers = pd.Index(tickers)
    if callable(spec) and not isinstance(spec, Mapping):
        if snap is None:
            raise NotImplementedError("a per-security callable (e.g. cap_tiered) needs a snapshot, which "
                                      "simulate_paths does not have per path; use a number or {ticker: value}")
        return spec(snap.reindex(tickers)).reindex(tickers).fillna(0.0)
    if isinstance(spec, (Mapping, pd.Series)):
        spec = pd.Series(spec, dtype=float)
        return spec.reindex(tickers).fillna(spec.get("_default", 0.0))
    return pd.Series(float(spec), index=tickers)


# --------------------------------------------------------------------------- protocols
@runtime_checkable
class Selection(Protocol):
    """Which names the screen picks at a rebalance date."""
    def picks(self, mkt, screen, d: pd.Timestamp) -> list: ...


@runtime_checkable
class Weighting(Protocol):
    """Turns picks into target weights. `retain` applies the hold rule to the picks (names kept for turnover
    control), `target` returns weights summing to <= 1 (empty = hold cash). `prev` = drifted weights of the
    live holdings just before the rebalance."""
    def retain(self, picks: list, screen, snap: pd.DataFrame, prev: pd.Series, min_holdings: int) -> list: ...
    def target(self, snap: pd.DataFrame, picks: list, prev: pd.Series, min_holdings: int) -> pd.Series: ...


@runtime_checkable
class CostModel(Protocol):
    """Trading and holding costs.

    `trade_cost(dw, snap, value)`: fraction of portfolio value lost to trading. `dw` holds the absolute weight
    changes of the traded names (zero = not traded): a Series (tickers) for one path, or a DataFrame (paths x
    tickers) for many; `value` is the portfolio value (float, or array with one entry per path). Return a float,
    or an array with one entry per path.
    `holding_drag(tickers, snap)`: annual expense ratio per ticker as an ndarray (accrues daily)."""
    def trade_cost(self, dw, snap: pd.DataFrame, value): ...
    def holding_drag(self, tickers, snap: pd.DataFrame) -> np.ndarray: ...


@runtime_checkable
class Overlay(Protocol):
    """Exit exposure over time and the parked money. `policy` is the ExitPolicy behind it (reported on the
    Result). `exposure` is the (lagged) share invested per bar; `park_returns` the per-bar return of the parked
    share over `index` (first element 0); `apply` mixes the sleeve path with the parked asset, for a path of
    shape (..., T) and exposure `e` / change `de` of shape (T,) or (..., T)."""
    policy: object
    needs_curve: bool
    rescreen_on_reentry: bool
    reentry_dates: list
    def exposure(self, mkt, rebalance_dates, curve=None) -> pd.Series: ...
    def park_returns(self, mkt, index: pd.DatetimeIndex, yrs: np.ndarray, accrual: "Accrual") -> np.ndarray: ...
    def apply(self, path: np.ndarray, e, de, park_r) -> np.ndarray: ...


@runtime_checkable
class Accrual(Protocol):
    """Cash and delistings. `cash_factor(yrs)`: growth of 1 in cash after `yrs` years. `cash_returns(yrs)`:
    per-bar cash return along an elapsed-years axis. `delist(g)`: growth factors with a delisted name's value
    replaced by its last price times (1 + haircut); returns (g, dead) where `dead` flags names gone at the end
    of the segment. `g` is a DataFrame (bars x names) or an ndarray (paths, bars, names)."""
    cash_rate: float
    def cash_factor(self, yrs: np.ndarray) -> np.ndarray: ...
    def cash_returns(self, yrs: np.ndarray) -> np.ndarray: ...
    def delist(self, g): ...


# --------------------------------------------------------------------------- default stages
@dataclass(frozen=True)
class ScreenSelection:
    """The screen's picks on the market's snapshot at `d` (today's behaviour)."""
    def picks(self, mkt, screen, d) -> list:
        return mkt.picks(screen, d)


@serializable("Costs")
@dataclass(frozen=True)
class StandardCosts:
    """Per-security bps on |weight change|, a flat commission per trade, and a per-security MER accrual.

    cost_bps   one-way cost per security (spread + slippage)
    commission flat $ per trade (per security, per side), against `capital` x portfolio value
    mer        annual expense ratio per security (ETFs / funds)
    Per-security inputs accept a float, {ticker: value} (optional "_default") or callable(snapshot)."""
    cost_bps: PerSecurity = 25.0
    commission: float = 0.0
    capital: float = 100_000
    mer: PerSecurity = 0.0

    def trade_cost(self, dw, snap, value):
        if isinstance(dw, pd.DataFrame):                                # many paths: rows = paths
            bps = resolve(self.cost_bps, dw.columns, snap).to_numpy()
            x = dw.to_numpy()
            tc = (x * bps[None, :]).sum(axis=1) / 1e4
            return tc + self.commission * (x > 0).sum(axis=1) / (self.capital * np.asarray(value))
        tc = float((dw * resolve(self.cost_bps, dw.index, snap)).sum()) / 1e4
        return tc + self.commission * len(dw) / (self.capital * value)

    def holding_drag(self, tickers, snap) -> np.ndarray:
        return resolve(self.mer, tickers, snap).to_numpy()


@serializable("Accrual")
@dataclass(frozen=True)
class CashAccrual:
    """Cash earns `cash_rate` a year; a holding that vanishes mid-period is written to its last price times
    (1 + delist_return)."""
    cash_rate: float = 0.0
    delist_return: float = -0.30

    def cash_factor(self, yrs):
        return (1 + self.cash_rate) ** yrs

    def cash_returns(self, yrs):
        return np.power(1 + self.cash_rate, np.diff(yrs, prepend=yrs[0])) - 1

    def delist(self, g):
        if isinstance(g, pd.DataFrame):
            last = g.ffill()
            dead = g.isna() & last.notna()
            return g.where(~dead, last * (1 + self.delist_return)), dead
        a = np.asarray(g, dtype=float)                                  # (paths, bars, names)
        nan = np.isnan(a)
        if not nan.any():
            return a, nan
        T = a.shape[1]
        pos = np.where(~nan, np.arange(T)[None, :, None], -1)
        np.maximum.accumulate(pos, axis=1, out=pos)                     # index of the last valid bar
        last = np.take_along_axis(a, np.maximum(pos, 0), axis=1)
        last = np.where(pos >= 0, last, np.nan)
        dead = nan & (pos >= 0)
        return np.where(dead, last * (1 + self.delist_return), a), dead


# --------------------------------------------------------------------------- tax
class TaxStage:
    """Collects the taxable events of one `simulate` run and has `taxes.assess` turn them into tax (DESIGN.md J5).

    Everything is kept in pre-tax wealth units (start wealth 1.0). The accounting is homogeneous in wealth, so the
    tax that is later paid from the portfolio (which shrinks positions and cost basis in proportion) does not change
    any ratio recorded here; `taxes.assess` converts units to dollars with the after-tax factor in force.

    Events:
      trade      each rebalance sells the drop in weight of every name (realizing gain = proceeds - average cost
                 of the part sold; average cost per name, "lot level" = one pooled lot) and buys the rise.
      dividends  per bar, income = value at the start of the bar x the dividend yield x the bar's length in years.
                 APPROXIMATION: the yield is `dividend / price` of the snapshot at the last rebalance, held
                 constant to the next one, paid pro rata per bar; the real dates and amounts of dividends are not
                 in the data. The dividends are inside the total-return prices, so they are reinvested: each adds
                 to the name's cost basis (the price gain on a later sale excludes them).
      switches   an exit-policy exposure change sells the matching share of the equity sleeve (gain realized) into
                 the parked asset, or sells the parked asset (gain realized) to buy equity. The parked asset's
                 gain is taxed as a capital gain when sold; interest on cash is not taxed. Between switches the mix
                 is treated as rebalanced for free.
      delisting  a name written off at the end of its segment realizes its loss (value at the haircut - cost).
    Trading costs are not added to the cost basis nor deducted from proceeds. Tax payments are taken pro rata from
    all holdings without realizing the gain on the part sold."""

    def __init__(self, tax, capital: float):
        self.tax, self.capital = tax, float(capital)
        self.basis = pd.Series(dtype=float)          # name -> cost basis, wealth units
        self.pb = None                               # cost basis of the parked asset (None until the first trade)
        self._ev: list = []
        self._last_mv = pd.Series(dtype=float)
        self._last_park = 0.0

    # ---- events
    def _gains(self, date, gains) -> None:
        g = np.asarray(gains, float)
        self._ev.append(pd.DataFrame({"gain": [g[g > 0].sum()], "loss": [g[g < 0].sum()]}, index=[date]))

    def trade(self, d, value: float, e: float, prev: pd.Series, target: pd.Series) -> None:
        """Rebalance at `d`: `value` wealth before the trade cost, `e` equity share, `prev` / `target` drifted and new
        sleeve weights."""
        if self.pb is None:
            self.pb = value * (1 - e)
        M = value * e
        names = prev.index.union(target.index)
        p, t = prev.reindex(names, fill_value=0.0).to_numpy(), target.reindex(names, fill_value=0.0).to_numpy()
        b = self.basis.reindex(names, fill_value=0.0).to_numpy()
        sell, buy = np.maximum(p - t, 0.0) * M, np.maximum(t - p, 0.0) * M
        den = p * M
        frac = np.divide(sell, den, out=np.zeros_like(sell), where=den > 0)
        gains = sell - b * frac
        self._gains(d, gains[sell > 1e-15])
        b = b * (1 - frac) + buy
        self.basis = pd.Series(b, index=names)[lambda s: (s != 0) | s.index.isin(target.index)]

    def segment(self, seg_index, yrs, value0: float, g: pd.DataFrame, target: pd.Series, S: np.ndarray, P: np.ndarray,
                e: np.ndarray | None, dead: pd.DataFrame, snap: pd.DataFrame) -> None:
        """One holding period. `value0` wealth after the trade cost, `g` growth of each name (after delisting and
        MER), `S` / `P` sleeve / total growth path (equal without an overlay), `e` exposure per bar."""
        names, tax = target.index, self.tax
        n_b = len(seg_index)
        e = np.ones(n_b) if e is None else np.asarray(e, float)
        G = np.nan_to_num(g.to_numpy() * target.to_numpy())
        V = value0 * np.asarray(P)
        w = G / np.asarray(S)[:, None]
        mv = (V * e)[:, None] * w                                      # market value of each name per bar
        yld = snap["div_yield"].reindex(names).fillna(0.0).clip(lower=0.0).to_numpy() if "div_yield" in snap \
            else np.zeros(len(names))
        fx = tax.foreign_mask(names, snap)
        alive = ~dead.to_numpy()
        D = np.zeros_like(mv)
        D[1:] = mv[:-1] * yld[None, :] * np.diff(yrs)[:, None] * alive[:-1]
        self._ev.append(pd.DataFrame({"div_ca": D[:, ~fx].sum(axis=1), "div_fx": D[:, fx].sum(axis=1)}, index=seg_index))
        cd = np.cumsum(D, axis=0)
        b = self.basis.reindex(names, fill_value=0.0).to_numpy()
        last, tiny = 0, 1e-12
        sw = np.flatnonzero(np.abs(np.diff(e)) > tiny) + 1
        for i in sw:
            b = b + cd[i] - cd[last]
            last = i
            e0, e1, Vi = e[i - 1], e[i], V[i]
            if e1 < e0 and e0 > 0:                                      # equity sold into the parked asset
                s = (e0 - e1) / e0
                mvpre = Vi * e0 * w[i]
                self._gains(seg_index[i], s * (mvpre - b))
                b = b * (1 - s)
                self.pb += s * float(mvpre.sum())
            elif e1 > e0 and e0 < 1:                                    # parked asset sold, equity bought
                s = min((e1 - e0) / (1 - e0), 1.0)
                pmv = Vi * (1 - e0)
                self._gains(seg_index[i], [s * (pmv - self.pb)])
                self.pb *= 1 - s
                tot = G[i].sum()
                if tot > 0:
                    b = b + Vi * (e1 - e0) * G[i] / tot
        b = b + cd[-1] - cd[last]
        dd = dead.iloc[-1].to_numpy()
        if dd.any():                                                    # written-off names realize their loss
            self._gains(seg_index[-1], mv[-1][dd] - b[dd])
            b[dd] = 0.0
        self.basis = pd.Series(b, index=names)
        self._last_mv = pd.Series(np.where(dd, 0.0, mv[-1]), index=names)
        self._last_park = float(V[-1] * (1 - e[-1]))

    def finish(self, curve: pd.Series, mkt=None) -> tuple:
        """(tax table per year, after-tax curve) for the pre-tax `curve`."""
        from .taxes import assess
        if self.tax.liquidate_at_end:
            gains = list((self._last_mv - self.basis.reindex(self._last_mv.index, fill_value=0.0)).to_numpy())
            if self.pb is not None:
                gains.append(self._last_park - self.pb)
            self._gains(curve.index[-1], gains)
        ev = pd.concat(self._ev) if self._ev else pd.DataFrame()
        ev = ev.groupby(level=0).sum() if len(ev) else ev
        return assess(curve, ev, self.tax, self.capital)


# --------------------------------------------------------------------------- many paths at once
class PathWealth(NamedTuple):
    """Result of `simulate_paths`. Arrays are [P paths, N dates] on `dates`; NaN before `start` / after `end`."""
    portfolio: np.ndarray            # wealth of the portfolio, `start_wealth` at the first formation date
    index: np.ndarray | None         # wealth of the passive index on the same paths (None without index returns)
    dates: pd.DatetimeIndex
    exposure: np.ndarray | None      # exit-policy exposure [P, N] (None without an exit policy)
    turnover: np.ndarray             # one-way turnover [P, R] per rebalance
    rebalance_dates: list
    selection: str                   # "fixed" or "per_path"
    hold_rule_applied: bool = False  # the Config's HoldRule (keep_within / band) was applied


# screen function name -> (trailing statistic of Market.trailing_stats, True = take the largest)
PRICE_RANKS: dict = {"momentum_12_1": ("mom_12_1", True), "momentum_6m": ("mom_6m", True),
                     "momentum_12m": ("mom_12m", True), "low_vol": ("vol_1y", False)}


def register_price_rank(fn, stat: str, largest: bool = True) -> None:
    """Declare that screen function `fn` (or its __name__) is 'top n by trailing statistic `stat`' (mom_12_1,
    mom_6m, mom_12m, vol_1y, vol_260d), so `simulate_paths(selection="per_path")` can rank it vectorized on every path."""
    if stat not in _STATS:
        raise ValueError(f"stat must be one of {sorted(_STATS)}, got {stat!r}")
    PRICE_RANKS[getattr(fn, "__name__", fn)] = (stat, bool(largest))


_STATS = ("mom_12_1", "mom_6m", "mom_12m", "vol_1y", "vol_260d")
# columns a spec screen may use in its size cut / filters and still count as a price rank: they come from the base
# data and do not change with a simulated path's prices (market_cap is the base's, as in fixed mode)
_STATIC_COLS = frozenset({"market_cap", "dividend", "div_years", "eps", "eps_prev", "sector", "sales"})


@dataclass(frozen=True)
class PriceRank:
    """A screen that is 'cut the universe, then take the n best by one trailing price statistic'.

    column     trailing statistic ranked on (one of mom_6m, mom_12m, mom_12_1, vol_260d, vol_1y)
    largest    True = the highest values win ("desc"), False = the lowest ("asc")
    top        size cut (top-N by market_cap of the base snapshot), None without one
    n          number of holdings
    pre        the screen's stages without the rank (universe cut, static filters), run on the base snapshot of
               each source date to decide which names may be ranked; () for a registered rank function"""
    column: str
    largest: bool
    top: int | None
    n: int
    pre: tuple = ()

    @property
    def direction(self) -> str:
        return "desc" if self.largest else "asc"


def price_rank_of(screen) -> PriceRank | None:
    """The `PriceRank` a screen reduces to, or None when it is not one (so Monte Carlo keeps its picks fixed).

    A registered rank function (`PRICE_RANKS`: momentum_12_1, low_vol, ...) is one. A spec screen (rule_screen)
    is one when its stages reduce to universe cuts / filters on columns that do not depend on the path's prices
    (market_cap, dividend, div_years, eps, eps_prev, sector, sales) followed by a single rank on one trailing
    column (mom_6m, mom_12m, mom_12_1, vol_260d, vol_1y) as the last step: no derived columns, Graham tests or
    `keep` cuts, no second rank (frugal_dividend, stable_high_yield rank on P/E or yield after the volatility cut)."""
    name = getattr(screen.fn, "__name__", None)
    if name in PRICE_RANKS:
        stat, largest = PRICE_RANKS[name]
        return PriceRank(stat, largest, None, screen.n)
    if name != "rule_screen":
        return None
    import re
    from .spec import Stage
    pr = screen.params
    sts = [Stage.from_dict(x) for x in pr["stages"]] if pr.get("stages") else \
        [Stage(pr.get("universe") or {}, pr.get("derive") or {}, list(pr.get("filters") or ()), pr.get("graham"),
               pr.get("rank") or {})]
    if not sts or any(st.derive or st.graham or st.keep for st in sts) or sts[-1].rank == {} \
            or any(st.rank for st in sts[:-1]):
        return None
    col, direction = next(iter(sts[-1].rank.items()))
    if len(sts[-1].rank) != 1 or col not in _STATS or str(direction).lower() not in ("asc", "desc"):
        return None
    tops = []
    for st in sts:
        uni = st.universe or {}
        if set(uni) - {"top", "by", "sectors", "exclude_sectors"} or uni.get("by", "market_cap") != "market_cap":
            return None
        if uni.get("top"):
            tops.append(int(uni["top"]))
        for expr in st.filters or ():
            if not set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expr)) - {"and", "or", "not", "True", "False"} <= _STATIC_COLS:
                return None
    pre = tuple(Stage(st.universe, st.derive, list(st.filters), None, {}) for st in sts)
    return PriceRank(col, str(direction).lower() == "desc", min(tops) if tops else None, screen.n,
                     pre if any(st.universe or st.filters for st in pre) else ())


def per_path_ok(weighting) -> bool:
    """True when `weighting` is equal weight without caps (a HoldRule is fine): what per_path selection supports."""
    return type(weighting).__name__ == "StandardWeighting" and weighting.weighting == "equal" \
        and weighting.max_weight is None and weighting.sector_cap is None


def auto_selection(screen, cfg) -> str:
    """The mode `selection="auto"` picks: per_path for a price rank (see `price_rank_of`) run with equal weight
    and no caps (a hold rule is applied per path), otherwise fixed."""
    return "per_path" if price_rank_of(screen) is not None and per_path_ok(cfg.construction) else "fixed"


def _trailing(stat: str, lev: np.ndarray, dates: pd.DatetimeIndex, i: int, ppy: float):
    """`Market.trailing_stats(dates[i])[stat]` for every path: [P, S], or None when the history is too short."""
    asof = dates[i]
    if stat == "vol_260d" and ppy > 100:                   # daily data: the last 260 returns
        if i < 260:
            return None
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanstd(np.diff(np.log(lev[:, i - 260:i + 1, :]), axis=1), axis=1, ddof=1) * np.sqrt(ppy)
    months = 6 if stat == "mom_6m" else 12
    if asof - pd.DateOffset(months=months) < dates[0]:
        return None
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        i0 = dates.searchsorted(asof - pd.DateOffset(months=months))
        if stat in ("mom_6m", "mom_12m"):
            return lev[:, i, :] / lev[:, i0, :] - 1
        if stat == "mom_12_1":
            i1 = dates.searchsorted(asof - pd.DateOffset(months=1), side="right") - 1
            return lev[:, i1, :] / lev[:, i0, :] - 1
        win = lev[:, i0:i + 1, :]
        return np.nanstd(np.diff(np.log(win), axis=1), axis=1, ddof=1) * np.sqrt(ppy)


def _hold_per_path(hr, sc, ok, cnt, idx, w, prev, n, min_holdings, K):
    """`HoldRule.keep` + equal weight + `HoldRule.apply_band` for every path at once (what `StandardWeighting.retain`
    and `.target` do in `simulate`): sc/ok [P, S] the path's score and eligibility, cnt [P] its pick count, idx/w
    [P, K] the plain top-K picks and equal weights, prev [P, S] the drifted weights. Returns the new (idx, w)."""
    P, S = sc.shape
    if hr.keep_within:
        order = np.argsort(-sc, axis=1, kind="stable")                    # ranking, best first; ineligible last
        rank = np.empty((P, S), int)
        np.put_along_axis(rank, order, np.tile(np.arange(S), (P, 1)), axis=1)
        limit = int(np.ceil(hr.keep_within * n - 1e-9))
        held = prev > 0
        apply = (cnt >= min_holdings) & held.any(axis=1)
        km = (held & ok & (rank < limit) & apply[:, None])
        km = np.take_along_axis(km, order, axis=1)                         # in rank order
        keep = km & (np.cumsum(km, axis=1) <= cnt[:, None])                # the best len(picks) kept names
        pickable = (np.arange(S)[None, :] < cnt[:, None]) & ~keep          # picks not already kept, in order
        fresh = pickable & (np.cumsum(pickable, axis=1) <= (cnt - keep.sum(axis=1))[:, None])
        sel = np.where(apply[:, None], keep | fresh, np.arange(S)[None, :] < cnt[:, None])
        pos = np.argsort(~sel, axis=1, kind="stable")[:, :K]
        chosen = np.take_along_axis(sel, pos, axis=1)
        idx = np.where(chosen, np.take_along_axis(order, pos, axis=1), S)
        w = np.where(chosen & (cnt >= min_holdings)[:, None], 1.0 / np.maximum(cnt, 1)[:, None], 0.0)
    if hr.band:
        gi = np.minimum(idx, S - 1)
        t, p = w, np.take_along_axis(prev, gi, axis=1) * (idx < S)
        member = t > 0
        frozen = member & (p > 0) & (np.abs(p - t) <= hr.band * t + 1e-12)
        room = t.sum(axis=1) - np.where(frozen, p, 0.0).sum(axis=1)
        rest = member & ~frozen
        rsum = np.where(rest, t, 0.0).sum(axis=1)
        go = frozen.any(axis=1) & rest.any(axis=1) & (room > 0)
        scale = np.where(go, room / np.where(rsum > 0, rsum, 1.0), 1.0)
        w = np.where(go[:, None] & frozen, p, np.where(go[:, None] & rest, t * scale[:, None], t))
    return idx, w


class _Cal:
    """Just enough of a Market (index, trading_day) for `backtest.schedule`."""
    def __init__(self, index):
        self.index = index

    def trading_day(self, d, side="next"):
        from .data import Market
        return Market.trading_day(self, d, side)


def simulate_paths(screen, cfg=None, paths=None, *, base=None, returns=None, alive=None, index_returns=None,
                   dates=None, names=None, index_names=None, source_idx=None, index=None, index_mer: float = 0.0,
                   index_cost_bps: float = 0.0, index_dividend_yield: float = 0.0, selection: str = "auto", start=None, end=None,
                   start_wealth: float = 1.0) -> PathWealth:
    """Wealth of a portfolio (screen + `cfg`) and of the passive index on many return paths at once.

    `paths`: a `montecarlo.PathSet`; or raw arrays `returns` [P, N, S] (0 where not alive), `alive` [P, N, S]
    (default all True), `index_returns` [P, N, I], with `dates` (N), `names` (S), `index_names` (I). `base`: the
    base Dataset / Market the paths were drawn from (needed by `selection="fixed"`, and in per_path mode
    to know which names have fundamentals on each step's source date).

    selection
      "fixed"     picks and weights are decided on the base data at each step's source date (`source_idx` /
                  `PathSet.source_dates`) with the screen as it is, once per distinct source date and shared by
                  all paths that land on it: for screens whose inputs are fundamentals carried with the dates.
                  A hold rule (keep_within / band) runs per path on that path's drifted weights, exactly as
                  `simulate` does (`PathWealth.hold_rule_applied`).
      "per_path"  price ranks (`price_rank_of`: momentum_12_1, momentum_6m, low_vol, ...; `register_price_rank`
                  adds more; spec screens of a size cut + static filters + one trailing-price rank) re-rank every
                  path at every rebalance from that path's own trailing prices, vectorized across paths. The size
                  cut / filters use the base snapshot of each step's source date (caps do not resample).
                  Equal weight only (no caps). A hold rule runs per path: `keep_within` retains held names that
                  still rank within keep_within * n on that path's own ranking, `band` freezes names near target
                  on that path's drifted weights, as `simulate` does (`PathWealth.hold_rule_applied`).
      "auto"      per_path when the screen is a price rank and the weighting is equal weight without caps, else fixed.

    Stages: `cfg.costs` (trade cost, MER), `cfg.cash` (cash rate, delisting haircut), `cfg.exit` (exposure computed
    per path from that path's own index: TrendBreak and signals with `compute_paths`; other signals raise
    NotImplementedError) and `cfg.construction` all act as in `backtest.simulate`: for one path the result
    equals `run(screen, Market(generator.scenario_dataset(...)), cfg, start)`. Rebalances follow
    `cfg.rebalance_months` from `start` (default: the first date). The index is `index` (a name, default the first
    index) after `index_mer` and a one-off `index_cost_bps`, like `benchmarks.IndexBenchmark`; a price index
    gets `index_dividend_yield` (annual, e.g. 0.02) added back as `(1 + y) ** years`.
    """
    from .backtest import Config, schedule
    from .exit_policy import as_overlay
    cfg = cfg or Config()
    if getattr(cfg, "tax", None) is not None:
        raise NotImplementedError("Config.tax is not supported by simulate_paths (Monte Carlo): the tax stage keeps "
                                  "per-lot cost basis path by path; run the backtest (backtest.run) with tax, or "
                                  "drop Config.tax for Monte Carlo")
    if paths is not None:
        returns, alive, index_returns = paths.returns, paths.alive, paths.index_returns
        dates, names, index_names = paths.dates, paths.names, paths.index_names
        source_idx = paths.source_idx if source_idx is None else source_idx
    if returns is None or dates is None:
        raise ValueError("give a PathSet, or returns= and dates=")
    returns = np.asarray(returns)
    P, N, S = returns.shape
    dates = pd.DatetimeIndex(dates)
    names = tuple(names) if names is not None else tuple(range(S))
    alive = np.ones(returns.shape, bool) if alive is None else np.asarray(alive, bool)
    I = 0 if index_returns is None else np.asarray(index_returns).shape[2]
    index_names = tuple(index_names) if index_names is not None else tuple(range(I))

    # ---- stages and the screen's mode
    weighting, cost_model, accrual = cfg.construction, cfg.costs, cfg.cash
    ov = as_overlay(cfg.exit)
    pr = price_rank_of(screen)
    rank = None if pr is None else (pr.column, pr.largest)
    if selection == "auto":
        selection = auto_selection(screen, cfg)
    if selection not in ("fixed", "per_path"):
        raise ValueError(f"selection must be 'fixed', 'per_path' or 'auto', got {selection!r}")
    market = None
    if base is not None:
        from .data import Market
        market = base if isinstance(base, Market) else Market(base)
    if selection == "per_path":
        if rank is None:
            raise NotImplementedError(f"screen {screen.name!r} (function {getattr(screen.fn, '__name__', screen.fn)!r}) "
                                      f"is not a registered price rank (register_price_rank / price_rank_of); use selection='fixed'")
        if screen.params and getattr(screen.fn, "__name__", None) != "rule_screen":
            raise NotImplementedError("per_path ranking does not support screen params")
        if not per_path_ok(weighting):
            raise NotImplementedError("per_path selection supports equal weight without max_weight / sector_cap")
    else:
        if market is None or source_idx is None:
            raise ValueError("selection='fixed' needs base= and the paths' source_idx (a PathSet)")

    # ---- levels (the prices of the scenario dataset) and schedule
    lev = np.add(returns, 1.0, dtype=np.float64)
    np.cumprod(lev, axis=1, out=lev)
    np.putmask(lev, ~alive, np.nan)
    ilev = None if I == 0 else np.cumprod(1.0 + np.asarray(index_returns, dtype=np.float64), axis=1)
    start = dates[0] if start is None else start
    rebal = schedule(_Cal(dates), start, cfg.rebalance_months, 0, end)
    R = len(rebal)
    pos = dates.get_indexer(rebal)
    i_end = N - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end), side="right") - 1)
    ppy = (len(dates) - 1) / ((dates[-1] - dates[0]).days / 365.25)

    # ---- overlay: exposure per path from that path's own index
    held = dheld = None
    if ov is not None:
        def levels_of(name):
            if name not in index_names:
                raise KeyError(name)
            return ilev[:, :, index_names.index(name)]
        held = ov.exposure_paths(dates, levels_of, rebal)
        dheld = np.abs(np.diff(held, axis=1, prepend=held[:, :1]))
        dheld[:, 0] = 0.0
    drag = np.asarray(cost_model.holding_drag(names, None), dtype=float) * np.ones(S)

    # ---- selection helpers
    hr = getattr(weighting, "hold_rule", None)
    hold_on = hr is not None and bool(hr.keep_within or hr.band)
    if selection == "per_path":
        K = min(screen.n, S)
        has_fund = None
        if market is not None and source_idx is not None:
            from .universe import AllListed, Membership
            if not isinstance(market.universe, (AllListed, Membership)):
                raise NotImplementedError("per_path selection supports AllListed / Membership universes only")
            first = market.fund.groupby("ticker")["date"].min().reindex(list(names))
            has_fund = market.index.to_numpy()[:, None] >= first.to_numpy()[None, :]    # NaT -> False
        stat, largest = rank
        umask_cache: dict = {}

        def univ_mask(u):
            """Names the screen's size cut / static filters admit at base step `u` (before the rank): [S] bool."""
            if u not in umask_cache:
                from .spec import _run_stage
                v = market.view(market.index[u])
                for st in pr.pre:
                    v = _run_stage(v, st)
                umask_cache[u] = np.isin(np.asarray(names, dtype=object), v.index.to_numpy(dtype=object))
            return umask_cache[u]
    else:
        cache: dict = {}
        col = {n: i for i, n in enumerate(names)}
        empty = pd.Series(dtype=float)
        src = np.asarray(source_idx)

        def base_pick(u):
            if u not in cache:
                d = market.index[u]
                picks = market.picks(screen, d)
                tw = weighting.target(market.view(d), picks, empty, cfg.min_holdings)
                tw = tw[[t in col for t in tw.index]]
                cache[u] = (np.array([col[t] for t in tw.index], int), tw.to_numpy(float))
            return cache[u]

        def held_pick(u, p, prev_row):
            """The hold rule on one path: `retain` + `target` exactly as `simulate` runs them, on the base data at
            source step `u` and this path's drifted weights `prev_row` [S]."""
            d = market.index[u]
            held = np.flatnonzero(prev_row > 0)
            pv = pd.Series(prev_row[held], index=[names[k] for k in held])
            snap = market.view(d)
            picks = weighting.retain(market.picks(screen, d), screen, snap, pv, cfg.min_holdings)
            tw = weighting.target(snap, picks, pv, cfg.min_holdings)
            tw = tw[[t in col for t in tw.index]]
            return np.array([col[t] for t in tw.index], int), tw.to_numpy(float)

    W = np.full((P, N), np.nan)
    W[:, pos[0]] = 1.0
    value = np.ones(P)                                                    # relative wealth
    prev = np.zeros((P, S + 1))                                           # drifted weights; column S = padding
    turnover = np.zeros((P, R))
    rows = np.arange(P)[:, None]
    for j in range(R):
        progress.report(j / R, f"{screen.name}: rebalance {j + 1}/{R} on {P} paths")
        i_d = pos[j]
        i_nx = pos[j + 1] if j + 1 < R else i_end

        # Selection + Weighting
        if selection == "per_path":
            elig = alive[:, i_d, :]
            if has_fund is not None:
                elig = elig & has_fund[np.asarray(source_idx)[:, i_d]]
            if pr.pre:
                if market is None or source_idx is None:
                    raise ValueError("a price rank with a size cut / filters needs base= and the paths' source_idx (a PathSet)")
                us_, inv_ = np.unique(np.asarray(source_idx)[:, i_d], return_inverse=True)
                elig = elig & np.stack([univ_mask(int(u)) for u in us_])[inv_]
            x = _trailing(stat, lev, dates, i_d, ppy)
            if x is None:
                idx, w = np.full((P, K), S), np.zeros((P, K))
            else:
                ok = elig & ~np.isnan(x)
                sc = np.where(ok, x if largest else -x, -np.inf)
                top = np.argpartition(-sc, K - 1, axis=1)[:, :K] if K < S else np.tile(np.arange(S), (P, 1))
                good = np.take_along_axis(ok, top, axis=1)
                cnt = good.sum(axis=1)
                idx = np.where(good, top, S)
                w = np.where(good & (cnt >= cfg.min_holdings)[:, None], 1.0 / np.maximum(cnt, 1)[:, None], 0.0)
                if hold_on:
                    idx, w = _hold_per_path(hr, sc, ok, cnt, idx, w, prev[:, :S], screen.n, cfg.min_holdings, K)
        else:
            us, inv = np.unique(src[:, i_d], return_inverse=True)
            tabs = [base_pick(int(u)) for u in us]
            Kmax = max([len(c) for c, _ in tabs] + [1])
            ti, tw_ = np.full((len(us), Kmax), S), np.zeros((len(us), Kmax))
            for r, (c, ww) in enumerate(tabs):
                ti[r, :len(c)], tw_[r, :len(c)] = c, ww
            idx, w = ti[inv], tw_[inv]
            if hold_on:                                              # retain / band need each path's drifted weights
                rows_ = []
                for p_ in range(P):
                    c_, ww_ = held_pick(int(src[p_, i_d]), p_, prev[p_, :S])
                    rows_.append((c_, ww_))
                Kmax = max([len(c) for c, _ in rows_] + [1])
                idx, w = np.full((P, Kmax), S), np.zeros((P, Kmax))
                for p_, (c_, ww_) in enumerate(rows_):
                    idx[p_, :len(c_)], w[p_, :len(c_)] = c_, ww_
            w = np.where(np.take_along_axis(alive[:, i_d, :], np.minimum(idx, S - 1), axis=1) & (idx < S), w, 0.0)
        gidx = np.minimum(idx, S - 1)
        target = np.zeros((P, S + 1))
        np.put_along_axis(target, idx, w, axis=1)
        target = target[:, :S]

        # CostModel
        dw = np.abs(target - prev[:, :S])
        dw[dw <= 1e-12] = 0.0
        tc = np.asarray(cost_model.trade_cost(pd.DataFrame(dw, columns=list(names)), None, value), dtype=float)
        if ov is not None:
            tc = tc * held[:, i_d]
        value = value * (1 - tc)
        turnover[:, j] = dw.sum(axis=1) / 2

        # Accrual over the segment
        sl = slice(i_d, i_nx + 1)
        yrs = ((dates[sl] - dates[i_d]).days / 365.25).to_numpy()
        Lg = np.take_along_axis(lev[:, sl, :], gidx[:, None, :], axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            g = Lg / Lg[:, :1, :]
        g = np.where((w > 0)[:, None, :], g, 1.0)
        g, dead = accrual.delist(g)
        g = g * np.power(1 - drag[gidx][:, None, :], yrs[None, :, None])
        path = (g * w[:, None, :]).sum(axis=2)
        wsum = w.sum(axis=1)
        path = path + np.where(wsum < 1 - 1e-9, 1 - wsum, 0.0)[:, None] * accrual.cash_factor(yrs)[None, :]
        prev = np.zeros((P, S + 1))
        np.put_along_axis(prev, idx, np.where(dead[:, -1, :], 0.0, g[:, -1, :] * w / path[:, -1:]), axis=1)

        # Overlay
        if ov is not None:
            pol = getattr(ov, "policy", None)
            if pol is not None and isinstance(pol.park, str) and pol.park != "cash":
                if pol.park not in index_names:
                    raise NotImplementedError(f"park asset {pol.park!r} is not among the path set's indices")
                park_r = np.asarray(index_returns, dtype=float)[:, sl, index_names.index(pol.park)].copy()
                park_r[:, 0] = 0.0
                if pol.park_mer:
                    park_r = (1 + park_r) * np.power(1 - pol.park_mer, np.diff(yrs, prepend=yrs[0]))[None, :] - 1
            else:
                park_r = ov.park_returns(None, dates[sl], yrs, accrual)
            applied = ov.apply(path, held[:, sl], dheld[:, sl], park_r)
            path = np.where((w > 0).any(axis=1)[:, None], applied, path)

        W[:, i_d + 1:i_nx + 1] = value[:, None] * path[:, 1:]
        value = value * path[:, -1]
    W *= float(start_wealth)

    iw = None
    if ilev is not None:
        k = 0 if index is None else index_names.index(index)
        a, b = pos[0], i_end + 1
        yrs = ((dates[a:b] - dates[a]).days / 365.25).to_numpy()
        g = ilev[:, a:b, k] / ilev[:, a:a + 1, k] * np.power((1 - index_mer) * (1 + index_dividend_yield), yrs)[None, :]
        g[:, 1:] *= 1 - index_cost_bps / 1e4
        iw = np.full((P, N), np.nan)
        iw[:, a:b] = g * float(start_wealth)
    return PathWealth(W, iw, dates, held, turnover, rebal, selection, bool(hold_on))
