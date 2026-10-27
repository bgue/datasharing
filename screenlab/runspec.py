"""A run as plain data: `RunSpec`, JSON serialization of every input type, and a stable `run_key`.

    spec = RunSpec("backtest", "sp500", {"screens": ["dogs", Portfolio("h", "dogs", exit=policy)],
                                         "rebalance_months": 6, "cost_bps": cap_tiered({0: 75, 1000: 30})})
    spec.persistable                  # False if any input is a Python callable we cannot rebuild
    key = spec.run_key(dataset_id(lab.market))          # sha256 of kind + args + seed + data + code
    d = spec.to_dict(); RunSpec.from_dict(json.loads(json.dumps(d)))    # round trip

Serialization (`to_dict` / `from_dict`) is a tagged-dict encoding: JSON natives stay as they are, anything
else becomes `{"__t": "<Tag>", ...}` (tuples, dates, non-string-keyed dicts, Series, dataclasses by name,
signals by `BUILTINS` name + params, screens as a built-in name + overrides or as their ScreenSpec dict,
`cap_tiered` closures as their tiers, MacroSeries by reference). Defaults are omitted, so adding a field
with a default to a dataclass does not change existing keys. The encoding is idempotent: `to_dict` of an
already serialized value returns it unchanged, so a spec may hold live objects or their JSON.

Things that cannot be rebuilt (a Python function weighting, a custom cost function, `Screen(fn=...)`, a
custom signal, a MacroSeries without a registered reference) become `{"__t": "unpersistable", "reason"}`.
Such a spec still runs (in-process) but has `persistable == False`, no `run_key`, and no cache reuse.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import functools
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__version__ = "0.1.0"        # fallback; the package's own __version__ wins when it defines one

TAG = "__t"
BUNDLED_KINDS = ("backtest", "timing", "sweep", "significance", "extended", "exit_evaluate",
                 "exit_calibrate", "graham", "picks")


class Unpersistable(ValueError):
    """Raised by `from_dict` on an input that was recorded as unpersistable."""


# ---------------------------------------------------------------------------------------------------
# macro references
# ---------------------------------------------------------------------------------------------------
MACRO_LOADERS: dict = {}


def register_macro(name: str, loader) -> None:
    """Make `name` resolvable: `loader()` returns the MacroSeries (called when a spec is rebuilt)."""
    MACRO_LOADERS[name] = loader


def macro_ref(macro, name: str):
    """Tag a MacroSeries with the reference its specs will carry (and register `macro` as its loader if
    nothing is registered under that name yet). Returns `macro`."""
    macro._ref = name
    MACRO_LOADERS.setdefault(name, lambda m=macro: m)
    return macro


def _builtin_macros():
    from .macro import MacroSeries
    return {"statsmodels": MacroSeries.from_statsmodels_macrodata}


def _resolve_macro(ref: str):
    if ref in MACRO_LOADERS:
        return MACRO_LOADERS[ref]()
    b = _builtin_macros()
    if ref in b:
        m = b[ref]()
        m._ref = ref
        return m
    raise Unpersistable(f"unknown macro reference {ref!r}; register it with runspec.register_macro")


# ---------------------------------------------------------------------------------------------------
# class registry (lazy: runspec must import without pulling every module at package import)
# ---------------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _dataclass_tags() -> dict:
    from .backtest import Config
    from .benchmarks import IndexBenchmark
    from .construction import HoldRule
    from .exit_policy import ExitPolicy
    from .exit_signals import ExitScreen
    from .screens import GrahamTests
    from .spec import ScreenSpec
    return {"Config": Config, "HoldRule": HoldRule, "ExitPolicy": ExitPolicy, "ExitScreen": ExitScreen,
            "IndexBenchmark": IndexBenchmark, "GrahamTests": GrahamTests, "ScreenSpec": ScreenSpec}


def _builtins():
    from . import exit_inputs  # noqa: F401  (registers its signals into BUILTINS)
    from . import exit_signals
    return dict(exit_signals.BUILTINS) if isinstance(exit_signals.BUILTINS, dict) else {
        c.__name__: c for c in exit_signals.BUILTINS}


_SKIP_FIELDS = {"Config": ("exit_overlay",)}


def _unp(reason: str, **extra) -> dict:
    return {TAG: "unpersistable", "reason": reason, **extra}


# ---------------------------------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------------------------------
def _is_default(f: dataclasses.Field, v) -> bool:
    if f.default is not dataclasses.MISSING:
        d = f.default
    elif f.default_factory is not dataclasses.MISSING:
        d = f.default_factory()
    else:
        return False
    if isinstance(v, (pd.Series, pd.DataFrame, np.ndarray)) or isinstance(d, (pd.Series, pd.DataFrame, np.ndarray)):
        return False
    if type(v) is not type(d):
        return False
    try:
        return bool(v == d)
    except Exception:
        return False


def _cap_tiers(fn):
    """The tiers of a `backtest.cap_tiered` closure, or None for any other callable."""
    if getattr(fn, "__qualname__", "") != "cap_tiered.<locals>.f" or not getattr(fn, "__closure__", None):
        return None
    names = fn.__code__.co_freevars
    cells = dict(zip(names, fn.__closure__))
    try:
        t = cells["tiers"].cell_contents
        return {float(k): float(v) for k, v in dict(t).items()}
    except Exception:
        return None


def _hash_frame(frame) -> str:
    h = hashlib.sha256(repr(list(map(str, frame.columns))).encode())
    h.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    return h.hexdigest()[:20]


def _dc_to_dict(tag: str, obj) -> dict:
    out = {TAG: tag}
    for f in dataclasses.fields(obj):
        if not f.init or f.name in _SKIP_FIELDS.get(tag, ()):
            continue
        v = getattr(obj, f.name)
        if _is_default(f, v):
            continue
        out[f.name] = to_dict(v)
    for name in _SKIP_FIELDS.get(tag, ()):
        if getattr(obj, name, None) is not None:
            out[name] = _unp(f"{name} is not supported by RunSpec (use ExitPolicy)")
    return out


def _screen_to_dict(s) -> dict:
    from .screens import REGISTRY
    from .spec import rule_screen
    base = REGISTRY.get(s.name)
    if base is not None and base.fn is s.fn:
        ov: dict = {}
        if s.n != base.n:
            ov["n"] = s.n
        for k, v in s.params.items():
            bv = base.params.get(k)
            if k == "tests" and bv is not None and dataclasses.is_dataclass(v):
                for f in dataclasses.fields(v):
                    if getattr(v, f.name) != getattr(bv, f.name):
                        ov[f.name] = getattr(v, f.name)
                continue
            try:
                same = k in base.params and bool(v == bv)
            except Exception:
                same = False
            if not same:
                ov[k] = v
        return {TAG: "Screen", "name": s.name, **({"overrides": to_dict(ov)} if ov else {})}
    if s.fn is rule_screen:
        spec_keys = {"universe", "derive", "filters", "graham", "rank"}
        if set(s.params) <= spec_keys:
            spec = {"name": s.name, "n": s.n, "note": "" if s.note == "spec screen" else s.note, **s.params}
            return {TAG: "Screen", "spec": to_dict(spec)}
        return {TAG: "Screen", "rule": {"name": s.name, "n": s.n, "note": s.note, "params": to_dict(dict(s.params))}}
    return _unp(f"Screen {s.name!r} wraps a Python function (fn={getattr(s.fn, '__name__', s.fn)!r})",
                name=s.name)


def to_dict(obj):
    """Serialize `obj` to JSON-able tagged dicts (see the module docstring). Idempotent."""
    from .exit_policy import Portfolio
    from .benchmarks import ScreenBenchmark
    from .macro import MacroSeries
    from .screens import Screen
    from .sweep import Grid

    if obj is None or isinstance(obj, (str, bool, np.bool_)):
        return None if obj is None else (obj if isinstance(obj, str) else bool(obj))
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else {TAG: "float", "v": repr(f)}
    if obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, _dt.datetime, _dt.date, np.datetime64)):
        t = pd.Timestamp(obj)
        return None if pd.isna(t) else {TAG: "date", "v": t.isoformat()[:10] if t == t.normalize() else t.isoformat()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, tuple):
        return {TAG: "tuple", "v": [to_dict(v) for v in obj]}
    if isinstance(obj, (set, frozenset)):
        return {TAG: "set", "v": sorted((to_dict(v) for v in obj), key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(obj, dict):
        if TAG in obj:                                    # already serialized
            return {k: to_dict(v) for k, v in obj.items()}
        if all(isinstance(k, str) for k in obj):
            return {k: to_dict(v) for k, v in obj.items()}
        return {TAG: "dict", "items": [[to_dict(k), to_dict(v)] for k, v in obj.items()]}
    if isinstance(obj, pd.Series):
        dt = isinstance(obj.index, pd.DatetimeIndex)
        idx = [pd.Timestamp(i).isoformat() for i in obj.index] if dt else [to_dict(i) for i in obj.index]
        vals = ([None if pd.isna(v) else float(v) for v in obj.to_numpy()] if obj.dtype.kind in "fiu"
                else [to_dict(v) for v in obj.tolist()])
        return {TAG: "Series", "name": None if obj.name is None else str(obj.name), "dt_index": dt,
                "index": idx, "values": vals}
    if isinstance(obj, MacroSeries):
        ref = getattr(obj, "_ref", None)
        if ref is None:
            return _unp("MacroSeries has no dataset reference (tag it with runspec.macro_ref)")
        return {TAG: "MacroSeries", "ref": ref, "hash": _hash_frame(obj.frame),
                **({"align": True} if getattr(obj, "_align", False) else {})}
    if isinstance(obj, Grid):
        return {TAG: "Grid", "spec": to_dict(obj.spec)}
    if isinstance(obj, Portfolio):
        return {TAG: "Portfolio", "name": obj.name, "screen": to_dict(obj.screen),
                **({"exit": to_dict(obj.exit)} if obj.exit is not None else {}),
                **({"overrides": to_dict(obj.config_overrides)} if obj.config_overrides else {})}
    if isinstance(obj, Screen):
        return _screen_to_dict(obj)
    if isinstance(obj, ScreenBenchmark):
        sc = obj.screen
        if sc.fn.__qualname__ == "benchmark_screen.<locals>.<lambda>" and sc.name == f"bench_top{sc.n}":
            out = {TAG: "ScreenBenchmark", "top": int(sc.n)}
        else:
            out = {TAG: "ScreenBenchmark", "screen": _screen_to_dict(sc)}
        if obj.name:
            out["name"] = obj.name
        return out
    # signals: by BUILTINS name + params
    cls = type(obj)
    b = _builtins()
    if b.get(cls.__name__) is cls and dataclasses.is_dataclass(obj):
        params = {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)
                  if f.init and not _is_default(f, getattr(obj, f.name))}
        return {TAG: "Signal", "type": cls.__name__, "params": params}
    if not isinstance(obj, type) and dataclasses.is_dataclass(obj):
        for tag, cls in _dataclass_tags().items():
            if type(obj) is cls:
                return _dc_to_dict(tag, obj)
        return _unp(f"{type(obj).__name__} is not a serializable input type")
    if isinstance(obj, np.ndarray):
        return [to_dict(v) for v in obj.tolist()]
    if callable(obj):
        tiers = _cap_tiers(obj)
        if tiers is not None:
            return {TAG: "cap_tiered", "tiers": [[k, v] for k, v in sorted(tiers.items())]}
        return _unp(f"Python callable {getattr(obj, '__qualname__', repr(obj))!r}")
    return _unp(f"{cls.__name__} is not a serializable input type")


# ---------------------------------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------------------------------
def from_dict(d):
    """Inverse of `to_dict`. Raises `Unpersistable` on a value recorded as unpersistable."""
    if isinstance(d, list):
        return [from_dict(v) for v in d]
    if not isinstance(d, dict):
        return d
    tag = d.get(TAG)
    if tag is None:
        return {k: from_dict(v) for k, v in d.items()}
    if tag == "unpersistable":
        raise Unpersistable(d.get("reason", "unpersistable input"))
    if tag == "float":
        return float(d["v"])
    if tag == "date":
        return pd.Timestamp(d["v"])
    if tag == "tuple":
        return tuple(from_dict(v) for v in d["v"])
    if tag == "set":
        return set(from_dict(v) for v in d["v"])
    if tag == "dict":
        return {from_dict(k): from_dict(v) for k, v in d["items"]}
    if tag == "Series":
        idx = pd.DatetimeIndex(d["index"]) if d["dt_index"] else pd.Index([from_dict(i) for i in d["index"]])
        vals = [np.nan if v is None else v for v in d["values"]]
        vals = [from_dict(v) for v in vals] if not all(isinstance(v, (int, float)) for v in vals) else vals
        return pd.Series(vals, index=idx, name=d.get("name"))
    if tag == "cap_tiered":
        from .backtest import cap_tiered
        return cap_tiered({k: v for k, v in d["tiers"]})
    if tag == "MacroSeries":
        m = _resolve_macro(d["ref"])
        if d.get("align"):
            m = copy.copy(m)
            m._align = True                                # execute_spec aligns it to the lab's market index
        return m
    if tag == "Grid":
        from .sweep import Grid
        return Grid(from_dict(d["spec"]))
    if tag == "Portfolio":
        from .exit_policy import Portfolio
        return Portfolio(d["name"], from_dict(d["screen"]), from_dict(d["exit"]) if "exit" in d else None,
                         **from_dict(d.get("overrides", {})))
    if tag == "Screen":
        return _screen_from_dict(d)
    if tag == "ScreenBenchmark":
        from .benchmarks import ScreenBenchmark, equal_weight_top
        b = equal_weight_top(d["top"]) if "top" in d else ScreenBenchmark(_screen_from_dict(d["screen"]))
        if d.get("name"):
            b.name = d["name"]
        return b
    if tag == "Signal":
        b = _builtins()
        if d["type"] not in b:
            raise Unpersistable(f"unknown signal {d['type']!r}")
        return b[d["type"]](**{k: from_dict(v) for k, v in d["params"].items()})
    classes = _dataclass_tags()
    if tag in classes:
        cls = classes[tag]
        kw = {k: from_dict(v) for k, v in d.items() if k != TAG}
        if tag == "GrahamTests" and "utility_sectors" in kw:
            kw["utility_sectors"] = tuple(kw["utility_sectors"])
        return cls(**kw)
    raise ValueError(f"unknown type tag {tag!r}")


def _screen_from_dict(d):
    from .screens import REGISTRY, Screen
    from .spec import ScreenSpec, rule_screen
    if "name" in d and TAG in d and "spec" not in d and "rule" not in d:
        if d["name"] not in REGISTRY:
            raise Unpersistable(f"unknown built-in screen {d['name']!r}")
        ov = from_dict(d.get("overrides", {}))
        base = REGISTRY[d["name"]]
        return base.with_overrides(**ov) if ov else base
    if "spec" in d:
        return ScreenSpec(**from_dict(d["spec"])).build()
    if "rule" in d:
        r = d["rule"]
        return Screen(r["name"], rule_screen, r["n"], from_dict(r["params"]), r["note"])
    raise Unpersistable(d.get("reason", "unpersistable screen"))


# ---------------------------------------------------------------------------------------------------
# persistability, canonical JSON, keys
# ---------------------------------------------------------------------------------------------------
def unpersistable_reasons(d) -> list[str]:
    """Reasons found in a serialized value (empty list = fully persistable)."""
    out: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            if x.get(TAG) == "unpersistable":
                out.append(x.get("reason", "unpersistable input"))
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    return out


def _canon(x):
    if isinstance(x, dict):
        if x.get(TAG) == "tuple":
            return [_canon(v) for v in x["v"]]
        return {k: _canon(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_canon(v) for v in x]
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return int(x)                                  # 25.0 and 25 are the same run
    return x


def canonical_json(obj) -> str:
    """Stable JSON of a (serialized) value: sorted keys, no whitespace, tuples as lists, integral floats
    as ints, other floats via repr, dates as ISO strings. Identical across processes and dict orderings."""
    return json.dumps(_canon(to_dict(obj)), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _screen_names(d, key=None, out=None) -> list[str]:
    out = [] if out is None else out
    if isinstance(d, dict):
        t = d.get(TAG)
        if t == "Screen":
            nm = d.get("name") or (d.get("spec") or {}).get("name") or (d.get("rule") or {}).get("name")
            if nm:
                out.append(nm)
            return out
        if t == "Portfolio":
            return _screen_names(d["screen"], "screen", out)
        for k, v in d.items():
            if k in ("screen", "screens"):
                _screen_names(v, "screen", out)
            elif isinstance(v, (dict, list)) and k not in ("exit", "config"):
                _screen_names(v, None, out)
    elif isinstance(d, list):
        for v in d:
            _screen_names(v, key, out)
    elif isinstance(d, str) and key == "screen":
        out.append(d)
    return out


def _exit_signal_types(d, out=None) -> list[str]:
    out = [] if out is None else out
    if isinstance(d, dict):
        if d.get(TAG) == "Signal":
            out.append(d["type"])
        else:
            for v in d.values():
                _exit_signal_types(v, out)
    elif isinstance(d, list):
        for v in d:
            _exit_signal_types(v, out)
    return out


_DEFAULTS = None            # kind -> {arg name: default}; installed by jobs.py


def set_defaults_provider(fn) -> None:
    global _DEFAULTS
    _DEFAULTS = fn


@dataclass
class RunSpec:
    """One run as data: the Lab method (`kind`), the dataset it runs on, that method's keyword arguments
    (live objects or their serialized form), a seed and free-form label / tags (not part of the key)."""
    kind: str
    dataset: str | None = None
    args: dict = field(default_factory=dict)
    seed: int = 0
    label: str | None = None
    tags: tuple = ()

    def __post_init__(self):
        self.tags = tuple(self.tags or ())
        self.args = dict(self.args or {})

    # ---- serialization
    def serialized_args(self) -> dict:
        return to_dict(self.args)

    def build_args(self) -> dict:
        """The keyword arguments as live objects (raises `Unpersistable` when an input cannot be rebuilt)."""
        return from_dict(self.serialized_args())

    @property
    def reasons(self) -> list[str]:
        return unpersistable_reasons(self.serialized_args())

    @property
    def persistable(self) -> bool:
        return not self.reasons

    @property
    def reason(self) -> str | None:
        r = self.reasons
        return "; ".join(dict.fromkeys(r)) if r else None

    def to_dict(self) -> dict:
        r = self.reason
        return {"kind": self.kind, "dataset": self.dataset, "args": self.serialized_args(), "seed": self.seed,
                "label": self.label, "tags": list(self.tags), "persistable": r is None, "reason": r}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> "RunSpec":
        return cls(d["kind"], d.get("dataset"), d.get("args") or {}, d.get("seed", 0), d.get("label"),
                   tuple(d.get("tags") or ()))

    @classmethod
    def from_json(cls, s: str) -> "RunSpec":
        return cls.from_dict(json.loads(s))

    # ---- identity
    def canonical(self) -> str:
        """Canonical JSON of (kind, args, seed). Arguments that are None or equal to the Lab method's own
        default are dropped, so `lab.timing("dogs")` and `{"screen": "dogs", "stride": 1}` are one run."""
        args = self.serialized_args()
        defaults = _DEFAULTS(self.kind) if _DEFAULTS is not None else {}
        args = {k: v for k, v in args.items()
                if v is not None and not (k in defaults and _canon(to_dict(defaults[k])) == _canon(v))}
        return canonical_json({"kind": self.kind, "args": args, "seed": self.seed})

    def run_key(self, dataset_id: str, code_version_: str | None = None) -> str | None:
        """sha256(canonical(kind, args, seed) + dataset_id + code_version); None if not persistable."""
        if not self.persistable:
            return None
        cv = code_version() if code_version_ is None else code_version_
        return hashlib.sha256(f"{self.canonical()}|{dataset_id}|{cv}".encode()).hexdigest()

    def screen_names(self) -> list[str]:
        """Base names of the entry screens the run touches (sorted, unique)."""
        return sorted(set(_screen_names(self.serialized_args())))

    def family(self) -> str:
        """Trial family: same kind + dataset + screen base (exit kinds: the signal set)."""
        base = self.screen_names() or [f"exit:{'+'.join(sorted(set(_exit_signal_types(self.serialized_args()))))}"
                                       if self.kind.startswith("exit") else "*"]
        return f"{self.kind}|{self.dataset}|{'+'.join(base)}"


# ---------------------------------------------------------------------------------------------------
# dataset id and code version
# ---------------------------------------------------------------------------------------------------
def dataset_id(market) -> str:
    """Fingerprint of a Market's prices, fundamentals and indices (hash_pandas_object), computed once and
    cached on the Market object."""
    cached = getattr(market, "_screenlab_dataset_id", None)
    if cached:
        return cached
    h = hashlib.sha256()
    fund = market.fund
    if {"date", "ticker"} <= set(fund.columns):          # canonical row order: Market's own sort is unstable
        fund = fund.sort_values(["date", "ticker"], kind="stable")
    for name, df, ix in (("prices", market.prices, True), ("fund", fund, False), ("indices", market.indices, True)):
        h.update(name.encode())
        h.update(repr((list(map(str, df.columns)), str(df.index.dtype) if ix else "", df.shape)).encode())
        try:
            hv = pd.util.hash_pandas_object(df, index=ix)
        except TypeError:
            hv = pd.util.hash_pandas_object(df.astype(str), index=ix)
        h.update(hv.to_numpy().tobytes())
    did = h.hexdigest()[:24]
    try:
        market._screenlab_dataset_id = did
    except AttributeError:
        pass
    return did


def dataset_manifest(market, name: str | None = None, lab=None) -> dict:
    """Small description of a dataset for the store's `datasets` table."""
    bm = getattr(lab, "benchmark", None)
    return {"name": name, "span": [str(market.index[0].date()), str(market.index[-1].date())],
            "n_names": int(market.prices.shape[1]), "n_dates": int(len(market.index)),
            "n_fund_rows": int(len(market.fund)), "indices": [str(c) for c in market.indices.columns],
            "benchmark": getattr(bm, "label", None), "benchmark_kind": type(bm).__name__ if bm is not None else None}


@dataclass
class LabContext:
    """What a Lab contributes to a run besides the call's arguments: its Config, benchmark / compare list and
    every screen in `lab.screens` that is not an unmodified built-in."""
    hash: str
    bad_screens: frozenset      # screens (by name) that cannot be rebuilt: a Python function
    reasons: tuple              # lab-wide reasons (e.g. a callable in lab.config): nothing is persistable


def lab_context(lab) -> LabContext:
    cfg = to_dict(lab.config)
    bench = to_dict([lab.benchmark, list(lab.compare)])
    reasons = unpersistable_reasons(cfg) + unpersistable_reasons(bench)
    screens, bad = {}, set()
    for n, sc in lab.screens.items():
        d = to_dict(sc)
        if d == {TAG: "Screen", "name": n}:                      # unmodified built-in
            continue
        if unpersistable_reasons(d):
            bad.add(n)
            d = {TAG: "unpersistable", "name": n}
        screens[n] = d
    h = hashlib.sha256(canonical_json({"config": cfg, "bench": bench, "screens": screens}).encode()).hexdigest()
    return LabContext(h[:16], frozenset(bad), tuple(reasons))


def run_key_for(spec: RunSpec, did: str, ctx: LabContext, code_version_: str | None = None) -> str | None:
    """`spec.run_key` for a run on a lab: the dataset id is extended by the lab context, and the key is None
    when the lab's config / benchmark is unpersistable or the call uses a screen defined by a Python function."""
    if ctx.reasons or (ctx.bad_screens & set(spec.screen_names())):
        return None
    return spec.run_key(f"{did}:{ctx.hash}", code_version_)


@functools.lru_cache(maxsize=1)
def code_version() -> str:
    """Package `__version__` (runspec's own fallback if the package has none) plus the git commit when
    `git rev-parse HEAD` works in the package directory. `SCREENLAB_CODE_VERSION` overrides it."""
    env = os.environ.get("SCREENLAB_CODE_VERSION")
    if env:
        return env
    pkg = sys.modules.get(__package__ or "")
    ver = getattr(pkg, "__version__", None) or __version__
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"{ver}+{sha[:12]}" if sha else str(ver)
