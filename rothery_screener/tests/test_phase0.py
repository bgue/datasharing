"""Phase 0 regression tests: CRITIQUE.md B1-B6 repros, the removed ExitOverlay alias, the Market LRU.

    python -m pytest rothery_screener/tests/test_phase0.py -q
"""
from __future__ import annotations

import pytest

from rothery_screener import Config, ExitPolicy, ExitScreen, Lab, Market, Portfolio, TrendBreak


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def policy():
    return ExitPolicy(ExitScreen([TrendBreak("SYN_CAPWT", window_months=6)], threshold=0.6))


# ---- B1 ------------------------------------------------------------------------------------------
def test_b1_same_named_portfolios_keep_both_rows(lab):
    rep = lab.backtest([Portfolio("dogs", "dogs", rebalance_months=6), "dogs"])
    assert list(rep.table.index) == ["SYN_CAPWT", "dogs", "dogs#2"]
    assert set(rep.results) == {"dogs", "dogs#2"}
    # first occurrence is the semiannual portfolio, the second the default annual one
    assert len(rep.results["dogs"].holdings) > len(rep.results["dogs#2"].holdings)
    assert rep.curves.columns.tolist() == ["SYN_CAPWT", "dogs", "dogs#2"]


def test_b1_plain_duplicates_and_benchmark_collision(lab):
    assert list(lab.backtest(["dogs", "dogs", "dogs"]).table.index) == ["SYN_CAPWT", "dogs", "dogs#2", "dogs#3"]
    rep = lab.backtest([Portfolio("SYN_CAPWT", "dogs")])          # named like the benchmark
    assert list(rep.table.index) == ["SYN_CAPWT", "SYN_CAPWT#2"]
    # existing label that already looks like a suffix does not clash
    rep = lab.backtest([Portfolio("dogs#2", "dogs"), "dogs", "dogs"])
    assert len(set(rep.table.index)) == 4


def test_b1_duplicates_with_exit_labels(lab):
    rep = lab.backtest(["dogs", "dogs"], exit=policy(), with_and_without=True)
    assert list(rep.table.index) == ["SYN_CAPWT", "dogs", "dogs+exit", "dogs#2", "dogs#2+exit"]


# ---- B2 / B3 ---------------------------------------------------------------------------------------
def test_b2_with_and_without_honours_config_exit(lab):
    l2 = Lab(lab.market, config=Config(exit=policy()))
    rep = l2.backtest(["dogs"], with_and_without=True)
    assert list(rep.table.index) == ["SYN_CAPWT", "dogs", "dogs+exit"]
    assert rep.results["dogs"].exit is None and rep.results["dogs+exit"].exit is not None
    assert rep.table.loc["dogs+exit", "TimeOut"] > 0
    plain = lab.backtest(["dogs"])
    assert rep.results["dogs"].curve.to_numpy() == pytest.approx(plain.results["dogs"].curve.to_numpy())


def test_b3_exit_overlay_alias_is_gone(lab):
    import rothery_screener
    import rothery_screener.exit_signals as es
    assert not hasattr(es, "ExitOverlay") and not hasattr(rothery_screener, "ExitOverlay")
    with pytest.raises(TypeError):
        Config(exit_overlay=object())
    with pytest.raises(TypeError):
        lab.backtest(["dogs"], exit_overlay=object())


# ---- B4 --------------------------------------------------------------------------------------------
def test_b4_unknown_screen_is_keyerror(lab):
    with pytest.raises(KeyError, match="unknown screen: no_such_screen"):
        lab.screen("no_such_screen")
    with pytest.raises(KeyError, match="unknown screen: zzz"):
        lab.picks("zzz")
    with pytest.raises(KeyError, match="unknown screen"):
        lab.backtest(["dogs", "zzz"])


def test_b4_inline_json_and_specs_still_work(lab):
    sc = lab.screen('{"name": "tmp_inline", "n": 5, "universe": {"top": 100}, "rank": {"div_yield": "desc"}}')
    assert sc.name == "tmp_inline"
    assert lab.screen({"name": "tmp_d", "n": 5, "rank": {"div_yield": "desc"}}).name == "tmp_d"


# ---- B5 --------------------------------------------------------------------------------------------
def test_b5_topn_benchmark_is_a_compare_row(lab):
    rep = lab.backtest(["dogs"], benchmark="top60")
    assert list(rep.table.index) == ["SYN_CAPWT", "bench_top60 (compare)", "dogs"]
    assert rep.benchmark_label == "SYN_CAPWT"
    rep2 = lab.backtest(["dogs"], compare=["top40"])
    assert "bench_top40 (compare)" in rep2.table.index


# ---- B6 / servers ----------------------------------------------------------------------------------
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient            # noqa: E402

from rothery_screener.server.mcp_server import build_tools   # noqa: E402
from rothery_screener.server.registry import LabRegistry, ServerError   # noqa: E402
from rothery_screener.server.rest import create_app   # noqa: E402


@pytest.fixture(scope="module")
def reg(lab):
    return LabRegistry({"syn": lambda: lab}, default="syn")


@pytest.fixture(scope="module")
def client(reg):
    return TestClient(create_app(reg))


def test_b4_rest_unknown_screen_is_404(client):
    r = client.get("/labs/syn/picks/zzz")
    assert r.status_code == 404 and r.json() == {"error": "unknown screen: zzz"}
    r = client.post("/labs/syn/backtest", json={"screens": ["dogs", "zzz"]})
    assert r.status_code == 404 and "unknown screen: zzz" in r.json()["error"]


def test_b4_mcp_unknown_screen_is_clear_error(reg):
    tools = build_tools(reg)
    with pytest.raises(ServerError) as e:
        tools["picks"]("zzz")
    assert e.value.status == 404 and e.value.message == "unknown screen: zzz"


def test_b6_config_with_exit_key_is_a_clean_400(client):
    r = client.post("/labs/syn/backtest", json={"screens": ["dogs"], "config": {"exit": "x"}})
    assert r.status_code == 400 and "config.exit" in r.json()["error"]
    assert "_call" not in r.json()["error"] and "keyword" not in r.json()["error"]


@pytest.mark.parametrize("body, field", [
    ({"config": {"rebalance_months": "twelve"}}, "rebalance_months"),
    ({"config": {"rebalance_months": 1.5}}, "rebalance_months"),
    ({"config": {"cost_bps": "lots"}}, "cost_bps"),
    ({"config": {"nope": 1}}, "nope"),
    ({"config": [1]}, "config"),
    ({"start": "not-a-date"}, "start"),
    ({"bogus": 1}, "bogus"),
    ({"exit": "x"}, "exit"),
    ({"exit": {"signals": ["Nope"]}}, "Nope"),
    ({"benchmark": 5}, "benchmark"),
])
def test_b6_bad_input_is_400_naming_the_field(client, body, field):
    r = client.post("/labs/syn/backtest", json=body)
    err = r.json()["error"]
    assert r.status_code == 400 and field in err, err
    for leak in ("Traceback", "invalid literal", "__init__", "LabRegistry", "Error:"):
        assert leak not in err


def test_b6_other_endpoints_are_clean(client):
    assert client.post("/labs/syn/sweep", json={"screen": "dogs"}).json() == {"error": "missing required field 'grid'"}
    assert client.get("/labs/syn/picks/dogs", params={"asof": "garbage"}).status_code == 400
    assert client.get("/labs/nope/screens").status_code == 404
    assert client.post("/labs/syn/screens", json={"nonsense": 1}).status_code == 400
    r = client.get("/labs/syn/graham", params={"overrides": '{"bogus": 1}'})
    assert r.status_code == 400 and "bogus" in r.json()["error"]
    r = client.post("/labs/syn/backtest", content="[1, 2]", headers={"content-type": "application/json"})
    assert r.status_code == 400 and "request body" in r.json()["error"]
    r = client.post("/labs/syn/timing", json={"screen": "dogs", "mode": "bogus"})
    assert r.status_code == 400 and "Error" not in r.json()["error"]


def test_b6_registry_errors_are_server_errors(reg):
    for call in (lambda: reg.backtest(config={"exit": 1}), lambda: reg.picks("zzz"),
                 lambda: reg.timing("dogs", start="x"), lambda: reg.sweep("dogs", {"nope.x": [1]})):
        with pytest.raises(ServerError):
            call()


# ---- LRU ---------------------------------------------------------------------------------------------
def test_market_view_cache_is_bounded_and_results_identical(lab):
    fund, prices, indices = lab.market.fund, lab.market.prices, lab.market.indices
    small = Market(fund, prices, indices, max_views=4)
    dates = list(prices.index[14:24])
    first = [small.view(d).copy() for d in dates]
    assert len(small._views) == 4 and small.max_views == 4
    for d, v in zip(dates, first):                       # evicted views are recomputed identically
        assert small.view(d).equals(v)
    assert len(small._views) == 4
    assert Market(fund, prices, indices).max_views == 2048
    sc = lab.screens["dogs"]
    picks = [small.picks(sc, d) for d in dates]
    assert len(small._picks) <= 4
    assert [small.picks(sc, d) for d in dates] == picks
    ref = lab.market.picks(sc, dates[0])
    assert ref == picks[0]
