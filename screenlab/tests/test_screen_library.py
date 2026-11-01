"""LA: the screen library (docs/SCREEN_LIBRARY.md): availability ("needs") of every screen, the price-derived columns
high_52w and sma_200, the ten library screens, and named expressions ("my columns").

    python -m pytest screenlab/tests/test_screen_library.py -q
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab, REGISTRY
from screenlab import columns as C
from screenlab.data import COLUMN_INFO, Market, add_ratios
from screenlab.errors import ServerError
from screenlab.operations import OPERATIONS, builtin_names
from screenlab.screens import GROUPS, Screen
from screenlab.server.registry import LabRegistry
from screenlab.spec import ScreenSpec, rule_screen

NAN = np.nan
SPEC_SCREENS = [n for n, s in REGISTRY.items() if s.fn is rule_screen]
LIBRARY = ["momentum_12_1", "low_vol", "high_52w", "trend_momentum", "small_dogs", "large_low_pe", "shareholder_yield",
           "value_composite", "gross_profitability", "safe_yield"]
PRICE_ONLY = ["momentum_12_1", "low_vol", "high_52w", "trend_momentum"]


@pytest.fixture(scope="module")
def syn():
    return Lab.synthetic(freq="B", n_stocks=120, years=4, seed=11)


@pytest.fixture(scope="module")
def syn_snap(syn):
    return syn.snapshot()


def frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=[f"T{i:02d}" for i in range(n)])


# ---- the ten screens are registered, grouped and documented --------------------------------------------------
def test_the_ten_library_screens_are_registered_with_notes_and_group():
    assert list(REGISTRY)[-10:] == LIBRARY
    for n in LIBRARY:
        sc = REGISTRY[n]
        assert sc.fn is rule_screen and len(sc.note) > 80 and GROUPS[n] == "Classic"
    assert [REGISTRY[n].n for n in LIBRARY] == [10, 20, 10, 10, 5, 10, 20, 25, 20, 20]
    assert GROUPS["screaming_value"] == "Globe and Mail" and GROUPS["net_nets"] == "Classic"
    for n in PRICE_ONLY:
        assert REGISTRY[n].params["universe"]["top_optional"] is True and REGISTRY[n].params["universe"]["top"] == 300
    # the screens written with starter column names hold the formulas once built
    assert REGISTRY["shareholder_yield"].params["derive"] == {"shy": "coalesce(div_yield, 0) + coalesce(buyback_yield, 0)"}
    assert REGISTRY["safe_yield"].params["derive"] == {"payout": "dividend / eps"}
    assert REGISTRY["gross_profitability"].params["derive"] == {"gpa": "gross_margin * asset_turnover"}
    assert REGISTRY["high_52w"].params["derive"] == {"pct_of_high": "price / high_52w"}
    assert list(REGISTRY["value_composite"].params["derive"]) == ["ps", "shy"]
    # and screen_load hands back the file as written (names, not formulas)
    assert REGISTRY["safe_yield"].source_spec["rank"] == {"div_yield": "desc"}


# ---- requires(): the columns a screen actually uses ----------------------------------------------------------
@pytest.mark.parametrize("name", list(REGISTRY))
def test_requires_equals_the_columns_the_screen_really_uses(name, syn_snap):
    """Ablation: the screen gives the same picks on a snapshot cut down to requires(); removing any one required column
    breaks it (an error or other picks)."""
    sc = REGISTRY[name]
    req = sc.requires()
    assert req is not None and req
    full = sc(syn_snap)
    cut = syn_snap[sorted(req | ({"market_cap"} if name in PRICE_ONLY else set()))]     # keeps data order (ties) as the full frame
    assert sc(cut) == full, f"{name}: picks change when only requires() is kept"
    for c in sorted(req):
        try:
            same = sc(cut.drop(columns=c)) == full
        except Exception:                                    # noqa: BLE001  (KeyError, UndefinedVariableError ...)
            same = False
        assert not same, f"{name}: {c} is listed in requires() but the screen does not use it"


def test_requires_details():
    assert REGISTRY["momentum_12_1"].requires() == {"mom_12_1"}                     # top_optional: no market_cap
    assert REGISTRY["small_dogs"].requires() == {"market_cap", "div_yield", "price"}
    assert REGISTRY["value_composite"].requires() == {"market_cap", "sector", "pe", "pb", "ev_ebit", "sales", "div_yield",
                                                      "buyback_yield"}
    assert REGISTRY["dogs"].requires() == {"market_cap", "div_yield"}
    assert REGISTRY["graham_defensive"].requires() >= {"sales", "pe_avg3", "div_years", "sector"}
    assert REGISTRY["net_nets"].requires() == {"ncav_to_mcap", "eps"}
    unknown = Screen("mine", lambda s, n=3: list(s.index[:n]), 3)
    assert unknown.requires() is None
    from screenlab.screens import register_requires
    fn = lambda s, n=3: list(s.index[:n])                    # noqa: E731
    fn.__name__ = "my_fn_screen"
    register_requires(fn, ["pe"])
    try:
        assert Screen("x", fn, 3).requires() == {"pe"}
    finally:
        from screenlab.screens import FUNCTION_REQUIRES
        FUNCTION_REQUIRES.pop("my_fn_screen")


def test_requires_expands_named_expressions_first_and_skips_own_derived_columns():
    sp = ScreenSpec("x", universe={"top": 50}, derive={"mine": "pe * 2"}, filters=["mine < 30", "payout < 1"],
                    rank={"shy": "desc"}).build()
    assert sp.requires() == {"market_cap", "pe", "dividend", "eps", "div_yield", "buyback_yield"}
    top_opt = ScreenSpec("y", universe={"top": 50, "top_optional": True, "exclude_sectors": ["Fin"]}, rank={"mom_12_1": "desc"}).build()
    assert top_opt.requires() == {"sector", "mom_12_1"}


# ---- the new price columns -----------------------------------------------------------------------------------
def _one_name_market(tri: np.ndarray, price0: float = 10.0) -> Market:
    idx = pd.bdate_range("2020-01-01", periods=len(tri))
    px = pd.DataFrame({"AAA": tri, "BBB": tri * 2}, index=idx)
    fund = pd.DataFrame({"date": idx[0], "ticker": ["AAA", "BBB"], "sector": "Ind", "price": price0, "market_cap": [100.0, 50.0],
                         "eps": 1.0, "bvps": 5.0, "dividend": 0.0})
    return Market(add_ratios(fund), px)


def test_high_52w_and_sma_200_in_price_units_with_exact_windows():
    tri = np.concatenate([[5.0], np.linspace(1.0, 2.0, 299)])        # day 0 is the high
    m = _one_name_market(tri)
    for i, has_high in ((250, False), (251, True), (252, True)):     # 252 closes needed, including today
        v = m.view(m.index[i])
        assert np.isnan(v.loc["AAA", "high_52w"]) != has_high
    v = m.view(m.index[251])                                         # window = closes 0..251: still contains day 0
    scale = 10.0 / tri[0]                                            # price units: fundamentals price 10 at the report date
    assert v.loc["AAA", "high_52w"] == pytest.approx(scale * tri[:252].max())
    v = m.view(m.index[252])                                         # day 0 has left the window
    assert v.loc["AAA", "high_52w"] == pytest.approx(scale * tri[1:253].max())
    assert v.loc["AAA", "price"] == pytest.approx(scale * tri[252])
    # a risen price is below the 52-week high by exactly price / high
    last = m.view(m.index[-1])
    assert last.loc["AAA", "price"] / last.loc["AAA", "high_52w"] == pytest.approx(tri[-1] / tri[-252:].max())
    # sma_200: mean of the last 200 closes including today, in price units; NaN before 200 closes
    assert np.isnan(m.view(m.index[198]).loc["AAA", "sma_200"]) and not np.isnan(m.view(m.index[199]).loc["AAA", "sma_200"])
    assert last.loc["AAA", "sma_200"] == pytest.approx(scale * tri[-200:].mean())
    assert last.loc["BBB", "sma_200"] / last.loc["BBB", "price"] == pytest.approx(last.loc["AAA", "sma_200"] / last.loc["AAA", "price"])


def test_high_52w_and_sma_200_monthly_data_use_calendar_windows():
    lab = Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=2)
    mk = lab.market
    v = mk.view(mk.index[-1])
    tri = mk.prices[v.index]
    win12 = tri.iloc[-13:]                                          # 12 months back through today
    ratio = (v["price"] / v["high_52w"]).to_numpy()
    assert ratio == pytest.approx((tri.iloc[-1] / win12.max()).to_numpy(), nan_ok=True)
    win10 = tri.iloc[-11:]
    assert (v["price"] / v["sma_200"]).to_numpy() == pytest.approx((tri.iloc[-1] / win10.mean()).to_numpy(), nan_ok=True)


def test_new_columns_are_documented():
    for c in ("high_52w", "sma_200"):
        assert COLUMN_INFO[c]["description"] and COLUMN_INFO[c]["group"]


# ---- every spec screen previews on the synthetic lab -----------------------------------------------------------
@pytest.mark.parametrize("name", SPEC_SCREENS)
def test_every_spec_screen_builds_and_previews_with_a_funnel_on_the_synthetic_lab(name, syn, syn_snap):
    sc = syn.screens[name]
    spec = syn.screen_load(name)["spec"]
    r = syn.screen_preview(spec)
    assert r["ok"], r["errors"]
    assert len(r["funnel"]) >= 2 and all(f["rows_in"] > 0 for f in r["funnel"][:2]), name
    assert r["n_picks"] > 0 and r["picks"], name
    assert [p["ticker"] for p in r["picks"]][:r["n_picks"]] == sc(syn_snap)[:15]      # the preview is the screen
    assert syn.screen_status()[name]["available"] and syn.screen_status()[name]["missing"] == []


def test_screen_status_on_the_synthetic_lab_lists_all_registered_screens_available(syn):
    st = syn.screen_status()
    assert list(st) == list(REGISTRY) and all(v["available"] and v["runnable"] for v in st.values())
    assert {v["group"] for v in st.values()} == {"Globe and Mail", "Classic"}


# ---- status on the bundled S&P 500 sample ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def bundled():
    pytest.importorskip("skfolio")
    from screenlab.realdata.bundled import sp500_lab
    return sp500_lab()


def test_screen_status_on_the_bundled_sample(bundled):
    st = bundled.screen_status()
    assert list(st) == list(REGISTRY)
    runnable = {n for n, v in st.items() if v["available"]}
    assert runnable == {"momentum_12_1", "momentum_6m", "momentum_12m", "low_vol", "high_52w", "trend_momentum"}
    assert runnable == set(bundled.screens)                                   # available == runnable here
    assert all(v["runnable"] == v["available"] for v in st.values())
    snap = bundled.snapshot()
    present = {c for c in snap.columns if snap[c].notna().any()}
    for n, v in st.items():
        if v["available"]:
            assert v["missing"] == []
        else:
            assert v["missing"] and not set(v["missing"]) & present, n
            assert set(v["missing"]) <= set(REGISTRY[n].requires()), n
    assert st["dogs"]["missing"] == ["market_cap", "div_yield"]
    assert st["shareholder_yield"]["missing"] == ["market_cap", "div_yield", "buyback_yield"]
    assert st["small_dogs"]["missing"] == ["market_cap", "div_yield"]
    assert "market_cap" in st["gross_profitability"]["missing"] and "gross_margin" in st["gross_profitability"]["missing"]
    assert st["high_52w"]["group"] == "Classic" and st["screaming_value"]["group"] == "Globe and Mail"
    assert "no size cut: market cap not in the data" in st["high_52w"]["note"]
    assert "market cap not in the data" not in st["momentum_12m"]["note"]
    # an unavailable screen is still registered, and builds and previews where the columns exist
    assert "dogs" in REGISTRY and "dogs" not in bundled.screens


def test_price_only_library_screens_run_on_the_bundled_sample_with_a_card_note(bundled):
    for n in ("high_52w", "trend_momentum"):
        assert 0 < len(bundled.picks(n)) <= 10
    rep = bundled.backtest(["high_52w", "trend_momentum"], start="2015-01-01", rebalance_months=12)
    notes = rep.data_card.warnings()
    assert "NOTE: high_52w: no size cut: market cap not in the data" in notes
    assert "NOTE: trend_momentum: no size cut: market cap not in the data" in notes
    assert len(rep.table) == 3
    # the funnel of a preview says it too
    r = bundled.screen_preview({"name": "high_52w"})
    assert r["ok"] and r["funnel"][0]["note"] == "no size cut: market cap not in the data" and r["n_picks"] == 10
    # a backtest of a screen that is not top_optional carries no such note
    assert not [w for w in bundled.backtest(["momentum_12m"], start="2015-01-01", rebalance_months=12).data_card.warnings()
                if "no size cut" in w]


def test_screen_status_for_function_screens_and_lab_screens():
    lab = Lab.synthetic(freq="ME", n_stocks=40, years=6)
    lab.define({"name": "mine1", "filters": ["pe < 9"], "note": "mine"})
    lab.screens["fn"] = Screen("fn", lambda s, n=3: list(s.index[:n]), 3, note="a function")
    st = lab.screen_status()
    assert st["mine1"] == {"available": True, "missing": [], "group": "Mine", "note": "mine", "runnable": True}
    assert st["fn"]["available"] and st["fn"]["missing"] == [] and st["fn"]["group"] == "Mine"          # requires() is None
    lab.define({"name": "needs_nothing_here", "filters": ["nonesuch > 1"]})
    assert st["graham_defensive"]["available"]
    assert lab.screen_status()["needs_nothing_here"]["missing"] == ["nonesuch"]


# ---- exact rules on hand-built snapshots ---------------------------------------------------------------------
def test_small_dogs_takes_the_five_lowest_prices_of_the_ten_highest_yields_of_the_thirty_largest():
    n = 34
    cap = [1000.0 - i for i in range(n)]                       # T00 largest ... T33 smallest
    y = [0.0 if i % 3 == 0 else 0.01 + 0.001 * i for i in range(n)]
    for i in range(30, 34):
        y[i] = 0.5                                             # huge yields outside the 30 largest: cut away first
    price = [float((i * 7) % 31 + 10) for i in range(n)]
    s = frame(market_cap=cap, div_yield=y, price=price)
    # payers among T00..T29 are i % 3 != 0; the ten highest yields (the Dogs) are i = 29, 28, 26, 25, 23, 22, 20, 19, 17, 16
    # with prices 27, 20, 37, 30, 16, 40, 26, 19, 36, 29: the five lowest are 16 (T23), 19 (T19), 20 (T28), 26 (T20), 27 (T29)
    want = ["T23", "T19", "T28", "T20", "T29"]
    assert REGISTRY["small_dogs"](s) == want
    assert not set(want) & {"T30", "T31", "T32", "T33"}          # the 0.5 yields sit outside the 30 largest
    assert REGISTRY["small_dogs"].ranked(s)[:5] == want


def test_small_dogs_by_hand_small_case():
    # 12 names; the 10 highest yields are T02..T11 (T00, T01 pay least); prices decide stage 2
    s = frame(market_cap=[100.0 - i for i in range(12)], div_yield=[.01, .02] + [.03 + .01 * i for i in range(10)],
              price=[1.0, 2.0, 50.0, 40.0, 30.0, 20.0, 10.0, 60.0, 70.0, 80.0, 90.0, 5.0])
    assert REGISTRY["small_dogs"](s) == ["T11", "T06", "T05", "T04", "T03"]            # prices 5, 10, 20, 30, 40


def test_shareholder_yield_orders_by_dividend_plus_buyback_with_missing_parts_as_zero():
    s = frame(market_cap=[600, 500, 400, 300, 200, 100.0],
              div_yield=[.03, 0.0, .05, NAN, .02, .04], buyback_yield=[.01, .06, NAN, NAN, -.03, .03])
    sc = REGISTRY["shareholder_yield"]
    assert sc.with_overrides(n=3)(s) == ["T05", "T01", "T02"]                       # .07, .06, .05
    assert sc.ranked(s) == ["T05", "T01", "T02", "T00", "T03", "T04"]               # .07 .06 .05 .04 0 -.01


def test_safe_yield_cuts_payout_at_point_six_and_negative_eps():
    s = frame(market_cap=[700, 600, 500, 400, 300, 200, 100.0],
              eps=[2.0, 2.0, -1.0, 1.0, 1.0, 4.0, NAN], dividend=[1.0, 1.4, 0.5, 0.59, 0.6, 0.8, 0.1],
              div_yield=[.05, .09, .08, .04, .07, .06, .03])
    # payouts .5, .7 (cut), -.5 and eps <= 0 (cut), .59, .6 (not < .6: cut), .2, NaN (cut)
    assert REGISTRY["safe_yield"](s) == ["T05", "T00", "T03"]                       # yields .06, .05, .04


def test_value_composite_composite_rank_by_hand():
    # ps = market_cap / sales; shy = coalesce(div_yield, 0) + coalesce(buyback_yield, 0)
    ps = {"A": 1.0, "B": 1.5, "C": 0.5, "D": 4.0, "E": 2.0}
    rows = {  # name: (sector, pe, pb, ev_ebit, div_yield, buyback_yield)
        "A": ("Ind", 10, 1.0, 8, .03, .02), "B": ("Ind", 12, 0.8, 10, .02, 0.0), "C": ("Tech", 8, 1.2, 6, .03, 0.0),
        "D": ("Ind", 20, 3.0, 20, .06, .04), "E": ("Ind", 15, 2.0, 12, .01, 0.0),
        "F": ("Fin", 5, 0.5, 4, .09, .05),            # best on everything but a financial: out
        "G": ("Ind", NAN, 0.9, 5, .05, .05),          # loss-making (pe missing): out
    }
    ps["F"], ps["G"] = 0.4, 0.4
    s = pd.DataFrame({"sector": [r[0] for r in rows.values()], "market_cap": 120.0,
                      "pe": [r[1] for r in rows.values()], "pb": [r[2] for r in rows.values()],
                      "ev_ebit": [r[3] for r in rows.values()], "div_yield": [r[4] for r in rows.values()],
                      "buyback_yield": [r[5] for r in rows.values()],
                      "sales": [120.0 / ps[k] for k in rows]}, index=list(rows))
    # ranks (1 = best): pb A2 B1 C3 D5 E4; pe A2 B3 C1 D5 E4; ps A2 B3 C1 D5 E4; ev_ebit A2 B3 C1 D5 E4;
    # shy (.05 .02 .03 .10 .01) high first: A2 B4 C3 D1 E5.  Sums: A 10, B 14, C 9, D 21, E 21.
    sc = REGISTRY["value_composite"]
    assert sc.ranked(s) == ["C", "A", "B", "D", "E"]
    assert sc.with_overrides(n=2)(s) == ["C", "A"]
    assert sc.n == 25


def test_gross_profitability_is_gross_margin_times_asset_turnover_without_financials_and_utilities():
    s = frame(market_cap=[600, 500, 400, 300, 200, 100.0], sector=["Ind", "Tech", "Ind", "Fin", "Util", "Ind"],
              gross_margin=[.5, .3, .9, 1.0, 1.0, NAN], asset_turnover=[.8, 2.0, .5, 3.0, 3.0, 1.0])
    # gpa: .40, .60, .45, (Fin), (Util), NaN
    sc = REGISTRY["gross_profitability"]
    assert sc.ranked(s) == ["T01", "T02", "T00"] and sc.with_overrides(n=2)(s) == ["T01", "T02"]


def test_price_screens_skip_the_size_cut_only_without_market_caps():
    n = 305
    cap = [1000.0 - i for i in range(n)]
    mom = [float(i) for i in range(n)]                          # the smallest names have the best momentum
    s = frame(market_cap=cap, mom_12_1=mom)
    sc = REGISTRY["momentum_12_1"]
    assert sc(s) == [f"T{i:02d}" for i in range(299, 289, -1)]  # top 300 by cap are T00..T299; best momentum T299..T290
    nocap = s.assign(market_cap=NAN)
    assert sc(nocap) == [f"T{i:02d}" for i in range(304, 294, -1)]       # cut skipped: the whole table is ranked
    assert sc(s.drop(columns="market_cap")) == sc(nocap)
    # a screen without top_optional runs its size cut as before: the top 30 by market cap, not the whole table
    dogs = frame(market_cap=[100.0 - i for i in range(35)], div_yield=[0.01] * 30 + [0.2] * 5)
    assert not set(REGISTRY["dogs"](dogs)) & {f"T{i:02d}" for i in range(30, 35)}


def test_low_vol_high_52w_trend_momentum_and_large_low_pe_rules():
    n = 40
    s = frame(market_cap=[500.0 - i for i in range(n)], vol_260d=[0.5 - 0.01 * i for i in range(n)],
              price=[10.0] * n, high_52w=[10.0 + (i % 7) for i in range(n)], sma_200=[8.0 + (i % 5) for i in range(n)],
              mom_12_1=[0.001 * ((i * 13) % 40) for i in range(n)], pe=[5.0 + i if i % 4 else -1 for i in range(n)])
    # low_vol: 20 lowest volatility = the last 20 names, lowest first
    assert REGISTRY["low_vol"](s) == [f"T{i:02d}" for i in range(39, 19, -1)]
    # high_52w: price / high_52w highest first (ties keep data order): i % 7 == 0 is 1.0, i % 7 == 1 is 10/11, ...
    got = REGISTRY["high_52w"](s)
    assert got == [f"T{i:02d}" for i in (0, 7, 14, 21, 28, 35, 1, 8, 15, 22)]
    # trend_momentum: only price > sma_200 (sma 8 or 9: i % 5 in {0, 1}), then the highest 12-1 momentum
    cand = [i for i in range(n) if 8.0 + (i % 5) < 10.0]
    want = sorted(cand, key=lambda i: -0.001 * ((i * 13) % 40))[:10]
    assert REGISTRY["trend_momentum"](s) == [f"T{i:02d}" for i in want]
    # large_low_pe: 30 largest, positive P/E, the 10 lowest (pe = 5 + i for i % 4 != 0)
    lp = [i for i in range(30) if i % 4]
    assert REGISTRY["large_low_pe"](s) == [f"T{i:02d}" for i in sorted(lp, key=lambda i: 5.0 + i)[:10]]


# ---- named expressions ---------------------------------------------------------------------------------------
@pytest.fixture()
def folder():
    return Path(os.environ["SCREENLAB_SCREENS"])


@pytest.fixture(scope="module")
def mlab():
    return Lab.synthetic(freq="ME", n_stocks=80, years=8, seed=5)


def test_starter_columns_in_schema_with_coverage_and_expr(mlab):
    r = mlab.screen_schema()
    named = {c["name"]: c for c in r["named"]}
    assert list(named) == ["shy", "ps", "payout", "gpa", "pct_of_high"]
    assert {c["group"] for c in named.values()} == {"Starter columns"}
    assert named["shy"]["expr"] == "coalesce(div_yield, 0) + coalesce(buyback_yield, 0)"
    assert named["ps"]["coverage"] == 1.0 and named["ps"]["kind"] == "number" and named["ps"]["max"] > named["ps"]["min"]
    assert named["gpa"]["missing"] == [] and named["gpa"]["description"]
    assert [c["name"] for c in r["columns"]] == list(mlab.snapshot().columns)               # the data columns are untouched
    mlab.column_define("dy2", "div_yield * 2", note="double")
    named = {c["name"]: c for c in mlab.screen_schema()["named"]}
    assert named["dy2"]["group"] == "My columns" and named["dy2"]["expr"] == "div_yield * 2" and named["dy2"]["description"] == "double"


def test_starter_columns_on_data_that_lacks_inputs_show_what_is_missing(bundled):
    named = {c["name"]: c for c in bundled.screen_schema()["named"]}
    assert named["ps"]["coverage"] == 0.0 and named["ps"]["missing"] == ["sales"] and "error" in named["ps"]
    assert named["pct_of_high"]["coverage"] == 1.0 and named["pct_of_high"]["missing"] == []


def test_define_and_delete_with_atomic_storage(mlab, folder):
    out = mlab.column_define("dy2", "div_yield * 2", note="double")
    path = folder / "columns.json"
    assert out["name"] == "dy2" and out["group"] == "My columns" and out["path"] == str(path) and out["warnings"] == []
    assert json.loads(path.read_text()) == {"dy2": {"expr": "div_yield * 2", "note": "double"}}
    assert not list(folder.glob("*.tmp"))
    mlab.column_define("dy4", "dy2 * 2")
    assert mlab.column_define("dy4", "dy2 * 3", overwrite=True)["replaced"] is True
    assert list(json.loads(path.read_text())) == ["dy2", "dy4"]
    assert mlab.column_delete("dy4")["deleted"] is True and list(json.loads(path.read_text())) == ["dy2"]
    with pytest.raises(ServerError) as e:
        mlab.column_delete("dy4")
    assert e.value.status == 404
    with pytest.raises(ServerError) as e:
        mlab.column_delete("shy")
    assert e.value.status == 409
    assert C.ColumnLibrary().mine() == {"dy2": {"expr": "div_yield * 2", "note": "double"}}


def test_define_validation_rules(mlab, folder):
    bad = {"1abc": "pe * 2", "a b": "pe", "": "pe", "for": "pe", "__x": "pe", "x" * 70: "pe"}
    for name, expr in bad.items():
        with pytest.raises(ValueError, match="not a usable column name"):
            mlab.column_define(name, expr)
    for name, why in (("pe", "column of the data"), ("high_52w", "column of the data"), ("pct", "helper"), ("shy", "starter")):
        with pytest.raises(ServerError) as e:
            mlab.column_define(name, "pe * 2")
        assert e.value.status == 409 and why in str(e.value), name
    mlab.column_define("ok1", "pe * 2")
    with pytest.raises(ServerError) as e:
        mlab.column_define("ok1", "pe * 3")                        # taken, no overwrite
    assert e.value.status == 409 and "overwrite" in str(e.value)
    with pytest.raises(ValueError, match="unknown column 'nonesuch'"):
        mlab.column_define("bad1", "nonesuch + 1")
    with pytest.raises(ValueError, match="did you mean 'div_yield'"):
        mlab.column_define("bad2", "div_yeild * 2")
    with pytest.raises(ValueError):
        mlab.column_define("bad3", "pe.__class__")
    with pytest.raises(ValueError, match="empty"):
        mlab.column_define("bad4", "  ")
    # other named expressions are names too
    mlab.column_define("uses_shy", "shy + ok1")
    assert not (folder / "columns.json").read_text().count("bad")


def test_cycles_are_rejected_with_the_path(mlab):
    mlab.column_define("ca", "pe + 1")
    mlab.column_define("cb", "ca + 1")
    with pytest.raises(ValueError, match=r"ca -> cb -> ca"):
        mlab.column_define("ca", "cb + 1", overwrite=True)
    with pytest.raises(ValueError, match=r"self -> self"):
        mlab.column_define("self", "self + 1")
    assert C.ColumnLibrary().mine()["ca"]["expr"] == "pe + 1"                 # nothing was written
    with pytest.raises(ValueError, match="a -> b -> c -> a"):
        C.closure(["a"], {"a": "b + 1", "b": "c + 1", "c": "a + 1"})


def test_trial_evaluation_and_all_missing_warning(mlab):
    with pytest.raises(ValueError, match="does not evaluate"):
        mlab.column_define("boom", "sector - 1")                                  # passes check_expression, raises on the data
    out = mlab.column_define("allnan", "pe / 0 * log(0 - 1)")
    assert out["warnings"] and "missing for every name" in out["warnings"][0] and out["coverage"] == 0.0
    assert "allnan" in C.ColumnLibrary().mine()                               # accepted with a warning


def test_expansion_inlines_per_stage_ahead_of_own_derive_columns():
    spec = {"name": "x", "n": 3, "stages": [
        {"universe": {"top": 50}, "derive": {"mine": "shy * 2"}, "rank": {"mine": "desc"}, "keep": 10},
        {"filters": ["pct_of_high > 0.5", "shy > 0"], "rank": {"gpa": "desc"}}]}
    out, uses = C.expand_spec(spec)
    s1, s2 = out["stages"]
    assert list(s1["derive"]) == ["shy", "mine"] and s1["derive"]["shy"].startswith("coalesce(div_yield")
    assert list(s2["derive"]) == ["pct_of_high", "gpa"]                         # shy is already a column from stage 1
    assert [u["name"] for u in uses] == ["shy", "pct_of_high", "gpa"] and uses[0]["group"] == "Starter columns"
    assert spec["stages"][0]["derive"] == {"mine": "shy * 2"}                   # the input is untouched
    same, none = C.expand_spec({"name": "y", "filters": ["pe < 9"]})
    assert none == [] and same == {"name": "y", "filters": ["pe < 9"]}


def test_a_named_expression_as_universe_by_gets_a_derive_only_stage_first(mlab):
    out, uses = C.expand_spec({"name": "z", "universe": {"top": 20, "by": "ps"}, "rank": {"pe": "asc"}})
    assert out["stages"][0] == {"derive": {"ps": "market_cap / sales"}} and out["stages"][1]["universe"] == {"top": 20, "by": "ps"}
    r = mlab.screen_preview({"name": "z", "n": 5, "universe": {"top": 20, "by": "ps"}, "rank": {"pe": "asc"}})
    assert r["ok"] and r["stages"] == 2 and [u["name"] for u in r["uses"]] == ["ps"]
    snap = mlab.snapshot()
    top = (snap["market_cap"] / snap["sales"]).nlargest(20).index
    assert {p["ticker"] for p in r["picks"]} <= set(top)


def test_screen_preview_reports_uses_and_marks_funnel_rows(mlab):
    mlab.column_define("mine2", "shy * 100", note="percent")
    spec = {"name": "p", "n": 4, "universe": {"top": 60}, "filters": ["mine2 > 1"], "rank": {"gpa": "desc"}}
    r = mlab.screen_preview(spec)
    assert r["ok"], r["errors"]
    assert [(u["name"], u["group"]) for u in r["uses"]] == [("shy", "Starter columns"), ("mine2", "My columns"), ("gpa", "Starter columns")]
    assert r["uses"][1] == {"name": "mine2", "group": "My columns", "expr": "shy * 100", "note": "percent"}
    derive_rows = [f for f in r["funnel"] if f["kind"] == "derive"]
    assert [(f["step"], f.get("named")) for f in derive_rows] == [("derive shy", "Starter columns"), ("derive mine2", "My columns"),
                                                                    ("derive gpa", "Starter columns")]
    assert derive_rows[1]["expr"] == "shy * 100"
    plain = mlab.screen_preview({"name": "q", "universe": {"top": 20}, "rank": {"pe": "asc"}})
    assert plain["uses"] == [] and all("named" not in f for f in plain["funnel"])
    # the picks are what the built screen gives
    sc = mlab.define(spec)
    assert [p["ticker"] for p in r["picks"]] == sc(mlab.market.view(pd.Timestamp(r["asof"])))[:4]


def test_saved_screen_holds_the_formulas_and_survives_library_edits(mlab, folder):
    mlab.column_define("edge", "div_yield * 10", note="v1")
    spec = {"name": "uses_edge", "n": 5, "universe": {"top": 60}, "filters": ["edge > 0.1"], "rank": {"edge": "desc"}}
    mlab.screen_save(spec)
    saved = json.loads((folder / "uses_edge.json").read_text())
    assert saved["derive"] == {"edge": "div_yield * 10"} and saved["rank"] == {"edge": "desc"}      # formula, not just the name
    sc = mlab.screens["uses_edge"]
    assert sc.params["derive"] == {"edge": "div_yield * 10"}
    snap = mlab.snapshot()
    picks_before = sc(snap)
    from screenlab.screen_codec import screen_to_dict
    key_before = json.dumps(screen_to_dict(sc), sort_keys=True, default=str)
    assert "div_yield * 10" in key_before                                                           # the run key holds the formula
    # edit the library: an old saved spec does not move
    mlab.column_define("edge", "dividend * 0 + 1 / pb", note="v2", overwrite=True)
    fresh = Lab.synthetic(freq="ME", n_stocks=80, years=8, seed=5)                                   # a new process loads the file
    from screenlab.spec import load_saved_screens
    assert load_saved_screens(fresh, folder) == ["uses_edge"]
    sc2 = fresh.screens["uses_edge"]
    assert sc2.params == sc.params and sc2(fresh.snapshot()) == picks_before
    assert json.dumps(screen_to_dict(sc2), sort_keys=True, default=str) == key_before
    # while a new screen written with the name now gets the new formula
    assert mlab.define({"name": "uses_edge2", "universe": {"top": 60}, "rank": {"edge": "desc"}}).params["derive"] == {
        "edge": "dividend * 0 + 1 / pb"}
    # rebuilding the saved spec through its own JSON (what a run does) keeps the old formula
    again = ScreenSpec.from_dict(saved).build()
    assert again.params == sc.params


def test_built_in_screens_do_not_depend_on_the_users_library(mlab, folder):
    before = REGISTRY["safe_yield"].params["derive"]
    (folder / "columns.json").write_text(json.dumps({"payout": {"expr": "1 + 1", "note": "ignored: a starter name"}}))
    assert C.ColumnLibrary().mine() == {}                                                          # starter names cannot be shadowed
    assert ScreenSpec("s", rank={"payout": "asc"}).build().params["derive"] == {"payout": "dividend / eps"}
    assert REGISTRY["safe_yield"].params["derive"] == before


def test_bad_columns_file_is_ignored_with_a_log_line(folder, caplog):
    (folder / "columns.json").write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="screenlab.columns"):
        assert C.ColumnLibrary().mine() == {}
    assert "columns.json" in caplog.text
    (folder / "columns.json").write_text(json.dumps({"good": {"expr": "pe * 2"}, "1bad": {"expr": "pe"}, "x": 3}))
    assert list(C.ColumnLibrary().mine()) == ["good"]
    assert ScreenSpec("t", rank={"good": "asc"}).build().params["derive"] == {"good": "pe * 2"}


# ---- operations, REST and MCP ----------------------------------------------------------------------------------
def test_column_operations_are_registered_builtin_untracked():
    for k in ("column_define", "column_delete"):
        op = OPERATIONS[k]
        assert k in builtin_names()
        assert op.tracked_entry is False and op.tracking_args(None) is None


@pytest.fixture(scope="module")
def reg():
    return LabRegistry({"m": lambda: Lab.synthetic(freq="ME", n_stocks=60, years=8, seed=5)}, default="m")


def test_registry_screens_command_returns_status(reg):
    rows = reg.list_screens()["screens"]
    assert [r["name"] for r in rows] == list(REGISTRY)
    r0 = rows[0]
    assert {"name", "n", "note", "params", "available", "missing", "group", "runnable"} <= set(r0)
    assert all(r["available"] and r["runnable"] and r["missing"] == [] for r in rows)
    assert {r["group"] for r in rows} == {"Globe and Mail", "Classic"}


def test_rest_and_mcp_for_screens_status_and_columns(reg, folder):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    c = TestClient(create_app(reg))
    paths = c.get("/openapi.json").json()["paths"]
    assert "/labs/{lab}/column_define" in paths and "/labs/{lab}/column_delete" in paths
    rows = c.get("/labs/m/screens").json()["screens"]
    assert rows[0]["available"] is True and rows[0]["group"] in ("Classic", "Globe and Mail") and "missing" in rows[0]
    r = c.post("/labs/m/column_define", json={"name": "rest_col", "expr": "div_yield * 3", "note": "n"})
    assert r.status_code == 200 and r.json()["group"] == "My columns" and "trials_in_family" not in r.json()
    assert c.post("/labs/m/column_define", json={"name": "rest_col", "expr": "div_yield * 3"}).status_code == 409
    assert c.post("/labs/m/column_define", json={"name": "pe", "expr": "pe"}).status_code == 409
    assert c.post("/labs/m/column_define", json={"name": "rest_bad", "expr": "nonesuch"}).status_code == 400
    assert c.post("/labs/m/column_define", json={"name": "rest_col"}).status_code == 400
    named = c.get("/labs/m/screen_schema").json()["named"]
    assert [n["name"] for n in named][-1] == "rest_col"
    pv = c.post("/labs/m/screen_preview", json={"spec": {"name": "x", "universe": {"top": 30}, "rank": {"rest_col": "desc"}}}).json()
    assert pv["ok"] and [u["name"] for u in pv["uses"]] == ["rest_col"]
    assert c.post("/labs/m/column_delete", json={"name": "rest_col"}).json()["deleted"] is True
    assert c.post("/labs/m/column_delete", json={"name": "rest_col"}).status_code == 404
    assert c.post("/labs/m/column_delete", json={"name": "shy"}).status_code == 409


def test_mcp_tools_for_columns(reg):
    pytest.importorskip("mcp")
    from screenlab.server.mcp_server import build_tools
    t = build_tools(reg)
    assert {"column_define", "column_delete"} <= set(t)
    assert t["column_define"]("mcp_col", "pe * 2")["name"] == "mcp_col"
    assert [n["name"] for n in t["screen_schema"]()["named"]][-1] == "mcp_col"
    assert t["column_delete"]("mcp_col")["deleted"]
    assert t["list_screens"]()["screens"][0]["available"] is True
