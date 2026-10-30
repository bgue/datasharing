"""Multi-portfolio stock screener and backtester for rules-based screen portfolios of the kind
published in The Globe and Mail's "Inside the Market" portfolio columns, plus classic value screens.

Public API (see api.py for a walkthrough):
    Lab                      data + screens + benchmark + config in one object
    ScreenBuilder, ScreenSpec, define_screen, load_screens   screen definitions
    IndexBenchmark, ScreenBenchmark                          benchmarks
    Config, cap_tiered                                       costs / MER / rebalancing (grouped by pipeline stage)
    simulate_paths                                           portfolio + passive index on many return paths (K)
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
    backtest.py    periodic-rebalance simulator (orchestrates pipeline.py's stages)
    pipeline.py    stage protocols (selection, weighting, costs, overlay, accrual) + simulate_paths
    benchmarks.py  buy-and-hold index or screen benchmarks
    timing.py      daily-start rebalance-timing study
    metrics.py     CAGR / volatility / drawdown / hit-rate stats
    construction.py weights, caps, hold rules, blends        stats.py / sweep.py  statistics, sweeps
    macro.py / metrics_ext.py  macro series, extended metrics   exit_signals.py  exit-signal screen
    calibration.py exit-screen calibration;  server/  REST (FastAPI) and MCP servers over Lab
    realdata/      bundled S&P 500 + macro data, Bank of Canada and SEC adapters, ACQUIRE.md
    run.py         CLI: compare screens;   timing.py: CLI for timing studies
"""
try:
    from importlib.metadata import PackageNotFoundError, version as _version
    __version__ = _version("screenlab")
except Exception:                      # not installed (plain checkout on sys.path)
    __version__ = "0.1.0"

from .api import BacktestReport, Lab
from .backtest import Config, cap_tiered
from .pipeline import PathWealth, simulate_paths
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top, passive_index
from .data import Market, synthetic_market
from .screens import DEFENSIVE, ENTERPRISING, GLOBE_PORTFOLIOS, REGISTRY, GrahamTests, Screen
from .spec import ScreenBuilder, ScreenSpec, define_screen, load_screens
from .construction import HoldRule, blend, union_screen
from .exit_signals import (Breadth, Dispersion, DrawdownFromPeak, ExitScreen, MomentumCrash,
                           RateShock, TrendBreak, ValuationStretch, VolRegime, evaluate as evaluate_exit, overlay)
from .exit_policy import ExitPolicy, Portfolio
from .taxes import TaxConfig, PlaceholderCanadianTax, NoTax, register_tax_model
from . import exit_inputs  # registers the additional exit signals into exit_signals.BUILTINS
from .exit_inputs import (CorrelationSpike, CreditSpread, DeathCross, DistanceFromHigh, DownsideDeviation,
                          EarningsBreadth, NewLowsMinusHighs, PortfolioStop, PortfolioVsIndex,
                          VolTermStructure, YieldCurve, YieldSpreadCompression)
from .dataset import (BoCMacro, BundledSP500Builder, CSVBuilder, RealUSBuilder, DataCard, Dataset, DatasetBuilder, SECBuilder,
                      SyntheticBuilder)
from .universe import AllListed, Filtered, Membership, TopN, Universe
from .runspec import RunSpec, code_version, dataset_id
from .store import NullStore, SQLiteStore
from .jobs import Job, JobManager
from .progress import Cancelled

# Heavy, rarely needed modules load on first use (PEP 562): `from screenlab import Grid` still works.
_LAZY = {"macro": ("MacroSeries",),
         "metrics_ext": ("decompose", "extended_summary"),
         "calibration": ("CalibrationResult", "calibrate_exit"),
         "sweep": ("Grid", "random_search", "run_sweep", "walk_forward"),
         "stats": ("bootstrap_excess", "deflated_sharpe", "t_stat_excess")}
_LAZY_NAMES = {n: mod for mod, names in _LAZY.items() for n in names}


def __getattr__(name):
    mod = _LAZY_NAMES.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(f".{mod}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals(), *_LAZY_NAMES})


__all__ = ["__version__", "Lab", "BacktestReport", "Config", "cap_tiered", "simulate_paths", "PathWealth", "IndexBenchmark", "ScreenBenchmark",
           "equal_weight_top", "passive_index", "Market", "synthetic_market", "DEFENSIVE", "ENTERPRISING", "REGISTRY", "GLOBE_PORTFOLIOS",
           "GrahamTests", "Screen", "ScreenBuilder", "ScreenSpec", "define_screen", "load_screens",
           "HoldRule", "blend", "union_screen", "MacroSeries", "decompose", "extended_summary",
           "ExitScreen", "TrendBreak", "Breadth", "VolRegime", "DrawdownFromPeak", "ValuationStretch",
           "MomentumCrash", "RateShock", "Dispersion", "evaluate_exit", "overlay",
           "CalibrationResult", "calibrate_exit", "ExitPolicy", "Portfolio",
           "TaxConfig", "PlaceholderCanadianTax", "NoTax", "register_tax_model",
           "CorrelationSpike", "CreditSpread", "DeathCross", "DistanceFromHigh", "DownsideDeviation",
           "EarningsBreadth", "NewLowsMinusHighs", "PortfolioStop", "PortfolioVsIndex",
           "VolTermStructure", "YieldCurve", "YieldSpreadCompression",
           "Grid", "random_search", "run_sweep", "walk_forward",
           "bootstrap_excess", "deflated_sharpe", "t_stat_excess", "RunSpec", "code_version", "dataset_id",
           "Dataset", "DataCard", "DatasetBuilder", "SyntheticBuilder", "BundledSP500Builder", "RealUSBuilder", "CSVBuilder",
           "SECBuilder", "BoCMacro", "Universe", "AllListed", "TopN", "Membership", "Filtered",
           "NullStore", "SQLiteStore", "Job", "JobManager", "Cancelled"]
