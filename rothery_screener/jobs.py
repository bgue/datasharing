"""Asynchronous runs: `JobManager` executes `RunSpec`s on a process (or thread) pool (DESIGN.md I2).

    jm = JobManager(store=SQLiteStore("runs.sqlite"), labs={"synthetic": make_lab}, workers=2)
    job = jm.submit(RunSpec("timing", "synthetic", {"screen": "dogs", "stride": 1}))
    job.status, job.progress, job.message        # queued / running / done / failed / cancelled, 0..1
    job.cancel()                                  # stops within one loop iteration
    table = job.result().table                    # blocks; raises JobError / Cancelled

* Dedup: a finished run with the same `run_key` in the store is returned at once (`cache_hit=True`)
  unless `force`; a run with the same key already queued or running is attached to, not started twice.
* Executor: `mode="process"` (spawn; the labs are rebuilt in each worker from the `labs` factories, which
  must therefore be importable module-level callables) or `mode="thread"` (tests, small machines; one
  lock per lab because `Market` caches are not thread-safe).
* Progress and cancel go through `progress.py`'s hook. Process workers talk to the parent through the
  store's `jobs` table, or a multiprocessing Manager dict when the store is not persistent.
* A spec with an unpersistable input (a Python callable) runs in a thread of this process, with no key.
* Workers persist results themselves (`persist` level) so a finished run survives the manager.
"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import inspect
import io
import multiprocessing as mp
import os
import pickle
import threading
import time
import traceback
import types
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from . import progress
from .progress import Cancelled
from .runspec import RunSpec, _cap_tiers, dataset_id, dataset_manifest, set_defaults_provider
from .store import LEVELS, NullStore, extract

STATUSES = ("queued", "running", "done", "failed", "cancelled")


class JobError(RuntimeError):
    """A job failed; `.error` is "<ExceptionType>: message" and `.traceback` the worker's traceback."""

    def __init__(self, error: str, tb: str = ""):
        super().__init__(error)
        self.error, self.traceback = error, tb


# ---------------------------------------------------------------------------------------------------
# what a kind runs
# ---------------------------------------------------------------------------------------------------
def _k_significance(lab, n=2000, seed=0, **bt):
    return lab.significance(lab.backtest(**bt), n=n, seed=seed)


def _k_extended(lab, macro=None, spread_over_prime=0.0, leverage=1.0, **bt):
    return lab.extended(lab.backtest(**bt), macro, spread_over_prime, leverage)


def _method(name):
    return lambda lab, **kw: getattr(lab, name)(**kw)


KINDS: dict[str, Callable] = {
    "backtest": _method("backtest"), "timing": _method("timing"), "sweep": _method("sweep"),
    "significance": _k_significance, "extended": _k_extended, "exit_evaluate": _method("exit_eval"),
    "exit_calibrate": _method("calibrate_exit"), "graham": _method("graham"), "picks": _method("picks"),
}


def _sig_defaults(*fns) -> dict:
    out = {}
    for f in fns:
        try:
            ps = inspect.signature(f).parameters
        except (TypeError, ValueError):
            continue
        out.update({n: p.default for n, p in ps.items() if p.default is not inspect.Parameter.empty})
    return out


def kind_defaults(kind: str) -> dict:
    """Default argument values of a kind's function (what an omitted argument means)."""
    from .api import Lab
    if kind == "significance":
        return _sig_defaults(Lab.backtest, _k_significance)
    if kind == "extended":
        return _sig_defaults(Lab.backtest, _k_extended)
    meth = {"exit_evaluate": "exit_eval", "exit_calibrate": "calibrate_exit"}.get(kind, kind)
    if kind in KINDS and hasattr(Lab, meth) and KINDS[kind].__qualname__.endswith("<lambda>"):
        return _sig_defaults(getattr(Lab, meth))
    return _sig_defaults(KINDS[kind]) if kind in KINDS else {}


set_defaults_provider(kind_defaults)


def register_kind(name: str, fn: Callable) -> None:
    """Add (or replace) a run kind: `fn(lab, **args) -> result`. Process workers only see kinds registered
    at import time of an importable module."""
    KINDS[name] = fn


def _takes_seed(fn) -> bool:
    try:
        return "seed" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def live_args(spec: RunSpec) -> dict:
    """The spec's keyword arguments as live objects (as given for an unpersistable spec)."""
    return dict(spec.build_args() if spec.persistable else spec.args)


def _align_macros(x, index):
    """Align macro series that were serialized by reference with `align` (the server's) to the market index."""
    from .exit_policy import ExitPolicy, Portfolio
    from .macro import MacroSeries
    if isinstance(x, MacroSeries) and getattr(x, "_align", False):
        a = x.align(index)
        a._ref, a._align = x._ref, False
        return a
    if isinstance(x, ExitPolicy) and x.macro is not None:
        x.macro = _align_macros(x.macro, index)
    elif isinstance(x, Portfolio) and x.exit is not None:
        _align_macros(x.exit, index)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _align_macros(v, index)
    elif isinstance(x, dict):
        for k, v in list(x.items()):
            x[k] = _align_macros(v, index)
    return x


def execute_spec(spec: RunSpec, lab, args: dict | None = None):
    """Run `spec` on `lab` synchronously (what a worker does). `args`: already-built keyword arguments."""
    if spec.kind not in KINDS:
        raise ValueError(f"unknown run kind {spec.kind!r}; available: {sorted(KINDS)}")
    kw = dict(args if args is not None else live_args(spec))
    _align_macros(kw, lab.market.index)
    fn = KINDS[spec.kind]
    if "seed" not in kw and _takes_seed(fn):
        kw["seed"] = spec.seed
    return fn(lab, **kw)


# ---------------------------------------------------------------------------------------------------
# results that cross a process boundary
# ---------------------------------------------------------------------------------------------------
def _none():
    return None


def _cap(tiers):
    from .backtest import cap_tiered
    return cap_tiered(tiers)


@dataclass
class DetachedResult:
    """Tables of a result that could not be pickled whole (live Market or callables inside)."""
    kind: str
    summary: dict = field(default_factory=dict)
    bulk: dict = field(default_factory=dict)

    @property
    def table(self):
        return next(iter(self.summary.values()), None)


class _SlimPickler(pickle.Pickler):
    """Pickles results without the live Market / unimportable callables they may drag along (policy caches,
    benchmark caches): those become None, `cap_tiered` closures are rebuilt from their tiers."""

    def reducer_override(self, obj):
        from .data import Market
        if isinstance(obj, Market):
            return _none, ()
        if isinstance(obj, types.FunctionType) and ("<locals>" in obj.__qualname__ or obj.__name__ == "<lambda>"):
            tiers = _cap_tiers(obj)
            return (_cap, (tiers,)) if tiers is not None else (_none, ())
        return NotImplemented


def _dumps(result, kind: str) -> bytes:
    buf = io.BytesIO()
    try:
        _SlimPickler(buf, protocol=pickle.HIGHEST_PROTOCOL).dump(result)
        return buf.getvalue()
    except Exception:
        ex = extract(result)
        return pickle.dumps(DetachedResult(kind, ex.summary, ex.bulk))


# ---------------------------------------------------------------------------------------------------
# channels: how a running job tells the parent its progress, and the parent tells it to stop
# ---------------------------------------------------------------------------------------------------
class _LocalChannel:
    def __init__(self):
        self._d: dict = {}
        self._lock = threading.Lock()

    def put(self, job_id, status="queued", run_key=None):
        with self._lock:
            self._d[job_id] = {"status": status, "progress": 0.0, "message": "", "cancel": threading.Event()}

    def update(self, job_id, status=None, progress=None, message=None):
        with self._lock:
            s = self._d.get(job_id)
            if s is None:
                return
            if status is not None:
                s["status"] = status
            if progress is not None:
                s["progress"] = progress
            if message is not None:
                s["message"] = message

    def get(self, job_id):
        with self._lock:
            s = self._d.get(job_id)
            return None if s is None else {k: v for k, v in s.items() if k != "cancel"}

    def request_cancel(self, job_id):
        with self._lock:
            if job_id in self._d:
                self._d[job_id]["cancel"].set()

    def cancel_requested(self, job_id):
        with self._lock:
            s = self._d.get(job_id)
            return bool(s and s["cancel"].is_set())

    def drop(self, job_id):
        with self._lock:
            self._d.pop(job_id, None)


class _StoreChannel:
    """Shared through the store's `jobs` table (picklable: the store reconnects by path)."""

    def __init__(self, store):
        self.store = store

    def put(self, job_id, status="queued", run_key=None):
        self.store.job_put(job_id, run_key, status)

    def update(self, job_id, status=None, progress=None, message=None):
        self.store.job_update(job_id, status, progress, message)

    def get(self, job_id):
        return self.store.job_get(job_id)

    def request_cancel(self, job_id):
        self.store.job_request_cancel(job_id)

    def cancel_requested(self, job_id):
        return self.store.job_cancel_requested(job_id)

    def drop(self, job_id):
        return None


class _ManagerChannel:
    """multiprocessing.Manager dicts, for process mode without a persistent store."""

    def __init__(self, state, cancel):
        self.state, self.cancel = state, cancel

    def put(self, job_id, status="queued", run_key=None):
        self.state[job_id] = (status, 0.0, "")

    def update(self, job_id, status=None, progress=None, message=None):
        try:
            s0, p0, m0 = self.state.get(job_id, ("queued", 0.0, ""))
        except Exception:
            return
        self.state[job_id] = (status or s0, p0 if progress is None else progress, m0 if message is None else message)

    def get(self, job_id):
        s = self.state.get(job_id)
        return None if s is None else {"status": s[0], "progress": s[1], "message": s[2]}

    def request_cancel(self, job_id):
        self.cancel[job_id] = True

    def cancel_requested(self, job_id):
        return bool(self.cancel.get(job_id, False))

    def drop(self, job_id):
        self.state.pop(job_id, None)
        self.cancel.pop(job_id, None)


class _Hook:
    """Worker side of a channel: a throttled progress callback and a throttled cancel poll (`is_set`)."""
    MIN_GAP = 0.05

    def __init__(self, channel, job_id):
        self.ch, self.id = channel, job_id
        self._last_write = 0.0
        self._last_poll = 0.0
        self._cancel = False
        self._frac = 0.0

    def callback(self, frac: float, msg: str = "") -> None:
        self._frac = max(self._frac, float(frac))
        now = time.monotonic()
        if now - self._last_write >= self.MIN_GAP or self._frac >= 1.0:
            self._last_write = now
            self.ch.update(self.id, progress=self._frac, message=msg)

    def is_set(self) -> bool:
        if self._cancel:
            return True
        now = time.monotonic()
        if now - self._last_poll >= self.MIN_GAP:
            self._last_poll = now
            self._cancel = bool(self.ch.cancel_requested(self.id))
        return self._cancel


# ---------------------------------------------------------------------------------------------------
# runtime: labs by name (one per process)
# ---------------------------------------------------------------------------------------------------
class _Runtime:
    def __init__(self, factories: dict, encoder=None):
        self.factories = dict(factories)
        self.encoder = encoder
        self._labs: dict = {}
        self._locks: dict = {}
        self._guard = threading.Lock()

    def lock(self, name):
        with self._guard:
            return self._locks.setdefault(name, threading.RLock())

    def lab(self, name):
        with self._guard:
            if name not in self._labs:
                f = self.factories[name]
                self._labs[name] = f() if callable(f) else f
            return self._labs[name]

    def job_lab(self, name, store=None, session_id=None):
        """A read-only-by-convention lab for one job: shares the Market, copies the screens dict, and gets
        the job's store / session so trials are counted where the manager counts them."""
        lab = copy.copy(self.lab(name))
        lab.screens = dict(lab.screens)
        lab._tl = threading.local()
        lab.jobs = None
        lab.name = lab.name or name
        if store is not None:
            lab.store = store
        if session_id:
            lab.session_id = session_id
        return lab


_RT: _Runtime | None = None


def _init_worker(factories, encoder):
    global _RT
    _RT = _Runtime(factories, encoder)


def _probe(name):
    """(dataset_id, manifest) of a named lab, computed inside a worker."""
    lab = _RT.lab(name)
    return dataset_id(lab.market), dataset_manifest(lab.market, name, lab)


def _run_job(p: dict):
    """Module-level worker entry (spawn-safe). Returns ("ok", result | pickled) | ("cancelled",) |
    ("error", message, traceback)."""
    rt = p.get("rt") or _RT
    spec: RunSpec = p["spec"]
    store = p.get("store")
    key, level = p["store_key"], p["persist"]
    hook = _Hook(p["channel"], p["job_id"])
    try:
        hook.ch.update(p["job_id"], status="running")
        with progress.job_context(hook.callback, hook):
            progress.check_cancel()
            if p["live_args"]:
                args = p["args"]
            else:
                args = None
                from .runspec import from_dict
                args = from_dict(p["args"])
            lock = rt.lock(spec.dataset)
            with lock:
                lab = rt.job_lab(spec.dataset, store, p.get("session_id"))
                res = execute_spec(spec, lab, args)
                payload = rt.encoder(spec, res, lab) if rt.encoder is not None else None
        if store is not None and key is not None and level != "none":
            store.finish_run(key, res, level, payload)
        return ("ok", res if p["in_process"] else _dumps(res, spec.kind), payload)
    except Cancelled:
        if store is not None and key is not None:
            store.fail_run(key, "cancelled", "cancelled")
        return ("cancelled",)
    except BaseException as e:                                # noqa: BLE001  (reported, not swallowed)
        msg = f"{type(e).__name__}: {e}"
        if store is not None and key is not None:
            store.fail_run(key, msg)
        return ("error", msg, traceback.format_exc())


# ---------------------------------------------------------------------------------------------------
# Job and JobManager
# ---------------------------------------------------------------------------------------------------
class Job:
    """Handle on a submitted run."""

    def __init__(self, job_id, spec, run_key, store_key, dataset_id_, channel, cache_hit=False):
        self.id, self.spec, self.run_key, self.store_key = job_id, spec, run_key, store_key
        self.dataset_id, self.cache_hit = dataset_id_, cache_hit
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None
        self.traceback: str | None = None
        self._channel = channel
        self._final: str | None = None
        self._result: Any = None
        self._blob: bytes | None = None
        self._future: cf.Future | None = None
        self._max_progress = 0.0
        self._done = threading.Event()
        self._manager: JobManager | None = None
        self._in_process = True
        self._payload = None

    # ---- state
    @property
    def status(self) -> str:
        if self._final:
            return self._final
        s = self._channel.get(self.id) if self._channel is not None else None
        return s["status"] if s and s["status"] in ("queued", "running") else "queued"

    @property
    def progress(self) -> float:
        if self._final == "done":
            return 1.0
        s = self._channel.get(self.id) if self._channel is not None and not self._final else None
        if s:
            self._max_progress = max(self._max_progress, float(s["progress"] or 0.0))
        return self._max_progress

    @property
    def message(self) -> str:
        if self._final:
            return self._final
        s = self._channel.get(self.id) if self._channel is not None else None
        return (s or {}).get("message", "") or ""

    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def result(self, timeout: float | None = None):
        """The result (blocking). The live result object when this job computed it; a `StoredResult` on a
        cache hit. Raises `JobError` if the run failed, `Cancelled` if it was cancelled, TimeoutError."""
        if not self._done.wait(timeout):
            raise TimeoutError(f"job {self.id} still {self.status}")
        if self._final == "failed":
            raise JobError(self.error or "failed", self.traceback or "")
        if self._final == "cancelled":
            raise Cancelled("job cancelled")
        if self._blob is not None:
            self._result, self._blob = pickle.loads(self._blob), None
        return self._result

    def result_json(self):
        """The JSON payload the manager's encoder made for this run (None without an encoder); for a cache
        hit, the one stored with the original run."""
        if self.cache_hit:
            return self._result.payload()
        self.result()
        return self._payload

    def cancel(self) -> bool:
        """Ask the job to stop (queued jobs are dropped at once, running ones stop at the next progress
        check). False if it had already finished."""
        if self._done.is_set():
            return False
        if self._channel is not None:
            self._channel.request_cancel(self.id)
        if self._future is not None:
            self._future.cancel()
        return True

    def to_dict(self) -> dict:
        return {"job_id": self.id, "run_key": self.run_key, "kind": self.spec.kind, "dataset": self.spec.dataset,
                "status": self.status, "progress": self.progress, "message": self.message,
                "cache_hit": self.cache_hit, "error": self.error, "label": self.spec.label,
                "persistable": self.run_key is not None, "created_at": self.created_at,
                "finished_at": self.finished_at}

    def __repr__(self):
        return f"Job({self.id[:8]}, {self.spec.kind}, {self.status}, {self.progress:.0%})"

    # ---- manager side
    def _finish(self, status, result=None, blob=None, error=None, tb=None):
        self._final, self._result, self._blob = status, result, blob
        self.error, self.traceback, self.finished_at = error, tb, time.time()
        self._done.set()


class JobManager:
    """Runs RunSpecs asynchronously with dedup, progress, cancellation and optional persistence.

    store     a RunStore (default NullStore). With a persistent store, finished runs are cached by run_key
              and job state is mirrored in its `jobs` table.
    workers   pool size; None = cores - 1 (process) / up to 4 (thread). The string "thread" is accepted as
              a shorthand for mode="thread".
    mode      "process" (spawn) or "thread".
    labs      {dataset name: zero-arg factory returning a Lab} (or a ready Lab in thread mode).
    persist   persist level for stored runs: none | inputs | metrics | full.
    encoder   optional (spec, result, lab) -> JSON-able, run in the worker and stored with each run so cache
              hits can serve the same JSON as the synchronous endpoint (an importable function in process mode).
    """

    def __init__(self, store=None, workers=None, mode="process", labs: dict | None = None, persist="metrics",
                 encoder=None, session_id: str | None = None, dataset_ids: dict | None = None,
                 max_finished: int = 500):
        if workers == "thread":
            workers, mode = None, "thread"
        if mode not in ("process", "thread"):
            raise ValueError("mode must be 'process' or 'thread'")
        if persist not in LEVELS:
            raise ValueError(f"persist must be one of {LEVELS}")
        self.store = store if store is not None else NullStore()
        self.mode, self.persist, self.encoder = mode, persist, encoder
        self.labs = dict(labs or {})
        self.session_id = session_id or uuid.uuid4().hex
        cores = os.cpu_count() or 2
        self.workers = int(workers) if workers else (max(1, cores - 1) if mode == "process" else min(4, cores))
        self._rt = _Runtime(self.labs, encoder)                 # this process: probes and thread-mode runs
        self._jobs: dict[str, Job] = {}
        self._inflight: dict[str, Job] = {}
        self._dsids: dict = {k: (v, None) for k, v in (dataset_ids or {}).items()}
        self._lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._pool = None
        self._threads = None
        self._mgr = None
        self._mgr_channel = None
        self._local = _LocalChannel()
        self._max_finished = max_finished
        self._closed = False

    # ---- pools
    def _executor(self, in_process: bool):
        if in_process or self.mode == "thread":
            if self._threads is None:
                self._threads = cf.ThreadPoolExecutor(self.workers if self.mode == "thread" else 2,
                                                      thread_name_prefix="rothery-job")
            return self._threads
        if self._pool is None:
            ctx = mp.get_context("spawn")
            self._pool = cf.ProcessPoolExecutor(self.workers, mp_context=ctx, initializer=_init_worker,
                                                initargs=(self.labs, self.encoder))
        return self._pool

    def _channel(self, in_process: bool):
        if in_process or self.mode == "thread":
            return self._local
        if getattr(self.store, "persistent", False):
            return _StoreChannel(self.store)
        if self._mgr_channel is None:
            self._mgr = mp.get_context("spawn").Manager()
            self._mgr_channel = _ManagerChannel(self._mgr.dict(), self._mgr.dict())
        return self._mgr_channel

    # ---- dataset ids
    def dataset_info(self, name: str):
        """(dataset_id, manifest) for a named lab, computed once (in a worker in process mode)."""
        with self._probe_lock:
            hit = self._dsids.get(name)
            if hit is None:
                if name not in self.labs:
                    raise ValueError(f"unknown dataset {name!r}; available: {sorted(self.labs)}")
                if self.mode == "thread":
                    lab = self._rt.lab(name)
                    did, man = dataset_id(lab.market), dataset_manifest(lab.market, name, lab)
                else:
                    did, man = self._executor(False).submit(_probe, name).result()
                self._dsids[name] = (did, man)
                self.store.put_dataset(did, man)
            return self._dsids[name]

    def warm(self, *names: str) -> None:
        """Build the dataset fingerprints (and, in process mode, start a worker) ahead of the first submit."""
        for n in names or self.labs:
            self.dataset_info(n)

    # ---- submit
    def submit(self, spec, force: bool = False) -> Job:
        if self._closed:
            raise RuntimeError("JobManager is shut down")
        if isinstance(spec, dict):
            spec = RunSpec.from_dict(spec)
        if spec.kind not in KINDS:
            raise ValueError(f"unknown run kind {spec.kind!r}; available: {sorted(KINDS)}")
        if spec.dataset is None:
            if len(self.labs) != 1:
                raise ValueError(f"spec.dataset is required (labs: {sorted(self.labs)})")
            spec = RunSpec(spec.kind, next(iter(self.labs)), spec.args, spec.seed, spec.label, spec.tags)
        dsid, _ = self.dataset_info(spec.dataset)
        sargs = spec.serialized_args()
        persistable = spec.persistable
        key = spec.run_key(dsid) if persistable else None
        with self._lock:
            if key is not None:
                fam = spec.family() + ("|runs" if spec.kind in ("sweep", "exit_calibrate") else "")
                self.store.record_trial(self.session_id, key, fam)
                if not force:
                    hit = self.store.cached(key)
                    if hit is not None and self.encoder is not None and not self.store.has_payload(key):
                        hit = None                                 # recorded without JSON (e.g. by the SDK): redo
                    if hit is not None:
                        job = Job(uuid.uuid4().hex, spec, key, key, dsid, None, cache_hit=True)
                        job._manager = self
                        job._finish("done", result=hit)
                        self._remember(job)
                        return job
                live = self._inflight.get(key)
                if live is not None and not live.done():
                    return live
            in_process = self.mode == "thread" or not persistable
            channel = self._channel(in_process)
            job_id = uuid.uuid4().hex
            store_key = self.store.begin_run(spec, key, dsid, self.persist) if self.persist != "none" else key
            job = Job(job_id, spec, key, store_key, dsid, channel)
            job._manager = self
            channel.put(job_id, "queued", key)
            if getattr(self.store, "persistent", False) and not isinstance(channel, _StoreChannel):
                self.store.job_put(job_id, key, "queued")
            job._in_process = in_process
            wire = spec if in_process else RunSpec(spec.kind, spec.dataset, sargs, spec.seed, spec.label, spec.tags)
            payload = {"job_id": job_id, "spec": wire, "session_id": self.session_id, "store_key": store_key, "persist": self.persist,
                       "channel": channel, "in_process": in_process, "live_args": not persistable,
                       "args": spec.args if not persistable else sargs,
                       "store": self.store if getattr(self.store, "persistent", False) else None}
            if in_process:
                payload["rt"] = self._rt
            self._jobs[job_id] = job
            if key is not None:
                self._inflight[key] = job
            fut = self._executor(in_process).submit(_run_job, payload)
            job._future = fut
            fut.add_done_callback(lambda f, j=job: self._on_done(j, f))
            self._trim()
            return job

    def _remember(self, job):
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

    def _trim(self):
        fin = [j for j in self._jobs.values() if j.done()]
        for j in sorted(fin, key=lambda j: j.finished_at or 0)[:max(0, len(fin) - self._max_finished)]:
            self._jobs.pop(j.id, None)

    def _on_done(self, job: Job, fut: cf.Future) -> None:
        status, result, blob, err, tb = "failed", None, None, None, None
        try:
            out = fut.result()
        except cf.CancelledError:
            status = "cancelled"
            self.store.fail_run(job.store_key, "cancelled", "cancelled")
        except BaseException as e:                              # worker died, pickling failed, ...
            err, tb = f"{type(e).__name__}: {e}", traceback.format_exc()
            self.store.fail_run(job.store_key, err)
        else:
            if out[0] == "ok":
                status = "done"
                job._payload = out[2]
                result, blob = (out[1], None) if job._in_process else (None, out[1])
            elif out[0] == "cancelled":
                status = "cancelled"
            else:
                err, tb = out[1], out[2]
        try:
            if getattr(self.store, "persistent", False):
                self.store.job_update(job.id, status=status, progress=1.0 if status == "done" else None)
        except Exception:                                       # the job's own outcome matters more
            pass
        with self._lock:
            if job.run_key is not None and self._inflight.get(job.run_key) is job:
                del self._inflight[job.run_key]
        job._finish(status, result, blob, err, tb)
        if job._channel is not None:
            job._channel.drop(job.id)

    # ---- inspection and control
    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"unknown job {job_id!r}") from None

    def jobs(self, status: str | None = None) -> list:
        with self._lock:
            js = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j for j in js if status is None or j.status == status]

    def cancel(self, job_id: str) -> bool:
        return self.get(job_id).cancel()

    def shutdown(self, wait: bool = True, cancel_pending: bool = True) -> None:
        self._closed = True
        if cancel_pending:
            for j in self.jobs():
                if not j.done():
                    j.cancel()
        for ex in (self._pool, self._threads):
            if ex is not None:
                ex.shutdown(wait=wait, cancel_futures=cancel_pending)
        if self._mgr is not None:
            self._mgr.shutdown()
        self._pool = self._threads = self._mgr = self._mgr_channel = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()
