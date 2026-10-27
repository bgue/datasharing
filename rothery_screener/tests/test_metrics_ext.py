"""Extended return metrics: closed-form checks on constant-growth curves plus a synthetic backtest.

    python -m pytest rothery_screener/tests/test_metrics_ext.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rothery_screener import Lab
from rothery_screener.macro import MacroSeries, accrual_curve, cpi_deflator
from rothery_screener.metrics_ext import decompose, extended_summary

IDX = {"D": pd.bdate_range("2010-01-01", "2019-12-31"),
       "M": pd.date_range("2010-01-31", "2019-12-31", freq="ME")}


def _t(idx):
    return (idx - idx[0]).days.to_numpy() / 365.25


def _macro(idx):
    t = _t(idx)
    return MacroSeries.from_frame(pd.DataFrame({
        "cpi": 100 * 1.02 ** t, "fx": 1.25 * 1.015 ** t, "prime": 0.05, "rf": 0.03}, index=idx))


@pytest.fixture(params=["D", "M"])
def setup(request):
    idx = IDX[request.param]
    return pd.Series(100 * 1.10 ** _t(idx), index=idx), _macro(idx), _t(idx)[-1]


def test_closed_form(setup):
    curve, mac, T = setup
    s = extended_summary(curve, mac)
    assert s["CAGR"] == pytest.approx(0.10)
    assert s["Inflation"] == pytest.approx(0.02)
    assert s["RealCAGR"] == pytest.approx(1.10 / 1.02 - 1)
    assert s["RealMaxDD"] == pytest.approx(0, abs=1e-12)
    assert s["RealWorst12m"] == pytest.approx(1.10 / 1.02 - 1, abs=2e-3)
    assert s["CAGR_home"] == pytest.approx(1.10 * 1.015 - 1)
    assert s["FX_contrib"] == pytest.approx(0.015)
    assert s["NetOfFinancingCAGR"] == pytest.approx(1.10 / 1.05 - 1)
    assert s["BreakevenSpread"] == pytest.approx(0.05, abs=1e-9)
    assert s["YearsAboveHurdle"] == 1.0
    assert s["ExcessOverRf"] == pytest.approx(0.07)
    assert "LeveredCAGR" not in s                 # leverage 1 -> no levered curve


def test_spread_and_breakeven(setup):
    curve, mac, T = setup
    s = extended_summary(curve, mac, spread_over_prime=0.02)
    assert s["NetOfFinancingCAGR"] == pytest.approx(1.10 / 1.07 - 1)
    assert s["BreakevenSpread"] == pytest.approx(0.05, abs=1e-9)    # independent of the spread paid


def test_leverage(setup):
    curve, mac, T = setup
    s = extended_summary(curve, mac, leverage=2.0)
    assert s["LeveredCAGR"] == pytest.approx((2 * 1.10 ** T - 1.05 ** T) ** (1 / T) - 1)
    assert s["LeveredMaxDD"] == pytest.approx(0, abs=1e-12)


def test_leverage_wipeout():
    idx = IDX["M"]
    curve = pd.Series(np.where(_t(idx) < 5, 1.0, 0.3), index=idx)
    s = extended_summary(curve, _macro(idx), leverage=3.0)
    assert s["LeveredCAGR"] == -1.0 and s["LeveredMaxDD"] == -1.0


def test_missing_series_omitted(setup):
    curve, mac, _ = setup
    only_cpi = MacroSeries.from_frame(mac.frame[["cpi"]])
    s = extended_summary(curve, only_cpi, leverage=2.0)
    assert "RealCAGR" in s
    for k in ("CAGR_home", "NetOfFinancingCAGR", "BreakevenSpread", "LeveredCAGR", "ExcessOverRf", "SharpeRf"):
        assert k not in s
    assert not any(isinstance(v, float) and np.isnan(v) for k, v in s.items() if k not in ("Sharpe",))
    d = decompose(curve, only_cpi)
    assert set(d.index.get_level_values(0)) == {"nominal"}


def test_decompose_multiplies_back(setup):
    curve, mac, T = setup
    d = decompose(curve, mac, spread_over_prime=0.01)
    g = d["Growth"]
    assert g["nominal", "real"] * g["nominal", "inflation"] == pytest.approx(g["nominal", "nominal"])
    assert g["home", "local"] * g["home", "fx"] == pytest.approx(g["home", "home"])
    assert g["net", "net"] * g["net", "financing"] == pytest.approx(g["net", "nominal"])
    assert d.loc[("nominal", "inflation"), "CAGR"] == pytest.approx(0.02)
    assert d.loc[("home", "fx"), "CAGR"] == pytest.approx(0.015)
    assert d.loc[("net", "financing"), "CAGR"] == pytest.approx(0.06)
    assert d.loc[("net", "nominal"), "Growth"] == pytest.approx(1.10 ** T)


def test_alignment_and_helpers():
    idx = pd.bdate_range("2020-01-01", "2020-03-31")
    long = pd.DataFrame({"date": ["2019-12-31", "2020-02-03"], "series": ["rf", "rf"], "value": [0.01, 0.02]})
    m = MacroSeries.from_frame(long.pivot(index="date", columns="series", values="value").set_axis(
        pd.to_datetime(["2019-12-31", "2020-02-03"])), idx)
    assert m.rf.loc["2020-01-02"] == 0.01 and m.rf.loc["2020-02-03"] == 0.02   # pre-index obs carries in
    a = accrual_curve(pd.Series(0.05, index=idx))
    assert a.iloc[-1] == pytest.approx(1.05 ** ((idx[-1] - idx[0]).days / 365.25))
    assert cpi_deflator(pd.Series([2.0, 3.0], index=idx[:2])).iloc[-1] == 1.5


def test_from_csv_and_boc(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("date,series,value\n2020-01-01,V80691311,3.95\n2020-01-01,FXUSDCAD,1.30\n"
                 "2020-01-01,V41690973,100\n2020-02-01,FXUSDCAD,1.32\n2020-02-01,other,7\n")
    b = MacroSeries.from_boc_csv(p)
    assert b.prime.iloc[0] == pytest.approx(0.0395) and b.fx.iloc[-1] == 1.32 and b.cpi.iloc[0] == 100
    assert "other" in b.names
    c = MacroSeries.from_csv(p)
    assert c.get("V80691311").iloc[0] == 3.95
    a = b.align(pd.bdate_range("2020-01-01", "2020-03-02"))
    assert a.fx.loc["2020-02-20"] == 1.32 and a.prime.loc["2020-03-02"] == pytest.approx(0.0395)


def test_statsmodels_constructor():
    m = MacroSeries.from_statsmodels_macrodata()
    assert m.frame.index[0] == pd.Timestamp("1959-03-31")
    assert m.rf.iloc[0] == pytest.approx(0.0282) and 0 < m.rf.max() < 0.2     # tbilrate percent -> decimal
    assert m.cpi.iloc[0] == pytest.approx(28.98)
    idx = pd.bdate_range("1990-01-01", "1990-12-31")
    a = m.align(idx)
    assert a.cpi.loc["1990-02-15"] == m.cpi.loc["1989-12-31"]                  # pad-filled


def test_synthetic_backtest():
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    rep = lab.backtest()
    curve = rep.curves.drop(columns=[rep.benchmark_label] if hasattr(rep, "benchmark_label") else [],
                            errors="ignore").iloc[:, 0].dropna()
    idx = curve.index
    t = _t(idx)
    mac = MacroSeries.from_frame(pd.DataFrame({
        "cpi": 100 * 1.025 ** t, "fx": 1.3 * np.exp(0.02 * t + 0.05 * np.sin(t)),
        "prime": 0.045 + 0.01 * np.sin(t / 2), "rf": 0.02 + 0.005 * np.cos(t)}, index=idx))
    s = extended_summary(curve, mac, spread_over_prime=0.01, leverage=1.5)
    for k in ("CAGR", "RealCAGR", "RealMaxDD", "CAGR_home", "FX_contrib", "NetOfFinancingCAGR",
              "BreakevenSpread", "LeveredCAGR", "LeveredMaxDD", "ExcessOverRf", "SharpeRf"):
        assert np.isfinite(s[k]), k
    assert s["RealCAGR"] == pytest.approx((1 + s["CAGR"]) / 1.025 - 1, abs=1e-9)
    assert s["RealMaxDD"] <= 0 and 0 <= s["YearsAboveHurdle"] <= 1
    d = decompose(curve, mac)
    g = d["Growth"]
    assert g["net", "net"] * g["net", "financing"] == pytest.approx(g["net", "nominal"])
    print({k: round(float(v), 4) for k, v in s.items()})
