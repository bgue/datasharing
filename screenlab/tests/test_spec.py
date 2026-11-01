"""SB1: exclude criteria, include_sectors, expression helpers and the expression safety check, on hand-built data
with exact arithmetic. The golden and fingerprint tests elsewhere prove existing specs did not move.

    python -m pytest screenlab/tests/test_spec.py -q
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from screenlab import REGISTRY, Lab, ScreenBuilder, ScreenSpec, define_screen
from screenlab import screens as S
from screenlab.spec import ExpressionError, Stage, check_expression, eval_expr, rule_screen

NAN = np.nan


@pytest.fixture(scope="module")
def toy():
    # A..J in market-cap order; pe has two missing values (E, G), vol is a permutation
    return pd.DataFrame({
        "market_cap": [100, 90, 80, 70, 60, 50, 40, 30, 20, 10],
        "sector": ["Ind", "Fin", "Ind", "Tech", "Fin", "Tech", "Ind", "Fin", "Tech", "Ind"],
        "pe": [5, 10, 15, 20, NAN, 8, NAN, 30, 12, 25],
        "vol": [.5, .1, .2, .9, .3, .4, .05, .6, .7, .8],
        "eps": [1, 1, 1, 1, -1, 1, -1, 1, 1, 1],
    }, index=list("ABCDEFGHIJ"))


def names_of(toy, **kw):
    return rule_screen(toy, n=20, **kw)


# ---- exclude --------------------------------------------------------------------------------------------
def test_exclude_removes_only_true_rows_and_keeps_missing(toy):
    # pe > 20 is true for H (30) and J (25); E and G have no pe: a comparison with NaN is false, so they stay
    assert names_of(toy, exclude=["pe > 20"]) == list("ABCDEFGI")
    # the include filter with the opposite test drops the missing ones too
    assert names_of(toy, filters=["pe <= 20"]) == list("ABCDFI")
    # include drops NaN, exclude keeps it: together the two views of "pe > 20" differ by exactly E and G
    kept_by_exclude = set(names_of(toy, exclude=["pe > 20"]))
    kept_by_include = set(names_of(toy, filters=["pe <= 20"]))
    assert kept_by_exclude - kept_by_include == {"E", "G"}


def test_exclude_missing_in_the_expression_itself_is_not_excluded(toy):
    # isna is true for E, G: excluding them removes exactly those two
    assert names_of(toy, exclude=["isna(pe)"]) == list("ABCDFHIJ")
    # the result of an expression that is NaN (pe - pe on NaN rows) never excludes
    assert names_of(toy, derive={"d": "pe - pe"}, exclude=["d > 0"]) == list("ABCDEFGHIJ")


def test_filters_and_exclude_combine(toy):
    assert names_of(toy, filters=["pe <= 20"], exclude=["sector == 'Fin'"]) == list("ACDFI")
    # exclude of two expressions drops a row when ANY is true
    assert names_of(toy, exclude=["pe > 20", "vol < 0.06"]) == list("ABCDEFI")      # H, J by pe; G (.05) by vol


def test_include_sectors_and_exclude_sectors(toy):
    assert names_of(toy, universe={"include_sectors": ["Fin", "Tech"]}) == list("BDEFHI")
    assert names_of(toy, universe={"include_sectors": ["Fin", "Tech"], "exclude_sectors": ["Fin"]}) == list("DFI")
    assert names_of(toy, universe={"sectors": ["Fin"]}) == names_of(toy, universe={"include_sectors": ["Fin"]})
    # the size cut still comes first: top 4 = A..D, then Ind only
    assert names_of(toy, universe={"top": 4, "include_sectors": ["Ind"]}) == ["A", "C"]


def test_stages_with_exclude(toy):
    # stage 1: drop losers (eps < 0 is true for E, G) and the two highest pe; keep the 3 lowest vol
    st = [{"exclude": ["eps < 0", "pe > 20"], "rank": {"vol": "asc"}, "keep": 3},
          {"exclude": ["sector == 'Fin'"], "rank": {"pe": "asc"}}]
    # survivors of stage 1: A B C D F I (pe>20 drops H, J); by vol: B .1, C .2, F .4 -> keep 3
    # stage 2: B is Fin, excluded; C pe 15, F pe 8 -> F, C
    assert rule_screen(toy, n=10, stages=st) == ["F", "C"]


# ---- helpers: values by hand ---------------------------------------------------------------------------
def test_pct_by_hand(toy):
    # pe present for 8 names; sorted 5, 8, 10, 12, 15, 20, 25, 30 -> pct = rank / 8
    p = eval_expr(toy, "pct(pe)")
    assert p["A"] == 0.125 and p["F"] == 0.25 and p["B"] == 0.375 and p["I"] == 0.5
    assert p["C"] == 0.625 and p["D"] == 0.75 and p["J"] == 0.875 and p["H"] == 1.0
    assert np.isnan(p["E"]) and np.isnan(p["G"])
    assert names_of(toy, filters=["pct(pe) <= 0.25"]) == ["A", "F"]
    assert names_of(toy, exclude=["pct(pe) > 0.5"]) == list("ABEFGI")        # E, G stay (missing)


def test_q_by_hand(toy):
    # median of 5 8 10 12 15 20 25 30 = (12 + 15) / 2
    assert eval_expr(toy, "q(pe, 0.5)") == 13.5
    assert eval_expr(toy, "q(pe, 0.25)") == pytest.approx(9.5)                # 5 8 10 12 -> rank 1.75: 8 + 0.75 * 2
    assert names_of(toy, filters=["pe < q(pe, 0.5)"]) == ["A", "B", "F", "I"]


def test_z_by_hand(toy):
    v = np.array([5, 8, 10, 12, 15, 20, 25, 30.0])
    assert v.mean() == 15.625 and v.var(ddof=1) == pytest.approx(529.875 / 7)
    z = eval_expr(toy, "z(pe)")
    sd = (529.875 / 7) ** 0.5
    assert z["H"] == pytest.approx(14.375 / sd) and z["A"] == pytest.approx(-10.625 / sd)
    assert names_of(toy, filters=["z(pe) > 1.5"]) == ["H"]
    assert names_of(toy, exclude=["z(pe) < -1"]) == list("BCDEFGHIJ")        # only A (z = -1.22) goes


def test_log_sqrt_abs_isna_coalesce(toy):
    t = pd.DataFrame({"x": [4.0, 1.0, 0.0, -9.0, NAN], "y": [1.0, NAN, 3.0, 4.0, NAN]}, index=list("abcde"))
    log = eval_expr(t, "log(x)")
    assert log["b"] == 0.0 and log["a"] == pytest.approx(np.log(4)) and np.isnan(log["c"]) and np.isnan(log["d"])
    sq = eval_expr(t, "sqrt(x)")
    assert sq["a"] == 2.0 and sq["b"] == 1.0 and sq["c"] == 0.0 and np.isnan(sq["d"]) and np.isnan(sq["e"])
    assert eval_expr(t, "abs(x)").tolist()[:4] == [4.0, 1.0, 0.0, 9.0]
    assert eval_expr(t, "isna(x)").tolist() == [False, False, False, False, True]
    co = eval_expr(t, "coalesce(y, x)")
    assert co.tolist()[:4] == [1.0, 1.0, 3.0, 4.0] and np.isnan(co["e"])
    assert eval_expr(t, "coalesce(y, 0)").tolist() == [1.0, 0.0, 3.0, 4.0, 0.0]
    assert eval_expr(t, "coalesce(y, x, -1)").tolist() == [1.0, 1.0, 3.0, 4.0, -1.0]


def test_helpers_see_the_rows_entering_the_stage_not_the_earlier_filters(toy):
    # pct(pe) is taken over all 10 rows' pe values, even though the first filter already removes high pe names
    one = names_of(toy, filters=["pe < 20", "pct(pe) <= 0.375"])             # 5, 8, 10 -> A, B, F
    assert one == ["A", "B", "F"]
    # a second stage ranks among the survivors of the first: pct over (5, 8, 10, 12, 15) -> 1/5 steps
    two = rule_screen(toy, n=10, stages=[{"filters": ["pe < 20"]}, {"filters": ["pct(pe) <= 0.4"]}])
    assert two == ["A", "F"]
    # the universe cut is part of the stage: pct among the top 5 by cap only (pe 5, 10, 15, 20)
    assert rule_screen(toy, n=10, universe={"top": 5}, filters=["pct(pe) <= 0.5"]) == ["A", "B"]


def test_helpers_in_derive_and_expressions_without_helpers_unchanged(toy):
    got = names_of(toy, derive={"zz": "z(pe) + z(vol)"}, filters=["zz < 0"])
    zp = (toy["pe"] - toy["pe"].mean()) / toy["pe"].std()
    zv = (toy["vol"] - toy["vol"].mean()) / toy["vol"].std()
    assert got == [t for t in toy.index if (zp + zv)[t] < 0]
    assert names_of(toy, derive={"a": "pe * 2"}, filters=["a > 30", "sector not in ['Fin']"]) == ["D", "J"]


# ---- builder and serialisation ---------------------------------------------------------------------------
def test_builder_exclude_and_include_sectors(toy):
    b = (ScreenBuilder("b", n=5).universe(top=8, include_sectors=["Ind", "Tech"]).where("pe < 30")
         .exclude("vol > 0.8").rank(pe="asc"))
    spec = b.spec.to_dict()
    assert spec["universe"] == {"top": 8, "by": "market_cap", "include_sectors": ["Ind", "Tech"]}
    assert spec["exclude"] == ["vol > 0.8"] and spec["filters"] == ["pe < 30"]
    # by hand: top 8 = A..H; Ind/Tech = A C D F G H; pe < 30 drops G (NaN) and H (30); vol > 0.8 excludes D (.9)
    assert b.build()(toy) == ["A", "F", "C"]
    # exclude set before .then() moves into stage 1
    m = ScreenBuilder("m").exclude("pe > 20").then_keep(3, by="vol").build()
    assert m.params["stages"][0]["exclude"] == ["pe > 20"]
    # exclude pe > 20 drops D H J; lowest vol 3 of the rest: G .05, B .1, C .2 (E, G pe missing stay)
    assert m(toy) == ["G", "B", "C"]


def test_exclude_serialised_only_when_non_empty():
    plain = ScreenSpec("p", 5, universe={"top": 10}, filters=["pe < 9"])
    assert "exclude" not in plain.to_dict() and "exclude" not in plain.build().params
    assert plain.to_dict() == ScreenSpec.from_dict(plain.to_dict()).to_dict()
    ex = ScreenSpec("x", 5, filters=["pe < 9"], exclude=["pb > 3"])
    assert ex.to_dict()["exclude"] == ["pb > 3"] and ex.build().params["exclude"] == ["pb > 3"]
    assert ScreenSpec.from_dict(json.loads(ex.to_json())) == ex
    with pytest.raises(ValueError):
        ScreenSpec("bad", exclude=["pe > 1"], stages=[{"keep": 2}])
    assert Stage(exclude=["pe > 1"]).to_dict() == {"exclude": ["pe > 1"]} and Stage().to_dict() == {}


def test_every_builtin_screen_params_have_no_exclude_key():
    for name, sc in REGISTRY.items():
        assert '"exclude"' not in json.dumps(sc.params, default=str), name


# ---- check_expression -----------------------------------------------------------------------------------
NAMES = ["pe", "pb", "div_yield", "sector", "vol_260d", "eps"]


@pytest.mark.parametrize("text", [
    "pe < 15", "pe < 15 and div_yield > 0.03", "sector not in ['Fin', 'Util']", "sector in ('Fin',)", "pe * pb < 22.5",
    "-pe > -10", "not (pe > 10)", "(pe > 1) & (pb < 2)", "pct(pe) < 0.3", "z(vol_260d) > 2 or isna(pe)",
    "coalesce(pe, 99) < q(pe, 0.9)", "log(pe) > 1", "0 < pe < 10", "sector == 'Fin'", "abs(eps) ** 2 > 1", "pe // 2 == 3",
    "pe.notna() & pb.notna()", "eps.isna()",
])
def test_check_expression_accepts(text):
    assert isinstance(check_expression(text, NAMES), list)


def test_check_expression_returns_the_columns_used():
    assert check_expression("pct(pe) < 0.3 and pe * pb < 22.5 and sector in ['A']", NAMES) == ["pe", "pb", "sector"]


@pytest.mark.parametrize("text, why", [
    ("pe.mean() > 1", "a call of anything but a helper"),
    ("pe.values", "attribute access"),
    ("pe.notna(0)", "a call of anything but a helper"),
    ("pe.notna().sum() > 1", "a call of anything but a helper"),
    ("__import__('os').system('x')", "a call of anything but a helper"),
    ("__import__ > 1", "the name"),
    ("pe @ pb", "operator"),
    ("pe[0] > 1", "indexing"),
    ("(lambda: 1)() > 0", "a call of anything but a helper"),
    ("pe > (lambda: 1)", "lambda"),
    ("1 if pe > 1 else 2", "if/else"),
    ("[x for x in pe]", "comprehension"),
    ("pe << 2 > 1", "operator"),
    ("pe is None", "comparison"),
    ("open('f')", "unknown function"),
    ("pct(pe, pb)", "argument"),
    ("pct(col=pe)", "keyword"),
    ("pct > 1", "is a function"),
    ("pe in pb", "list of literals"),
    ("x := 3", "syntax"),
    ("", "empty"),
    ("pe >", "syntax"),
    ("pe ** __class__", "the name"),
])
def test_check_expression_rejects(text, why):
    with pytest.raises(ExpressionError) as e:
        check_expression(text, NAMES)
    assert why in e.value.message


def test_check_expression_names_position_and_suggestion():
    with pytest.raises(ExpressionError) as e:
        check_expression("pe < 15 and dividend_yield > 0.03", NAMES)
    assert e.value.position == 13 and e.value.suggestion == "div_yield" and "dividend_yield" in e.value.message
    with pytest.raises(ExpressionError) as e:
        check_expression("pe < 15 and pcts(pe) < 1", NAMES)
    assert e.value.suggestion == "pct"
    with pytest.raises(ExpressionError) as e:
        check_expression("pe < (15", NAMES)
    assert e.value.position and "syntax error at character" in e.value.message


def test_every_builtin_spec_passes_check_expression():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=12)
    cols = list(lab.snapshot().columns)
    n_exprs = 0
    for name, sc in REGISTRY.items():
        if sc.fn is not rule_screen:
            continue
        stages = sc.params.get("stages") or [sc.params]
        for st in stages:
            names = set(cols)
            for col, expr in (st.get("derive") or {}).items():
                check_expression(expr, names)
                names.add(col)
                n_exprs += 1
            for expr in [*(st.get("filters") or []), *(st.get("exclude") or [])]:
                check_expression(expr, names)
                n_exprs += 1
    assert n_exprs >= 8
