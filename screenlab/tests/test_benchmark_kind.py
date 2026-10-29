"""Benchmark kind: a price index is never silently compared with total-return portfolios (CRITIQUE.md risk 2).

Kind inference per dataset, the refusal and its message, the allow flag, the approximate total return
(`dividend_yield`) arithmetic, the data card wording, and REST / MCP requests.

    python -m pytest screenlab/tests/test_benchmark_kind.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import (BundledSP500Builder, CSVBuilder, ExitScreen, IndexBenchmark, Lab, SyntheticBuilder, TrendBreak)
from screenlab.backtest import Config
from screenlab.data import Market
from screenlab.dataset import Dataset
from screenlab.errors import BenchmarkKindError
from screenlab.realdata.bundled import price_only_fundamentals, price_screens


# --------------------------------------------------------------------------- fixtures
def _syn(kind="total_return") -> Dataset:
    """The small synthetic market with its SYN_CAPWT index recorded as `kind`."""
    d = SyntheticBuilder(n_stocks=60, years=6, freq="ME", seed=3).build()
    return Dataset.from_frames(d.fundamentals, d.prices, d.indices, name="syn_" + kind,
                               index_kinds={"SYN_CAPWT": kind})


@pytest.fixture(scope="module")
def price_lab() -> Lab:
    return Lab.from_dataset(_syn("price"))


def constant_lab(index_kind="price", ret=0.005, months=180, **kw) -> Lab:
    dates = pd.date_range("1995-01-31", periods=months, freq="ME")
    t = np.arange(months)
    px = pd.DataFrame({f"S{i}": 100 * (1 + r) ** t for i, r in enumerate((0.01, 0.02, 0.03, 0.04))}, index=dates)
    px.index.name = "date"
    idx = pd.DataFrame({"IDX": 1000 * (1 + ret) ** t}, index=dates)
    ds = Dataset.from_frames(price_only_fundamentals(px), px, idx, name="constant", index_kinds={"IDX": index_kind})
    return Lab.from_dataset(ds, screens=price_screens(3), **kw)


# --------------------------------------------------------------------------- kind inference per dataset
def test_synthetic_and_built_passive_are_total_return():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)
    assert lab.benchmark.source_kind(lab.market) == lab.benchmark.effective_kind(lab.market) == "total_return"
    assert lab.data_card().benchmark_kind == "total_return"
    d = _syn()
    bare = Dataset.from_frames(d.fundamentals, d.prices, None, name="no_index")      # no index series at all
    pl = Lab.from_dataset(bare)
    assert pl.benchmark.index in ("PASSIVE_CAPWT", "PASSIVE_EW")
    assert pl.benchmark.effective_kind(pl.market) == "total_return"
    assert pl.data_card().benchmark_kind == "total_return"
    pl.backtest(["dogs"])                                                              # runs, no refusal


def test_bundled_sp500_index_is_price_and_default_benchmark_is_approx_tr():
    pytest.importorskip("skfolio")
    from screenlab.realdata.bundled import sp500_lab
    ds = BundledSP500Builder().build()
    assert ds.card.benchmark_kind == "price" and ds.kind_of("SP500") == "price" and ds.kind_of("AAPL") == "total_return"
    lab = sp500_lab()
    assert lab.benchmark == IndexBenchmark("SP500", dividend_yield=0.02)
    assert lab.benchmark.effective_kind(lab.market) == "approx_total_return"
    assert lab.benchmark.label == "SP500 +2.0% div (approx. TR)"
    raw = sp500_lab(benchmark="price")
    assert raw.benchmark == IndexBenchmark("SP500") and raw.benchmark.effective_kind(raw.market) == "price"
    assert sp500_lab(dividend_yield=0.03).benchmark.label == "SP500 +3.0% div (approx. TR)"


def test_csv_index_kind_column_else_assumed_total_return_with_warning(tmp_path):
    base = Lab.synthetic(freq="ME", n_stocks=30, years=4, seed=3).market
    f, p, i = tmp_path / "f.csv", tmp_path / "p.csv", tmp_path / "i.csv"
    base.fund.to_csv(f, index=False)
    base.prices.stack().rename("tri").rename_axis(["date", "ticker"]).reset_index().to_csv(p, index=False)
    idx = base.indices["SYN_CAPWT"].rename("tri").rename_axis("date").reset_index().assign(index="SYN_CAPWT")
    idx.to_csv(i, index=False)
    ds = CSVBuilder(str(f), str(p), str(i)).build()                                    # no kind column
    assert ds.kind_of("SYN_CAPWT") == "total_return"
    assert ds.card.assumed_kinds == ("SYN_CAPWT",)
    assert any("assumed" in w and "kind" in w for w in ds.card.warnings())
    idx.assign(kind="price").to_csv(i, index=False)                                    # stated: price
    ds = CSVBuilder(str(f), str(p), str(i)).build()
    assert ds.kind_of("SYN_CAPWT") == "price" and ds.card.assumed_kinds == ()
    assert not any("assumed" in w for w in ds.card.warnings())
    idx.assign(kind="total_return").to_csv(i, index=False)
    assert CSVBuilder(str(f), str(p), str(i)).build().card.benchmark_kind == "total_return"
    # the explicit builder option still wins over (and replaces) the column
    assert CSVBuilder(str(f), str(p), str(i), index_kind="price").build().card.benchmark_kind == "price"


def test_explicit_kind_overrides_the_dataset():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)
    b = IndexBenchmark("SYN_CAPWT", kind="price")
    assert b.effective_kind(lab.market) == "price"
    with pytest.raises(BenchmarkKindError):
        lab.backtest(["dogs"], benchmark=b)
    lab2 = Lab.from_dataset(_syn("price"))
    assert IndexBenchmark("SYN_CAPWT", kind="total_return").effective_kind(lab2.market) == "total_return"
    lab2.backtest(["dogs"], benchmark=IndexBenchmark("SYN_CAPWT", kind="total_return"))


def test_bad_kind_and_yield_are_rejected():
    for kw in (dict(kind="gross"), dict(dividend_yield=-0.01), dict(dividend_yield=2.0),
               dict(kind="approx_total_return"), dict(kind="total_return", dividend_yield=0.02)):
        with pytest.raises(ValueError):
            IndexBenchmark("X", **kw)
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)            # a TR series + dividends = double count
    with pytest.raises(ValueError, match="twice"):
        lab.backtest(["dogs"], benchmark=IndexBenchmark("SYN_CAPWT", dividend_yield=0.02))


# --------------------------------------------------------------------------- the refusal
def test_refusal_message_names_the_fixes(price_lab):
    with pytest.raises(BenchmarkKindError) as e:
        price_lab.backtest(["dogs"])
    m = str(e.value)
    assert isinstance(e.value, ValueError)
    assert "SYN_CAPWT" in m and "price index" in m and "dividends" in m
    assert "IndexBenchmark('SYN_CAPWT', dividend_yield=0.02)" in m
    assert "total-return series" in m and "allow_price_benchmark=True" in m


def test_every_analysis_refuses_a_price_benchmark(price_lab):
    lab = price_lab
    es = ExitScreen([TrendBreak("SYN_CAPWT")])
    calls = {
        "backtest": lambda: lab.backtest(["dogs"]),
        "timing": lambda: lab.timing("dogs", stride=30),
        "significance": lambda: lab.significance(lab.backtest(["dogs"], allow_price_benchmark=False)),
        "extended": lambda: lab.extended(lab.backtest(["dogs"]), None),
        "exit_eval": lambda: lab.exit_eval(es),
        "calibrate_exit": lambda: lab.calibrate_exit(es, {"threshold": [0.6, 0.8]}),
        "sweep": lambda: lab.sweep("dogs", {"dogs.n": [5, 10]}),
        "montecarlo": lambda: lab.montecarlo(["dogs"], n_paths=5, horizon_years=[1]),
        "passive_resolution": lambda: lab.benchmark_for(),
    }
    for name, call in calls.items():
        with pytest.raises(BenchmarkKindError, match="allow_price_benchmark"):
            call()
    # the run operations, which compose backtest + analysis, refuse too
    with pytest.raises(BenchmarkKindError):
        lab.run("significance", screens=["dogs"])


def test_descriptive_calls_do_not_refuse(price_lab):
    assert price_lab.passive().index == "SYN_CAPWT"
    c = price_lab.data_card()
    assert c.benchmark_kind == "price" and any("price index" in w for w in c.warnings())


def test_report_of_a_price_benchmark_is_refused_by_significance_and_extended(price_lab):
    rep = price_lab.backtest(["dogs"], allow_price_benchmark=True)
    assert rep.price_benchmark_allowed
    price_lab.significance(rep, n=50)                       # the run opted in: its analyses follow
    other = Lab.from_dataset(_syn("price"))
    rep2 = other.backtest(["dogs"], allow_price_benchmark=True)
    rep2.price_benchmark_allowed = False                    # a report that did not opt in
    with pytest.raises(BenchmarkKindError):
        other.significance(rep2, n=50)
    with pytest.raises(BenchmarkKindError):
        other.extended(rep2, None)
    other.significance(rep2, n=50, allow_price_benchmark=True)


# --------------------------------------------------------------------------- the allow flag
def test_allow_flag_per_call_and_on_the_lab_keeps_the_old_numbers_and_warning():
    lab = Lab.from_dataset(_syn("price"))
    ok = lab.backtest(["dogs"], allow_price_benchmark=True)
    assert ok.benchmark_label == "SYN_CAPWT" and ok.data_card.benchmark_kind == "price"
    assert "WARNING: benchmark SYN_CAPWT is a price index" in str(ok)
    tr = Lab.from_dataset(_syn("total_return")).backtest(["dogs"])           # same numbers, other kind
    pd.testing.assert_frame_equal(ok.table, tr.table)
    on = Lab.from_dataset(_syn("price"), allow_price_benchmark=True)
    pd.testing.assert_frame_equal(on.backtest(["dogs"]).table, ok.table)
    with pytest.raises(BenchmarkKindError):                                   # per call can switch it back off
        on.backtest(["dogs"], allow_price_benchmark=False)
    on.timing("dogs", stride=30)
    on.sweep("dogs", {"dogs.n": [5, 10]})
    on.exit_eval(ExitScreen([TrendBreak("SYN_CAPWT")]))
    on.montecarlo(["dogs"], n_paths=5, horizon_years=[1])


# --------------------------------------------------------------------------- approximate total return
def test_approx_tr_arithmetic_constant_two_percent_over_ten_years():
    dates = pd.to_datetime(["2000-01-03", "2005-01-03", "2010-01-04"])
    px = pd.DataFrame({"A": [1.0, 1.0, 1.0]}, index=dates)
    idx = pd.DataFrame({"FLAT": [100.0, 100.0, 100.0]}, index=dates)
    ds = Dataset.from_frames(price_only_fundamentals(px), px, idx, index_kinds={"FLAT": "price"})
    mkt = Market(ds)
    cfg = Config()
    b = IndexBenchmark("FLAT", dividend_yield=0.02)
    c = b.curve(mkt, cfg, dates[0])
    yrs = [(d - dates[0]).days / 365.25 for d in dates]
    assert c.tolist() == pytest.approx([1.02 ** y for y in yrs], rel=1e-14)
    assert c.iloc[-1] == pytest.approx(1.02 ** 10, rel=1e-3) and yrs[-1] == pytest.approx(10, abs=0.01)
    assert b.label == "FLAT +2.0% div (approx. TR)" and b.effective_kind(mkt) == "approx_total_return"
    # a price index that grows 5%/yr: approx TR = price curve x 1.02 ** years; MER and yield combine
    g = pd.DataFrame({"G": [100 * 1.05 ** y for y in yrs]}, index=dates)
    mkt2 = Market(Dataset.from_frames(price_only_fundamentals(px), px, g, index_kinds={"G": "price"}))
    raw = IndexBenchmark("G", kind="price").curve(mkt2, cfg, dates[0])
    tr = IndexBenchmark("G", dividend_yield=0.02).curve(mkt2, cfg, dates[0])
    assert (tr / raw).tolist() == pytest.approx([1.02 ** y for y in yrs], rel=1e-12)
    both = IndexBenchmark("G", mer=0.01, dividend_yield=0.02).curve(mkt2, cfg, dates[0])
    assert (both / raw).tolist() == pytest.approx([(0.99 * 1.02) ** y for y in yrs], rel=1e-12)
    assert IndexBenchmark("G", dividend_yield=0.02, name="mine").label == "mine"


def test_approx_tr_benchmark_runs_without_a_flag_and_the_card_says_so(price_lab):
    b = IndexBenchmark("SYN_CAPWT", dividend_yield=0.02)
    rep = price_lab.backtest(["dogs"], benchmark=b)
    assert rep.benchmark_label == "SYN_CAPWT +2.0% div (approx. TR)"
    assert rep.table.index[0] == rep.benchmark_label and not rep.price_benchmark_allowed
    card = rep.data_card
    assert card.benchmark_kind == "approx_total_return" and card.dividend_yield == 0.02
    assert "benchmark SYN_CAPWT (approx_total_return: price index + assumed 2.0%/yr dividends)" in card.line()
    w = " ".join(card.warnings())
    assert "approximate total return" in w and "2.0%/yr" in w and "price index (dividends left out)" not in w
    assert "approximate total return" in str(rep)
    # the benchmark curve is the price curve with the dividends added back, the portfolio is untouched
    raw = price_lab.backtest(["dogs"], allow_price_benchmark=True)
    pd.testing.assert_series_equal(rep.table.loc["dogs", ["CAGR", "Vol", "MaxDD", "Turnover"]],
                                   raw.table.loc["dogs", ["CAGR", "Vol", "MaxDD", "Turnover"]])
    yrs = (rep.benchmark.index - rep.benchmark.index[0]).days / 365.25
    np.testing.assert_allclose((rep.benchmark / raw.benchmark).to_numpy(), 1.02 ** yrs, rtol=1e-12)
    assert rep.table.loc["dogs", "Excess/yr"] < raw.table.loc["dogs", "Excess/yr"]      # less flattered
    # significance / extended / timing / sweep follow with no flag
    price_lab.significance(rep, n=50)
    price_lab.timing("dogs", stride=30, benchmark=b)
    price_lab.sweep("dogs", {"dogs.n": [5, 10]}, benchmark=b)
    price_lab.exit_eval(ExitScreen([TrendBreak("SYN_CAPWT")]), allow_price_benchmark=True)


def test_data_card_line_for_each_kind(price_lab):
    assert "benchmark SYN_CAPWT (price)" in price_lab.data_card().line()
    tr = Lab.from_dataset(_syn("total_return")).data_card()
    assert "benchmark SYN_CAPWT (total_return)" in tr.line()
    assert price_lab.data_card(IndexBenchmark("SYN_CAPWT", dividend_yield=0.02)).dividend_yield == 0.02
    d = price_lab.data_card(IndexBenchmark("SYN_CAPWT", dividend_yield=0.02)).to_dict()
    assert d["benchmark_kind"] == "approx_total_return" and d["dividend_yield"] == 0.02
    # the survivorship score is taken against the approximate total return, not the raw price index
    assert (price_lab.data_card(IndexBenchmark("SYN_CAPWT", dividend_yield=0.02)).survivorship_score
            < price_lab.data_card().survivorship_score)


def test_montecarlo_index_gets_the_dividend_yield():
    lab = constant_lab("price")
    kw = dict(screens=["low_vol"], n_paths=8, horizon_years=[5], seed=1)
    raw = lab.montecarlo(allow_price_benchmark=True, **kw)
    tr = lab.montecarlo(benchmark=IndexBenchmark("IDX", dividend_yield=0.02), **kw)
    assert tr.index_label == "IDX +2.0% div (approx. TR)"
    a = raw.per_path[raw.per_path.portfolio == "IDX"]["cagr"].to_numpy()
    b = tr.per_path[tr.per_path.portfolio == tr.index_label]["cagr"].to_numpy()
    np.testing.assert_allclose((1 + b) / (1 + a), 1.02, rtol=2e-3)
    with pytest.raises(BenchmarkKindError):
        lab.montecarlo(**kw)


# --------------------------------------------------------------------------- REST / MCP
@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient
    from screenlab.server.registry import LabRegistry
    from screenlab.server.rest import create_app
    reg = LabRegistry({"pricey": lambda: Lab.from_dataset(_syn("price"))}, default="pricey")
    return TestClient(create_app(reg)), reg


def test_rest_refuses_with_400_and_the_same_message(client):
    c, _ = client
    r = c.post("/labs/pricey/backtest", json={"screens": ["dogs"]})
    assert r.status_code == 400
    msg = r.json()["error"]
    assert "price index" in msg and "dividend_yield" in msg and "allow_price_benchmark" in msg
    for path, body in (("timing", {"screen": "dogs", "stride": 30}), ("significance", {"screens": ["dogs"]}),
                       ("sweep", {"screen": "dogs", "grid": {"dogs.n": [5, 10]}}),
                       ("montecarlo", {"screens": ["dogs"], "n_paths": 5, "horizon_years": [1]})):
        rr = c.post(f"/labs/pricey/{path}", json=body)
        assert rr.status_code == 400 and "allow_price_benchmark" in rr.json()["error"], path


def test_rest_dict_benchmark_and_allow_flag(client):
    c, _ = client
    r = c.post("/labs/pricey/backtest", json={"screens": ["dogs"], "benchmark": {
        "index": "SYN_CAPWT", "dividend_yield": 0.02, "mer": 0.0, "cost_bps": 0}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["benchmark"] == "SYN_CAPWT +2.0% div (approx. TR)"
    assert j["data_card"]["benchmark_kind"] == "approx_total_return" and j["data_card"]["dividend_yield"] == 0.02
    assert any("approximate total return" in w for w in j["data_card"]["warnings"])
    ok = c.post("/labs/pricey/backtest", json={"screens": ["dogs"], "allow_price_benchmark": True})
    assert ok.status_code == 200 and ok.json()["benchmark"] == "SYN_CAPWT"
    assert ok.json()["data_card"]["benchmark_kind"] == "price"
    sig = c.post("/labs/pricey/significance", json={"screens": ["dogs"], "n": 50, "benchmark": {
        "index": "SYN_CAPWT", "dividend_yield": 0.02}})
    assert sig.status_code == 200 and sig.json()["benchmark"].endswith("(approx. TR)")
    # bad objects are 400s
    assert c.post("/labs/pricey/backtest", json={"benchmark": {"dividend_yield": 0.02}}).status_code == 400
    assert c.post("/labs/pricey/backtest", json={"benchmark": {"index": "SYN_CAPWT", "dividend_yield": 5}}
                  ).status_code in (400, 422)
    assert c.post("/labs/pricey/backtest", json={"benchmark": 3}).status_code in (400, 422)
    assert c.post("/labs/pricey/backtest", json={"screens": ["dogs"], "benchmark": "SYN_CAPWT"}).status_code == 400


def test_mcp_backtest_tool_takes_the_dict_and_the_flag(client):
    from screenlab.server.mcp_server import build_tools
    _, reg = client
    t = build_tools(reg)
    out = t["backtest"](screens=["dogs"], benchmark={"index": "SYN_CAPWT", "dividend_yield": 0.02})
    assert out["benchmark"].endswith("(approx. TR)")
    assert t["backtest"](screens=["dogs"], allow_price_benchmark=True)["benchmark"] == "SYN_CAPWT"
    with pytest.raises(Exception, match="allow_price_benchmark"):
        t["backtest"](screens=["dogs"])


def test_index_benchmark_round_trips_through_the_run_spec_encoding():
    from screenlab.serial import from_dict, to_dict
    b = IndexBenchmark("SP500", mer=0.001, dividend_yield=0.02, kind="price")
    assert from_dict(to_dict(b)) == b
    assert to_dict(IndexBenchmark("SP500")) == {"__t": "IndexBenchmark", "index": "SP500"}     # old keys unchanged


def test_cli_refuses_a_price_benchmark_and_accepts_the_fixes(capsys):
    pytest.importorskip("skfolio")
    from screenlab import run
    base = ["--data", "sp500", "--only", "low_vol", "--rebalance", "12", "--benchmark", "SP500"]
    with pytest.raises(SystemExit, match="--bench-dividend-yield"):
        run.main(base)
    assert run.main([*base, "--bench-dividend-yield", "0.02"]).benchmark_label == "SP500 +2.0% div (approx. TR)"
    assert run.main([*base, "--allow-price-benchmark"]).benchmark_label == "SP500"
