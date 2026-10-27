"""Datasets, data cards and universes (DESIGN.md J1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import (AllListed, BundledSP500Builder, CSVBuilder, DataCard, Dataset, Filtered, Lab, Membership,
                       SyntheticBuilder, TopN)
from screenlab.data import Market
from screenlab.dataset import SURVIVORSHIP_WARN, DatasetBuilder, survivorship_score
from screenlab.screens import REGISTRY

D = pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01"])
YRS = (D[-1] - D[0]).days / 365.25


def tiny(index_kind="total_return", universe=None):
    """3 names over 4 yearly dates: A 1,2,3,4; B 1,1.5 then delisted; C 1,.8,.6,.5. Benchmark 1..1.5."""
    prices = pd.DataFrame({"A": [1, 2, 3, 4.0], "B": [1, 1.5, np.nan, np.nan], "C": [1, .8, .6, .5]}, index=D)
    prices.index.name = "date"
    bench = pd.DataFrame({"BM": [1, 1.1, 1.3, 1.5]}, index=D)
    rows = [{"date": d, "ticker": t, "market_cap": m, "price": 10.0, "sector": "X",
             "security_type": "common" if t != "C" else "etf"}
            for d in D for t, m in (("A", 300.0), ("B", 200.0), ("C", 100.0))]
    fund = pd.DataFrame(rows)
    return Dataset.from_frames(fund, prices, bench, universe, name="tiny", index_kinds={"BM": index_kind})


def test_survivorship_score_arithmetic():
    ds = tiny()
    # equal weight: (A 4 + B 1.5 flat + C 0.5) / 3 = 2.0 at the end
    exp = 2.0 ** (1 / YRS) - 1.5 ** (1 / YRS)
    assert ds.card.survivorship_score == pytest.approx(exp, abs=1e-12)
    assert survivorship_score(ds.prices, ds.indices["BM"]) == pytest.approx(exp, abs=1e-12)
    assert survivorship_score(ds.prices, None) != survivorship_score(ds.prices, None)   # NaN
    # restricting the names changes the basket: only C -> 0.5
    assert survivorship_score(ds.prices, ds.indices["BM"], ["C"]) == pytest.approx(0.5 ** (1 / YRS) - 1.5 ** (1 / YRS))
    c = ds.card
    assert (c.n_names, c.n_delisted, c.delisted_included, c.start, c.end) == (3, 1, True, "2020-01-01", "2023-01-01")
    assert c.benchmark == "BM" and c.benchmark_kind == "total_return" and c.span == "2020-01-01..2023-01-01"


def test_card_line_and_warnings():
    ds = tiny("price")
    assert ds.card.benchmark_kind == "price"
    w = ds.card.warnings()
    assert any("price index" in x for x in w) and any("survivorship" in x for x in w)
    assert "benchmark BM (price)" in ds.card.line() and "delisted yes (1)" in ds.card.line()
    ok = tiny().card
    assert not any("price index" in x for x in ok.warnings())
    low = ok.for_benchmark("BM", "total_return", 0.0)
    assert low.warnings() == [] and SURVIVORSHIP_WARN > 0
    d = ok.to_dict()
    assert d["name"] == "tiny" and isinstance(d["warnings"], list)


def test_universes_on_hand_built_data():
    ds = tiny()
    f, p = ds.fundamentals, ds.prices
    assert list(AllListed().bind(f, p).members("2022-01-01")) == ["A", "C"]            # B delisted
    assert list(AllListed().bind(f, p).members("2021-01-01")) == ["A", "B", "C"]
    assert list(TopN(2).bind(f, p).members("2021-01-01")) == ["A", "B"]
    assert list(TopN(2).bind(f, p).members("2022-01-01")) == ["A", "C"]                # B no longer trades
    assert list(TopN(1, by="price").bind(f, p).members("2021-01-01")) in (["A"], ["B"], ["C"])
    flt = Filtered(AllListed(), lambda s: s["security_type"] == "common").bind(f, p)
    assert list(flt.members("2021-01-01")) == ["A", "B"]
    assert list(Filtered(TopN(1), lambda s: s["security_type"] == "common").bind(f, p).members("2021-01-01")) == ["A"]
    with pytest.raises(ValueError):
        TopN(2).members("2021-01-01")                                                  # unbound


def test_topn_cut_is_nlargest():
    s = pd.DataFrame({"market_cap": [3.0, 5.0, 5.0, np.nan, 1.0]}, index=list("abcde"))
    pd.testing.assert_frame_equal(TopN(2).cut(s), s.nlargest(2, "market_cap"))
    assert TopN(3).restrict(s).index.tolist() == s.nlargest(3, "market_cap").index.tolist()


def test_membership_entry_exit():
    t = pd.DataFrame({"ticker": ["A", "B", "B", "C"],
                      "start": ["2020-01-01", "2020-01-01", "2022-01-01", "2021-01-01"],
                      "end": ["2022-01-01", "2021-01-01", None, "2021-06-01"]})
    f, p = tiny().fundamentals, tiny().prices
    m = Membership(t).bind(f, pd.DataFrame(1.0, index=D, columns=["A", "B", "C"]))
    assert list(m.members("2020-01-01")) == ["A", "B"]          # start inclusive
    assert list(m.members("2020-12-31")) == ["A", "B"]
    assert list(m.members("2021-01-01")) == ["A", "C"]          # B's end is exclusive
    assert list(m.members("2021-12-31")) == ["A"]               # C left mid-year
    assert list(m.members("2022-01-01")) == ["B"]               # A left, B re-entered
    with pytest.raises(ValueError):
        Membership(pd.DataFrame({"ticker": ["A"]}))
    assert Membership(t).fingerprint() == Membership(t.iloc[::-1]).fingerprint()


def test_market_view_respects_universe():
    base = Market(tiny())
    assert base.universe.fingerprint() == "AllListed()"
    v0 = base.view(D[1])
    assert sorted(v0.index) == ["A", "B", "C"]
    top = Market(tiny(universe=TopN(2)))
    assert sorted(top.view(D[1]).index) == ["A", "B"]
    flt = Market(tiny(universe=Filtered(AllListed(), lambda s: s["security_type"] == "common")))
    assert sorted(flt.view(D[1]).index) == ["A", "B"]
    mem = Membership(pd.DataFrame({"ticker": ["C", "A"], "start": ["2020-01-01"] * 2, "end": [None, "2021-06-01"]}))
    mm = Market(tiny(universe=mem))
    assert sorted(mm.view(D[1]).index) == ["A", "C"] and sorted(mm.view(D[2]).index) == ["C"]
    # AllListed default is exactly the old behaviour: the view is the unfiltered frame
    assert Market(tiny().fundamentals, tiny().prices, tiny().indices).view(D[1]).equals(v0)


def test_builders_and_protocol():
    assert isinstance(SyntheticBuilder(), DatasetBuilder)
    ds = SyntheticBuilder(freq="ME", n_stocks=60, years=5, seed=3).build()
    assert isinstance(ds, Dataset) and isinstance(ds.manifest, DataCard)
    c = ds.card
    assert (c.name, c.n_names, c.benchmark, c.benchmark_kind, c.pit, c.delisted_included) == (
        "synthetic", 60, "SYN_CAPWT", "total_return", True, True)
    assert SyntheticBuilder(n_stocks=999).build(n_stocks=60, freq="ME", years=5, seed=3).card.n_names == 60
    with pytest.raises(Exception):
        ds.card.name = "x"                                    # frozen
    lab = Lab.from_dataset(ds)
    assert lab.market.dataset is ds and lab.data_card().benchmark == "SYN_CAPWT"


def test_synthetic_card_values():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    c = lab.data_card()
    assert c.n_names == 150 and c.n_delisted > 0 and c.benchmark_kind == "total_return"
    assert abs(c.survivorship_score) < SURVIVORSHIP_WARN and c.warnings() == []


def test_csv_builder_roundtrip(tmp_path):
    ds = tiny()
    ds.fundamentals.to_csv(tmp_path / "f.csv", index=False)
    ds.prices.reset_index().melt("date", var_name="ticker", value_name="tri").dropna().to_csv(tmp_path / "p.csv", index=False)
    ds.indices.reset_index().melt("date", var_name="index", value_name="tri").to_csv(tmp_path / "i.csv", index=False)
    b = CSVBuilder(str(tmp_path / "f.csv"), str(tmp_path / "p.csv"), str(tmp_path / "i.csv"), name="mine")
    got = b.build()
    assert got.card.name == "mine" and got.card.benchmark == "BM" and got.card.benchmark_kind == "total_return"
    assert got.card.n_delisted == 1
    assert b.build(index_kind="price").card.benchmark_kind == "price"
    lab = Lab.from_csv(str(tmp_path / "f.csv"), str(tmp_path / "p.csv"), str(tmp_path / "i.csv"))
    assert lab.data_card().benchmark == "BM"


def test_passive_index_registers_total_return_in_card():
    ds = tiny()
    f = ds.fundamentals
    ds2 = Dataset.from_frames(f, ds.prices)                      # no indices: no benchmark yet
    assert ds2.card.benchmark is None and ds2.card.survivorship_score != ds2.card.survivorship_score
    lab = Lab.from_dataset(ds2)
    assert lab.benchmark.index == "PASSIVE_CAPWT"
    c = lab.data_card()
    assert c.benchmark == "PASSIVE_CAPWT" and c.benchmark_kind == "total_return"


def test_bundled_sp500_card_and_warnings():
    pytest.importorskip("skfolio")
    from screenlab.realdata.bundled import sp500_lab
    lab = sp500_lab()
    c = lab.data_card()
    assert (c.name, c.n_names, c.benchmark, c.benchmark_kind, c.delisted_included) == (
        "sp500_bundled", 20, "SP500", "price", False)
    assert c.survivorship_score > SURVIVORSHIP_WARN
    rep = lab.backtest(["momentum_12_1"], rebalance_months=6)
    assert rep.data_card.benchmark_kind == "price"
    text = str(rep)
    assert "Data: sp500_bundled" in text and "benchmark SP500 (price)" in text
    assert text.count("WARNING") == 2 and "price index" in text and "survivorship" in text
    from screenlab.server.registry import result_json
    out = result_json("backtest", lab, {}, rep)
    assert out["data_card"]["benchmark_kind"] == "price" and len(out["data_card"]["warnings"]) == 2


def test_synthetic_report_prints_card_without_warnings():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    rep = lab.backtest(["dogs"])
    text = str(rep)
    assert "Data: synthetic" in text and "WARNING" not in text


def test_dataset_id_stable_for_all_listed_and_changes_with_universe():
    from screenlab.runspec import dataset_id
    a, b = Market(tiny()), Market(tiny(universe=TopN(2)))
    assert dataset_id(a) == dataset_id(Market(tiny().fundamentals, tiny().prices, tiny().indices))
    assert dataset_id(a) != dataset_id(b)


def test_adapter_entry_points(tmp_path):
    from screenlab.realdata import boc_valet, sec_fsds
    p = tmp_path / "boc.json"
    p.write_text('{"observations": [{"d": "2024-01-02", "FXUSDCAD": {"v": "1.33"}, "V80691311": {"v": "7.2"}},'
                 ' {"d": "2024-01-03", "FXUSDCAD": {"v": "1.34"}, "V80691311": {"v": "7.2"}}]}')
    m = boc_valet.builder(p).build()
    assert list(m.frame.columns) == ["fx", "prime"] and m.frame["prime"].iloc[0] == pytest.approx(0.072)
    b = sec_fsds.builder([], {}, tiny().prices)
    assert b.forms == ("10-K",) and isinstance(b, DatasetBuilder)


def test_screens_use_topn_and_registry_intact():
    import inspect
    import screenlab.screens as sc
    assert not hasattr(sc, "_largest") and "TopN" in inspect.getsource(sc)
    assert "dogs" in REGISTRY
