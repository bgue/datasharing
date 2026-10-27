"""Service-layer tests: REST through TestClient, MCP tool functions in-process (no network, no transport).

    python -m pytest screenlab/tests/test_server.py -q
"""
from __future__ import annotations

import json
import math

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient

from screenlab import Lab
from screenlab.server import registry as R
from screenlab.server.mcp_server import build_tools
from screenlab.server.registry import LabRegistry
from screenlab.server.rest import create_app


@pytest.fixture(scope="module")
def reg():
    return LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)},
                       default="synthetic")


@pytest.fixture(scope="module")
def client(reg):
    return TestClient(create_app(reg))


SPEC = {"name": "cheap", "n": 8, "universe": {"top": 100}, "filters": ["pe < 20", "div_yield > 0.01"],
        "rank": {"div_yield": "desc"}}


def rows(t):
    return [dict(zip(t["columns"], r)) for r in t["rows"]]


def test_serializers():
    import numpy as np
    import pandas as pd
    df = pd.DataFrame({"a": [1.0, np.nan, np.inf], "t": pd.to_datetime(["2020-01-01", None, "2020-03-01"])},
                      index=pd.Index(["x", "y", "z"], name="k"))
    t = R.table(df)
    assert t["columns"] == ["k", "a", "t"]
    assert t["rows"][1] == ["y", None, None] and t["rows"][2][1] is None and t["rows"][0][2] == "2020-01-01"
    json.dumps(t)
    s = pd.Series(np.arange(1000.0), index=pd.date_range("2000-01-01", periods=1000))
    c = R.curve(s, 100)
    assert len(c) <= 100 and c[0][0] == "2000-01-01" and c[-1][1] == 999.0
    assert len(R.curve(s.iloc[:10], 100)) == 10


def test_labs_and_screens(client):
    r = client.get("/labs").json()
    assert r["default"] == "synthetic" and r["labs"][0]["name"] == "synthetic"
    sc = client.get("/labs/synthetic/screens").json()
    assert "dogs" in [s["name"] for s in sc["screens"]]
    bad = client.get("/labs/nope/screens")
    assert bad.status_code == 404 and "error" in bad.json()


def test_define_and_picks(client):
    r = client.post("/labs/synthetic/screens", json=SPEC)
    assert r.status_code == 200 and r.json()["name"] == "cheap" and "cheap" in r.json()["screens"]
    p = client.get("/labs/synthetic/picks/cheap").json()
    assert p["columns"][0] == "ticker" and 0 < len(p["rows"]) <= 8
    pa = client.get("/labs/synthetic/picks/dogs", params={"asof": "2005-06-30"})
    assert pa.status_code == 200 and pa.json()["asof"] == "2005-06-30"
    assert client.get("/labs/synthetic/picks/missing_screen").status_code in (400, 404)
    assert client.post("/labs/synthetic/screens", json={"nonsense": 1}).status_code == 400


def test_backtest(client):
    r = client.post("/labs/synthetic/backtest", json={
        "screens": ["dogs", "cheap"], "config": {"rebalance_months": 6, "cost_bps": 20}, "max_points": 50})
    assert r.status_code == 200
    j = r.json()
    json.dumps(j, allow_nan=False)
    tbl = {x["index"]: x for x in rows(j["table"])}
    assert {"dogs", "cheap", j["benchmark"]} <= set(tbl)
    assert j["config"]["rebalance_months"] == 6 and j["config"]["cost_bps"] == 20
    assert set(j["curves"]) == {j["benchmark"], "dogs", "cheap"}
    assert all(len(c) <= 50 for c in j["curves"].values())
    assert tbl["dogs"]["CAGR"] is not None and math.isfinite(tbl["dogs"]["CAGR"])
    assert tbl[j["benchmark"]]["Turnover"] is None            # NaN -> null
    assert client.post("/labs/synthetic/backtest", json={"bogus": 1}).status_code == 400
    assert client.post("/labs/synthetic/backtest", json={"config": {"nope": 1}}).status_code == 400


def test_timing(client):
    r = client.post("/labs/synthetic/timing", json={"screen": "dogs", "stride": 3, "config": {"rebalance_months": 6}})
    assert r.status_code == 200
    j = r.json()
    assert j["screen"] == "dogs" and len(j["table"]["rows"]) >= 2 and "mean" in j["distribution"]["columns"]


def test_graham(client):
    r = client.get("/labs/synthetic/graham", params={"preset": "defensive",
                                                      "overrides": json.dumps({"max_pe": 18})})
    assert r.status_code == 200
    j = r.json()
    assert j["funnel"]["columns"] == ["step", "count"] and len(j["funnel"]["rows"]) >= 2
    counts = [c for _, c in j["funnel"]["rows"]]
    assert counts == sorted(counts, reverse=True)
    assert j["table"]["columns"][0] == "ticker"
    assert client.get("/labs/synthetic/graham", params={"overrides": "{bad"}).status_code == 400


def test_sweep_2x2(client):
    r = client.post("/labs/synthetic/sweep", json={
        "screen": "dogs", "grid": {"dogs.n": [5, 10], "cost_bps": [10, 50]}, "objective": "Sharpe"})
    assert r.status_code == 200
    j = r.json()
    assert len(j["table"]["rows"]) == 4
    assert {"dogs.n", "cost_bps"} <= set(j["table"]["columns"])
    r2 = client.post("/labs/synthetic/sweep", json={"screen": "dogs"})
    assert r2.status_code == 400


def test_significance_and_extended(client):
    r = client.post("/labs/synthetic/significance", json={"screens": ["dogs"], "n": 200})
    assert r.status_code == 200 and "t" in r.json()["table"]["columns"]
    assert client.post("/labs/synthetic/extended", json={"screens": ["dogs"]}).status_code == 400
    recs = [{"date": f"{y}-{m:02d}-28", "series": "cpi", "value": 100 + 0.2 * (12 * (y - 1999) + m)}
            for y in range(1999, 2011) for m in range(1, 13)]
    recs += [{"date": f"{y}-06-30", "series": "rf", "value": 0.02} for y in range(1999, 2011)]
    e = client.post("/labs/synthetic/extended", json={"screens": ["dogs"], "macro": recs})
    assert e.status_code == 200, e.text
    assert len(e.json()["table"]["rows"]) == 2


EXIT = {"signals": [{"type": "TrendBreak", "window_months": 6}, {"type": "Breadth"}, {"type": "DrawdownFromPeak"}],
        "threshold": 0.7, "horizons": [1, 3], "max_points": 60}


def test_exit_evaluate(client):
    r = client.post("/labs/synthetic/exit/evaluate", json=EXIT)
    assert r.status_code == 200, r.text
    j = r.json()
    json.dumps(j, allow_nan=False)
    assert 0 <= j["frac_flagged"] <= 1
    assert {"derisked", "always_in"} == set(j["curves"]) and len(j["curves"]["derisked"]) <= 60
    assert j["forward"]["columns"][:2] == ["horizon_months", "state"]
    bad = client.post("/labs/synthetic/exit/evaluate", json={"signals": [{"type": "Nope"}]})
    assert bad.status_code == 400


def test_backtest_with_exit_policy(client):
    exit_ = {"signals": EXIT["signals"], "threshold": 0.6, "park": 0.03, "reentry": "signal_or_rebalance",
             "reentry_delay_days": 30, "reentry_steps": 2, "rescreen_on_reentry": True}
    r = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "exit": exit_, "with_and_without": True,
                                                      "max_points": 40})
    assert r.status_code == 200, r.text
    j = r.json()
    json.dumps(j, allow_nan=False)
    by = {x["index"]: x for x in rows(j["table"])}
    assert {"dogs", "dogs+exit"} <= set(by) and {"TimeOut", "Switches/yr"} <= set(j["table"]["columns"])
    assert by["dogs"]["TimeOut"] is None and 0 < by["dogs+exit"]["TimeOut"] < 1
    assert {"dogs", "dogs+exit"} <= set(j["curves"])
    bad = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "exit": {**exit_, "reentry": "never"}})
    assert bad.status_code == 400
    bad = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "exit": {**exit_, "bogus": 1}})
    assert bad.status_code == 400
    ev = client.post("/labs/synthetic/exit/evaluate", json={**EXIT, "policy": {"park": 0.03, "reentry_steps": 2}})
    assert ev.status_code == 200, ev.text
    cal = client.post("/labs/synthetic/exit/calibrate", json={"signals": EXIT["signals"],
                      "grid": {"reentry": ["signal", "next_rebalance"], "park": ["cash", 0.03]},
                      "policy": {"reentry_steps": 2}})
    assert cal.status_code == 200, cal.text
    assert "reentry" in cal.json()["table"]["columns"]


def test_exit_calibrate(client, reg):
    r = client.post("/labs/synthetic/exit/calibrate", json={"signals": EXIT["signals"], "grid": {"threshold": [0.6, 0.8]}})
    if hasattr(reg.get("synthetic"), "calibrate_exit"):
        assert r.status_code == 200, r.text
        assert "table" in r.json()
    else:
        assert r.status_code == 501 and "calibrate_exit" in r.json()["error"]


def test_columns_and_signals(client):
    c = client.get("/labs/synthetic/columns").json()
    names = {x["name"]: x for x in c["columns"]}
    assert names["pe"]["in_lab"] and "description" in names["pe"]
    s = client.get("/signals").json()["signals"]
    assert "TrendBreak" in [x["type"] for x in s]


# --------------------------------------------------------------------------- MCP tools in-process
def test_mcp_tools(reg):
    t = build_tools(reg)
    assert {"list_labs", "list_screens", "define_screen", "picks", "backtest", "timing", "graham", "sweep",
            "significance", "extended", "exit_evaluate", "exit_calibrate", "describe_columns",
            "list_signals"} <= set(t)
    assert t["list_labs"]()["default"] == "synthetic"
    t["define_screen"](SPEC)
    assert len(t["picks"]("cheap")["rows"]) > 0
    bt = t["backtest"](["dogs"], config={"rebalance_months": 12}, max_points=40)
    assert "dogs" in bt["curves"] and len(bt["curves"]["dogs"]) <= 40
    g = t["graham"]("defensive", overrides={"max_pe": 15})
    assert g["funnel"]["rows"]
    sw = t["sweep"]("dogs", {"dogs.n": [5, 10], "cost_bps": [10, 50]})
    assert len(sw["table"]["rows"]) == 4
    ev = t["exit_evaluate"](EXIT["signals"])
    assert "frac_flagged" in ev
    assert "pe" in {c["name"] for c in t["describe_columns"]()["columns"]}
    assert any(s["type"] == "Breadth" for s in t["list_signals"]()["signals"])
    with pytest.raises(R.ServerError):
        t["picks"]("nope", lab="missing")


def test_mcp_server_registers_tools(reg):
    from screenlab.server.mcp_server import create_server
    srv = create_server(reg)
    assert srv is not None


def test_backtest_compare_rows_never_primary(client):
    j = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "benchmark": "top20",
                                                       "compare": ["top10"], "max_points": 20}).json()
    assert j["benchmark"] == "SYN_CAPWT"
    assert {"bench_top20 (compare)", "bench_top10 (compare)"} <= set(j["curves"])
    assert client.post("/labs/synthetic/timing", json={"screen": "dogs", "stride": 6}).json()["benchmark"] == "SYN_CAPWT"
