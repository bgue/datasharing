"""Multi-portfolio stock screener and backtester modelled on the screens
Norman Rothery writes up in The Globe and Mail (and on StingyInvestor.com).

Public API (see api.py for a walkthrough):
    Lab                      data + screens + benchmark + config in one object
    ScreenBuilder, ScreenSpec, define_screen, load_screens   screen definitions
    IndexBenchmark, ScreenBenchmark                          benchmarks
    Config, cap_tiered                                       costs / MER / rebalancing
    GrahamTests, DEFENSIVE, ENTERPRISING                     Graham criteria
    HoldRule, blend, union_screen                            construction research
    MacroSeries, extended_summary, decompose                 real / FX / financing metrics
    ExitScreen + signals, evaluate_exit, overlay, ExitOverlay   exit-signal screen (own daily clock)
    calibrate_exit                                           walk-forward calibration of an exit screen
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
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top
from .data import Market, synthetic_market
from .screens import DEFENSIVE, ENTERPRISING, REGISTRY, GrahamTests, Screen
from .spec import ScreenBuilder, ScreenSpec, define_screen, load_screens
from .construction import HoldRule, blend, union_screen
from .macro import MacroSeries
from .metrics_ext import decompose, extended_summary
from .exit_signals import (Breadth, Dispersion, DrawdownFromPeak, ExitOverlay, ExitScreen, MomentumCrash,
                           RateShock, TrendBreak, ValuationStretch, VolRegime, evaluate as evaluate_exit, overlay)
from .calibration import CalibrationResult, calibrate_exit
from .sweep import Grid, random_search, run_sweep, walk_forward
from .stats import bootstrap_excess, deflated_sharpe, t_stat_excess

__all__ = ["Lab", "BacktestReport", "Config", "cap_tiered", "IndexBenchmark", "ScreenBenchmark",
           "equal_weight_top", "Market", "synthetic_market", "DEFENSIVE", "ENTERPRISING", "REGISTRY",
           "GrahamTests", "Screen", "ScreenBuilder", "ScreenSpec", "define_screen", "load_screens",
           "HoldRule", "blend", "union_screen", "MacroSeries", "decompose", "extended_summary",
           "ExitScreen", "TrendBreak", "Breadth", "VolRegime", "DrawdownFromPeak", "ValuationStretch",
           "MomentumCrash", "RateShock", "Dispersion", "evaluate_exit", "overlay", "ExitOverlay",
           "CalibrationResult", "calibrate_exit",
           "Grid", "random_search", "run_sweep", "walk_forward",
           "bootstrap_excess", "deflated_sharpe", "t_stat_excess"]
