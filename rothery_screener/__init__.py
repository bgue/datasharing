"""Multi-portfolio stock screener and backtester modelled on the screens
Norman Rothery writes up in The Globe and Mail (and on StingyInvestor.com).

Public API (see api.py for a walkthrough):
    Lab                      data + screens + benchmark + config in one object
    ScreenBuilder, ScreenSpec, define_screen, load_screens   screen definitions
    IndexBenchmark, ScreenBenchmark                          benchmarks
    Config, cap_tiered                                       costs / MER / rebalancing
    GrahamTests, DEFENSIVE, ENTERPRISING                     Graham criteria

Modules:
    data.py        point-in-time Market (+ synthetic market for testing)
    screens.py     built-in screens and Graham tests
    spec.py        declarative screen definitions (builder / dict / JSON)
    backtest.py    periodic-rebalance, equal-weight simulator
    benchmarks.py  buy-and-hold index or screen benchmarks
    timing.py      daily-start rebalance-timing study
    metrics.py     CAGR / volatility / drawdown / hit-rate stats
    run.py         CLI: compare screens;   timing.py: CLI for timing studies
"""
from .api import BacktestReport, Lab
from .backtest import Config, cap_tiered
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top
from .data import Market, synthetic_market
from .screens import DEFENSIVE, ENTERPRISING, REGISTRY, GrahamTests, Screen
from .spec import ScreenBuilder, ScreenSpec, define_screen, load_screens

__all__ = ["Lab", "BacktestReport", "Config", "cap_tiered", "IndexBenchmark", "ScreenBenchmark",
           "equal_weight_top", "Market", "synthetic_market", "DEFENSIVE", "ENTERPRISING", "REGISTRY",
           "GrahamTests", "Screen", "ScreenBuilder", "ScreenSpec", "define_screen", "load_screens"]
