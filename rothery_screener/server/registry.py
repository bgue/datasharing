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
import math
import os
import threading
from datetime import date, datetime

import numpy as np
import pandas as pd

from .. import progress

DEFAULT_MAX_POINTS = 500

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


class ServerError(Exception):
    """An error with an HTTP-style status (400 bad input, 404 unknown name, 501 not implemented)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ JSON serializers
def scalar(x):
    """One value -> JSON-safe Python (NaN/inf/NaT -> None, timestamps -> ISO strings)."""
    if x is None or x is pd.NaT:
        return None
    if isinstance(x, (pd.Timestamp, datetime, date, np.datetime64)):
        t = pd.Timestamp(x)
        return None if pd.isna(t) else t.isoformat()[:10] if (t == t.normalize()) else t.isoformat()
    if isinstance(x, (pd.Timedelta, np.timedelta64)):
        return None if pd.isna(x) else pd.Timedelta(x).days
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, (str,)):
        return x
    return str(x)


def table(df: pd.DataFrame, index: bool = True) -> dict:
    """DataFrame -> {"columns", "rows"}; the index (or each level of a MultiIndex) becomes leading column(s)."""
    d = df
    if index:
        names = [n if n is not None else "index" for n in d.index.names]
        d = d.reset_index()
        d.columns = [str(c) for c in names] + [str(c) for c in d.columns[len(names):]]
    else:
        d = d.copy()
        d.columns = [str(c) for c in d.columns]
    rows = [[scalar(v) for v in r] for r in d.astype(object).itertuples(index=False, name=None)]
    return {"columns": list(d.columns), "rows": rows}


def series_table(s: pd.Series, key: str = "key", value: str = "value") -> dict:
    return {"columns": [key, value], "rows": [[scalar(k), scalar(v)] for k, v in s.items()]}


def downsample_index(n: int, max_points: int) -> np.ndarray:
    if max_points is None or max_points <= 0 or n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def curve(s: pd.Series, max_points: int = DEFAULT_MAX_POINTS) -> list:
    """Series -> [[iso_date, value], ...], at most `max_points` evenly spaced (first and last kept)."""
    s = s.dropna() if s.dtype != object else s
    pos = downsample_index(len(s), max_points)
    return [[scalar(s.index[i]), scalar(s.iloc[i])] for i in pos]


def curves(df, max_points: int = DEFAULT_MAX_POINTS) -> dict:
    if isinstance(df, pd.Series):
        df = df.to_frame(df.name or "value")
    return {str(c): curve(df[c], max_points) for c in df.columns}


def jsonify(x, max_points: int = DEFAULT_MAX_POINTS):
    """Best-effort conversion of results (DataFrames, Series, dataclasses, dicts, scalars) to JSON."""
    if isinstance(x, pd.DataFrame):
        return table(x)
    if isinstance(x, pd.Series):
        if isinstance(x.index, pd.DatetimeIndex) and len(x) > 50:
            return curve(x, max_points)
        return series_table(x)
    if isinstance(x, dict):
        return {str(k): jsonify(v, max_points) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonify(v, max_points) for v in x]
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: jsonify(getattr(x, f.name), max_points) for f in dataclasses.fields(x)}
    return scalar(x)


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
    """The server's store: `ROTHERY_STORE` (a path; "" / "none" / "off" disables), else ~/.rothery/runs.sqlite.
    Falls back to no persistence, with a warning, if the file cannot be opened."""
    from ..store import NullStore, SQLiteStore
    env = os.environ.get("ROTHERY_STORE")
    if env is not None and env.strip().lower() in ("", "none", "off"):
        return NullStore()
    path = env or os.path.join(os.path.expanduser("~"), ".rothery", "runs.sqlite")
    try:
        return SQLiteStore(path)
    except Exception as e:                                          # read-only home, bad path, ...
        _log.warning("run store %s unavailable (%s); persistence is off", path, e)
        return NullStore()


def report_json(rep, max_points) -> dict:
    c = rep.config
    cfg = {f.name: scalar(getattr(c, f.name)) for f in dataclasses.fields(c)
           if isinstance(getattr(c, f.name), (int, float, str, type(None)))}
    return {"benchmark": rep.benchmark_label, "config": cfg, "table": table(rep.table),
            "curves": curves(rep.curves, max_points)}


def result_json(kind: str, L, kw: dict, res, max_points=DEFAULT_MAX_POINTS) -> dict:
    """The JSON a run of `kind` returns (same for the synchronous operation and the job result). `L` is the
    lab it ran on, `kw` the Lab keyword arguments, `res` the result. Carries `trials_in_family`."""
    if kind == "backtest":
        out = report_json(res, max_points)
    elif kind == "timing":
        out = {"benchmark": L.passive().label, "screen": res.screen, "table": table(res.table),
               "distribution": table(res.distribution())}
        if res.tranche is not None:
            out["tranche"] = curve(res.tranche, max_points)
    elif kind == "graham":
        rep, funnel = res
        out = {"preset": kw.get("tests", "defensive"), "table": table(rep),
               "funnel": series_table(funnel, "step", "count")}
    elif kind == "sweep":
        out = {"benchmark": L.passive().label, "screen": kw["screen"], "objective": kw.get("objective", "Excess/yr"),
               "table": table(res, index=False)}
        summ = res.attrs.get("summary")
        if summ is not None:
            out["summary"] = jsonify(summ)
        if "walk_forward" in res.attrs:
            out["walk_forward"] = jsonify(res.attrs["walk_forward"])
        try:
            from ..stats import summarize_trials
            n = int(res.attrs.get("n_trials") or len(res))
            out["n_trials"] = n
            out["dsr"] = scalar(summarize_trials(res, n_trials=n)["DSR"].max())
        except Exception:                                           # e.g. a custom objective table without Sharpe
            pass
    elif kind in ("significance", "extended"):
        out = {"benchmark": L.passive(kw.get("benchmark")).label, "table": table(res)}
    elif kind == "exit_evaluate":
        ov = res.overlay
        out = {"benchmark": L.passive().label, "frac_flagged": scalar(res.frac_flagged), "forward": table(res.forward),
               "drawdowns": table(res.drawdowns, index=False), "overlay_stats": table(ov.stats),
               "switches": ov.switches, "turnover": scalar(ov.turnover),
               "curves": {"derisked": curve(ov.curve, max_points), "always_in": curve(ov.baseline, max_points)},
               "score": curve(res.score, max_points),
               "state": [[d, bool(v)] for d, v in curve(res.state.astype(float), max_points)]}
    elif kind == "exit_calibrate":
        out = jsonify(res)
        if isinstance(out, dict):
            out = {"benchmark": L.passive().label, **out}
    elif kind == "picks":
        out = {"screen": kw["screen"], "asof": scalar(L._asof(kw.get("asof"))), **table(res)}
    else:
        out = jsonify(res)
    info = L.last_trials() if hasattr(L, "last_trials") else None
    if isinstance(out, dict) and info:
        out["trials_in_family"] = info["trials_in_family"]
        out["trials_scope"] = info["trials_scope"]
    return out


def encode_result(spec, res, lab) -> dict:
    """JobManager encoder: the JSON payload of a finished run (runs in the worker, importable for spawn)."""
    from ..jobs import live_args
    return result_json(spec.kind, lab, live_args(spec), res)


class LabRegistry:
    """Named labs, built on first use. `factories` maps name -> zero-arg callable (or a ready Lab).

    store         a RunStore: labs record their runs and trials in it (default: none).
    sync_timeout  seconds a synchronous operation may run before it is stopped with a 408 that points at the
                  job operations (default 120, env ROTHERY_SYNC_TIMEOUT; 0 = no limit).
    workers, mode, persist   JobManager settings for submit_run (mode "process" or "thread")."""

    def __init__(self, factories: dict | None = None, default: str | None = None, store=None,
                 sync_timeout: float | None = None, workers=None, mode: str = "process", persist: str = "metrics"):
        from ..store import NullStore
        self.store = store if store is not None else NullStore()
        self.sync_timeout = (float(os.environ.get("ROTHERY_SYNC_TIMEOUT", 120)) if sync_timeout is None
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

    # ---- run kinds ---------------------------------------------------------------------------------
    # Each kind has three steps shared by the synchronous operation and the job path: `_kw_<kind>` turns
    # request arguments into the Lab method's keyword arguments (validating them), `_exec` runs it, and
    # `result_json` turns the result into the JSON both return.
    KINDS = ("backtest", "timing", "sweep", "significance", "extended", "exit_evaluate", "exit_calibrate",
             "graham", "picks")

    def _kw_picks(self, L, screen, asof=None, columns=None):
        kw = {"screen": screen, "asof": self._date("asof", asof)}
        if columns:
            kw["columns"] = tuple(columns)
        return _clean(kw)

    def _kw_backtest(self, L, screens=None, start=None, end=None, config=None, exit=None,
                     with_and_without=False, benchmark=None, compare=None):
        cfg = self._config(config)
        start, end = self._date("start", start), self._date("end", end)
        if exit is not None and not isinstance(exit, dict):
            raise ServerError(400, "`exit` must be an object (see list_signals)")
        pol = self._policy(L, exit) if exit else None
        bm, cmp = self._benchmarks(benchmark, compare)
        return _clean(dict(screens=screens, start=start, end=end, benchmark=bm, exit=pol,
                           with_and_without=bool(with_and_without) or None, compare=cmp or None, **cfg))

    def _kw_timing(self, L, screen, mode="phase", stride=1, start=None, end=None, config=None):
        cfg = self._config(config)
        return _clean(dict(screen=screen, mode=mode, stride=stride, start=self._date("start", start),
                           end=self._date("end", end), **cfg))

    def _kw_graham(self, L, preset="defensive", asof=None, overrides=None):
        if overrides is not None and not isinstance(overrides, dict):
            raise ServerError(400, "`overrides` must be an object of GrahamTests fields")
        from ..screens import GrahamTests
        known = [f.name for f in dataclasses.fields(GrahamTests)]
        bad = sorted(set(overrides or {}) - set(known))
        if bad:
            raise ServerError(400, f"unknown graham override {bad[0]!r}; use {known}")
        return _clean(dict(tests=preset, asof=self._date("asof", asof), **(overrides or {})))

    def _kw_sweep(self, L, screen, grid, objective="Excess/yr", start=None, end=None, splits=None):
        return _clean(dict(screen=screen, grid=grid, objective=objective, start=self._date("start", start),
                           end=self._date("end", end), splits=splits))

    def _kw_significance(self, L, screens=None, start=None, end=None, config=None, n=2000, seed=0):
        return _clean(dict(screens=screens, start=self._date("start", start), end=self._date("end", end),
                           n=n, seed=seed, **self._config(config)))

    def _kw_extended(self, L, screens=None, start=None, end=None, config=None, macro=None,
                     spread_over_prime=0.0, leverage=1.0):
        m = self._macro(macro, L)
        if m is None:
            raise ServerError(400, "extended needs `macro`: records [{date, series, value}], "
                                   "a {series: {date: value}} mapping, or \"statsmodels\"")
        return _clean(dict(screens=screens, start=self._date("start", start), end=self._date("end", end),
                           macro=m, spread_over_prime=spread_over_prime, leverage=leverage,
                           **self._config(config)))

    def _kw_exit_evaluate(self, L, signals, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                          screen_params=None, target=None, horizons=(1, 3, 12), macro=None,
                          exposure_when_flagged=0.0, cash_rate=0.0, policy=None):
        es = self._exit_screen(L, signals, weights, threshold, min_votes, hysteresis, screen_params)
        pol = self._policy_from(es, policy, exposure_when_flagged) if policy else None
        return _clean(dict(exit_screen=es, target=target, horizons=tuple(horizons), macro=self._macro(macro, L),
                           policy=pol, exposure_when_flagged=exposure_when_flagged, cash_rate=cash_rate))

    def _kw_exit_calibrate(self, L, signals, grid, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                           screen_params=None, objective="Calmar", splits=None, constraints=None, target=None,
                           macro=None, policy=None):
        if getattr(L, "calibrate_exit", None) is None:
            raise ServerError(501, "Lab.calibrate_exit is not available in this build of rothery_screener")
        es = self._exit_screen(L, signals, weights, threshold, min_votes, hysteresis, screen_params)
        kw = {"policy": self._policy_from(es, policy, 0.0)} if policy else {}
        return _clean(dict(exit_screen=es, grid=grid, objective=objective, splits=splits, constraints=constraints,
                           target=target, macro=self._macro(macro, L), **kw))

    def _kw(self, kind, L, args) -> dict:
        if kind not in self.KINDS:
            raise ServerError(400, f"unknown run kind {kind!r}; use {list(self.KINDS)}")
        if not isinstance(args, dict):
            raise ServerError(400, "`args` must be a JSON object")
        return getattr(self, f"_kw_{kind}")(L, **args)

    def _exec(self, L, kind, kw):
        """Run a kind on `L`. With `sync_timeout` the call runs in a helper thread and is cancelled (through
        progress.py's hook) when it overruns, with a 408 that points at the job endpoints."""
        from ..jobs import KINDS
        fn = lambda: KINDS[kind](L, **kw)
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
        threading.Thread(target=target, daemon=True, name=f"rothery-sync-{kind}").start()
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
    def picks(self, screen, asof=None, lab=None, columns=None) -> dict:
        L = self.get(lab)
        kw = self._kw_picks(L, screen, asof, columns)
        return result_json("picks", L, kw, self._exec(L, "picks", kw))

    @_guarded
    def backtest(self, screens=None, start=None, end=None, config=None, lab=None,
                 max_points=DEFAULT_MAX_POINTS, exit=None, with_and_without=False, benchmark=None,
                 compare=None) -> dict:
        """`exit`: {"signals": [...], threshold, weights, min_votes, hysteresis, screen_params, + policy fields
        (exposure_when_flagged, park, park_mer, park_cost_bps, switch_cost_bps, reentry, reentry_delay_days,
        reentry_steps, rescreen_on_reentry, macro)} applied to every screen; with_and_without runs both.
        The primary benchmark is always a passive index: `benchmark` (an index name) replaces the lab's,
        while "topN" or `compare` entries (index names / "topN") only add "... (compare)" rows."""
        L = self.get(lab)
        kw = self._kw_backtest(L, screens, start, end, config, exit, with_and_without, benchmark, compare)
        return result_json("backtest", L, kw, self._exec(L, "backtest", kw), max_points)

    @_guarded
    def timing(self, screen, mode="phase", stride=1, start=None, end=None, config=None, lab=None,
               max_points=DEFAULT_MAX_POINTS) -> dict:
        L = self.get(lab)
        kw = self._kw_timing(L, screen, mode, stride, start, end, config)
        return result_json("timing", L, kw, self._exec(L, "timing", kw), max_points)

    @_guarded
    def graham(self, preset="defensive", asof=None, overrides=None, lab=None) -> dict:
        L = self.get(lab)
        kw = self._kw_graham(L, preset, asof, overrides)
        return result_json("graham", L, kw, self._exec(L, "graham", kw))

    @_guarded
    def sweep(self, screen, grid, objective="Excess/yr", start=None, end=None, splits=None, lab=None) -> dict:
        L = self.get(lab)
        kw = self._kw_sweep(L, screen, grid, objective, start, end, splits)
        return result_json("sweep", L, kw, self._exec(L, "sweep", kw))

    @_guarded
    def significance(self, screens=None, start=None, end=None, config=None, n=2000, seed=0, lab=None) -> dict:
        L = self.get(lab)
        kw = self._kw_significance(L, screens, start, end, config, n, seed)
        return result_json("significance", L, kw, self._exec(L, "significance", kw))

    @_guarded
    def extended(self, screens=None, start=None, end=None, config=None, macro=None,
                 spread_over_prime=0.0, leverage=1.0, lab=None) -> dict:
        L = self.get(lab)
        kw = self._kw_extended(L, screens, start, end, config, macro, spread_over_prime, leverage)
        return result_json("extended", L, kw, self._exec(L, "extended", kw))

    @_guarded
    def exit_evaluate(self, signals, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                      screen_params=None, target=None, horizons=(1, 3, 12), macro=None,
                      exposure_when_flagged=0.0, cash_rate=0.0, lab=None, max_points=DEFAULT_MAX_POINTS,
                      policy=None) -> dict:
        """`policy`: ExitPolicy fields (park, reentry, reentry_delay_days, reentry_steps, park_mer, ...);
        when given, the overlay trades that policy."""
        L = self.get(lab)
        kw = self._kw_exit_evaluate(L, signals, weights, threshold, min_votes, hysteresis, screen_params,
                                    target, horizons, macro, exposure_when_flagged, cash_rate, policy)
        return result_json("exit_evaluate", L, kw, self._exec(L, "exit_evaluate", kw), max_points)

    @_guarded
    def exit_calibrate(self, signals, grid, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                       screen_params=None, objective="Calmar", splits=None, constraints=None, target=None,
                       macro=None, lab=None, policy=None) -> dict:
        """`policy`: fixed ExitPolicy fields for every grid point; the grid may sweep reentry,
        reentry_delay_days, reentry_steps, park and exposure_when_flagged."""
        L = self.get(lab)
        kw = self._kw_exit_calibrate(L, signals, grid, weights, threshold, min_votes, hysteresis, screen_params,
                                     objective, splits, constraints, target, macro, policy)
        return result_json("exit_calibrate", L, kw, self._exec(L, "exit_calibrate", kw))

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
        return out if out is not None else jsonify(job.result())

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

    @staticmethod
    def _signals():
        """exit_signals.BUILTINS, with the optional extra inputs registered when that module exists."""
        from .. import exit_signals as es
        try:
            from .. import exit_inputs  # noqa: F401  (registers itself into BUILTINS)
        except ImportError:
            pass
        return es

    @_guarded
    def list_signals(self) -> dict:
        es = self._signals()
        out = []
        for cls in es.BUILTINS.values():
            params = {f.name: scalar(f.default) for f in dataclasses.fields(cls)}
            doc = (cls.__doc__ or "").strip().splitlines()
            out.append({"type": cls.__name__, "params": params, "doc": doc[0] if doc else ""})
        return {"signals": out}

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

    # fields a JSON `config` may override (Config fields that are plain numbers / strings / per-security maps)
    CONFIG_INT = ("rebalance_months", "min_holdings")
    CONFIG_NUM = ("commission", "capital", "delist_return", "cash_rate")
    CONFIG_NUM_OR_MAP = ("cost_bps", "mer")
    CONFIG_OPT_NUM = ("max_weight", "sector_cap")
    CONFIG_FIELDS = CONFIG_INT + CONFIG_NUM + CONFIG_NUM_OR_MAP + CONFIG_OPT_NUM + ("weighting",)

    @classmethod
    def _config(cls, config) -> dict:
        """Validated Config overrides from a request: unknown keys and wrongly typed values are a 400 that
        names the field. `exit` is not a config field here: pass the top-level `exit` object."""
        if config is None:
            return {}
        if not isinstance(config, dict):
            raise ServerError(400, "`config` must be an object of Config overrides")
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        for k, v in config.items():
            if k == "exit":
                raise ServerError(400, "config.exit is not allowed; pass the top-level `exit` object")
            if k not in cls.CONFIG_FIELDS:
                raise ServerError(400, f"unknown config field {k!r}; use {list(cls.CONFIG_FIELDS)}")
            if k in cls.CONFIG_INT and not (isinstance(v, int) and not isinstance(v, bool)):
                raise ServerError(400, f"config.{k} must be an integer, got {v!r}")
            if k in cls.CONFIG_NUM and not num(v):
                raise ServerError(400, f"config.{k} must be a number, got {v!r}")
            if k in cls.CONFIG_NUM_OR_MAP and not (num(v) or isinstance(v, dict)):
                raise ServerError(400, f"config.{k} must be a number or a {{ticker: value}} object, got {v!r}")
            if k in cls.CONFIG_OPT_NUM and v is not None and not num(v):
                raise ServerError(400, f"config.{k} must be a number or null, got {v!r}")
            if k == "weighting" and v not in ("equal", "cap", "inv_vol"):
                raise ServerError(400, f"config.weighting must be 'equal', 'cap' or 'inv_vol', got {v!r}")
        return dict(config)

    @staticmethod
    def _date(name, v):
        """`v` unchanged if it parses as a date (or is None), else a 400 naming the field."""
        if v is None:
            return None
        try:
            if pd.isna(pd.Timestamp(v)):
                raise ValueError(v)
        except (ValueError, TypeError, OverflowError):
            raise ServerError(400, f"`{name}` is not a valid date: {v!r}") from None
        return v

    @staticmethod
    def _benchmarks(benchmark, compare):
        """(IndexBenchmark or None, [extra benchmarks]) from request strings: "topN" is never primary."""
        from ..benchmarks import IndexBenchmark, parse_benchmark as mk
        for name, v in (("benchmark", benchmark), ("compare", compare)):
            if v is not None and not isinstance(v, (str, list, tuple)):
                raise ServerError(400, f"`{name}` must be a string" + (" or a list of strings" if name == "compare" else ""))
        extra = [compare] if isinstance(compare, str) else list(compare or [])
        if isinstance(benchmark, str):
            b = mk(benchmark)
            if isinstance(b, IndexBenchmark):
                return b, [mk(x) for x in extra]
            extra = [benchmark, *extra]
        return None, [mk(x) for x in extra]

    def _macro(self, macro, L):
        if macro is None:
            return None
        from ..macro import MacroSeries
        try:
            if macro == "statsmodels":
                m = MacroSeries.from_statsmodels_macrodata(L.market.index)
                m._ref, m._align = "statsmodels", True         # runspec: by reference, aligned to the market
                return m
            if isinstance(macro, list):
                df = pd.DataFrame(macro).pivot_table(index="date", columns="series", values="value", aggfunc="last")
                df.index = pd.to_datetime(df.index)
                return MacroSeries.from_frame(df, L.market.index)
            if isinstance(macro, dict):
                df = pd.DataFrame({k: pd.Series(v) for k, v in macro.items()})
                df.index = pd.to_datetime(df.index)
                return MacroSeries.from_frame(df, L.market.index)
        except (ValueError, KeyError, TypeError) as e:
            raise ServerError(400, f"bad macro payload: {e}") from e
        raise ServerError(400, "macro must be a record list, a {series: {date: value}} mapping, or \"statsmodels\"")

    POLICY_FIELDS = ("exposure_when_flagged", "park", "park_mer", "park_cost_bps", "switch_cost_bps", "reentry",
                     "reentry_delay_days", "reentry_steps", "rescreen_on_reentry")

    def _policy_from(self, es, spec, exposure_when_flagged):
        """ExitPolicy from an ExitScreen and a dict of policy fields."""
        from ..exit_policy import ExitPolicy
        bad = set(spec) - set(self.POLICY_FIELDS)
        if bad:
            raise ServerError(400, f"unknown policy fields {sorted(bad)}; use {list(self.POLICY_FIELDS)}")
        kw = {"exposure_when_flagged": exposure_when_flagged, **spec}
        return self._call(ExitPolicy, es, **kw)

    def _policy(self, L, spec):
        """ExitPolicy from a backtest `exit` object: signals + screen fields + policy fields."""
        spec = dict(spec)
        sig = spec.pop("signals", None)
        macro = self._macro(spec.pop("macro", None), L)
        screen_keys = ("weights", "threshold", "min_votes", "hysteresis", "screen_params")
        sk = {k: spec.pop(k) for k in screen_keys if k in spec}
        es = self._exit_screen(L, sig, sk.get("weights"), sk.get("threshold", 0.7), sk.get("min_votes"),
                               sk.get("hysteresis", 0.1), sk.get("screen_params"))
        pol = self._policy_from(es, spec, spec.pop("exposure_when_flagged", 0.0))
        pol.macro = macro
        return pol

    def _exit_screen(self, L, signals, weights, threshold, min_votes, hysteresis, screen_params):
        es = self._signals()
        known = dict(es.BUILTINS)
        sigs = []
        for s in signals or []:
            s = {"type": s} if isinstance(s, str) else dict(s)
            t = s.pop("type", None)
            if t not in known:
                raise ServerError(400, f"unknown signal {t!r}; available: {sorted(known)}")
            cls = known[t]
            fields = {f.name for f in dataclasses.fields(cls)}
            if "index" in fields and "index" not in s and "SP500" not in L.market.indices.columns \
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


_DEFAULT: LabRegistry | None = None


def default_registry() -> LabRegistry:
    """The server's registry: bundled labs, runs persisted to `default_store()`."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LabRegistry(store=default_store())
    return _DEFAULT
