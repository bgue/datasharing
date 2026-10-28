"""Named Labs (created lazily, cached) and the JSON-in / JSON-out operations both servers expose.

Every operation takes plain JSON-able arguments and returns plain JSON: tables as
{"columns": [...], "rows": [[...], ...]}, curves as {name: [[iso_date, value], ...]} downsampled to at
most `max_points`, NaN/inf as null. REST and MCP are thin wrappers, so their shapes are identical.
"""
from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import os
import threading

import pandas as pd

from .. import progress
from ..errors import ServerError
from ..jsonout import (DEFAULT_MAX_POINTS, curve, curves, downsample_index, jsonify, scalar,  # noqa: F401  (re-exported)
                       series_table, table)
from ..operations import OPERATIONS, _load_builtins
from ..ops import _request
from ..ops.backtest import report_json                                                       # noqa: F401

# the fundamentals column contract (data.py), with a one-line meaning each
COLUMNS = {
    "ticker": "security id (index of a snapshot)", "date": "date the row became knowable",
    "sector": "sector label", "price": "price at report date (snapshots are marked to market)",
    "market_cap": "market capitalization", "sales": "revenue", "eps": "latest annual EPS",
    "eps_prev": "EPS a year earlier", "eps_avg3": "3-year average EPS",
    "eps_avg3_10y_ago": "3-year average EPS ten years ago", "loss_years_5y": "loss years in last 5",
    "loss_years_10y": "loss years in last 10", "bvps": "book value per share", "dividend": "annual dividend per share",
    "div_years": "consecutive years of dividends", "current_ratio": "current assets / current liabilities",
    "current_ratio_prev": "prior-year current ratio", "lt_debt": "long-term debt",
    "net_current_assets": "current assets minus total liabilities", "roa": "return on assets",
    "roa_prev": "prior-year ROA", "cfo_to_assets": "operating cash flow / assets", "leverage": "debt / assets",
    "leverage_prev": "prior-year leverage", "shares": "shares outstanding", "shares_prev": "prior-year shares",
    "gross_margin": "gross margin", "gross_margin_prev": "prior-year gross margin",
    "asset_turnover": "sales / assets", "asset_turnover_prev": "prior-year asset turnover",
    "ebit": "operating income", "enterprise_value": "market cap + net debt", "invested_capital": "invested capital",
    "pe": "derived: price / eps (eps > 0)", "pe_avg3": "derived: price / eps_avg3", "pb": "derived: price / bvps",
    "div_yield": "derived: dividend / price", "earnings_yield": "derived: ebit / enterprise_value",
    "roic": "derived: ebit / invested_capital", "ncav_to_mcap": "derived: net current assets / market cap",
    "book_equity": "derived: book equity", "f_score": "derived: Piotroski 0-9",
    "mom_12_1": "snapshot: 12-1 month price momentum", "vol_1y": "snapshot: one-year annualized volatility",
}


# ------------------------------------------------------------------ registry
def _skfolio_available() -> bool:
    try:
        import skfolio  # noqa: F401
        return True
    except ImportError:
        return False


def _make_synthetic():
    from ..api import Lab
    return Lab.synthetic()


def _make_sp500():
    from ..realdata.bundled import sp500_lab
    return sp500_lab()


_log = logging.getLogger(__name__)


def _translate(e: Exception) -> ServerError:
    """SDK exception -> ServerError with a clean message: KeyError -> 404, bad input -> 400, anything
    unexpected -> 500 "internal error" (the traceback goes to the log, not the response)."""
    if isinstance(e, ServerError):
        return e
    if isinstance(e, KeyError):
        return ServerError(404, str(e.args[0]) if e.args else "not found")
    if isinstance(e, NotImplementedError):
        return ServerError(501, str(e) or "not implemented")
    if (isinstance(e, (ValueError, TypeError, SyntaxError, AttributeError, IndexError, FileNotFoundError))
            or type(e).__name__ in ("UndefinedVariableError", "JSONDecodeError")):
        return ServerError(400, str(e) or "bad request")
    _log.error("unexpected error", exc_info=e)
    return ServerError(500, "internal error")


def _guarded(fn):
    """Run a registry operation so that every failure leaves as a ServerError."""
    @functools.wraps(fn)
    def inner(*a, **kw):
        try:
            return fn(*a, **kw)
        except ServerError:
            raise
        except Exception as e:
            raise _translate(e) from e
    return inner


def _clean(kw: dict) -> dict:
    """Keyword arguments without the None entries (an omitted argument and None mean the same everywhere)."""
    return {k: v for k, v in kw.items() if v is not None}


def default_store():
    """The server's store: `SCREENLAB_STORE` (a path; "" / "none" / "off" disables), else ~/.screenlab/runs.sqlite.
    Falls back to no persistence, with a warning, if the file cannot be opened."""
    from ..store import NullStore, SQLiteStore
    env = os.environ.get("SCREENLAB_STORE")
    if env is not None and env.strip().lower() in ("", "none", "off"):
        return NullStore()
    path = env or os.path.join(os.path.expanduser("~"), ".screenlab", "runs.sqlite")
    try:
        return SQLiteStore(path)
    except Exception as e:                                          # read-only home, bad path, ...
        _log.warning("run store %s unavailable (%s); persistence is off", path, e)
        return NullStore()


def result_json(kind: str, L, kw: dict, res, max_points=DEFAULT_MAX_POINTS) -> dict:
    """The JSON a run of `kind` returns (same for the synchronous operation and the job result). `L` is the
    lab it ran on, `kw` the Lab keyword arguments, `res` the result. Carries `trials_in_family`."""
    _load_builtins()
    op = OPERATIONS.get(kind)
    if op is None:
        out = jsonify(res, max_points)
        info = L.last_trials() if hasattr(L, "last_trials") else None
        if isinstance(out, dict) and info:
            out["trials_in_family"], out["trials_scope"] = info["trials_in_family"], info["trials_scope"]
        return out
    return op.result_json(res, max_points, lab=L, kw=kw)


def encode_result(spec, res, lab) -> dict:
    """JobManager encoder: the JSON payload of a finished run (runs in the worker, importable for spawn)."""
    from ..jobs import live_args
    return result_json(spec.kind, lab, live_args(spec), res)


class LabRegistry:
    """Named labs, built on first use. `factories` maps name -> zero-arg callable (or a ready Lab).

    store         a RunStore: labs record their runs and trials in it (default: none).
    sync_timeout  seconds a synchronous operation may run before it is stopped with a 408 that points at the
                  job operations (default 120, env SCREENLAB_SYNC_TIMEOUT; 0 = no limit).
    workers, mode, persist   JobManager settings for submit_run (mode "process" or "thread")."""

    def __init__(self, factories: dict | None = None, default: str | None = None, store=None,
                 sync_timeout: float | None = None, workers=None, mode: str = "process", persist: str = "metrics"):
        from ..store import NullStore
        self.store = store if store is not None else NullStore()
        self.sync_timeout = (float(os.environ.get("SCREENLAB_SYNC_TIMEOUT", 120)) if sync_timeout is None
                             else float(sync_timeout))
        self.workers, self.mode, self.persist = workers, mode, persist
        self._jm = None
        self.factories = dict(factories) if factories is not None else {
            "synthetic": _make_synthetic, "sp500": _make_sp500}
        self.default = default or ("sp500" if "sp500" in self.factories and _skfolio_available()
                                   else "synthetic" if "synthetic" in self.factories
                                   else next(iter(self.factories)))
        self._labs: dict = {}
        self._lock = threading.RLock()

    # ---- labs
    def register(self, name: str, lab_or_factory):
        with self._lock:
            self.factories[name] = lab_or_factory
            self._labs.pop(name, None)
            if self._jm is not None:                       # thread-mode managers see it at once
                self._jm.labs[name] = lab_or_factory
                self._jm._rt.factories[name] = lab_or_factory
                self._jm._rt._labs.pop(name, None)
                self._jm._dsids.pop(name, None)

    def register_csv(self, name: str, fundamentals: str, prices: str, indices: str | None = None):
        from ..api import Lab
        self.register(name, lambda: Lab.from_csv(fundamentals, prices, indices))

    def get(self, name: str | None = None):
        name = name or self.default
        with self._lock:
            if name not in self._labs:
                if name not in self.factories:
                    raise ServerError(404, f"unknown lab {name!r}; available: {sorted(self.factories)}")
                f = self.factories[name]
                L = self._labs[name] = f() if callable(f) else f
                L.name = L.name or name
                L.store, L.persist = self.store, self.persist
            return self._labs[name]

    # ---- operations (all return JSON-able dicts)
    def list_labs(self) -> dict:
        return {"default": self.default,
                "labs": [{"name": n, "loaded": n in self._labs} for n in self.factories]}

    @_guarded
    def list_screens(self, lab=None) -> dict:
        L = self.get(lab)
        rows = [{"name": n, "n": s.n, "note": s.note, "params": jsonify(dict(s.params))}
                for n, s in L.screens.items()]
        return {"lab": lab or self.default, "screens": rows}

    @_guarded
    def define_screen(self, spec, lab=None) -> dict:
        L = self.get(lab)
        if isinstance(spec, dict):
            from ..spec import ScreenSpec
            known = [f.name for f in dataclasses.fields(ScreenSpec)]
            bad = sorted(set(spec) - set(known))
            if bad:
                raise ServerError(400, f"unknown screen spec field {bad[0]!r}; use {known}")
        with self._lock:
            sc = self._call(L.define, spec)
        return {"lab": lab or self.default, "name": sc.name, "n": sc.n, "note": sc.note,
                "screens": list(L.screens)}

    # ---- operations ----------------------------------------------------------------------------------
    # Every analysis is an Operation (operations.py). A request goes: `parse` (validate the JSON arguments
    # against the operation's typed Args), `build` (live objects for the Lab method), `_exec` (run it, with the
    # synchronous timeout), `result_json` (the JSON both the sync endpoint and the job result return).
    # The per-operation methods (`reg.backtest(...)`, `reg.timing(...)`, ...) are generated below.
    @property
    def KINDS(self) -> tuple:
        return tuple(OPERATIONS)

    def _kw(self, kind, L, args) -> dict:
        op = OPERATIONS.require(kind)
        if not isinstance(args, dict):
            raise ServerError(400, "`args` must be a JSON object")
        return op.build(L, op.parse(args))

    def _exec(self, L, kind, kw):
        """Run a kind on `L`. With `sync_timeout` the call runs in a helper thread and is cancelled (through
        progress.py's hook) when it overruns, with a 408 that points at the job endpoints."""
        op = OPERATIONS[kind]
        fn = lambda: op.execute(L, **kw)
        t = self.sync_timeout
        if not t:
            return self._call(fn)
        box, cancel, done = {}, threading.Event(), threading.Event()

        def target():
            try:
                with progress.job_context(None, cancel):
                    box["r"], box["trials"] = fn(), L.last_trials()
            except BaseException as e:                              # noqa: BLE001  (re-raised below)
                box["e"] = e
            finally:
                done.set()
        threading.Thread(target=target, daemon=True, name=f"screenlab-sync-{kind}").start()
        if not done.wait(t):
            cancel.set()
            raise ServerError(408, f"{kind} did not finish within {t:g}s and was stopped; start it as a job "
                                   f"(POST /jobs, MCP submit_run) and fetch the result when it is done")
        if "e" in box:
            e = box["e"]
            raise e if isinstance(e, ServerError) else _translate(e) from e
        L._tl.last = box["trials"]
        return box["r"]

    @_guarded
    def run_operation(self, name, args=None, lab=None, max_points=DEFAULT_MAX_POINTS) -> dict:
        """Run operation `name` synchronously with JSON `args`; returns its JSON."""
        op = OPERATIONS.require(name)
        L = self.get(lab)
        kw = self._kw(name, L, args or {})
        return self._register_fetched(name, result_json(name, L, kw, self._exec(L, name, kw), max_points))

    def __getattr__(self, name):                     # operations registered after the class was built
        if not name.startswith("_") and name in OPERATIONS:
            return registry_method(OPERATIONS[name]).__get__(self)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    @_guarded
    def list_operations(self) -> dict:
        return {"operations": [op.describe() for op in OPERATIONS.values()]}

    # ---- asynchronous runs (jobs.py) -----------------------------------------------------------------
    @property
    def job_manager(self):
        """The JobManager behind the run operations, built on first use over this registry's labs."""
        with self._lock:
            if self._jm is None:
                from ..jobs import JobManager
                self._jm = JobManager(store=self.store, workers=self.workers, mode=self.mode,
                                      labs=self.factories, persist=self.persist, encoder=encode_result)
            return self._jm

    def _job_json(self, job) -> dict:
        d = job.to_dict()
        d["lab"] = job.spec.dataset
        return d

    @_guarded
    def submit_run(self, kind, args=None, lab=None, seed=0, label=None, tags=None, force=False) -> dict:
        """Start a run asynchronously. `args` are the same arguments the matching synchronous operation takes
        (for "timing": screen, mode, stride, ...). An identical finished run comes back at once
        (`cache_hit`) unless `force`; an identical running one is attached to."""
        from ..runspec import RunSpec
        L = self.get(lab)
        kw = self._kw(kind, L, args or {})
        spec = RunSpec(kind, lab or self.default, kw, int(seed or 0), label, tuple(tags or ()))
        return self._job_json(self.job_manager.submit(spec, force=bool(force)))

    @_guarded
    def run_status(self, job_id) -> dict:
        return self._job_json(self.job_manager.get(job_id))

    @_guarded
    def run_result(self, job_id) -> dict:
        """The JSON the synchronous operation returns; 409 until the run is done (or if it failed / was cancelled)."""
        job = self.job_manager.get(job_id)
        st = job.status
        if st in ("queued", "running"):
            raise ServerError(409, f"job {job_id} is {st} ({job.progress:.0%}); poll run_status")
        if st == "failed":
            raise ServerError(409, f"job {job_id} failed: {job.error}")
        if st == "cancelled":
            raise ServerError(409, f"job {job_id} was cancelled")
        out = job.result_json()
        out = out if out is not None else jsonify(job.result())
        return self._register_fetched(job.spec.kind, out)

    @_guarded
    def cancel_run(self, job_id) -> dict:
        job = self.job_manager.get(job_id)
        ok = job.cancel()
        return {"job_id": job_id, "cancel_requested": ok, "status": job.status}

    @_guarded
    def list_runs(self, status=None) -> dict:
        if status is not None and status not in ("queued", "running", "done", "failed", "cancelled"):
            raise ServerError(400, f"status must be one of queued, running, done, failed, cancelled; got {status!r}")
        return {"jobs": [self._job_json(j) for j in self.job_manager.jobs(status)]}

    @_guarded
    def describe_columns(self, lab=None) -> dict:
        L = self.get(lab)
        present = set(L.snapshot().columns)
        return {"columns": [{"name": k, "description": v, "in_lab": k in present or k in ("ticker", "date")}
                            for k, v in COLUMNS.items()],
                "note": "filters and derive expressions are pandas expressions over these snapshot columns"}

    @_guarded
    def list_signals(self) -> dict:
        from ..exit_signals import _signal_classes
        out = []
        for cls in _signal_classes().values():
            d = cls.describe()
            doc = (cls.__doc__ or "").strip().splitlines()
            out.append({"type": cls.__name__, "params": {k: scalar(v) for k, v in d["params"].items()},
                        "units": d["units"], "needs": d["needs"],
                        "deprecated_params": {old: new for old, (new, _, _) in getattr(cls, "_renamed", {}).items()},
                        "doc": doc[0] if doc else ""})
        return {"signals": out}

    # ---- fetch_data lab registration (realdata/fetch.py; the operation returns lab specs, this registers them)
    def _register_fetched(self, kind, out):
        if kind == "fetch_data" and isinstance(out, dict):
            seen = self.__dict__.setdefault("_fetched", {})
            for spec in out.get("labs") or ():
                key = (spec["fundamentals"], spec["prices"], spec.get("indices"))
                if seen.get(spec["name"]) == key:                      # already registered (repeat run_result)
                    continue
                seen[spec["name"]] = key
                self.register_csv(spec["name"], spec["fundamentals"], spec["prices"], spec.get("indices"))
        return out
    # ---- end fetch_data block

    # ---- helpers
    @staticmethod
    def _call(fn, *a, **kw):
        """Run an SDK call, mapping bad input to 400 / unknown names to 404 (see `_translate`)."""
        try:
            return fn(*a, **kw)
        except ServerError:
            raise
        except Exception as e:
            raise _translate(e) from e

    # request helpers (they live with the operations; kept here for callers of the old private names)
    CONFIG_INT, CONFIG_NUM = _request.CONFIG_INT, _request.CONFIG_NUM
    CONFIG_NUM_OR_MAP, CONFIG_OPT_NUM = _request.CONFIG_NUM_OR_MAP, _request.CONFIG_OPT_NUM
    CONFIG_FIELDS, POLICY_FIELDS = _request.CONFIG_FIELDS, _request.POLICY_FIELDS
    _config = staticmethod(_request.config_overrides)
    _date = staticmethod(_request.parse_date)
    _benchmarks = staticmethod(_request.benchmarks)


def registry_method(op):
    """`LabRegistry.<op.name>(...)`: positional order `op.registry_order`, then `lab`, `max_points`."""
    specs = {s.name: s for s in op.arg_specs()}
    order = list(op.registry_order or [*specs, "lab", *(["max_points"] if op.takes_max_points else [])])
    order += [n for n in [*specs, "lab", *(["max_points"] if op.takes_max_points else [])] if n not in order]
    params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    for n in order:
        if n == "lab":
            d = None
        elif n == "max_points":
            d = DEFAULT_MAX_POINTS
        else:
            d = inspect.Parameter.empty if specs[n].required else specs[n].default_value()
        params.append(inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=d))
    sig = inspect.Signature(params)

    @_guarded
    def method(self, *a, **kw):
        vals = dict(sig.bind(self, *a, **kw).arguments)
        vals.pop("self")
        lab, mp = vals.pop("lab", None), vals.pop("max_points", DEFAULT_MAX_POINTS)
        return self.run_operation(op.name, vals, lab, mp)
    method.__name__, method.__qualname__ = op.name, f"LabRegistry.{op.name}"
    method.__doc__ = inspect.getdoc(type(op))
    method.__signature__ = sig
    method._operation = op.name
    return method


def install_registry_methods() -> None:
    _load_builtins()
    for op in list(OPERATIONS.values()):
        if getattr(LabRegistry.__dict__.get(op.name), "_operation", None) in (None, op.name):
            setattr(LabRegistry, op.name, registry_method(op))


install_registry_methods()


_DEFAULT: LabRegistry | None = None


def default_registry() -> LabRegistry:
    """The server's registry: bundled labs, runs persisted to `default_store()`."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LabRegistry(store=default_store())
    return _DEFAULT
