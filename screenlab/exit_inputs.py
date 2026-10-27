"""Additional exit inputs (DESIGN H3), registered into `exit_signals.BUILTINS` on import.

Same contract and the same base class as the built-ins in `exit_signals`: `compute(mkt, macro=None,
curve=None)` returns a float Series on `mkt.index`, higher = riskier, normalized to a rolling percentile
rank (10y calendar lookback), strictly causal, NaN where the inputs are absent (missing columns, macro
series, or `curve`). `curve` is the portfolio's own fully-invested equity curve (None when evaluating
against an index). Windows follow the shared convention (`exit_signals.calendar_window`): `days` =
trading days, `months` = calendar months, converted to calendar time and widened to a minimum number of
observations, so daily and monthly price data both work. `describe()` lists parameters with their units.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import exit_signals as _es
from .exit_signals import (CAL_DAYS, CAL_MONTHS, TRADING_DAYS, _Base, _blank, _macro_get, _month_end_dates,
                           _renamed, _signal, _target_series, calendar_window, window_not_full)


def _curve_on(mkt, curve) -> pd.Series | None:
    """The portfolio curve pad-aligned to the market index (NaN before it starts); None if absent."""
    if curve is None:
        return None
    c = pd.Series(curve).dropna().astype(float)
    if c.empty:
        return None
    c = c[~c.index.duplicated(keep="last")].sort_index()
    return c.reindex(c.index.union(mkt.index)).ffill().reindex(mkt.index).where(mkt.index >= c.index[0])


# --------------------------------------------------------------------------- price-based
@_signal(renamed={"window_days": _renamed("days")})
class CorrelationSpike(_Base):
    """Mean pairwise correlation of returns across the universe over `days` trading days (crises: all move together).

    Efficient: no correlation matrix. Each name's return is scaled by its own trailing 1y volatility
    (known the bar before), S = sum of the scaled returns, and over the rolling window
    mean_corr = (var(S) - sum_i var(z_i)) / ((sum_i sd(z_i))^2 - sum_i var(z_i)),
    which is the mean pairwise correlation when the scale is constant over the window (an
    approximation under time-varying scale). Missing returns count as zero; needs >= `min_names` live names.
    """
    days: int = 21
    min_names: int = 10
    needs = ("prices",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        px, idx = mkt.prices, mkt.index
        r = px.pct_change(fill_method=None)
        year_obs = max(4, min(20, int(mkt.ppy / 2)))
        sd = r.rolling("365D", min_periods=year_obs).std().shift(1)
        z = (r / sd.where(sd > 0)).clip(-8, 8)
        live = z.notna().sum(axis=1)
        z = z.fillna(0.0)
        days = calendar_window(idx, days=self.days, min_obs=6)
        v_i = z.rolling(f"{days}D", min_periods=5).var()
        var_s = z.sum(axis=1).rolling(f"{days}D", min_periods=5).var()
        a = v_i.sum(axis=1)
        b = v_i.pow(0.5).sum(axis=1) ** 2 - a
        with np.errstate(invalid="ignore", divide="ignore"):
            rho = ((var_s - a) / b.where(b > 0)).clip(-1, 1)
        rho[(live < self.min_names).to_numpy() | window_not_full(idx, days)] = np.nan
        return rho


@_signal(renamed={"window_days": _renamed("days")})
class NewLowsMinusHighs(_Base):
    """Fraction of names at a `days`-trading-day (default 252 = 52-week) low minus the fraction at a high; needs >= 10 names."""
    days: int = 252
    needs = ("prices",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        px, idx = mkt.prices, mkt.index
        days = calendar_window(idx, days=self.days, min_obs=6)
        lo = px.rolling(f"{days}D").min()
        hi = px.rolling(f"{days}D").max()
        first = px.notna().to_numpy().argmax(axis=0)
        t = idx.to_numpy()
        eligible = t[:, None] >= (t[first] + np.timedelta64(days, "D"))[None, :]
        valid = eligible & px.notna().to_numpy()
        x = px.to_numpy()
        n = valid.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            frac = np.where(n >= 10, ((x <= lo.to_numpy()) & valid).sum(axis=1) / n
                            - ((x >= hi.to_numpy()) & valid).sum(axis=1) / n, np.nan)
        return pd.Series(frac, index=idx)


@_signal(renamed={"short": _renamed("short_days"), "long": _renamed("long_days")})
class VolTermStructure(_Base):
    """Short / long realized volatility ratio of the index (a spike = stress); `short_days` / `long_days` in trading days."""
    short_days: int = 21
    long_days: int = 126
    index: str | None = "SP500"
    needs = ("index",)
    units = {"short_days": TRADING_DAYS, "long_days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        r = np.log(p).diff()
        ds = calendar_window(p.index, days=self.short_days, min_obs=6)
        dl = calendar_window(p.index, days=self.long_days, min_obs=12)
        dl = max(dl, ds + 1)
        vs = r.rolling(f"{ds}D", min_periods=5).std()
        vl = r.rolling(f"{dl}D", min_periods=10).std()
        out = vs / vl.where(vl > 0)
        out[window_not_full(p.index, dl)] = np.nan
        return out


@_signal(renamed={"fast": _renamed("fast_days"), "slow": _renamed("slow_days")})
class DeathCross(_Base):
    """Fast MA (`fast_days` trading days) below slow MA (`slow_days`): raw = -(fast / slow - 1), so a wider gap = riskier."""
    fast_days: int = 50
    slow_days: int = 200
    index: str | None = "SP500"
    needs = ("index",)
    units = {"fast_days": TRADING_DAYS, "slow_days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        df = calendar_window(p.index, days=self.fast_days, min_obs=2)
        ds = calendar_window(p.index, days=self.slow_days, min_obs=8)
        ds = max(ds, df + 1)
        out = -(p.rolling(f"{df}D").mean() / p.rolling(f"{ds}D").mean() - 1)
        out[window_not_full(p.index, ds)] = np.nan
        return out


@_signal(renamed={"window_days": _renamed("days")})
class DistanceFromHigh(_Base):
    """Fraction below the trailing `days`-trading-day (default 252 = 52-week) high of the index."""
    index: str | None = "SP500"
    days: int = 252
    needs = ("index",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        return 1 - p / p.rolling(f"{calendar_window(p.index, days=self.days, min_obs=6)}D").max()


@_signal(renamed={"window": _renamed("days")})
class DownsideDeviation(_Base):
    """Annualized semi-deviation of the index's returns (negative returns only) over `days` trading days."""
    days: int = 63
    index: str | None = "SP500"
    needs = ("index",)
    units = {"days": TRADING_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        d = calendar_window(p.index, days=self.days, min_obs=6)
        down = np.log(p).diff().clip(upper=0.0) ** 2
        out = down.rolling(f"{d}D", min_periods=5).mean() ** 0.5 * np.sqrt(mkt.ppy)
        out[window_not_full(p.index, d)] = np.nan
        return out


# --------------------------------------------------------------------------- portfolio-based
@_signal
class PortfolioStop(_Base):
    """The portfolio's own drawdown from its running peak, as a trailing stop (needs `curve`).

    Unlike the other signals this is NOT a percentile: the score is the raw `drawdown / stop` ratio
    clipped to 0..1, so it equals 1 exactly once the portfolio is down `drawdown` (default 15%) or more
    from its peak and rises linearly from 0 before that. Percentile-ranking would make a stop depend on
    how often the portfolio has been this deep before, which defeats the point of a stop.
    """
    drawdown: float = 0.15
    needs = ("curve",)

    def raw(self, mkt, macro=None, curve=None):
        c = _curve_on(mkt, curve)
        if c is None:
            return _blank(mkt)
        dd = 1 - c / c.cummax()
        return (dd / self.drawdown).clip(0.0, 1.0)

    def normalize(self, x):
        return x


@_signal
class PortfolioVsIndex(_Base):
    """Relative drawdown of the portfolio against the index over the last `months` (needs `curve`).

    rel = curve / index; raw = 1 - rel / (max of rel over the trailing window), i.e. how much the portfolio
    has lost relative to the market since its recent relative high. Percentile-ranked.
    """
    months: int = 3
    index: str | None = "SP500"
    needs = ("curve", "index")
    units = {"months": CAL_MONTHS}

    def raw(self, mkt, macro=None, curve=None):
        c = _curve_on(mkt, curve)
        if c is None:
            return _blank(mkt)
        p = _target_series(mkt, self.index).reindex(mkt.index)
        rel = c / p
        days = calendar_window(mkt.index, months=self.months, min_obs=2)
        return 1 - rel / rel.rolling(f"{days}D").max()


# --------------------------------------------------------------------------- fundamentals-based
@_signal
class EarningsBreadth(_Base):
    """Fraction of names whose earnings fell (`eps < eps_prev`), sampled at month-ends; NaN without those columns."""
    min_names: int = 5
    sampling = "monthly"
    needs = ("fundamentals",)

    def monthly(self, mkt, macro=None, curve=None):
        dates = _month_end_dates(mkt.index)
        out = pd.Series(np.nan, index=dates, name=self.name)
        if not len(mkt.fund) or not {"eps", "eps_prev"} <= set(mkt.fund.columns):
            return out
        first = pd.Timestamp(mkt.fund["date"].min())
        for d in dates[dates >= first]:
            v = mkt.view(d)
            if "eps" not in v or "eps_prev" not in v:
                continue
            ok = v["eps"].notna() & v["eps_prev"].notna()
            if ok.sum() >= self.min_names:
                out[d] = float((v.loc[ok, "eps"] < v.loc[ok, "eps_prev"]).mean())
        return out


@_signal
class YieldSpreadCompression(_Base):
    """Fed-model style: universe median earnings yield minus the risk-free rate; lower = riskier.

    Sampled at month-ends; `rf` is an annual rate as a decimal (like `MacroSeries.rf`). NaN without the
    fundamentals columns behind `earnings_yield` or without the macro series. Raw = -(yield - rf).
    """
    rf: str = "rf"
    min_names: int = 5
    sampling = "monthly"
    needs = ("fundamentals", "macro:rf")

    def monthly(self, mkt, macro=None, curve=None):
        dates = _month_end_dates(mkt.index)
        out = pd.Series(np.nan, index=dates, name=self.name)
        rf = _macro_get(macro, self.rf)
        if rf is None or rf.empty or not len(mkt.fund):
            return out
        rf_m = rf.reindex(rf.index.union(dates)).ffill().reindex(dates)
        first = pd.Timestamp(mkt.fund["date"].min())
        for d in dates[dates >= first]:
            v = mkt.view(d)
            if "earnings_yield" not in v:
                continue
            ey = v["earnings_yield"].replace([np.inf, -np.inf], np.nan).dropna()
            if len(ey) >= self.min_names:
                out[d] = -(float(ey.median()) - rf_m[d])
        return out


# --------------------------------------------------------------------------- macro-based
@_signal(renamed={"lag_days": _renamed("lag_calendar_days")})
class YieldCurve(_Base):
    """Short minus long rate (macro series, decimals); inverted (positive) = riskier. NaN without both."""
    short: str = "rf"
    long: str = "long_rate"
    lag_calendar_days: int = 0           # publication lag applied to the macro series
    needs = ("macro:rf", "macro:long_rate")
    units = {"lag_calendar_days": CAL_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        s, l = _macro_get(macro, self.short), _macro_get(macro, self.long)
        if s is None or l is None or s.empty or l.empty:
            return _blank(mkt)
        lag = pd.Timedelta(days=self.lag_calendar_days)
        both = pd.concat([s.rename("s"), l.rename("l")], axis=1)
        both.index = both.index + lag
        both = both.ffill().dropna()
        spread = (both["s"] - both["l"]).reindex(mkt.index, method="pad")
        spread[mkt.index < both.index[0]] = np.nan
        return spread


@_signal(renamed={"lag_days": _renamed("lag_calendar_days")})
class CreditSpread(_Base):
    """Credit-spread level and its change over `months`: score = mean of the two rolling percentiles.

    Wider and widening = riskier. NaN without the macro series (the change part is NaN for the first
    `months`, then only the level counts).
    """
    series: str = "credit_spread"
    months: int = 3
    lag_calendar_days: int = 0
    needs = ("macro:credit_spread",)
    units = {"months": CAL_MONTHS, "lag_calendar_days": CAL_DAYS}

    def raw(self, mkt, macro=None, curve=None):
        s = _macro_get(macro, self.series)
        if s is None or s.empty:
            return _blank(mkt)
        s = s.copy()
        s.index = s.index + pd.Timedelta(days=self.lag_calendar_days)
        now = s.reindex(mkt.index, method="pad")
        now[mkt.index < s.index[0]] = np.nan
        return now

    def compute(self, mkt, macro=None, curve=None):
        lvl = self.raw(mkt, macro, curve)
        if lvl.isna().all():
            return lvl
        then = lvl.reindex(mkt.index - pd.DateOffset(months=self.months), method="pad")
        chg = lvl - then.to_numpy()
        chg[mkt.index < mkt.index[0] + pd.DateOffset(months=self.months)] = np.nan
        both = pd.concat([self.normalize(lvl), self.normalize(chg)], axis=1)
        return both.mean(axis=1, skipna=True).reindex(mkt.index)


EXIT_INPUTS = (CorrelationSpike, NewLowsMinusHighs, VolTermStructure, DeathCross, DistanceFromHigh,
               DownsideDeviation, PortfolioStop, PortfolioVsIndex, EarningsBreadth,
               YieldSpreadCompression, YieldCurve, CreditSpread)


def _register():
    new = {c.__name__: c for c in EXIT_INPUTS}
    if isinstance(_es.BUILTINS, dict):
        _es.BUILTINS.update(new)
    else:                                  # legacy tuple of classes: rebind with ours appended
        _es.BUILTINS = tuple(_es.BUILTINS) + tuple(c for c in EXIT_INPUTS if c not in _es.BUILTINS)


_register()
