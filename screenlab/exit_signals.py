"""Exit-signal screen: a dated risk score that says when upcoming returns are at greater risk.

Separate from the entry screens and never tuned jointly with them. Every `Signal` maps a `Market`
(plus optional macro series) to a float Series on the trading-day index, higher = riskier, already
normalized to a rolling percentile rank (10y calendar lookback, expanding before that), so signals
are comparable and can be averaged. Everything is strictly causal: the value on day t only uses
data up to t (trailing windows, no centered windows, no full-sample normalization).

Window convention (one for every signal, see `calendar_window`): a parameter named `days` (or `*_days`
such as `short_days`) is a number of TRADING days, a parameter named `months` (or `*_months`) is a number of
CALENDAR months, and a parameter that is genuinely a calendar-day count says so (`lag_calendar_days`).
Trading days are converted to calendar time once (x 365.25 / 252) and widened to a signal-specific minimum
number of observations, so daily and monthly price frequencies both work. `describe()` reports every
window parameter with its unit.

`ExitScreen` averages signals into a score and a hysteresis flag; `evaluate` measures what the flag
would have told you about a target curve; `overlay` trades the flag (cash when flagged).

Macro inputs are plain pandas Series, passed as a dict or any object with `.get(name) -> Series | None`.
"""
from __future__ import annotations

import functools
import inspect
import warnings
from dataclasses import dataclass, field, fields
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from . import metrics

_DAYS_PER_MONTH = 30.4375


# --------------------------------------------------------------------------- helpers
def rolling_percentile(x: pd.Series, years: float = 10, min_obs: int = 20) -> pd.Series:
    """Percentile rank (0..1, ties at mid-rank) of each value within its own trailing window.

    The window is `years` of calendar time ending on that day (expanding while history is shorter) and
    includes the day itself, so nothing after t is used. NaN until `min_obs` valid values exist.
    """
    v = x.to_numpy(dtype=float)
    idx = x.index
    lo = idx.searchsorted(idx - pd.DateOffset(years=years)) if years else np.zeros(len(idx), int)
    out = np.full(len(v), np.nan)
    for t in range(len(v)):
        if np.isnan(v[t]):
            continue
        w = v[lo[t]:t + 1]
        w = w[~np.isnan(w)]
        if len(w) < min_obs:
            continue
        out[t] = ((w < v[t]).sum() + 0.5 * (w == v[t]).sum()) / len(w)
    return pd.Series(out, index=idx, name=x.name)


def _macro_get(macro, name):
    if macro is None:
        return None
    s = macro.get(name) if hasattr(macro, "get") else None
    return None if s is None else pd.Series(s).dropna().sort_index()


def _month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in `index`."""
    return pd.DatetimeIndex(index.to_series().groupby(index.to_period("M")).last().to_numpy())


def _universe_curve(mkt) -> pd.Series:
    """Equal-weight total-return curve of every live name (delisted names simply drop out)."""
    r = mkt.prices.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    return (1 + r).cumprod()


def _target_series(mkt, index) -> pd.Series:
    """The named index/ticker, or an equal-weight universe curve when it is not in the data."""
    if index is not None:
        try:
            return mkt.series(index).astype(float)
        except KeyError:
            warnings.warn(f"index {index!r} not found; using the equal-weight universe curve", stacklevel=3)
    return _universe_curve(mkt)


TRADING_DAYS_PER_YEAR = 252
CAL_PER_TRADING_DAY = 365.25 / TRADING_DAYS_PER_YEAR      # calendar days per trading day (~1.4494)

TRADING_DAYS, CAL_MONTHS, CAL_DAYS = "trading days", "calendar months", "calendar days"


def median_step(index: pd.DatetimeIndex) -> float:
    """Median spacing of `index` in calendar days (1 for fewer than two bars, ~1.4 daily, ~30 monthly)."""
    if len(index) < 2:
        return 1.0
    return float(np.median(np.diff(index.values).astype("timedelta64[D]").astype(float)))


def calendar_window(index: pd.DatetimeIndex, days: float | None = None, months: float | None = None,
                    min_obs: int = 1) -> int:
    """Length in calendar days of a trailing window given in trading `days` XOR calendar `months`.

    `days` x 365.25 / 252 and `months` x 30.4375, rounded, then widened to at least `min_obs` observations
    of this index (`ceil(min_obs x median_step)`), so a 63-trading-day window on monthly data still spans a
    few bars. Never shorter than one calendar day. The same call serves daily and monthly data; pass the
    result to `rolling(f"{n}D")` or `pd.Timedelta(days=n)`.
    """
    if (days is None) == (months is None):
        raise ValueError("give exactly one of `days` (trading days) or `months` (calendar months)")
    n = int(round(days * CAL_PER_TRADING_DAY if days is not None else months * _DAYS_PER_MONTH))
    return max(1, n, int(np.ceil(min_obs * median_step(index))))


def window_not_full(index: pd.DatetimeIndex, calendar_days: int) -> np.ndarray:
    """True where a `calendar_days`-long window starting at the first bar is not yet full."""
    return np.asarray(index < index[0] + pd.Timedelta(days=calendar_days))


def _renamed(new: str, to_new=lambda v: v, from_new=lambda v: v):
    """Deprecation record for a renamed parameter: new name + value converters (old -> new, new -> old)."""
    return (new, to_new, from_new)


def _blank(mkt) -> pd.Series:
    return pd.Series(np.nan, index=mkt.index)


def _takes(fn, param: str) -> bool:
    try:
        return param in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def uses_curve(sig) -> bool:
    """Does this signal read the portfolio curve? (explicit `uses_curve`, a `needs` tuple, else its signature)

    `ExitPolicy` caches curve-free signals per Market and recomputes only the curve-dependent ones.
    """
    flag = getattr(sig, "uses_curve", None)
    if flag is not None:
        return bool(flag)
    needs = getattr(sig, "needs", None)          # e.g. needs = ("curve", "index")
    if needs is not None:
        return "curve" in needs
    cls = type(sig)
    if isinstance(sig, _Base):
        own = cls.compute is not _Base.compute
        part = cls.monthly if sig.sampling == "monthly" else cls.raw
        return _takes(part, "curve") or (own and _takes(cls.compute, "curve"))
    return _takes(getattr(cls, "compute", None), "curve")


def _compute(sig, mkt, macro, curve) -> pd.Series:
    """`sig.compute`, handing over `curve` only when there is one (older two-argument signals keep working)."""
    if curve is None or not _takes(sig.compute, "curve"):
        return sig.compute(mkt, macro)
    return sig.compute(mkt, macro, curve=curve)


# --------------------------------------------------------------------------- protocol
@runtime_checkable
class Signal(Protocol):
    """Anything with a `name` and `compute(mkt, macro=None, curve=None) -> Series` (percentile scores, higher = riskier).

    `curve` is the portfolio's own fully-invested equity curve, passed by `ExitPolicy` for signals that
    look at the portfolio itself; it is `None` when evaluating against an index. Signals that do not
    need it ignore it.
    """
    name: str

    def compute(self, mkt, macro=None, curve=None) -> pd.Series: ...


def _signal(cls=None, *, renamed: dict | None = None):
    """Dataclass decorator for signals (replaces `@dataclass(repr=False)`), with deprecated parameter names.

    `renamed` maps an OLD parameter name to `_renamed(new, to_new, from_new)`. The constructor still accepts
    the old keyword (DeprecationWarning, value converted to the new unit; if both are given the old one wins,
    which is what `dataclasses.replace` needs), and the old attribute still reads
    and writes through to the new one, so `dataclasses.replace(sig, old=...)` and grid keys like
    "signals.Breadth.window_days" keep working for one release.
    """
    def wrap(cls):
        cls = dataclass(repr=False)(cls)
        ren = dict(renamed or {})
        cls._renamed = {**getattr(cls.__mro__[1], "_renamed", {}), **ren}
        if ren:
            init = cls.__init__

            @functools.wraps(init)
            def __init__(self, *args, **kw):
                for old, (new, to_new, _) in ren.items():
                    if old in kw:               # the deprecated name wins (dataclasses.replace passes both)
                        warnings.warn(f"{cls.__name__}({old}=...) is deprecated; use {new}= "
                                      f"({cls.units.get(new, 'new unit')})", DeprecationWarning, stacklevel=2)
                        kw[new] = to_new(kw.pop(old))
                init(self, *args, **kw)
            cls.__init__ = __init__
            for old, (new, to_new, from_new) in ren.items():
                def _get(self, old=old, new=new, from_new=from_new):
                    warnings.warn(f"{type(self).__name__}.{old} is deprecated; use .{new}",
                                  DeprecationWarning, stacklevel=2)
                    return from_new(getattr(self, new))

                def _set(self, v, old=old, new=new, to_new=to_new):
                    warnings.warn(f"{type(self).__name__}.{old} is deprecated; use .{new}",
                                  DeprecationWarning, stacklevel=2)
                    setattr(self, new, to_new(v))
                setattr(cls, old, property(_get, _set))
        return cls
    return wrap(cls) if cls is not None else wrap


class _Describe:
    """`describe` on the class returns the defaults, on an instance the current values."""
    def __get__(self, obj, cls):
        def describe() -> dict:
            params = {f.name: (getattr(obj, f.name) if obj is not None else f.default) for f in fields(cls)}
            return {"name": cls.__name__, "params": params,
                    "units": {k: u for k, u in cls.units.items() if k in params},
                    "needs": list(cls.needs or ())}
        return describe


class _Base:
    """The one signal base. Subclasses give `raw()` (daily-sampled) or `monthly()` (`sampling = "monthly"`).

    `compute` normalizes to a rolling percentile. Monthly-sampled signals (valuation, earnings) rank the
    month-end series and pad it to the daily index; a month-end value is only visible from that month-end
    day on, so padding stays causal. `raw`/`monthly` receive `curve` only when they declare it.

    Class attributes: `needs` (inputs read: "prices", "index", "curve", "fundamentals", "macro:<name>"),
    `units` (parameter -> unit string for every window-like parameter, reported by `describe()`),
    `min_obs` (percentile warm-up; None = 20 daily / 12 monthly).
    """
    lookback_years = 10
    min_obs = None
    sampling = "daily"                 # "daily" or "monthly"
    needs = None                       # None = infer from the method signature (custom subclasses)
    units: dict = {}
    _renamed: dict = {}
    describe = _Describe()

    @property
    def name(self) -> str:
        return type(self).__name__

    def raw(self, mkt, macro=None, curve=None) -> pd.Series:
        if self.sampling == "monthly":
            return self._call(self.monthly, mkt, macro, curve).reindex(mkt.index, method="pad")
        raise NotImplementedError

    def monthly(self, mkt, macro=None, curve=None) -> pd.Series:
        raise NotImplementedError

    @staticmethod
    def _call(fn, mkt, macro, curve):
        return fn(mkt, macro, curve=curve) if _takes(fn, "curve") else fn(mkt, macro)

    def normalize(self, x: pd.Series) -> pd.Series:
        n = self.min_obs if self.min_obs is not None else (12 if self.sampling == "monthly" else 20)
        return rolling_percentile(x, self.lookback_years, n)

    def compute(self, mkt, macro=None, curve=None) -> pd.Series:
        if self.sampling == "monthly":
            return self.normalize(self._call(self.monthly, mkt, macro, curve)).reindex(mkt.index, method="pad")
        return self.normalize(self._call(self.raw, mkt, macro, curve)).reindex(mkt.index)

    def __repr__(self):
        return f"{self.name}()"


# --------------------------------------------------------------------------- built-in signals
@_signal
class TrendBreak(_Base):
    """Index below its `window_months` SMA (Faber); raw score = -(price / SMA - 1)."""
    index: str | None = "SP500"
    window_months: float = 10
    needs = ("index",)
    units = {"window_months": CAL_MONTHS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        days = calendar_window(p.index, months=self.window_months)
        sma = p.rolling(f"{days}D").mean()
        out = -(p / sma - 1)
        out[window_not_full(p.index, days)] = np.nan
        return out


@_signal(renamed={"window_days": _renamed("days", lambda v: v / CAL_PER_TRADING_DAY,
                                          lambda v: v * CAL_PER_TRADING_DAY)})
class Breadth(_Base):
    """1 - fraction of the universe above its `days`-trading-day moving average (default 138 ~ 200 calendar days).

    The old `window_days` was a CALENDAR-day count; it is still accepted (deprecated) and converted.
    """
    days: float = 138
    needs = ("prices",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        px = mkt.prices
        w = calendar_window(px.index, days=self.days, min_obs=6)
        ma = px.rolling(f"{w}D").mean()
        first = px.notna().to_numpy().argmax(axis=0)
        eligible = px.index.to_numpy()[:, None] >= (px.index.to_numpy()[first]
                                                    + np.timedelta64(w, "D"))[None, :]
        valid = eligible & px.notna().to_numpy() & ma.notna().to_numpy()
        above = (px.to_numpy() > ma.to_numpy()) & valid
        n = valid.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            frac = np.where(n >= 10, above.sum(axis=1) / n, np.nan)
        return pd.Series(1 - frac, index=px.index)


@_signal(renamed={"window": _renamed("days")})
class VolRegime(_Base):
    """Realized volatility of the index over `days` trading days (as calendar time, >= 6 observations)."""
    index: str | None = "SP500"
    days: int = 63
    needs = ("index",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        r = np.log(p).diff()
        w = calendar_window(p.index, days=self.days, min_obs=6)
        vol = r.rolling(f"{w}D", min_periods=5).std() * np.sqrt(mkt.ppy)
        vol[window_not_full(p.index, w)] = np.nan
        return vol


@_signal
class DrawdownFromPeak(_Base):
    """Depth of the index's current drawdown from its running peak."""
    index: str | None = "SP500"
    needs = ("index",)

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        return -(p / p.cummax() - 1)


@_signal
class ValuationStretch(_Base):
    """Universe aggregate of a valuation column (P/E, or CAPE-like `pe_avg3`); all-NaN if unavailable.

    Snapshots are expensive, so the series is sampled at calendar month-ends via `Market.view` and
    padded to daily, not recomputed every day.
    """
    col: str = "pe"
    agg: str = "median"
    sampling = "monthly"
    needs = ("fundamentals",)

    def monthly(self, mkt, macro=None, curve=None):
        dates = _month_end_dates(mkt.index)
        first_report = pd.Timestamp(mkt.fund["date"].min()) if len(mkt.fund) else None
        vals = []
        for d in dates:
            v = np.nan
            if first_report is not None and d >= first_report:
                try:
                    s = mkt.view(d)[self.col].replace([np.inf, -np.inf], np.nan).dropna()
                    if len(s) >= 5:
                        v = float(getattr(s, self.agg)())
                except KeyError:
                    pass
            vals.append(v)
        return pd.Series(vals, index=dates, name=self.name)


@_signal
class MomentumCrash(_Base):
    """Momentum is losing money while the market is in a drawdown (the setup for momentum crashes).

    Each month, 12-1 momentum deciles are formed on month-end prices; the top-minus-bottom decile return
    over the following month is the realized spread. Raw risk = max(0, -`spread_months`-month spread) *
    max(0, depth of the market's drawdown from its 12-month high).
    """
    index: str | None = None            # market proxy for the drawdown; default equal-weight universe
    decile: float = 0.1
    spread_months: int = 3
    sampling = "monthly"
    needs = ("prices",)
    units = {"spread_months": CAL_MONTHS}

    def monthly(self, mkt, macro=None, curve=None):
        me = _month_end_dates(mkt.index)
        M = mkt.prices.reindex(me).to_numpy()
        n = len(me)
        spread = np.full(n, np.nan)
        for j in range(12, n - 1):                 # formed at j, realized over month j+1
            mom = M[j - 1] / M[j - 12] - 1
            nxt = M[j + 1] / M[j] - 1
            ok = np.isfinite(mom) & np.isfinite(nxt)
            k = int(ok.sum() * self.decile)
            if k < 3:
                continue
            order = np.argsort(np.where(ok, mom, np.nan))     # NaN last
            ok_idx = order[:ok.sum()]
            spread[j + 1] = nxt[ok_idx[-k:]].mean() - nxt[ok_idx[:k]].mean()
        sp = pd.Series(spread, index=me)
        recent = sp.rolling(self.spread_months, min_periods=self.spread_months).sum()
        mk = (_target_series(mkt, self.index) if self.index else _universe_curve(mkt)).reindex(me)
        dd = (mk / mk.rolling(12, min_periods=6).max() - 1).clip(upper=0)
        return (-recent).clip(lower=0) * (-dd)


@_signal(renamed={"lag_days": _renamed("lag_calendar_days")})
class RateShock(_Base):
    """Change in a macro rate series (e.g. prime or the risk-free rate) over the last `months`; needs macro."""
    series: str = "prime"
    months: int = 12
    lag_calendar_days: int = 0           # publication lag applied to the macro series
    needs = ("macro:prime",)
    units = {"months": CAL_MONTHS, "lag_calendar_days": CAL_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        s = _macro_get(macro, self.series)
        if s is None or s.empty:
            return _blank(mkt)
        s = s.copy()
        s.index = s.index + pd.Timedelta(days=self.lag_calendar_days)
        now = s.reindex(mkt.index, method="pad")
        then = s.reindex(mkt.index - pd.DateOffset(months=self.months), method="pad")
        out = now - then.to_numpy()
        out[mkt.index < s.index[0] + pd.DateOffset(months=self.months)] = np.nan
        return out


@_signal
class Dispersion(_Base):
    """Cross-sectional standard deviation of trailing `months`-month returns across the universe."""
    months: int = 1
    min_names: int = 10
    needs = ("prices",)
    units = {"months": CAL_MONTHS}

    def raw(self, mkt, macro=None, curve=None):
        idx = mkt.index
        back = idx.searchsorted(idx - pd.DateOffset(months=self.months), side="left")
        px = mkt.prices.to_numpy()
        ret = px / px[np.minimum(back, len(idx) - 1)] - 1
        ok = np.isfinite(ret) & (back < np.arange(len(idx)))[:, None]
        ret = np.where(ok, ret, np.nan)
        n = ok.sum(axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            sd = np.nanstd(ret, axis=1, ddof=1)
        valid = (n >= self.min_names) & (idx >= idx[0] + pd.DateOffset(months=self.months))
        return pd.Series(np.where(valid, sd, np.nan), index=idx)


# name -> class; a plain dict so other modules (exit_inputs) can `.update()` it on import
BUILTINS = {c.__name__: c for c in (TrendBreak, Breadth, VolRegime, DrawdownFromPeak, ValuationStretch,
                                    MomentumCrash, RateShock, Dispersion)}


# --------------------------------------------------------------------------- the screen
def hysteresis(score: pd.Series, enter: float, exit_: float, votes: pd.Series | None = None) -> pd.Series:
    """Bool flag: turns on when score >= enter (and votes ok), off when score < exit_; NaN carries the state."""
    on, out = False, np.zeros(len(score), bool)
    s = score.to_numpy()
    v = None if votes is None else votes.to_numpy()
    for i in range(len(s)):
        if not np.isnan(s[i]):
            if on and s[i] < exit_:
                on = False
            elif not on and s[i] >= enter and (v is None or v[i]):
                on = True
        out[i] = on
    return pd.Series(out, index=score.index, name="flagged")


def _check_dates(index: pd.DatetimeIndex, check: str) -> pd.DatetimeIndex:
    """Check dates for a cadence alias, covering the index span (month aliases "M"/"Q"/"Y" accepted)."""
    alias = {"M": "ME", "Q": "QE", "Y": "YE", "A": "YE"}.get(check, check)
    try:
        return pd.date_range(index[0].normalize(), index[-1], freq=alias)
    except ValueError:
        return pd.date_range(index[0].normalize(), index[-1], freq=check)


def sample_at_checks(flag: pd.Series, check: str = "D") -> pd.Series:
    """Let `flag` change only at check points: value at the last bar on/before each check date, padded forward.

    Check dates run from the first bar at the `check` cadence; before the first one the flag is off.
    "D" returns the flag unchanged.
    """
    if check in (None, "D", "B"):
        return flag
    idx = flag.index
    pos = idx.searchsorted(_check_dates(idx, check), side="right") - 1
    pos = np.unique(pos[pos >= 0])
    out = np.zeros(len(idx), bool)
    if len(pos):
        v = flag.to_numpy()[pos]
        k = np.searchsorted(pos, np.arange(len(idx)), side="right") - 1
        out = np.where(k >= 0, v[np.maximum(k, 0)], False)
    return pd.Series(out, index=idx, name=flag.name)


def hold_min_days(flag: pd.Series, days: int) -> pd.Series:
    """Ignore changes of `flag` until `days` calendar days have passed since the last accepted change."""
    if not days or days <= 0:
        return flag
    t = flag.index.to_numpy("datetime64[D]").astype(np.int64)
    v = flag.to_numpy()
    out = np.empty(len(v), bool)
    cur, since = bool(v[0]) if len(v) else False, -10**12
    for i in range(len(v)):
        if bool(v[i]) != cur and t[i] - since >= days:
            cur, since = bool(v[i]), t[i]
        out[i] = cur
    return pd.Series(out, index=flag.index, name=flag.name)


@dataclass
class ExitScreen:
    """Weighted average of signal percentiles, plus a hysteresis flag.

    `weights` maps signal name -> weight (default equal); signals that are all-NaN (no data, no macro)
    are dropped, and per day the weights are renormalized over the signals that have a value.
    `min_votes`: if set, the flag can only turn on when at least that many signals are individually at
    or above `threshold`. The flag turns off when the score drops below `threshold - hysteresis`.

    Two clocks, kept separate. The *rebalance clock* belongs to the entry screen (`rebalance_months`
    in `backtest.Config`) and nothing here changes it. The *exit clock* belongs to this screen:
    `check` is the cadence at which the flag may change (pandas offset alias; "D" = every bar,
    "W-FRI", "M"/"ME"; "H" is reserved for intraday bars). `state()` is computed on the market index,
    sampled at the last bar on/before each check date and padded forward; before the first check
    point the flag is off. `check` and `rebalance_months` are independent. `lag` is the execution lag
    in bars (signal at close t, exposure from close t+lag) and is used by `overlay`/`evaluate`.
    `min_hold_days` keeps either state at least that many calendar days after it changes.
    """
    signals: list
    weights: dict | None = None
    threshold: float = 0.7
    min_votes: int | None = None
    hysteresis: float = 0.1
    check: str = "D"
    lag: int = 1
    min_hold_days: int = 0

    def components(self, mkt, macro=None, curve=None) -> pd.DataFrame:
        """Per-signal percentile scores (columns by signal name; all-NaN signals left out).

        `curve`: the portfolio's own fully-invested curve for signals that need it (None = index evaluation)."""
        cols = {}
        for s in self.signals:
            c = _compute(s, mkt, macro, curve).reindex(mkt.index)
            if c.notna().any():
                cols[s.name] = c
        return pd.DataFrame(cols, index=mkt.index)

    def _weights(self, cols) -> np.ndarray:
        w = np.array([(self.weights or {}).get(c, 1.0) for c in cols], float)
        return w

    def score(self, mkt, macro=None, components: pd.DataFrame | None = None, curve=None) -> pd.Series:
        comp = self.components(mkt, macro, curve) if components is None else components
        if comp.empty:
            return _blank(mkt).rename("score")
        w = self._weights(comp.columns)
        x = comp.to_numpy()
        ok = np.isfinite(x)
        den = (ok * w).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            sc = np.where(den > 0, np.where(ok, x, 0).dot(w) / den, np.nan)
        return pd.Series(sc, index=comp.index, name="score")

    def state(self, mkt, macro=None, components: pd.DataFrame | None = None, curve=None) -> pd.Series:
        """Bool per bar, True = flagged (elevated risk, derisk). Enter at `threshold`, leave below threshold - hysteresis;
        changes only at `check` points and not within `min_hold_days` of the previous change."""
        comp = self.components(mkt, macro, curve) if components is None else components
        sc = self.score(mkt, macro, comp)
        votes = None
        if self.min_votes:
            votes = (comp >= self.threshold).sum(axis=1) >= self.min_votes
        flag = hysteresis(sc, self.threshold, self.threshold - self.hysteresis, votes)
        flag = sample_at_checks(flag, self.check)
        return hold_min_days(flag, self.min_hold_days)


# --------------------------------------------------------------------------- overlay
@dataclass
class OverlayResult:
    curve: pd.Series                 # derisked equity curve (starts at 1)
    baseline: pd.Series              # always-in curve (starts at 1)
    exposure: pd.Series              # exposure actually held each period
    stats: pd.DataFrame              # rows always_in / derisked: CAGR, Vol, Sharpe, MaxDD, ..., Switches
    switches: int
    turnover: float                  # sum of |change in exposure|
    frac_flagged: float


def overlay(curve: pd.Series, state: pd.Series, exposure_when_flagged: float = 0.0,
            cash_rate: float | pd.Series = 0.0, cost: float = 0.0, lag: int = 1) -> OverlayResult:
    """Trade the flag: hold `exposure_when_flagged` of the curve (rest in cash at `cash_rate`/yr) while flagged.

    The state seen at the close of period t sets the exposure for period t+`lag` (default one period),
    so the overlay never trades on information it did not have. `cost` is charged per unit of exposure traded.
    """
    c = curve.dropna().astype(float)
    st = state.reindex(c.index, method="pad").fillna(False).astype(bool)
    expo = pd.Series(np.where(st, exposure_when_flagged, 1.0), index=c.index)
    held = expo.shift(lag).fillna(1.0)
    r = c.pct_change().fillna(0.0)
    dt = pd.Series(c.index, index=c.index).diff().dt.days.fillna(0) / 365.25
    cash = (cash_rate.reindex(c.index, method="pad").fillna(0.0) if isinstance(cash_rate, pd.Series)
            else pd.Series(float(cash_rate), index=c.index)) * dt
    trade = held.diff().abs().fillna(0.0)
    ret = held * r + (1 - held) * cash - cost * trade
    derisked = (1 + ret).cumprod()
    base = c / c.iloc[0]
    rf = float(cash_rate.mean()) if isinstance(cash_rate, pd.Series) else float(cash_rate)
    switches = int((trade > 1e-12).sum())
    stats = pd.DataFrame({"always_in": metrics.summarize(base, rf=rf),
                          "derisked": metrics.summarize(derisked, rf=rf)}).T
    stats["Switches"] = [0, switches]
    return OverlayResult(derisked, base, held, stats, switches, float(trade.sum()),
                         float(st.mean()))


# --------------------------------------------------------------------------- evaluation
@dataclass
class ExitEvaluation:
    forward: pd.DataFrame            # index (horizon_months, state) -> n, mean, median, hit_rate, worst
    frac_flagged: float              # share of scored days spent flagged
    drawdowns: pd.DataFrame          # largest drawdowns of the target and when the flag first fired
    overlay: OverlayResult
    score: pd.Series
    state: pd.Series

    def __str__(self):
        with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 120):
            return (f"time flagged: {self.frac_flagged:.1%}\n\nforward returns by horizon (months)\n"
                    f"{self.forward}\n\nlargest drawdowns\n{self.drawdowns}\n\noverlay\n{self.overlay.stats}")


def forward_returns(curve: pd.Series, months: int) -> pd.Series:
    """Return from each date to the last observation on or before date + `months` (NaN past the end)."""
    c = curve.dropna()
    tgt = c.index + pd.DateOffset(months=months)
    pos = c.index.searchsorted(tgt, side="right") - 1
    fwd = c.to_numpy()[np.clip(pos, 0, len(c) - 1)] / c.to_numpy() - 1
    # a horizon only counts when the data really reaches it (monthly data lands exactly on the date)
    fwd[(tgt > c.index[-1]) | (pos <= np.arange(len(c)))] = np.nan
    return pd.Series(fwd, index=c.index)


def drawdown_episodes(curve: pd.Series, top: int = 3) -> pd.DataFrame:
    """The `top` deepest peak-to-trough episodes: peak, trough, depth."""
    c = curve.dropna()
    peak_id = (c == c.cummax()).cumsum()
    rows = []
    for _, g in c.groupby(peak_id):
        trough = g.idxmin()
        rows.append((g.index[0], trough, g.min() / g.iloc[0] - 1))
    df = pd.DataFrame(rows, columns=["peak", "trough", "depth"])
    return df.sort_values("depth").head(top).reset_index(drop=True)


def evaluate(exit_screen: ExitScreen, mkt, target_curve: pd.Series, horizons=(1, 3, 12), macro=None,
             top_drawdowns: int = 3, exposure_when_flagged: float = 0.0,
             cash_rate: float | pd.Series = 0.0, cost: float = 0.0, curve=None) -> ExitEvaluation:
    """What the flag would have told you about `target_curve` (an index or a screen's equity curve).

    Forward returns over each horizon (in calendar months) split by state on the start day; fraction
    of time flagged; for the deepest drawdowns, how many days before the trough the flag first fired
    (within peak..trough) and how much of the fall was still ahead at that moment; and the overlay.
    `curve` is handed to signals that need the portfolio's own curve (None when `target_curve` is an index).
    """
    comp = exit_screen.components(mkt, macro, curve)
    score = exit_screen.score(mkt, macro, comp)
    state = exit_screen.state(mkt, macro, comp)
    return evaluate_state(score, state, target_curve, horizons, top_drawdowns, exposure_when_flagged,
                          cash_rate, cost, exit_screen.lag)


def evaluate_state(score: pd.Series, state: pd.Series, target_curve: pd.Series, horizons=(1, 3, 12),
                   top_drawdowns: int = 3, exposure_when_flagged: float = 0.0,
                   cash_rate: float | pd.Series = 0.0, cost: float = 0.0, lag: int = 1) -> ExitEvaluation:
    """`evaluate` for a precomputed score and flag (what calibration reuses on window slices)."""
    curve = target_curve.dropna().astype(float)
    st = state.reindex(curve.index, method="pad").fillna(False)
    scored = score.reindex(curve.index, method="pad").notna()

    rows = {}
    for h in horizons:
        fwd = forward_returns(curve, h)
        for label, mask in (("flagged", st), ("unflagged", ~st & scored)):
            x = fwd[mask & fwd.notna()]
            rows[(h, label)] = dict(n=len(x), mean=x.mean(), median=x.median(),
                                    hit_rate=(x > 0).mean() if len(x) else np.nan,
                                    worst=x.min() if len(x) else np.nan)
    forward = pd.DataFrame(rows).T
    forward.index.names = ["horizon_months", "state"]

    dd = drawdown_episodes(curve, top_drawdowns)
    first, lead, left = [], [], []
    for _, e in dd.iterrows():
        w = st[e.peak:e.trough]
        hit = w[w]
        if len(hit):
            f = hit.index[0]
            first.append(f)
            lead.append((e.trough - f).days)
            left.append(curve[e.trough] / curve[f] - 1)
        else:
            first.append(pd.NaT)
            lead.append(np.nan)
            left.append(np.nan)
    dd["first_flag"], dd["lead_days"], dd["fall_after_flag"] = first, lead, left

    ov = overlay(curve, st, exposure_when_flagged, cash_rate, cost, lag)
    return ExitEvaluation(forward, float(st[scored].mean()) if scored.any() else float("nan"),
                          dd, ov, score, state)
