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
from screenlab import runspec as RS
from screenlab.runspec import RunSpec
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


def test_backtest_hold_rule_and_weighting(client):
    def turnover(cfg):
        r = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "config": cfg, "max_points": 20})
        assert r.status_code == 200, r.text
        return {x["index"]: x for x in rows(r.json()["table"])}["dogs"]["Turnover"]
    base = turnover({"rebalance_months": 1})
    held = turnover({"rebalance_months": 1, "hold_rule": {"keep_within": 2.0, "band": 0.2}})
    assert held != base and held < base
    assert turnover({"weighting": "cap"}) is not None


@pytest.mark.parametrize("cfg,needle", [
    ({"hold_rule": {"keep_within": "x"}}, "config.hold_rule.keep_within must be a number >= 1, got 'x'"),
    ({"hold_rule": {"keep_within": 0.5}}, "config.hold_rule.keep_within"),
    ({"hold_rule": {"band": -0.1}}, "config.hold_rule.band"),
    ({"hold_rule": {"bogus": 1}}, "bogus"),
    ({"hold_rule": 3}, "config.hold_rule"),
    ({"weighting": "foo"}, "'cap'"),
    ({"max_weight": "x"}, "config.max_weight"),
    ({"sector_cap": "x"}, "config.sector_cap"),
])
def test_backtest_bad_construction_overrides(client, cfg, needle):
    r = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "config": cfg})
    assert r.status_code == 400 and needle in r.json()["error"], r.text


def test_unknown_weighting_lists_allowed_names(client):
    err = client.post("/labs/synthetic/backtest", json={"screens": ["dogs"], "config": {"weighting": "foo"}}).json()["error"]
    assert "weighting" in err and all(n in err for n in ("equal", "cap", "inv_vol"))


def test_mcp_hold_rule(reg):
    t = build_tools(reg)
    a = t["backtest"](["dogs"], config={"rebalance_months": 1}, max_points=20)
    b = t["backtest"](["dogs"], config={"rebalance_months": 1, "hold_rule": {"keep_within": 2.0}}, max_points=20)
    tv = lambda r: {x["index"]: x for x in rows(r["table"])}["dogs"]["Turnover"]
    assert tv(a) != tv(b)


def _bt(client, tax=None, **cfg):
    body = {"screens": ["dogs"], "config": {**cfg, **({"tax": tax} if tax else {})}, "max_points": 20}
    return client.post("/labs/synthetic/backtest", json=body)


def test_backtest_tax_columns_and_accounts(client):
    plain = _bt(client)
    assert plain.status_code == 200
    assert "AfterTaxCAGR" not in plain.json()["table"]["columns"]
    taxable = _bt(client, {"model": "placeholder", "account": "taxable"})
    tfsa = _bt(client, {"model": "placeholder", "account": "tfsa"})
    assert taxable.status_code == 200 and tfsa.status_code == 200, taxable.text
    for r in (taxable, tfsa):
        assert {"AfterTaxCAGR", "TaxDrag/yr"} <= set(r.json()["table"]["columns"])
    rt, rf = ({x["index"]: x for x in rows(r.json()["table"])} for r in (taxable, tfsa))
    assert rt["dogs"]["AfterTaxCAGR"] != rf["dogs"]["AfterTaxCAGR"]
    assert rt["dogs"]["AfterTaxCAGR"] <= rt["dogs"]["CAGR"] + 1e-12
    bench = [k for k in rt if k not in ("dogs",) and not k.endswith("(compare)")][0]
    assert rt[bench]["AfterTaxCAGR"] is not None                       # the passive index is taxed too


@pytest.mark.parametrize("prov", ["AB", "BC"])
def test_backtest_canadian_tax_provinces(client, prov):
    def get(account):
        r = _bt(client, {"model": "canadian", "account": account, "province": prov, "employment_income": 90000})
        assert r.status_code == 200, r.text
        return {x["index"]: x for x in rows(r.json()["table"])}["dogs"]
    t, f = get("taxable"), get("tfsa")
    assert t["AfterTaxCAGR"] <= t["CAGR"] + 1e-12 and t["AfterTaxCAGR"] != f["AfterTaxCAGR"]
    assert f["AfterTaxCAGR"] == pytest.approx(f["CAGR"], abs=1e-9)


@pytest.mark.parametrize("tax,needle", [
    ({"model": "canadian", "account": "taxable", "province": "ON"}, "config.tax.province must be one of ['AB', 'BC'], got 'ON'"),
    ({"model": "canadian", "account": "bogus", "province": "AB"}, "config.tax.account must be one of"),
    ({"model": "nope", "account": "tfsa"}, "config.tax.model must be one of"),
    ({"model": "none", "account": "tfsa", "us_withholding": 2}, "config.tax.us_withholding"),
    ({"model": "none", "account": "tfsa", "bogus": 1}, "bogus"),
    ({"model": "none"}, "config.tax.account is required"),
])
def test_backtest_bad_tax_is_a_400_naming_the_field(client, tax, needle):
    r = _bt(client, tax)
    assert r.status_code in (400, 422) and needle in json.dumps(r.json()), r.text


def test_mcp_tax_and_bad_province(reg):
    from screenlab.errors import ServerError
    t = build_tools(reg)
    b = t["backtest"](["dogs"], config={"tax": {"model": "placeholder", "account": "taxable"}}, max_points=20)
    assert "AfterTaxCAGR" in b["table"]["columns"]
    with pytest.raises(ServerError) as e:
        t["backtest"](["dogs"], config={"tax": {"model": "canadian", "account": "taxable", "province": "ON"}})
    assert e.value.status == 400 and "config.tax.province" in e.value.message


def test_tax_config_changes_the_run_key(reg):
    keys = set()
    for tax in (None, {"model": "placeholder", "account": "taxable"}, {"model": "placeholder", "account": "tfsa"}):
        args = {"screens": ["dogs"], "config": {"tax": tax} if tax else {}}
        L = reg.get("synthetic")
        spec = RunSpec("backtest", "synthetic", reg._kw("backtest", L, args))
        keys.add(RS.run_key_for(spec, RS.dataset_id(L.market), RS.lab_context(L), "t"))
    assert len(keys) == 3


def test_openapi_shows_tax_sub_model(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "TaxRequest" in schemas
    assert {"model", "account", "province", "year", "other_income", "deductions", "eligible_share",
            "us_withholding"} <= set(schemas["TaxRequest"]["properties"])


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


# Q1: TaxRequest account-centric fields
def test_q1_tax_request_fields_and_harvest_toggle():
    from screenlab.harvesting import TaxLossHarvest
    from screenlab.ops._request import _tax_config
    base = {"model": "canadian", "account": "taxable", "province": "AB"}
    off = _tax_config(base)
    assert off.harvest is None and off.model.other_income == 90_000.0
    on = _tax_config({**base, "harvest": True, "harvest_threshold_pct": 0.2, "harvest_when": "both",
                      "harvest_replacement": "second_pick"})
    assert isinstance(on.harvest, TaxLossHarvest) and on.harvest.threshold_pct == 0.2 and on.harvest.when == "both"
    r = _tax_config({"model": "canadian", "account": "rrsp", "province": "BC", "rrsp_withdrawal_rate": 0.3,
                     "marginal_rate": 0.4})
    assert r.rrsp_withdrawal_rate == 0.3 and r.model.marginal_rate == 0.4
    with pytest.warns(DeprecationWarning):
        assert _tax_config({**base, "employment_income": 50_000}).model.other_income == 50_000.0
    for bad, needle in (({"harvest_when": "never"}, "harvest_when must be one of"),
                        ({"harvest_replacement": "similar"}, "harvest_replacement must be one of"),
                        ({"harvest": "yes"}, "harvest must be true or false"),
                        ({"other_income": 1, "marginal_rate": 0.3}, "not both"),
                        ({"rrsp_withdrawal_rate": 2}, "rrsp_withdrawal_rate must be")):
        with pytest.raises(Exception, match=needle):
            _tax_config({**base, **bad})


def test_q1_openapi_tax_request_new_fields(client):
    props = client.get("/openapi.json").json()["components"]["schemas"]["TaxRequest"]["properties"]
    assert {"other_income", "marginal_rate", "rrsp_withdrawal_rate", "harvest", "harvest_threshold_pct", "harvest_when",
            "harvest_replacement"} <= set(props)
