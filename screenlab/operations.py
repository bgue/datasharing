"""Operations: one definition per analysis, everything else derives from it (DESIGN.md J4).

An `Operation` says what an analysis is: its name, typed arguments, how to run it on a `Lab`, how to turn the
result into JSON, which numbers to store, and which trial family it belongs to. Registering one makes it
available everywhere at once, with no other module edited:

    @register_operation
    class EchoStats(Operation):
        '''Summary statistics of a backtest curve.'''
        name = "echo_stats"
        long_running = False

        @dataclass
        class Args:                                   # the typed request (JSON) arguments
            screen: str = arg(doc="screen name")
            rebalance_months: int = arg(12, "months between rebalances")

        def run(self, lab, screen, rebalance_months=12):        # raw computation on a Lab
            rep = lab.backtest([screen], rebalance_months=rebalance_months)
            return rep.curves[screen].describe().to_frame("value")

What that gives, for free:

* `Lab`: `lab.echo_stats(...)` and `lab.run("echo_stats", ...)` (trial counting and recording included);
* jobs: `lab.submit("echo_stats", ...)`, `jobs.KINDS`, `RunSpec("echo_stats", ...)` with its keys, family and
  argument defaults;
* store: metrics rows (`Operation.metrics`, by default every numeric cell of the result tables);
* REST: `POST /labs/{lab}/echo_stats` with a typed body, a `/jobs` kind, `GET /operations`;
* MCP: a tool `echo_stats`, and `submit_run(kind="echo_stats")`.

Layers, from the inside out. `run(lab, **kw)` is the raw computation with the Lab method's own keyword
arguments (live objects). `call` wraps it with trial counting and run recording (what `Lab.<name>` does).
`execute` is what jobs and servers call. `Args` is the JSON request model; `build(lab, args)` turns it into
`run`'s keyword arguments (validating, building live objects). `to_json` makes the response. The framework
imports no web library: `pydantic_model()` builds a pydantic request model from `Args` when REST/MCP need one.
"""
from __future__ import annotations

import dataclasses
import functools
import inspect
import re
import sys
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from typing import Any, Callable

from .errors import ServerError
from .jsonout import DEFAULT_MAX_POINTS, jsonify


# ---------------------------------------------------------------------------------------------------
# argument declarations
# ---------------------------------------------------------------------------------------------------
def arg(default=MISSING, doc: str = "", *, factory=None):
    """A dataclass field for an operation argument: `arg()` = required, `arg(None, "doc")` = optional."""
    md = {"doc": doc}
    if factory is not None:
        return field(default_factory=factory, metadata=md)
    if default is MISSING:
        return field(metadata=md)
    return field(default=default, metadata=md)


@dataclass
class ArgSpec:
    name: str
    annotation: Any
    default: Any = MISSING
    factory: Any = None
    doc: str = ""

    @property
    def required(self) -> bool:
        return self.default is MISSING and self.factory is None

    def default_value(self):
        return self.factory() if self.factory is not None else self.default


def _hints(cls) -> dict:
    try:
        return typing.get_type_hints(cls, include_extras=True)
    except Exception:
        return {}


def arg_specs(args_cls) -> list:
    """`ArgSpec`s of an Args dataclass (type hints resolved)."""
    hints = _hints(args_cls)
    out = []
    for f in fields(args_cls):
        out.append(ArgSpec(f.name, hints.get(f.name, f.type), f.default,
                           f.default_factory if f.default_factory is not MISSING else None,
                           f.metadata.get("doc", "")))
    return out


def _sig_defaults(fn) -> dict:
    try:
        ps = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return {}
    return {p.name: p.default for p in ps if p.default is not inspect.Parameter.empty}


# ---------------------------------------------------------------------------------------------------
# the base class
# ---------------------------------------------------------------------------------------------------
class Operation:
    """One analysis. Subclass, set `name`, optionally `Args`, implement `run`, and register it."""

    name: str = ""
    Args: type | None = None            # dataclass of JSON request arguments (None = free-form keyword arguments)
    long_running: bool = False          # minutes, not seconds: servers point at the job endpoints
    counts_points: bool = False         # each grid point is a trial of the family (sweeps, calibrations)
    by_signals: bool = False            # family is the exit-signal set rather than the entry screens
    tracked_entry: bool = True          # `execute` goes through `call` (trial counting / recording)
    takes_max_points: bool = True       # the sync response has curves: requests may set `max_points`
    method_names: tuple = ()            # extra names for the Lab method (the name itself always works)
    rest_path: str | None = None        # path under /labs/{lab}/ (default: the name; may hold {arg} placeholders)
    rest_method: str = "POST"           # "GET": path placeholders + query parameters instead of a body
    registry_order: tuple | None = None  # legacy positional parameter order of LabRegistry methods / MCP tools
    tool_order: tuple | None = None
    builtin: bool = False

    # ---- computation
    def run(self, lab, **kw):
        """The raw computation with the Lab method's keyword arguments."""
        raise NotImplementedError(f"operation {self.name!r} does not implement run()")

    def method(self, lab, *a, **kw):
        """The Lab method form, when it differs from `run` (e.g. `Lab.significance(report, ...)`)."""
        return self.run(lab, *a, **kw)

    def method_fn(self):
        """The function whose signature and docstring the Lab method has: `method` if overridden, else `run`."""
        return self.run if type(self).method is Operation.method else self.method

    def tracking_args(self, lab, *a, **kw) -> dict | None:
        """The argument dict that identifies this call as a trial / recorded run (None = do not track)."""
        from .tracking import bound_args
        return bound_args(inspect.signature(self.method_fn()), lab, *a, **kw)

    def annotate(self, result, args: dict) -> None:
        """Hook after a tracked call (e.g. attach the arguments to the result)."""

    def call(self, lab, *a, **kw):
        """`Lab.<name>(...)`: `method` with trial counting and run recording."""
        from .tracking import run_tracked
        return run_tracked(lab, self, lambda: self.method(lab, *a, **kw), lambda: self.tracking_args(lab, *a, **kw))

    def execute(self, lab, **kw):
        """What jobs, the servers and `Lab.run` call: `run` with tracking."""
        return self.call(lab, **kw) if self.tracked_entry else self.run(lab, **kw)

    # ---- identity
    def defaults(self) -> dict:
        """Default values of the Lab-level keyword arguments (what an omitted argument means); an argument
        equal to its default is left out of the run key."""
        return _sig_defaults(self.run)

    def accepts_seed(self) -> bool:
        try:
            return "seed" in inspect.signature(self.run).parameters
        except (TypeError, ValueError):
            return False

    def family(self, args: dict) -> str:
        """The screen base of the trial family for serialized `args` (the full family is
        "<kind>|<dataset>|<this>"): the entry screens, else the exit-signal set, else "*"."""
        from .runspec import default_family
        return default_family(args, self.by_signals)

    # ---- requests
    def arg_specs(self) -> list:
        if self.Args is not None:
            return arg_specs(self.Args)
        return []

    def parse(self, raw: dict):
        """Validate a request's argument dict against `Args` (unknown / missing fields are a 400) and return
        the Args instance (or the dict itself for a free-form operation)."""
        if not isinstance(raw, dict):
            raise ServerError(400, "`args` must be a JSON object")
        if self.Args is None:
            return dict(raw)
        specs = self.arg_specs()
        names = [s.name for s in specs]
        bad = [k for k in raw if k not in names]
        if bad:
            raise ServerError(400, f"unknown field {bad[0]!r} for {self.name}; allowed: {names}")
        missing = [s.name for s in specs if s.required and s.name not in raw]
        if missing:
            raise ServerError(400, f"missing required field {missing[0]!r}")
        return self.Args(**raw)

    def build(self, lab, args) -> dict:
        """The keyword arguments of `run` from parsed request arguments (live objects, validated).
        Default: the fields that are not None."""
        d = args if isinstance(args, dict) else {s.name: getattr(args, s.name) for s in self.arg_specs()}
        return {k: v for k, v in d.items() if v is not None}

    def precheck(self, raw: dict) -> None:
        """Validate a request's JSON arguments without a Lab (`parse` + `build` with `lab=None`): raises the
        ServerError (400) naming the bad field. The REST layer calls it to explain a rejected body."""
        self.build(None, self.parse(dict(raw)))

    def args_to_dict(self, args) -> dict:
        """An Args instance as a plain dict (all fields); the inverse of `parse` for valid input."""
        return dict(args) if isinstance(args, dict) else {s.name: getattr(args, s.name) for s in self.arg_specs()}

    # ---- results
    def to_json(self, result, max_points: int = DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        """The JSON-able response for `result` (a dict is expected: data card and trials are added to it)."""
        return jsonify(result, max_points)

    def result_json(self, result, max_points: int = DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        """`to_json` plus the data card and the trial count every response carries."""
        out = self.to_json(result, max_points, lab=lab, kw=kw or {})
        card = getattr(result, "data_card", None) or (lab.data_card() if hasattr(lab, "data_card") else None)
        if isinstance(out, dict) and card is not None:
            out["data_card"] = card.to_dict()
        info = lab.last_trials() if hasattr(lab, "last_trials") else None
        if isinstance(out, dict) and info:
            out["trials_in_family"] = info["trials_in_family"]
            out["trials_scope"] = info["trials_scope"]
        return out

    def extract(self, result):
        """Tables of a result for the store: `store.Extracted(summary=..., bulk=...)`."""
        from .store import extract
        return extract(result)

    def metrics(self, result) -> list:
        """Long rows `(row_label, metric, value)` stored in the `metrics` table: by default every numeric cell
        of the summary tables."""
        from .store import long_rows
        return long_rows(self.extract(result))

    # ---- schema
    def request_model(self):
        """A pydantic model of the request body (built lazily; needs pydantic)."""
        return _models.request_model(self)

    def describe(self) -> dict:
        """JSON description for `GET /operations` and `list_operations`."""
        specs = self.arg_specs()
        return {"name": self.name, "doc": (inspect.getdoc(type(self)) or "").strip().splitlines()[0]
                if inspect.getdoc(type(self)) else "", "long_running": self.long_running,
                "builtin": self.builtin,
                "args": [{"name": s.name, "required": s.required, "doc": s.doc,
                          "default": None if s.required else jsonify(s.default_value())} for s in specs]}


class FunctionOperation(Operation):
    """An operation made from a plain function `fn(lab, **kw)` (`jobs.register_kind`): free-form arguments,
    no tracking, default store metrics."""
    tracked_entry = False
    takes_max_points = False

    def __init__(self, name: str, fn: Callable):
        self.name, self._fn = name, fn

    def run(self, lab, **kw):
        return self._fn(lab, **kw)

    def defaults(self) -> dict:
        return _sig_defaults(self._fn)

    def accepts_seed(self) -> bool:
        try:
            return "seed" in inspect.signature(self._fn).parameters
        except (TypeError, ValueError):
            return False

    def describe(self) -> dict:
        d = super().describe()
        d["doc"] = (inspect.getdoc(self._fn) or "").strip().splitlines()[0] if inspect.getdoc(self._fn) else ""
        return d


# ---------------------------------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------------------------------
class OperationRegistry(dict):
    """name -> Operation. Registering installs the Lab method(s) and tells subscribers (REST apps)."""

    def __init__(self):
        super().__init__()
        self._subscribers: list = []

    def register(self, op: Operation) -> Operation:
        if not op.name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", op.name):
            raise ValueError(f"operation name must be an identifier, got {op.name!r}")
        self[op.name] = op
        _install_lab_methods(op)
        for cb in list(self._subscribers):
            try:
                if cb(op) is False:
                    self._subscribers.remove(cb)
            except Exception:                                   # a dead subscriber must not break registration
                self._subscribers.remove(cb)
        return op

    def subscribe(self, cb: Callable) -> None:
        """`cb(op)` is called for every operation registered from now on (return False to unsubscribe)."""
        self._subscribers.append(cb)

    def require(self, name: str) -> Operation:
        try:
            return self[name]
        except KeyError:
            raise ServerError(400, f"unknown run kind {name!r}; use {sorted(self)}") from None

    def builtin_names(self) -> tuple:
        return tuple(n for n, o in self.items() if o.builtin)


OPERATIONS = OperationRegistry()


def builtin_names() -> tuple:
    _load_builtins()
    return OPERATIONS.builtin_names()


def register_operation(obj=None, *, builtin: bool = False):
    """Register an `Operation` subclass (decorator; returns the class) or instance (returns it)."""
    def wrap(o):
        op = o() if isinstance(o, type) else o
        op.builtin = builtin or op.builtin
        OPERATIONS.register(op)
        return o
    return wrap(obj) if obj is not None else wrap


def register_function(name: str, fn: Callable) -> Operation:
    """Add (or replace) an operation made from `fn(lab, **kw) -> result` (see `jobs.register_kind`)."""
    op = FunctionOperation(name, fn)
    OPERATIONS.register(op)
    return op


@functools.lru_cache(maxsize=1)
def _load_builtins() -> bool:
    from . import ops  # noqa: F401  (each module registers its operation)
    return True


# ---------------------------------------------------------------------------------------------------
# Lab methods
# ---------------------------------------------------------------------------------------------------
def lab_method(op: Operation, name: str | None = None):
    """A function `Lab.<name>(self, ...)` with the signature and docstring of the operation's method form."""
    name = name or op.name
    fn = op.method_fn()
    sig = inspect.signature(fn)
    ps = list(sig.parameters.values())
    ps[0] = ps[0].replace(name="self")

    def m(self, *a, **kw):
        return op.call(self, *a, **kw)
    m.__name__, m.__qualname__ = name, f"Lab.{name}"
    m.__doc__ = inspect.getdoc(fn)
    m.__signature__ = sig.replace(parameters=ps)
    m._operation = op.name
    return m


def _install_lab_methods(op: Operation) -> None:
    mod = sys.modules.get(f"{__package__}.api")
    cls = getattr(mod, "Lab", None) if mod is not None else None
    if cls is not None:
        install_lab_methods(cls, op)


def install_lab_methods(cls, op: Operation) -> None:
    """Give `cls` (Lab) the methods of `op`; never replaces an attribute that is not an operation method."""
    for nm in dict.fromkeys((op.name, *op.method_names)):
        cur = cls.__dict__.get(nm)
        if cur is not None and getattr(cur, "_operation", None) is None:
            continue
        setattr(cls, nm, lab_method(op, nm))


# ---------------------------------------------------------------------------------------------------
# pydantic request models (lazy: only the servers need pydantic)
# ---------------------------------------------------------------------------------------------------
MODEL_HOOKS: dict = {}          # marker class -> () -> pydantic annotation


def model_hook(marker):
    """Register how a marker annotation class becomes a pydantic type (`@model_hook(SignalSpec)`)."""
    def deco(fn):
        MODEL_HOOKS[marker] = fn
        return fn
    return deco


class _Models:
    def __init__(self):
        self._cache: dict = {}

    def _conv(self, tp):
        import types
        from pydantic import Field
        if tp in MODEL_HOOKS:
            return MODEL_HOOKS[tp]()
        if isinstance(tp, type) and is_dataclass(tp):
            return self.dataclass_model(tp)
        origin, targs = typing.get_origin(tp), typing.get_args(tp)
        if origin is typing.Annotated:
            return typing.Annotated[(self._conv(targs[0]), *targs[1:])]
        if origin in (typing.Union, types.UnionType):
            return typing.Union[tuple(self._conv(a) for a in targs)]
        if origin is typing.Literal or origin is None:
            return tp
        try:
            return origin[tuple(self._conv(a) for a in targs)] if len(targs) != 1 else origin[self._conv(targs[0])]
        except TypeError:
            return tp

    def dataclass_model(self, cls, extra_fields: dict | None = None, name: str | None = None):
        from pydantic import ConfigDict, Field, create_model
        key = (cls, name, tuple(sorted((extra_fields or {}).items(), key=lambda kv: kv[0])) if extra_fields else None)
        if key in self._cache:
            return self._cache[key]
        flds: dict = {}
        for s in arg_specs(cls):
            tp = self._conv(s.annotation)
            if s.required:
                flds[s.name] = (tp, Field(..., description=s.doc or None))
            elif s.factory is not None:
                flds[s.name] = (tp, Field(default_factory=s.factory, description=s.doc or None))
            else:
                flds[s.name] = (tp, Field(s.default, description=s.doc or None))
        for k, v in (extra_fields or {}).items():
            flds[k] = v
        model = create_model(name or cls.__name__, __config__=ConfigDict(extra="forbid"),
                             __doc__=inspect.getdoc(cls) or None, **flds)
        self._cache[key] = model
        return model

    def request_model(self, op: Operation, with_max_points: bool | None = None):
        from pydantic import ConfigDict, Field, create_model
        wmp = op.takes_max_points if with_max_points is None else with_max_points
        key = ("request", op.name, wmp, id(op))
        if key in self._cache:
            return self._cache[key]
        camel = "".join(p.capitalize() for p in op.name.split("_"))
        if op.Args is not None:
            extra = {"max_points": (int, Field(DEFAULT_MAX_POINTS, description="most points per returned curve"))} \
                if wmp else None
            model = self.dataclass_model(op.Args, extra, f"{camel}Request" if wmp else f"{camel}Args")
        else:
            model = create_model(f"{camel}Request", __config__=ConfigDict(extra="allow"),
                                 __doc__=f"Free-form arguments of {op.name}.")
        self._cache[key] = model
        return model


_models = _Models()


def pydantic_type(annotation):
    """The pydantic counterpart of a request annotation (nested dataclasses become models)."""
    return _models._conv(annotation)
