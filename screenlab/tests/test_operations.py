"""Operations registry (DESIGN.md J4): the acceptance test is a toy operation defined in this file that becomes
available through the Lab, jobs, the store, REST and MCP without editing any other module. Plus: every
built-in operation's typed args round-trip, run keys did not move with the refactor, deprecated signal names
canonicalize, CapTiered pickles."""
from __future__ import annotations

import json
import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from screenlab import (Breadth, Config, ExitPolicy, ExitScreen, HoldRule, IndexBenchmark, Lab, MacroSeries,
                       ScreenBuilder, SQLiteStore, TrendBreak, VolRegime, cap_tiered, equal_weight_top)
from screenlab import runspec as R
from screenlab.exit_policy import Portfolio
from screenlab.exit_signals import CAL_PER_TRADING_DAY
from screenlab.jobs import KINDS, JobManager
from screenlab.jsonout import table
from screenlab.operations import OPERATIONS, Operation, arg, builtin_names, register_operation
from screenlab.sweep import Grid

FIXTURE = Path(__file__).parent / "fixtures" / "run_keys_pre_j4.json"


# ---------------------------------------------------------------------------------------------------
# the toy operation: defined here, registered here, nothing else edited
# ---------------------------------------------------------------------------------------------------
@register_operation
class EchoStats(Operation):
    """Summary statistics of a backtest curve."""
    name = "echo_stats"

    @dataclass
    class Args:
        screen: str = arg(doc="screen name")
        rebalance_months: int = arg(12, "months between rebalances")

    def run(self, lab, screen, rebalance_months=12):
        c = lab.backtest([screen], rebalance_months=rebalance_months).curves[screen]
        return pd.DataFrame({"value": {"first": c.iloc[0], "last": c.iloc[-1], "max": c.max(), "n": float(len(c))}})

    def to_json(self, result, max_points=500, *, lab=None, kw=None):
        return {"screen": kw["screen"], **table(result)}

    def metrics(self, result):                      # custom rows: (row_label, metric, value)
        return [("curve", k, float(v)) for k, v in result["value"].items()]


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def test_toy_operation_on_the_lab(lab):
    assert "echo_stats" in OPERATIONS and "echo_stats" not in builtin_names()
    a = lab.echo_stats("dogs", rebalance_months=6)
    b = lab.run("echo_stats", screen="dogs", rebalance_months=6)
    pd.testing.assert_frame_equal(a, b)
    assert list(a.index) == ["first", "last", "max", "n"] and a.loc["first", "value"] == pytest.approx(1.0)
    assert hasattr(Lab, "echo_stats") and "rebalance_months" in str(__import__("inspect").signature(Lab.echo_stats))
    with pytest.raises(KeyError):
        lab.run("no_such_operation")


def test_toy_operation_run_spec_family_and_defaults():
    spec = R.RunSpec("echo_stats", "synthetic", {"screen": "dogs", "rebalance_months": 12})
    assert spec.canonical() == R.RunSpec("echo_stats", "synthetic", {"screen": "dogs"}).canonical()   # default dropped
    assert spec.family() == "echo_stats|synthetic|dogs"
    assert "echo_stats" in KINDS and "echo_stats" in set(KINDS)


def test_toy_operation_as_a_job_with_stored_metrics(tmp_path):
    store = SQLiteStore(tmp_path / "t.sqlite")
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3, store=store)
    lab.name = "synthetic"
    job = lab.submit("echo_stats", screen="dogs", rebalance_months=6)           # thread mode, same process
    res = job.result(timeout=120)
    assert job.status == "done" and res.loc["last", "value"] > 0
    found = store.find(kind="echo_stats")
    assert set(found["row_label"]) == {"curve"} and {"first", "last", "max", "n"} <= set(found.columns)
    assert found["last"].iloc[0] == pytest.approx(res.loc["last", "value"])
    again = lab.submit("echo_stats", screen="dogs", rebalance_months=6)
    assert again.cache_hit and again.run_key == job.run_key
    assert store.trial_count("echo_stats|synthetic|dogs") == 1
    # a direct SDK call is recorded under the same key
    lab.echo_stats("dogs", rebalance_months=6)
    assert store.find(kind="echo_stats")["run_key"].nunique() == 1
    lab.jobs.shutdown()


@pytest.fixture(scope="module")
def reg():
    from screenlab.server.registry import LabRegistry
    return LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)},
                       default="synthetic", mode="thread")


def test_toy_operation_over_rest_and_mcp(reg):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from screenlab.server.mcp_server import build_tools
    from screenlab.server.rest import create_app
    app = create_app(reg)
    c = TestClient(app)
    r = c.post("/labs/synthetic/echo_stats", json={"screen": "dogs", "rebalance_months": 6})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["screen"] == "dogs" and j["columns"] == ["index", "value"] and "trials_in_family" in j
    assert c.post("/labs/synthetic/echo_stats", json={}).json() == {"error": "missing required field 'screen'"}
    assert c.post("/labs/synthetic/echo_stats", json={"screen": "dogs", "bogus": 1}).status_code == 400
    # typed request body in OpenAPI
    spec = c.get("/openapi.json").json()
    body = spec["paths"]["/labs/{lab}/echo_stats"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema = spec["components"]["schemas"][body["$ref"].split("/")[-1]]
    assert set(schema["properties"]) == {"screen", "rebalance_months", "max_points"} and schema["required"] == ["screen"]
    assert schema["properties"]["rebalance_months"]["default"] == 12 and schema["additionalProperties"] is False
    # as a job
    jj = c.post("/jobs", json={"kind": "echo_stats", "args": {"screen": "dogs", "rebalance_months": 6}}).json()
    for _ in range(300):
        st = c.get(f"/jobs/{jj['job_id']}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    assert c.get(f"/jobs/{jj['job_id']}/result").json()["screen"] == "dogs"
    assert any(o["name"] == "echo_stats" for o in c.get("/operations").json()["operations"])
    # MCP tool functions
    tools = build_tools(reg)
    assert tools["echo_stats"]("dogs", 6)["rows"] == j["rows"]
    assert "Summary statistics" in tools["echo_stats"].__doc__ and "rebalance_months" in tools["echo_stats"].__doc__
    assert reg.job_manager.get(jj["job_id"]).status == "done"
    reg.job_manager.shutdown()


def test_operation_registered_after_the_app_is_routed(reg):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    c = TestClient(create_app(reg))

    @register_operation
    class Late(Operation):
        """Registered after create_app."""
        name = "late_one"
        takes_max_points = False

        @dataclass
        class Args:
            x: float = arg(1.5, "a number")

        def run(self, lab, x=1.5):
            return pd.DataFrame({"x": [x]}, index=["r"])

        def to_json(self, result, max_points=500, *, lab=None, kw=None):
            return {"x": float(result["x"].iloc[0])}
    try:
        assert c.post("/labs/synthetic/late_one", json={"x": 2.5}).json()["x"] == 2.5
        assert "LateOneArgs" in c.get("/openapi.json").json()["components"]["schemas"]
        assert reg.late_one(x=3.0)["x"] == 3.0                                        # LabRegistry method
    finally:
        OPERATIONS.pop("late_one", None)


# ---------------------------------------------------------------------------------------------------
# built-in operations
# ---------------------------------------------------------------------------------------------------
SAMPLES = {
    "picks": {"screen": "dogs", "asof": "2005-06-30", "columns": ["pe", "pb"]},
    "backtest": {"screens": ["dogs"], "start": "2002-01-31", "config": {"rebalance_months": 6, "cost_bps": 20},
                 "exit": {"signals": [{"type": "TrendBreak", "window_months": 6}, {"type": "Breadth", "days": 100}],
                          "threshold": 0.6, "park": 0.03, "reentry": "signal_or_rebalance"},
                 "with_and_without": True, "benchmark": "SYN_CAPWT", "compare": ["top20"]},
    "timing": {"screen": "dogs", "mode": "start", "stride": 3, "config": {"rebalance_months": 6}},
    "graham": {"preset": "defensive", "overrides": {"max_pe": 18.0}},
    "sweep": {"screen": "dogs", "grid": {"dogs.n": [5, 10]}, "objective": "Sharpe",
              "splits": {"train_years": 4.0, "test_years": 2.0}},
    "significance": {"screens": ["dogs"], "n": 100, "seed": 1, "config": {"rebalance_months": 12}},
    "extended": {"screens": ["dogs"], "macro": "statsmodels", "leverage": 1.5},
    "exit_evaluate": {"signals": [{"type": "TrendBreak"}, {"type": "VolRegime", "days": 63}],
                      "weights": {"TrendBreak": 2.0}, "horizons": (1, 3), "policy": {"park": "cash", "reentry_steps": 2}},
    "montecarlo": {"screens": ["dogs"], "generator": {"type": "BlockBootstrap", "block_months": 6}, "n_paths": 20,
                   "horizon_years": [2.0, 3.0], "start_wealth": 50000.0, "seed": 3, "steps": "monthly",
                   "target_wealth": 60000.0, "config": {"cost_bps": 10}},
    "fetch_data": {"sources": ["boc"], "dest": "data", "sec_user_agent": "A B a@b.c", "tickers": ["XIU.TO"],
                   "start": "2010-01-01", "periods": ["2023q1"]},
    "exit_calibrate": {"signals": [{"type": "TrendBreak"}], "grid": {"threshold": [0.6, 0.8]},
                       "constraints": {"max_switches_per_year": 4.0}},
}


def test_every_builtin_is_covered_and_documented():
    assert set(SAMPLES) == set(builtin_names()) == set(R.BUNDLED_KINDS)
    for name in builtin_names():
        op = OPERATIONS[name]
        assert op.Args is not None and all(s.doc for s in op.arg_specs()), name
        assert (op.__doc__ or "").strip(), name
    assert OPERATIONS["timing"].long_running and OPERATIONS["sweep"].counts_points


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_builtin_args_round_trip_through_the_typed_model(name):
    pytest.importorskip("pydantic")
    op, raw = OPERATIONS[name], SAMPLES[name]
    a = op.parse(dict(raw))                                   # dataclass: unknown / missing fields are 400s
    back = op.args_to_dict(a)
    assert {k: back[k] for k in raw} == raw
    M = op.request_model()
    dumped = M(**raw).model_dump(exclude_unset=True, mode="python")
    norm = lambda d: json.loads(json.dumps(d, default=list))
    assert norm(dumped) == norm(raw)                          # nothing lost or invented by the pydantic model
    op.precheck(raw)                                          # the lab-free validation accepts it too


def test_openapi_has_typed_bodies_without_open_objects(reg):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    spec = TestClient(create_app(reg)).get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in builtin_names():
        path = {"exit_evaluate": "exit/evaluate", "exit_calibrate": "exit/calibrate"}.get(name, name)
        op = spec["paths"][f"/labs/{{lab}}/{path}" if name not in ("picks", "graham") else f"/labs/{{lab}}/{name}"]
        post = op["post"]
        assert "$ref" in post["requestBody"]["content"]["application/json"]["schema"] or "anyOf" in \
            post["requestBody"]["content"]["application/json"]["schema"], name

    def open_objects(x, where=""):
        if isinstance(x, dict):
            if x.get("additionalProperties") is True:
                yield where
            for k, v in x.items():
                yield from open_objects(v, f"{where}/{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                yield from open_objects(v, f"{where}[{i}]")
    mine = {k: v for k, v in schemas.items() if k.endswith("Request") or k.endswith("Args") or k.endswith("Job") or k.startswith("Signal_")
            or k in ("ConfigOverrides", "ExitRequest", "PolicyRequest", "SplitsRequest", "GrahamOverrides")}
    assert "BacktestRequest" in mine and "TimingRequest" in mine
    free_form = {"EchoStatsRequest"}                           # none: Args declared; fx kinds of other tests are free-form
    bad = [w for k, v in mine.items() if k.split("_")[0] not in free_form for w in open_objects(v, k)
           if not k.startswith("Fx")]
    assert not bad, bad
    # picks / graham: typed query parameters on GET
    qp = {p["name"]: p for p in spec["paths"]["/labs/{lab}/graham"]["get"]["parameters"]}
    assert {"preset", "asof", "overrides"} <= set(qp)
    # the jobs body is a union over the operations
    jb = spec["paths"]["/jobs"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    refs = [x["$ref"].split("/")[-1] for x in jb.get("anyOf") or jb.get("oneOf")]
    assert {"BacktestJob", "TimingJob", "ExitCalibrateJob"} <= set(refs)


# ---------------------------------------------------------------------------------------------------
# run keys did not move
# ---------------------------------------------------------------------------------------------------
def _cases(lab):
    pol = ExitPolicy(ExitScreen([TrendBreak("SYN_CAPWT", window_months=6), Breadth(window_days=200),
                                 VolRegime("SYN_CAPWT", window=63)], check="W-FRI", threshold=0.6),
                     exposure_when_flagged=0.25, park=0.02, reentry="signal_or_rebalance", reentry_delay_days=10,
                     reentry_steps=2)
    cases = {
        "backtest_plain": ("backtest", {"screens": ["dogs", "graham_defensive"], "rebalance_months": 6, "cost_bps": 20}),
        "backtest_default": ("backtest", {}),
        "backtest_none_args": ("backtest", {"screens": None, "start": None}),
        "backtest_rich": ("backtest", {"screens": ["dogs", "cheap", Portfolio("h", "dogs", exit=pol, rebalance_months=3)],
                                       "cost_bps": cap_tiered({0: 75, 1000: 30}), "mer": {"_default": 0.001, "T0001": 0.01},
                                       "hold_rule": HoldRule(keep_within=2.0, band=0.2), "weighting": "inv_vol",
                                       "max_weight": 0.2, "benchmark": IndexBenchmark("SYN_CAPWT", mer=0.0006),
                                       "compare": [equal_weight_top(30)], "exit": pol, "with_and_without": True,
                                       "start": pd.Timestamp("2002-01-31")}),
        "backtest_override_screen": ("backtest", {"screens": [lab.screen("dogs", n=5),
                                                              lab.screen("graham_defensive", n=5, max_pe=12)]}),
        "timing_plain": ("timing", {"screen": "dogs", "stride": 3, "rebalance_months": 6}),
        "timing_default": ("timing", {"screen": "dogs"}),
        "timing_explicit_defaults": ("timing", {"screen": "dogs", "mode": "phase", "stride": 1}),
        "picks_plain": ("picks", {"screen": "dogs"}),
        "picks_asof": ("picks", {"screen": "dogs", "asof": "2005-06-30", "runners_up": 3}),
        "picks_cheap": ("picks", {"screen": "cheap"}),
        "graham": ("graham", {"tests": "defensive", "max_pe": 18}),
        "sweep": ("sweep", {"screen": "dogs", "grid": Grid({"dogs.n": [5, 10], "cost_bps": [10, 50]}), "objective": "Sharpe"}),
        "sweep_dict": ("sweep", {"screen": "dogs", "grid": {"dogs.n": [5, 10]}, "splits": {"train_years": 4, "test_years": 2}}),
        "significance": ("significance", {"screens": ["dogs"], "n": 200, "rebalance_months": 12}),
        "exit_evaluate": ("exit_evaluate", {"exit_screen": pol.screen, "horizons": (1, 3), "policy": pol}),
        "exit_calibrate": ("exit_calibrate", {"exit_screen": pol.screen, "grid": {"threshold": [0.6, 0.8]},
                                              "objective": "Calmar"}),
    }
    try:
        m = R.macro_ref(MacroSeries.from_statsmodels_macrodata(lab.market.index), "statsmodels")
        cases["extended"] = ("extended", {"screens": ["dogs"], "macro": m, "leverage": 1.5})
    except Exception:
        pass
    return cases


def test_run_keys_identical_to_before_the_refactor():
    """tests/fixtures/run_keys_pre_j4.json was captured from the code before J4 (code_version 'fixture-1')."""
    import warnings
    warnings.simplefilter("ignore")
    fx = json.loads(FIXTURE.read_text())
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    lab.name = "synthetic"
    lab.define(ScreenBuilder("cheap").universe(top=100).where("pe < 15").rank(div_yield="desc").take(8))
    did, ctx = R.dataset_id(lab.market), R.lab_context(lab)
    for name, (kind, args) in _cases(lab).items():
        if name not in fx["cases"]:
            continue
        spec = R.RunSpec(kind, "synthetic", args)
        want = fx["cases"][name]
        assert spec.canonical() == want["canonical"], name
        assert spec.family() == want["family"], name
        if did == fx["dataset_id"]:                                  # the data fingerprint is environment dependent
            assert ctx.hash == fx["lab_hash"]
            assert R.run_key_for(spec, did, ctx, "fixture-1") == want["run_key"], name
    assert {"backtest_plain", "timing_plain", "picks_plain"} <= set(fx["cases"])


# ---------------------------------------------------------------------------------------------------
# self-serializing types
# ---------------------------------------------------------------------------------------------------
def test_deprecated_signal_names_share_a_run_key(lab):
    import warnings
    warnings.simplefilter("ignore", DeprecationWarning)
    old, new = Breadth(window_days=200), Breadth(days=200 / CAL_PER_TRADING_DAY)
    assert R.canonical_json(old) == R.canonical_json(new)
    as_dict = {"__t": "Signal", "type": "Breadth", "params": {"window_days": 200}}          # a stored, older spec
    assert R.canonical_json(as_dict) == R.canonical_json(new)
    assert R.canonical_json(VolRegime("SYN_CAPWT", window=63)) == R.canonical_json(VolRegime("SYN_CAPWT", days=63))
    keys = [R.RunSpec("exit_evaluate", "synthetic", {"exit_screen": ExitScreen([s])}).canonical() for s in (old, new)]
    assert keys[0] == keys[1]
    assert R.to_dict(Breadth())["params"] == {}


def test_types_own_their_serialization():
    for obj in (Config(rebalance_months=6, hold_rule=HoldRule(keep_within=2.0)), HoldRule(band=0.1),
                ExitScreen([Breadth(days=100)]), Breadth(days=100), IndexBenchmark("X", mer=0.01),
                equal_weight_top(40), Grid({"a": [1, 2]}), Portfolio("p", "dogs", rebalance_months=3),
                cap_tiered({0: 75, 1000: 30})):
        d = obj.to_dict()
        assert d["__t"] and d == R.to_dict(obj)
        back = type(obj).from_dict(json.loads(json.dumps(d)))
        assert R.to_dict(back) == d
    assert Config.from_dict({"__t": "Config", "rebalance_months": 3}).rebalance_months == 3


def test_cap_tiered_is_a_picklable_class_not_a_closure(lab):
    from screenlab.backtest import CapTiered
    f = cap_tiered({0: 75, 1_000: 30, 10_000: 10})
    assert isinstance(f, CapTiered) and "<locals>" not in type(f).__qualname__
    g = pickle.loads(pickle.dumps(f))
    snap = lab.snapshot()
    pd.testing.assert_series_equal(f(snap), g(snap))
    assert g == f and R.to_dict(g) == {"__t": "cap_tiered", "tiers": [[0.0, 75.0], [1000.0, 30.0], [10000.0, 10.0]]}
    assert not hasattr(R, "_cap_tiers")


def test_config_overrides_hold_rule_round_trip_and_run_key(lab):
    from screenlab.operations import _models
    op = OPERATIONS["backtest"]
    model = _models.request_model(op)
    a = model.model_validate({"screens": ["dogs"], "config": {"hold_rule": {"keep_within": 2.0, "band": 0.2},
                                                               "weighting": "cap", "max_weight": 0.1, "sector_cap": 0.3}})
    assert a.config.hold_rule.keep_within == 2.0 and a.config.weighting == "cap"
    from screenlab.ops._request import config_overrides
    cfg = config_overrides(a.config.model_dump(exclude_none=True))
    assert cfg["hold_rule"] == HoldRule(keep_within=2.0, band=0.2)
    assert cfg["max_weight"] == 0.1 and cfg["sector_cap"] == 0.3
    with pytest.raises(Exception):
        model.model_validate({"screens": ["dogs"], "config": {"hold_rule": {"nope": 1}}})
    from screenlab.construction import WEIGHTINGS
    assert set(model.model_json_schema()["$defs"]["ConfigOverrides"]["properties"]["weighting"]["anyOf"][0]["enum"]) == set(WEIGHTINGS)
    ctx = R.lab_context(lab)
    did = R.dataset_id(lab.market)
    keys = []
    for hr in (None, {"keep_within": 2.0}, {"keep_within": 3.0}):
        cfg = {} if hr is None else {"hold_rule": hr}
        keys.append(R.run_key_for(R.RunSpec("backtest", "synthetic", {"screens": ["dogs"], "config": cfg}), did, ctx, "t"))
    assert None not in keys and len(set(keys)) == 3
