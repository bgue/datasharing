"""Python SDK: one object, `Lab`, wraps data, screens, benchmark and config.

    from rothery_screener import Lab, ScreenBuilder, IndexBenchmark

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
from . import progress as _progress
from .tracking import grid_points, note_points, tracked
from .exit_policy import ExitPolicy, Portfolio, as_policy, policy_overlay
from .benchmarks import IndexBenchmark, ScreenBenchmark, equal_weight_top, parse_benchmark, passive_index
from .screens import REGISTRY, Screen, graham_funnel, graham_report
from .spec import define_screen, graham_tests

PCT_COLS = ["CAGR", "Vol", "MaxDD", "Worst12m", "BeatBenchYrs", "Excess/yr", "Turnover", "Costs/yr", "TimeOut"]


def format_table(tbl: pd.DataFrame) -> str:
    out = tbl.copy().astype(object)
    for c in tbl.columns:
        out[c] = tbl[c].map(lambda x: "" if pd.isna(x) else (f"{x:.1%}" if c in PCT_COLS else f"{x:.2f}"))
    return out.to_string()


def _unique_label(label: str, used: set, reserved: str, suffix: str | None) -> str:
    """Row label that collides with nothing so far: `dogs`, then `dogs#2`, `dogs#3`, ... (also avoids the
    benchmark's label and, with `suffix` ("+exit"), the suffixed variant). Records the labels it takes."""
    def taken(x):
        return x == reserved or x in used or (suffix is not None and x + suffix in used)
    out, k = label, 1
    while taken(out):
        k += 1
        out = f"{label}#{k}"
    used.add(out)
    if suffix is not None:
        used.add(out + suffix)
    return out


@dataclass
class BacktestReport:
    table: pd.DataFrame                       # one row per screen + the benchmark
    results: dict                             # name -> backtest.Result
    benchmark: pd.Series                      # the PASSIVE index equity curve (primary benchmark)
    benchmark_label: str
    config: backtest.Config
    compare: dict = field(default_factory=dict)   # label -> extra comparison curve (screen benchmarks etc.)

    @property
    def curves(self) -> pd.DataFrame:
        c = {k: r.curve for k, r in self.results.items()}
        return pd.DataFrame({self.benchmark_label: self.benchmark, **self.compare, **c})

    def __str__(self):
        span = f"{self.benchmark.index[0]:%Y-%m-%d} to {self.benchmark.index[-1]:%Y-%m-%d}"
        head = (f"Backtest {span}, rebalance every {self.config.rebalance_months}m, "
                f"benchmark {self.benchmark_label}\n")
        return head + "\n" + format_table(self.table.sort_values("CAGR", ascending=False))


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

    def __post_init__(self):
        self._tl = threading.local()
        if self.store is None and os.environ.get("ROTHERY_STORE"):
            from .store import SQLiteStore
            self.store = SQLiteStore(os.environ["ROTHERY_STORE"])
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
            return IndexBenchmark(name)
        return IndexBenchmark(ind.columns[0])

    def passive(self, benchmark=None) -> IndexBenchmark:
        """The primary (passive) benchmark: `benchmark` if it is an IndexBenchmark (or an index name),
        else the lab's own, rebuilt if someone assigned a non-index to `lab.benchmark`."""
        benchmark = parse_benchmark(benchmark)       # "topN" is a screen benchmark: never the primary
        if isinstance(benchmark, IndexBenchmark):
            return benchmark
        return self.benchmark if isinstance(self.benchmark, IndexBenchmark) else self._passive_default()

    def _extras(self, benchmark, compare) -> list:
        """Comparison benchmarks: lab.compare, a non-index lab.benchmark / `benchmark`, and `compare`."""
        cmp = [compare] if isinstance(compare, (str, IndexBenchmark, ScreenBenchmark)) else list(compare or ())
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
        fund, prices, indices = data.synthetic_market(n_stocks=n_stocks, years=years, freq=freq, seed=seed)
        return cls(data.Market(fund, prices, indices), **kw)

    @classmethod
    def from_csv(cls, fundamentals: str, prices: str, indices: str | None = None, **kw) -> "Lab":
        return cls(data.Market(*data.load_csv(fundamentals, prices, indices)), **kw)

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

    @tracked("picks")
    def picks(self, screen, asof=None, columns=("sector", "market_cap", "pe", "pb", "div_yield",
                                                  "f_score", "mom_12_1")) -> pd.DataFrame:
        s = self.snapshot(asof)
        names = self.market.picks(self.screen(screen), self._asof(asof))
        return s.loc[names, [c for c in columns if c in s]]

    # ---- analysis ---------------------------------------------------------
    @tracked("backtest")
    def backtest(self, screens=None, start=None, end=None, benchmark=None, exit=None,
                 with_and_without: bool = False, compare=(), **cfg) -> BacktestReport:
        """Backtest screens (names, Screens or `Portfolio(name, screen, exit=None, **config_overrides)`).

        `exit`: an ExitPolicy (or ExitScreen, wrapped in a default policy) applied to every entry in the
        call that has no policy of its own. `with_and_without=True` runs each such entry twice and labels
        the rows `name` and `name+exit`.
        Row labels are unique: a repeated name (two entries called "dogs", or a Portfolio named like a
        screen or like the benchmark) is kept and numbered in order of appearance, `dogs`, `dogs#2`, `dogs#3`.
        Rows with a policy also report TimeOut and Switches/yr.

        The primary benchmark (`report.benchmark`, Excess/yr, BeatBenchYrs) is always a passive index:
        the lab's, or `benchmark` if that is an IndexBenchmark. A ScreenBenchmark passed as `benchmark`,
        and anything in `compare`, only adds rows labelled "<name> (compare)"."""
        cfg = self._cfg(**cfg)
        bench, extras = self.passive(benchmark), self._extras(benchmark, compare)
        call_exit = as_policy(exit) or cfg.exit
        items, used = [], set()                      # (label, screen, config, policy or None)
        for s in (screens or list(self.screens)):
            if isinstance(s, Portfolio):
                sc, c, pol = self.screen(s.screen), self._cfg_of(cfg, s.config_overrides), s.exit or call_exit
                label = s.name
            else:
                sc, c, pol = self.screen(s), cfg, call_exit
                label = sc.name
            both = pol is not None and with_and_without
            label = _unique_label(label, used, bench.label, "+exit" if both else None)
            if pol is None:
                items.append((label, sc, c, None))
                continue
            if both:
                items.append((label, sc, replace(c, exit=None), None))
                label += "+exit"
            items.append((label, sc, replace(c, exit=pol), pol))
        screens = items
        start = start or self.market.index[0] + pd.DateOffset(months=13)
        start = self.market.trading_day(start)
        bcurve = bench.curve(self.market, cfg, start, end)
        rows = {bench.label: {**metrics.summarize(bcurve), "AvgN": float("nan")}}
        ccurves = {}
        for x in extras:
            lab_x = f"{x.label} (compare)"
            ccurves[lab_x] = x.curve(self.market, cfg, start, end)
            rows[lab_x] = {**metrics.summarize(ccurves[lab_x], bcurve), "AvgN": float("nan")}
        results = {}
        for i, (label, sc, c, _) in enumerate(screens):
            with _progress.scaled(i / len(screens), (i + 1) / len(screens)):
                res = backtest.run(sc, self.market, c, start, end)
            results[label] = res
            yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
            rows[label] = {**metrics.summarize(res.curve, bcurve),
                           "AvgN": pd.Series({k: len(v) for k, v in res.holdings.items()}).mean(),
                           "Turnover": res.turnover.iloc[1:].mean(),
                           "Costs/yr": res.costs.sum() / yrs}
            if res.exit is not None:
                rows[label].update({"TimeOut": res.time_out, "Switches/yr": res.switches / yrs})
        return BacktestReport(pd.DataFrame(rows).T, results, bcurve, bench.label, cfg, ccurves)

    @tracked("timing")
    def timing(self, screen, mode: str = "phase", stride: int = 1, start=None, end=None,
               offsets=None, benchmark=None, progress: bool = False, **cfg):
        """Rebalance-timing study (see timing.py); returns a timing.TimingResult."""
        from . import timing          # lazy: lets `python -m rothery_screener.timing` run cleanly
        return timing.study(self.screen(screen), self.market, self._cfg(**cfg), start, end, mode,
                            stride, offsets, self.passive(benchmark), progress)

    # ---- research --------------------------------------------------------
    @tracked("significance", lambda self, report, n=2000, seed=0: {
        **getattr(report, "_args", {"screens": list(report.results)}), "n": n, "seed": seed})
    def significance(self, report: BacktestReport, n: int = 2000, seed: int = 0) -> pd.DataFrame:
        """Bootstrap CI on excess return and Newey-West t-stat per screen, plus DSR across screens.

        The deflated Sharpe uses `n_trials` = the family's trial count (distinct significance runs on this
        dataset and screen base, from the store or this process) when that exceeds the number of screens;
        `result.attrs` carries `trials_in_family` and `trials_scope`."""
        from . import stats
        rows = {}
        for name, res in report.results.items():
            b = stats.bootstrap_excess(res.curve, report.benchmark, n=n, seed=seed, curves=True)
            rows[name] = {"Excess/yr": b["mean"], "CI5": b["ci_low"], "CI95": b["ci_high"],
                          "P(>0)": b["p_positive"],
                          "t": stats.t_stat_excess(res.curve, report.benchmark, curves=True)}
        out = pd.DataFrame(rows).T
        out["Sharpe"] = report.table.loc[out.index, "Sharpe"]
        n_obs = int(report.benchmark.pct_change().dropna().shape[0])
        info = self.last_trials() or {}
        n_trials = max(len(out), int(info.get("trials_in_family", 0)))
        res = stats.summarize_trials(out, n_obs=n_obs, n_trials=n_trials,
                                     periods_per_year=stats.periods_per_year(report.benchmark.index))
        res.attrs.update(info, n_trials=n_trials)
        return res

    @tracked("sweep")
    def sweep(self, screen, grid, objective="Excess/yr", start=None, end=None, splits=None,
              benchmark=None) -> pd.DataFrame:
        """Grid / random-search sweep; dotted keys ("dogs.n") hit the screen, bare keys Config.
        `splits=dict(train_years=.., test_years=..)` adds walk-forward out-of-sample columns.
        Every grid point counts as a trial: `result.attrs["n_trials"]` (also `trials_in_family`,
        `trials_scope`) is what `stats.summarize_trials(result, n_trials=...)` should deflate by."""
        from .sweep import Grid, run_sweep
        if isinstance(grid, dict):
            grid = Grid(grid)
        n = note_points(self, "sweep", self._tl.args or {}, grid_points(grid.spec if isinstance(grid, Grid) else grid)) \
            if getattr(self._tl, "args", None) is not None else 0
        tbl = run_sweep(self, self.screen(screen), grid, objective, start, end, splits, self.passive(benchmark))
        info = self.last_trials() or {}
        tbl.attrs.update(info, n_trials=max(len(tbl), n))
        return tbl

    @tracked("extended", lambda self, report, macro, spread_over_prime=0.0, leverage=1.0: {
        **getattr(report, "_args", {"screens": list(report.results)}), "macro": macro,
        "spread_over_prime": spread_over_prime, "leverage": leverage})
    def extended(self, report: BacktestReport, macro, spread_over_prime: float = 0.0,
                 leverage: float = 1.0) -> pd.DataFrame:
        """Real / home-currency / net-of-financing / excess-over-rf metrics for every curve in a report."""
        from .metrics_ext import extended_summary
        rows = {report.benchmark_label: extended_summary(report.benchmark, macro, None, spread_over_prime, leverage)}
        for name, c in report.compare.items():
            rows[name] = extended_summary(c, macro, report.benchmark, spread_over_prime, leverage)
        for name, res in report.results.items():
            rows[name] = extended_summary(res.curve, macro, report.benchmark, spread_over_prime, leverage)
        return pd.DataFrame(rows).T

    @tracked("exit_evaluate")
    def exit_eval(self, exit_screen, target=None, horizons=(1, 3, 12), macro=None, policy=None, **kw):
        """Evaluate an ExitScreen against a target curve (default: the passive index).

        `exit_screen` may also be an ExitPolicy; with `policy` (or a policy as the first argument) the
        overlay in the result trades that policy (reentry, park, steps, delay, costs) instead of the
        plain flag. `cash_rate` is the rate for park="cash"."""
        from .exit_signals import evaluate
        if isinstance(exit_screen, ExitPolicy):
            policy, exit_screen = exit_screen, exit_screen.screen
        if target is None:
            target = self.passive().curve(self.market, self.config, self.market.index[0])
        elif isinstance(target, str):
            target = self.market.series(target)
        macro = macro if macro is not None else (policy.macro if policy is not None else None)
        if policy is None:
            return evaluate(exit_screen, self.market, target, horizons, macro, **kw)
        ev = evaluate(exit_screen, self.market, target, horizons, macro, **kw)
        rebal = backtest.schedule(self.market, target.dropna().index[0], self.config.rebalance_months)
        ev.overlay = policy_overlay(target, policy, self.market, rebal, float(kw.get("cash_rate", 0.0)),
                                    state=ev.state, signal_curve=kw.get("curve"))
        return ev

    @tracked("exit_calibrate")
    def calibrate_exit(self, exit_screen, grid, target=None, objective="Calmar", splits=None,
                       constraints=None, macro=None, **kw):
        """Walk-forward calibration of an ExitScreen's thresholds / timing (see calibration.py).
        `target`: index name, ticker or curve (default: the passive index curve)."""
        from .calibration import calibrate_exit
        kw.setdefault("rebalance_months", self.config.rebalance_months)
        if target is None:
            target = self.passive().curve(self.market, self.config, self.market.index[0])
        n = (note_points(self, "exit_calibrate", self._tl.args, grid_points(grid))
             if getattr(self._tl, "args", None) is not None else 0)
        res = calibrate_exit(exit_screen, self.market, target, grid, objective, splits, constraints, macro,
                             n_trials=n or None, **kw)
        res.summary.update(self.last_trials() or {})
        return res

    @tracked("graham")
    def graham(self, tests="defensive", asof=None, **overrides) -> tuple[pd.DataFrame, pd.Series]:
        """(per-name pass/fail table, cumulative funnel) for a preset or GrahamTests."""
        t = graham_tests(tests)
        t = replace(t, **overrides) if overrides else t
        s = self.snapshot(asof)
        return graham_report(s, t), graham_funnel(s, t)

    # ---- runs, jobs and trials ---------------------------------------------------------------
    def last_trials(self) -> dict | None:
        """{"trials_in_family", "trials_scope", ...} of this thread's latest tracked call (None before any)."""
        return getattr(self._tl, "last", None)

    def submit(self, kind: str, *, label=None, tags=(), force: bool = False, run_seed: int = 0, **args):
        """Start `kind` ("backtest", "timing", "sweep", "significance", "extended", "exit_evaluate",
        "exit_calibrate", "graham", "picks") asynchronously with the Lab method's keyword arguments and
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
