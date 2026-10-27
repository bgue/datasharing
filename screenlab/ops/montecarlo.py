"""montecarlo: distribution of outcomes of screen portfolios and the passive index on resampled paths
(`Lab.montecarlo`, DESIGN.md K).

One PathSet is drawn from the lab's dataset by a scenario generator (`montecarlo/generators.py`); every
portfolio AND the passive index are run on exactly those paths (`pipeline.simulate_paths`), and the outcome
distributions are read off per horizon. The first 12 months of every path are a warm-up (price screens and
trend signals need history); the portfolio is formed after it, so horizon `h` covers the `h` years after
the warm-up. A shorter horizon is the prefix of the longest one: the same simulated paths are cut, not redrawn.

Time-weighted vs money-weighted: CAGR, excess CAGR, drawdowns and P(trail the index) always use the
wealth path WITHOUT contributions or withdrawals (the portfolio's own return). Terminal wealth and the
shortfall probability include the flows (`contributions` / `withdrawals` per year, spread evenly over the
steps, invested at the portfolio's return; wealth cannot go below zero).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np
import pandas as pd

from .. import progress as _progress
from ..dataset import DataCard, Dataset
from ..errors import ServerError
from ..exit_policy import as_policy
from ..jsonout import DEFAULT_MAX_POINTS, scalar, table
from ..metrics import M
from ..operations import Operation, arg, model_hook, register_operation
from ._request import ConfigOverrides, ExitRequest, clean, config_overrides, exit_policy

QUANTILES = (5, 25, 50, 75, 95)
DD_LEVELS = (0.30, 0.50)
WARMUP_MONTHS = 12
STEPS_PER_YEAR = {"monthly": 12, "daily": 252}
BIAS_NOTE = ("Monte Carlo note: resampling reproduces the data it is drawn from, so every bias of the base "
             "dataset (survivors, price-only index, ...) is inherited by every path.")


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------
def to_monthly(ds: Dataset) -> Dataset:
    """The dataset on month-end steps (last price of each month; indices likewise): a fast base for the
    generators. Fundamentals, universe and card are kept (the card's span is the monthly span)."""
    px = ds.prices.sort_index().resample("ME").last()
    ix = ds.indices.sort_index()
    ix = ix.resample("ME").last() if len(ix.columns) else pd.DataFrame(index=px.index)
    ix = ix.reindex(px.index)
    px.index.name = ix.index.name = "date"
    card = replace(ds.manifest, start=str(px.index[0].date()), end=str(px.index[-1].date()))
    return Dataset(px, ds.fundamentals, ix, ds.universe, card)


def _generator(g):
    from ..montecarlo import BlockBootstrap
    from ..serial import from_dict
    if g is None:
        return BlockBootstrap(block_months=12)
    if isinstance(g, dict):
        try:
            g = from_dict(g if "__t" in g else {"__t": g.get("type"), **{k: v for k, v in g.items() if k != "type"}})
        except Exception as e:
            raise ServerError(400, f"bad generator {g!r}: {e}") from e
    if not hasattr(g, "paths"):
        raise ServerError(400, f"generator must be a scenario generator or its serialized form, got {g!r}")
    return g


def _flows(rel: np.ndarray, start_wealth: float, net_per_step: float) -> np.ndarray:
    """Wealth [P, M+1] from the relative wealth path `rel` [P, M+1] with `net_per_step` added after each step."""
    g = rel[:, 1:] / rel[:, :-1]
    v = np.full(rel.shape[0], float(start_wealth))
    out = np.empty_like(rel)
    out[:, 0] = v
    for t in range(g.shape[1]):
        v = np.maximum(v * g[:, t] + net_per_step, 0.0)
        out[:, t + 1] = v
    return out


def _drawdown_stats(rel: np.ndarray, steps_per_year: int):
    """max drawdown [P] (<= 0), years from the trough of the worst drawdown to the recovery of its peak [P]
    (inf: not recovered by the end of the path)."""
    peak = np.maximum.accumulate(rel, axis=1)
    dd = rel / peak - 1.0
    trough = dd.argmin(axis=1)
    rows = np.arange(rel.shape[0])
    pk = peak[rows, trough]
    idx = np.arange(rel.shape[1])[None, :]
    back = (idx > trough[:, None]) & (rel >= pk[:, None] * (1 - 1e-12))
    first = back.argmax(axis=1)
    rec = np.where(back.any(axis=1), (first - trough) / steps_per_year, np.inf)
    rec = np.where(dd[rows, trough] < -1e-12, rec, 0.0)                    # never below its peak: nothing to recover
    return dd.min(axis=1), rec


def _q(x: np.ndarray, qs=QUANTILES) -> dict:
    v = np.nanpercentile(x, qs)
    return dict(zip(qs, v))


@dataclass
class MonteCarloResult:
    """Outcome distributions of every portfolio and the passive index on the same resampled paths.

    table      one row per (portfolio, horizon_years); the index row (label `index_label`) has no trail columns.
    per_path   long table, one row per (portfolio, horizon_years, path): terminal wealth, CAGR, excess CAGR,
               max drawdown, years to recover, and the index's CAGR on the same path.
    fan        wealth quantile curves over the longest horizon: index = years since formation,
               columns `<portfolio>|p5` ... `p95` (time-weighted wealth + flows).
    """
    table: pd.DataFrame
    per_path: pd.DataFrame
    fan: pd.DataFrame
    index_label: str
    horizon_years: list
    n_paths: int
    seed: int
    steps: str
    start_wealth: float
    generator: dict
    data_card: DataCard | None = None
    warnings: list = field(default_factory=list)
    args: dict = field(default_factory=dict)

    def __str__(self):
        head = (f"Monte Carlo: {self.n_paths} paths, horizons {self.horizon_years} years, {self.steps} steps, "
                f"start wealth {self.start_wealth:,.0f}, generator {self.generator.get('__t', '?')} "
                f"{ {k: v for k, v in self.generator.items() if k != '__t'} }, seed {self.seed}, "
                f"passive index {self.index_label}\n")
        card = "\n".join(([self.data_card.line()] if self.data_card is not None else []) + list(self.warnings)) + "\n"
        t = self.table.copy()
        cols = {c: (lambda x: "" if pd.isna(x) else f"{x:,.0f}") for c in t.columns if c.startswith("TermWealth")}
        for c in t.columns:
            if c in cols:
                continue
            cols[c] = (lambda x: "" if pd.isna(x) else f"{x:.1%}") if c != M.MC_RECOVER_YRS else \
                (lambda x: "" if pd.isna(x) else f"{x:.1f}")
        out = t.copy().astype(object)
        for c in t.columns:
            out[c] = t[c].map(cols[c])
        return head + card + "\n" + out.to_string()


def _extract(obj, j, out):
    out.summary[j("table")] = obj.table
    out.bulk[j("fan")] = obj.fan


class GeneratorSpec:
    """Marker annotation: one scenario generator, `{"type": <name>, <parameters>}`."""


@model_hook(GeneratorSpec)
def _generator_union():
    import typing
    from pydantic import ConfigDict, Field, create_model
    from ..montecarlo import BlockBootstrap, IIDBootstrap, RegimeBootstrap, StationaryBootstrap
    models = []
    for cls in (BlockBootstrap, StationaryBootstrap, RegimeBootstrap, IIDBootstrap):
        flds: dict = {"type": (Literal[cls.__name__], Field(..., description=f"generator type {cls.__name__}"))}
        for f in dataclasses.fields(cls):
            if f.name == "signal_screen":                    # a live ExitScreen: use the Python API
                continue
            tp = float | list[float] | None if f.name == "p_stay" else float
            flds[f.name] = (tp, Field(None if f.name == "p_stay" else f.default,
                                      description=f"{cls.__name__}.{f.name}"))
        models.append(create_model(f"Generator_{cls.__name__}", __config__=ConfigDict(extra="forbid"),
                                   __doc__=(cls.__doc__ or "").strip().splitlines()[0], **flds))
    return typing.Annotated[typing.Union[tuple(models)], Field(discriminator="type")]


# ---------------------------------------------------------------------------------------------------
@register_operation(builtin=True)
class MonteCarlo(Operation):
    """Monte Carlo: outcome distributions of screen portfolios vs the passive index on resampled return paths."""
    name = "montecarlo"
    long_running = True
    takes_max_points = False
    method_names = ("monte_carlo",)
    registry_order = ("screens", "generator", "n_paths", "horizon_years", "start_wealth", "contributions",
                      "withdrawals", "rebalance_months", "exit", "seed", "steps", "target_wealth", "config", "lab")

    @dataclass
    class Args:
        screens: list[str] | None = arg(None, "screen names (price rank screens: momentum_12_1, momentum_6m, "
                                              "momentum_12m, low_vol); default every screen of the lab")
        generator: GeneratorSpec | None = arg(None, "scenario generator, e.g. {'type': 'BlockBootstrap', "
                                                    "'block_months': 12} (the default); also StationaryBootstrap "
                                                    "(mean_block_months), RegimeBootstrap, IIDBootstrap")
        n_paths: int = arg(1000, "number of resampled paths")
        horizon_years: list[float] = arg(None, "investment horizons in years (default [5, 10, 20])",
                                         factory=lambda: [5, 10, 20])
        start_wealth: float = arg(100_000.0, "starting wealth")
        contributions: float = arg(0.0, "added per year, spread over the steps")
        withdrawals: float = arg(0.0, "taken out per year, spread over the steps")
        rebalance_months: int | None = arg(None, "months between rebalances (default: the lab's Config)")
        exit: ExitRequest | None = arg(None, "exit policy applied to every screen (TrendBreak only)")
        with_and_without: bool = arg(False, "with `exit`: also run each screen without it (rows name, name+exit)")
        seed: int = arg(0, "random seed of the path draw")
        steps: Literal["monthly", "daily"] = arg("monthly", "'monthly': the base is resampled to month-ends first "
                                                            "(fast); 'daily': the base's own steps (memory heavy)")
        target_wealth: float | None = arg(None, "report P(terminal wealth below this)")
        config: ConfigOverrides | None = arg(None, "Config overrides: cost_bps, mer, cash_rate, ...")

    # ---- computation
    def run(self, lab, screens=None, generator=None, n_paths: int = 1000, horizon_years=(5, 10, 20),
            start_wealth: float = 100_000.0, contributions: float = 0.0, withdrawals: float = 0.0,
            rebalance_months=None, exit=None, with_and_without: bool = False, seed: int = 0,
            steps: str = "monthly", target_wealth=None, benchmark=None, **cfg) -> MonteCarloResult:
        """Run every screen and the passive index on the SAME resampled paths and summarize per horizon:
        terminal wealth and CAGR (p5/p25/p50/p75/p95), P(trail the index), median excess CAGR, P(max drawdown
        worse than 30% / 50%), median years to recover from the worst drawdown, P(shortfall vs target_wealth).

        `generator`: a scenario generator (BlockBootstrap(12) by default). The base dataset is first cut to
        month-end steps (`steps="monthly"`). Screens must be price ranks (momentum_*, low_vol) with equal weight;
        `exit` must be vectorizable (TrendBreak). See the module docstring for flows and horizons."""
        from ..pipeline import simulate_paths
        if steps not in STEPS_PER_YEAR:
            raise ValueError(f"steps must be 'monthly' or 'daily', got {steps!r}")
        hs = sorted({float(h) for h in (horizon_years if np.iterable(horizon_years) else [horizon_years])})
        if not hs or hs[0] <= 0:
            raise ValueError("horizon_years must be positive")
        if n_paths < 1:
            raise ValueError("n_paths must be at least 1")
        spy = STEPS_PER_YEAR[steps]
        gen = _generator(generator)
        bench = lab.passive(benchmark)
        base = lab.market.dataset
        card = lab.data_card(benchmark)
        if steps == "monthly":
            base = to_monthly(base)
        if rebalance_months is not None:
            cfg["rebalance_months"] = int(rebalance_months)
        c0 = lab._cfg(**cfg)
        pol = as_policy(exit)
        items = []                                          # (label, screen, config)
        for s in (screens or list(lab.screens)):
            sc = lab.screen(s)
            if pol is None:
                items.append((sc.name, sc, c0))
            else:
                if with_and_without:
                    items.append((sc.name, sc, replace(c0, exit=None)))
                items.append((sc.name + "+exit" if with_and_without else sc.name, sc, replace(c0, exit=pol)))

        # ---- the paths, once
        hmax = hs[-1]
        ps = gen.paths(base, int(n_paths), hmax + WARMUP_MONTHS / 12 + 0.1, seed)
        dates = ps.dates
        start = dates[0] + pd.DateOffset(months=WARMUP_MONTHS)
        s0 = int(dates.searchsorted(start))
        ends = {h: s0 + int(round(h * spy)) for h in hs}
        if ends[hmax] >= len(dates):
            raise ValueError("internal: horizon longer than the simulated paths")
        flow = (float(contributions) - float(withdrawals)) / spy

        wealth, idx_w = {}, None
        for i, (label, sc, c) in enumerate(items):
            with _progress.scaled(i / len(items), (i + 1) / len(items)):
                pw = simulate_paths(sc, c, ps, base=base, selection="per_path", start=start,
                                    index=bench.index, index_mer=bench.mer, index_cost_bps=bench.cost_bps,
                                    start_wealth=1.0)
            wealth[label] = pw.portfolio
            idx_w = pw.index
        if idx_w is None:
            raise ValueError("the dataset has no index series to compare with")

        # ---- distributions
        rows, long, fan = {}, [], {}
        P = int(n_paths)
        series = {**wealth, bench.label: idx_w}
        cagr_idx, rel_idx_h = {}, {}
        for h in hs:
            e = ends[h]
            rel = idx_w[:, s0:e + 1] / idx_w[:, s0:s0 + 1]
            rel_idx_h[h] = rel
            cagr_idx[h] = rel[:, -1] ** (1.0 / h) - 1
        for label, w in series.items():
            is_index = label == bench.label
            for h in hs:
                e = ends[h]
                rel = w[:, s0:e + 1] / w[:, s0:s0 + 1]
                if not np.isfinite(rel).all():
                    raise ValueError(f"non-finite wealth for {label!r}: the path set has gaps the screen cannot handle")
                cagr = rel[:, -1] ** (1.0 / h) - 1
                tv = _flows(rel, start_wealth, flow)[:, -1] if flow else rel[:, -1] * start_wealth
                mdd, rec = _drawdown_stats(rel, spy)
                excess = cagr - cagr_idx[h]
                row = {}
                for q, v in _q(tv).items():
                    row[f"{M.MC_TERMW}_p{q}"] = v
                for q, v in _q(cagr).items():
                    row[f"{M.MC_CAGR}_p{q}"] = v
                row[M.MC_P_TRAIL] = np.nan if is_index else float((cagr < cagr_idx[h]).mean())
                row[M.MC_EXCESS_MED] = np.nan if is_index else float(np.median(excess))
                for lv in DD_LEVELS:
                    row[M.mc_pdd(lv)] = float((mdd < -lv).mean())
                row[M.MC_RECOVER_YRS] = float(np.median(rec)) if np.isfinite(np.median(rec)) else np.nan
                row[M.MC_P_UNRECOVERED] = float(np.isinf(rec).mean())
                row[M.MC_P_SHORTFALL] = float((tv < target_wealth).mean()) if target_wealth is not None else np.nan
                rows[(label, h)] = row
                long.append(pd.DataFrame({"portfolio": label, "horizon_years": h, "path": np.arange(P),
                                          "terminal_wealth": tv, "cagr": cagr, "index_cagr": cagr_idx[h],
                                          "excess_cagr": excess, "max_drawdown": mdd,
                                          "recover_years": np.where(np.isinf(rec), np.nan, rec),
                                          "recovered": np.isfinite(rec)}))
                if h == hmax:
                    vals = _flows(rel, start_wealth, flow) if flow else rel * start_wealth
                    qs = np.percentile(vals, QUANTILES, axis=0)
                    for q, curve in zip(QUANTILES, qs):
                        fan[f"{label}|p{q}"] = curve
        tbl = pd.DataFrame.from_dict(rows, orient="index")
        tbl.index = pd.MultiIndex.from_tuples(tbl.index, names=["portfolio", "horizon_years"])
        years = np.arange(ends[hmax] - s0 + 1) / spy
        fan_df = pd.DataFrame(fan, index=pd.Index(years, name="years"))
        warns = [*card.warnings(), BIAS_NOTE]
        if steps == "monthly":
            warns.append("Steps are month-ends: daily-clock rules (weekly exit checks, 10-day holds) act at "
                         "monthly granularity.")
        gd = gen.to_dict() if hasattr(gen, "to_dict") else {"__t": type(gen).__name__}
        return MonteCarloResult(tbl, pd.concat(long, ignore_index=True), fan_df, bench.label, hs, P, int(seed),
                                steps, float(start_wealth), gd, card, warns,
                                dict(screens=[i[0] for i in items], contributions=contributions,
                                     withdrawals=withdrawals, target_wealth=target_wealth))

    # ---- requests
    def build(self, lab, a) -> dict:
        cfg = config_overrides(a.config)
        if a.exit is not None and not isinstance(a.exit, dict):
            raise ServerError(400, "`exit` must be an object (see list_signals)")
        pol = exit_policy(lab, a.exit) if a.exit else None
        gen = _generator(a.generator) if a.generator is not None else None
        if a.n_paths < 1:
            raise ServerError(400, "`n_paths` must be at least 1")
        hs = list(a.horizon_years or [5, 10, 20])
        if any((not isinstance(h, (int, float))) or h <= 0 for h in hs):
            raise ServerError(400, "`horizon_years` must be a list of positive numbers")
        return clean(dict(screens=a.screens, generator=gen, n_paths=a.n_paths, horizon_years=hs,
                          start_wealth=a.start_wealth, contributions=a.contributions or None,
                          withdrawals=a.withdrawals or None, rebalance_months=a.rebalance_months, exit=pol,
                          with_and_without=bool(a.with_and_without) or None, seed=a.seed, steps=a.steps,
                          target_wealth=a.target_wealth, **cfg))

    def defaults(self) -> dict:
        d = super().defaults()
        d["horizon_years"] = [5, 10, 20]
        return d

    # ---- results
    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        fan = result.fan.reset_index()
        return {"benchmark": result.index_label, "generator": result.generator, "n_paths": result.n_paths,
                "seed": result.seed, "steps": result.steps, "horizon_years": result.horizon_years,
                "start_wealth": result.start_wealth, "table": table(result.table),
                "fan": {"columns": [str(c) for c in fan.columns],
                        "rows": [[scalar(v) for v in r] for r in fan.itertuples(index=False, name=None)]},
                "warnings": list(result.warnings)}

    def metrics(self, result) -> list:
        rows = []
        for (label, h), r in result.table.iterrows():
            for m, v in r.items():
                rows.append((f"{label}@{h:g}y", str(m), None if pd.isna(v) else float(v)))
        return rows


from .. import store as _store  # noqa: E402
_store.register_extractor("MonteCarloResult", _extract)
