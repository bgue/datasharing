"""Python SDK: one object, `Lab`, wraps data, screens, benchmark and config.

    from screenlab import Lab, ScreenBuilder, IndexBenchmark

    lab = Lab.synthetic()                                   # or Lab.from_csv(f, p, indices)
    lab.benchmark = IndexBenchmark("SYN_CAPWT", mer=0.0006)
    lab.define(ScreenBuilder("cheap_yield").universe(top=100)
               .where("pe < 15", "div_yield > 0.03").rank(div_yield="desc").take(10))

    lab.picks("cheap_yield")                                # today's names + key ratios
    rep = lab.backtest(["dogs", "cheap_yield"], rebalance_months=6, cost_bps=20)
    print(rep)                                              # formatted comparison table
    rep.table, rep.curves                                   # raw numbers / equity curves
    tr = lab.timing("dogs", stride=5)                       # daily-start timing study
    lab.graham("defensive", max_pe=18)                      # funnel + per-test pass table
    lab.significance(rep)                                   # bootstrap CI, t-stat, deflated Sharpe
    lab.sweep("dogs", {"dogs.n": [5, 10], "cost_bps": [10, 50]}, splits=dict(train_years=5, test_years=1))
    lab.extended(rep, MacroSeries.from_csv("macro.csv"), spread_over_prime=0.01)   # real / FX / financing
    lab.exit_eval(ExitScreen([TrendBreak("SYN_CAPWT"), Breadth()]))              # exit-signal evaluation

Every call takes Config fields as keyword overrides (rebalance_months,
cost_bps, commission, capital, mer, delist_return, min_holdings, cash_rate)
without changing `lab.config`.
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field, replace

import pandas as pd

from . import backtest, data, metrics
from .dataset import CSVBuilder, DataCard, Dataset, SyntheticBuilder
from .benchmarks import (IndexBenchmark, ScreenBenchmark, equal_weight_top, parse_benchmark, passive_index,
                         price_benchmark_message)
from .errors import BenchmarkKindError
from .metrics import PCT_COLS
from .operations import OPERATIONS, _load_builtins, install_lab_methods, lab_method
from .ops.backtest import BacktestReport, _unique_label, format_table   # noqa: F401  (re-exported)
from .screens import REGISTRY, Screen
from .spec import define_screen

@dataclass
class Lab:
    market: data.Market
    config: backtest.Config = field(default_factory=backtest.Config)
    benchmark: IndexBenchmark | ScreenBenchmark | None = None   # primary is always a passive IndexBenchmark
    screens: dict = field(default_factory=lambda: dict(REGISTRY))
    compare: tuple = ()                       # extra comparison benchmarks shown in every backtest
    store: object = None                      # RunStore: records runs and counts trials (None = nothing kept)
    jobs: object = None                       # JobManager for lab.submit (None = a thread pool on first use)
    name: str | None = None                   # dataset name in RunSpecs (the server sets it)
    persist: str = "metrics"                  # persist level when a store is on: none|inputs|metrics|full
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    allow_price_benchmark: bool = False       # True: a price-index benchmark is allowed (flattered excess, with a warning)

    def __post_init__(self):
        self._tl = threading.local()
        if self.store is None and os.environ.get("SCREENLAB_STORE"):
            from .store import SQLiteStore
            self.store = SQLiteStore(os.environ["SCREENLAB_STORE"])
        """Passive index: the explicit IndexBenchmark, else the first loaded index series, else one built
        from the universe (PASSIVE_CAPWT, or PASSIVE_EW without market caps) and registered in
        market.indices. A ScreenBenchmark given here is only an extra comparison."""
        self.compare = tuple(self.compare)
        if self.benchmark is not None and not isinstance(self.benchmark, IndexBenchmark):
            self.compare, self.benchmark = (self.benchmark, *self.compare), None
        if self.benchmark is None:
            self.benchmark = self._passive_default()

    def _passive_default(self) -> IndexBenchmark:
        ind = self.market.indices
        if not len(ind.columns):
            name, series = passive_index(self.market)
            self.market.indices[name] = series
            self.market.dataset = self.market.dataset.with_index(name, series, "total_return")
            return IndexBenchmark(name)
        return IndexBenchmark(ind.columns[0])

    def passive(self, benchmark=None) -> IndexBenchmark:
        """The primary (passive) benchmark: `benchmark` if it is an IndexBenchmark (or an index name),
        else the lab's own, rebuilt if someone assigned a non-index to `lab.benchmark`."""
        benchmark = parse_benchmark(benchmark)       # "topN" is a screen benchmark: never the primary
        if isinstance(benchmark, IndexBenchmark):
            return benchmark
        return self.benchmark if isinstance(self.benchmark, IndexBenchmark) else self._passive_default()

    def benchmark_for(self, benchmark=None, allow_price_benchmark=None, what: str = "this comparison") -> IndexBenchmark:
        """`passive(benchmark)` for an analysis: refuses a price-index benchmark (portfolios are total return,
        so excess return would be overstated by about the dividend yield) with a `BenchmarkKindError` that
        names the fixes, unless `allow_price_benchmark` (default: `lab.allow_price_benchmark`) is True."""
        b = self.passive(benchmark)
        self.check_kind(b.effective_kind(self.market), b.index, allow_price_benchmark, what)
        return b

    def check_extras(self, extras, allow_price_benchmark=None, what: str = "this comparison") -> None:
        """Apply the kind check to every IndexBenchmark among comparison benchmarks (`compare=` rows);
        a ScreenBenchmark is a total-return portfolio and always passes."""
        for x in extras:
            if isinstance(x, IndexBenchmark):
                self.check_kind(x.effective_kind(self.market), x.index, allow_price_benchmark, what)

    def check_target(self, target, allow_price_benchmark=None, what: str = "this analysis") -> None:
        """Apply the kind check to a named target (an index name or ticker); curves and None pass."""
        if isinstance(target, str):
            b = IndexBenchmark(target.strip())
            self.check_kind(b.effective_kind(self.market), b.index, allow_price_benchmark, what)

    def check_kind(self, kind, index, allow_price_benchmark=None, what: str = "this comparison") -> None:
        allow = self.allow_price_benchmark if allow_price_benchmark is None else bool(allow_price_benchmark)
        if kind == "price" and not allow:
            raise BenchmarkKindError(price_benchmark_message(index, what))

    def check_report(self, report, allow_price_benchmark=None, what: str = "this analysis") -> None:
        """Refuse an analysis of a BacktestReport whose benchmark is a price index, unless the report was run
        with `allow_price_benchmark` or the flag is given here (see `benchmark_for`)."""
        card = getattr(report, "data_card", None)
        if card is None or not card.benchmark:
            return
        if allow_price_benchmark is None and getattr(report, "price_benchmark_allowed", False):
            allow_price_benchmark = True
        self.check_kind(card.benchmark_kind, card.benchmark, allow_price_benchmark, what)

    def _extras(self, benchmark, compare) -> list:
        """Comparison benchmarks: lab.compare, a non-index lab.benchmark / `benchmark`, and `compare`."""
        cmp = [compare] if isinstance(compare, (str, dict, IndexBenchmark, ScreenBenchmark)) else list(compare or ())
        raw = [*self.compare, *cmp, *(x for x in (self.benchmark, benchmark) if not isinstance(x, IndexBenchmark))]
        out = {}
        for b in raw:
            if b is None:
                continue
            b = parse_benchmark(b)
            out.setdefault(b.label, b)
        return list(out.values())

    # ---- construction ---------------------------------------------------
    @classmethod
    def synthetic(cls, freq: str = "B", n_stocks: int = 250, years: int = 25, seed: int = 7, **kw) -> "Lab":
        return cls.from_dataset(SyntheticBuilder(n_stocks=n_stocks, years=years, freq=freq, seed=seed).build(), **kw)

    @classmethod
    def from_csv(cls, fundamentals: str, prices: str, indices: str | None = None, **kw) -> "Lab":
        return cls.from_dataset(CSVBuilder(fundamentals, prices, indices).build(), **kw)

    @classmethod
    def from_dataset(cls, dataset: Dataset, max_views: int | None = data.DEFAULT_MAX_VIEWS, **kw) -> "Lab":
        """A Lab on a Dataset (see dataset.py for the builders)."""
        return cls(data.Market(dataset, max_views=max_views), **kw)

    def data_card(self, benchmark=None) -> DataCard:
        """The data card, with the kind and survivorship score taken against `benchmark` (the passive
        index of this lab by default)."""
        b = self.passive(benchmark)
        return self.market.dataset.card_for(b.index, b.dividend_yield, b.kind if b.kind in ("price", "total_return") else None)

    # ---- screens ----------------------------------------------------------
    def define(self, spec) -> Screen:
        """Register a screen from a builder, spec, dict, JSON string/path or Screen."""
        sc = define_screen(spec)
        self.screens[sc.name] = sc
        return sc

    def screen(self, s, **overrides) -> Screen:
        """A Screen from a registered name, a Screen, or an inline spec (dict / builder / JSON text / .json path).
        A plain string that is neither a registered name nor JSON / a path raises KeyError("unknown screen: ...")."""
        if isinstance(s, Screen):
            sc = s
        elif isinstance(s, str) and s in self.screens:
            sc = self.screens[s]
        elif isinstance(s, str) and not (s.lstrip().startswith(("{", "[")) or s.endswith(".json")):
            raise KeyError(f"unknown screen: {s}")
        else:
            sc = define_screen(s)
        return sc.with_overrides(**overrides) if overrides else sc

    def _cfg(self, **kw) -> backtest.Config:
        return replace(self.config, **kw) if kw else self.config

    def _cfg_of(self, base: backtest.Config, overrides: dict) -> backtest.Config:
        return replace(base, **overrides) if overrides else base

    def _asof(self, asof=None) -> pd.Timestamp:
        return self.market.trading_day(asof, "prev") if asof else self.market.index[-1]

    def snapshot(self, asof=None) -> pd.DataFrame:
        return self.market.view(self._asof(asof))

    # ---- analyses: generated from the operations (operations.py, ops/*.py) ---------------------------
    #   lab.picks / backtest / timing / significance / sweep / extended / exit_eval / calibrate_exit / graham,
    #   and any operation registered later: lab.<name>(...) or lab.run("<name>", ...)

    def run(self, name: str, **args):
        """Run the registered operation `name` with its Lab-method keyword arguments (tracked like `lab.<name>`)."""
        try:
            op = OPERATIONS[name]
        except KeyError:
            raise KeyError(f"unknown operation {name!r}; available: {sorted(OPERATIONS)}") from None
        return op.execute(self, **args)

    def __getattr__(self, name):                 # operations registered after the class was built
        if not name.startswith("_") and name in OPERATIONS:
            return lab_method(OPERATIONS[name], name).__get__(self)
        for op in OPERATIONS.values():
            if name in op.method_names:
                return lab_method(op, name).__get__(self)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    # ---- runs, jobs and trials ---------------------------------------------------------------
    def last_trials(self) -> dict | None:
        """{"trials_in_family", "trials_scope", ...} of this thread's latest tracked call (None before any)."""
        return getattr(self._tl, "last", None)

    def submit(self, kind: str, *, label=None, tags=(), force: bool = False, run_seed: int = 0, **args):
        """Start `kind` (a registered operation: "backtest", "timing", "sweep", "significance", "extended",
        "exit_evaluate", "exit_calibrate", "graham", "picks", ...) asynchronously with the Lab method's keyword arguments and
        return a `jobs.Job` (status / progress / cancel / result()). Uses `Lab(jobs=JobManager(...))` when
        given (this lab's `name` must be one of its datasets), else a private thread pool over this lab.
        An identical finished run in the store is returned at once (`job.cache_hit`) unless `force`."""
        from .jobs import JobManager
        from .runspec import RunSpec
        if self.jobs is None:
            self.jobs = JobManager(store=self.store, mode="thread", labs={self.name or "lab": self},
                                   persist=self.persist)
        name = self.name or ("lab" if "lab" in self.jobs.labs else None)
        return self.jobs.submit(RunSpec(kind, name, args, run_seed, label, tuple(tags)), force=force)


_load_builtins()
for _op in list(OPERATIONS.values()):            # the Lab methods of every operation registered so far
    install_lab_methods(Lab, _op)
