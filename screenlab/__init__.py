"""Multi-portfolio stock screener and backtester for rules-based screen portfolios of the kind
published in The Globe and Mail's "Inside the Market" portfolio columns, plus classic value screens.

Public API (see api.py for a walkthrough):
    Lab                      data + screens + benchmark + config in one object
    ScreenBuilder, ScreenSpec, define_screen, load_screens   screen definitions
    IndexBenchmark, ScreenBenchmark                          benchmarks
    Config, cap_tiered                                       costs / MER / rebalancing
    GrahamTests, DEFENSIVE, ENTERPRISING                     Graham criteria
    HoldRule, blend, union_screen                            construction research
    MacroSeries, extended_summary, decompose                 real / FX / financing metrics
    ExitScreen + signals, evaluate_exit, overlay   exit-signal screen (own daily clock)
    ExitPolicy, Portfolio                                    where exited money goes, when/how it re-enters
    exit_inputs.*                                            twelve more exit signals (price, portfolio, fundamentals, macro)
    calibrate_exit                                           walk-forward calibration of an exit screen + policy
    Grid, run_sweep, walk_forward, bootstrap_excess, ...     sweeps and statistics

Modules:
    data.py        point-in-time Market (+ synthetic market for testing)
    screens.py     built-in screens and Graham tests
    spec.py        declarative screen definitions (builder / dict / JSON)
    backtest.py    periodic-rebalance, equal-weight simulator
    benchmarks.py  buy-and-hold index or screen benchmarks
    timing.py      daily-start rebalance-timing study
    metrics.py     CAGR / volatility / drawdown / hit-rate stats
    construction.py weights, caps, hold rules, blends        stats.py / sweep.py  statistics, sweeps
    macro.py / metrics_ext.py  macro series, extended metrics   exit_signals.py  exit-signal screen
    calibration.py exit-screen calibration;  server/  REST (FastAPI) and MCP servers over Lab
    realdata/      bundled S&P 500 + macro data, Bank of Canada and SEC adapters, ACQUIRE.md
    run.py         CLI: compare screens;   timing.py: CLI for timing studies
"""
from .api import BacktestReport, Lab
from .backtest import Config, cap_tiered
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top, passive_index
from .data import Market, synthetic_market
from .screens import DEFENSIVE, ENTERPRISING, REGISTRY, GrahamTests, Screen
from .spec import ScreenBuilder, ScreenSpec, define_screen, load_screens
from .construction import HoldRule, blend, union_screen
from .macro import MacroSeries
from .metrics_ext import decompose, extended_summary
from .exit_signals import (Breadth, Dispersion, DrawdownFromPeak, ExitScreen, MomentumCrash,
                           RateShock, TrendBreak, ValuationStretch, VolRegime, evaluate as evaluate_exit, overlay)
from .calibration import CalibrationResult, calibrate_exit
from .exit_policy import ExitPolicy, Portfolio
from . import exit_inputs  # registers the additional exit signals into exit_signals.BUILTINS
from .exit_inputs import (CorrelationSpike, CreditSpread, DeathCross, DistanceFromHigh, DownsideDeviation,
                          EarningsBreadth, NewLowsMinusHighs, PortfolioStop, PortfolioVsIndex,
                          VolTermStructure, YieldCurve, YieldSpreadCompression)
from .sweep import Grid, random_search, run_sweep, walk_forward
from .stats import bootstrap_excess, deflated_sharpe, t_stat_excess
from .runspec import RunSpec, code_version, dataset_id
from .store import NullStore, SQLiteStore
from .jobs import Job, JobManager
from .progress import Cancelled

__all__ = ["Lab", "BacktestReport", "Config", "cap_tiered", "IndexBenchmark", "ScreenBenchmark",
           "equal_weight_top", "passive_index", "Market", "synthetic_market", "DEFENSIVE", "ENTERPRISING", "REGISTRY",
           "GrahamTests", "Screen", "ScreenBuilder", "ScreenSpec", "define_screen", "load_screens",
           "HoldRule", "blend", "union_screen", "MacroSeries", "decompose", "extended_summary",
           "ExitScreen", "TrendBreak", "Breadth", "VolRegime", "DrawdownFromPeak", "ValuationStretch",
           "MomentumCrash", "RateShock", "Dispersion", "evaluate_exit", "overlay",
           "CalibrationResult", "calibrate_exit", "ExitPolicy", "Portfolio",
           "CorrelationSpike", "CreditSpread", "DeathCross", "DistanceFromHigh", "DownsideDeviation",
           "EarningsBreadth", "NewLowsMinusHighs", "PortfolioStop", "PortfolioVsIndex",
           "VolTermStructure", "YieldCurve", "YieldSpreadCompression",
           "Grid", "random_search", "run_sweep", "walk_forward",
           "bootstrap_excess", "deflated_sharpe", "t_stat_excess", "RunSpec", "code_version", "dataset_id",
           "NullStore", "SQLiteStore", "Job", "JobManager", "Cancelled"]
