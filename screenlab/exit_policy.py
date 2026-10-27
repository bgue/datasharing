"""Exit policy: what to do when the exit screen flags, where the money sits, and how it comes back.

The `ExitScreen` (exit_signals.py) says *when* risk is elevated. An `ExitPolicy` turns that flag into an
equity-sleeve exposure path in [0, 1] and says what the rest earns:

    r_t = e_t * r_equity + (1 - e_t) * r_park - switch_cost * |de_t| - park_cost * |d(1 - e_t)|

with e_t the exposure held over bar t (decided at the close `screen.lag` bars earlier). The entry
screen never sees the flag; its sleeve keeps running on paper while exposure is below 1.

    policy = ExitPolicy(ExitScreen([TrendBreak("SP500"), Breadth()], check="W-FRI"),
                        park="cash", reentry="signal", reentry_delay_days=10, reentry_steps=2)
    lab.backtest(["dogs"], exit=policy, with_and_without=True)
    lab.backtest([Portfolio("dogs_exit", "dogs", exit=policy, rebalance_months=6), "dogs"])

Re-entry modes (the flag must be clear to come back in every mode; "stay out" below means the flag
may still be on):
    "signal"               come back when the flag has been clear for `reentry_delay_days`
    "next_rebalance"       stay out until the entry screen's first scheduled rebalance after the exit
                           (and only if the flag is clear then; otherwise until it clears)
    "signal_or_rebalance"  whichever of the two comes first
`reentry_steps=k` scales back in over k check periods (k equal steps from `exposure_when_flagged` to 1,
the first on the re-entry bar); a new flag during the ramp drops exposure back to the flagged level.
With `rescreen_on_reentry` every day exposure returns to 1 from below (`policy.reentry_dates`)
becomes an extra rebalance date of the entry screen.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from . import metrics
from .serial import register_tag, TAG, from_dict as _from, serializable, to_dict as _to
from .exit_signals import (ExitScreen, OverlayResult, _check_dates, _compute, uses_curve)

REENTRY_MODES = ("signal", "next_rebalance", "signal_or_rebalance")
_EPS = 1e-12


@serializable("ExitPolicy")
@dataclass
class ExitPolicy:
    screen: ExitScreen
    exposure_when_flagged: float = 0.0   # 0 = fully out, 0.5 = half out
    park: str | float = "cash"           # "cash" -> cfg.cash_rate; a float -> that annual rate; any other
                                         # string -> a series in the Market (index or ticker, total return)
    park_mer: float = 0.0                # annual fund fee on the parked money (any park type)
    park_cost_bps: float = 0.0           # charged on |d(1 - e)|, like buying/selling the park fund
    switch_cost_bps: float = 5.0         # charged on |de| of the equity sleeve
    reentry: str = "signal"
    reentry_delay_days: int = 0          # the flag must stay clear this many calendar days
    reentry_steps: int = 1               # scale back in over k check periods
    rescreen_on_reentry: bool = False    # True: unscheduled entry-screen rebalance when exposure is back to 1
    macro: object = None                 # macro series handed to the signals

    def __post_init__(self):
        if self.reentry not in REENTRY_MODES:
            raise ValueError(f"reentry must be one of {REENTRY_MODES}, got {self.reentry!r}")
        if self.reentry_steps < 1 or int(self.reentry_steps) != self.reentry_steps:
            raise ValueError("reentry_steps must be an integer >= 1")
        if self.reentry_delay_days < 0:
            raise ValueError("reentry_delay_days must be >= 0")
        if not 0.0 <= self.exposure_when_flagged <= 1.0:
            raise ValueError("exposure_when_flagged must be in [0, 1]")
        self._cache: dict = {}            # (id(mkt), id(signal)) -> (mkt, signal, series); curve-free signals only
        self._reentry: list = []

    # ---- inputs ---------------------------------------------------------------------------------
    @property
    def needs_curve(self) -> bool:
        """Does any signal read the portfolio's own curve (so a fully-invested first pass is needed)?"""
        return any(uses_curve(s) for s in self.screen.signals)

    @property
    def reentry_dates(self) -> list:
        """Days exposure returned to 1 from below, as of the last `exposure()` call."""
        return list(self._reentry)

    def components(self, mkt, curve=None) -> pd.DataFrame:
        """`screen.components`, reusing the cached columns of signals that do not read `curve`."""
        cols = {}
        for s in self.screen.signals:
            if uses_curve(s):
                c = _compute(s, mkt, self.macro, curve)
            else:
                k = (id(mkt), id(s))
                hit = self._cache.get(k)
                if hit is None or hit[0] is not mkt or hit[1] is not s:
                    hit = (mkt, s, s.compute(mkt, self.macro).reindex(mkt.index))
                    self._cache[k] = hit
                c = hit[2]
            c = c.reindex(mkt.index)
            if c.notna().any():
                cols[s.name] = c
        return pd.DataFrame(cols, index=mkt.index)

    # ---- exposure path ----------------------------------------------------------------------------
    def exposure(self, mkt, rebalance_dates=None, curve=None, components=None, state=None) -> pd.Series:
        """Equity-sleeve exposure held over each bar of `mkt.index` (values in [0, 1], lagged by `screen.lag`).

        `rebalance_dates`: the entry screen's scheduled rebalances (needed by the "next_rebalance" modes).
        `curve`: the fully-invested portfolio curve, for portfolio-based signals. `components` / `state`:
        precomputed screen output (calibration reuses them). Sets `self.reentry_dates`.
        """
        idx = mkt.index
        if state is None:
            comp = self.components(mkt, curve) if components is None else components
            state = self.screen.state(mkt, self.macro, comp)
        flag = state.reindex(idx, method="pad").fillna(False).to_numpy(bool)
        n = len(idx)
        f, K, delay = float(self.exposure_when_flagged), int(self.reentry_steps), int(self.reentry_delay_days)
        days = idx.to_numpy("datetime64[D]").astype(np.int64)
        reb = np.sort(pd.DatetimeIndex([] if rebalance_dates is None else rebalance_dates)
                      .to_numpy("datetime64[D]").astype(np.int64))
        chk = self.screen.check
        if chk in (None, "D", "B"):
            is_check = np.ones(n, bool)
        else:
            is_check = np.zeros(n, bool)
            pos = idx.searchsorted(_check_dates(idx, chk), side="right") - 1
            is_check[pos[pos >= 0]] = True

        level = np.ones(n)
        mode, cur, k = "in", 1.0, 0            # in | out (waiting to re-enter) | ramp (scaling back in)
        clear_since, next_reb = None, np.inf
        for i in range(n):
            fl = flag[i]
            if fl:
                clear_since = None
            elif clear_since is None:
                clear_since = days[i]
            if fl and mode != "out":           # (re-)exit; a flag during the ramp starts over
                mode, cur, k = "out", f, 0
                j = np.searchsorted(reb, days[i], side="right")
                next_reb = float(reb[j]) if j < len(reb) else np.inf
            elif mode == "out" and not fl:
                clear_ok = days[i] - clear_since >= delay
                reb_ok = days[i] >= next_reb
                go = {"signal": clear_ok, "next_rebalance": reb_ok,
                      "signal_or_rebalance": clear_ok or reb_ok}[self.reentry]
                if go:
                    k = 1
                    cur = f + (1.0 - f) * k / K
                    mode = "in" if k >= K else "ramp"
            elif mode == "ramp" and is_check[i]:
                k += 1
                cur = f + (1.0 - f) * k / K
                if k >= K:
                    mode = "in"
            level[i] = cur
        held = pd.Series(level, index=idx).shift(self.screen.lag).fillna(1.0).rename("exposure")
        below = held.to_numpy() < 1 - _EPS
        back = (~below[1:]) & below[:-1]
        self._reentry = list(idx[1:][back])
        return held

    # ---- the parked money ---------------------------------------------------------------------------
    def park_returns(self, mkt, index: pd.DatetimeIndex, cash_rate: float = 0.0) -> np.ndarray:
        """Per-bar return of the parked money over `index` (first element 0), after `park_mer`."""
        yrs = ((index - index[0]).days / 365.25).to_numpy()
        dt = np.diff(yrs, prepend=yrs[0])
        if isinstance(self.park, str) and self.park != "cash":
            s = mkt.series(self.park).astype(float).pct_change(fill_method=None).fillna(0.0)
            r = s.reindex(index).fillna(0.0).to_numpy().copy()
            r[0] = 0.0
        else:
            rate = cash_rate if self.park == "cash" else float(self.park)
            r = np.power(1 + rate, dt) - 1
        if self.park_mer:
            r = (1 + r) * np.power(1 - self.park_mer, dt) - 1
        return r

    def with_screen(self, screen: ExitScreen, **kw) -> "ExitPolicy":
        return replace(self, screen=screen, **kw)


def as_policy(x) -> ExitPolicy | None:
    """None / ExitPolicy pass through; a bare ExitScreen gets the default policy (park cash, signal re-entry)."""
    if x is None or isinstance(x, ExitPolicy):
        return x
    if isinstance(x, ExitScreen):
        return ExitPolicy(x)
    raise TypeError(f"exit must be an ExitPolicy or ExitScreen, got {type(x).__name__}")


class Portfolio:
    """An entry screen, an optional exit policy and Config overrides: one row of a `Lab.backtest` report.

        Portfolio("dogs_hedged", "dogs", exit=policy, rebalance_months=6, cost_bps=10)

    `screen` is anything `Lab.screen` accepts. `exit` takes an ExitPolicy or an ExitScreen; without one
    the portfolio inherits the call-level `exit` (if any). Extra keyword arguments override `Config` fields.
    """

    def __init__(self, name: str, screen, exit=None, **config_overrides):
        self.name, self.screen, self.exit = name, screen, as_policy(exit)
        self.config_overrides = config_overrides

    def to_dict(self) -> dict:
        return {TAG: "Portfolio", "name": self.name, "screen": _to(self.screen),
                **({"exit": _to(self.exit)} if self.exit is not None else {}),
                **({"overrides": _to(self.config_overrides)} if self.config_overrides else {})}

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        return cls(d["name"], _from(d["screen"]), _from(d["exit"]) if "exit" in d else None,
                   **_from(d.get("overrides", {})))

    def __repr__(self):
        return (f"Portfolio({self.name!r}, {getattr(self.screen, 'name', self.screen)!r}, exit={self.exit is not None}, "
                f"overrides={self.config_overrides})")


Portfolio._serial_tag = "Portfolio"
register_tag("Portfolio", Portfolio.from_dict)


def policy_overlay(curve: pd.Series, policy: ExitPolicy, mkt, rebalance_dates=None, cash_rate: float = 0.0,
                   components=None, state=None, signal_curve=None) -> OverlayResult:
    """Trade `policy` on `curve` (a target index/portfolio curve): the OverlayResult counterpart of `overlay`.

    Used by calibration and `Lab.exit_eval` so reentry / park / steps / delay shape the evaluated curve.
    """
    c = curve.dropna().astype(float)
    held = policy.exposure(mkt, rebalance_dates, signal_curve, components, state)
    held = held.reindex(c.index, method="pad").fillna(1.0)
    r = c.pct_change().fillna(0.0).to_numpy()
    rp = policy.park_returns(mkt, c.index, cash_rate)
    e = held.to_numpy()
    de = np.abs(np.diff(e, prepend=e[0]))
    ret = e * r + (1 - e) * rp - policy.switch_cost_bps / 1e4 * de - policy.park_cost_bps / 1e4 * de
    ret[0] = 0.0
    derisked = pd.Series(np.cumprod(1 + ret), index=c.index)
    base = c / c.iloc[0]
    rf = float(cash_rate)
    trade = pd.Series(de, index=c.index)
    switches = int((trade > _EPS).sum())
    stats = pd.DataFrame({"always_in": metrics.summarize(base, rf=rf),
                          "derisked": metrics.summarize(derisked, rf=rf)}).T
    stats["Switches"] = [0, switches]
    return OverlayResult(derisked, base, held, stats, switches, float(trade.sum()),
                         float((held < 1 - _EPS).mean()))
