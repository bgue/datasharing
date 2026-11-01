"""SB1: the screen-builder operations (screen_schema, screen_preview, screen_save, screen_load), the data
dictionary and saved screens, on a hand-built market with exact counts.

    python -m pytest screenlab/tests/test_screen_builder.py -q
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab, REGISTRY
from screenlab.data import COLUMN_INFO, Market, add_ratios, synthetic_market
from screenlab.operations import OPERATIONS, builtin_names
from screenlab.server.registry import LabRegistry
from screenlab.spec import load_saved_screens, rule_screen

NAN = np.nan
KINDS = ("screen_schema", "screen_preview", "screen_save", "screen_load")


# ---- a hand-built market ---------------------------------------------------------------------------------
def tiny_lab(thin_last_day: bool = False) -> Lab:
    """12 names T00..T11, flat prices (so the snapshot price is the reported price), one report on 2020-01-31.
    market_cap 120, 110, ..., 10 (T00 largest); price 10; dividend yield by name:
    T00 .05, T01 0, T02 .04, T03 .02, T04 0, T05 .06, T06 .01, T07 .03, T08..T11 .09 (outside the top 8).
    eps is missing (non-positive) for T02, T06, T10, so pe is missing for 3 of 12 names."""
    tk = [f"T{i:02d}" for i in range(12)]
    yld = [.05, 0, .04, .02, 0, .06, .01, .03, .09, .09, .09, .09]
    eps = [2.0, 1.0, -1.0, 2.5, 1.0, 2.0, -1.0, 1.0, 2.0, 1.0, -1.0, 2.0]
    fund = pd.DataFrame({"date": pd.Timestamp("2020-01-31"), "ticker": tk,
                         "sector": ["Ind", "Fin", "Ind", "Tech", "Fin", "Tech", "Ind", "Fin", "Tech", "Ind", "Fin", "Tech"],
                         "price": 10.0, "market_cap": [120.0 - 10 * i for i in range(12)],
                         "eps": eps, "bvps": 5.0, "dividend": [10.0 * y for y in yld]})
    idx = pd.bdate_range("2020-01-31", periods=330)                      # ~ 15 months of business days
    px = pd.DataFrame(1.0, index=idx, columns=tk)
    if thin_last_day:
        px.iloc[-1, 2:] = NAN                                            # only two names print on the last day
    return Lab(Market(fund, px))


@pytest.fixture(scope="module")
def tiny():
    return tiny_lab()


DOGS8 = {"universe": {"top": 8}, "filters": ["div_yield > 0"], "rank": {"div_yield": "desc"}, "n": 3}


# ---- data dictionary -------------------------------------------------------------------------------------
def test_every_snapshot_column_has_a_description():
    frames = {}
    frames["synthetic"] = Lab.synthetic(freq="ME", n_stocks=60, years=12).snapshot()
    frames["tiny"] = tiny_lab().snapshot()
    from screenlab.realdata.bundled import sp500_lab
    frames["bundled_sp500"] = sp500_lab().snapshot()
    # a panel-like frame: month-end price + trailing dividend + Unknown sector + NaN market cap, as sp500_panel builds it
    fr = pd.DataFrame({"date": pd.Timestamp("2020-01-31"), "ticker": ["A", "B"], "price": [10.0, 20.0],
                       "dividend": [0.2, 0.0], "sector": "Unknown", "market_cap": NAN})
    px = pd.DataFrame(1.0, index=pd.bdate_range("2019-01-02", periods=330), columns=["A", "B"])
    from screenlab.dataset import Dataset
    from screenlab.data import security_meta
    ds = Dataset.from_frames(add_ratios(fr), px, None, security_meta=security_meta(["A", "B"], domicile="US", currency="USD",
                                                                                       eligible_dividend=False))
    frames["panel_like"] = Market(ds).view(px.index[-1])
    for label, snap in frames.items():
        missing = [c for c in snap.columns if c not in COLUMN_INFO]
        assert not missing, f"{label}: snapshot columns without a COLUMN_INFO entry: {missing}"
    for c, info in COLUMN_INFO.items():
        assert info["group"] and len(info["description"]) > 15, c
    # the fundamentals contract of the synthetic market too (before the view adds ratios)
    f, _, _ = synthetic_market(n_stocks=10, years=5, freq="ME")
    assert [c for c in f.columns if c not in COLUMN_INFO and c != "ticker"] == []


def test_schema_describes_every_column(tiny):
    r = tiny.screen_schema()
    snap = tiny.snapshot()
    assert [c["name"] for c in r["columns"]] == list(snap.columns)
    by = {c["name"]: c for c in r["columns"]}
    assert r["n_names"] == 12 and r["asof"] == str(tiny.market.index[-1].date())
    dy = by["div_yield"]
    assert dy["kind"] == "number" and dy["group"] == "valuation" and dy["coverage"] == 1.0
    assert dy["min"] == 0.0 and dy["max"] == pytest.approx(0.09) and dy["p50"] == pytest.approx(0.045)
    assert "0.03 = 3%" in dy["description"]
    pe = by["pe"]
    assert pe["coverage"] == 0.75 and pe["min"] == 4.0 and pe["max"] == 10.0       # price 10 / eps: 10/2.5 .. 10/1
    sec = by["sector"]
    assert sec["kind"] == "text" and sec["min"] is None and sec["top"][0]["count"] == 4
    assert by["date"]["kind"] == "date" and by["date"]["min"] == "2020-01-31"
    assert {h["name"] for h in r["helpers"]} == {"pct", "q", "z", "log", "sqrt", "abs", "isna", "coalesce"}
    assert all(h["signature"] and h["description"] for h in r["helpers"])
    json.dumps(r)


# ---- preview: exact funnel -------------------------------------------------------------------------------
def test_dogs_funnel_counts_on_a_hand_built_snapshot(tiny):
    r = tiny.screen_preview(DOGS8)
    assert r["ok"] and r["errors"] == [] and r["n_names"] == 12 and r["stages"] == 1
    f = r["funnel"]
    assert [(s["kind"], s["rows_in"], s["rows_out"], s["alone"]) for s in f] == [
        ("universe", 12, 8, None), ("include", 8, 6, 6), ("rank", 6, 6, None), ("take", 6, 3, None)]
    assert f[0]["step"] == "universe: top 8 by market_cap" and f[1]["expr"] == "div_yield > 0"
    assert f[1]["note"] is None and f[2]["step"] == "rank by div_yield desc" and f[3]["step"] == "take 3"
    # T01 and T04 pay nothing; by yield: T05 .06, T00 .05, T02 .04 (T07 .03, T03 .02, T06 .01)
    assert [p["ticker"] for p in r["picks"]] == ["T05", "T00", "T02"] and r["n_picks"] == 3
    assert r["picks"][0]["rank"] == 1 and r["picks"][0]["sector"] == "Tech"
    v = r["picks"][0]["values"]                                  # the columns the criteria and the ranking use
    assert set(v) == {"market_cap", "div_yield"} and v["market_cap"] == 70.0 and v["div_yield"] == pytest.approx(0.06)
    # the preview is exactly what the screen picks
    assert tiny.screen({"name": "d", **DOGS8})(tiny.snapshot()) == [p["ticker"] for p in r["picks"]]


def test_alone_counts_and_notes(tiny):
    spec = {"filters": ["div_yield > 0.02", "pe < 100", "price > 0", "price < 0"], "n": 5}
    f = tiny.screen_preview(spec)["funnel"]
    # div_yield > 0.02: T00 .05, T02 .04, T05 .06, T07 .03, T08-T11 .09 -> 8 of 12
    assert (f[1]["rows_in"], f[1]["rows_out"], f[1]["alone"]) == (12, 8, 8)
    # pe < 100: of those 8, T02 and T10 have eps < 0, so no pe -> 6 stay; alone (on the 8 entering) is 6 too
    assert (f[2]["rows_in"], f[2]["rows_out"], f[2]["alone"]) == (8, 6, 6)
    assert f[2]["note"] == "pe is missing for 25% of names"                   # 2 of the 8 rows entering the step
    assert (f[3]["rows_in"], f[3]["rows_out"], f[3]["alone"], f[3]["note"]) == (6, 6, 6, "keeps every name")
    assert (f[4]["rows_in"], f[4]["rows_out"], f[4]["alone"], f[4]["note"]) == (6, 0, 0, "drops every name")


def test_missing_note_percentage_on_all_names(tiny):
    f = tiny.screen_preview({"filters": ["pe < 100"]})["funnel"]
    assert (f[1]["rows_in"], f[1]["rows_out"]) == (12, 9)
    assert f[1]["note"] == "pe is missing for 25% of names"                  # T02, T06, T10 of 12


def test_exclude_steps_keep_missing_and_have_no_alone(tiny):
    f = tiny.screen_preview({"exclude": ["pe > 8", "div_yield == 0"]})["funnel"]
    # pe = 10 / eps: T00 5, T01 10, T03 4, T04 10, T05 5, T07 10, T08 5, T09 10, T11 5 ; pe > 8 is true for T01 T04 T07 T09
    assert (f[1]["kind"], f[1]["rows_in"], f[1]["rows_out"], f[1]["alone"]) == ("exclude", 12, 8, None)
    assert f[1]["note"] == "pe is missing for 25% of names"                  # still in: missing is never excluded
    # div_yield == 0 is true for T01 and T04, both already gone
    assert (f[2]["rows_in"], f[2]["rows_out"], f[2]["note"]) == (8, 8, "keeps every name")
    assert tiny.screen_preview({"exclude": ["pe > 8"], "n": 20})["n_picks"] == 8


def test_include_and_exclude_agree_with_the_screen(tiny):
    spec = {"universe": {"include_sectors": ["Ind", "Tech"]}, "derive": {"yy": "div_yield * 100"},
            "filters": ["yy > 1"], "exclude": ["pe > 8"], "rank": {"yy": "desc"}, "n": 10}
    r = tiny.screen_preview(spec)
    assert r["ok"]
    assert [p["ticker"] for p in r["picks"]] == tiny.screen({"name": "q", **spec})(tiny.snapshot())
    assert set(r["picks"][0]["values"]) == {"yy", "div_yield", "pe"} and r["funnel"][1]["kind"] == "derive"
    assert [s["kind"] for s in r["funnel"]] == ["universe", "derive", "include", "exclude", "rank", "take"]
    assert r["funnel"][0]["step"] == "universe: sectors Ind+Tech" and (r["funnel"][0]["rows_out"]) == 8


def test_multi_stage_and_graham_previews(tiny):
    spec = {"n": 2, "stages": [{"universe": {"top": 10}, "filters": ["div_yield > 0"], "rank": {"div_yield": "desc"}, "keep": 5},
                               {"exclude": ["sector == 'Fin'"], "rank": {"market_cap": "desc"}}]}
    r = tiny.screen_preview(spec)
    assert r["ok"] and r["stages"] == 2
    # stage 1: top 10 = T00..T09; div>0 drops T01 T04 -> 8; by yield: T08 T09 .09, T05 .06, T00 .05, T02 .04 -> keep 5
    # stage 2: no Fin among them; by market cap desc: T00 (120), T02 (100), T05 (70), ... -> take 2
    assert [p["ticker"] for p in r["picks"]] == ["T00", "T02"]
    kinds = [(s["step"].split(":")[0], s["kind"], s["rows_in"], s["rows_out"]) for s in r["funnel"]]
    assert kinds == [("stage 1", "universe", 12, 10), ("stage 1", "include", 10, 8), ("stage 1", "rank", 8, 8),
                     ("stage 1", "take", 8, 5), ("stage 2", "exclude", 5, 5), ("stage 2", "rank", 5, 5), ("take 2", "take", 5, 2)]
    assert r["picks"] and tiny.screen({"name": "m", **spec})(tiny.snapshot()) == ["T00", "T02"]
    # a Graham stage: the tiny market has no sales / debt / history columns, so the Graham tests cannot run: reported, not raised
    g = tiny.screen_preview({"graham": {"preset": "defensive"}})
    assert not g["ok"] and g["errors"][0]["where"] == "graham" and "which this data does not have" in g["errors"][0]["message"]


def test_graham_on_the_synthetic_lab_matches_the_screen():
    lab = Lab.synthetic(freq="ME", n_stocks=120, years=12, seed=5)
    spec = {"universe": {"top": 100}, "graham": {"preset": "defensive", "min_passes": 4, "max_pe": 25.0}, "rank": {"pe": "asc"}, "n": 6}
    r = lab.screen_preview(spec)
    assert r["ok"]
    g = [s for s in r["funnel"] if s["step"].startswith("graham")][0]
    assert g["kind"] == "include" and g["step"] == "graham tests (defensive, at least 4 passes)" and g["rows_in"] == r["funnel"][0]["rows_out"] > 50
    assert [p["ticker"] for p in r["picks"]] == lab.screen({"name": "g", **spec})(lab.snapshot(r["asof"]))
    bad = lab.screen_preview({"graham": {"preset": "defensiv"}})
    assert bad["errors"][0]["where"] == "graham" and bad["errors"][0]["suggestion"] == "defensive"
    bad = lab.screen_preview({"graham": {"preset": "defensive", "max_pee": 3}})
    assert bad["errors"][0]["where"] == "graham" and bad["errors"][0]["suggestion"] == "max_pe"


def test_preview_equals_the_registered_screens_on_the_synthetic_lab():
    lab = Lab.synthetic(freq="ME", n_stocks=200, years=12, seed=11)
    d = lab._asof(None)
    snap = lab.snapshot()
    for name in ("dogs", "magic_formula", "value_momentum", "frugal_dividend", "stable_dividend", "bluechip_dividend_growth"):
        r = lab.screen_preview({"name": name}, show=50)
        assert r["ok"] and r["errors"] == [], (name, r["errors"])
        assert [p["ticker"] for p in r["picks"]] == lab.screens[name](snap), name
    assert lab.screen_preview({"name": "frugal_dividend"})["stages"] == 2


def test_name_only_spec_for_a_function_screen_is_an_error(tiny):
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=12)
    with pytest.raises(ValueError, match="Python function screen"):
        lab.screen_preview({"name": "net_nets"})
    with pytest.raises(ValueError, match="Python function screen"):
        lab.screen_load("piotroski")
    with pytest.raises(KeyError):
        lab.screen_load("nope")


# ---- preview: errors -------------------------------------------------------------------------------------
def test_unknown_column_gets_a_suggestion(tiny):
    r = tiny.screen_preview({"filters": ["div_yeild > 0.01"], "n": 3})
    assert not r["ok"] and r["picks"] == [] and r["n_picks"] == 0
    e = r["errors"][0]
    assert e["where"] == "filters[0]" and "div_yeild" in e["message"] and e["suggestion"] == "div_yield"
    # the broken criterion is in the funnel as an error row that keeps everything
    f = r["funnel"][1]
    assert f["note"].startswith("error") and f["rows_in"] == f["rows_out"] == 12


def test_syntax_error_position(tiny):
    r = tiny.screen_preview({"derive": {"x": "pe * (pb"}})
    e = r["errors"][0]
    assert e["where"] == "derive.x" and "syntax error at character" in e["message"]
    r = tiny.screen_preview({"derive": {"x": "pe +"}})
    assert r["errors"][0]["message"].startswith("syntax error at character ")


def test_every_where_label(tiny):
    spec = {"universe": {"top": 5, "by": "market_capp"},
            "derive": {"x": "pe * zz", "1bad": "pe"},
            "filters": ["pe", "nope > 1"], "exclude": ["pe + 1", "pcts(pe) > 1"],
            "rank": {"pee": "asc", "pe": "sideways"}}
    r = tiny.screen_preview(spec)
    wheres = [e["where"] for e in r["errors"]]
    assert wheres == ["universe", "derive.x", "derive.1bad", "filters[0]", "filters[1]", "exclude[0]", "exclude[1]",
                      "rank.pee", "rank.pe"]
    msgs = {e["where"]: e for e in r["errors"]}
    assert msgs["universe"]["suggestion"] == "market_cap"
    assert "zz" in msgs["derive.x"]["message"] and "not a usable column name" in msgs["derive.1bad"]["message"]
    assert "not true/false" in msgs["filters[0]"]["message"] and "not true/false" in msgs["exclude[0]"]["message"]
    assert msgs["exclude[1]"]["suggestion"] == "pct" and msgs["rank.pee"]["suggestion"] == "pe"
    assert "asc" in msgs["rank.pe"]["message"]
    # structural problems: a stage label prefix, the spec itself, n
    r = tiny.screen_preview({"stages": [{"filters": ["pe < 5"]}, {"exclude": ["qq > 1"]}]})
    assert r["errors"][0]["where"] == "stages[1].exclude[0]"
    r = tiny.screen_preview({"n": 0})
    assert r["errors"][0]["where"] == "n"
    r = tiny.screen_preview({"filters": ["pe < 5"], "stages": [{"keep": 2}]})
    assert r["errors"][0]["where"] == "spec" and "not both" in r["errors"][0]["message"]
    r = tiny.screen_preview({"universe": {"tpo": 5}})
    assert r["errors"][0]["where"] == "universe" and r["errors"][0]["suggestion"] == "top"


def test_non_boolean_filter_says_so(tiny):
    e = tiny.screen_preview({"filters": ["div_yield * 2"]})["errors"][0]
    assert e["where"] == "filters[0]" and "not true/false" in e["message"]


def test_a_dangerous_expression_is_an_error_not_evaluated(tiny):
    for expr in ("__import__('os').getcwd() == 'x'", "pe.mean() > 1", "price.values[0] > 1"):
        r = tiny.screen_preview({"filters": [expr]})
        assert not r["ok"] and r["errors"][0]["where"] == "filters[0]"


# ---- asof -----------------------------------------------------------------------------------------------
def test_asof_none_picks_the_last_full_snapshot():
    lab = tiny_lab(thin_last_day=True)
    last, prev = lab.market.index[-1], lab.market.index[-2]
    assert lab.snapshot().shape[0] == 2                                    # the plain "latest" is the thin day
    r = lab.screen_preview({"n": 3})
    assert r["asof"] == str(prev.date()) and r["n_names"] == 12
    assert lab.screen_schema()["asof"] == str(prev.date()) and lab.screen_schema()["n_names"] == 12
    # an explicit date is honoured (snapped back to a trading day)
    assert lab.screen_preview({"n": 3}, asof=str(last.date()))["n_names"] == 2
    assert tiny_lab().screen_preview({"n": 3})["asof"] == str(tiny_lab().market.index[-1].date())


# ---- not a trial ----------------------------------------------------------------------------------------
def test_screen_operations_are_not_trials(tiny):
    from screenlab.tracking import PROCESS_TRIALS
    tiny.screen_preview(DOGS8)
    tiny.screen_schema()
    tiny.screen_load("dogs")
    assert tiny.last_trials() is None
    assert all(not OPERATIONS[k].tracked_entry and OPERATIONS[k].builtin for k in KINDS)
    assert set(KINDS) <= set(builtin_names())
    assert not PROCESS_TRIALS._trials


# ---- save / load / restart ------------------------------------------------------------------------------
SAVE = {"name": "my_dogs", "n": 3, "note": "mine", **{k: v for k, v in DOGS8.items() if k != "n"}}


def test_save_writes_a_file_registers_and_refuses(tiny, tmp_path):
    lab = tiny_lab()
    out = lab.screen_save(SAVE, folder=str(tmp_path))
    assert out == {"name": "my_dogs", "path": str(tmp_path / "my_dogs.json"), "registered": True}
    body = json.loads((tmp_path / "my_dogs.json").read_text())
    assert body["name"] == "my_dogs" and body["n"] == 3 and body["filters"] == ["div_yield > 0"]
    assert "exclude" not in body and "stages" not in body
    assert lab.screens["my_dogs"](lab.snapshot()) == ["T05", "T00", "T02"]
    with pytest.raises(Exception, match="already exists"):
        lab.screen_save(SAVE, folder=str(tmp_path))
    out2 = lab.screen_save({**SAVE, "n": 2}, folder=str(tmp_path), overwrite=True)
    assert json.loads((tmp_path / "my_dogs.json").read_text())["n"] == 2 and lab.screens["my_dogs"].n == 2
    for builtin in ("dogs", "net_nets", "graham_defensive"):
        with pytest.raises(Exception, match="built-in"):
            lab.screen_save({**SAVE, "name": builtin}, folder=str(tmp_path), overwrite=True)
        assert not (tmp_path / f"{builtin}.json").exists()
    with pytest.raises(ValueError, match="needs a name"):
        lab.screen_save({**SAVE, "name": "../evil"}, folder=str(tmp_path))
    with pytest.raises(ValueError, match="not valid.*filters\\[0\\]"):
        lab.screen_save({"name": "broken", "filters": ["nope > 1"]}, folder=str(tmp_path))
    assert not (tmp_path / "broken.json").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my_dogs.json"]


def test_default_folder_is_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path / "sub" / "dir"))
    lab = tiny_lab()
    out = lab.screen_save(SAVE)
    assert out["path"] == str(tmp_path / "sub" / "dir" / "my_dogs.json") and (tmp_path / "sub" / "dir" / "my_dogs.json").exists()


def test_screen_load_returns_an_editable_spec():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=12)
    r = lab.screen_load("dogs")
    assert r["name"] == "dogs" and r["spec"]["universe"] == {"top": 30} and r["spec"]["filters"] == ["div_yield > 0"]
    assert r["spec"]["rank"] == {"div_yield": "desc"} and r["spec"]["n"] == 10 and "built-in" in r["note"]
    fr = lab.screen_load("frugal_dividend")["spec"]
    assert len(fr["stages"]) == 2 and fr["stages"][0]["keep"] == 50
    # the loaded spec previews as the screen does, and saves under a new name
    assert lab.screen_preview(r["spec"])["picks"] == lab.screen_preview({"name": "dogs"})["picks"]


def test_saved_screens_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path))
    make = lambda: tiny_lab()
    reg1 = LabRegistry({"tiny": make}, default="tiny")
    out = reg1.screen_save(SAVE)
    assert out["registered"] and "my_dogs" in reg1.get("tiny").screens
    (tmp_path / "second.json").write_text(json.dumps({"name": "second", "n": 2, "filters": ["pe < 9"], "exclude": ["pe > 7"]}))
    reg2 = LabRegistry({"tiny": make}, default="tiny")                       # a new process, a new registry
    names = [s["name"] for s in reg2.list_screens()["screens"]]
    assert "my_dogs" in names and "second" in names and "dogs" in names
    assert reg2.get("tiny").screens["second"].params["exclude"] == ["pe > 7"]
    assert reg2.run_operation("screen_load", {"name": "my_dogs"})["spec"]["filters"] == ["div_yield > 0"]
    # backtests see it too
    assert "my_dogs" in reg2.get("tiny").screens


def test_bad_files_are_skipped_with_a_log_line(tmp_path, caplog):
    (tmp_path / "ok.json").write_text(json.dumps({"name": "ok", "filters": ["pe < 9"]}))
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[1, 2]")
    (tmp_path / "unknown_key.json").write_text(json.dumps({"name": "uk", "bogus": 1}))
    (tmp_path / "badname.json").write_text(json.dumps({"name": "../x"}))
    (tmp_path / "dogs.json").write_text(json.dumps({"name": "dogs", "filters": ["pe < 1"]}))     # a built-in name
    (tmp_path / "notes.txt").write_text("ignore me")
    lab = tiny_lab()
    before = dict(lab.screens)
    with caplog.at_level(logging.INFO, logger="screenlab.spec"):
        loaded = load_saved_screens(lab, tmp_path)
    assert loaded == ["ok"] and set(lab.screens) - set(before) == {"ok"}
    assert lab.screens["dogs"] is before["dogs"]                           # a file never replaces a built-in
    text = caplog.text
    for f in ("junk.json", "list.json", "unknown_key.json", "badname.json"):
        assert f in text
    assert load_saved_screens(lab, tmp_path / "missing") == []


def test_jobs_labs_load_saved_screens(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path))
    (tmp_path / "saved1.json").write_text(json.dumps({"name": "saved1", "filters": ["pe < 9"]}))
    from screenlab.jobs import _Runtime
    rt = _Runtime({"tiny": lambda: tiny_lab()})
    assert "saved1" in rt.lab("tiny").screens


# ---- REST / MCP ------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def reg():
    return LabRegistry({"tiny": lambda: tiny_lab()}, default="tiny")


def test_rest_routes_exist_by_registry_and_work(reg, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path))
    c = TestClient(create_app(reg))
    paths = c.get("/openapi.json").json()["paths"]
    for k in KINDS:
        assert f"/labs/{{lab}}/{k}" in paths, k
    assert "get" in paths["/labs/{lab}/screen_schema"] and "get" in paths["/labs/{lab}/screen_load/{name}"]
    assert {k["name"] for k in c.get("/operations").json()["operations"]} >= set(KINDS)
    r = c.post("/labs/tiny/screen_preview", json={"spec": DOGS8, "show": 2})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and [p["ticker"] for p in j["picks"]] == ["T05", "T00"] and j["n_picks"] == 3
    assert "trials_in_family" not in j and "data_card" not in j
    assert c.get("/labs/tiny/screen_schema").json()["n_names"] == 12
    # errors in the spec are data, not a 4xx
    bad = c.post("/labs/tiny/screen_preview", json={"spec": {"filters": ["nope > 1"]}})
    assert bad.status_code == 200 and bad.json()["errors"][0]["where"] == "filters[0]"
    # 400s: unknown spec field, wrong type, missing spec, unknown field in a stage, bad date, bad save
    for body in ({"spec": {"fliters": ["pe < 1"]}}, {"spec": {"filters": "pe < 1"}}, {}, {"spec": {"stages": [{"x": 1}]}},
                 {"spec": {"n": "many"}}, {"spec": {"universe": {"tpo": 3}}}, {"spec": DOGS8, "asof": "garbage"},
                 {"spec": DOGS8, "show": -1}):
        r = c.post("/labs/tiny/screen_preview", json=body)
        assert r.status_code == 400 and "error" in r.json(), body
    r = c.post("/labs/tiny/screen_save", json={"spec": {"filters": ["pe < 1"]}})
    assert r.status_code == 400 and "needs a name" in r.json()["error"]
    r = c.post("/labs/tiny/screen_save", json={"spec": {"name": "dogs", "filters": ["pe < 1"]}})
    assert r.status_code == 409 and "built-in" in r.json()["error"]
    r = c.post("/labs/tiny/screen_save", json={"spec": SAVE})
    assert r.status_code == 200 and r.json()["registered"] and (tmp_path / "my_dogs.json").exists()
    assert c.post("/labs/tiny/screen_save", json={"spec": SAVE}).status_code == 409
    r = c.get("/labs/tiny/screen_load/my_dogs")
    assert r.status_code == 200 and r.json()["spec"]["rank"] == {"div_yield": "desc"}
    assert c.get("/labs/tiny/screen_load/nope").status_code == 404
    # the typed job union accepts the new kinds
    assert "ScreenPreviewJob" in c.get("/openapi.json").json()["components"]["schemas"]


def test_mcp_tools_exist_and_work(reg, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from screenlab.server.mcp_server import build_tools
    monkeypatch.setenv("SCREENLAB_SCREENS", str(tmp_path))
    t = build_tools(reg)
    assert set(KINDS) <= set(t)
    assert [p["ticker"] for p in t["screen_preview"](DOGS8, show=3)["picks"]] == ["T05", "T00", "T02"]
    assert t["screen_schema"]()["n_names"] == 12
    assert t["screen_save"]({**SAVE, "name": "mcp_saved"})["registered"]
    assert t["screen_load"]("mcp_saved")["spec"]["n"] == 3
    from screenlab.errors import ServerError
    with pytest.raises(ServerError) as e:
        t["screen_load"]("net_nets_missing")
    assert e.value.status == 404
    typed = build_tools(reg, typed=True)
    assert set(KINDS) <= set(typed)


def test_registry_method_and_jobs(reg):
    assert reg.screen_preview(DOGS8)["n_picks"] == 3
    import time
    jr = LabRegistry({"tiny": lambda: tiny_lab()}, default="tiny", mode="thread")
    try:
        job = jr.submit_run("screen_preview", {"spec": DOGS8}, "tiny")
        for _ in range(100):
            if jr.run_status(job["job_id"])["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert jr.run_status(job["job_id"])["status"] == "done"
        res = jr.run_result(job["job_id"])
        assert res["ok"] and [p["ticker"] for p in res["picks"]] == ["T05", "T00", "T02"]
    finally:
        jr.job_manager.close() if hasattr(jr.job_manager, "close") else None
