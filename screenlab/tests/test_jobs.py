"""JobManager: dedup, cache, progress, cancel, errors; thread mode mostly, one process-mode check each way."""
import threading
import time

import pandas as pd
import pytest

from screenlab import progress
from screenlab.jobs import JobError, JobManager, execute_spec
from screenlab.progress import Cancelled
from screenlab.runspec import RunSpec
from screenlab.store import NullStore, SQLiteStore
from screenlab.tests import _jobfx as fx

LABS = {"synthetic": fx.make_lab}


@pytest.fixture
def jm(tmp_path):
    m = JobManager(store=SQLiteStore(tmp_path / "runs.sqlite"), workers=3, mode="thread", labs=LABS)
    yield m
    fx.GATE.set()
    m.shutdown()


def bt_spec(**kw):
    return RunSpec("backtest", "synthetic", {"screens": ["dogs", "graham_defensive"], "rebalance_months": 6, **kw})


def wait_for(cond, timeout=10):
    t = time.time()
    while not cond():
        assert time.time() - t < timeout, "timed out"
        time.sleep(0.005)


# ------------------------------------------------------------------ progress module
def test_progress_is_a_noop_outside_a_job():
    assert not progress.active()
    progress.report(0.5, "x")
    progress.check_cancel()
    assert list(progress.track(range(3))) == [0, 1, 2]


def test_progress_context_scaling_and_cancel():
    got, ev = [], threading.Event()
    with progress.job_context(lambda f, m: got.append((round(f, 3), m)), ev):
        assert progress.active()
        progress.report(0.25, "a")
        with progress.scaled(0.5, 1.0):
            progress.report(0.5, "b")
        ev.set()
        with pytest.raises(Cancelled):
            progress.report(0.9)
        with pytest.raises(Cancelled):
            progress.check_cancel()
    assert got == [(0.25, "a"), (0.75, "b")] and not progress.active()


# ------------------------------------------------------------------ results
def test_thread_result_equals_synchronous_call_bit_for_bit(jm):
    sync = fx.make_lab().backtest(["dogs", "graham_defensive"], rebalance_months=6)
    job = jm.submit(bt_spec())
    res = job.result(30)
    assert job.status == "done" and job.progress == 1.0 and not job.cache_hit
    pd.testing.assert_frame_equal(res.table, sync.table, check_exact=True)
    pd.testing.assert_frame_equal(res.curves, sync.curves, check_exact=True)


def test_cache_hit_does_not_recompute(jm):
    sp = RunSpec("fx_count", "synthetic", {"x": 1})
    before = fx.STATE["count"]
    j1 = jm.submit(sp)
    j1.result(10)
    assert fx.STATE["count"] == before + 1 and j1.run_key
    j2 = jm.submit(RunSpec("fx_count", "synthetic", {"x": 1.0}, label="same run, other label"))
    assert j2.cache_hit and j2.status == "done" and j2.run_key == j1.run_key
    assert fx.STATE["count"] == before + 1                                   # worker not called
    assert j2.result().metrics.loc["r", "x"] == 1.0
    j3 = jm.submit(sp, force=True)
    j3.result(10)
    assert fx.STATE["count"] == before + 2 and not j3.cache_hit
    jm.submit(RunSpec("fx_count", "synthetic", {"x": 2})).result(10)           # different args: new run
    assert fx.STATE["count"] == before + 3


def test_in_flight_dedup_attaches_to_the_running_job(jm):
    fx.GATE.clear()
    sp = RunSpec("fx_gate", "synthetic", {"x": 7})
    a = jm.submit(sp)
    wait_for(lambda: a.status == "running")
    b = jm.submit(sp)
    c = jm.submit(sp, force=True)
    assert b is a and c is a
    fx.GATE.set()
    assert a.result(10).loc["g", "x"] == 7 and len(jm.jobs()) == 1


def test_progress_monotonic_and_reaches_one(jm):
    job = jm.submit(RunSpec("fx_loop", "synthetic", {"n": 40, "sleep": 0.01}))
    seen = []
    while not job.done():
        seen.append(job.progress)
        time.sleep(0.003)
    seen.append(job.progress)
    assert job.status == "done" and seen[-1] == 1.0
    assert all(b >= a for a, b in zip(seen, seen[1:])) and any(0 < x < 1 for x in seen)


def test_cancel_running_job_stops_within_one_step(jm):
    job = jm.submit(RunSpec("fx_loop", "synthetic", {"n": 400, "sleep": 0.01}))
    wait_for(lambda: job.progress > 0.1)
    assert job.cancel()
    at_cancel = fx.STATE["steps"]
    assert job.wait(5) and job.status == "cancelled"
    assert fx.STATE["steps"] - at_cancel <= 12 and fx.STATE["steps"] < 400      # poll gap is 50 ms
    with pytest.raises(Cancelled):
        job.result()
    assert jm.store.get(job.run_key).status == "cancelled"
    assert not job.cancel()                                                    # already finished
    again = jm.submit(RunSpec("fx_loop", "synthetic", {"n": 3, "sleep": 0.0}))   # a different key still runs
    assert again.result(10) is not None


def test_cancel_queued_job_is_immediate(tmp_path):
    fx.GATE.clear()
    m = JobManager(store=SQLiteStore(tmp_path / "r.sqlite"), workers=1, mode="thread", labs=LABS)
    try:
        first = m.submit(RunSpec("fx_gate", "synthetic", {"x": 1}))
        wait_for(lambda: first.status == "running")
        second = m.submit(RunSpec("fx_gate", "synthetic", {"x": 2}))
        assert second.status == "queued"
        second.cancel()
        assert second.wait(5) and second.status == "cancelled"
        fx.GATE.set()
        assert first.result(10) is not None
    finally:
        fx.GATE.set()
        m.shutdown()


def test_failed_job_stores_its_error(jm):
    job = jm.submit(RunSpec("fx_fail", "synthetic", {"msg": "kaboom"}))
    assert job.wait(10) and job.status == "failed"
    assert job.error == "ValueError: kaboom" and "Traceback" in job.traceback
    with pytest.raises(JobError, match="kaboom"):
        job.result()
    row = jm.store.get(job.run_key)
    assert row.status == "failed" and row.error == "ValueError: kaboom"
    assert jm.store.cached(job.run_key) is None
    retry = jm.submit(RunSpec("fx_fail", "synthetic", {"msg": "kaboom"}))      # a failure is never a cache hit
    assert retry is not job and retry.wait(10) and retry.status == "failed"


def test_unknown_inputs_raise_at_submit(jm):
    with pytest.raises(ValueError, match="unknown run kind"):
        jm.submit(RunSpec("nope", "synthetic"))
    with pytest.raises(ValueError, match="unknown dataset"):
        jm.submit(RunSpec("backtest", "mars"))
    single = jm.submit(RunSpec("fx_count", None, {"x": 5}))                     # one lab: dataset is implied
    assert single.spec.dataset == "synthetic" and single.result(10) is not None


def test_unpersistable_spec_runs_without_a_key(jm):
    spec = RunSpec("backtest", "synthetic", {"screens": ["dogs"], "cost_bps": lambda s: pd.Series(10.0, index=s.index)})
    job = jm.submit(spec)
    res = job.result(30)
    assert job.run_key is None and job.store_key.startswith("unpersistable:") and not job.cache_hit
    assert "dogs" in res.table.index
    again = jm.submit(spec)
    assert again is not job and again.run_key is None                           # never deduplicated
    again.result(30)


def test_results_persist_and_trials_are_counted(jm):
    sp = bt_spec()
    j = jm.submit(sp)
    j.result(30)
    stored = jm.store.get(j.run_key)
    assert stored.status == "done" and stored.metrics.loc["dogs", "CAGR"] == pytest.approx(j.result().table.loc["dogs", "CAGR"])
    fam = sp.family()
    assert jm.store.trial_count(fam) == 1
    jm.submit(sp).result()                                                      # cache hit: same key
    jm.submit(sp, force=True).result(30)
    assert jm.store.trial_count(fam) == 1
    jm.submit(bt_spec(rebalance_months=3)).result(30)
    assert jm.store.trial_count(fam) == 2
    assert jm.store.dataset(j.dataset_id)["name"] == "synthetic"
    assert jm.store.job_get(j.id)["status"] == "done"


def test_no_store_means_no_cache_and_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = JobManager(workers=1, mode="thread", labs=LABS)
    try:
        a = m.submit(RunSpec("fx_count", "synthetic", {"x": 1}))
        a.result(10)
        b = m.submit(RunSpec("fx_count", "synthetic", {"x": 1}))
        b.result(10)
        assert not b.cache_hit and b is not a
        assert m.store.trial_count(RunSpec("fx_count", "synthetic", {"x": 1}).family()) == 1
        assert list(tmp_path.iterdir()) == []
    finally:
        m.shutdown()


def test_execute_spec_passes_the_seed():
    lab = fx.make_lab()
    sp = RunSpec("significance", "synthetic", {"screens": ["dogs"], "n": 200}, seed=5)
    a = execute_spec(sp, lab)
    b = execute_spec(RunSpec("significance", "synthetic", {"screens": ["dogs"], "n": 200}, seed=5), lab)
    pd.testing.assert_frame_equal(a, b, check_exact=True)


# ------------------------------------------------------------------ process mode (spawn)
def test_process_mode_matches_sync_and_reports_progress_and_cancels(tmp_path):
    sync = fx.make_lab().backtest(["dogs", "graham_defensive"], rebalance_months=6)
    store = SQLiteStore(tmp_path / "runs.sqlite")
    with JobManager(store=store, workers=1, mode="process", labs=LABS) as m:
        job = m.submit(bt_spec())
        res = job.result(120)
        pd.testing.assert_frame_equal(res.table, sync.table, check_exact=True)
        pd.testing.assert_frame_equal(res.curves, sync.curves, check_exact=True)
        assert m.submit(bt_spec()).cache_hit                                     # store hit across the pool
        loop = m.submit(RunSpec("fx_loop", "synthetic", {"n": 300, "sleep": 0.01}))
        wait_for(lambda: loop.progress > 0.05, 60)
        assert loop.status == "running" and "step" in loop.message
        assert store.job_get(loop.id)["progress"] > 0                            # via the jobs table
        loop.cancel()
        assert loop.wait(30) and loop.status == "cancelled"
        assert store.get(loop.run_key).status == "cancelled"
        bad = m.submit(RunSpec("fx_fail", "synthetic", {}))
        assert bad.wait(30) and bad.status == "failed" and "boom" in bad.error


def test_process_mode_without_a_store_uses_a_manager_channel():
    with JobManager(workers=1, mode="process", labs=LABS) as m:
        job = m.submit(RunSpec("fx_loop", "synthetic", {"n": 300, "sleep": 0.01}))
        wait_for(lambda: job.progress > 0.05, 60)
        job.cancel()
        assert job.wait(30) and job.status == "cancelled"
        ok = m.submit(RunSpec("picks", "synthetic", {"screen": "dogs"}))
        assert "market_cap" in ok.result(60).columns
