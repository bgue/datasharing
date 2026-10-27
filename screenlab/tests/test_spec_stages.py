"""Multi-stage screen specs (DESIGN J3): stage semantics on hand-built data, single-stage sugar, JSON round
trip, and picks equality between the original Python screens and their JSON specs at every golden date.

    python -m pytest screenlab/tests/test_spec_stages.py -q
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from screenlab import REGISTRY, Lab, ScreenBuilder, ScreenSpec, define_screen
from screenlab import screens as S
from screenlab.spec import Stage, rule_screen


@pytest.fixture(scope="module")
def toy():
    # cap order A..H; vol and pe disagree on purpose, so stage order matters
    return pd.DataFrame({
        "market_cap": [80, 70, 60, 50, 40, 30, 20, 10],
        "sector": ["T", "T", "F", "T", "F", "T", "T", "T"],
        "vol": [.5, .1, .2, .9, .3, .4, .05, .6],
        "pe": [5, 20, 9, 2, 7, 3, 1, 4],
        "dividend": [1, 1, 0, 1, 1, 1, 1, 0],
    }, index=list("ABCDEFGH"))


def test_keep_counts_and_order_matter(toy):
    vol_then_pe = [{"rank": {"vol": "asc"}, "keep": 5}, {"rank": {"pe": "asc"}, "keep": 3}]
    pe_then_vol = [{"rank": {"pe": "asc"}, "keep": 5}, {"rank": {"vol": "asc"}, "keep": 3}]
    assert rule_screen(toy, n=10, stages=vol_then_pe) != rule_screen(toy, n=10, stages=pe_then_vol)
    assert len(rule_screen(toy, n=10, stages=vol_then_pe)) == 3               # keep binds before n
    assert len(rule_screen(toy, n=2, stages=vol_then_pe)) == 2                # n takes the head of the last


def test_stage_values_exact(toy):
    # lowest-vol 5: G .05, B .1, C .2, E .3, F .4 ; by pe asc: G 1, F 3, E 7, C 9, B 20 -> keep 3
    got = rule_screen(toy, n=10, stages=[{"rank": {"vol": "asc"}, "keep": 5}, {"rank": {"pe": "asc"}, "keep": 3}])
    assert got == ["G", "F", "E"]
    # pe first: G 1, D 2, F 3, H 4, A 5 (lowest 5) ; by vol asc: G .05, F .4, A .5, H .6, D .9 -> keep 3
    got = rule_screen(toy, n=10, stages=[{"rank": {"pe": "asc"}, "keep": 5}, {"rank": {"vol": "asc"}, "keep": 3}])
    assert got == ["G", "F", "A"]


def test_universe_is_per_stage(toy):
    # stage 1: top 6 by cap; stage 2: top 3 of the SURVIVORS by vol (descending), not of the whole frame
    st = [{"universe": {"top": 6}}, {"universe": {"top": 3, "by": "vol"}}]
    assert rule_screen(toy, n=10, stages=st) == ["D", "A", "F"]
    st = [{"universe": {"top": 6}, "filters": ["dividend > 0"]}, {"universe": {"exclude_sectors": ["F"]}}]
    assert rule_screen(toy, n=10, stages=st) == ["A", "B", "D", "F"]
    # later stage universe sectors act on survivors only
    assert rule_screen(toy, n=10, stages=[{"universe": {"top": 3}}, {"universe": {"sectors": ["F"]}}]) == ["C"]


def test_rank_drops_nan_and_ranked_gives_full_order(toy):
    t = toy.copy()
    t.loc["G", "pe"] = None
    got = rule_screen(t, n=10, stages=[{"rank": {"vol": "asc"}, "keep": 5}, {"rank": {"pe": "asc"}}])
    assert got == ["F", "E", "C", "B"]                                      # G (NaN pe) dropped
    sc = ScreenBuilder("x", n=2).then_keep(5, by="vol").then().rank(pe="asc").build()
    assert sc(t) == ["F", "E"] and sc.ranked(t)[:4] == ["F", "E", "C", "B"]


def test_single_stage_sugar_equals_explicit_stage(toy):
    sugar = ScreenSpec("s", 4, universe={"top": 7}, derive={"x": "pe * 2"}, filters=["dividend > 0", "x < 30"],
                       rank={"vol": "asc", "pe": "desc"})
    explicit = ScreenSpec("e", 4, stages=[Stage(universe={"top": 7}, derive={"x": "pe * 2"},
                                                filters=["dividend > 0", "x < 30"], rank={"vol": "asc", "pe": "desc"})])
    assert sugar.build()(toy) == explicit.build()(toy) != []
    plain = ScreenSpec("p", 3, filters=["dividend > 0"])                     # no rank: head in frame order
    assert plain.build()(toy) == ["A", "B", "D"]
    assert "stages" not in plain.build().params                              # sugar screens keep their old params
    with pytest.raises(ValueError):
        ScreenSpec("bad", 3, filters=["pe > 1"], stages=[{"keep": 2}])
    with pytest.raises(ValueError):
        Stage(keep=0)
    with pytest.raises(TypeError):
        Stage.from_dict({"keeps": 3})


def test_builder_fluent_api(toy):
    b = (ScreenBuilder("b", n=3).universe(top=7).where("dividend > 0")
         .then_keep(4, by="vol").then().rank(pe="asc"))
    assert [s.keep for s in b.spec.stages] == [None, 4, None]
    explicit = rule_screen(toy, n=3, stages=[{"universe": {"top": 7}, "filters": ["dividend > 0"]},
                                              {"rank": {"vol": "asc"}, "keep": 4}, {"rank": {"pe": "asc"}}])
    assert b.build()(toy) == explicit
    k = ScreenBuilder("k", n=3).rank(vol="asc").keep(4).then().rank(pe="asc").build()
    assert k(toy) == rule_screen(toy, n=3, stages=[{"rank": {"vol": "asc"}, "keep": 4}, {"rank": {"pe": "asc"}}])
    desc = ScreenBuilder("d", n=2).then_keep(3, by=["vol", "pe"], asc=False).build()
    assert len(desc(toy)) == 2
    # never calling then/keep leaves the single-stage fields alone
    assert ScreenBuilder("s").where("pe > 1").spec.stages == []


def test_json_round_trip(toy, tmp_path):
    b = (ScreenBuilder("rt", n=3, note="n").universe(top=7, exclude_sectors=["F"]).where("dividend > 0")
         .then_keep(4, by="vol").then().graham("defensive", min_passes=2).rank(pe="asc"))
    d = b.spec.to_dict()
    assert len(d["stages"]) == 3 and d["stages"][1] == {"rank": {"vol": "asc"}, "keep": 4}
    assert ScreenSpec.from_dict(json.loads(b.spec.to_json())).to_dict() == d
    assert ScreenSpec(**d).to_dict() == d                                    # what the run store does
    path = tmp_path / "rt.json"
    path.write_text(b.spec.to_json())
    assert define_screen(str(path)).params == b.build().params
    sc = ScreenBuilder("r2", n=3).universe(top=7).then_keep(4, by="vol").then().rank(pe="asc")
    assert define_screen(sc.spec.to_json())(toy) == define_screen(sc.spec.to_dict())(toy) == sc.build()(toy)
    one = ScreenSpec("one", 3, filters=["pe > 1"])
    assert one.to_dict()["stages"] == [] and ScreenSpec.from_dict(one.to_dict()) == one


def test_universe_override_on_spec_screens():
    base = REGISTRY["dogs"]
    assert base.with_overrides(universe=15).params["universe"] == {"top": 15}
    f = REGISTRY["frugal_dividend"].with_overrides(universe=120, n=4)
    assert f.n == 4 and f.params["stages"][0]["universe"]["top"] == 120
    assert REGISTRY["frugal_dividend"].params["stages"][0]["universe"]["top"] == 300     # original untouched


# --------------------------------------------------------------------------- legacy vs spec
CONVERTED = ["dogs", "magic_formula", "value_momentum", "low_vol_dividend", "screaming_value", "stable_dividend",
             "frugal_dividend", "stable_high_yield", "basic_buyback", "momentum_6m", "momentum_12m",
             "bluechip_dividend_growth"]


@pytest.fixture(scope="module")
def golden_lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)          # the golden run's market


@pytest.mark.parametrize("name", CONVERTED)
def test_spec_picks_equal_legacy_at_every_rebalance_date(golden_lab, name):
    legacy_fn, sc = getattr(S, f"_legacy_{name}"), REGISTRY[name]
    assert sc.fn is rule_screen and sc.note
    seen = 0
    for d in golden_lab.market.index:
        snap = golden_lab.snapshot(d)
        want = legacy_fn(snap, n=sc.n)
        assert sc(snap) == want, (name, d)
        assert sc.ranked(snap)[:len(want)] == want
        seen += bool(want)
    assert seen > 12                                                         # the comparison is not vacuous


def test_python_screens_stay_python():
    for k in ("graham_defensive", "graham_enterprising", "net_nets", "piotroski"):
        assert REGISTRY[k].fn is not rule_screen
    assert list(REGISTRY)[:3] == ["dogs", "graham_defensive", "graham_enterprising"]


def test_registry_specs_are_the_json_files():
    for f in S.spec_files():
        d = json.loads(f.read_text())
        assert define_screen(d).params == REGISTRY[d["name"]].params
    assert {f.parent.name for f in S.spec_files()} == {"globe", "classic"}
    assert len(S.spec_files()) == len(CONVERTED)


def test_runners_up_are_n_plus_two_with_two_flagged(golden_lab):
    for name in ("frugal_dividend", "stable_high_yield", "value_momentum", "dogs"):
        base = golden_lab.picks(name)
        p = golden_lab.picks(name, runners_up=2)
        assert len(p) == len(base) + 2 and p["runner_up"].sum() == 2, name
        assert p.index[:len(base)].tolist() == base.index.tolist()
