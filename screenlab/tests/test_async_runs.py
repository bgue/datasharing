"""Phase 0 stage 2: progress/cancel in the real loops, Lab(store=, jobs=), Lab.submit, trial counting,
REST /jobs*, MCP run tools, sync timeout. (Fake-kind job tests live in test_jobs.py.)"""
import os
import time

import numpy as np
import pandas as pd
import pytest

from screenlab import (Breadth, ExitScreen, JobManager, Lab, RunSpec, SQLiteStore, TrendBreak, progress,
                              stats)
from screenlab.jobs import execute_spec
from screenlab.tests import _jobfx as fx
from screenlab.tracking import PROCESS_TRIALS

LABS = {"synthetic": fx.make_lab, "daily": fx.make_daily_lab}


@pytest.fixture(autouse=True)
def fresh_process_trials():
    PROCESS_TRIALS._trials.clear()
    yield


@pytest.fixture(scope="module")
def lab():
    return fx.make_lab()


@pytest.fixture
def jm(tmp_path):
    m = JobManager(store=SQLiteStore(tmp_path / "runs.sqlite"), workers=2, mode="thread", labs=LABS)
    yield m
    m.shutdown()


def wait_for(cond, timeout=30):
    t = time.time()
    while not cond():
        assert time.time() - t < timeout, "timed out"
        time.sleep(0.005)


def trace(lab_, fn):
    """Run fn() under a recording job context: [(frac, msg)]."""
    ev = []
    with progress.job_context(lambda f, m: ev.append((f, m)), None):
        out = fn()
    return out, ev


# ------------------------------------------------------------------ hooks in the real loops
def assert_progress(ev, word):
    fr = [f for f, _ in ev]
    assert fr and all(0.0 <= f <= 1.0 for f in fr)
    assert all(b >= a - 1e-12 for a, b in zip(fr, fr[1:])), "progress went backwards"
    assert any(word in m for _, m in ev) and max(fr) > 0.5


def test_hooks_in_backtest_timing_sweep_calibration(lab):
    _, ev = trace(lab, lambda: lab.backtest(["dogs", "graham_defensive"], rebalance_months=6))
    assert_progress(ev, "rebalance")
    assert any(m.startswith("graham_defensive") for _, m in ev) and any(f >= 0.5 for f, m in ev if m.startswith("graham"))
    _, ev = trace(lab, lambda: lab.timing("dogs", stride=3))
    assert_progress(ev, "offset")
    _, ev = trace(lab, lambda: lab.sweep("dogs", {"dogs.n": [5, 8, 10], "cost_bps": [10, 50]}))
    assert_progress(ev, "sweep point")
    es = ExitScreen([TrendBreak("SYN_CAPWT"), Breadth()])
    _, ev = trace(lab, lambda: lab.calibrate_exit(es, {"threshold": [0.6, 0.7, 0.8]},
                                                  splits=dict(train_years=4, test_years=2)))
    assert_progress(ev, "calibrate")


def test_hooks_leave_numbers_unchanged(lab):
    plain = lab.backtest(["dogs", "magic_formula"], rebalance_months=6)
    traced, _ = trace(lab, lambda: lab.backtest(["dogs", "magic_formula"], rebalance_months=6))
    pd.testing.assert_frame_equal(plain.table, traced.table, check_exact=True)
    pd.testing.assert_frame_equal(plain.curves, traced.curves, check_exact=True)


def test_exit_policy_two_pass_progress_is_monotonic(lab):
    from screenlab import ExitPolicy, PortfolioStop
    pol = ExitPolicy(ExitScreen([PortfolioStop()], threshold=0.5))
    _, ev = trace(lab, lambda: lab.backtest(["dogs"], exit=pol))
    fr = [f for f, _ in ev]
    assert all(b >= a - 1e-12 for a, b in zip(fr, fr[1:])) and max(fr) > 0.5


def test_cancel_a_daily_timing_study_within_one_offset(jm):
    job = jm.submit(RunSpec("timing", "daily", {"screen": "dogs", "stride": 1}))
    wait_for(lambda: job.progress > 0.03)
    assert job.status == "running" and "offset" in job.message
    t0 = time.time()
    job.cancel()
    assert job.wait(10) and job.status == "cancelled"
    assert time.time() - t0 < 2.0                      # one offset takes ~0.15 s here
    assert job.progress < 0.9
    assert jm.store.get(job.run_key).status == "cancelled"


def test_cancel_sweep_and_calibration(jm):
    grid = {"dogs.n": list(range(3, 15)), "cost_bps": [5, 10, 20, 40, 80]}
    job = jm.submit(RunSpec("sweep", "synthetic", {"screen": "dogs", "grid": grid}))
    wait_for(lambda: job.progress > 0.1)
    job.cancel()
    assert job.wait(10) and job.status == "cancelled"
    es = ExitScreen([TrendBreak("SYN_CAPWT"), Breadth()])
    job = jm.submit(RunSpec("exit_calibrate", "synthetic", {
        "exit_screen": es, "grid": {"threshold": [0.5, 0.6, 0.7, 0.8], "hysteresis": [0.05, 0.1, 0.2],
                                     "signals.TrendBreak.window_months": [4, 6, 8, 10, 12]}}))
    wait_for(lambda: job.progress > 0.05)
    job.cancel()
    assert job.wait(30) and job.status == "cancelled"


def test_real_kinds_as_jobs_match_synchronous_calls(jm, lab):
    cases = [("timing", {"screen": "dogs", "stride": 4}, lambda: lab.timing("dogs", stride=4)),
             ("sweep", {"screen": "dogs", "grid": {"dogs.n": [5, 10]}}, lambda: lab.sweep("dogs", {"dogs.n": [5, 10]})),
             ("graham", {"tests": "defensive"}, lambda: lab.graham("defensive")),
             ("picks", {"screen": "dogs"}, lambda: lab.picks("dogs"))]
    for kind, args, sync in cases:
        got, want = jm.submit(RunSpec(kind, "synthetic", args)).result(60), sync()
        if kind == "timing":
            pd.testing.assert_frame_equal(got.table, want.table, check_exact=True)
        elif kind == "graham":
            pd.testing.assert_frame_equal(got[0], want[0], check_exact=True)
        else:
            pd.testing.assert_frame_equal(got, want, check_exact=True)
    sig = jm.submit(RunSpec("significance", "synthetic", {"screens": ["dogs"], "n": 300}, seed=0)).result(60)
    assert "DSR" in sig.columns and list(sig.index) == ["dogs"]


# ------------------------------------------------------------------ Lab(store=, jobs=) and Lab.submit
def test_default_lab_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCREENLAB_STORE", raising=False)
    L = fx.make_lab()
    assert L.store is None and L.jobs is None
    L.backtest(["dogs"])
    assert list(tmp_path.iterdir()) == []
    assert L.last_trials()["trials_scope"] == "process"


def test_env_turns_the_store_on(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENLAB_STORE", str(tmp_path / "e.sqlite"))
    L = fx.make_lab()
    assert isinstance(L.store, SQLiteStore)
    L.backtest(["dogs"])
    assert len(L.store.find(kind="backtest")) == 2
    L.store.close()


def test_sync_runs_are_recorded_and_match_job_keys(tmp_path):
    store = SQLiteStore(tmp_path / "runs.sqlite")
    L = fx.make_lab()
    L.store, L.name = store, "synthetic"
    rep = L.backtest(["dogs", "graham_defensive"], rebalance_months=6)
    found = store.find(kind="backtest")
    assert set(found["row_label"]) == {"SYN_CAPWT", "dogs", "graham_defensive"} and (found["dataset"] == "synthetic").all()
    assert store.datasets()["name"].tolist() == ["synthetic"]
    # the identical request as a job is a cache hit: the SDK call and the spec have the same run_key
    L.jobs = JobManager(store=store, mode="thread", labs={"synthetic": L})
    job = L.submit("backtest", screens=["dogs", "graham_defensive"], rebalance_months=6)
    assert job.cache_hit and job.run_key == found["run_key"].iloc[0]
    assert job.result().metrics.loc["dogs", "CAGR"] == pytest.approx(rep.table.loc["dogs", "CAGR"])
    L.jobs.shutdown()
    store.close()


def test_lab_submit_without_a_manager_uses_a_thread_pool(tmp_path):
    L = fx.make_lab()
    L.store = SQLiteStore(tmp_path / "r.sqlite")
    try:
        job = L.submit("timing", screen="dogs", stride=6, label="t")
        res = job.result(60)
        pd.testing.assert_frame_equal(res.table, L.timing("dogs", stride=6).table, check_exact=True)
        again = L.submit("timing", screen="dogs", stride=6)
        assert again.cache_hit
        assert not L.submit("timing", screen="dogs", stride=6, force=True).cache_hit
    finally:
        L.jobs.shutdown()
        L.store.close()


# ------------------------------------------------------------------ trials
def test_sweep_counts_points_and_deflates(tmp_path):
    L = fx.make_lab()
    L.store, L.name = SQLiteStore(tmp_path / "r.sqlite"), "synthetic"
    g1 = {"dogs.n": [5, 8, 10], "cost_bps": [10, 50]}
    t1 = L.sweep("dogs", g1)
    assert t1.attrs["trials_in_family"] == 6 and t1.attrs["n_trials"] == 6 and t1.attrs["trials_scope"] == "store"
    assert L.sweep("dogs", g1).attrs["trials_in_family"] == 6                 # same points: not new trials
    t3 = L.sweep("dogs", {"dogs.n": [5, 12], "cost_bps": [10, 50]})            # two new points
    assert t3.attrs["trials_in_family"] == 8
    other = L.sweep("graham_defensive", {"cost_bps": [10, 50]})                 # another family
    assert other.attrs["trials_in_family"] == 2
    d_few = stats.summarize_trials(t1, n_trials=len(t1))["DSR"].max()
    d_many = stats.summarize_trials(t3, n_trials=t3.attrs["n_trials"])["DSR"].max()
    assert 0 <= d_many <= 1 and 0 <= d_few <= 1
    L.store.close()


def test_significance_uses_family_count_and_calibration_records_it(tmp_path):
    L = fx.make_lab()
    L.store, L.name = SQLiteStore(tmp_path / "r.sqlite"), "synthetic"
    rep = L.backtest(["dogs"])
    s1 = L.significance(rep, n=200)
    assert s1.attrs["trials_in_family"] == 1 and s1.attrs["n_trials"] == 1
    rep2 = L.backtest(["dogs"], rebalance_months=6)
    s2 = L.significance(rep2, n=200)
    assert s2.attrs["trials_in_family"] == 2
    L.significance(rep2, n=200)                                                 # repeated: no new trial
    assert L.last_trials()["trials_in_family"] == 2
    es = ExitScreen([TrendBreak("SYN_CAPWT"), Breadth()])
    res = L.calibrate_exit(es, {"threshold": [0.6, 0.7, 0.8]})
    assert res.summary["trials_in_family"] == 3 and res.summary["n_trials"] == 3
    res = L.calibrate_exit(es, {"threshold": [0.6, 0.7, 0.8, 0.9]})
    assert res.summary["trials_in_family"] == 4
    L.store.close()


def test_process_counter_without_a_store():
    L = fx.make_lab()
    t = L.sweep("dogs", {"dogs.n": [5, 8]})
    assert t.attrs["trials_scope"] == "process" and t.attrs["trials_in_family"] == 2
    assert L.sweep("dogs", {"dogs.n": [5, 8, 10]}).attrs["trials_in_family"] == 3


def test_jobs_count_the_same_trials_as_sync_calls(jm):
    job = jm.submit(RunSpec("sweep", "synthetic", {"screen": "dogs", "grid": {"dogs.n": [5, 8, 10]}}))
    res = job.result(60)
    assert res.attrs["trials_in_family"] == 3 and res.attrs["trials_scope"] == "store"
    fam = RunSpec("sweep", "synthetic", {"screen": "dogs", "grid": {}}).family()
    assert jm.store.trial_count(fam) == 3 and jm.store.trial_count(fam + "|runs") == 1


# ------------------------------------------------------------------ REST and MCP
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from screenlab.server.mcp_server import build_tools  # noqa: E402
from screenlab.server.registry import LabRegistry, default_store  # noqa: E402
from screenlab.server.rest import create_app  # noqa: E402


@pytest.fixture
def reg(tmp_path):
    r = LabRegistry(dict(LABS), default="synthetic", store=SQLiteStore(tmp_path / "srv.sqlite"),
                    mode="thread", workers=2, sync_timeout=0)
    yield r
    if r._jm is not None:
        r._jm.shutdown()


def poll(client, jid, timeout=60):
    t = time.time()
    while True:
        st = client.get(f"/jobs/{jid}").json()
        if st["status"] in ("done", "failed", "cancelled"):
            return st
        assert time.time() - t < timeout
        time.sleep(0.02)


def test_rest_job_lifecycle_and_store_hit(reg):
    client = TestClient(create_app(reg))
    body = {"kind": "timing", "lab": "daily", "args": {"screen": "dogs", "stride": 21}, "label": "daily dogs"}
    r = client.post("/jobs", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] in ("queued", "running") and not j["cache_hit"] and len(j["run_key"]) == 64
    early = client.get(f"/jobs/{j['job_id']}/result")
    if early.status_code != 200:
        assert early.status_code == 409 and "poll" in early.json()["error"]
    st = poll(client, j["job_id"])
    assert st["status"] == "done" and st["progress"] == 1.0
    client2 = TestClient(create_app(reg))                                       # a different connection
    res = client2.get(f"/jobs/{j['job_id']}/result")
    assert res.status_code == 200
    out = res.json()
    assert out["screen"] == "dogs" and len(out["table"]["rows"]) >= 10 and out["trials_in_family"] == 1
    # the synchronous endpoint returns the same JSON
    sync = client.post("/labs/daily/timing", json={"screen": "dogs", "stride": 21}).json()
    assert sync == out
    # identical second request: straight from the store
    t0 = time.time()
    r2 = client.post("/jobs", json=body).json()
    assert r2["cache_hit"] and r2["status"] == "done" and r2["run_key"] == j["run_key"]
    assert client.get(f"/jobs/{r2['job_id']}/result").json() == out
    assert time.time() - t0 < 1.0
    assert reg.store.find(kind="timing")["run_key"].nunique() == 1
    jobs = client.get("/jobs").json()["jobs"]
    assert {x["job_id"] for x in jobs} >= {j["job_id"], r2["job_id"]}
    assert len(client.get("/jobs?status=done").json()["jobs"]) == 2 and client.get("/jobs?status=bogus").status_code == 400


def test_rest_cancel_errors_and_validation(reg):
    client = TestClient(create_app(reg))
    j = client.post("/jobs", json={"kind": "timing", "lab": "daily", "args": {"screen": "dogs", "stride": 1}}).json()
    t = time.time()
    while client.get(f"/jobs/{j['job_id']}").json()["progress"] < 0.03:
        assert time.time() - t < 30
        time.sleep(0.01)
    assert client.get(f"/jobs/{j['job_id']}/result").status_code == 409
    d = client.delete(f"/jobs/{j['job_id']}")
    assert d.status_code == 200 and d.json()["cancel_requested"] is True
    assert poll(client, j["job_id"])["status"] == "cancelled"
    assert "cancelled" in client.get(f"/jobs/{j['job_id']}/result").json()["error"]
    assert client.get("/jobs/nope").status_code == 404 and client.delete("/jobs/nope").status_code == 404
    assert client.post("/jobs", json={"args": {}}).status_code == 400                       # no kind
    assert client.post("/jobs", json={"kind": "nope"}).status_code == 400
    assert client.post("/jobs", json={"kind": "backtest", "bogus": 1}).status_code == 400
    bad = client.post("/jobs", json={"kind": "backtest", "args": {"screens": ["dogs"], "config": {"x": 1}}})
    assert bad.status_code == 400 and "config field" in bad.json()["error"]
    assert client.post("/jobs", json={"kind": "backtest", "lab": "mars"}).status_code == 404
    fail = client.post("/jobs", json={"kind": "backtest", "args": {"screens": ["no_such_screen"]}}).json()
    assert poll(client, fail["job_id"])["status"] == "failed"
    r = client.get(f"/jobs/{fail['job_id']}/result")
    assert r.status_code == 409 and "no_such_screen" in r.json()["error"]


def test_every_sync_result_carries_trials(reg):
    client = TestClient(create_app(reg))
    for path, body in [("/labs/synthetic/backtest", {"screens": ["dogs"]}),
                       ("/labs/synthetic/timing", {"screen": "dogs", "stride": 6}),
                       ("/labs/synthetic/sweep", {"screen": "dogs", "grid": {"dogs.n": [5, 8]}}),
                       ("/labs/synthetic/significance", {"screens": ["dogs"], "n": 100}),
                       ("/labs/synthetic/exit/evaluate", {"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}]}),
                       ("/labs/synthetic/exit/calibrate", {"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}],
                                                           "grid": {"threshold": [0.6, 0.7]}})]:
        out = client.post(path, json=body)
        assert out.status_code == 200, (path, out.text)
        j = out.json()
        assert j["trials_in_family"] >= 1 and j["trials_scope"] == "store", path
    assert client.get("/labs/synthetic/graham").json()["trials_in_family"] == 1
    assert client.get("/labs/synthetic/picks/dogs").json()["trials_in_family"] == 1
    sw = client.post("/labs/synthetic/sweep", json={"screen": "dogs", "grid": {"dogs.n": [5, 8, 11]}}).json()
    assert sw["trials_in_family"] == 3 and sw["n_trials"] == 3 and "dsr" in sw


def test_job_json_equals_sync_json_for_exit_kinds(reg):
    client = TestClient(create_app(reg))
    for kind, path, args in [
            ("exit_evaluate", "/labs/synthetic/exit/evaluate", {"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}],
                                                                "policy": {"reentry_steps": 2}}),
            ("significance", "/labs/synthetic/significance", {"screens": ["dogs"], "n": 100}),
            ("backtest", "/labs/synthetic/backtest", {"screens": ["dogs"], "config": {"rebalance_months": 6},
                                                      "exit": {"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}]}})]:
        j = client.post("/jobs", json={"kind": kind, "args": args}).json()
        assert poll(client, j["job_id"])["status"] == "done", kind
        via_job = client.get(f"/jobs/{j['job_id']}/result").json()
        sync = client.post(path, json=args).json()
        assert via_job == sync, kind


def test_sync_timeout_stops_the_run_and_points_at_jobs(tmp_path):
    r = LabRegistry(dict(LABS), default="daily", sync_timeout=0.3)
    client = TestClient(create_app(r))
    t = time.time()
    out = client.post("/labs/daily/timing", json={"screen": "dogs", "stride": 1})
    assert out.status_code == 408 and "/jobs" in out.json()["error"] and time.time() - t < 3
    time.sleep(0.5)
    import threading
    assert not [x for x in threading.enumerate() if x.name.startswith("screenlab-sync")]   # the run was cancelled
    r.sync_timeout = 60
    assert client.post("/labs/daily/timing", json={"screen": "dogs", "stride": 63}).status_code == 200


def test_mcp_run_tools(reg):
    tools = build_tools(reg)
    assert {"submit_run", "run_status", "run_result", "cancel_run", "list_runs"} <= set(tools)
    j = tools["submit_run"]("timing", {"screen": "dogs", "stride": 21}, "daily")
    t = time.time()
    while tools["run_status"](j["job_id"])["status"] not in ("done", "failed"):
        assert time.time() - t < 60
        time.sleep(0.02)
    out = tools["run_result"](j["job_id"])
    assert out["trials_in_family"] == 1 and out == tools["timing"]("dogs", "phase", 21, lab="daily")
    assert tools["submit_run"]("timing", {"screen": "dogs", "stride": 21}, "daily")["cache_hit"]
    c = tools["submit_run"]("timing", {"screen": "dogs", "stride": 1}, "daily", label="slow")
    tools["cancel_run"](c["job_id"])
    assert any(x["job_id"] == c["job_id"] for x in tools["list_runs"]()["jobs"])
    from screenlab.server.registry import ServerError
    with pytest.raises(ServerError):
        tools["run_status"]("nope")


def test_server_store_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENLAB_STORE", str(tmp_path / "x" / "s.sqlite"))
    s = default_store()
    assert isinstance(s, SQLiteStore) and s.path.endswith("s.sqlite") and os.path.exists(s.path)
    s.close()
    monkeypatch.setenv("SCREENLAB_STORE", "none")
    assert default_store().persistent is False
    monkeypatch.delenv("SCREENLAB_STORE")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = default_store()
    assert s.path == str(tmp_path / "home" / ".screenlab" / "runs.sqlite")
    s.close()


# ------------------------------------------------------------------ the lab is part of the key
def test_labs_differing_only_in_config_never_share_cached_results(tmp_path):
    from screenlab import Config
    store = SQLiteStore(tmp_path / "shared.sqlite")
    a, b, a2 = (Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3, config=Config(cost_bps=c))
                for c in (25.0, 80.0, 25.0))
    for L in (a, b, a2):
        L.store, L.name = store, "synthetic"
    ra, rb = a.backtest(["dogs"]), b.backtest(["dogs"])
    assert ra.table.loc["dogs", "CAGR"] != rb.table.loc["dogs", "CAGR"]
    assert store.find(kind="backtest")["run_key"].nunique() == 2
    a2.backtest(["dogs"])                                                        # identical lab: same run
    assert store.find(kind="backtest")["run_key"].nunique() == 2
    for L, want in ((a, ra), (b, rb)):
        m = JobManager(store=store, mode="thread", labs={"synthetic": L})
        job = m.submit(RunSpec("backtest", "synthetic", {"screens": ["dogs"]}))
        assert job.cache_hit and job.result().metrics.loc["dogs", "CAGR"] == pytest.approx(want.table.loc["dogs", "CAGR"])
        m.shutdown()
    c = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3, config=Config(cost_bps=40.0))
    m = JobManager(store=store, mode="thread", labs={"synthetic": c})
    assert not m.submit(RunSpec("backtest", "synthetic", {"screens": ["dogs"]})).cache_hit
    m.shutdown()
    store.close()


# ------------------------------------------------------------------ S&P 500 lab: function screens are persistable
def _sp500(store=None):
    from screenlab.realdata.bundled import sp500_lab
    lab = sp500_lab(store=store)
    lab.name = "sp500"
    return lab


def test_sp500_backtest_has_run_key_caches_reruns_and_counts_trials(tmp_path):
    from screenlab.runspec import dataset_id, lab_context, run_key_for
    from screenlab.tracking import trial_store
    store = SQLiteStore(tmp_path / "runs.sqlite")
    lab = _sp500(store)
    rep = lab.backtest(["momentum_12_1", "low_vol"], rebalance_months=6)
    spec = RunSpec("backtest", "sp500", {"screens": ["momentum_12_1", "low_vol"], "rebalance_months": 6})
    key = run_key_for(spec, dataset_id(lab.market), lab_context(lab))
    assert key and not key.startswith("unpersistable:")
    assert store.cached(key) is not None
    assert lab.last_trials()["trials_in_family"] == 1
    # second submit is a cache hit with identical metrics
    job = lab.submit("backtest", screens=["momentum_12_1", "low_vol"], rebalance_months=6)
    assert job.cache_hit
    pd.testing.assert_frame_equal(job.result().table, rep.table)
    # rerun from the store reproduces the numbers
    out = store.rerun(key, lambda sp: execute_spec(sp, lab), dataset_id=dataset_id(lab.market))
    assert out.match
    # a new variation is a second trial of the family
    lab.backtest(["momentum_12_1", "low_vol"], rebalance_months=3)
    assert lab.last_trials()["trials_in_family"] == 2
    store.close()


def test_sp500_process_mode_matches_sync(tmp_path):
    sync = _sp500().backtest(["momentum_12_1"], rebalance_months=6)
    from screenlab.realdata.bundled import sp500_lab
    with JobManager(store=SQLiteStore(tmp_path / "p.sqlite"), workers=1, mode="process",
                    labs={"sp500": sp500_lab}) as m:
        job = m.submit(RunSpec("backtest", "sp500", {"screens": ["momentum_12_1"], "rebalance_months": 6}))
        res = job.result(180)
        assert job.run_key and not job.run_key.startswith("unpersistable:")
        pd.testing.assert_frame_equal(res.table, sync.table, check_exact=True)
