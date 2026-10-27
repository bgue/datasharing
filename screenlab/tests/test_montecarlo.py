"""The montecarlo operation (DESIGN.md K): registration, determinism, shared paths, known-answer degenerate
base, horizons, flows, warnings, jobs (thread mode), REST and MCP."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from screenlab import ExitPolicy, ExitScreen, Lab, SQLiteStore, TrendBreak
from screenlab.dataset import Dataset
from screenlab.metrics import METRICS
from screenlab.montecarlo import BlockBootstrap
from screenlab.operations import OPERATIONS
from screenlab.ops.montecarlo import MonteCarloResult, to_monthly
from screenlab.realdata.bundled import price_only_fundamentals, price_screens, sp500_lab

N = 24


@pytest.fixture(scope="module")
def lab():
    return sp500_lab(n=5)


# --------------------------------------------------------------------------- a base whose answer is known
def constant_lab(name_rets=(0.01, 0.02, 0.03, 0.04), index_ret=0.005, months=180) -> Lab:
    """Every name and the index grow by a constant monthly return: no volatility, every resampled path identical."""
    dates = pd.date_range("1995-01-31", periods=months, freq="ME")
    t = np.arange(months)
    px = pd.DataFrame({f"S{i}": 100 * (1 + r) ** t for i, r in enumerate(name_rets)}, index=dates)
    px.index.name = "date"
    idx = pd.DataFrame({"IDX": 1000 * (1 + index_ret) ** t}, index=dates)
    ds = Dataset.from_frames(price_only_fundamentals(px), px, idx, name="constant", index_kinds={"IDX": "total_return"})
    return Lab.from_dataset(ds, screens=price_screens(3))


def mc(lab, **kw):
    kw.setdefault("n_paths", N)
    kw.setdefault("horizon_years", [2, 3])
    kw.setdefault("screens", ["momentum_12_1", "low_vol"])
    kw.setdefault("seed", 5)
    return lab.montecarlo(**kw)


# --------------------------------------------------------------------------- registration
def test_operation_is_registered_and_reachable_via_lab(lab):
    op = OPERATIONS["montecarlo"]
    assert op.long_running and op.builtin and hasattr(Lab, "montecarlo")
    assert all(s.doc for s in op.arg_specs())
    r = mc(lab)
    assert isinstance(r, MonteCarloResult)
    r2 = lab.run("montecarlo", screens=["momentum_12_1", "low_vol"], n_paths=N, horizon_years=[2, 3], seed=5)
    pd.testing.assert_frame_equal(r.table, r2.table)
    assert set(r.table.index.get_level_values("portfolio")) == {"momentum_12_1", "low_vol", "SP500"}
    assert set(r.table.index.get_level_values("horizon_years")) == {2.0, 3.0}
    assert set(r.table.columns) <= set(METRICS)                      # every output column is a registered metric
    assert r.fan.index[-1] == 3.0 and "momentum_12_1|p50" in r.fan and "SP500|p95" in r.fan
    assert len(r.per_path) == 3 * 2 * N


def test_family_counts_each_generator_configuration(tmp_path):
    store = SQLiteStore(tmp_path / "t.sqlite")
    lab = sp500_lab(n=5, store=store)
    lab.name = "sp500"
    kw = dict(screens=["low_vol"], n_paths=8, horizon_years=[2])
    lab.montecarlo(generator=BlockBootstrap(12), **kw)
    lab.montecarlo(generator=BlockBootstrap(6), **kw)
    lab.montecarlo(generator=BlockBootstrap(6), **kw)                 # repeat: same run key, not a new trial
    fam = lab.last_trials()["family"]
    assert fam.startswith("montecarlo|") and fam.endswith("|low_vol")
    assert store.trial_count(fam) == 2


# --------------------------------------------------------------------------- determinism, shared paths
def test_deterministic_by_seed(lab):
    a, b, c = mc(lab, seed=1), mc(lab, seed=1), mc(lab, seed=2)
    pd.testing.assert_frame_equal(a.table, b.table)
    pd.testing.assert_frame_equal(a.per_path, b.per_path)
    pd.testing.assert_frame_equal(a.fan, b.fan)
    assert not a.table.equals(c.table)


def test_index_and_portfolios_share_the_paths(lab):
    r = mc(lab, seed=9, horizon_years=[2])
    pp = r.per_path
    ic = pp[pp.portfolio != "SP500"].groupby("portfolio")["index_cagr"].apply(lambda s: s.to_numpy())
    assert np.array_equal(ic["momentum_12_1"], ic["low_vol"])                      # one index per path, shared
    own = pp[pp.portfolio == "SP500"].sort_values("path")
    np.testing.assert_allclose(own["cagr"], own["index_cagr"])                    # the index row is that index
    # and it is the generator's own path set: recompute the index CAGR from the PathSet
    base = to_monthly(lab.market.dataset)
    ps = BlockBootstrap(12).paths(base, N, 2 + 1 + 0.1, seed=9)
    lv = ps.index_levels()[:, :, ps.index_names.index("SP500")]
    want = (lv[:, 12 + 24] / lv[:, 12]) ** (1 / 2) - 1
    np.testing.assert_allclose(own["cagr"].to_numpy(), want)
    # P(trail) is exactly the share of paths with portfolio CAGR below the index CAGR
    for name in ("momentum_12_1", "low_vol"):
        p = pp[pp.portfolio == name]
        assert r.table.loc[(name, 2.0), "P(trail)"] == pytest.approx((p["cagr"] < p["index_cagr"]).mean())


def test_horizons_are_prefixes_of_one_set_of_paths(lab):
    r = mc(lab, horizon_years=[2, 3], screens=["low_vol"])
    for name in ("low_vol", "SP500"):
        at2 = r.fan[f"{name}|p50"].loc[2.0]                       # fan curve = the longest horizon's paths cut at year 2
        assert r.table.loc[(name, 2.0), "TermWealth_p50"] == pytest.approx(at2)
        assert r.table.loc[(name, 3.0), "TermWealth_p50"] == pytest.approx(r.fan[f"{name}|p50"].iloc[-1])


def test_generators_and_serialized_form(lab):
    a = mc(lab, generator={"type": "StationaryBootstrap", "mean_block_months": 6}, screens=["low_vol"])
    b = mc(lab, generator={"__t": "StationaryBootstrap", "mean_block_months": 6}, screens=["low_vol"])
    pd.testing.assert_frame_equal(a.table, b.table)
    assert a.generator["__t"] == "StationaryBootstrap"
    for g in ({"type": "IIDBootstrap"}, {"type": "RegimeBootstrap", "block_months": 6}):
        assert len(mc(lab, generator=g, screens=["low_vol"], n_paths=8, horizon_years=[2]).table) == 2


# --------------------------------------------------------------------------- known answers
def test_zero_volatility_base_every_path_identical():
    lab = constant_lab()
    r = mc(lab, screens=["momentum_12_1"], horizon_years=[5], target_wealth=1e30)
    row, idx = r.table.loc[("momentum_12_1", 5.0)], r.table.loc[("IDX", 5.0)]
    for lo, hi in (("p5", "p95"), ("p25", "p75")):
        assert row[f"CAGR_{lo}"] == pytest.approx(row[f"CAGR_{hi}"], rel=1e-12) == pytest.approx(row["CAGR_p50"], rel=1e-12)
        assert row[f"TermWealth_{lo}"] == pytest.approx(row[f"TermWealth_{hi}"], rel=1e-12)
    pp = r.per_path[r.per_path.portfolio == "momentum_12_1"]
    assert pp["cagr"].max() - pp["cagr"].min() < 1e-12                          # every path identical
    assert idx["CAGR_p50"] == pytest.approx(1.005 ** 12 - 1)
    assert idx["TermWealth_p50"] == pytest.approx(100_000 * 1.005 ** 60)
    assert row["CAGR_p50"] > 0.4 > idx["CAGR_p50"]                                  # holds the top three growers
    assert row["P(trail)"] == 0.0 and row["ExcessCAGR_med"] > 0.3
    assert row["P(MaxDD<-30%)"] == 0.0 and row["P(MaxDD<-50%)"] == 0.0
    assert row["RecoverYrs_med"] == 0.0 and row["P(unrecovered)"] == 0.0
    assert row["P(shortfall)"] == 1.0                                                 # target far above any outcome
    assert np.isnan(idx["P(trail)"])


def test_zero_volatility_base_portfolio_behind_the_index():
    r = mc(constant_lab(index_ret=0.06), screens=["momentum_12_1"], horizon_years=[3, 5])
    assert (r.table.xs("momentum_12_1", level="portfolio")["P(trail)"] == 1.0).all()
    assert (r.table.xs("momentum_12_1", level="portfolio")["ExcessCAGR_med"] < 0).all()


def test_contributions_and_withdrawals_known_answer():
    lab = constant_lab()
    r = mc(lab, screens=["momentum_12_1"], horizon_years=[5], contributions=12_000, target_wealth=1.0)
    want = 100_000.0
    for _ in range(60):
        want = want * 1.005 + 1000.0
    assert r.table.loc[("IDX", 5.0), "TermWealth_p50"] == pytest.approx(want)
    assert r.table.loc[("IDX", 5.0), "CAGR_p50"] == pytest.approx(1.005 ** 12 - 1)     # time-weighted: flows excluded
    w = mc(lab, screens=["momentum_12_1"], horizon_years=[5], withdrawals=1_000_000)   # ruin: wealth stops at zero
    assert w.table.loc[("IDX", 5.0), "TermWealth_p95"] == 0.0
    assert r.table.loc[("IDX", 5.0), "P(shortfall)"] == 0.0


def test_exit_policy_with_and_without(lab):
    pol = ExitPolicy(ExitScreen([TrendBreak("SP500")], threshold=0.8, hysteresis=0.1, check="W-FRI", min_hold_days=10))
    r = mc(lab, screens=["momentum_12_1"], exit=pol, with_and_without=True, n_paths=60, horizon_years=[5],
           rebalance_months=1, cost_bps=25)
    assert list(r.table.index.get_level_values("portfolio").unique()) == ["momentum_12_1", "momentum_12_1+exit", "SP500"]
    a, b = r.table.loc[("momentum_12_1", 5.0)], r.table.loc[("momentum_12_1+exit", 5.0)]
    assert b["P(MaxDD<-30%)"] <= a["P(MaxDD<-30%)"]
    with pytest.raises(ValueError, match="steps must be"):
        mc(lab, steps="weekly")


# --------------------------------------------------------------------------- warnings and printing
def test_data_card_and_warnings_are_carried(lab):
    r = mc(lab, horizon_years=[2])
    assert r.data_card.survivorship_score > 0.02 and r.data_card.benchmark_kind == "price"
    text = " ".join(r.warnings)
    assert "price index" in text and "survivorship score" in text and "inherited" in text
    s = str(r)
    assert r.data_card.line() in s and all(w in s for w in r.warnings)
    assert "BlockBootstrap" in s and "seed 5" in s
    j = OPERATIONS["montecarlo"].to_json(r, lab=lab)
    assert j["warnings"] == r.warnings
    assert j["table"]["columns"][:2] == ["portfolio", "horizon_years"] and j["fan"]["columns"][0] == "years"


def test_metrics_rows(lab):
    r = mc(lab, horizon_years=[2], screens=["low_vol"])
    rows = OPERATIONS["montecarlo"].metrics(r)
    labels = {x[0] for x in rows}
    assert labels == {"low_vol@2y", "SP500@2y"}
    assert {"CAGR_p50", "P(trail)", "P(MaxDD<-30%)", "TermWealth_p5"} <= {x[1] for x in rows}
    assert all(m in METRICS for _, m, _ in rows)
    ex = OPERATIONS["montecarlo"].extract(r)
    assert "table" in ex.summary and "fan" in ex.bulk


# --------------------------------------------------------------------------- jobs, REST, MCP
JOB = dict(screens=["low_vol"], n_paths=10, horizon_years=[2], seed=3)


def test_runs_as_a_job_in_thread_mode(tmp_path):
    store = SQLiteStore(tmp_path / "t.sqlite")
    lab = sp500_lab(n=5, store=store)
    lab.name = "sp500"
    job = lab.submit("montecarlo", **JOB)
    res = job.result(timeout=300)
    assert job.status == "done" and isinstance(res, MonteCarloResult)
    found = store.find(kind="montecarlo")
    assert {"low_vol@2y", "SP500@2y"} <= set(found["row_label"]) and "CAGR_p50" in found.columns
    again = lab.submit("montecarlo", **JOB)
    assert again.cache_hit and again.run_key == job.run_key
    lab.jobs.shutdown()


@pytest.fixture(scope="module")
def reg():
    from screenlab.server.registry import LabRegistry
    return LabRegistry({"sp500": lambda: sp500_lab(n=5)}, default="sp500", mode="thread")


def test_rest_and_mcp(reg):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from screenlab.server.mcp_server import build_tools
    from screenlab.server.rest import create_app
    c = TestClient(create_app(reg))
    body = {"screens": ["low_vol"], "n_paths": 10, "horizon_years": [2], "seed": 3,
            "generator": {"type": "StationaryBootstrap", "mean_block_months": 12}}
    r = c.post("/labs/sp500/montecarlo", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["generator"]["__t"] == "StationaryBootstrap" and j["n_paths"] == 10 and "trials_in_family" in j
    assert j["table"]["columns"][:2] == ["portfolio", "horizon_years"] and len(j["table"]["rows"]) == 2
    assert any("survivorship" in w for w in j["warnings"]) and j["data_card"]["benchmark_kind"] == "price"
    assert c.post("/labs/sp500/montecarlo", json={**body, "bogus": 1}).status_code == 400
    assert c.post("/labs/sp500/montecarlo", json={**body, "n_paths": 0}).status_code == 400
    assert c.post("/labs/sp500/montecarlo", json={**body, "generator": {"type": "Nope"}}).status_code in (400, 422)
    spec = c.get("/openapi.json").json()
    assert "/labs/{lab}/montecarlo" in spec["paths"]
    assert any(o["name"] == "montecarlo" and o["long_running"] for o in c.get("/operations").json()["operations"])
    jj = c.post("/jobs", json={"kind": "montecarlo", "args": body}).json()
    for _ in range(600):
        st = c.get(f"/jobs/{jj['job_id']}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    assert c.get(f"/jobs/{jj['job_id']}/result").json()["table"]["rows"] == j["table"]["rows"]
    tools = build_tools(reg)
    out = tools["montecarlo"](**body)
    assert out["table"]["rows"] == j["table"]["rows"]
    reg.job_manager.shutdown()
