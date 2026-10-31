"""plan and plan_montecarlo: a dated investment plan on a configured strategy, against the same flows into the index.

# PC: operations for plan.py (the straight calculation) and montecarlo/plan_mc.py (the Monte Carlo study). Nothing here is
new arithmetic: the operations turn a request into a `PlanSpec`, call `plan.run_plan` / `plan_mc.plan_montecarlo` and shape
the result as JSON. REST routes, MCP tools, job kinds and the store come from the operation registry (operations.py).
Contract and approximations: docs/PLAN.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import pandas as pd

from .. import plan as _plan_module  # PC: also registers the CashFlow / PlanSpec serial tags used by job specs
from ..errors import ServerError
from ..jsonout import scalar, table
from ..operations import Operation, arg, register_operation
from . import _request as R
from ._request import ConfigOverrides, TaxRequest, clean, config_overrides, parse_date

GROWTHS = ("cpi",)
HIST_BINS = 30
SERIES_COLS = ("contributed", "strategy_pre", "strategy_after_tax", "index_pre", "index_after_tax", "strategy_real",
               "index_real", "strategy_after_tax_real", "index_after_tax_real", "cash")


@dataclass
class ExtraFlow:
    """One dated extraordinary flow: positive = investment, negative = withdrawal."""
    date: str = arg(doc="date of the flow (it executes on the next trading day)")
    amount: float = arg(doc="dollars, positive = contribution, negative = withdrawal")
    note: str = arg("", "free text")


@dataclass
class PlanRequest:
    """The plan: every field of `plan.PlanSpec`, with the account and tax settings as `tax` and the strategy settings as `config`."""
    screen: str = arg(doc="the configured strategy: a screen name of the lab (see list_screens)")
    start: str = arg(doc="first investment date (the lump sum and the first periodic contribution)")
    end: str | None = arg(None, "anticipated end date (default: the last data bar; past the data the straight calculation stops "
                                "at the last bar and says so, the Monte Carlo continues)")
    initial: float = arg(0.0, "lump sum in dollars on `start`")
    periodic: float = arg(0.0, "contribution per period in dollars")
    frequency: Literal["biweekly", "monthly", "quarterly", "annual"] = arg("monthly", "period of the contribution")
    growth: float | Literal["cpi"] = arg(0.0, "annual growth of the periodic contribution: a number (0.03) or 'cpi' (indexed to inflation)")
    extra: list[ExtraFlow] | None = arg(None, "extraordinary dated flows [{date, amount, note}]; negative = withdrawal")
    withdrawals: float = arg(0.0, "periodic withdrawal in dollars once `withdraw_from` is reached (needs withdraw_from)")
    withdraw_from: str | None = arg(None, "start of the decumulation phase; periodic contributions stop there")
    withdraw_growth: float | Literal["cpi"] = arg(0.0, "annual growth of the withdrawal: a number or 'cpi'")
    cash_timing: Literal["invest", "hold"] = arg("invest", "'invest': a contribution buys the target weights on the next "
                                                          "trading day; 'hold': it waits in cash until the next rebalance")
    index_mer: float = arg(0.0005, "annual expense ratio of the passive fund the same flows go into")
    goal: float | None = arg(None, "target final value in dollars, nominal, after tax")
    risk_free: float | Literal["us10y", "ca10y"] = arg("us10y", "risk-free rate for Sharpe and alpha: a series name or a constant annual rate")
    inflation: float | Literal["auto", "us", "ca"] = arg("auto", "inflation: 'auto' (CA CPI if loaded, else US CPI with a warning), 'us', 'ca' or a constant annual rate")
    tax_from_outside: bool = arg(False, "pay the tax from money outside the account (the older basis: after-tax value = pre-tax value "
                                        "less the tax paid so far, never credited with growth); default: the tax is paid FROM the "
                                        "portfolio at each year's last bar, as in every other backtest and in the Monte Carlo")
    label: str = arg("", "free text carried on the result")
    tax: TaxRequest | None = arg(None, "account and tax settings {model, account, province, other_income, rrsp_withdrawal_rate, ...}; "
                                       "without tax every figure is pre-tax")
    config: ConfigOverrides | None = arg(None, "Config overrides (rebalance_months, cost_bps, ...)")


@dataclass
class PlanArgs(PlanRequest):
    """`plan`: the plan plus the response option."""
    daily: bool = arg(False, "return the full daily series instead of month-ends")


@dataclass
class PlanMCArgs(PlanRequest):
    """`plan_montecarlo`: the plan plus the study settings."""
    n_paths: int = arg(1000, "number of resampled paths")
    generator: Literal["block", "stationary", "regime", "iid", "history"] = arg("block", "return resampler: 12-month blocks, stationary bootstrap, regime blocks or iid months; "
                                                                                       "'history' replays the recorded months in order (reproduces the straight calculation)")
    seed: int = arg(0, "random seed (path k depends on the seed and k only)")
    horizon_end: str | None = arg(None, "last date of the paths (default: `end`, else the last data month)")


# ---------------------------------------------------------------------------------------------------
# request -> run keywords
# ---------------------------------------------------------------------------------------------------
def _num(name, v, lo=None, hi=None, strict_lo=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ServerError(400, f"`{name}` must be a number, got {v!r}")
    if lo is not None and (v <= lo if strict_lo else v < lo):
        raise ServerError(400, f"`{name}` must be {'>' if strict_lo else '>='} {lo}, got {v!r}")
    if hi is not None and v >= hi:
        raise ServerError(400, f"`{name}` must be < {hi}, got {v!r}")
    return float(v)


def _growth(name, v):
    if v == "cpi":
        return v
    if isinstance(v, str):
        raise ServerError(400, f"`{name}` must be a number or 'cpi', got {v!r}")
    return _num(name, v, lo=-0.5)


def _flows(extra) -> tuple:
    from ..plan import CashFlow
    out = []
    for i, x in enumerate(extra or ()):
        if isinstance(x, CashFlow):
            out.append(x)
            continue
        if not isinstance(x, dict):
            raise ServerError(400, f"`extra[{i}]` must be an object {{date, amount, note}}")
        bad = [k for k in x if k not in ("date", "amount", "note")]
        if bad:
            raise ServerError(400, f"unknown field {bad[0]!r} in extra[{i}]; allowed: ['date', 'amount', 'note']")
        if "date" not in x or "amount" not in x:
            raise ServerError(400, f"extra[{i}] needs `date` and `amount`")
        parse_date(f"extra[{i}].date", x["date"])
        out.append(CashFlow(str(x["date"])[:10], _num(f"extra[{i}].amount", x["amount"]), str(x.get("note") or "")))
    return tuple(out)


def _plan_kw(lab, a) -> dict:
    """The run keywords shared by both operations, validated (every bad field is a 400 naming the field)."""
    if lab is not None and a.screen not in lab.screens and not a.screen.lstrip().startswith("{"):
        raise ServerError(400, f"unknown screen {a.screen!r}; the lab has {sorted(lab.screens)}")
    start, end = parse_date("start", a.start), parse_date("end", a.end)
    wf = parse_date("withdraw_from", a.withdraw_from)
    if start is None:
        raise ServerError(400, "`start` is required")
    ts = pd.Timestamp(start)
    if end is not None and pd.Timestamp(end) <= ts:
        raise ServerError(400, f"`end` ({end}) must be after `start` ({start})")
    if wf is not None and pd.Timestamp(wf) <= ts:
        raise ServerError(400, f"`withdraw_from` ({wf}) must be after `start` ({start})")
    if a.withdrawals and wf is None:
        raise ServerError(400, "`withdrawals` needs `withdraw_from` (the date the decumulation phase starts)")
    initial = _num("initial", a.initial, lo=0.0)
    periodic = _num("periodic", a.periodic, lo=0.0)
    extra = _flows(a.extra)
    if initial + periodic <= 0 and not any(f.amount > 0 for f in extra):
        raise ServerError(400, "the plan has no contribution: give `initial`, `periodic` or a positive `extra` flow")
    rf = a.risk_free if isinstance(a.risk_free, str) else _num("risk_free", a.risk_free, lo=-0.5)
    if isinstance(rf, str) and rf not in ("us10y", "ca10y"):
        raise ServerError(400, f"`risk_free` must be 'us10y', 'ca10y' or a number, got {rf!r}")
    infl = a.inflation if isinstance(a.inflation, str) else _num("inflation", a.inflation, lo=-0.5)
    if isinstance(infl, str) and infl not in ("auto", "us", "ca"):
        raise ServerError(400, f"`inflation` must be 'auto', 'us', 'ca' or a number, got {infl!r}")
    cfg = config_overrides(a.config)
    if a.tax is not None:
        if not isinstance(a.tax, dict):
            raise ServerError(400, "`tax` must be an object (the fields of config.tax)")
        if cfg.get("tax") is not None:
            raise ServerError(400, "give the tax settings as `tax` or as `config.tax`, not both")
        d = {"model": "canadian", "account": "taxable", **a.tax}
        if d["model"] == "canadian":
            d.setdefault("province", "AB")
        cfg["tax"] = R._tax_config(d)
    return clean(dict(screen=a.screen, start=start, end=end, initial=initial, periodic=periodic, frequency=a.frequency,
                      growth=_growth("growth", a.growth), extra=extra or None, withdrawals=_num("withdrawals", a.withdrawals, lo=0.0),
                      withdraw_from=wf, withdraw_growth=_growth("withdraw_growth", a.withdraw_growth),
                      cash_timing=a.cash_timing, index_mer=_num("index_mer", a.index_mer, lo=0.0, hi=0.5),
                      goal=None if a.goal is None else _num("goal", a.goal, lo=0.0, strict_lo=True),
                      risk_free=rf, inflation=infl, label=a.label or None,
                      tax_from_outside=bool(a.tax_from_outside) or None, **cfg))


def make_spec(lab, screen, start, *, end=None, initial=0.0, periodic=0.0, frequency="monthly", growth=0.0, extra=None,
              withdrawals=0.0, withdraw_from=None, withdraw_growth=0.0, cash_timing="invest", index_mer=0.0005, goal=None,
              risk_free="us10y", inflation="auto", label="", tax_from_outside=False, **cfg):
    """The `plan.PlanSpec` of the keywords both operations take (`cfg`: Config overrides, `tax` included)."""
    from ..plan import PlanSpec
    return PlanSpec(screen=screen, start=str(start)[:10], config=lab._cfg(**cfg), end=None if end is None else str(end)[:10],
                    initial=initial, periodic=periodic, frequency=frequency, growth=growth, extra=_flows(extra),
                    withdrawals=withdrawals, withdraw_from=None if withdraw_from is None else str(withdraw_from)[:10],
                    withdraw_growth=withdraw_growth, cash_timing=cash_timing, index_mer=index_mer, goal=goal,
                    risk_free=risk_free, inflation=inflation, label=label, tax_from_outside=bool(tax_from_outside))


# ---------------------------------------------------------------------------------------------------
# result -> JSON
# ---------------------------------------------------------------------------------------------------
def spec_json(spec) -> dict:
    cfg = spec.config
    tx = getattr(cfg, "tax", None)
    out = {k: scalar(getattr(spec, k)) for k in ("screen", "start", "end", "initial", "periodic", "frequency", "growth",
                                                 "withdrawals", "withdraw_from", "withdraw_growth", "cash_timing", "index_mer",
                                                 "goal", "risk_free", "inflation", "tax_from_outside", "label")}
    out["extra"] = [{"date": f.date, "amount": scalar(f.amount), "note": f.note} for f in spec.extra]
    out["account"] = getattr(tx, "account", None)
    out["rebalance_months"] = getattr(cfg, "rebalance_months", None)
    return out


def tax_json(tax) -> dict | None:
    if tax is None:
        return None
    t = tax.copy()
    t.columns = [".".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
    return table(t)


def card_json(card, extra_notes=()) -> dict:
    return {"line": card.line(), "warnings": [*card.warnings(), *extra_notes]}


def series_json(series: pd.DataFrame, daily: bool) -> dict:
    cols = [c for c in SERIES_COLS if c in series]
    s = series[cols]
    if not daily:
        s = s.resample("ME").last().dropna(how="all")
    return table(s.rename_axis("date"))


def goal_json(goal) -> dict | None:
    if goal is None:
        return None
    return {k: scalar(v) for k, v in goal.items()}


def plan_json(res, daily: bool = False) -> dict:
    return {"spec": spec_json(res.spec), "metrics": table(res.metrics.rename_axis("row")),
            "series": series_json(res.series, daily), "daily": bool(daily),
            "flows": table(res.flows.reset_index(drop=True), index=False),
            "tax": tax_json(res.tax), "goal": goal_json(res.goal), "card": card_json(res.card)}


def hist_json(x: pd.Series, bins: int = HIST_BINS) -> dict:
    x = x.dropna().to_numpy(dtype=float)
    if len(x) == 0:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(x, bins=bins)
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def paired_json(paired: pd.Series) -> dict:
    p = paired.dropna()
    q = p.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {"mean": scalar(p.mean()), "std": scalar(p.std()), "share_positive": scalar((p >= 0).mean()),
            "quantiles": {str(int(k * 100)): scalar(v) for k, v in q.items()}}


def mc_json(mc, spec) -> dict:
    t = mc.terminal
    out = {"spec": spec_json(spec), "n_paths": int(mc.n_paths), "seed": int(mc.seed),
           "quantiles": table(mc.quantiles), "p_beat_index": scalar(mc.p_beat_index), "p_shortfall": scalar(mc.p_shortfall),
           "p_shortfall_real": scalar(mc.p_shortfall_real), "p_shortfall_index": scalar(mc.p_shortfall_index),
           "goal": scalar(mc.goal), "sequence": table(mc.sequence), "paired": paired_json(mc.paired),
           "reversal": None if mc.reversal is None else table(mc.reversal),
           "fan": None if mc.fan is None else table(mc.fan.rename_axis("date")),
           "histograms": {c: hist_json(t[c]) for c in ("strategy_after_tax", "index_after_tax") if c in t}
                         | {"paired": hist_json(mc.paired)},
           "card": {"line": mc.card.line(), "notes": list(mc.card.notes), "warnings": list(dict.fromkeys([*mc.card.warnings(), *mc.warnings])),
                    "generator": scalar_dict(mc.card.generator)}}
    return out


def scalar_dict(d) -> dict:
    return {str(k): (scalar_dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, (list, tuple)) else scalar(v)))
            for k, v in (d or {}).items()}


# ---------------------------------------------------------------------------------------------------
# the operations call `plan_mc.plan_montecarlo` directly (one Monte Carlo implementation; it takes a `PlanResult` so that a
# caller who has the straight run does not repeat it)
# ---------------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------------
# the operations
# ---------------------------------------------------------------------------------------------------
@register_operation(builtin=True)
class Plan(Operation):
    """A dated investment plan (lump sum, periodic, extraordinary flows, withdrawals) on a strategy against the index: wealth, XIRR, alpha / beta / Sharpe, pre-tax, after-tax and real."""
    name = "plan"
    takes_max_points = False
    registry_order = ("screen", "start", "end", "initial", "periodic", "frequency", "growth", "extra", "withdrawals",
                      "withdraw_from", "withdraw_growth", "cash_timing", "index_mer", "goal", "risk_free", "inflation",
                      "tax_from_outside", "label", "tax", "config", "lab", "daily")
    Args = PlanArgs

    def run(self, lab, screen, start, end=None, initial: float = 0.0, periodic: float = 0.0, frequency: str = "monthly",
            growth=0.0, extra=None, withdrawals: float = 0.0, withdraw_from=None, withdraw_growth=0.0,
            cash_timing: str = "invest", index_mer: float = 0.0005, goal=None, risk_free="us10y", inflation="auto",
            label: str = "", tax_from_outside: bool = False, daily: bool = False, **cfg):
        """The straight calculation of a plan (see plan.py, docs/PLAN.md); returns a `plan.PlanResult`.

        `screen` is a screen name, `start` the first investment date; `extra` is a list of `CashFlow` or
        `{date, amount, note}`; `tax` (a `TaxConfig`, in `cfg`) puts the plan in an account; other `cfg` keys override
        the lab's Config. `daily` only affects the JSON (full daily series). Pure computation on the lab's data."""
        from ..plan import run_plan
        return run_plan(lab, make_spec(lab, screen, start, end=end, initial=initial, periodic=periodic, frequency=frequency,
                                       growth=growth, extra=extra, withdrawals=withdrawals, withdraw_from=withdraw_from,
                                       withdraw_growth=withdraw_growth, cash_timing=cash_timing, index_mer=index_mer,
                                       goal=goal, risk_free=risk_free, inflation=inflation, label=label,
                                       tax_from_outside=tax_from_outside, **cfg))

    def build(self, lab, a) -> dict:
        return {**_plan_kw(lab, a), **({"daily": True} if a.daily else {})}

    def to_json(self, result, max_points=500, *, lab=None, kw=None):
        return plan_json(result, bool((kw or {}).get("daily")))


@register_operation(builtin=True)
class PlanMonteCarlo(Operation):
    """Monte Carlo study of a plan: after-tax terminal wealth of the strategy and the index on resampled paths, P(beat the index), P(shortfall vs the goal), sequence of returns."""
    name = "plan_montecarlo"
    long_running = True
    takes_max_points = False
    registry_order = Plan.registry_order[:-1] + ("n_paths", "generator", "seed", "horizon_end")
    Args = PlanMCArgs

    def run(self, lab, screen, start, end=None, initial: float = 0.0, periodic: float = 0.0, frequency: str = "monthly",
            growth=0.0, extra=None, withdrawals: float = 0.0, withdraw_from=None, withdraw_growth=0.0,
            cash_timing: str = "invest", index_mer: float = 0.0005, goal=None, risk_free="us10y", inflation="auto",
            label: str = "", tax_from_outside: bool = False, n_paths: int = 1000, generator: str = "block", seed: int = 0,
            horizon_end=None, result=None, **cfg):
        """Monte Carlo study of a plan (montecarlo/plan_mc.py); returns a `plan_mc.PlanMC`. Arguments as `plan`, plus
        the number of paths, the resampler (block | stationary | regime | iid | history), the seed and the last date of the
        paths. `result`: the `plan.PlanResult` of the same plan, when the caller has it (not repeated)."""
        spec = make_spec(lab, screen, start, end=end, initial=initial, periodic=periodic, frequency=frequency,
                         growth=growth, extra=extra, withdrawals=withdrawals, withdraw_from=withdraw_from,
                         withdraw_growth=withdraw_growth, cash_timing=cash_timing, index_mer=index_mer, goal=goal,
                         risk_free=risk_free, inflation=inflation, label=label, tax_from_outside=tax_from_outside, **cfg)
        from ..montecarlo.plan_mc import plan_montecarlo
        mc = plan_montecarlo(lab, spec, n_paths=n_paths, generator=generator, seed=seed, horizon_end=horizon_end, result=result)
        mc.spec = spec
        return mc

    def build(self, lab, a) -> dict:
        if isinstance(a.n_paths, bool) or not isinstance(a.n_paths, int) or a.n_paths < 2:
            raise ServerError(400, f"`n_paths` must be an integer >= 2, got {a.n_paths!r}")
        he = parse_date("horizon_end", a.horizon_end)
        if he is not None and pd.Timestamp(he) <= pd.Timestamp(a.start):
            raise ServerError(400, f"`horizon_end` ({he}) must be after `start` ({a.start})")
        if a.generator not in ("block", "stationary", "regime", "iid", "history"):
            raise ServerError(400, f"`generator` must be block, stationary, regime, iid or history, got {a.generator!r}")
        return {**_plan_kw(lab, a), "n_paths": a.n_paths, "generator": a.generator, "seed": a.seed,
                **({"horizon_end": he} if he else {})}

    def to_json(self, result, max_points=500, *, lab=None, kw=None):
        return mc_json(result, result.spec)


# ---------------------------------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------------------------------
def _extract_plan(obj, j, out):
    out.summary[j("metrics")] = obj.metrics
    out.bulk[j("series")] = obj.series
    if obj.tax is not None:
        out.bulk[j("tax")] = obj.tax


def _extract_mc(obj, j, out):
    p = {"p_beat_index": obj.p_beat_index, "p_shortfall": obj.p_shortfall, "p_shortfall_real": obj.p_shortfall_real,
         "p_shortfall_index": obj.p_shortfall_index}
    out.summary[j("probabilities")] = pd.DataFrame({"value": {k: v for k, v in p.items() if v is not None}}).T.rename(index={"value": "all"})
    out.summary[j("quantiles")] = obj.quantiles
    out.summary[j("sequence")] = obj.sequence
    out.bulk[j("terminal")] = obj.terminal
    if obj.fan is not None:
        out.bulk[j("fan")] = obj.fan


def _register_extractors():
    from .. import store
    store.register_extractor("PlanResult", _extract_plan)
    store.register_extractor("PlanMC", _extract_mc)


_register_extractors()
