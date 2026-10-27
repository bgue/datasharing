from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .metrics import M
from .macro import DAYS_PER_YEAR, MacroSeries, accrual_curve, cpi_deflator


def _years(w: pd.Series) -> float:
    return (w.index[-1] - w.index[0]).days / DAYS_PER_YEAR


def _cagr(w: pd.Series) -> float:
    end = w.iloc[-1] / w.iloc[0]
    return end ** (1 / _years(w)) - 1 if end > 0 else -1.0


def _maxdd(w: pd.Series) -> float:
    return float((w / w.cummax() - 1).min())


def _worst12m(w: pd.Series) -> float:
    ago = w.reindex(w.index - pd.Timedelta(days=365), method="pad").to_numpy()
    r = w.to_numpy() / ago - 1
    return float(np.nanmin(r)) if np.isfinite(r).any() else np.nan


def _calendar_year_returns(w: pd.Series) -> pd.Series:
    y = w.groupby(w.index.year).last()
    return y / y.shift(1).fillna(1.0) - 1


def _prep(curve: pd.Series, macro: MacroSeries) -> tuple[pd.Series, MacroSeries]:
    w = curve.dropna()
    w = w / w.iloc[0]
    return w, macro.align(w.index)


def _hurdle(m: MacroSeries, spread: float) -> pd.Series | None:
    return accrual_curve(m.prime, spread) if m.prime is not None else None


def _breakeven_spread(w: pd.Series, prime: pd.Series) -> float:
    """Constant spread over prime s with accrual(prime + s)[-1] == w[-1] (i.e. NetOfFinancing CAGR = 0)."""
    target = w.iloc[-1]
    lo, hi = -1.0 - float(prime.min()) + 1e-9, 1.0
    while accrual_curve(prime, hi).iloc[-1] < target and hi < 1e4:
        hi *= 2
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if accrual_curve(prime, mid).iloc[-1] < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def extended_summary(curve: pd.Series, macro: MacroSeries, bench: pd.Series | None = None,
                     spread_over_prime: float = 0.0, leverage: float = 1.0) -> dict:
    """`metrics.summarize` plus real / home-currency / financing / excess-over-rf metrics.
    Metrics whose macro series is missing are omitted. Annual rates are decimals."""
    w, m = _prep(curve, macro)
    out = metrics.summarize(curve, bench)
    cagr = out[M.CAGR]
    yrs = _years(w)
    if m.cpi is not None:
        d = cpi_deflator(m.cpi)
        real = w / d
        infl = d.iloc[-1] ** (1 / yrs) - 1
        out.update({"Inflation": infl, "RealCAGR": (1 + cagr) / (1 + infl) - 1,
                    "RealMaxDD": _maxdd(real), "RealWorst12m": _worst12m(real)})
    if m.fx is not None:
        home = w * m.fx / m.fx.iloc[0]
        h = _cagr(home)
        out.update({"CAGR_home": h, "FX_contrib": (1 + h) / (1 + cagr) - 1})
    H = _hurdle(m, spread_over_prime)
    if H is not None:
        net = w / H
        out.update({"NetOfFinancingCAGR": _cagr(net),
                    "YearsAboveHurdle": float((_calendar_year_returns(w) > _calendar_year_returns(H)).mean()),
                    "BreakevenSpread": _breakeven_spread(w, m.prime)})
        if leverage != 1.0:
            lev = (leverage * w - (leverage - 1.0) * H)
            lev = lev.where(~(lev <= 0).cummax(), 0.0)      # wiped out stays wiped out
            out.update({"Leverage": leverage, "LeveredCAGR": _cagr(lev),
                        "LeveredMaxDD": _maxdd(lev)})
    if m.rf is not None:
        R = accrual_curve(m.rf)
        r = w.pct_change().dropna()
        ex = r - R.pct_change().dropna()
        ppy = (len(w) - 1) / yrs
        out["ExcessOverRf"] = cagr - _cagr(R)
        out["SharpeRf"] = ex.mean() * ppy / (r.std() * np.sqrt(ppy)) if r.std() > 0 else np.nan
    return out


def decompose(curve: pd.Series, macro: MacroSeries, spread_over_prime: float = 0.0) -> pd.DataFrame:
    """Geometric decomposition over the whole window. Columns `Growth` (end/start factor) and `CAGR`.
    Rows (group, component): nominal = real x inflation; home = local x fx; net = nominal / financing.
    Groups whose macro series is missing are omitted; `Growth` of the pieces multiplies to the total."""
    w, m = _prep(curve, macro)
    yrs = _years(w)
    rows = []

    def add(group, comp, growth):
        rows.append((group, comp, growth, growth ** (1 / yrs) - 1 if growth > 0 else -1.0))

    G = w.iloc[-1]
    if m.cpi is not None:
        d = cpi_deflator(m.cpi).iloc[-1]
        add("nominal", "nominal", G); add("nominal", "real", G / d); add("nominal", "inflation", d)
    if m.fx is not None:
        f = m.fx.iloc[-1] / m.fx.iloc[0]
        add("home", "home", G * f); add("home", "local", G); add("home", "fx", f)
    H = _hurdle(m, spread_over_prime)
    if H is not None:
        add("net", "net", G / H.iloc[-1]); add("net", "nominal", G); add("net", "financing", H.iloc[-1])
    cols = ["group", "component", "Growth", "CAGR"]
    return pd.DataFrame(rows, columns=cols).set_index(["group", "component"])
