"""Statistics for judging a backtest or a sweep: is the edge real, or luck and selection?

Pure functions over pandas / numpy; no scipy. Return series are *periodic* simple
returns of any frequency (daily, monthly, ...); pass `curves=True` to hand in equity
curves instead (the benchmark is then padded onto the portfolio's dates, as metrics.py does).
"""
from __future__ import annotations

from math import e, floor, sqrt
from statistics import NormalDist
from typing import Sequence

import numpy as np
import pandas as pd

from .metrics import M

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def periods_per_year(index: pd.DatetimeIndex) -> float:
    """Observations per year, from the calendar span (same convention as metrics.summarize)."""
    yrs = (index[-1] - index[0]).days / 365.25
    return (len(index) - 1) / yrs


def excess_returns(port: pd.Series, bench: pd.Series, curves: bool = False) -> pd.Series:
    """Paired periodic excess return port - bench on the portfolio's dates."""
    if curves:
        p = port.dropna()
        b = bench.reindex(p.index, method="pad")
        d = (p.pct_change() - b.pct_change()).dropna()
    else:
        d = (port - bench.reindex(port.index)).dropna()
    if len(d) < 3:
        raise ValueError("need at least 3 paired observations")
    return d


# ------------------------------------------------------------------ bootstrap
def bootstrap_excess(port: pd.Series, bench: pd.Series, block_months: float = 12, n: int = 2000,
                     seed: int = 0, curves: bool = False) -> dict:
    """Stationary block bootstrap of the paired excess return (port - bench).

    Resamples blocks of geometric length (mean `block_months`, converted to periods at the
    inferred frequency) so autocorrelation in the excess survives. Returns a dict:
    mean (annualized arithmetic excess), ci_low / ci_high (5th / 95th percentile of the
    bootstrap means), p_positive (share of resampled means > 0), periods_per_year, block.
    """
    d = excess_returns(port, bench, curves)
    x = d.to_numpy(float)
    T = len(x)
    ppy = periods_per_year(d.index)
    block = max(1.0, block_months / 12.0 * ppy)
    p_new = 1.0 / block
    rng = np.random.default_rng(seed)
    idx = np.empty((n, T), dtype=np.int64)
    idx[:, 0] = rng.integers(0, T, n)
    restart = rng.random((n, T)) < p_new
    fresh = rng.integers(0, T, (n, T))
    for t in range(1, T):                                  # circular: continue the block or jump
        idx[:, t] = np.where(restart[:, t], fresh[:, t], (idx[:, t - 1] + 1) % T)
    means = x[idx].mean(axis=1) * ppy
    return {"mean": float(x.mean() * ppy),
            "ci_low": float(np.percentile(means, 5)), "ci_high": float(np.percentile(means, 95)),
            "p_positive": float((means > 0).mean()), "periods_per_year": ppy, "block": block}


def t_stat_excess(port: pd.Series, bench: pd.Series, lags: int | None = None,
                  curves: bool = False) -> float:
    """Newey-West (Bartlett kernel) t-statistic of the mean paired excess return.

    Default lag is floor(4 (T/100)^(2/9)); lags=0 gives the plain heteroskedastic-free t.
    """
    x = excess_returns(port, bench, curves).to_numpy(float)
    T = len(x)
    L = floor(4 * (T / 100) ** (2 / 9)) if lags is None else int(lags)
    L = min(L, T - 1)
    m = x.mean()
    u = x - m
    s = u @ u / T
    for l in range(1, L + 1):
        s += 2 * (1 - l / (L + 1)) * (u[l:] @ u[:-l]) / T
    if s <= 1e-24:
        return float("nan") if abs(m) < 1e-15 else float(np.sign(m) * np.inf)
    return float(m / sqrt(s / T))


# ------------------------------------------------------------ Sharpe deflation
def probabilistic_sharpe(sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0,
                         benchmark: float = 0.0) -> float:
    """PSR: P(true per-period Sharpe > benchmark) given n_obs, skew and (non-excess) kurtosis."""
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if n_obs < 2 or denom <= 0 or not np.isfinite(sr):
        return float("nan")
    return _N.cdf((sr - benchmark) * sqrt(n_obs - 1) / sqrt(denom))


def expected_max_sharpe(var_sr: float, n_trials: int) -> float:
    """Expected best per-period Sharpe among n_trials unskilled trials (Bailey & Lopez de Prado)."""
    if n_trials < 2:
        return 0.0
    g = EULER_GAMMA
    return sqrt(var_sr) * ((1 - g) * _N.inv_cdf(1 - 1 / n_trials)
                           + g * _N.inv_cdf(1 - 1 / (n_trials * e)))


def deflated_sharpe(sharpes: Sequence[float], n_obs: int, skew: float = 0.0, kurt: float = 3.0,
                    periods_per_year: float = 1.0, n_trials: int | None = None) -> float:
    """Deflated Sharpe Ratio of the best of the trials: the probability its edge is real.

    `sharpes` are the Sharpe ratios of every trial tried; the best one is tested against the
    Sharpe you would expect from the luckiest of `n_trials` (default len(sharpes)) skill-less
    trials, using their cross-trial variance. Sharpes may be annualized: pass `periods_per_year`
    to convert to the per-period units that `n_obs` (number of returns), `skew` and `kurt` of the
    best trial refer to. With one trial this is the ordinary PSR against zero.
    """
    s = np.asarray(sharpes, float) / sqrt(periods_per_year)
    N = n_trials or len(s)
    var = float(np.var(s, ddof=1)) if len(s) > 1 else 0.0
    sr0 = expected_max_sharpe(var, N)
    return probabilistic_sharpe(float(np.nanmax(s)), n_obs, skew, kurt, sr0)


def summarize_trials(table: pd.DataFrame, n_obs: int | None = None, periods_per_year: float | None = None,
                     sharpe_col: str = M.SHARPE, n_trials: int | None = None) -> pd.DataFrame:
    """Copy of a sweep/backtest table with DSR and Bonferroni-style haircut columns.

    Uses columns `Obs`, `PerYr`, `Skew`, `Kurt` when present (run_sweep writes them), else the
    n_obs / periods_per_year arguments (required) and normal skew/kurtosis. Every row's Sharpe is
    deflated against the same benchmark: the expected best of the table's rows. Added columns:
    DSR, BonfP (one-sided p-value of Sharpe > 0, times the number of trials, capped at 1) and
    Haircut (share of the Sharpe's z-score lost to that correction; 1 = nothing survives).
    """
    out = table.copy()
    sr = out[sharpe_col].astype(float)
    obs = out[M.OBS] if M.OBS in out else pd.Series(n_obs, index=out.index)
    ppy = out[M.PERYR] if M.PERYR in out else pd.Series(periods_per_year, index=out.index)
    if obs.isna().any() or ppy.isna().any():
        raise ValueError("give n_obs and periods_per_year, or columns Obs and PerYr")
    skew = out["Skew"] if "Skew" in out else pd.Series(0.0, index=out.index)
    kurt = out["Kurt"] if "Kurt" in out else pd.Series(3.0, index=out.index)
    ok = sr.dropna()
    N = n_trials or len(ok)
    per = sr / np.sqrt(ppy)
    var = float(per.dropna().var(ddof=1)) if len(ok) > 1 else 0.0
    sr0 = expected_max_sharpe(var, N)
    dsr, bonf, hair = [], [], []
    for i in out.index:
        if not np.isfinite(sr[i]):
            dsr.append(np.nan); bonf.append(np.nan); hair.append(np.nan)
            continue
        dsr.append(probabilistic_sharpe(per[i], int(obs[i]), skew[i], kurt[i], sr0))
        p = 1 - probabilistic_sharpe(per[i], int(obs[i]), skew[i], kurt[i], 0.0)
        pa = min(1.0, N * p)
        bonf.append(pa)
        z, za = _N.inv_cdf(min(max(1 - p, 1e-12), 1 - 1e-12)), _N.inv_cdf(min(max(1 - pa, 1e-12), 1 - 1e-12))
        hair.append(1.0 if z <= 0 else min(1.0, max(0.0, 1 - za / z)))
    out[M.DSR], out[M.BONFP], out[M.HAIRCUT] = dsr, bonf, hair
    return out
