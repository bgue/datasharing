"""The `plan` and `plan_montecarlo` operations: REST route, MCP tool, job kind and store, all from the registry, on the
synthetic lab. # PC:

    python -m pytest screenlab/tests/test_plan_ops.py -q
"""
from __future__ import annotations

import json
import time

import pytest

from screenlab import Lab
from screenlab.errors import ServerError
from screenlab.operations import OPERATIONS

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

BODY = {"screen": "dogs", "start": "2002-01-31", "end": "2008-12-31", "initial": 10000, "periodic": 500, "growth": "cpi",
        "extra": [{"date": "2004-03-01", "amount": 5000, "note": "bonus"}], "withdrawals": 800, "withdraw_from": "2007-01-01",
        "goal": 90000, "tax": {"model": "canadian", "account": "tfsa", "province": "AB"}, "config": {"rebalance_months": 12}}
MC = {**BODY, "n_paths": 30, "seed": 5, "horizon_end": "2011-12-31"}


def make_lab():
    return Lab.synthetic(freq="ME", n_stocks=60, years=10, seed=3)


@pytest.fixture(scope="module")
def reg():
    from screenlab.server.registry import LabRegistry
    return LabRegistry({"synthetic": make_lab}, default="synthetic")


@pytest.fixture(scope="module")
def client(reg):
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    return TestClient(create_app(reg))


@pytest.fixture(scope="module")
def plan_json(client):
    r = client.post("/labs/synthetic/plan", json=BODY)
    assert r.status_code == 200, r.text
    return r.json()


def test_operations_are_registered_and_documented(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/labs/{lab}/plan" in paths and "/labs/{lab}/plan_montecarlo" in paths
    ops = {o["name"]: o for o in client.get("/operations").json()["operations"]}
    assert ops["plan_montecarlo"]["long_running"] and not ops["plan"]["long_running"]
    for n in ("plan", "plan_montecarlo"):
        assert OPERATIONS[n].builtin and all(s.doc for s in OPERATIONS[n].arg_specs())
    from screenlab.plan import PlanSpec
    import dataclasses
    fields = {f.name for f in dataclasses.fields(PlanSpec)}
    assert fields - {"config"} <= {s.name for s in OPERATIONS["plan"].arg_specs()}      # every PlanSpec field is a request field


def test_rest_plan_shape(plan_json):
    j = plan_json
    assert j["spec"]["screen"] == "dogs" and j["spec"]["account"] == "tfsa" and j["spec"]["extra"][0]["amount"] == 5000
    m = j["metrics"]
    assert m["columns"][0] == "row" and [r[0] for r in m["rows"]] == ["strategy", "index", "difference"]
    cols = m["columns"]
    for c in ("Contributed", "FinalAfterTax", "XIRR", "XIRRAfterTax", "XIRRReal", "TWR", "Alpha", "Beta", "Sharpe", "YearsToGoal"):
        assert c in cols
    s = j["series"]
    assert s["columns"][:3] == ["date", "contributed", "strategy_pre"] and len(s["rows"]) > 50 and not j["daily"]
    assert {"kind", "executed", "amount"} <= set(j["flows"]["columns"]) and len(j["flows"]["rows"]) > 60
    assert {"year", "strategy.tax_paid", "index.tax_paid"} <= set(j["tax"]["columns"])
    assert j["goal"] is not None and "final_vs_goal" in j["goal"]
    assert j["card"]["line"].startswith("Data:") and any("CPI" in w for w in j["card"]["warnings"])
    assert j["data_card"]["name"] == "synthetic"


def test_series_is_month_ends_unless_daily(client):
    lab = Lab.synthetic(freq="B", n_stocks=30, years=3, seed=1)
    from screenlab.ops.plan import series_json
    res = lab.plan("dogs", "2001-03-01", end="2002-12-31", initial=1000, periodic=100)
    monthly, daily = series_json(res.series, False), series_json(res.series, True)
    assert len(daily["rows"]) == len(res.series) and 10 < len(monthly["rows"]) < len(daily["rows"]) / 10
    assert monthly["rows"][0][0] <= monthly["rows"][-1][0]


def test_mcp_tool_and_job_come_from_the_registry(reg, client, plan_json):
    pytest.importorskip("mcp")
    from screenlab import jobs
    from screenlab.server.mcp_server import build_tools
    tools = build_tools(reg)
    assert "plan" in tools and "plan_montecarlo" in tools
    out = tools["plan"]("dogs", "2002-01-31", end="2008-12-31", initial=10000, periodic=500, growth="cpi",
                        extra=BODY["extra"], withdrawals=800, withdraw_from="2007-01-01", goal=90000, tax=BODY["tax"],
                        config=BODY["config"])
    assert out["metrics"] == plan_json["metrics"] and out["series"] == plan_json["series"]
    assert {"plan", "plan_montecarlo"} <= set(jobs.KINDS)
    j = client.post("/jobs", json={"kind": "plan", "lab": "synthetic", "args": BODY}).json()
    t = time.time()
    while client.get(f"/jobs/{j['job_id']}").json()["status"] not in ("done", "failed", "cancelled"):
        assert time.time() - t < 120
        time.sleep(0.05)
    st = client.get(f"/jobs/{j['job_id']}").json()
    assert st["status"] == "done", st.get("error")
    assert client.get(f"/jobs/{j['job_id']}/result").json()["metrics"] == plan_json["metrics"]


def test_plan_is_deterministic_and_stored(reg, client, plan_json):
    again = client.post("/labs/synthetic/plan", json=BODY).json()
    assert again["metrics"] == plan_json["metrics"] and again["tax"] == plan_json["tax"]
    # the store extractor keeps the metrics rows
    lab = reg.get("synthetic") if hasattr(reg, "get") else make_lab()
    op = OPERATIONS["plan"]
    res = lab.plan("dogs", "2002-01-31", end="2006-12-31", initial=10000, periodic=500, rebalance_months=12)
    rows = op.metrics(res)
    assert ("strategy", "XIRR", pytest.approx(res.metrics.loc["strategy", "XIRR"])) in [(a, b, c) for a, b, c in rows if b == "XIRR"]
    assert {r[1] for r in rows} >= {"XIRR", "FinalAfterTax", "Alpha", "Sharpe"}


def test_rest_plan_montecarlo(client):
    r = client.post("/labs/synthetic/plan_montecarlo", json=MC)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["n_paths"] == 30 and j["seed"] == 5
    q = j["quantiles"]
    assert q["columns"][0] == "pct" and [x[0] for x in q["rows"]] == [5, 25, 50, 75, 95]
    assert 0 <= j["p_beat_index"] <= 1 and j["p_shortfall"] is not None and j["goal"] == 90000
    assert j["sequence"]["rows"] and j["paired"]["quantiles"].keys() == {"5", "25", "50", "75", "95"}
    assert sum(j["histograms"]["strategy_after_tax"]["counts"]) == 30 and j["fan"]["rows"]
    assert j["card"]["notes"] and j["card"]["line"].startswith("Data:")
    assert client.post("/labs/synthetic/plan_montecarlo", json=MC).json() == j                   # determinism
    other = client.post("/labs/synthetic/plan_montecarlo", json={**MC, "seed": 6}).json()
    assert other["quantiles"] != j["quantiles"]


@pytest.mark.parametrize("patch,word", [
    ({"screen": "nope"}, "screen"),
    ({"start": "not-a-date"}, "start"),
    ({"end": "2001-01-01"}, "end"),
    ({"frequency": "weekly"}, "frequency"),
    ({"growth": "inflation"}, "growth"),
    ({"initial": -5}, "initial"),
    ({"withdrawals": 100, "withdraw_from": None}, "withdraw_from"),
    ({"withdraw_from": "2001-01-01"}, "withdraw_from"),
    ({"extra": [{"date": "2004-03-01"}]}, "amount"),
    ({"extra": [{"date": "x", "amount": 5}]}, "date"),
    ({"extra": [{"date": "2004-03-01", "amount": 5, "bogus": 1}]}, "bogus"),
    ({"cash_timing": "later"}, "cash_timing"),
    ({"goal": 0}, "goal"),
    ({"index_mer": 0.9}, "index_mer"),
    ({"risk_free": "libor"}, "risk_free"),
    ({"tax": {"model": "canadian", "account": "isa"}}, "account"),
    ({"config": {"tax": {"model": "canadian", "account": "tfsa", "province": "AB"}}}, "not both"),
    ({"config": {"nonsense": 1}}, "nonsense"),
    ({"surprise": 1}, "surprise"),
    ({"initial": 0, "periodic": 0, "extra": None}, "contribution"),
    ({"phases": [{"years_from": 0, "years_to": 2, "amount": -1}]}, "phases[0].amount"),                          # V2:
    ({"phases": [{"years_from": 0, "years_to": 2, "amount": 1}, {"years_from": 1, "years_to": 3, "amount": 1}]}, "phases[1]"),
    ({"phases": [{"years_from": 3, "years_to": 4, "amount": 1}, {"years_from": 0, "years_to": 1, "amount": 1}]}, "ascending"),
    ({"phases": [{"years_from": 0, "years_to": 0, "amount": 1}]}, "phases[0].years_to"),
    ({"phases": [{"years_from": 0, "amount": 1, "bogus": 2}]}, "bogus"),
    ({"phases": [{"years_from": 0}]}, "amount"),
    ({"phase_growth": "real"}, "phase_growth"),
])
def test_bad_fields_are_400s_that_name_the_field(client, patch, word):
    body = {**BODY, **patch}
    for route in ("plan", "plan_montecarlo"):
        r = client.post(f"/labs/synthetic/{route}", json=body)
        assert r.status_code == 400, (patch, r.status_code, r.text)
        assert word in r.json()["error"], (patch, r.json())


def test_mc_specific_400s(client):
    for patch, word in (({"n_paths": 1}, "n_paths"), ({"generator": "magic"}, "generator"),
                        ({"horizon_end": "2001-01-01"}, "horizon_end")):
        r = client.post("/labs/synthetic/plan_montecarlo", json={**MC, **patch})
        assert r.status_code == 400 and word in r.json()["error"], (patch, r.text)
    with pytest.raises(ServerError):
        OPERATIONS["plan"].precheck({"screen": "dogs", "start": "2002-01-31", "withdrawals": 5})


def test_lab_methods_take_live_objects():
    lab = make_lab()
    from screenlab.plan import CashFlow
    res = lab.plan("dogs", "2002-01-31", end="2006-12-31", initial=1000, periodic=100, extra=[CashFlow("2004-03-01", 500)],
                   rebalance_months=12)
    assert res.flows["kind"].tolist().count("extra") == 1 and res.spec.config.rebalance_months == 12
    mc = lab.plan_montecarlo("dogs", "2002-01-31", end="2006-12-31", initial=1000, periodic=100, n_paths=10, seed=1)
    assert len(mc.terminal) == 10


def test_one_monte_carlo_implementation_and_the_tax_basis_option(client):
    import screenlab.ops.plan as ops_plan
    assert not hasattr(ops_plan, "monte_carlo") and not hasattr(ops_plan, "split_tax")          # the operation calls plan_mc
    taxable = {**BODY, "tax": {"model": "canadian", "account": "taxable", "province": "AB"}}
    a = client.post("/labs/synthetic/plan", json=taxable).json()
    b = client.post("/labs/synthetic/plan", json={**taxable, "tax_from_outside": True}).json()
    assert a["spec"]["tax_from_outside"] is False and b["spec"]["tax_from_outside"] is True
    assert a["metrics"] != b["metrics"]                                                         # the two bases differ
    mc = client.post("/labs/synthetic/plan_montecarlo", json={**taxable, "n_paths": 6, "seed": 2, "generator": "history",
                                                              "horizon_end": "2008-12-31"})
    assert mc.status_code == 200, mc.text


def test_phases_request_reaches_the_flows(client):  # V2:
    body = {**BODY, "periodic": 0, "growth": 0.0, "initial": 0, "withdrawals": 0, "withdraw_from": None, "extra": None,
            "phases": [{"years_from": 0, "years_to": 1, "amount": 100, "note": "a"}, {"years_from": 2, "years_to": 3, "amount": 300, "note": "b"}]}
    r = client.post("/labs/synthetic/plan", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["spec"]["phases"][1] == {"years_from": 2.0, "years_to": 3.0, "amount": 300.0, "note": "b"}
    cols = j["flows"]["columns"]
    rows = [dict(zip(cols, row)) for row in j["flows"]["rows"]]
    per = [x for x in rows if x["kind"] == "periodic"]
    assert sorted({(x["amount"], x["note"]) for x in per}) == [(100.0, "a"), (300.0, "b")]
    assert sum(x["note"] == "a" for x in per) == 12 and sum(x["note"] == "b" for x in per) == 12
