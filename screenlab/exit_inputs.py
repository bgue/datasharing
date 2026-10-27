"""Additional exit inputs (DESIGN H3), registered into `exit_signals.BUILTINS` on import.

Same contract as the built-ins in `exit_signals`: `compute(mkt, macro=None, curve=None)` returns a float
Series on `mkt.index`, higher = riskier, normalized to a rolling percentile rank (10y calendar lookback),
strictly causal, NaN where the inputs are absent (missing columns, macro series, or `curve`). `curve` is
the portfolio's own fully-invested equity curve (None when evaluating against an index). Windows are
nominal trading days converted to calendar time (x 365.25 / 252) and widened to a minimum number of
observations, so daily and monthly price data both work. Every class has a `describe()` classmethod.

Each `compute` is implemented here (not inherited) so the signals work whether or not the base
protocol in `exit_signals` already passes `curve` through.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np
import pandas as pd

from . import exit_signals as _es
from .exit_signals import _Base, _blank, _macro_get, _month_end_dates, _target_series

_CAL = 365.25 / 252          # calendar days per nominal trading day


def _step(index: pd.DatetimeIndex) -> float:
    """Median spacing of the index in calendar days (1 for daily, ~30 for monthly)."""
    if len(index) < 2:
        return 1.0
    return float(np.median(np.diff(index.values).astype("timedelta64[D]").astype(float)))


def _days(index, n: float, min_obs: int = 1) -> int:
    """Calendar-day length of an `n`-trading-day window, at least `min_obs` observations long."""
    return max(int(round(n * _CAL)), int(np.ceil(min_obs * _step(index))))


def _not_full(index, days: int) -> np.ndarray:
    """True where a `days`-long window starting at the first bar is not yet full."""
    return np.asarray(index < index[0] + pd.Timedelta(days=days))


class _In(_Base):
    """Base for the H3 signals: `raw(mkt, macro, curve)` -> `normalize` -> reindexed to `mkt.index`."""
    needs: tuple = ()

    def raw(self, mkt, macro=None, curve=None) -> pd.Series:
        raise NotImplementedError

    def compute(self, mkt, macro=None, curve=None) -> pd.Series:
        return self.normalize(self.raw(mkt, macro, curve)).reindex(mkt.index)

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.__name__, "params": {f.name: f.default for f in fields(cls)},
                "needs": list(cls.needs)}


class _InMonthly(_In):
    """Monthly-sampled (like `ValuationStretch`): rank the month-end series, pad to daily (causal)."""
    min_obs = 12

    def monthly(self, mkt, macro=None, curve=None) -> pd.Series:
        raise NotImplementedError

    def raw(self, mkt, macro=None, curve=None):
        return self.monthly(mkt, macro, curve).reindex(mkt.index, method="pad")

    def compute(self, mkt, macro=None, curve=None):
        return self.normalize(self.monthly(mkt, macro, curve)).reindex(mkt.index, method="pad")


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
@dataclass(repr=False)
class CorrelationSpike(_In):
    """Mean pairwise correlation of returns across the universe over `window_days` (crises: all move together).

    Efficient: no correlation matrix. Each name's return is scaled by its own trailing 1y volatility
    (known the bar before), S = sum of the scaled returns, and over the rolling window
    mean_corr = (var(S) - sum_i var(z_i)) / ((sum_i sd(z_i))^2 - sum_i var(z_i)),
    which is the mean pairwise correlation when the scale is constant over the window (an
    approximation under time-varying scale). Missing returns count as zero; needs >= `min_names` live names.
    """
    window_days: int = 21
    min_names: int = 10
    needs = ("prices",)

    def raw(self, mkt, macro=None, curve=None):
        px, idx = mkt.prices, mkt.index
        r = px.pct_change(fill_method=None)
        year_obs = max(4, min(20, int(mkt.ppy / 2)))
        sd = r.rolling("365D", min_periods=year_obs).std().shift(1)
        z = (r / sd.where(sd > 0)).clip(-8, 8)
        live = z.notna().sum(axis=1)
        z = z.fillna(0.0)
        days = _days(idx, self.window_days, 6)
        v_i = z.rolling(f"{days}D", min_periods=5).var()
        var_s = z.sum(axis=1).rolling(f"{days}D", min_periods=5).var()
        a = v_i.sum(axis=1)
        b = v_i.pow(0.5).sum(axis=1) ** 2 - a
        with np.errstate(invalid="ignore", divide="ignore"):
            rho = ((var_s - a) / b.where(b > 0)).clip(-1, 1)
        rho[(live < self.min_names).to_numpy() | _not_full(idx, days)] = np.nan
        return rho


@dataclass(repr=False)
class NewLowsMinusHighs(_In):
    """Fraction of names at a `window_days` (52-week) low minus the fraction at a high; needs >= 10 names."""
    window_days: int = 252
    needs = ("prices",)

    def raw(self, mkt, macro=None, curve=None):
        px, idx = mkt.prices, mkt.index
        days = _days(idx, self.window_days, 6)
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


@dataclass(repr=False)
class VolTermStructure(_In):
    """Short / long realized volatility ratio of the index (a spike = stress); windows in trading days."""
    short: int = 21
    long: int = 126
    index: str | None = "SP500"
    needs = ("index",)

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        r = np.log(p).diff()
        ds, dl = _days(p.index, self.short, 6), _days(p.index, self.long, 12)
        dl = max(dl, ds + 1)
        vs = r.rolling(f"{ds}D", min_periods=5).std()
        vl = r.rolling(f"{dl}D", min_periods=10).std()
        out = vs / vl.where(vl > 0)
        out[_not_full(p.index, dl)] = np.nan
        return out


@dataclass(repr=False)
class DeathCross(_In):
    """Fast MA (`fast` trading days) below slow MA (`slow`): raw = -(fast / slow - 1), so a wider gap = riskier."""
    fast: int = 50
    slow: int = 200
    index: str | None = "SP500"
    needs = ("index",)

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        df, ds = _days(p.index, self.fast, 2), _days(p.index, self.slow, 8)
        ds = max(ds, df + 1)
        out = -(p.rolling(f"{df}D").mean() / p.rolling(f"{ds}D").mean() - 1)
        out[_not_full(p.index, ds)] = np.nan
        return out


@dataclass(repr=False)
class DistanceFromHigh(_In):
    """Fraction below the trailing `window_days` (52-week) high of the index."""
    index: str | None = "SP500"
    window_days: int = 252
    needs = ("index",)

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        return 1 - p / p.rolling(f"{_days(p.index, self.window_days, 6)}D").max()


@dataclass(repr=False)
class DownsideDeviation(_In):
    """Annualized semi-deviation of the index's returns (negative returns only) over `window` trading days."""
    window: int = 63
    index: str | None = "SP500"
    needs = ("index",)

    def raw(self, mkt, macro=None, curve=None):
        p = _target_series(mkt, self.index)
        d = _days(p.index, self.window, 6)
        down = np.log(p).diff().clip(upper=0.0) ** 2
        out = down.rolling(f"{d}D", min_periods=5).mean() ** 0.5 * np.sqrt(mkt.ppy)
        out[_not_full(p.index, d)] = np.nan
        return out


# --------------------------------------------------------------------------- portfolio-based
@dataclass(repr=False)
class PortfolioStop(_In):
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


@dataclass(repr=False)
class PortfolioVsIndex(_In):
    """Relative drawdown of the portfolio against the index over the last `months` (needs `curve`).

    rel = curve / index; raw = 1 - rel / (max of rel over the trailing window), i.e. how much the portfolio
    has lost relative to the market since its recent relative high. Percentile-ranked.
    """
    months: int = 3
    index: str | None = "SP500"
    needs = ("curve", "index")

    def raw(self, mkt, macro=None, curve=None):
        c = _curve_on(mkt, curve)
        if c is None:
            return _blank(mkt)
        p = _target_series(mkt, self.index).reindex(mkt.index)
        rel = c / p
        days = max(int(round(self.months * _es._DAYS_PER_MONTH)), int(np.ceil(2 * _step(mkt.index))))
        return 1 - rel / rel.rolling(f"{days}D").max()


# --------------------------------------------------------------------------- fundamentals-based
@dataclass(repr=False)
class EarningsBreadth(_InMonthly):
    """Fraction of names whose earnings fell (`eps < eps_prev`), sampled at month-ends; NaN without those columns."""
    min_names: int = 5
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


@dataclass(repr=False)
class YieldSpreadCompression(_InMonthly):
    """Fed-model style: universe median earnings yield minus the risk-free rate; lower = riskier.

    Sampled at month-ends; `rf` is an annual rate as a decimal (like `MacroSeries.rf`). NaN without the
    fundamentals columns behind `earnings_yield` or without the macro series. Raw = -(yield - rf).
    """
    rf: str = "rf"
    min_names: int = 5
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
@dataclass(repr=False)
class YieldCurve(_In):
    """Short minus long rate (macro series, decimals); inverted (positive) = riskier. NaN without both."""
    short: str = "rf"
    long: str = "long_rate"
    lag_days: int = 0                    # publication lag to apply to the macro series
    needs = ("macro:rf", "macro:long_rate")

    def raw(self, mkt, macro=None, curve=None):
        s, l = _macro_get(macro, self.short), _macro_get(macro, self.long)
        if s is None or l is None or s.empty or l.empty:
            return _blank(mkt)
        lag = pd.Timedelta(days=self.lag_days)
        both = pd.concat([s.rename("s"), l.rename("l")], axis=1)
        both.index = both.index + lag
        both = both.ffill().dropna()
        spread = (both["s"] - both["l"]).reindex(mkt.index, method="pad")
        spread[mkt.index < both.index[0]] = np.nan
        return spread


@dataclass(repr=False)
class CreditSpread(_In):
    """Credit-spread level and its change over `months`: score = mean of the two rolling percentiles.

    Wider and widening = riskier. NaN without the macro series (the change part is NaN for the first
    `months`, then only the level counts).
    """
    series: str = "credit_spread"
    months: int = 3
    lag_days: int = 0
    needs = ("macro:credit_spread",)

    def raw(self, mkt, macro=None, curve=None):
        s = _macro_get(macro, self.series)
        if s is None or s.empty:
            return _blank(mkt)
        s = s.copy()
        s.index = s.index + pd.Timedelta(days=self.lag_days)
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
