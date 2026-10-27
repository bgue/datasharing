"""Named Labs (created lazily, cached) and the JSON-in / JSON-out operations both servers expose.

Every operation takes plain JSON-able arguments and returns plain JSON: tables as
{"columns": [...], "rows": [[...], ...]}, curves as {name: [[iso_date, value], ...]} downsampled to at
most `max_points`, NaN/inf as null. REST and MCP are thin wrappers, so their shapes are identical.
"""
from __future__ import annotations

import dataclasses
import math
import threading
from datetime import date, datetime

import numpy as np
import pandas as pd

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


class LabRegistry:
    """Named labs, built on first use. `factories` maps name -> zero-arg callable (or a ready Lab)."""

    def __init__(self, factories: dict | None = None, default: str | None = None):
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
                self._labs[name] = f() if callable(f) else f
            return self._labs[name]

    # ---- operations (all return JSON-able dicts)
    def list_labs(self) -> dict:
        return {"default": self.default,
                "labs": [{"name": n, "loaded": n in self._labs} for n in self.factories]}

    def list_screens(self, lab=None) -> dict:
        L = self.get(lab)
        rows = [{"name": n, "n": s.n, "note": s.note, "params": jsonify(dict(s.params))}
                for n, s in L.screens.items()]
        return {"lab": lab or self.default, "screens": rows}

    def define_screen(self, spec, lab=None) -> dict:
        L = self.get(lab)
        with self._lock:
            sc = self._call(L.define, spec)
        return {"lab": lab or self.default, "name": sc.name, "n": sc.n, "note": sc.note,
                "screens": list(L.screens)}

    def picks(self, screen, asof=None, lab=None, columns=None) -> dict:
        L = self.get(lab)
        kw = {"columns": tuple(columns)} if columns else {}
        df = self._call(L.picks, screen, asof, **kw)
        return {"screen": screen, "asof": scalar(L._asof(asof)), **table(df)}

    def backtest(self, screens=None, start=None, end=None, config=None, lab=None,
                 max_points=DEFAULT_MAX_POINTS) -> dict:
        L = self.get(lab)
        rep = self._call(L.backtest, screens, start, end, **(config or {}))
        return self._report_json(rep, max_points)

    def timing(self, screen, mode="phase", stride=1, start=None, end=None, config=None, lab=None,
               max_points=DEFAULT_MAX_POINTS) -> dict:
        L = self.get(lab)
        tr = self._call(L.timing, screen, mode, stride, start, end, **(config or {}))
        out = {"screen": tr.screen, "table": table(tr.table), "distribution": table(tr.distribution())}
        if tr.tranche is not None:
            out["tranche"] = curve(tr.tranche, max_points)
        return out

    def graham(self, preset="defensive", asof=None, overrides=None, lab=None) -> dict:
        L = self.get(lab)
        rep, funnel = self._call(L.graham, preset, asof, **(overrides or {}))
        return {"preset": preset, "table": table(rep), "funnel": series_table(funnel, "step", "count")}

    def sweep(self, screen, grid, objective="Excess/yr", start=None, end=None, splits=None, lab=None) -> dict:
        L = self.get(lab)
        df = self._call(L.sweep, screen, grid, objective, start, end, splits)
        out = {"screen": screen, "objective": objective, "table": table(df, index=False)}
        summ = df.attrs.get("summary")
        if summ is not None:
            out["summary"] = jsonify(summ)
        if "walk_forward" in df.attrs:
            out["walk_forward"] = jsonify(df.attrs["walk_forward"])
        return out

    def significance(self, screens=None, start=None, end=None, config=None, n=2000, seed=0, lab=None) -> dict:
        L = self.get(lab)
        rep = self._call(L.backtest, screens, start, end, **(config or {}))
        return {"table": table(self._call(L.significance, rep, n, seed))}

    def extended(self, screens=None, start=None, end=None, config=None, macro=None,
                 spread_over_prime=0.0, leverage=1.0, lab=None) -> dict:
        L = self.get(lab)
        rep = self._call(L.backtest, screens, start, end, **(config or {}))
        m = self._macro(macro, L)
        if m is None:
            raise ServerError(400, "extended needs `macro`: records [{date, series, value}], "
                                   "a {series: {date: value}} mapping, or \"statsmodels\"")
        return {"table": table(self._call(L.extended, rep, m, spread_over_prime, leverage))}

    def exit_evaluate(self, signals, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                      screen_params=None, target=None, horizons=(1, 3, 12), macro=None,
                      exposure_when_flagged=0.0, cash_rate=0.0, lab=None, max_points=DEFAULT_MAX_POINTS) -> dict:
        L = self.get(lab)
        es = self._exit_screen(L, signals, weights, threshold, min_votes, hysteresis, screen_params)
        ev = self._call(L.exit_eval, es, target, tuple(horizons), self._macro(macro, L),
                        exposure_when_flagged=exposure_when_flagged, cash_rate=cash_rate)
        ov = ev.overlay
        return {"frac_flagged": scalar(ev.frac_flagged), "forward": table(ev.forward),
                "drawdowns": table(ev.drawdowns, index=False), "overlay_stats": table(ov.stats),
                "switches": ov.switches, "turnover": scalar(ov.turnover),
                "curves": {"derisked": curve(ov.curve, max_points), "always_in": curve(ov.baseline, max_points)},
                "score": curve(ev.score, max_points),
                "state": [[d, bool(v)] for d, v in curve(ev.state.astype(float), max_points)]}

    def exit_calibrate(self, signals, grid, weights=None, threshold=0.7, min_votes=None, hysteresis=0.1,
                       screen_params=None, objective="Calmar", splits=None, constraints=None, target=None,
                       macro=None, lab=None) -> dict:
        L = self.get(lab)
        fn = getattr(L, "calibrate_exit", None)
        if fn is None:
            raise ServerError(501, "Lab.calibrate_exit is not available in this build of rothery_screener")
        es = self._exit_screen(L, signals, weights, threshold, min_votes, hysteresis, screen_params)
        res = self._call(fn, es, grid=grid, objective=objective, splits=splits, constraints=constraints,
                         target=target, macro=self._macro(macro, L))
        return jsonify(res)

    def describe_columns(self, lab=None) -> dict:
        L = self.get(lab)
        present = set(L.snapshot().columns)
        return {"columns": [{"name": k, "description": v, "in_lab": k in present or k in ("ticker", "date")}
                            for k, v in COLUMNS.items()],
                "note": "filters and derive expressions are pandas expressions over these snapshot columns"}

    def list_signals(self) -> dict:
        from .. import exit_signals as es
        out = []
        for cls in es.BUILTINS:
            params = {f.name: scalar(f.default) for f in dataclasses.fields(cls)}
            doc = (cls.__doc__ or "").strip().splitlines()
            out.append({"type": cls.__name__, "params": params, "doc": doc[0] if doc else ""})
        return {"signals": out}

    # ---- helpers
    @staticmethod
    def _call(fn, *a, **kw):
        """Run an SDK call, mapping bad input to 400 / unknown names to 404."""
        try:
            return fn(*a, **kw)
        except ServerError:
            raise
        except KeyError as e:
            raise ServerError(404, f"unknown name: {e}") from e
        except (ValueError, TypeError, SyntaxError, AttributeError, IndexError) as e:
            raise ServerError(400, f"{type(e).__name__}: {e}") from e
        except Exception as e:
            if type(e).__name__ in ("UndefinedVariableError", "JSONDecodeError"):
                raise ServerError(400, f"{type(e).__name__}: {e}") from e
            raise

    def _report_json(self, rep, max_points) -> dict:
        c = rep.config
        cfg = {f.name: scalar(getattr(c, f.name)) for f in dataclasses.fields(c)
               if isinstance(getattr(c, f.name), (int, float, str, type(None)))}
        return {"benchmark": rep.benchmark_label, "config": cfg, "table": table(rep.table),
                "curves": curves(rep.curves, max_points)}

    def _macro(self, macro, L):
        if macro is None:
            return None
        from ..macro import MacroSeries
        try:
            if macro == "statsmodels":
                return MacroSeries.from_statsmodels_macrodata(L.market.index)
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

    def _exit_screen(self, L, signals, weights, threshold, min_votes, hysteresis, screen_params):
        from .. import exit_signals as es
        known = {c.__name__: c for c in es.BUILTINS}
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
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LabRegistry()
    return _DEFAULT
