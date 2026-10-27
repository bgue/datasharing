"""Equity-curve statistics and the metric registry.

`summarize` computes the standard statistics of a curve. `METRICS` is the one place that says what every
metric name means: name -> `Metric(label, unit, direction, format, description)`. Code refers to names through
the constants of `M` (`M.EXCESS_YR`), tables are formatted from `format`, and the LinkML `StandardMetric`
enum in schema/runstore.linkml.yaml is generated from this registry (`python -m screenlab.metrics --linkml`,
and tests/test_schema.py fails if the two drift).

Units: "fraction/yr" and "fraction" are decimals (0.10 = 10%), "ratio", "probability", "count", "count/yr",
"as measured" (distribution statistics of another metric). Direction: "higher" or "lower" is better, None =
neutral. Format: "pct" shows `0.123` as `12.3%`, "num" as `0.12`.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd


class Metric(NamedTuple):
    label: str
    unit: str
    direction: str | None
    format: str
    description: str


METRICS: dict = {}


def _m(name, label, unit, direction, fmt, description):
    METRICS[name] = Metric(label, unit, direction, fmt, description)


_m('CAGR', 'CAGR', 'fraction/yr', 'higher', 'pct',
   'Compound annual growth rate of the equity curve (fraction per year; 0.10 = 10%/yr).'),
_m('Vol', 'Volatility', 'fraction/yr', 'lower', 'pct',
   'Annualised standard deviation of the periodic returns (fraction per year).'),
_m('Sharpe', 'Sharpe ratio', 'ratio', 'higher', 'num',
   'Annualised mean return divided by annualised volatility, risk-free rate 0 (ratio, unitless).'),
_m('MaxDD', 'Max drawdown', 'fraction', 'higher', 'pct',
   'Maximum drawdown, the worst peak-to-trough fall of the curve (fraction, negative; -0.47 = -47%).'),
_m('Worst12m', 'Worst 12 months', 'fraction', 'higher', 'pct',
   'Worst rolling 12-month return (fraction, negative if a loss).'),
_m('BeatBenchYrs', 'Years beating benchmark', 'fraction', 'higher', 'pct',
   'Share of calendar years in which the portfolio beat the passive index (fraction 0 to 1; 0.6 = 6 years in 10).'),
_m('Excess/yr', 'Excess return per year', 'fraction/yr', 'higher', 'pct',
   "Portfolio CAGR minus the passive index's CAGR (fraction per year). In a significance run, the mean annualised arithmetic excess return instead."),
_m('AvgN', 'Average holdings', 'count', None, 'num',
   'Average number of holdings (count). Empty for the index.'),
_m('Turnover', 'Turnover per rebalance', 'fraction', 'lower', 'pct',
   'Average one-way turnover per rebalance (fraction of the portfolio traded; 0.3 = 30% bought and sold).'),
_m('Costs/yr', 'Costs per year', 'fraction/yr', 'lower', 'pct',
   'Trading costs lost per year (fraction of portfolio value per year, from cost_bps and commission).'),
_m('TimeOut', 'Time out of the market', 'fraction', None, 'pct',
   'Share of days with less than full market exposure because an exit policy was active (fraction 0 to 1). Only for rows with an exit policy.'),
_m('Switches/yr', 'Exposure switches per year', 'count/yr', 'lower', 'num',
   'Number of exposure changes (exit/re-entry) per year (count per year). Only for rows with an exit policy.'),
_m('CI5', 'Excess 5th percentile', 'fraction/yr', 'higher', 'num',
   'Significance run; 5th percentile of the bootstrapped annual excess return (fraction per year). If it is above 0 the excess is unlikely to be luck.'),
_m('CI95', 'Excess 95th percentile', 'fraction/yr', 'higher', 'num',
   'Significance run; 95th percentile of the bootstrapped annual excess return (fraction per year).'),
_m('P(>0)', 'P(excess > 0)', 'probability', 'higher', 'num',
   'Significance run; share of bootstrap samples with positive excess return (probability 0 to 1).'),
_m('t', 't-statistic', 'ratio', 'higher', 'num',
   'Significance run; Newey-West t-statistic of excess return (unitless; beyond about 2 is conventionally significant).'),
_m('DSR', 'Deflated Sharpe ratio', 'probability', 'higher', 'num',
   'Deflated Sharpe ratio, the probability (0 to 1) that the true Sharpe exceeds what the luckiest of the trials in the family would show by chance. Falls as trials increase.'),
_m('BonfP', 'Bonferroni p-value', 'probability', 'lower', 'num',
   'Bonferroni-adjusted one-sided p-value of Sharpe > 0, times the number of trials, capped at 1.'),
_m('Haircut', 'Sharpe haircut', 'fraction', 'lower', 'num',
   "Share of the Sharpe's z-score lost to the multiple-testing correction (0 to 1; 1 means nothing survives)."),
_m('Calmar', 'Calmar ratio', 'ratio', 'higher', 'num',
   'Exit calibration; CAGR divided by the absolute maximum drawdown of the overlay curve (ratio).'),
_m('Obs', 'Observations', 'count', None, 'num',
   'Sweep table; number of return observations behind the Sharpe (count).'),
_m('PerYr', 'Periods per year', 'count', None, 'num',
   'Sweep table; return periods per year (count).'),
_m('mean', 'Mean', 'as measured', None, 'num',
   'Timing study distribution; average across start offsets of the measure named in row_label.'),
_m('std', 'Standard deviation', 'as measured', None, 'num',
   'Timing study distribution; standard deviation across start offsets.'),
_m('min', 'Minimum', 'as measured', None, 'num',
   'Timing study distribution; smallest value across start offsets.'),
_m('5%', '5th percentile', 'as measured', None, 'num',
   'Timing study distribution; 5th percentile across start offsets.'),
_m('25%', '25th percentile', 'as measured', None, 'num',
   'Timing study distribution; 25th percentile.'),
_m('50%', 'Median', 'as measured', None, 'num',
   'Timing study distribution; median.'),
_m('75%', '75th percentile', 'as measured', None, 'num',
   'Timing study distribution; 75th percentile.'),
_m('95%', '95th percentile', 'as measured', None, 'num',
   'Timing study distribution; 95th percentile across start offsets.'),
_m('max', 'Maximum', 'as measured', None, 'num',
   'Timing study distribution; largest value across start offsets.'),
_m('range', 'Range', 'as measured', None, 'num',
   'Timing study distribution; max minus min, the spread caused only by when you started.'),

# Monte Carlo (ops/montecarlo.py): distributions over resampled paths, per portfolio and horizon
for _q in (5, 25, 50, 75, 95):
    _m(f'TermWealth_p{_q}', f'Terminal wealth p{_q}', 'currency', 'higher', 'num',
       f'Monte Carlo; {_q}th percentile of terminal wealth across paths at the horizon (currency units, includes any contributions and withdrawals).')
for _q in (5, 25, 50, 75, 95):
    _m(f'CAGR_p{_q}', f'CAGR p{_q}', 'fraction/yr', 'higher', 'pct',
       f'Monte Carlo; {_q}th percentile of the compound annual growth rate across paths at the horizon (fraction per year, time-weighted: without contributions and withdrawals).')
_m('P(trail)', 'P(trail the index)', 'probability', 'lower', 'pct',
   'Monte Carlo; share of paths on which the portfolio ends the horizon behind the passive index run on the same path (probability 0 to 1).')
_m('ExcessCAGR_med', 'Median excess CAGR', 'fraction/yr', 'higher', 'pct',
   'Monte Carlo; median across paths of portfolio CAGR minus the passive index CAGR on the same path (fraction per year).')
_m('P(MaxDD<-30%)', 'P(max drawdown worse than 30%)', 'probability', 'lower', 'pct',
   'Monte Carlo; share of paths whose worst peak-to-trough fall within the horizon exceeds 30% (probability 0 to 1).')
_m('P(MaxDD<-50%)', 'P(max drawdown worse than 50%)', 'probability', 'lower', 'pct',
   'Monte Carlo; share of paths whose worst peak-to-trough fall within the horizon exceeds 50% (probability 0 to 1).')
_m('RecoverYrs_med', 'Median years to recover', 'years', 'lower', 'num',
   'Monte Carlo; median across paths of the years from the trough of the worst drawdown back to its previous peak; empty when more than half of the paths had not recovered by the end of the horizon.')
_m('P(unrecovered)', 'P(worst drawdown not recovered)', 'probability', 'lower', 'pct',
   'Monte Carlo; share of paths that had not regained the peak before their worst drawdown by the end of the horizon (probability 0 to 1).')
_m('P(shortfall)', 'P(shortfall vs target)', 'probability', 'lower', 'pct',
   'Monte Carlo; share of paths whose terminal wealth is below the target wealth (probability 0 to 1). Empty without a target.')


class M:
    """Metric names as constants: `M.CAGR == "CAGR"`, `M.EXCESS_YR == "Excess/yr"`."""
    CAGR = 'CAGR'
    VOL = 'Vol'
    SHARPE = 'Sharpe'
    MAXDD = 'MaxDD'
    WORST12M = 'Worst12m'
    BEATBENCHYRS = 'BeatBenchYrs'
    EXCESS_YR = 'Excess/yr'
    AVGN = 'AvgN'
    TURNOVER = 'Turnover'
    COSTS_YR = 'Costs/yr'
    TIMEOUT = 'TimeOut'
    SWITCHES_YR = 'Switches/yr'
    CI5 = 'CI5'
    CI95 = 'CI95'
    P_POSITIVE = 'P(>0)'
    T_STAT = 't'
    DSR = 'DSR'
    BONFP = 'BonfP'
    HAIRCUT = 'Haircut'
    OBS = 'Obs'
    PERYR = 'PerYr'
    CALMAR = 'Calmar'
    DIST_MEAN = 'mean'
    DIST_STD = 'std'
    DIST_MIN = 'min'
    DIST_P5 = '5%'
    DIST_P25 = '25%'
    DIST_P50 = '50%'
    DIST_P75 = '75%'
    DIST_P95 = '95%'
    DIST_MAX = 'max'
    DIST_RANGE = 'range'
    MC_TERMW = 'TermWealth'          # + '_p5' ... '_p95'
    MC_CAGR = 'CAGR'                 # + '_p5' ... '_p95'
    MC_P_TRAIL = 'P(trail)'
    MC_EXCESS_MED = 'ExcessCAGR_med'
    MC_RECOVER_YRS = 'RecoverYrs_med'
    MC_P_UNRECOVERED = 'P(unrecovered)'
    MC_P_SHORTFALL = 'P(shortfall)'

    @staticmethod
    def mc_pdd(level: float) -> str:
        """'P(MaxDD<-30%)' for level 0.30."""
        return f'P(MaxDD<-{round(level * 100):d}%)'


# the standard table columns shown as percentages (everything else prints with two decimals)
PCT_COLS = [n for n in (M.CAGR, M.VOL, M.MAXDD, M.WORST12M, M.BEATBENCHYRS, M.EXCESS_YR, M.TURNOVER,
                        M.COSTS_YR, M.TIMEOUT) if METRICS[n].format == "pct"]


def fmt(name: str, x) -> str:
    """`x` formatted per the metric's registered format ("" for NaN); unregistered names print with two decimals."""
    if x is None or pd.isna(x):
        return ""
    return f"{x:.1%}" if name in METRICS and METRICS[name].format == "pct" else f"{x:.2f}"


def linkml_enum_values() -> dict:
    """`{name: {"description": ...}}`: the permissible values of StandardMetric."""
    return {n: {"description": m.description} for n, m in METRICS.items()}


def summarize(curve: pd.Series, bench: pd.Series | None = None, rf: float = 0.0) -> dict:
    """Stats for an equity curve of any frequency (value series, any starting level)."""
    w = curve.dropna()
    w = w / w.iloc[0]
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    ppy = (len(w) - 1) / yrs
    r = w.pct_change().dropna()
    year_ago = w.reindex(w.index - pd.Timedelta(days=365), method="pad").to_numpy()
    roll12 = w.to_numpy() / year_ago - 1
    out = {
        M.CAGR: w.iloc[-1] ** (1 / yrs) - 1,
        M.VOL: r.std() * np.sqrt(ppy),
        M.SHARPE: (r.mean() * ppy - rf) / (r.std() * np.sqrt(ppy)) if r.std() > 0 else np.nan,
        M.MAXDD: (w / w.cummax() - 1).min(),
        M.WORST12M: np.nanmin(roll12) if np.isfinite(roll12).any() else np.nan,
    }
    if bench is not None:
        b = bench.reindex(w.index, method="pad")
        b = b / b.iloc[0]
        yearly = lambda x: x.groupby(x.index.year).last().pipe(lambda y: y / y.shift(1).fillna(1.0) - 1)
        out[M.BEATBENCHYRS] = (yearly(w) > yearly(b)).mean()
        out[M.EXCESS_YR] = out[M.CAGR] - (b.iloc[-1] ** (1 / yrs) - 1)
    return out


if __name__ == "__main__":
    import sys
    if "--linkml" in sys.argv:
        import yaml
        print(yaml.safe_dump({"permissible_values": linkml_enum_values()}, sort_keys=False, width=100))
    else:
        print(pd.DataFrame({n: m._asdict() for n, m in METRICS.items()}).T.to_string())
