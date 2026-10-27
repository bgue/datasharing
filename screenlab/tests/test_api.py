"""Tests run through the public SDK (`Lab`, `ScreenBuilder`, benchmarks, Config).

Exact-arithmetic checks use a tiny hand-built market whose prices are flat or
grow at a known rate; behavioural checks use a small synthetic market.

    python -m pytest screenlab/tests -q
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from screenlab import (DEFENSIVE, Config, IndexBenchmark, Lab, Market, REGISTRY, ScreenBuilder,
                              ScreenSpec, define_screen, equal_weight_top)
from screenlab.data import add_ratios
from screenlab.screens import graham_report


# --------------------------------------------------------------------------- toy market
def _row(ticker, sector, **kw):
    base = dict(price=10.0, market_cap=1000.0, sales=2000.0, eps=1.0, eps_prev=0.9, eps_avg3=1.0,
                eps_avg3_10y_ago=0.5, loss_years_5y=0, loss_years_10y=0, bvps=8.0, dividend=0.5,
                div_years=25, current_ratio=2.5, current_ratio_prev=2.0, lt_debt=10.0,
                net_current_assets=100.0, roa=0.1, roa_prev=0.05, cfo_to_assets=0.12, leverage=0.1,
                leverage_prev=0.2, shares=100.0, shares_prev=100.0, gross_margin=0.4,
                gross_margin_prev=0.3, asset_turnover=1.0, asset_turnover_prev=0.9, ebit=100.0,
                enterprise_value=1200.0, invested_capital=500.0)
    base.update(kw)
    return {"ticker": ticker, "sector": sector, **base}


@pytest.fixture(scope="module")
def toy():
    dates = pd.bdate_range("2020-01-01", "2022-12-30")
    days = (dates - dates[0]).days.to_numpy() / 365.25
    prices = pd.DataFrame({
        "A": 1.0, "B": 1.0, "C": 1.0,                 # flat
        "D": 1.10 ** days,                            # +10%/yr
        "E": np.where(dates < "2021-01-01", 1.0, np.nan),   # flat, then delisted
        "F": 1.0,
    }, index=dates)
    indices = pd.DataFrame({"IDX": 1.10 ** days}, index=dates)
    fund = pd.DataFrame([
        _row("A", "Flat", market_cap=5000, dividend=0.6),
        _row("B", "Flat", market_cap=4000, dividend=0.4),
        _row("C", "Flat", market_cap=3000, dividend=0.2),
        _row("D", "Grow", market_cap=2000, dividend=0.0, eps=2.0),
        _row("E", "Dying", market_cap=1500, dividend=0.8),
        _row("F", "Fin", market_cap=1000, dividend=0.3, current_ratio=1.0),
    ])
    fund["date"] = dates[0]
    return Lab(Market(add_ratios(fund), prices, indices), config=Config(min_holdings=1))


def flat_three():
    return ScreenBuilder("flat3", n=3).where("sector == 'Flat'").build()


def end_value(curve):
    return curve.iloc[-1] / curve.iloc[0]


def years(curve):
    return (curve.index[-1] - curve.index[0]).days / 365.25


# --------------------------------------------------------------------------- benchmarks
def test_index_benchmark_is_default_and_frictionless(toy):
    assert isinstance(toy.benchmark, IndexBenchmark) and toy.benchmark.mer == 0 == toy.benchmark.cost_bps
    rep = toy.backtest([flat_three()], start="2020-06-01", rebalance_months=120)
    raw = toy.market.series("IDX")
    expect = raw.iloc[-1] / raw.loc[rep.benchmark.index[0]]
    assert end_value(rep.benchmark) == pytest.approx(expect)
    assert rep.table.loc["IDX", "CAGR"] == pytest.approx(0.10, abs=1e-3)


def test_index_benchmark_mer_and_cost(toy):
    b = IndexBenchmark("IDX", mer=0.01, cost_bps=10)
    c = b.curve(toy.market, toy.config, "2020-01-01")
    raw = c.index.map(lambda d: 1.10 ** ((d - c.index[0]).days / 365.25))
    assert c.iloc[0] == 1.0
    assert end_value(c) == pytest.approx(raw[-1] * 0.99 ** years(c) * (1 - 0.001))


def test_screen_benchmark_option(toy):
    rep = toy.backtest([flat_three()], benchmark=equal_weight_top(3), rebalance_months=120)
    assert rep.benchmark_label == "IDX"                 # the passive index stays primary
    c = rep.curves["bench_top3 (compare)"]              # A, B, C are the three largest -> flat
    assert end_value(c) == pytest.approx(1 - 3 * (1 / 3) * 25 / 1e4)


# --------------------------------------------------------------------------- costs / MER
def test_trading_cost_and_mer_exact(toy):
    rep = toy.backtest([flat_three()], rebalance_months=120, cost_bps=25, mer=0.01)
    c = rep.results["flat3"].curve
    assert end_value(c) == pytest.approx((1 - 0.0025) * 0.99 ** years(c))


def test_per_security_cost_and_commission(toy):
    rep = toy.backtest([flat_three()], rebalance_months=120, cost_bps={"A": 100, "_default": 0})
    assert end_value(rep.results["flat3"].curve) == pytest.approx(1 - 0.01 / 3)
    rep = toy.backtest([flat_three()], rebalance_months=120, cost_bps=0, commission=10, capital=10_000)
    assert end_value(rep.results["flat3"].curve) == pytest.approx(1 - 3 * 10 / 10_000)


def test_per_security_mer(toy):
    rep = toy.backtest([flat_three()], rebalance_months=120, cost_bps=0, mer={"B": 0.03})
    c = rep.results["flat3"].curve
    assert end_value(c) == pytest.approx((2 + 0.97 ** years(c)) / 3)


def test_delisting_haircut(toy):
    sc = ScreenBuilder("with_e", n=3).where("sector in ['Flat', 'Dying']").rank(market_cap="asc").build()
    assert toy.picks(sc, "2020-03-02").index.tolist() == ["E", "C", "B"]
    rep = toy.backtest([sc], start="2020-03-02", end="2020-12-31", rebalance_months=120, cost_bps=0)
    assert end_value(rep.results["with_e"].curve) == pytest.approx(1.0)   # E still alive
    rep = toy.backtest([sc], start="2020-03-02", rebalance_months=120, cost_bps=0, delist_return=-0.3)
    assert end_value(rep.results["with_e"].curve) == pytest.approx(1 - 0.3 / 3)


def test_config_overrides_do_not_mutate_lab(toy):
    before = toy.config
    toy.backtest([flat_three()], rebalance_months=3, cost_bps=99)
    assert toy.config is before and toy.config.cost_bps == 25


# --------------------------------------------------------------------------- screen definitions
def test_builder_filters_rank_and_take(toy):
    sc = (ScreenBuilder("yieldy").universe(top=5, exclude_sectors=["Grow"])
          .derive(yld_x2="div_yield * 2").where("yld_x2 > 0.05").rank(div_yield="desc").take(2).build())
    # top 5 by cap = A..E, drop D; yields A .06 B .04 C .02 E .08 -> x2 > .05: A, B, E
    assert toy.picks(sc, "2020-06-01").index.tolist() == ["E", "A"]
    assert toy.picks(sc).index.tolist() == ["A", "B"]          # E has since delisted


def test_composite_rank_sums_ranks(toy):
    sc = ScreenBuilder("combo", n=6).rank(market_cap="desc", div_yield="asc").build()
    # cap rank (desc): A1 B2 C3 D4 E5 F6; yield rank (asc): D1 C2 F3 B4 A5 E6
    assert toy.picks(sc, "2020-06-01").index.tolist()[:3] == ["C", "D", "A"]   # 5, 5 (stable order), 6


def test_spec_round_trip_json_dict_builder(toy, tmp_path):
    b = ScreenBuilder("rt", n=4).universe(top=6).where("pe < 20").rank("pb", div_yield="desc")
    js = b.spec.to_json()
    path = tmp_path / "rt.json"
    path.write_text(js)
    picks = [toy.picks(s).index.tolist()
             for s in (b.build(), define_screen(js), define_screen(json.loads(js)), define_screen(str(path)))]
    assert all(p == picks[0] for p in picks) and len(picks[0]) == 4


def test_define_registers_and_overrides(toy):
    toy.define({"name": "two_flat", "n": 3, "filters": ["sector == 'Flat'"]})
    assert "two_flat" in toy.screens
    assert len(toy.picks("two_flat")) == 3
    assert len(toy.picks(toy.screen("two_flat", n=2))) == 2


def test_graham_in_spec_and_toggles(toy):
    rep, funnel = toy.graham("defensive")
    assert funnel.index[0] == "universe" and "pe" in rep
    rep, _ = toy.graham("defensive", max_pe=None, min_current_ratio=None)
    assert "pe" not in rep and "current_ratio" not in rep
    strict = toy.picks(ScreenBuilder("g", n=6).graham("defensive").build()).index
    loose = toy.picks(ScreenBuilder("g2", n=6).graham("defensive", min_passes=5).build()).index
    assert "F" not in strict and set(strict) <= set(loose)


# --------------------------------------------------------------------------- synthetic market
@pytest.fixture(scope="module")
def syn():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def test_spec_reproduces_builtin_dogs_and_magic_formula(syn):
    dogs = ScreenBuilder("dogs_spec", 10).universe(top=30).where("div_yield > 0").rank(div_yield="desc")
    mf = (ScreenBuilder("mf_spec", 15).universe(top=200, exclude_sectors=["Fin", "Util"])
          .where("ebit > 0").rank(earnings_yield="desc", roic="desc"))
    for d in syn.market.index[24::30]:
        assert syn.picks(dogs.build(), d).index.tolist() == syn.picks("dogs", d).index.tolist()
        assert set(syn.picks(mf.build(), d).index) == set(syn.picks("magic_formula", d).index)


def test_builtin_graham_respects_all_tests(syn):
    s = syn.snapshot()
    picks = syn.picks("graham_defensive").index
    rep = graham_report(s, DEFENSIVE)
    assert (rep.loc[picks, "passes"] == rep.shape[1] - 1).all()


def test_backtest_report_shape(syn):
    rep = syn.backtest(["dogs", "piotroski"])
    assert list(rep.table.index) == ["SYN_CAPWT", "dogs", "piotroski"]
    assert {"CAGR", "Excess/yr", "Costs/yr", "Turnover"} <= set(rep.table.columns)
    assert rep.curves.index[0] == rep.benchmark.index[0]
    assert "benchmark SYN_CAPWT" in str(rep)


# --------------------------------------------------------------------------- timing
def test_timing_phase_mode_same_window(toy):
    sc = ScreenBuilder("d_only", 1).where("sector == 'Grow'").build()
    tr = toy.timing(sc, stride=20, start="2020-01-02", rebalance_months=6, cost_bps=0)
    c = tr.curves
    assert c.notna().all().all()                         # identical windows, no gaps
    idx, start = toy.market.index, toy.market.trading_day("2020-01-02")
    period = idx.searchsorted(start + pd.DateOffset(months=6)) - idx.get_loc(start)
    assert list(tr.table.index) == list(range(0, period, 20))
    assert tr.tranche.equals(c.ffill().mean(axis=1))
    # one holding, no costs: every phase earns D's 10%/yr exactly, so timing can't matter
    assert tr.table["CAGR"].max() - tr.table["CAGR"].min() < 1e-9
    assert tr.table.loc[0, "Excess/yr"] == pytest.approx(0, abs=1e-9)


def test_timing_offsets_cover_one_period(syn):
    tr = syn.timing("dogs", stride=1)                    # monthly data: 12 offsets per year
    assert list(tr.table.index) == list(range(12))
    base = syn.backtest(["dogs"]).results["dogs"].curve
    assert end_value(tr.curves[0]) == pytest.approx(end_value(base))
    assert tr.distribution().loc["CAGR", "range"] > 0    # with real turnover, timing matters


def test_timing_start_mode_buys_benchmark_each_start(syn):
    tr = syn.timing("dogs", mode="start", offsets=[0, 6])
    starts = [tr.curves[k].first_valid_index() for k in (0, 6)]
    assert starts[0] < starts[1]
    assert tr.table["Excess/yr"].notna().all()


# --------------------------------------------------------------------------- CLI
def test_cli_run_and_timing(tmp_path, capsys):
    from screenlab import run, timing
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps([{"name": "cli_cheap", "n": 5, "filters": ["pe < 12"], "rank": {"pb": "asc"}}]))
    rep = run.main(["--freq", "monthly", "--screen-file", str(spec), "--only", "cli_cheap,dogs",
                    "--bench-mer", "0.001", "--set", "dogs.n=5"])
    assert list(rep.table.index) == ["SYN_CAPWT", "cli_cheap", "dogs"]
    assert rep.results["dogs"].holdings and max(map(len, rep.results["dogs"].holdings.values())) == 5
    tables = timing.main(["--freq", "monthly", "--only", "dogs", "--stride", "4"])
    assert len(tables[0]) == 3
    assert "tranche blend" in capsys.readouterr().out
