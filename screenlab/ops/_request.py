"""Request-side helpers shared by the built-in operations: the typed JSON request pieces (Config overrides,
exit policy, splits, macro input, ...) and the functions that turn them into live objects, with the
400-style validation messages the servers return."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal, Union

import pandas as pd

from ..errors import ServerError
from ..operations import arg, model_hook


# ---------------------------------------------------------------------------------------------------
# typed request pieces (declarative: the functions below read plain dicts)
# ---------------------------------------------------------------------------------------------------
@dataclass
class HoldRuleRequest:
    """Turnover control (HoldRule): keep_within / band."""
    keep_within: float | None = arg(None, "keep a holding while it ranks within keep_within * n (a number >= 1)")
    band: float | None = arg(None, "leave a name untraded while its weight is within +-band (relative) of target (>= 0)")


@dataclass
class ConfigOverrides:
    """Config fields a request may override for one call."""
    rebalance_months: int | None = arg(None, "months between rebalances")
    min_holdings: int | None = arg(None, "fewer passes than this hold cash")
    commission: float | None = arg(None, "flat $ per trade and side")
    capital: float | None = arg(None, "starting $ (matters for flat commissions)")
    delist_return: float | None = arg(None, "return applied when a holding vanishes mid-period")
    cash_rate: float | None = arg(None, "annual return on cash")
    cost_bps: float | dict[str, float] | None = arg(None, "one-way cost per security in bps: a number or {ticker: bps} with an optional _default")
    mer: float | dict[str, float] | None = arg(None, "annual expense ratio per security: a number or {ticker: value}")
    max_weight: float | None = arg(None, "per-name weight cap")
    sector_cap: float | None = arg(None, "per-sector weight cap")
    weighting: Literal["equal", "cap", "inv_vol"] | None = arg(None, "weighting rule: equal, cap or inv_vol")
    hold_rule: HoldRuleRequest | None = arg(None, "turnover control: {keep_within, band}")


@dataclass
class BenchmarkRequest:
    """A passive-index benchmark with options (instead of a bare index name)."""
    index: str = arg(doc="index name or ticker, e.g. SP500")
    dividend_yield: float | None = arg(None, "annual dividend yield added back to a PRICE index, e.g. 0.02: "
                                             "the benchmark becomes an approximate total return")
    mer: float | None = arg(None, "annual expense ratio of the fund tracking it")
    cost_bps: float | None = arg(None, "one-time purchase cost in bps")
    kind: Literal["total_return", "price", "approx_total_return"] | None = arg(
        None, "override the dataset's record of what the series is")


class SignalSpec:
    """Marker annotation: one exit signal, `{"type": <name>, <parameters>}` (see list_signals)."""


@dataclass
class ScreenParams:
    """ExitScreen fields other than signals."""
    weights: dict[str, float] | None = arg(None, "signal name -> weight (default equal)")
    threshold: float | None = arg(None, "flag turns on at this score")
    min_votes: int | None = arg(None, "signals individually at or above the threshold needed to flag")
    hysteresis: float | None = arg(None, "flag turns off below threshold - hysteresis")
    check: str | None = arg(None, "decision cadence: D, W-FRI, ME, ...")
    lag: int | None = arg(None, "execution lag in bars")
    min_hold_days: int | None = arg(None, "calendar days a state is kept after it changes")


@dataclass
class PolicyRequest:
    """ExitPolicy fields (the exit screen itself comes from `signals`)."""
    exposure_when_flagged: float | None = arg(None, "equity exposure while flagged (0 = fully out)")
    park: str | float | None = arg(None, "'cash', an annual rate, or an index / ticker name")
    park_mer: float | None = arg(None, "annual fee on the parked money")
    park_cost_bps: float | None = arg(None, "cost on |change of parked share|, bps")
    switch_cost_bps: float | None = arg(None, "cost on |change of exposure|, bps")
    reentry: Literal["signal", "next_rebalance", "signal_or_rebalance"] | None = arg(None, "re-entry mode")
    reentry_delay_days: int | None = arg(None, "days the flag must stay clear before re-entry")
    reentry_steps: int | None = arg(None, "scale back in over this many check periods")
    rescreen_on_reentry: bool | None = arg(None, "run the entry screen fresh on re-entry")


@dataclass
class MacroRecord:
    date: str = arg(doc="observation date")
    series: str = arg(doc="series name (cpi, fx, prime, rf, ...)")
    value: float = arg(doc="value")


MacroInput = Union[list[MacroRecord], dict[str, dict[str, float]], Literal["statsmodels"]]


@dataclass
class ExitRequest:
    """An exit policy: `signals` + exit-screen fields + policy fields (applied to every screen of a backtest)."""
    signals: list[SignalSpec] = arg(doc="exit signals, see list_signals")
    weights: dict[str, float] | None = arg(None, "signal name -> weight")
    threshold: float | None = arg(None, "flag turns on at this score")
    min_votes: int | None = arg(None, "signals needed to flag")
    hysteresis: float | None = arg(None, "flag turns off below threshold - hysteresis")
    screen_params: ScreenParams | None = arg(None, "other ExitScreen fields")
    exposure_when_flagged: float | None = arg(None, "equity exposure while flagged")
    park: str | float | None = arg(None, "'cash', an annual rate, or an index / ticker name")
    park_mer: float | None = arg(None, "annual fee on the parked money")
    park_cost_bps: float | None = arg(None, "cost on parked-share changes, bps")
    switch_cost_bps: float | None = arg(None, "cost on exposure changes, bps")
    reentry: Literal["signal", "next_rebalance", "signal_or_rebalance"] | None = arg(None, "re-entry mode")
    reentry_delay_days: int | None = arg(None, "days the flag must stay clear")
    reentry_steps: int | None = arg(None, "scale back in over k check periods")
    rescreen_on_reentry: bool | None = arg(None, "run the entry screen fresh on re-entry")
    macro: MacroInput | None = arg(None, "macro series for the signals")


@dataclass
class SplitsRequest:
    """Walk-forward windows."""
    train_years: float = arg(doc="length of each train window")
    test_years: float = arg(doc="length of each test window")
    step_years: float | None = arg(None, "advance per window (default test_years)")
    expanding: bool | None = arg(None, "train from the first date onward")


@dataclass
class ConstraintsRequest:
    """Calibration feasibility constraints."""
    max_switches_per_year: float | None = arg(None, "most exposure switches per year")
    max_time_flagged: float | None = arg(None, "largest share of time flagged")
    min_time_flagged: float | None = arg(None, "smallest share of time flagged")


class GrahamOverrides:
    """Marker annotation: GrahamTests fields (null switches a test off)."""


@model_hook(SignalSpec)
def _signal_union():
    """A discriminated union of the built-in signals (rebuilt from BUILTINS), or a plain name."""
    import typing
    from pydantic import ConfigDict, Field, create_model
    from .. import exit_signals as es
    from ..exit_signals import _signal_classes
    models = []
    for name, cls in sorted(_signal_classes().items()):
        try:
            hints = typing.get_type_hints(cls)
        except Exception:
            hints = {}
        flds: dict = {"type": (Literal[name], Field(..., description=f"signal type {name}"))}
        for f in dataclasses.fields(cls):
            if not f.init:
                continue
            unit = getattr(cls, "units", {}).get(f.name)
            flds[f.name] = (hints.get(f.name, Any),
                            Field(None if f.default is dataclasses.MISSING else f.default,
                                  description=f"unit: {unit}" if unit else None))
        for old, (new, _, _) in getattr(cls, "_renamed", {}).items():
            flds[old] = (float | None, Field(None, description=f"deprecated; use {new}"))
        models.append(create_model(f"Signal_{name}", __config__=ConfigDict(extra="forbid"),
                                   __doc__=(cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else None,
                                   **flds))
    names = Literal[tuple(sorted(_signal_classes()))]
    if not models:
        return str
    return Union[Annotated_union(models), names]


def Annotated_union(models):
    import typing
    from pydantic import Field
    u = Union[tuple(models)]
    return typing.Annotated[u, Field(discriminator="type")]


@model_hook(GrahamOverrides)
def _graham_overrides():
    import typing
    from pydantic import ConfigDict, Field, create_model
    from ..screens import GrahamTests
    hints = typing.get_type_hints(GrahamTests)
    flds = {f.name: (hints.get(f.name, Any) | None, Field(None)) for f in dataclasses.fields(GrahamTests)}
    return create_model("GrahamOverrides", __config__=ConfigDict(extra="forbid"),
                        __doc__="GrahamTests fields; null switches a test off.", **flds)


# ---------------------------------------------------------------------------------------------------
# request -> live objects
# ---------------------------------------------------------------------------------------------------
CONFIG_INT = ("rebalance_months", "min_holdings")
CONFIG_NUM = ("commission", "capital", "delist_return", "cash_rate")
CONFIG_NUM_OR_MAP = ("cost_bps", "mer")
CONFIG_OPT_NUM = ("max_weight", "sector_cap")
CONFIG_FIELDS = CONFIG_INT + CONFIG_NUM + CONFIG_NUM_OR_MAP + CONFIG_OPT_NUM + ("weighting", "hold_rule")
POLICY_FIELDS = ("exposure_when_flagged", "park", "park_mer", "park_cost_bps", "switch_cost_bps", "reentry",
                 "reentry_delay_days", "reentry_steps", "rescreen_on_reentry")


def clean(kw: dict) -> dict:
    """Keyword arguments without the None entries (an omitted argument and None mean the same everywhere)."""
    return {k: v for k, v in kw.items() if v is not None}


def _hold_rule(v):
    """A HoldRule from `{"keep_within": float|null, "band": float|null}` (or None / a HoldRule), else a 400."""
    from ..construction import HoldRule
    if v is None or isinstance(v, HoldRule):
        return v
    if not isinstance(v, dict):
        raise ServerError(400, f"config.hold_rule must be an object {{keep_within, band}}, got {v!r}")
    for k in v:
        if k not in ("keep_within", "band"):
            raise ServerError(400, f"unknown config.hold_rule field {k!r}; use ['keep_within', 'band']")
    kw = {}
    for k, lo, word in (("keep_within", 1, ">= 1"), ("band", 0, ">= 0")):
        x = v.get(k)
        if x is None:
            continue
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x < lo:
            raise ServerError(400, f"config.hold_rule.{k} must be a number {word}, got {x!r}")
        kw[k] = x
    return HoldRule(**kw)


def config_overrides(config) -> dict:
    """Validated Config overrides from a request: unknown keys and wrongly typed values are a 400 that
    names the field. `exit` is not a config field here: pass the top-level `exit` object."""
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ServerError(400, "`config` must be an object of Config overrides")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    out = dict(config)
    for k, v in config.items():
        if k == "exit":
            raise ServerError(400, "config.exit is not allowed; pass the top-level `exit` object")
        if k not in CONFIG_FIELDS:
            raise ServerError(400, f"unknown config field {k!r}; use {list(CONFIG_FIELDS)}")
        if k in CONFIG_INT and not (isinstance(v, int) and not isinstance(v, bool)):
            raise ServerError(400, f"config.{k} must be an integer, got {v!r}")
        if k in CONFIG_NUM and not num(v):
            raise ServerError(400, f"config.{k} must be a number, got {v!r}")
        if k in CONFIG_NUM_OR_MAP and not (num(v) or isinstance(v, dict)):
            raise ServerError(400, f"config.{k} must be a number or a {{ticker: value}} object, got {v!r}")
        if k in CONFIG_OPT_NUM and v is not None and not num(v):
            raise ServerError(400, f"config.{k} must be a number or null, got {v!r}")
        if k == "weighting":
            from ..construction import WEIGHTINGS
            if v not in WEIGHTINGS:
                raise ServerError(400, f"config.weighting must be one of {sorted(WEIGHTINGS)}, got {v!r}")
        if k == "hold_rule":
            out[k] = _hold_rule(v)
    return out


def parse_date(name, v):
    """`v` unchanged if it parses as a date (or is None), else a 400 naming the field."""
    if v is None:
        return None
    try:
        if pd.isna(pd.Timestamp(v)):
            raise ValueError(v)
    except (ValueError, TypeError, OverflowError):
        raise ServerError(400, f"`{name}` is not a valid date: {v!r}") from None
    return v


def benchmarks(benchmark, compare):
    """(IndexBenchmark or None, [extra benchmarks]) from request strings: "topN" is never primary."""
    from ..benchmarks import IndexBenchmark, parse_benchmark as mk
    for name, v in (("benchmark", benchmark), ("compare", compare)):
        ok = (str, list, tuple) if name == "compare" else (str, dict)
        if v is not None and not isinstance(v, ok):
            raise ServerError(400, f"`{name}` must be a string" + (" or a list of strings" if name == "compare"
                              else " or an object {\"index\", \"dividend_yield\", \"mer\", \"cost_bps\"}"))
    extra = [compare] if isinstance(compare, str) else list(compare or [])
    if isinstance(benchmark, dict):
        try:
            b = mk({k: v for k, v in benchmark.items() if v is not None})
        except (ValueError, TypeError) as e:
            raise ServerError(400, f"bad `benchmark`: {e}") from None
        return b, [mk(x) for x in extra]
    if isinstance(benchmark, str):
        b = mk(benchmark)
        if isinstance(b, IndexBenchmark):
            return b, [mk(x) for x in extra]
        extra = [benchmark, *extra]
    return None, [mk(x) for x in extra]


def macro_from(macro, L):
    if macro is None:
        return None
    from ..macro import MacroSeries
    try:
        index = L.market.index if L is not None else None
        if macro == "statsmodels":
            m = MacroSeries.from_statsmodels_macrodata(index)
            m._ref, m._align = "statsmodels", True         # runspec: by reference, aligned to the market
            return m
        if isinstance(macro, list):
            df = pd.DataFrame(macro).pivot_table(index="date", columns="series", values="value", aggfunc="last")
            df.index = pd.to_datetime(df.index)
            return MacroSeries.from_frame(df, index)
        if isinstance(macro, dict):
            df = pd.DataFrame({k: pd.Series(v) for k, v in macro.items()})
            df.index = pd.to_datetime(df.index)
            return MacroSeries.from_frame(df, index)
    except (ValueError, KeyError, TypeError) as e:
        raise ServerError(400, f"bad macro payload: {e}") from e
    raise ServerError(400, "macro must be a record list, a {series: {date: value}} mapping, or \"statsmodels\"")


def call(fn, *a, **kw):
    """Run an SDK call, mapping bad input to 400 / unknown names to 404 (see server.registry._translate)."""
    from ..server.registry import _translate
    try:
        return fn(*a, **kw)
    except ServerError:
        raise
    except Exception as e:
        raise _translate(e) from e


def policy_from(es, spec, exposure_when_flagged):
    """ExitPolicy from an ExitScreen and a dict of policy fields."""
    from ..exit_policy import ExitPolicy
    bad = set(spec) - set(POLICY_FIELDS)
    if bad:
        raise ServerError(400, f"unknown policy fields {sorted(bad)}; use {list(POLICY_FIELDS)}")
    kw = {"exposure_when_flagged": exposure_when_flagged, **spec}
    return call(ExitPolicy, es, **kw)


def exit_screen(L, signals, weights, threshold, min_votes, hysteresis, screen_params):
    from .. import exit_signals as es
    from ..exit_signals import _signal_classes
    known = _signal_classes()
    sigs = []
    for s in signals or []:
        s = {"type": s} if isinstance(s, str) else dict(s)
        t = s.pop("type", None)
        if t not in known:
            raise ServerError(400, f"unknown signal {t!r}; available: {sorted(known)}")
        cls = known[t]
        fields = {f.name for f in dataclasses.fields(cls)}
        if L is not None and "index" in fields and "index" not in s and "SP500" not in L.market.indices.columns \
                and len(L.market.indices.columns):
            s["index"] = str(L.market.indices.columns[0])      # lab's own index instead of the "SP500" default
        try:
            sigs.append(cls(**s))
        except TypeError as e:
            raise ServerError(400, f"bad params for {t}: {e}") from e
    if not sigs:
        raise ServerError(400, "`signals` must list at least one signal (see list_signals)")
    extra = {k: v for k, v in (screen_params or {}).items()
             if k in {f.name for f in dataclasses.fields(es.ExitScreen)}}
    return es.ExitScreen(sigs, weights=weights, threshold=threshold, min_votes=min_votes,
                         hysteresis=hysteresis, **extra)


def exit_policy(L, spec):
    """ExitPolicy from a backtest `exit` object: signals + screen fields + policy fields."""
    spec = dict(spec)
    sig = spec.pop("signals", None)
    macro = macro_from(spec.pop("macro", None), L)
    screen_keys = ("weights", "threshold", "min_votes", "hysteresis", "screen_params")
    sk = {k: spec.pop(k) for k in screen_keys if k in spec}
    es = exit_screen(L, sig, sk.get("weights"), sk.get("threshold", 0.7), sk.get("min_votes"),
                     sk.get("hysteresis", 0.1), sk.get("screen_params"))
    pol = policy_from(es, spec, spec.pop("exposure_when_flagged", 0.0))
    pol.macro = macro
    return pol
