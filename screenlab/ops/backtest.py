"""backtest: compare screen portfolios and the passive index (`Lab.backtest`), and its report type."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace

import pandas as pd

from .. import backtest as _bt
from .. import metrics
from .. import progress as _progress
from ..dataset import DataCard
from ..errors import ServerError
from ..exit_policy import Portfolio, as_policy
from ..jsonout import DEFAULT_MAX_POINTS, curves, scalar, table
from ..metrics import M
from ..operations import Operation, arg, register_operation
from ._request import BenchmarkRequest, ConfigOverrides, ExitRequest, benchmarks, clean, config_overrides, exit_policy, parse_date


def format_table(tbl: pd.DataFrame) -> str:
    out = tbl.copy().astype(object)
    for c in tbl.columns:
        out[c] = tbl[c].map(lambda x: metrics.fmt(c, x))
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
    config: _bt.Config
    compare: dict = field(default_factory=dict)   # label -> extra comparison curve (screen benchmarks etc.)
    data_card: DataCard | None = None             # what the data is (benchmark kind, survivorship, ...)
    price_benchmark_allowed: bool = False         # the run was told to accept a price-index benchmark
    tax: dict = field(default_factory=dict)       # label -> tax table per year (taxes.TAX_COLS); empty without Config.tax

    @property
    def curves(self) -> pd.DataFrame:
        c = {k: r.curve for k, r in self.results.items()}
        return pd.DataFrame({self.benchmark_label: self.benchmark, **self.compare, **c})

    def _rebalance_text(self) -> str:
        """Each row's actual rebalance spacing (median months between scheduled dates), since a
        Portfolio can override the report's Config."""
        per = {}
        for name, res in self.results.items():
            d = pd.DatetimeIndex(sorted(res.holdings))
            gaps = (d[1:].year - d[:-1].year) * 12 + (d[1:].month - d[:-1].month)
            per[name] = int(pd.Series(gaps).median()) if len(gaps) else self.config.rebalance_months
        groups = {}
        for name, m in per.items():
            groups.setdefault(m, []).append(name)
        if len(groups) <= 1:
            return f"every {next(iter(groups), self.config.rebalance_months)}m"
        return "; ".join(f"every {m}m: {', '.join(n)}" if len(n) <= 2 else f"every {m}m: {len(n)} portfolios"
                         for m, n in sorted(groups.items()))

    def __str__(self):
        span = f"{self.benchmark.index[0]:%Y-%m-%d} to {self.benchmark.index[-1]:%Y-%m-%d}"
        head = (f"Backtest {span}, rebalance {self._rebalance_text()}, "
                f"benchmark {self.benchmark_label}\n")
        card = ""
        if self.data_card is not None:
            card = "\n".join([self.data_card.line(), *self.data_card.warnings()]) + "\n"
        return head + card + "\n" + format_table(self.table.sort_values(M.CAGR, ascending=False))


def report_json(rep, max_points) -> dict:
    c = rep.config
    cfg = {k: scalar(v) for k, v in c.flat().items() if isinstance(v, (int, float, str, type(None)))}
    return {"benchmark": rep.benchmark_label, "config": cfg, "table": table(rep.table),
            "curves": curves(rep.curves, max_points)}


def _extract_report(obj, j, out):
    out.summary[j("table")] = obj.table
    out.bulk[j("curves")] = obj.curves


@register_operation(builtin=True)
class Backtest(Operation):
    """Backtest screens against the passive index."""
    name = "backtest"
    registry_order = ("screens", "start", "end", "config", "lab", "max_points", "exit", "with_and_without",
                      "benchmark", "compare", "allow_price_benchmark")

    @dataclass
    class Args:
        screens: list[str] | None = arg(None, "screen names (default: every screen of the lab)")
        start: str | None = arg(None, "first formation date (default: 13 months into the data)")
        end: str | None = arg(None, "last date")
        config: ConfigOverrides | None = arg(None, "Config overrides: rebalance_months, cost_bps, commission, "
                                                   "capital, mer, delist_return, min_holdings, cash_rate, ...")
        exit: ExitRequest | None = arg(None, "exit policy applied to every screen")
        with_and_without: bool = arg(False, "also run each screen without the exit policy (rows name, name+exit)")
        benchmark: str | BenchmarkRequest | None = arg(None, "index name, or {index, dividend_yield, mer, cost_bps}, replacing the lab's passive benchmark")
        allow_price_benchmark: bool = arg(False, "keep a price-index benchmark (excess return is then overstated by about the dividend yield); by default it is refused")
        compare: list[str] | None = arg(None, "extra comparison rows: index names or 'topN'")

    def run(self, lab, screens=None, start=None, end=None, benchmark=None, exit=None,
            with_and_without: bool = False, compare=(), allow_price_benchmark=None, **cfg) -> BacktestReport:
        """Backtest screens (names, Screens or `Portfolio(name, screen, exit=None, **config_overrides)`).

        `exit`: an ExitPolicy (or ExitScreen, wrapped in a default policy) applied to every entry in the
        call that has no policy of its own. `with_and_without=True` runs each such entry twice and labels
        the rows `name` and `name+exit`.
        Row labels are unique: a repeated name (two entries called "dogs", or a Portfolio named like a
        screen or like the benchmark) is kept and numbered in order of appearance, `dogs`, `dogs#2`, `dogs#3`.
        Rows with a policy also report TimeOut and Switches/yr.

        The primary benchmark (`report.benchmark`, Excess/yr, BeatBenchYrs) is always a passive index:
        the lab's, or `benchmark` if that is an IndexBenchmark. A ScreenBenchmark passed as `benchmark`,
        and anything in `compare`, only adds rows labelled "<name> (compare)".

        Portfolios are total return, so a price-index benchmark (`IndexBenchmark.kind == "price"`) is refused
        with a `BenchmarkKindError` naming the fixes (`IndexBenchmark("SP500", dividend_yield=0.02)`, a TR
        series, or `allow_price_benchmark=True` here or on the Lab, which keeps it with a warning)."""
        cfg = lab._cfg(**cfg)
        bench = lab.benchmark_for(benchmark, allow_price_benchmark, "this backtest")
        extras = lab._extras(benchmark, compare)
        lab.check_extras(extras, allow_price_benchmark, "this backtest's compare rows")
        call_exit = as_policy(exit) or cfg.exit
        items, used = [], set()                      # (label, screen, config, policy or None)
        for s in (screens or list(lab.screens)):
            if isinstance(s, Portfolio):
                sc, c, pol = lab.screen(s.screen), lab._cfg_of(cfg, s.config_overrides), s.exit or call_exit
                label = s.name
            else:
                sc, c, pol = lab.screen(s), cfg, call_exit
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
        start = start or lab.market.index[0] + pd.DateOffset(months=13)
        start = lab.market.trading_day(start)
        bcurve = bench.curve(lab.market, cfg, start, end)
        rows = {bench.label: {**metrics.summarize(bcurve), M.AVGN: float("nan")}}
        ccurves = {}
        for x in extras:
            lab_x = f"{x.label} (compare)"
            ccurves[lab_x] = x.curve(lab.market, cfg, start, end)
            rows[lab_x] = {**metrics.summarize(ccurves[lab_x], bcurve), M.AVGN: float("nan")}
        results = {}
        for i, (label, sc, c, _) in enumerate(screens):
            with _progress.scaled(i / len(screens), (i + 1) / len(screens)):
                res = _bt.run(sc, lab.market, c, start, end)
            results[label] = res
            yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
            rows[label] = {**metrics.summarize(res.curve, bcurve),
                           M.AVGN: pd.Series({k: len(v) for k, v in res.holdings.items()}).mean(),
                           M.TURNOVER: res.turnover.iloc[1:].mean(),
                           M.COSTS_YR: res.costs.sum() / yrs}
            if res.exit is not None:
                rows[label].update({M.TIMEOUT: res.time_out, M.SWITCHES_YR: res.switches / yrs})
        # --- tax columns (taxes.py): AfterTaxCAGR, TaxDrag/yr, AfterTaxExcess/yr; the index is taxed the same way
        from ..taxes import report_columns as _tax_columns
        tax_tables = _tax_columns(rows, results, bench, bcurve, cfg, lab.market, start,
                                  cfg.tax or next((c.tax for _, _, c, _ in screens if c.tax is not None), None))
        # --- end tax columns
        rep = BacktestReport(pd.DataFrame(rows).T, results, bcurve, bench.label, cfg, ccurves,
                             lab.data_card(benchmark))
        rep.tax = tax_tables
        rep.price_benchmark_allowed = bool(lab.allow_price_benchmark if allow_price_benchmark is None
                                           else allow_price_benchmark)
        return rep

    def annotate(self, result, args):
        if isinstance(result, BacktestReport):
            result._args = args

    def build(self, lab, a) -> dict:
        cfg = config_overrides(a.config)
        start, end = parse_date("start", a.start), parse_date("end", a.end)
        if a.exit is not None and not isinstance(a.exit, dict):
            raise ServerError(400, "`exit` must be an object (see list_signals)")
        pol = exit_policy(lab, a.exit) if a.exit else None
        bm, cmp = benchmarks(a.benchmark, a.compare)
        return clean(dict(screens=a.screens, start=start, end=end, benchmark=bm, exit=pol,
                          with_and_without=bool(a.with_and_without) or None, compare=cmp or None,
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None, **cfg))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        return report_json(result, max_points)


def _register_extractors():
    from .. import store
    store.register_extractor("BacktestReport", _extract_report)


_register_extractors()
