"""Every result is benchmarked against a passive index; screen benchmarks are extra rows only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screenlab import (Config, ExitScreen, IndexBenchmark, Lab, Market, ScreenBuilder, TrendBreak,
                              equal_weight_top, passive_index)
from screenlab.data import add_ratios


def _market(prices: pd.DataFrame, caps: dict | None) -> Market:
    d0 = prices.index[0]
    if caps is None:
        fund = pd.DataFrame({"date": [d0] * prices.shape[1], "ticker": prices.columns, "price": 10.0})
    else:
        fund = pd.DataFrame({"date": [d0] * len(caps), "ticker": list(caps), "price": 10.0,
                             "market_cap": list(caps.values()), "eps": 1.0, "sector": "X"})
    return Market(add_ratios(fund), prices)


@pytest.fixture
def prices():
    dates = pd.bdate_range("2020-01-01", periods=300)
    t = np.arange(300)
    return pd.DataFrame({"A": 1.001 ** t, "B": 1.0 + 0 * t, "C": np.where(t < 150, 1.002 ** t, np.nan),
                         "D": 0.9995 ** t}, index=dates)


def sc():
    return ScreenBuilder("all2", n=2).build()


def test_no_indices_gets_passive_capwt(prices):
    lab = Lab(_market(prices, dict(A=400.0, B=300.0, C=200.0, D=100.0)), config=Config(min_holdings=1))
    assert lab.benchmark.index == "PASSIVE_CAPWT" and "PASSIVE_CAPWT" in lab.market.indices
    rep = lab.backtest([sc()], start=prices.index[20])
    assert rep.benchmark_label == "PASSIVE_CAPWT" and rep.table.index[0] == "PASSIVE_CAPWT"
    assert "benchmark PASSIVE_CAPWT" in str(rep)


def test_price_only_gets_passive_ew(prices):
    lab = Lab(_market(prices, None), config=Config(min_holdings=1))
    assert lab.benchmark.index == "PASSIVE_EW"
    assert lab.backtest([sc()], start=prices.index[20]).table.index[0] == "PASSIVE_EW"


def test_loaded_index_is_used_not_rebuilt():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)
    assert lab.benchmark.index == "SYN_CAPWT" and "PASSIVE_CAPWT" not in lab.market.indices


def test_passive_ew_exact():
    dates = pd.bdate_range("2021-01-04", periods=4)
    px = pd.DataFrame({"X": [1.0, 2.0, 2.0, 4.0], "Y": [1.0, 1.0, 0.5, 0.5],
                       "Z": [1.0, 1.5, np.nan, np.nan]}, index=dates)       # Z delists at 1.5
    name, s = passive_index(_market(px, None))
    assert name == "PASSIVE_EW"
    assert s.tolist() == pytest.approx([1.0, (2 + 1 + 1.5) / 3, (2 + 0.5 + 1.5) / 3, (4 + 0.5 + 1.5) / 3])


def test_passive_capwt_exact():
    dates = pd.bdate_range("2021-01-04", periods=3)
    px = pd.DataFrame({"X": [1.0, 2.0, 2.0], "Y": [1.0, 1.0, 1.5], "Z": [1.0, np.nan, np.nan]}, index=dates)
    name, s = passive_index(_market(px, dict(X=50.0, Y=30.0, Z=20.0)))
    assert name == "PASSIVE_CAPWT"
    # day 1: weights .5/.3/.2, X doubles, Y flat, Z delists at its last price (0% that day)
    r1 = 0.5 * 1.0
    # day 2: Z gone; X and Y weights 100:30 (after X's doubling and Y's flat): Y +50% on 30/130
    r2 = (100 * 0 + 30 * 0.5) / 130
    assert s.tolist() == pytest.approx([1.0, 1 + r1, (1 + r1) * (1 + r2)])


def test_screen_benchmark_is_compare_row_only(prices):
    lab = Lab(_market(prices, dict(A=400.0, B=300.0, C=200.0, D=100.0)), config=Config(min_holdings=1))
    passive = lab.backtest([sc()], start=prices.index[20])
    rep = lab.backtest([sc()], start=prices.index[20], benchmark=equal_weight_top(3))
    assert rep.benchmark_label == "PASSIVE_CAPWT"
    assert list(rep.table.index) == ["PASSIVE_CAPWT", "bench_top3 (compare)", "all2"]
    assert rep.table.loc["all2", "Excess/yr"] == pytest.approx(passive.table.loc["all2", "Excess/yr"])
    assert "bench_top3 (compare)" in rep.curves and rep.table.loc["bench_top3 (compare)", "CAGR"] == rep.table.loc["bench_top3 (compare)", "CAGR"]
    rep2 = lab.backtest([sc()], start=prices.index[20], compare=[equal_weight_top(2)])
    assert rep2.benchmark_label == "PASSIVE_CAPWT" and "bench_top2 (compare)" in rep2.table.index


def test_index_benchmark_replaces_passive(prices):
    m = _market(prices, dict(A=400.0, B=300.0, C=200.0, D=100.0))
    lab = Lab(m, config=Config(min_holdings=1))
    rep = lab.backtest([sc()], start=prices.index[20], benchmark=IndexBenchmark("A"))
    assert rep.benchmark_label == "A" and rep.table.index[0] == "A"


def test_lab_level_screen_benchmark_is_demoted(prices):
    lab = Lab(_market(prices, dict(A=400.0, B=300.0, C=200.0, D=100.0)), benchmark=equal_weight_top(3),
              config=Config(min_holdings=1))
    assert isinstance(lab.benchmark, IndexBenchmark)
    assert "bench_top3 (compare)" in lab.backtest([sc()], start=prices.index[20]).table.index


def test_exit_eval_default_target_is_passive(monkeypatch):
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8, seed=3)
    from screenlab import exit_signals
    seen = {}
    real = exit_signals.evaluate
    monkeypatch.setattr(exit_signals, "evaluate", lambda es, m, target, *a, **k: seen.update(t=target) or real(es, m, target, *a, **k))
    lab.exit_eval(ExitScreen([TrendBreak("SYN_CAPWT")]))
    assert seen["t"].equals(lab.passive().curve(lab.market, lab.config, lab.market.index[0]))

    bare = Lab(_market(pd.DataFrame({"A": np.linspace(1, 2, 80), "B": 1.0},
                                    index=pd.date_range("2018-01-31", periods=80, freq="ME")),
                       dict(A=1.0, B=1.0)))
    bare.exit_eval(ExitScreen([TrendBreak("PASSIVE_CAPWT")]))
    assert seen["t"].name == "PASSIVE_CAPWT" or seen["t"].equals(bare.market.indices["PASSIVE_CAPWT"])


def test_cli_compare_and_benchmark_top(capsys):
    from screenlab import run
    rep = run.main(["--freq", "monthly", "--only", "dogs", "--benchmark", "top20", "--compare", "top10"])
    assert rep.benchmark_label == "SYN_CAPWT"
    assert {"bench_top20 (compare)", "bench_top10 (compare)"} <= set(rep.table.index)
