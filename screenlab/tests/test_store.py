"""RunStore: NullStore, SQLiteStore persistence, find / compare / rerun, trials, retention (DESIGN.md I3, I4)."""
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab
from screenlab import runspec as R
from screenlab.jobs import execute_spec
from screenlab.store import NullStore, SQLiteStore, StoredResult, extract, long_rows, wide

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(tmp_path / "sub" / "runs.sqlite")
    yield s
    s.close()


def run(lab, store, level="metrics", label=None, **args):
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs", "graham_defensive"], **args}, label=label)
    rep = execute_spec(spec, lab)
    did = R.dataset_id(lab.market)
    key = store.record(spec, rep, spec.run_key(did), did, level)
    return spec, rep, key


def test_null_store_writes_no_files(tmp_path, lab, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ns = NullStore()
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs"]})
    rep = execute_spec(spec, lab)
    assert ns.record(spec, rep, "k", "d") == "k"
    ns.begin_run(spec, "k"); ns.finish_run("k", rep); ns.fail_run("k", "x"); ns.put_dataset("d", {})
    assert ns.get("k") is None and ns.cached("k") is None and ns.find().empty
    assert ns.job_get("j") is None and ns.job_list() == []
    assert list(tmp_path.iterdir()) == []
    assert ns.record_trial("s", "k1", "fam") and not ns.record_trial("s", "k1", "fam")
    assert ns.trial_count("fam") == 1                                    # in memory only


def test_metrics_roundtrip_matches_report_table(lab, store):
    spec, rep, key = run(lab, store)
    r = store.get(key)
    assert isinstance(r, StoredResult) and r.status == "done" and r.kind == "backtest"
    assert r.spec.kind == "backtest" and r.persist_level == "metrics" and r.persistable
    num = rep.table.select_dtypes("number")
    pd.testing.assert_frame_equal(r.metrics, num, check_exact=True, check_names=False)
    assert store.artifact_names(key) == []                                # nothing bulky at level "metrics"
    with pytest.raises(KeyError):
        r.artifact("curves")


def test_full_level_keeps_curves(lab, store):
    spec, rep, key = run(lab, store, level="full")
    assert "curves" in store.artifact_names(key)
    c = store.get(key).artifact("curves")
    pd.testing.assert_frame_equal(c, rep.curves, check_exact=False, rtol=1e-12, check_freq=False, check_names=False)
    assert isinstance(c.index, pd.DatetimeIndex)


def test_inputs_level_is_audit_only_not_a_cache_hit(lab, store):
    spec, rep, key = run(lab, store, level="inputs")
    r = store.get(key)
    assert r.persist_level == "inputs" and r.metrics.empty and r.spec.args == spec.serialized_args()
    assert store.cached(key) is None


def test_level_none_records_nothing(lab, store):
    spec, rep, key = run(lab, store, level="none")
    assert store.get(key) is None


def test_store_survives_restart(lab, tmp_path):
    path = tmp_path / "runs.sqlite"
    s1 = SQLiteStore(path)
    _, rep1, k1 = run(lab, s1, label="annual")
    _, rep2, k2 = run(lab, s1, label="semi", rebalance_months=6)
    s1.close()
    s2 = SQLiteStore(path)                                                  # "restart"
    assert s2.get(k1).label == "annual" and s2.cached(k2) is not None
    cmp = s2.compare([k1, k2], metrics=["CAGR", "Excess/yr"])
    assert list(cmp.columns) == ["annual", "semi"]
    assert cmp.index.names == ["row_label", "metric"]
    assert cmp.loc[("dogs", "CAGR"), "annual"] == pytest.approx(rep1.table.loc["dogs", "CAGR"])
    assert cmp.loc[("dogs", "CAGR"), "semi"] == pytest.approx(rep2.table.loc["dogs", "CAGR"])
    assert not np.isclose(cmp.loc[("dogs", "CAGR"), "annual"], cmp.loc[("dogs", "CAGR"), "semi"])
    s2.close()


def test_find_filters_on_metrics_and_run_fields(lab, store):
    _, rep1, k1 = run(lab, store, label="a")
    _, rep2, k2 = run(lab, store, label="b", rebalance_months=6)
    allrows = store.find(kind="backtest")
    assert set(allrows["run_key"]) == {k1, k2} and {"CAGR", "Excess/yr", "row_label", "label"} <= set(allrows.columns)
    assert len(allrows) == 2 * len(rep1.table)
    best = max(rep1.table["CAGR"].iloc[1:].max(), rep2.table["CAGR"].iloc[1:].max())
    hot = store.find(where={"CAGR": (">", best - 1e-9)}, row_label=None)
    hot = hot[hot["row_label"] != "SYN_CAPWT"]
    assert len(hot) == 1 and hot["CAGR"].iloc[0] == pytest.approx(best)
    both = store.find(where={"Excess/yr": (">", -1.0), "CAGR": ("<", 1.0)})
    assert len(both) == 4 and (both["Excess/yr"] > -1.0).all()          # benchmark rows have no Excess/yr
    none = store.find(where={"Excess/yr": (">", 5.0)})
    assert none.empty
    some = store.find(where={"Excess/yr": ("<=", 0.0)}, screen="graham_defensive")
    assert len(some) == 4 and (some["Excess/yr"] <= 0).all()
    assert set(store.find(label="b")["run_key"]) == {k2}
    assert store.find(screen="no_such_screen").empty
    assert set(store.find(dataset="synthetic")["run_key"]) == {k1, k2}
    assert store.find(since=pd.Timestamp.now("UTC") + pd.Timedelta(days=1)).empty
    assert len(store.find(row_label="dogs")) == 2
    assert len(store.find(where={"MaxDD": ("between", (-1.0, 0.0))}, row_label="dogs")) == 2
    with pytest.raises(ValueError):
        store.find(where={"CAGR": ("~", 1)})


def test_rerun_reproduces_stored_metrics(lab, store):
    spec, rep, key = run(lab, store, rebalance_months=6)
    did = R.dataset_id(lab.market)
    out = store.rerun(key, lambda sp: execute_spec(sp, lab), dataset_id=did)
    assert out.match and out.max_abs_diff == 0.0 and out.dataset_changed is False and out.code_changed is False
    # tamper with a stored number: the rerun now disagrees
    store._db().execute("UPDATE metrics SET value=value+0.01 WHERE run_key=? AND row_label='dogs' AND metric='CAGR'", (key,))
    bad = store.rerun(key, lambda sp: execute_spec(sp, lab), dataset_id=did)
    assert not bad.match and bad.max_abs_diff == pytest.approx(0.01)
    other = store.rerun(key, lambda sp: execute_spec(sp, lab), dataset_id="different")
    assert other.dataset_changed is True


def test_unpersistable_recorded_without_key_and_not_rerunnable(lab, store):
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs"], "cost_bps": lambda s: pd.Series(10.0, index=s.index)})
    assert not spec.persistable
    rep = execute_spec(spec, lab)
    key = store.record(spec, rep, None, R.dataset_id(lab.market))
    assert key.startswith("unpersistable:")
    r = store.get(key)
    assert not r.persistable and r.metrics.loc["dogs", "CAGR"] == pytest.approx(rep.table.loc["dogs", "CAGR"])
    assert store.cached(key) is None
    with pytest.raises(ValueError, match="not rerunnable"):
        store.rerun(key, lambda sp: rep)
    assert store.record(spec, rep, None, "d") != key                     # a fresh pseudo key per call


def test_failed_run_is_not_a_cache_hit(lab, store):
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs"]})
    key = store.begin_run(spec, "k1", "d")
    assert store.get("k1").status == "running" and store.cached("k1") is None
    store.fail_run("k1", "ValueError: boom")
    r = store.get("k1")
    assert r.status == "failed" and r.error == "ValueError: boom" and store.cached("k1") is None
    assert store.find().empty and len(store.find(status="failed")) == 0       # no metrics rows


def test_trial_counter_rises_only_on_distinct_run_keys(store):
    fam = "backtest|sp500|dogs"
    assert store.trial_count(fam) == 0
    assert store.record_trial("s1", "k1", fam) is True
    assert store.record_trial("s1", "k1", fam) is False                  # same key, same session
    assert store.trial_count(fam) == 1
    store.record_trial("s1", "k2", fam)
    store.record_trial("s1", "k3", "other|family")
    assert store.trial_count(fam) == 2 and store.trial_count("other|family") == 1
    store.record_trial("s2", "k2", fam); store.record_trial("s2", "k4", fam)
    assert store.trial_count(fam, "s1") == 2 and store.trial_count(fam, "s2") == 2
    assert store.trial_count(fam) == 3                                   # distinct keys over all sessions


def test_retention_deletes_oldest_first(lab, store):
    keys = []
    for m in (3, 4, 6, 12):
        _, _, k = run(lab, store, rebalance_months=m)
        keys.append(k)
        time.sleep(0.01)
    assert store.prune(keep_last=10) == []
    gone = store.prune(keep_last=2)
    assert gone == keys[:2]
    assert store.get(keys[0]) is None and store.get(keys[3]) is not None
    assert not store._db().execute("SELECT 1 FROM metrics WHERE run_key=?", (keys[0],)).fetchall()
    assert store.prune(older_than=pd.Timedelta(hours=1).to_pytimedelta()) == []
    assert store.prune(older_than=0) == [keys[2], keys[3]]
    assert store.find().empty


def test_delete_export_and_datasets(lab, store, tmp_path):
    _, _, k1 = run(lab, store, label="one")
    _, _, k2 = run(lab, store, label="two", rebalance_months=6)
    store.put_dataset(R.dataset_id(lab.market), R.dataset_manifest(lab.market, "synthetic", lab))
    ds = store.datasets()
    assert ds["name"].tolist() == ["synthetic"] and ds["n_names"].iloc[0] == 150 and ds["benchmark"].iloc[0] == "SYN_CAPWT"
    assert store.dataset(R.dataset_id(lab.market))["span"][0].startswith("2000")
    js = json.loads(open(store.export(tmp_path / "out.json")).read())
    assert {r["run_key"] for r in js} == {k1, k2} and js[0]["metrics"]["dogs"]["CAGR"] is not None
    sub = SQLiteStore(store.export(tmp_path / "sub.sqlite", [k1]))
    assert sub.get(k1) is not None and sub.get(k2) is None
    sub.close()
    store.export(tmp_path / "out.csv")
    assert len(pd.read_csv(tmp_path / "out.csv")) == len(store.find())
    assert store.delete(k1) and not store.delete(k1)
    assert store.get(k1) is None and store.get(k2) is not None


def test_extract_other_result_shapes(lab):
    tr = lab.timing("dogs", stride=6)
    ex = extract(tr)
    assert set(ex.summary) == {"distribution"} and {"offsets", "curves", "tranche"} <= set(ex.bulk)
    rows = long_rows(ex)
    assert {r[0] for r in rows} >= {"CAGR", "Excess/yr"} and {r[1] for r in rows} >= {"mean", "min", "max"}
    sw = lab.sweep("dogs", {"dogs.n": [5, 10], "cost_bps": [10, 50]})
    w = wide(long_rows(extract(sw)))
    assert len(w) == 4 and "dogs.n" in w.columns
    rep, funnel = lab.graham("defensive")
    ex = extract((rep, funnel))
    assert "part1" in ex.summary or "part0" in ex.summary
    assert extract(None).summary == {}


def test_jobs_table_api(store):
    store.job_put("j1", "k", "queued")
    store.job_update("j1", status="running", progress=0.4, message="halfway")
    j = store.job_get("j1")
    assert (j["status"], j["progress"], j["message"], j["cancel_requested"]) == ("running", 0.4, "halfway", 0)
    assert not store.job_cancel_requested("j1")
    store.job_request_cancel("j1")
    assert store.job_cancel_requested("j1")
    assert [x["job_id"] for x in store.job_list("running")] == ["j1"] and store.job_list("done") == []
    assert store.job_get("nope") is None


def test_pickles_by_path_and_is_safe_across_processes(lab, store, tmp_path):
    import pickle
    s2 = pickle.loads(pickle.dumps(store))
    assert s2.path == store.path
    code = (
        "import sys, time\n"
        "import pandas as pd\n"
        "from screenlab import runspec as R\n"
        "from screenlab.store import SQLiteStore\n"
        "s = SQLiteStore(sys.argv[1]); w = sys.argv[2]\n"
        "for i in range(25):\n"
        "    spec = R.RunSpec('x', 'd', {'i': i, 'w': w})\n"
        "    k = s.begin_run(spec, f'{w}-{i}', 'ds')\n"
        "    s.finish_run(k, pd.DataFrame({'v': [float(i)]}, index=['r']))\n"
        "    s.record_trial('sess', k, 'fam')\n")
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [subprocess.Popen([sys.executable, "-c", code, store.path, w], env=env, stderr=subprocess.PIPE)
             for w in "abc"]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()
    assert len(store.find(kind="x")) == 75 and store.trial_count("fam") == 75
