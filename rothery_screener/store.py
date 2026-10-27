"""Optional persistence of run inputs and output metrics (DESIGN.md I3).

    store = SQLiteStore("runs.sqlite")              # stdlib sqlite3 only; safe for several processes (WAL)
    key = store.record(spec, report, dataset_id=dataset_id(lab.market), level="full")
    store.find(kind="backtest", where={"Excess/yr": (">", 0.02)})     # runs x metrics DataFrame
    store.compare([k1, k2])                                           # side-by-side table
    store.rerun(k1, executor)                                         # recompute and check the metrics match

`NullStore` is the default everywhere: it keeps nothing and writes no files, except an in-memory trial
counter (so the deflated-Sharpe guardrail still works within one process). Persist levels:

    none      nothing recorded for the run (trials are still counted)
    inputs    the spec only (an audit trail of what was tried); never served as a cache hit
    metrics   also the output tables, long format in `metrics` (default when a store is on)
    full      also the bulky tables (equity curves, per-offset / per-point tables) as gzip-CSV artifacts

A spec that is not persistable (a Python callable input) is recorded under a pseudo key
`unpersistable:<id>`: metrics only, never a cache hit, no `rerun`.
"""
from __future__ import annotations

import dataclasses
import gzip
import io
import json
import operator
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .runspec import RunSpec, code_version

LEVELS = ("none", "inputs", "metrics", "full")
BULK_NAMES = ("curves", "tranche", "offsets")
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------------------------------
# turning a result object into tables
# ---------------------------------------------------------------------------------------------------
@dataclass
class Extracted:
    summary: dict = field(default_factory=dict)      # name -> DataFrame, goes to `metrics` (numeric part)
    bulk: dict = field(default_factory=dict)         # name -> DataFrame, artifacts at level "full"

    def all_tables(self) -> dict:
        return {**self.summary, **self.bulk}


def _as_frame(x):
    if isinstance(x, pd.Series):
        return x.to_frame(str(x.name) if x.name is not None else "value")
    return x


def _is_bulk(name: str, df: pd.DataFrame) -> bool:
    return (name.rsplit("/", 1)[-1] in BULK_NAMES) or (isinstance(df.index, pd.DatetimeIndex) and len(df) > 60)


def _collect(obj, prefix: str, out: Extracted, depth: int = 0) -> None:
    if obj is None or depth > 4:
        return
    name = prefix or "table"
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        df = _as_frame(obj)
        (out.bulk if _is_bulk(name, df) else out.summary)[name] = df
        return
    cls = type(obj).__name__
    j = lambda k: f"{prefix}/{k}" if prefix else k
    if cls == "BacktestReport":
        out.summary[j("table")] = obj.table
        out.bulk[j("curves")] = obj.curves
    elif cls == "TimingResult":
        out.summary[j("distribution")] = obj.distribution()
        out.bulk[j("offsets")] = obj.table
        out.bulk[j("curves")] = obj.curves
        if obj.tranche is not None:
            out.bulk[j("tranche")] = _as_frame(obj.tranche.rename("tranche"))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _collect(v, j(str(k)), out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _collect(v, j(f"part{i}"), out, depth + 1)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            _collect(getattr(obj, f.name), j(f.name), out, depth + 1)


def extract(result) -> Extracted:
    """Tables of a result: BacktestReport (table + curves), TimingResult (distribution + per-offset +
    curves), DataFrame / Series, tuples, dicts and dataclasses (every DataFrame / Series field)."""
    out = Extracted()
    _collect(result, "", out)
    return out


def _label(i) -> str:
    return "/".join(map(str, i)) if isinstance(i, tuple) else str(i)


def long_rows(ex: Extracted) -> list:
    """(row_label, metric, value) for the numeric cells of the summary tables. The first table's rows keep
    their plain labels; later tables' rows are prefixed `<table>/`."""
    rows = []
    for n, (name, df) in enumerate(ex.summary.items()):
        num = df.select_dtypes(include=["number", "bool"])
        pre = "" if n == 0 else f"{name}/"
        for lab, r in zip(num.index, num.to_numpy(dtype=float)):
            for m, v in zip(num.columns, r):
                rows.append((pre + _label(lab), str(m), None if np.isnan(v) else float(v)))
    return rows


def wide(rows: list) -> pd.DataFrame:
    """Long (row_label, metric, value) rows back to a row_label x metric DataFrame (first-seen order)."""
    data: dict = {}
    cols: dict = {}
    for lab, m, v in rows:
        data.setdefault(lab, {})[m] = np.nan if v is None else v
        cols.setdefault(m, None)
    df = pd.DataFrame.from_dict(data, orient="index", columns=list(cols)) if data else pd.DataFrame()
    return df.astype(float)


def _csv_gz(df: pd.DataFrame) -> tuple[bytes, str]:
    kind = "datetime" if isinstance(df.index, pd.DatetimeIndex) else "plain"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(df.to_csv().encode())
    return buf.getvalue(), f"csv.gz;index={kind}"


def _read_csv_gz(blob: bytes, fmt: str) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(blob), compression="gzip", index_col=0)
    if "index=datetime" in fmt:
        df.index = pd.to_datetime(df.index)
    return df


def _json_gz(obj) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(json.dumps(obj, allow_nan=False, default=str).encode())
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------
# stored runs
# ---------------------------------------------------------------------------------------------------
@dataclass
class StoredResult:
    """A run as read back from a store: the spec, status and the metrics table (plus lazy artifacts)."""
    run_key: str
    kind: str
    status: str
    spec: RunSpec
    metrics: pd.DataFrame                       # row_label x metric
    label: str | None = None
    tags: tuple = ()
    created_at: float = 0.0
    finished_at: float | None = None
    dataset_id: str | None = None
    code_version: str | None = None
    persist_level: str = "metrics"
    persistable: bool = True
    error: str | None = None
    _store: object = field(default=None, repr=False)

    @property
    def table(self) -> pd.DataFrame:
        return self.metrics

    def artifact_names(self) -> list:
        return self._store.artifact_names(self.run_key) if self._store is not None else []

    def artifact(self, name: str) -> pd.DataFrame:
        return self._store.artifact(self.run_key, name)

    def payload(self):
        """The JSON payload stored with the run (what the matching synchronous endpoint returns), if any."""
        return self._store.payload(self.run_key) if self._store is not None else None


@dataclass
class RerunReport:
    run_key: str
    match: bool
    max_abs_diff: float
    dataset_changed: bool | None            # None when no current dataset_id was supplied
    code_changed: bool
    stored: pd.DataFrame
    new: pd.DataFrame
    new_run_key: str | None = None


@runtime_checkable
class RunStore(Protocol):
    persistent: bool

    def begin_run(self, spec, run_key=None, dataset_id=None, level="metrics", code_version_=None) -> str | None: ...
    def finish_run(self, run_key, result=None, level="metrics", payload=None) -> None: ...
    def fail_run(self, run_key, error, status="failed") -> None: ...
    def get(self, run_key) -> StoredResult | None: ...
    def cached(self, run_key) -> StoredResult | None: ...
    def record_trial(self, session_id, run_key, family) -> bool: ...
    def trial_count(self, family, session_id=None) -> int: ...


# ---------------------------------------------------------------------------------------------------
# NullStore
# ---------------------------------------------------------------------------------------------------
class NullStore:
    """Stores nothing and touches no files. Counts trials in memory (per process)."""
    persistent = False
    path = None

    def __init__(self):
        self._trials: dict = {}
        self._lock = threading.Lock()

    def begin_run(self, spec, run_key=None, dataset_id=None, level="metrics", code_version_=None):
        return run_key

    def finish_run(self, run_key, result=None, level="metrics", payload=None):
        return None

    def fail_run(self, run_key, error, status="failed"):
        return None

    def record(self, spec, result, run_key=None, dataset_id=None, level="metrics", payload=None):
        return run_key

    def get(self, run_key):
        return None

    cached = get

    def has_payload(self, run_key):
        return False

    def find(self, **kw):
        return pd.DataFrame()

    def compare(self, run_keys, metrics=None, rows=None):
        return pd.DataFrame()

    def put_dataset(self, dataset_id, manifest):
        return None

    def datasets(self):
        return pd.DataFrame()

    # trials (memory only)
    def record_trial(self, session_id, run_key, family) -> bool:
        with self._lock:
            s = self._trials.setdefault(family, set())
            new = (session_id, run_key) not in s
            s.add((session_id, run_key))
            return new

    def trial_count(self, family, session_id=None) -> int:
        with self._lock:
            return len({k for sid, k in self._trials.get(family, ()) if session_id in (None, sid)})

    # jobs: nothing shared to persist
    def job_put(self, *a, **k):
        return None

    job_update = job_put
    job_request_cancel = job_put

    def job_get(self, job_id):
        return None

    def job_cancel_requested(self, job_id):
        return False

    def job_list(self, status=None):
        return []

    def close(self):
        return None


# ---------------------------------------------------------------------------------------------------
# SQLiteStore
# ---------------------------------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_key TEXT PRIMARY KEY, kind TEXT, status TEXT, created_at REAL, finished_at REAL,
  dataset_id TEXT, dataset TEXT, code_version TEXT, spec_json TEXT, label TEXT, tags TEXT,
  persist_level TEXT, error TEXT, persistable INTEGER, reason TEXT, screens TEXT, family TEXT);
CREATE TABLE IF NOT EXISTS metrics (run_key TEXT, row_label TEXT, metric TEXT, value REAL);
CREATE TABLE IF NOT EXISTS artifacts (run_key TEXT, name TEXT, format TEXT, bytes BLOB, PRIMARY KEY (run_key, name));
CREATE TABLE IF NOT EXISTS datasets (dataset_id TEXT PRIMARY KEY, name TEXT, span TEXT, n_names INTEGER,
  benchmark TEXT, benchmark_kind TEXT, manifest_json TEXT);
CREATE TABLE IF NOT EXISTS trials (session_id TEXT, run_key TEXT, family TEXT, created_at REAL,
  PRIMARY KEY (session_id, run_key));
CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, run_key TEXT, status TEXT, progress REAL,
  message TEXT, updated_at REAL, cancel_requested INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_metrics_run ON metrics (run_key);
CREATE INDEX IF NOT EXISTS ix_metrics_metric ON metrics (metric, value);
CREATE INDEX IF NOT EXISTS ix_runs_kind ON runs (kind, created_at);
CREATE INDEX IF NOT EXISTS ix_trials_family ON trials (family);
"""
_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "==": "=", "!=": "!=", "<>": "!="}


class SQLiteStore:
    """RunStore on a SQLite file. One connection per thread, WAL journal, short write transactions with a
    busy timeout, so a server process and its worker processes can share the file. Picklable (reconnects)."""
    persistent = True

    def __init__(self, path):
        self.path = str(Path(path).expanduser())
        if self.path == ":memory:":
            raise ValueError("SQLiteStore needs a file path (connections are per thread / process)")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list = []
        self._lock = threading.Lock()
        db = self._db()
        db.execute("PRAGMA journal_mode=WAL")
        with self._tx() as c:
            for stmt in filter(None, (s.strip() for s in _SCHEMA.split(";"))):
                c.execute(stmt)
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ---- plumbing
    def __getstate__(self):
        return {"path": self.path}

    def __setstate__(self, st):
        self.path = st["path"]
        self._local = threading.local()
        self._conns = []
        self._lock = threading.Lock()

    def __repr__(self):
        return f"SQLiteStore({self.path!r})"

    def _db(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
            with self._lock:
                self._conns.append(c)
        return c

    @contextmanager
    def _tx(self):
        c = self._db()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._conns = []
        self._local = threading.local()

    # ---- run lifecycle
    def begin_run(self, spec: RunSpec, run_key=None, dataset_id=None, level="metrics", code_version_=None):
        """Insert (or reset) the run's row as `running`; returns the key used (a pseudo key for
        unpersistable specs, which get a fresh one per call)."""
        if level not in LEVELS:
            raise ValueError(f"persist level must be one of {LEVELS}")
        if level == "none":
            return run_key
        d = spec.to_dict()
        key = run_key or f"unpersistable:{uuid.uuid4().hex[:16]}"
        now = time.time()
        screens = "," + ",".join(spec.screen_names()) + ","
        with self._tx() as c:
            c.execute(
                "INSERT INTO runs (run_key, kind, status, created_at, dataset_id, dataset, code_version, spec_json,"
                " label, tags, persist_level, persistable, reason, screens, family) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_key) DO UPDATE SET status='running', created_at=excluded.created_at,"
                " finished_at=NULL, error=NULL, label=excluded.label, tags=excluded.tags,"
                " persist_level=excluded.persist_level, spec_json=excluded.spec_json",
                (key, spec.kind, "running", now, dataset_id, spec.dataset, code_version_ or code_version(),
                 json.dumps(d, sort_keys=True), spec.label, json.dumps(list(spec.tags)), level,
                 int(d["persistable"]), d["reason"], screens, spec.family()))
        return key

    def finish_run(self, run_key, result=None, level="metrics", payload=None) -> None:
        if run_key is None or level == "none":
            return
        rows, arts = [], {}
        if level != "inputs" and result is not None:
            ex = extract(result)
            rows = long_rows(ex)
            if level == "full":
                arts = {n: _csv_gz(df) for n, df in ex.all_tables().items()}
        if payload is not None and level in ("metrics", "full"):
            arts["result_json"] = (_json_gz(payload), "json.gz")
        with self._tx() as c:
            c.execute("DELETE FROM metrics WHERE run_key=?", (run_key,))
            c.execute("DELETE FROM artifacts WHERE run_key=?", (run_key,))
            c.executemany("INSERT INTO metrics VALUES (?,?,?,?)", [(run_key, *r) for r in rows])
            c.executemany("INSERT INTO artifacts VALUES (?,?,?,?)", [(run_key, n, f, b) for n, (b, f) in arts.items()])
            c.execute("UPDATE runs SET status='done', finished_at=?, error=NULL, persist_level=? WHERE run_key=?",
                      (time.time(), level, run_key))

    def fail_run(self, run_key, error, status="failed") -> None:
        if run_key is None:
            return
        with self._tx() as c:
            c.execute("UPDATE runs SET status=?, finished_at=?, error=? WHERE run_key=?",
                      (status, time.time(), str(error), run_key))

    def record(self, spec, result, run_key=None, dataset_id=None, level="metrics", payload=None):
        """begin + finish in one call (the synchronous-SDK path). Returns the key."""
        key = self.begin_run(spec, run_key, dataset_id, level)
        self.finish_run(key, result, level, payload)
        return key

    # ---- reading
    def _stored(self, row, with_metrics=True) -> StoredResult:
        (key, kind, status, created, finished, dsid, _ds, cv, spec_json, label, tags, level, error, pers) = row
        spec = RunSpec.from_dict(json.loads(spec_json))
        rows = (self._db().execute("SELECT row_label, metric, value FROM metrics WHERE run_key=? ORDER BY rowid",
                                   (key,)).fetchall() if with_metrics else [])
        return StoredResult(key, kind, status, spec, wide(rows), label, tuple(json.loads(tags or "[]")), created,
                            finished, dsid, cv, level, bool(pers), error, self)

    _COLS = ("run_key, kind, status, created_at, finished_at, dataset_id, dataset, code_version, spec_json, "
             "label, tags, persist_level, error, persistable")

    def get(self, run_key) -> StoredResult | None:
        r = self._db().execute(f"SELECT {self._COLS} FROM runs WHERE run_key=?", (run_key,)).fetchone()
        return self._stored(r) if r else None

    def cached(self, run_key) -> StoredResult | None:
        """The run if it finished with metrics (a usable cache hit), else None."""
        if run_key is None or str(run_key).startswith("unpersistable:"):
            return None
        r = self.get(run_key)
        return r if r and r.status == "done" and r.persist_level in ("metrics", "full") else None

    def spec(self, run_key) -> RunSpec:
        r = self._db().execute("SELECT spec_json FROM runs WHERE run_key=?", (run_key,)).fetchone()
        if r is None:
            raise KeyError(run_key)
        return RunSpec.from_dict(json.loads(r[0]))

    def artifact_names(self, run_key) -> list:
        return [r[0] for r in self._db().execute(
            "SELECT name FROM artifacts WHERE run_key=? AND name!='result_json' ORDER BY name", (run_key,))]

    def artifact(self, run_key, name) -> pd.DataFrame:
        r = self._db().execute("SELECT bytes, format FROM artifacts WHERE run_key=? AND name=?",
                               (run_key, name)).fetchone()
        if r is None:
            raise KeyError(f"no artifact {name!r} for run {run_key} (stored at persist level 'full' only)")
        return _read_csv_gz(r[0], r[1])

    def has_payload(self, run_key) -> bool:
        return self._db().execute("SELECT 1 FROM artifacts WHERE run_key=? AND name='result_json'",
                                  (run_key,)).fetchone() is not None

    def payload(self, run_key):
        r = self._db().execute("SELECT bytes FROM artifacts WHERE run_key=? AND name='result_json'",
                               (run_key,)).fetchone()
        return None if r is None else json.loads(gzip.decompress(r[0]).decode())

    # ---- queries
    def find(self, kind=None, screen=None, dataset=None, since=None, where=None, status="done", label=None,
             tag=None, row_label=None, limit=None, include_unpersistable=True) -> pd.DataFrame:
        """Runs x metrics. One row per (run, row_label) with the run's info columns first and one column per
        metric. `where={"Excess/yr": (">", 0.02), "MaxDD": ("between", (-0.4, -0.1))}` keeps the rows meeting
        every condition; `screen` matches entry-screen names, `dataset` a name or dataset_id, `since` a
        Timestamp / datetime / epoch seconds, `tag` one of the run's tags."""
        w, p = [], []
        for col, v in (("kind", kind), ("label", label)):
            if v is not None:
                w.append(f"r.{col}=?"); p.append(v)
        if status is not None:
            w.append("r.status=?"); p.append(status)
        if screen is not None:
            w.append("r.screens LIKE ?"); p.append(f"%,{screen},%")
        if dataset is not None:
            w.append("(r.dataset=? OR r.dataset_id=?)"); p += [dataset, dataset]
        if since is not None:
            w.append("r.created_at>=?"); p.append(_epoch(since))
        if tag is not None:
            w.append("r.tags LIKE ?"); p.append(f'%"{tag}"%')
        if not include_unpersistable:
            w.append("r.persistable=1")
        runs_sql = "SELECT r.run_key FROM runs r" + (" WHERE " + " AND ".join(w) if w else "")
        sub, sp = f"m.run_key IN ({runs_sql})", list(p)
        parts, pp = [], []
        for metric, cond in (where or {}).items():
            op, val = cond
            if op == "between":
                parts.append("SELECT m.run_key, m.row_label FROM metrics m WHERE m.metric=? AND m.value>=? AND m.value<=? AND " + sub)
                pp += [metric, val[0], val[1], *sp]
            else:
                if op not in _OPS:
                    raise ValueError(f"unsupported operator {op!r}; use {sorted(_OPS)} or 'between'")
                parts.append(f"SELECT m.run_key, m.row_label FROM metrics m WHERE m.metric=? AND m.value{_OPS[op]}? AND " + sub)
                pp += [metric, val, *sp]
        if parts:
            keyed = "SELECT run_key, row_label FROM (" + " INTERSECT ".join(parts) + ")"
        else:
            keyed = f"SELECT DISTINCT m.run_key, m.row_label FROM metrics m WHERE {sub}"
            pp = list(sp)
        if row_label is not None:
            keyed = f"SELECT run_key, row_label FROM ({keyed}) WHERE row_label=?"
            pp.append(row_label)
        db = self._db()
        pairs = db.execute(keyed, pp).fetchall()
        info = {r[0]: r for r in db.execute(
            f"SELECT r.run_key, r.kind, r.label, r.dataset, r.created_at, r.status FROM runs r WHERE r.run_key IN ({runs_sql})", p)}
        want = set(pairs)
        data: dict = {}
        for k in {k for k, _ in pairs}:
            for lab, m, v in db.execute("SELECT row_label, metric, value FROM metrics WHERE run_key=? ORDER BY rowid", (k,)):
                if (k, lab) in want:
                    data.setdefault((k, lab), {})[m] = np.nan if v is None else v
        head = ["run_key", "row_label", "kind", "label", "dataset", "created_at", "status"]
        recs = []
        for (k, lab), mv in data.items():
            i = info[k]
            recs.append({"run_key": k, "row_label": lab, "kind": i[1], "label": i[2], "dataset": i[3],
                         "created_at": pd.Timestamp(i[4], unit="s"), "status": i[5], **mv})
        if not recs:
            return pd.DataFrame(columns=head)
        df = pd.DataFrame(recs).sort_values(["created_at", "run_key", "row_label"], kind="stable").reset_index(drop=True)
        df = df[head + [c for c in df.columns if c not in head]]
        return df.head(limit) if limit else df

    def compare(self, run_keys, metrics=None, rows=None) -> pd.DataFrame:
        """Side by side: index (row_label, metric), one column per run (its label, else the key's first 10
        characters). `metrics` / `rows` restrict the table."""
        cols, seen = {}, {}
        for k in run_keys:
            r = self.get(k)
            if r is None:
                raise KeyError(f"unknown run {k}")
            name = r.label or k[:10]
            if name in seen:
                name = f"{name}#{k[:6]}"
            seen[name] = True
            m = r.metrics
            if metrics is not None:
                m = m[[c for c in m.columns if c in set(metrics)]]
            if rows is not None:
                m = m.loc[[i for i in m.index if i in set(rows)]]
            cols[name] = m.stack(future_stack=True) if len(m) else pd.Series(dtype=float)
        out = pd.DataFrame(cols)
        out.index.names = ["row_label", "metric"]
        return out

    # ---- rerun / export / retention
    def rerun(self, run_key, executor: Callable, dataset_id=None, atol: float = 1e-12, save: bool = False) -> RerunReport:
        """Rebuild the stored spec and recompute it with `executor(spec) -> result`; report whether the new
        metrics equal the stored ones. `dataset_id`: the current dataset's id (to flag a changed dataset).
        With `save=True` the new run is recorded under its own key (it differs if the data or code changed)."""
        old = self.get(run_key)
        if old is None:
            raise KeyError(f"unknown run {run_key}")
        if not old.persistable:
            raise ValueError(f"run {run_key} is not rerunnable: its spec holds an unpersistable input")
        res = executor(old.spec)
        ex = extract(res)
        new = wide(long_rows(ex))
        a, b = old.metrics.align(new, join="outer")
        diff = (a - b).abs()
        both_nan = a.isna() & b.isna()
        diff = diff.where(~both_nan, 0.0)
        mx = float(np.nanmax(diff.to_numpy())) if diff.size and not diff.isna().all().all() else 0.0
        if diff.size and diff.isna().any().any():
            mx = float("inf")                                   # a cell is missing on one side
        cv_changed = (old.code_version or "") != code_version()
        ds_changed = None if dataset_id is None else (old.dataset_id != dataset_id)
        new_key = None
        if save:
            new_key = old.spec.run_key(dataset_id, code_version()) if dataset_id else None
            self.record(old.spec, res, new_key, dataset_id, old.persist_level if old.persist_level != "inputs" else "metrics")
        return RerunReport(run_key, mx <= atol, mx, ds_changed, cv_changed, old.metrics, new, new_key)

    def delete(self, run_key) -> bool:
        with self._tx() as c:
            n = c.execute("DELETE FROM runs WHERE run_key=?", (run_key,)).rowcount
            c.execute("DELETE FROM metrics WHERE run_key=?", (run_key,))
            c.execute("DELETE FROM artifacts WHERE run_key=?", (run_key,))
        return n > 0

    def prune(self, keep_last: int | None = None, older_than=None) -> list:
        """Retention: delete the oldest finished runs beyond the newest `keep_last`, and/or runs older than
        `older_than` (seconds, timedelta or a Timestamp). Returns the deleted keys, oldest first."""
        rows = self._db().execute("SELECT run_key, created_at FROM runs WHERE status!='running' "
                                  "ORDER BY created_at, rowid").fetchall()
        doomed = []
        if keep_last is not None and len(rows) > keep_last:
            doomed = [k for k, _ in rows[:len(rows) - keep_last]]
        if older_than is not None:
            cut = (time.time() - older_than.total_seconds() if isinstance(older_than, timedelta)
                   else time.time() - float(older_than) if isinstance(older_than, (int, float))
                   else _epoch(older_than))
            doomed += [k for k, t in rows if t < cut and k not in doomed]
        for k in doomed:
            self.delete(k)
        return doomed

    def export(self, path, run_keys=None) -> str:
        """Write runs out. `.sqlite` / `.db`: a self-contained store file; `.json`: specs + metrics;
        `.csv`: the find() table. `run_keys` restricts to those runs (default all)."""
        path = str(path)
        ext = Path(path).suffix.lower()
        keys = list(run_keys) if run_keys is not None else [r[0] for r in self._db().execute("SELECT run_key FROM runs")]
        if ext in (".sqlite", ".db", ".sqlite3"):
            dst = sqlite3.connect(path)
            try:
                self._db().backup(dst)
                keep = set(keys)
                for (k,) in dst.execute("SELECT run_key FROM runs").fetchall():
                    if k not in keep:
                        for t in ("runs", "metrics", "artifacts"):
                            dst.execute(f"DELETE FROM {t} WHERE run_key=?", (k,))
                dst.commit()
                dst.execute("VACUUM")
            finally:
                dst.close()
        elif ext == ".json":
            out = []
            for k in keys:
                r = self.get(k)
                out.append({"run_key": k, "kind": r.kind, "status": r.status, "label": r.label, "tags": list(r.tags),
                            "dataset_id": r.dataset_id, "code_version": r.code_version, "spec": r.spec.to_dict(),
                            "metrics": {i: {m: (None if pd.isna(v) else v) for m, v in row.items()}
                                        for i, row in r.metrics.iterrows()}})
            Path(path).write_text(json.dumps(out, indent=1, sort_keys=True))
        elif ext == ".csv":
            df = self.find(status=None)
            df[df["run_key"].isin(keys)].to_csv(path, index=False)
        else:
            raise ValueError("export path must end in .sqlite, .db, .json or .csv")
        return path

    # ---- datasets
    def put_dataset(self, dataset_id: str, manifest: dict) -> None:
        span = "/".join(map(str, manifest.get("span") or ()))
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?,?,?)",
                      (dataset_id, manifest.get("name"), span, manifest.get("n_names"), manifest.get("benchmark"),
                       manifest.get("benchmark_kind"), json.dumps(manifest, sort_keys=True, default=str)))

    def datasets(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT dataset_id, name, span, n_names, benchmark, benchmark_kind FROM datasets",
                                 self._db())

    def dataset(self, dataset_id: str) -> dict | None:
        r = self._db().execute("SELECT manifest_json FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        return json.loads(r[0]) if r else None

    # ---- trials (the multiple-testing counter)
    def record_trial(self, session_id: str, run_key: str, family: str) -> bool:
        """Count `run_key` as a trial of `family` for this session; True if it was new."""
        with self._tx() as c:
            return c.execute("INSERT OR IGNORE INTO trials VALUES (?,?,?,?)",
                             (session_id, run_key, family, time.time())).rowcount > 0

    def trial_count(self, family: str, session_id: str | None = None) -> int:
        """Distinct run keys tried in `family` (by one session, or by every session)."""
        q, p = "SELECT COUNT(DISTINCT run_key) FROM trials WHERE family=?", [family]
        if session_id is not None:
            q += " AND session_id=?"; p.append(session_id)
        return int(self._db().execute(q, p).fetchone()[0])

    # ---- jobs (live state shared between the API process and worker processes)
    def job_put(self, job_id, run_key=None, status="queued", progress=0.0, message="") -> None:
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,0)",
                      (job_id, run_key, status, float(progress), message, time.time()))

    def job_update(self, job_id, status=None, progress=None, message=None) -> None:
        sets, p = ["updated_at=?"], [time.time()]
        for col, v in (("status", status), ("progress", progress), ("message", message)):
            if v is not None:
                sets.append(f"{col}=?"); p.append(v)
        with self._tx() as c:
            c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id=?", (*p, job_id))

    def job_request_cancel(self, job_id) -> None:
        with self._tx() as c:
            c.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE job_id=?", (time.time(), job_id))

    def job_cancel_requested(self, job_id) -> bool:
        r = self._db().execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(r and r[0])

    def job_get(self, job_id) -> dict | None:
        r = self._db().execute("SELECT job_id, run_key, status, progress, message, updated_at, cancel_requested "
                               "FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(zip(("job_id", "run_key", "status", "progress", "message", "updated_at", "cancel_requested"), r)) if r else None

    def job_list(self, status=None) -> list:
        q = "SELECT job_id FROM jobs" + (" WHERE status=?" if status else "") + " ORDER BY updated_at DESC"
        return [self.job_get(r[0]) for r in self._db().execute(q, (status,) if status else ())]


def _epoch(t) -> float:
    """Epoch seconds of a number or timestamp (naive timestamps are taken as UTC)."""
    if isinstance(t, (int, float)):
        return float(t)
    t = pd.Timestamp(t)
    return float(t.timestamp()) if t.tzinfo else float((t - pd.Timestamp(0)).total_seconds())
