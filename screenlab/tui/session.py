"""Session: the state behind the terminal UI, with no Textual import (DESIGN.md L2, protocol in PROTOCOL.md).

    from screenlab.server.registry import LabRegistry
    s = Session(LabRegistry(mode="thread"))
    s.command("add_scenario", {"screen": "dogs", "run": True, "wait": True})
    s.command("set_params", {"scenario": "dogs", "params": {"cost_bps": 25}, "run": True, "wait": True})
    print(s.view())                      # plain text of the Compare tab: passive index, dogs, dogs v2

Scenarios are immutable versions: `set_params` creates `name v2` and keeps `name`. Every computation is an
operation run through `LabRegistry.submit_run` (JobManager, thread mode by default), so the TUI adds no
analysis. All commands take and return plain JSON; `Session` is thread safe (one re-entrant lock).
"""
from __future__ import annotations

import colorsys
import csv
import io
import json
import os
import threading
import time
from types import SimpleNamespace
from dataclasses import dataclass, field

from .. import metrics as _metrics
from ..errors import ServerError
from ..jsonout import jsonify

PROTOCOL = 1
TABS = ("compare", "curves", "timing", "picks", "exit", "montecarlo", "runs", "log", "data")
# Okabe-Ito order, as the research charts use it (research/montecarlo_sp500.py COL); more hues after the eight
PALETTE = ["#0072B2", "#56B4E9", "#D55E00", "#E69F00", "#009E73", "#CC79A7", "#F0E442", "#7B3294"]
PASSIVE_COLOR = "#6B6B6B"
PARAM_KEYS = ("screen", "lab", "n", "rebalance_months", "cost_bps", "commission", "mer", "cash_rate", "exit",
              "start", "end")
CONFIG_KEYS = ("rebalance_months", "cost_bps", "commission", "mer", "cash_rate")
COMPARE_COLS = ("CAGR", "Vol", "Sharpe", "MaxDD", "Worst12m", "Excess/yr", "CI5", "CI95", "P(>0)", "BeatBenchYrs",
                "AvgN", "Turnover", "Costs/yr", "TimeOut", "Switches/yr")
EXIT_ARGS = ("signals", "weights", "threshold", "min_votes", "hysteresis", "screen_params", "macro",
             "exposure_when_flagged")
POLICY_ARGS = ("park", "park_mer", "park_cost_bps", "switch_cost_bps", "reentry", "reentry_delay_days",
               "reentry_steps", "rescreen_on_reentry")
FINAL = ("done", "failed", "cancelled")
COMMANDS = ("add_scenario", "clone_scenario", "set_params", "remove_scenario", "run", "compare", "timing",
            "montecarlo", "exit_eval", "picks", "show", "select", "save_spec", "export", "history", "jobs",
            "cancel", "set_theme", "snapshot", "view", "curves", "screens", "datasets", "fetch_data")
# data downloader (operation `fetch_data`, implemented in ops/fetch_data.py); hosts are shown in the Data tab form
FETCH_SOURCES = ("sec", "boc", "fred", "stooq", "statcan")
FETCH_HOSTS = {"sec": ["www.sec.gov", "data.sec.gov"], "boc": ["www.bankofcanada.ca"], "fred": ["fred.stlouisfed.org"],
               "stooq": ["stooq.com"], "statcan": ["www150.statcan.gc.ca"]}


class SessionError(Exception):
    """A command failed; `status` is the HTTP status the control endpoint answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Scenario:
    id: str
    name: str
    version: int
    params: dict
    color: str
    job: dict | None = None          # latest backtest job info
    result: dict | None = None       # {"metrics", "curve", "bench_metrics", "bench_curve", "benchmark", "table"}
    ci: dict | None = None           # significance row (CI5, CI95, P(>0), t)
    ci_job: dict | None = None
    error: str | None = None
    diff_line: str | None = None
    diff: list = field(default_factory=list)
    previous: str | None = None

    @property
    def label(self) -> str:
        return self.name if self.version == 1 else f"{self.name} v{self.version}"

    @property
    def status(self) -> str:
        return self.job["status"] if self.job else "new"

    def to_dict(self, full: bool = False) -> dict:
        d = {"id": self.id, "name": self.name, "version": self.version, "label": self.label, "color": self.color,
             "params": self.params, "status": self.status, "job": self.job,
             "metrics": {**(self.result["metrics"] if self.result else {}), **(self.ci or {})},
             "diff_from_previous": self.diff_line, "error": self.error}
        return d


def _color(i: int) -> str:
    if i < len(PALETTE):
        return PALETTE[i]
    h = ((i - len(PALETTE)) * 0.381966) % 1.0                   # golden-angle hues, never equal to a palette entry
    r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.6)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def _num(x):
    return None if x is None else x


class Session:
    """Scenarios, versions, results, the comparison set, active tab and Monte Carlo settings.

    registry   a LabRegistry (build it with mode="thread" for in-process jobs); `registry.store` is the store.
    lab        dataset name (default `registry.default`)."""

    def __init__(self, registry, lab: str | None = None, store=None, jobs=None, own: bool = False):
        self.registry = registry
        self.own = own                                  # close() stops the registry's JobManager only if True
        self.lab = lab or registry.default
        if store is not None:
            registry.store = store
        self._jobs_override = jobs
        self._lock = threading.RLock()
        self.scenarios: list[Scenario] = []
        self.seq = 0
        self.selected: str | None = None
        self.active_tab = "compare"
        self.theme = "textual-dark"
        self.mc = {"n_paths": 500, "horizon_years": [5, 10], "generator": None, "seed": 0}
        self.analyses: dict = {"timing": {}, "montecarlo": None, "exit": {}, "picks": {}}
        self._tracked: dict = {}                       # job_id -> record
        self._next_id = 1
        self._next_color = 0
        self._log: list[str] = []
        self._listeners: list = []
        self._seq_cond = threading.Condition()
        self.theme_vars: dict | None = None            # Textual theme colours, pushed by an app (for /theme)
        self.data_fetch: dict | None = None            # result (or error) of the last fetch_data job
        self._cards: dict = {}
        self.control_port: int | None = None
        self._card = None
        self._plabel = None
        self.log(f"session on lab {self.lab}")

    # ---------------------------------------------------------------- events / log
    def subscribe(self, fn) -> None:
        """`fn(session, event_name)` is called after every state change (from the thread that changed it)."""
        self._listeners.append(fn)

    def _emit(self, event: str) -> None:
        self.seq += 1
        with self._seq_cond:
            self._seq_cond.notify_all()
        for fn in list(self._listeners):
            try:
                fn(self, event)
            except Exception:                           # a UI hook must not break a command
                pass

    def wait_seq(self, after: int, timeout: float = 0.0) -> int:
        """Block up to `timeout` seconds until `seq` differs from `after`; returns the current seq."""
        with self._seq_cond:
            if self.seq == after and timeout > 0:
                self._seq_cond.wait_for(lambda: self.seq != after, timeout)
            return self.seq

    def log_lines(self, n: int = 200) -> list:
        return list(self._log[-n:])

    def log(self, msg: str) -> None:
        self._log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self._log[:-300]

    # ---------------------------------------------------------------- lab facts
    @property
    def jm(self):
        return self._jobs_override or self.registry.job_manager

    def _lab(self, name=None):
        return self.registry.get(name or self.lab)

    def card(self):
        if self._card is None:
            self._card = self._lab().data_card()
        return self._card

    def passive_label(self) -> str:
        if self._plabel is None:
            self._plabel = self._lab().passive().label
        return self._plabel

    # ---------------------------------------------------------------- scenario lookup
    def _find(self, ref) -> Scenario:
        if ref is None:
            if self.selected is None:
                raise SessionError(400, "no scenario selected; pass `scenario`")
            ref = self.selected
        if isinstance(ref, dict):
            ref = ref.get("id") or ref.get("label")
        ref = str(ref)
        if ref == "passive" or ref == self.passive_label():
            raise SessionError(400, "the passive index is pinned: it cannot be changed or removed")
        for s in self.scenarios:
            if s.id == ref or s.label == ref:
                return s
        fam = [s for s in self.scenarios if s.name == ref]
        if fam:
            return max(fam, key=lambda s: s.version)
        raise SessionError(404, f"unknown scenario {ref!r}; have {[s.label for s in self.scenarios]}")

    def _find_many(self, refs, default_all_results=False, only_unrun=False) -> list:
        if refs in (None, "", []):
            if default_all_results:
                return [s for s in self.scenarios if s.result]
            return [self._find(None)]
        if refs == "all":
            return [s for s in self.scenarios if not only_unrun or s.status in ("new", "failed", "cancelled")]
        if not isinstance(refs, (list, tuple)):
            refs = [refs]
        return [self._find(r) for r in refs]

    # ---------------------------------------------------------------- params
    def _clean_params(self, params: dict) -> dict:
        if not isinstance(params, dict):
            raise SessionError(400, "`params` must be an object")
        bad = [k for k in params if k not in PARAM_KEYS]
        if bad:
            raise SessionError(400, f"unknown parameter {bad[0]!r}; allowed: {list(PARAM_KEYS)}")
        out = {k: v for k, v in params.items() if v is not None}
        for k in ("n", "rebalance_months"):
            if k in out:
                try:
                    out[k] = int(out[k])
                except (TypeError, ValueError):
                    raise SessionError(400, f"{k} must be an integer, got {out[k]!r}") from None
                if out[k] < 1:
                    raise SessionError(400, f"{k} must be positive")
        for k in ("cost_bps", "commission", "mer", "cash_rate"):
            if k in out:
                try:
                    out[k] = float(out[k])
                except (TypeError, ValueError):
                    raise SessionError(400, f"{k} must be a number, got {out[k]!r}") from None
        if "exit" in out and not isinstance(out["exit"], dict):
            raise SessionError(400, "`exit` must be an object (an exit policy request) or null")
        if "screen" in out:
            self._check_screen(out["screen"], out.get("lab"))
        return out

    def _check_screen(self, screen, lab=None) -> None:
        if isinstance(screen, dict):
            from ..spec import ScreenSpec
            try:
                ScreenSpec.from_dict({k: v for k, v in screen.items()}).build()
            except Exception as e:
                raise SessionError(400, f"bad screen spec: {e}") from None
            return
        if not isinstance(screen, str):
            raise SessionError(400, "`screen` must be a screen name or a ScreenSpec object")
        L = self._lab(lab)
        if screen not in L.screens:
            raise SessionError(404, f"unknown screen {screen!r}; available: {sorted(L.screens)}")

    def _screen_spec(self, params: dict) -> dict | None:
        """The ScreenSpec dict of the scenario's screen (with `n` applied), or None for non-spec screens."""
        s = params.get("screen")
        if isinstance(s, dict):
            spec = dict(s)
        else:
            sc = self._lab(params.get("lab")).screens[s]
            if sc.fn.__name__ != "rule_screen":
                return None
            from ..spec import ScreenSpec
            known = {f for f in ScreenSpec.__dataclass_fields__}
            spec = {"name": sc.name, "n": sc.n, "note": sc.note, **{k: v for k, v in sc.params.items() if k in known}}
        if params.get("n") is not None:
            spec["n"] = int(params["n"])
        return spec

    def _screen_ref(self, params: dict):
        """What the operations take as a screen: a registered name, or JSON text of a spec (also given `n`)."""
        s = params.get("screen")
        if isinstance(s, str) and params.get("n") is None:
            return s
        spec = self._screen_spec(params)
        if spec is None:
            raise SessionError(400, f"`n` is only supported for spec screens; {s!r} is a Python-defined screen")
        return json.dumps(spec)

    def _screen_name(self, params: dict) -> str:
        s = params.get("screen")
        return s.get("name", "screen") if isinstance(s, dict) else str(s)

    def _bt_args(self, sc: Scenario, **extra) -> dict:
        p = sc.params
        a = {"screens": [self._screen_ref(p)]}
        for k in ("start", "end"):
            if p.get(k):
                a[k] = p[k]
        cfg = {k: p[k] for k in CONFIG_KEYS if p.get(k) is not None}
        if cfg:
            a["config"] = cfg
        if p.get("exit"):
            a["exit"] = p["exit"]
        a.update(extra)
        return a

    @staticmethod
    def _diff(old: dict, new: dict) -> list:
        return [{"param": k, "from": old.get(k), "to": new.get(k)}
                for k in PARAM_KEYS if old.get(k) != new.get(k)]

    @staticmethod
    def _short(v):
        if isinstance(v, dict):
            return v.get("name") or ("{" + ",".join(sorted(v)[:3]) + "}")
        return v

    # ---------------------------------------------------------------- commands: scenarios
    def command(self, cmd: str, args: dict | None = None) -> dict:
        """Run one command: JSON in, JSON out. Raises SessionError(status, message)."""
        args = dict(args or {})
        if cmd not in COMMANDS:
            raise SessionError(404, f"unknown command {cmd!r}; commands: {list(COMMANDS)}")
        wait = args.pop("wait", None)
        timeout = float(args.pop("timeout", 120) or 120)
        if cmd == "picks" and wait is None:
            wait = True
        with self._lock:
            try:
                res = getattr(self, f"cmd_{cmd}")(**self._kw(cmd, args))
            except SessionError:
                raise
            except ServerError as e:
                raise SessionError(e.status, e.message) from None
            except TypeError as e:
                raise SessionError(400, f"bad arguments for {cmd}: {e}") from None
            except (ValueError, KeyError) as e:
                raise SessionError(404 if isinstance(e, KeyError) else 400, str(e.args[0] if e.args else e)) from None
        if wait:
            res = self.await_jobs(res, timeout)
        return jsonify(res, None)

    @staticmethod
    def _kw(cmd, args):
        return args

    def await_jobs(self, result: dict, timeout: float = 120) -> dict:
        """Block (without holding the lock) until the jobs in `result["jobs"]` are final; returns the result with
        their final status. Used by `wait`; the control endpoint calls it from its own thread."""
        ids = [j["job_id"] for j in result.get("jobs", []) if j.get("job_id")]
        t0, errors = time.time(), {}
        while True:
            with self._lock:
                self.poll()
                infos = {i: self._tracked[i]["info"] for i in ids if i in self._tracked}
            if all(v["status"] in FINAL for v in infos.values()):
                break
            if time.time() - t0 > timeout:
                raise SessionError(408, f"jobs did not finish within {timeout:g}s; they keep running")
            time.sleep(0.05)
        for j in result.get("jobs", []):
            info = infos.get(j.get("job_id"))
            if info:
                j.update({k: info[k] for k in ("status", "progress", "message", "cache_hit")})
                if info["status"] == "failed":
                    errors[j.get("scenario", j["job_id"])] = info.get("error")
        if errors:
            result["errors"] = errors
        with self._lock:
            if "picks_for" in result:
                result.update(self.analyses["picks"].get(result["picks_for"], {}))
            if any(j.get("kind") == "fetch_data" for j in result.get("jobs", [])):
                result["fetch"] = self.data_fetch
        return result

    def _new_scenario(self, name, version, params) -> Scenario:
        sc = Scenario(f"s{self._next_id}", name, version, params, _color(self._next_color))
        self._next_id += 1
        self._next_color += 1
        self.scenarios.append(sc)
        return sc

    def _unique_name(self, name: str) -> str:
        names = {s.name for s in self.scenarios}
        out, k = name, 1
        while out in names or out == self.passive_label():
            k += 1
            out = f"{name}#{k}"
        return out

    def cmd_add_scenario(self, screen=None, name=None, run=False, ci=False, **params) -> dict:
        if screen is None:
            raise SessionError(400, "add_scenario needs `screen` (a screen name or a ScreenSpec object)")
        p = self._clean_params({"screen": screen, **params})
        p.setdefault("lab", self.lab)
        base = name or self._screen_name(p)
        sc = self._new_scenario(self._unique_name(str(base)), 1, p)
        self.selected = sc.id
        self.log(f"added {sc.label} ({self._screen_name(p)})")
        out = {"scenario": sc.to_dict()}
        if run:
            out["jobs"] = self._submit_backtests([sc], ci=bool(ci))
        self._emit("scenarios")
        return out

    def cmd_clone_scenario(self, scenario=None, name=None) -> dict:
        src = self._find(scenario)
        sc = self._new_scenario(self._unique_name(name or src.name + "_copy"), 1, json.loads(json.dumps(src.params)))
        self.selected = sc.id
        self.log(f"cloned {src.label} -> {sc.label}")
        self._emit("scenarios")
        return {"scenario": sc.to_dict()}

    def cmd_set_params(self, scenario=None, params=None, run=False, ci=False) -> dict:
        src = self._find(scenario)
        merged = {**src.params}
        for k, v in (params or {}).items():
            if k not in PARAM_KEYS:
                raise SessionError(400, f"unknown parameter {k!r}; allowed: {list(PARAM_KEYS)}")
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        merged = self._clean_params(merged)
        diff = self._diff(src.params, merged)
        if not diff:
            raise SessionError(400, "no parameter changed")
        ver = max(s.version for s in self.scenarios if s.name == src.name) + 1
        sc = self._new_scenario(src.name, ver, merged)
        sc.diff, sc.previous = diff, src.label
        sc.diff_line = f"{src.label} -> {sc.label}: " + ", ".join(
            f"{d['param']} {self._short(d['from'])} -> {self._short(d['to'])}" for d in diff)
        self.selected = sc.id
        self.log(sc.diff_line)
        out = {"scenario": sc.to_dict(), "previous": src.label, "diff": diff, "diff_line": sc.diff_line}
        if run:
            out["jobs"] = self._submit_backtests([sc], ci=bool(ci))
        self._emit("scenarios")
        return out

    def cmd_remove_scenario(self, scenario=None) -> dict:
        sc = self._find(scenario)
        if sc.job and sc.job["status"] in ("queued", "running"):
            raise SessionError(400, f"{sc.label} is running; cancel it first")
        self.scenarios.remove(sc)
        if self.selected == sc.id:
            self.selected = self.scenarios[-1].id if self.scenarios else None
        for d in (self.analyses["timing"], self.analyses["exit"], self.analyses["picks"]):
            d.pop(sc.label, None)
        self.log(f"removed {sc.label}")
        self._emit("scenarios")
        return {"removed": sc.label, "scenarios": [s.label for s in self.scenarios]}

    def cmd_select(self, scenario=None) -> dict:
        sc = self._find(scenario)
        self.selected = sc.id
        self._emit("selection")
        return {"selected": sc.id, "label": sc.label}

    def cmd_show(self, tab=None) -> dict:
        if tab not in TABS:
            raise SessionError(400, f"tab must be one of {list(TABS)}; got {tab!r}")
        self.active_tab = tab
        self._emit("tab")
        return {"tab": tab}

    def cmd_set_theme(self, theme=None, variables=None) -> dict:
        if not theme or not isinstance(theme, str):
            raise SessionError(400, "set_theme needs `theme`, e.g. 'textual-light'")
        vars_ = None
        if variables is not None:
            if not isinstance(variables, dict):
                raise SessionError(400, "`variables` must be an object of colour strings")
            vars_ = {str(k): v for k, v in list(variables.items())[:40] if isinstance(v, str) and len(v) < 40}
        changed = theme != self.theme or (vars_ is not None and vars_ != self.theme_vars)
        self.theme = theme
        if vars_ is not None:
            self.theme_vars = vars_
        if changed or variables is None:
            self._emit("theme")
        return {"theme": theme}

    def set_theme(self, theme: str, variables: dict | None = None) -> None:
        """App hook (same name on RemoteSession): record the active Textual theme and its colours."""
        self.command("set_theme", {"theme": theme, **({"variables": variables} if variables else {})})

    # ---------------------------------------------------------------- commands: runs
    def _track(self, job: dict, kind: str, scenario_ids, scenario_label=None, extra=None) -> dict:
        rec = {"info": {**job, "kind": kind}, "kind": kind, "scenarios": list(scenario_ids), "handled": False,
               **(extra or {})}
        self._tracked[job["job_id"]] = rec
        return {"scenario": scenario_label, "scenario_id": scenario_ids[0] if scenario_ids else None,
                "job_id": job["job_id"], "kind": kind, "status": job["status"],
                "progress": job.get("progress", 0.0), "cache_hit": bool(job.get("cache_hit"))}

    def _submit(self, kind, args, scs, label=None, lab=None, force=False, extra=None) -> dict:
        label = label or (scs[0].label if scs else kind)
        job = self.registry.submit_run(kind, args, lab or (scs[0].params.get("lab") if scs else None) or self.lab,
                                       label=label, force=force)
        out = self._track(job, kind, [s.id for s in scs], label, extra)
        if not job.get("cache_hit") and job["status"] not in FINAL:
            self.log(f"{kind} {label} started")
        return out

    def _submit_backtests(self, scs, ci=False, force=False) -> list:
        jobs = []
        for sc in scs:
            sc.error = None
            sc.result = None if force or sc.status in ("failed", "cancelled", "new") else sc.result
            j = self._submit("backtest", self._bt_args(sc), [sc], force=force)
            sc.job = self._tracked[j["job_id"]]["info"]
            jobs.append(j)
            if ci:
                a = self._bt_args(sc, n=1000)
                jc = self._submit("significance", a, [sc], force=force)
                sc.ci_job = self._tracked[jc["job_id"]]["info"]
                jobs.append(jc)
        self.poll()
        for j in jobs:
            j["status"] = self._tracked[j["job_id"]]["info"]["status"]
        self._emit("jobs")
        return jobs

    def cmd_run(self, scenario=None, ci=False, force=False) -> dict:
        scs = self._find_many(scenario, only_unrun=True)
        if not scs:
            return {"jobs": []}
        return {"jobs": self._submit_backtests(scs, ci=bool(ci), force=bool(force))}

    def cmd_timing(self, scenario=None, mode="phase", stride=1, force=False) -> dict:
        sc = self._find(scenario)
        a = {"screen": self._screen_ref(sc.params), "mode": mode, "stride": int(stride)}
        for k in ("start", "end"):
            if sc.params.get(k):
                a[k] = sc.params[k]
        cfg = {k: sc.params[k] for k in CONFIG_KEYS if sc.params.get(k) is not None}
        if cfg:
            a["config"] = cfg
        j = self._submit("timing", a, [sc], force=bool(force))
        self._emit("jobs")
        return {"jobs": [j]}

    def cmd_montecarlo(self, scenarios=None, n_paths=None, horizon_years=None, generator=None, seed=None,
                       force=False, scenario=None) -> dict:
        scenarios = scenarios if scenarios is not None else scenario
        if n_paths is not None:
            self.mc["n_paths"] = int(n_paths)
        if horizon_years is not None:
            self.mc["horizon_years"] = [float(h) for h in (horizon_years if isinstance(horizon_years, (list, tuple))
                                                           else [horizon_years])]
        if generator is not None:
            self.mc["generator"] = generator
        if seed is not None:
            self.mc["seed"] = int(seed)
        scs = self._find_many(scenarios)
        a = {"screens": [self._screen_ref(s.params) for s in scs], "n_paths": self.mc["n_paths"],
             "horizon_years": self.mc["horizon_years"], "seed": self.mc["seed"]}
        if self.mc["generator"]:
            a["generator"] = self.mc["generator"]
        cfg = {k: scs[0].params[k] for k in CONFIG_KEYS if scs[0].params.get(k) is not None}
        if cfg:
            a["config"] = cfg
        if scs[0].params.get("exit"):
            a["exit"] = scs[0].params["exit"]
        j = self._submit("montecarlo", a, scs, label="montecarlo " + ", ".join(s.label for s in scs),
                         force=bool(force))
        self._emit("jobs")
        return {"jobs": [j]}

    def cmd_exit_eval(self, scenario=None, force=False, **op_args) -> dict:
        sc = self._find(scenario)
        ex = sc.params.get("exit") or {}
        a = {k: ex[k] for k in EXIT_ARGS if k in ex}
        pol = {k: ex[k] for k in POLICY_ARGS if k in ex}
        if pol:
            a["policy"] = pol
        a.update(op_args)
        if not a.get("signals"):
            raise SessionError(400, f"{sc.label} has no exit policy; set `exit` with `signals` or pass `signals`")
        j = self._submit("exit_evaluate", a, [sc], force=bool(force))
        self._emit("jobs")
        return {"jobs": [j]}

    def cmd_picks(self, scenario=None, asof=None, columns=None, force=False) -> dict:
        sc = self._find(scenario)
        a = {"screen": self._screen_ref(sc.params)}
        if asof:
            a["asof"] = asof
        if columns:
            a["columns"] = columns
        j = self._submit("picks", a, [sc], force=bool(force))
        self._emit("jobs")
        return {"jobs": [j], "picks_for": sc.label}

    def cmd_jobs(self) -> dict:
        self.poll()
        out = []
        for jid, rec in self._tracked.items():
            lab = [s.label for s in self.scenarios if s.id in rec["scenarios"]]
            out.append({**rec["info"], "scenario": lab[0] if lab else None})
        return {"jobs": out}

    def cmd_cancel(self, scenario=None, job_id=None) -> dict:
        ids = []
        if job_id:
            if job_id not in self._tracked:
                raise SessionError(404, f"unknown job {job_id!r}")
            ids = [job_id]
        else:
            sc = self._find(scenario)
            ids = [j for j, r in self._tracked.items() if sc.id in r["scenarios"] and r["info"]["status"] not in FINAL]
        done = []
        for j in ids:
            self.registry.cancel_run(j)
            done.append(j)
        self.poll()
        return {"cancelled": done}

    # ---------------------------------------------------------------- polling jobs
    def poll(self) -> bool:
        """Refresh job states from the JobManager and store finished results. Returns True if anything changed."""
        changed = False
        with self._lock:
            for jid, rec in self._tracked.items():
                if rec["handled"]:
                    continue
                try:
                    info = self.registry.run_status(jid)
                except ServerError:
                    continue
                if (info["status"], round(info["progress"], 2)) != (rec["info"]["status"], round(rec["info"]["progress"], 2)):
                    changed = True
                rec["info"] = {**rec["info"], **{k: info.get(k) for k in
                                                 ("status", "progress", "message", "cache_hit", "error", "run_key")}}
                for s in self.scenarios:
                    if s.id in rec["scenarios"]:
                        if rec["kind"] == "backtest":
                            s.job = rec["info"]
                        elif rec["kind"] == "significance":
                            s.ci_job = rec["info"]
                if info["status"] in FINAL:
                    rec["handled"] = True
                    changed = True
                    self._finish(jid, rec)
            if changed:
                self._emit("jobs")
        return changed

    def _finish(self, jid, rec) -> None:
        st = rec["info"]["status"]
        scs = [s for s in self.scenarios if s.id in rec["scenarios"]]
        label = scs[0].label if scs else rec["kind"]
        if st != "done":
            msg = rec["info"].get("error") or st
            if rec["kind"] == "fetch_data":
                self.data_fetch = {"error": msg, "job_id": jid}
            if rec["kind"] == "backtest":
                for s in scs:
                    s.error = msg
            self.log(f"{rec['kind']} {label} {st}: {msg}" if st == "failed" else f"{rec['kind']} {label} {st}")
            return
        try:
            res = self.registry.run_result(jid)
        except ServerError as e:
            rec["info"]["status"], rec["info"]["error"] = "failed", e.message
            self.log(f"{rec['kind']} {label}: {e.message}")
            return
        kind = rec["kind"]
        if kind == "backtest":
            for s in scs:
                self._store_backtest(s, res)
        elif kind == "significance":
            for s in scs:
                self._store_significance(s, res)
        elif kind == "timing":
            self.analyses["timing"][label] = {k: res.get(k) for k in ("table", "distribution", "tranche", "screen")}
        elif kind == "montecarlo":
            self.analyses["montecarlo"] = {"scenarios": [s.label for s in scs], **res,
                                           "hist": self._mc_hist(jid)}
        elif kind == "exit_evaluate":
            self.analyses["exit"][label] = res
        elif kind == "fetch_data":
            self._store_fetch(jid, res)
        elif kind == "picks":
            self.analyses["picks"][label] = {"scenario": label, "asof": res.get("asof"),
                                             "columns": res["columns"], "rows": res["rows"]}
        self.log(f"{kind} {label} done" + (" (cached)" if rec["info"].get("cache_hit") else ""))

    def _mc_hist(self, jid):
        """Binned CAGR / drawdown histograms for the chart panel (web.mc_hist), from the live result of the job;
        None when the result came from the store (it keeps only the table and the fan)."""
        try:
            from .web import mc_hist
            return mc_hist(getattr(self.registry.job_manager.get(jid).result(), "per_path", None))
        except Exception:                                                 # noqa: BLE001
            return None

    @staticmethod
    def _rows(table: dict) -> dict:
        cols = table["columns"]
        return {r[0]: dict(zip(cols[1:], r[1:])) for r in table["rows"]}

    def _store_backtest(self, sc: Scenario, res: dict) -> None:
        rows = self._rows(res["table"])
        bench = res["benchmark"]
        mine = [k for k in rows if k != bench and not k.endswith("(compare)")]
        if not mine:
            sc.error = "backtest returned no portfolio row"
            return
        label = mine[0]
        clean = lambda d: {k: v for k, v in d.items() if v is not None}
        sc.result = {"metrics": clean(rows[label]), "curve": res["curves"].get(label, []),
                     "benchmark": bench, "bench_metrics": clean(rows[bench]), "bench_curve": res["curves"].get(bench, []),
                     "data_card": res.get("data_card"), "trials_in_family": res.get("trials_in_family")}

    def _store_significance(self, sc: Scenario, res: dict) -> None:
        rows = self._rows(res["table"])
        if not rows:
            return
        r = next(iter(rows.values()))
        sc.ci = {k: r[k] for k in ("CI5", "CI95", "P(>0)", "t") if r.get(k) is not None}

    # ---------------------------------------------------------------- compare
    def cmd_compare(self, scenarios=None, ci=False) -> dict:
        with self._lock:
            return self._compare(scenarios)

    def _compare(self, scenarios=None) -> dict:
        self.poll()
        scs = self._find_many(scenarios, default_all_results=True) if scenarios else \
            [s for s in self.scenarios if s.result]
        first = next((s for s in self.scenarios if s.result), None)
        plabel = first.result["benchmark"] if first else self.passive_label()
        rows = [{"id": "passive", "label": plabel, "color": PASSIVE_COLOR, "passive": True, "version": None,
                 "values": dict(first.result["bench_metrics"]) if first else {}}]
        for s in scs:
            if s.result:
                rows.append({"id": s.id, "label": s.label, "color": s.color, "passive": False, "version": s.version,
                             "values": {**s.result["metrics"], **(s.ci or {})}})
        have = [c for c in COMPARE_COLS if any(r["values"].get(c) is not None for r in rows)]
        return {"benchmark": plabel, "columns": have, "rows": rows,
                "missing": [s.label for s in (scs if scenarios else self.scenarios) if not s.result],
                "diffs": [s.diff_line for s in self.scenarios if s.diff_line]}

    def compare_text(self) -> str:
        c = self.cmd_compare()
        cols = c["columns"]
        w = max([len(r["label"]) for r in c["rows"]] + [8])
        head = f"{'':2}{'Scenario':<{w}}  " + "  ".join(f"{x:>11}" for x in cols)
        lines = [head, "-" * len(head)]
        for r in c["rows"]:
            vals = "  ".join(f"{_metrics.fmt(x, r['values'].get(x)):>11}" for x in cols)
            lines.append(f"{'*' if r['passive'] else ' '} {r['label']:<{w}}  {vals}   [{r['color']}]")
        if c["missing"]:
            lines.append("not run yet: " + ", ".join(c["missing"]))
        lines += c["diffs"]
        return "\n".join(lines)

    def curves_data(self) -> dict:
        with self._lock:
            return self._curves_data()

    def _curves_data(self) -> dict:
        """label -> {"color", "points": [[iso_date, value], ...]} normalised to 1.0 at each curve's start,
        passive index first."""
        def norm(pts):
            v0 = next((v for _, v in pts if v), None)
            return [[d, v / v0] for d, v in pts if v is not None and v0]
        out = {}
        first = next((s for s in self.scenarios if s.result), None)
        if first:
            out[first.result["benchmark"]] = {"color": PASSIVE_COLOR, "points": norm(first.result["bench_curve"])}
        for s in self.scenarios:
            if s.result:
                out[s.label] = {"color": s.color, "points": norm(s.result["curve"])}
        return out

    # ---------------------------------------------------------------- store / files
    def cmd_save_spec(self, scenario=None, path=None) -> dict:
        sc = self._find(scenario)
        spec = self._screen_spec(sc.params)
        if spec is None:
            raise SessionError(400, f"{sc.label}: only spec (rule) screens can be saved as JSON")
        path = path or f"{sc.name.replace('#', '_')}.json"
        with open(path, "w") as f:
            json.dump(spec, f, indent=2)
        self.log(f"saved spec {path}")
        return {"path": os.path.abspath(path), "spec": spec}

    def cmd_export(self, path="screenlab_export", format="both") -> dict:
        if format not in ("markdown", "csv", "both"):
            raise SessionError(400, "format must be markdown, csv or both")
        c = self.cmd_compare()
        files = []
        base = str(path)
        if format in ("csv", "both"):
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["scenario", "passive", "color", *c["columns"]])
            for r in c["rows"]:
                w.writerow([r["label"], r["passive"], r["color"], *[r["values"].get(x) for x in c["columns"]]])
            with open(base + ".csv", "w", newline="") as f:
                f.write(buf.getvalue())
            files.append(base + ".csv")
        if format in ("markdown", "both"):
            card = self.card()
            lines = [f"# screenlab export", "", card.line(), *[f"- {x}" for x in card.warnings()], "",
                     "| scenario | " + " | ".join(c["columns"]) + " |", "|" + "---|" * (len(c["columns"]) + 1)]
            for r in c["rows"]:
                lines.append(f"| {r['label']} | " + " | ".join(_metrics.fmt(x, r["values"].get(x)) for x in c["columns"]) + " |")
            lines += ["", *c["diffs"]]
            with open(base + ".md", "w") as f:
                f.write("\n".join(lines) + "\n")
            files.append(base + ".md")
        self.log("exported " + ", ".join(files))
        return {"files": files}

    def store_path(self):
        p = getattr(self.registry.store, "path", None)
        return str(p) if p else None

    def cmd_history(self, kind=None, screen=None, limit=20, compare=None, rerun=None) -> dict:
        store = self.registry.store
        if compare:
            df = store.compare(list(compare))
            df = df.reset_index()
            return {"columns": [str(c) for c in df.columns],
                    "rows": [[None if v != v else v for v in r] for r in df.astype(object).itertuples(index=False, name=None)]}
        if rerun:
            from ..jobs import execute_spec
            L = self._lab()
            try:
                rep = store.rerun(rerun, lambda spec: execute_spec(spec, L))
            except KeyError as e:
                raise SessionError(404, str(e.args[0])) from None
            return {"run_key": rerun, "match": bool(rep.match), "max_abs_diff": float(rep.max_abs_diff),
                    "code_changed": bool(rep.code_changed), "dataset_changed": rep.dataset_changed}
        df = store.find(kind=kind, screen=screen)
        runs = []
        if len(df):
            df = df.drop_duplicates("run_key", keep="last").sort_values("created_at", ascending=False)
            df = df.head(int(limit or 20))
            keep = [c for c in ("run_key", "kind", "dataset", "status", "label", "created_at") if c in df]
            for r in df[keep].astype(object).itertuples(index=False, name=None):
                d = dict(zip(keep, [None if (isinstance(v, float) and v != v) else v for v in r]))
                try:
                    d["screen"] = ",".join(store.get(d["run_key"]).spec.screen_names())
                except Exception:
                    d["screen"] = None
                runs.append(d)
        return {"store": self.store_path(), "runs": runs}

    # ---------------------------------------------------------------- datasets and the downloader
    @staticmethod
    def downloader_available() -> bool:
        """True when the operation `fetch_data` is registered (ops/fetch_data.py)."""
        try:
            from .. import operations
            operations.builtin_names()                       # make sure the built-in operations are imported
            return "fetch_data" in operations.OPERATIONS
        except Exception:                                    # noqa: BLE001
            return False

    @staticmethod
    def fetch_hosts() -> dict:
        """source -> hostnames the downloader needs on the network allow-list (the downloader's own table when it
        exports one, else the defaults here)."""
        try:
            from ..realdata import fetch as _fetch
            for name in ("SOURCE_HOSTS", "HOSTS_BY_SOURCE", "SOURCES_HOSTS"):
                table = getattr(_fetch, name, None)
                if isinstance(table, dict) and table:
                    return {str(k): list(v) for k, v in table.items()}
        except Exception:                                    # noqa: BLE001
            pass
        return {k: list(v) for k, v in FETCH_HOSTS.items()}

    def lab_names(self) -> list:
        return list(self.registry.factories)

    def screen_names(self, lab=None) -> list:
        return sorted(self._lab(lab).screens)

    def cmd_screens(self, lab=None) -> dict:
        L = self._lab(lab)
        return {"lab": L.name or lab or self.lab, "screens": sorted(L.screens)}

    def cmd_curves(self) -> dict:
        return self.curves_data()

    def active_jobs(self) -> list:
        return [j for j in self.cmd_jobs()["jobs"] if j["status"] not in FINAL]

    def _lab_card(self, name: str) -> dict:
        """The data card facts of one registered lab (built on first use; failures are reported, not raised)."""
        f = self.registry.factories.get(name)
        hit = self._cards.get(name)
        if hit is not None and hit[0] is f:
            return hit[1]
        try:
            card = self._lab(name).data_card()
            info = {"line": card.line(), "warnings": list(card.warnings()), "error": None}
        except Exception as e:                               # noqa: BLE001  (a registered CSV may be unreadable)
            info = {"line": None, "warnings": [], "error": f"{type(e).__name__}: {e}"}
        self._cards[name] = (f, info)
        return info

    def cmd_datasets(self) -> dict:
        """Datasets the session can use (registry labs with data card and warnings) plus the downloader's status."""
        labs = []
        for name in list(self.registry.factories):
            labs.append({"name": name, "default": name == self.registry.default, "session": name == self.lab,
                         "loaded": name in self.registry._labs, **self._lab_card(name)})
        hosts = self.fetch_hosts()
        return jsonify({"labs": labs, **self.data_state(), "sources": list(FETCH_SOURCES), "hosts": hosts,
                        "allow_hosts": sorted({h for v in hosts.values() for h in v}),
                        "defaults": {"dest": "data", "start": "2000-01-01",
                                     "sec_user_agent": os.environ.get("SCREENLAB_SEC_USER_AGENT", ""),
                                     "tickers": ["XIU.TO"]}}, None)

    def data_state(self) -> dict:
        running = [j for j in self.cmd_jobs()["jobs"] if j["kind"] == "fetch_data" and j["status"] not in FINAL]
        return {"downloader": self.downloader_available(), "running": running[0] if running else None,
                "last_fetch": self.data_fetch}

    def cmd_fetch_data(self, sources=None, dest="data", sec_user_agent=None, tickers=None, start=None,
                       periods=None, force=False, **extra) -> dict:
        """Submit the `fetch_data` operation as a job (progress and result like any other run)."""
        if not self.downloader_available():
            raise SessionError(501, "downloader not installed: the operation fetch_data is not registered")
        sources = list(sources) if sources else list(FETCH_SOURCES)
        bad = [x for x in sources if x not in FETCH_SOURCES]
        if bad:
            raise SessionError(400, f"unknown source {bad[0]!r}; sources: {list(FETCH_SOURCES)}")
        ua = (sec_user_agent or "").strip()
        if "sec" in sources and ("@" not in ua or len(ua) < 6):
            raise SessionError(400, "the SEC requires a contact in the User-Agent: pass `sec_user_agent` "
                                    "as 'Your Name you@example.com' (or leave SEC out of `sources`)")
        a: dict = {"sources": sources, "dest": dest or "data"}
        if ua:
            a["sec_user_agent"] = ua
        for k, v in (("tickers", tickers), ("start", start), ("periods", periods)):
            if v not in (None, "", []):
                a[k] = v
        a.update(extra)
        j = self._submit("fetch_data", a, [], label="fetch_data", force=bool(force))
        self._emit("jobs")
        return {"jobs": [j]}

    def _store_fetch(self, jid: str, res: dict) -> None:
        """A finished fetch_data job: keep the manifest and make the labs it produced available."""
        res = res if isinstance(res, dict) else {}
        registered = []
        for lab in res.get("labs") or []:
            name = lab.get("name") if isinstance(lab, dict) else None
            if not name:
                continue
            if name not in self.registry.factories and lab.get("fundamentals") and lab.get("prices"):
                try:
                    self.registry.register_csv(name, lab["fundamentals"], lab["prices"], lab.get("indices"))
                    registered.append(name)
                except Exception as e:                       # noqa: BLE001
                    self.log(f"fetch_data: could not register lab {name}: {e}")
        man = res.get("manifest") or {}
        self.data_fetch = {"job_id": jid, "manifest": man, "labs": res.get("labs") or [], "registered": registered}
        self.log(f"fetch_data: {len(man.get('files') or [])} file(s), {len(man.get('errors') or [])} error(s)"
                 + (f", labs {', '.join(registered)}" if registered else ""))

    # ---------------------------------------------------------------- state / view
    def snapshot(self) -> dict:
        self.poll()
        with self._lock:
            card = self.card()
            return jsonify({
                "protocol": PROTOCOL, "seq": self.seq, "lab": self.lab, "labs": list(self.registry.factories),
                "data_card": card.to_dict(), "data_line": card.line(), "warnings": card.warnings(),
                "store": self.store_path(), "active_tab": self.active_tab, "selected": self.selected,
                "theme": self.theme, "screens": self.screen_names(), "data": self.data_state(),
                "passive": {"id": "passive", "label": self.passive_label(), "color": PASSIVE_COLOR},
                "scenarios": [s.to_dict() for s in self.scenarios],
                "jobs": [j for j in self.cmd_jobs()["jobs"] if j["status"] not in FINAL],
                "mc": dict(self.mc), "analyses": self.analyses, "log": self.log_lines(200)}, None)

    def cmd_snapshot(self) -> dict:
        return self.snapshot()

    def cmd_view(self, tab=None) -> dict:
        if tab is not None and tab not in TABS:
            raise SessionError(400, f"tab must be one of {list(TABS)}; got {tab!r}")
        return {"tab": tab or self.active_tab, "view": self.view(tab)}

    @staticmethod
    def _grid(columns, rows, limit=40) -> str:
        cells = [[("" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v))) for v in r] for r in rows[:limit]]
        w = [max([len(str(c))] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
        out = ["  ".join(f"{str(c):>{w[i]}}" for i, c in enumerate(columns))]
        out += ["  ".join(f"{v:>{w[i]}}" for i, v in enumerate(r)) for r in cells]
        if len(rows) > limit:
            out.append(f"... {len(rows) - limit} more rows")
        return "\n".join(out)

    def view(self, tab: str | None = None) -> str:
        """Plain text of what a tab shows (default: the active tab), for an agent to read the screen."""
        self.poll()
        with self._lock:
            tab = tab or self.active_tab
            card = self.card()
            head = [card.line(), *card.warnings(), f"Scenarios: " + (", ".join(
                f"{'>' if s.id == self.selected else ''}{s.label} [{s.color}] {s.status}" for s in self.scenarios) or "none"),
                    "Jobs: " + ("; ".join(f"{j['kind']} {j['progress']:.0%}" for j in self.cmd_jobs()["jobs"]
                                          if j["status"] not in FINAL) or "idle"), f"== {tab} =="]
            body = getattr(self, f"_view_{tab}")()
            return "\n".join(head + [body])

    def _view_compare(self) -> str:
        return self.compare_text()

    def _view_curves(self) -> str:
        cd = self.curves_data()
        if not cd:
            return "no curves yet: run a scenario"
        lines = ["wealth curves (log scale), start = 1.0"]
        for k, v in cd.items():
            pts = v["points"]
            lines.append(f"{k:<20} {v['color']}  {pts[0][0][:10]} .. {pts[-1][0][:10]}  end {pts[-1][1]:.2f}x")
        return "\n".join(lines)

    def _view_timing(self) -> str:
        t = self.analyses["timing"]
        if not t:
            return "no timing study yet: press t or run the `timing` command"
        out = []
        for k, v in t.items():
            out += [f"{k}: distribution", self._grid(v["distribution"]["columns"], v["distribution"]["rows"])]
        return "\n".join(out)

    def _view_picks(self) -> str:
        p = self.analyses["picks"]
        if not p:
            return "no picks yet: run the `picks` command"
        return "\n".join(f"{k} as of {v['asof']}\n" + self._grid(v["columns"], v["rows"]) for k, v in p.items())

    def _view_exit(self) -> str:
        e = self.analyses["exit"]
        if not e:
            return "no exit evaluation yet: give a scenario an `exit` policy and run `exit_eval`"
        out = []
        for k, v in e.items():
            out += [f"{k}: flagged {v.get('frac_flagged', 0):.1%}, switches {v.get('switches')}",
                    "forward returns", self._grid(v["forward"]["columns"], v["forward"]["rows"]),
                    "overlay", self._grid(v["overlay_stats"]["columns"], v["overlay_stats"]["rows"])]
        return "\n".join(out)

    def _view_montecarlo(self) -> str:
        m = self.analyses["montecarlo"]
        if not m:
            return f"no Monte Carlo yet (settings {self.mc}): run the `montecarlo` command"
        return (f"{', '.join(m['scenarios'])}: {m.get('n_paths')} paths, {m.get('generator')}, "
                f"horizons {m.get('horizon_years')}\n" + self._grid(m["table"]["columns"], m["table"]["rows"], 60))

    def _view_runs(self) -> str:
        h = self.cmd_history(limit=15)
        if not h["runs"]:
            return f"store: {h['store'] or 'none (runs are not kept)'}"
        return f"store: {h['store']}\n" + self._grid(list(h["runs"][0]), [list(r.values()) for r in h["runs"]])

    def _view_data(self) -> str:
        d = self.cmd_datasets()
        lines = ["datasets (name, state, data card):"]
        for L in d["labs"]:
            tags = ", ".join(t for t, on in (("default", L["default"]), ("session", L["session"]),
                                             ("loaded", L["loaded"])) if on)
            lines.append(f"  {L['name']} [{tags or 'not loaded'}] " + (L["line"] or f"unavailable: {L['error']}"))
            lines += [f"      {w}" for w in L["warnings"]]
        if not d["downloader"]:
            lines.append("downloader not installed (operation fetch_data is not registered)")
        else:
            lines.append("downloader: fetch_data (sources " + ", ".join(d["sources"]) + ")")
            lines.append("network allow-list hosts: " + ", ".join(d["allow_hosts"]))
        if d["running"]:
            lines.append(f"fetch running: {d['running'].get('progress') or 0:.0%}")
        lf = d["last_fetch"]
        if lf and lf.get("error"):
            lines.append(f"last fetch failed: {lf['error']}")
        elif lf:
            man = lf.get("manifest") or {}
            lines.append(f"last fetch: {len(man.get('files') or [])} file(s)")
            lines += [f"  {f.get('source')} {f.get('status')} {f.get('bytes')} {f.get('path')}"
                      for f in (man.get("files") or [])[:40]]
            lines += [f"  error: {e}" for e in (man.get("errors") or [])[:20]]
        return "\n".join(lines)

    def _view_log(self) -> str:
        return "\n".join(self._log[-40:]) or "(empty)"

    def close(self) -> None:
        """Stop the pool threads of the registry's JobManager when this session owns the registry."""
        try:
            if self.own and self.registry._jm is not None:
                self.registry._jm.shutdown(wait=False, cancel_pending=True)
        except Exception:
            pass
