"""Trial counting and run recording for `Lab` (DESIGN.md I3): the glue between the SDK and a RunStore.

`@tracked(kind)` wraps a Lab method. Per call it (1) counts the call as a trial of its family (same kind +
dataset + screen base) when it is a new run_key, (2) records the finished run in `lab.store` when that store
is persistent, and (3) leaves `lab.last_trials()` = {"trials_in_family", "trials_scope"} for the result JSON.
Inside a job (`progress.active()`) the job manager already did (1) and (2) for the job itself.

Sweeps and calibrations also count every grid point as a trial of their family (`note_points`); the family
count then feeds the deflated Sharpe (`n_trials`). The sweep's own run is counted under `<family>|runs`.
Without a persistent store the counter lives in this process (`trials_scope == "process"`).
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import itertools
import logging
from collections.abc import Mapping

from . import progress
from .runspec import RunSpec, canonical_json, code_version, dataset_id, dataset_manifest, lab_context, run_key_for
from .store import NullStore

_log = logging.getLogger(__name__)
PROCESS_TRIALS = NullStore()
POINT_KINDS = ("sweep", "exit_calibrate")


def trial_store(lab):
    s = getattr(lab, "store", None)
    return s if s is not None and getattr(s, "persistent", False) else PROCESS_TRIALS


def scope(lab) -> str:
    return "store" if trial_store(lab) is not PROCESS_TRIALS else "process"


def _spec(lab, kind, args) -> RunSpec:
    return RunSpec(kind, lab.name or "lab", args)


def _publish(lab, kind, args, family):
    n = trial_store(lab).trial_count(family)
    lab._tl.last = {"trials_in_family": n, "trials_scope": scope(lab), "family": family, "kind": kind}
    return n


def note_trial(lab, kind, args) -> int:
    """Count this call as a trial (no-op for a repeated run_key); returns the family's trial count."""
    spec = _spec(lab, kind, args)
    fam = spec.family()
    if kind in POINT_KINDS:
        fam_own = fam + "|runs"
    else:
        fam_own = fam
    key = run_key_for(spec, dataset_id(lab.market), lab_context(lab))
    if key is not None and not progress.active():
        trial_store(lab).record_trial(lab.session_id, key, fam_own)
    return _publish(lab, kind, args, fam)


def note_points(lab, kind, args, points) -> int:
    """Count each grid point of a sweep / calibration as a trial of the family; returns the family count."""
    spec = _spec(lab, kind, args)
    fam = spec.family()
    st = trial_store(lab)
    try:
        base = {k: v for k, v in args.items() if k not in ("grid", "splits")}
        base_json = canonical_json({"kind": kind + "_point", "args": base})
        did, cv = dataset_id(lab.market) + ":" + lab_context(lab).hash, code_version()
        for p in points:
            key = hashlib.sha256(f"{base_json}|{canonical_json(dict(p))}|{did}|{cv}".encode()).hexdigest()
            st.record_trial(lab.session_id, key, fam)
    except Exception:                                          # unserializable inputs: count rows only
        _log.debug("trial points not recorded", exc_info=True)
    return _publish(lab, kind, args, fam)


def grid_points(grid) -> list:
    """Points of a Grid, a {key: [values]} mapping or a list of point dicts."""
    if isinstance(grid, Mapping):
        return [dict(zip(grid, c)) for c in itertools.product(*[list(v) for v in grid.values()])]
    return [dict(p) for p in grid]


def bound_args(sig: inspect.Signature, *a, **kw) -> dict:
    """The arguments a call passed explicitly (positional ones by name, **kwargs flattened); no `self`."""
    b = sig.bind(*a, **kw)
    out = {}
    for name, v in list(b.arguments.items())[1:]:
        if sig.parameters[name].kind is inspect.Parameter.VAR_KEYWORD:
            out.update(v)
        else:
            out[name] = v
    return out


def tracked(kind: str, args_of=None):
    """Decorator for Lab methods; `args_of(self, *a, **kw) -> dict | None` overrides the default argument capture."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def inner(self, *a, **kw):
            if getattr(self._tl, "args", None) is not None:       # nested in a tracked call (a sweep's backtests)
                return fn(self, *a, **kw)
            try:
                args = args_of(self, *a, **kw) if args_of else bound_args(sig, self, *a, **kw)
                if args is not None:
                    n = note_trial(self, kind, args)
            except Exception:                                  # tracking must never break an analysis
                _log.warning("trial tracking failed", exc_info=True)
                args = None
            prev = getattr(self._tl, "args", None)
            self._tl.args = args
            try:
                res = fn(self, *a, **kw)
            finally:
                self._tl.args = prev
            if args is not None:
                _after(self, kind, args, res)
            return res
        return inner
    return deco


def _after(lab, kind, args, res) -> None:
    if type(res).__name__ == "BacktestReport":
        res._args = args
    st = getattr(lab, "store", None)
    if st is None or not getattr(st, "persistent", False) or progress.active() or lab.persist == "none":
        return
    try:
        spec = _spec(lab, kind, args)
        did = dataset_id(lab.market)
        if not getattr(lab, "_dataset_put", False):
            st.put_dataset(did, dataset_manifest(lab.market, lab.name, lab))
            lab._dataset_put = True
        key = run_key_for(spec, did, lab_context(lab))
        if key is not None and st.cached(key) is not None:      # identical run already stored (maybe with JSON)
            return
        st.record(spec, res, key, did, lab.persist)
    except Exception:
        _log.warning("could not record run in the store", exc_info=True)
