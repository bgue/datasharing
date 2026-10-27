"""A run as plain data: `RunSpec`, JSON serialization of every input type, and a stable `run_key`.

    spec = RunSpec("backtest", "sp500", {"screens": ["dogs", Portfolio("h", "dogs", exit=policy)],
                                         "rebalance_months": 6, "cost_bps": cap_tiered({0: 75, 1000: 30})})
    spec.persistable                  # False if any input is a Python callable we cannot rebuild
    key = spec.run_key(dataset_id(lab.market))          # sha256 of kind + args + seed + data + code
    d = spec.to_dict(); RunSpec.from_dict(json.loads(json.dumps(d)))    # round trip

The types own their serialization (serial.py: `to_dict()` / `from_dict()` on Config, HoldRule, ExitPolicy,
ExitScreen, signals, Portfolio, IndexBenchmark, ScreenBenchmark, Grid, MacroSeries, CapTiered; adapters in
screen_codec.py for Screen, GrahamTests, ScreenSpec); `to_dict` / `from_dict` here are the generic
dispatcher. Defaults are omitted, so adding a field with a default to a dataclass does not change existing
keys. Deprecated signal parameter names are canonicalized before hashing.

Things that cannot be rebuilt (a Python function weighting, a custom cost function, `Screen(fn=...)` with a lambda / closure /
`__main__` function, a custom signal, a MacroSeries without a registered reference) become `{"__t": "unpersistable", "reason"}`.
Such a spec still runs (in-process) but has `persistable == False`, no `run_key`, and no cache reuse.

`kind` is the name of a registered `Operation` (operations.py); its argument defaults and trial family come
from there.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

import pandas as pd

__version__ = "0.1.0"        # fallback; the package's own __version__ wins when it defines one

# re-exports (they moved with the types that own them)
from .macro import MACRO_LOADERS, macro_ref, register_macro  # noqa: E402,F401
from .serial import TAG, Unpersistable, from_dict, to_dict  # noqa: E402,F401


def __getattr__(name):
    if name == "BUNDLED_KINDS":                      # the built-in operations (extra kinds do not belong here)
        from .operations import builtin_names
        return builtin_names()
    raise AttributeError(name)


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


def default_family(args: dict, by_signals: bool = False) -> str:
    """The screen base of a trial family: the entry screens an argument dict touches ("+"-joined, sorted), else the
    exit-signal set when `by_signals` ("exit:A+B"), else "*"."""
    names = sorted(set(_screen_names(args)))
    if names:
        return "+".join(names)
    return f"exit:{'+'.join(sorted(set(_exit_signal_types(args))))}" if by_signals else "*"


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
        from .operations import OPERATIONS
        op = OPERATIONS.get(self.kind)
        defaults = op.defaults() if op is not None else {}
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
        from .operations import OPERATIONS
        op = OPERATIONS.get(self.kind)
        base = op.family(self.serialized_args()) if op is not None else default_family(self.serialized_args())
        return f"{self.kind}|{self.dataset}|{base}"


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
    uni = getattr(market, "universe", None)
    if uni is not None and uni.fingerprint() != "AllListed()":   # unchanged (AllListed) datasets keep their ids
        h.update(b"universe" + uni.fingerprint().encode())
    did = h.hexdigest()[:24]
    try:
        market._screenlab_dataset_id = did
    except AttributeError:
        pass
    return did


def dataset_manifest(market, name: str | None = None, lab=None) -> dict:
    """Small description of a dataset for the store's `datasets` table."""
    bm = getattr(lab, "benchmark", None)
    card = None
    if lab is not None and hasattr(lab, "data_card"):
        try:
            card = lab.data_card().to_dict()
        except Exception:                                   # a manifest must never fail a run
            card = None
    return {"card": card, "name": name, "span": [str(market.index[0].date()), str(market.index[-1].date())],
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
