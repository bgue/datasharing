"""Real-data adapters: bundled datasets, Bank of Canada Valet and SEC FSDS (fixtures only, no network).

    python -m pytest rothery_screener/tests/test_realdata.py -q
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from rothery_screener.data import Market, add_ratios
from rothery_screener.realdata import boc_valet, sec_fsds
from rothery_screener.realdata.bundled import SECTORS, macro_us, sp500_lab, sp500_prices


# --------------------------------------------------------------------------- bundled
@pytest.fixture(scope="module")
def lab():
    return sp500_lab()


def test_bundled_prices_shape():
    p = sp500_prices()
    assert p.shape == (8313, 20) and set(p.columns) == set(SECTORS)
    assert p.index[0] == pd.Timestamp("1990-01-02") and p.index[-1] == pd.Timestamp("2022-12-28")


def test_macro_us():
    m = macro_us()
    assert list(m.columns) == ["cpi", "tbilrate"] and len(m) == 203
    assert m.index[0] == pd.Timestamp("1959-03-31") and m.index[-1] == pd.Timestamp("2009-09-30")
    assert m["cpi"].iloc[0] == pytest.approx(28.98)


def test_add_ratios_tolerates_missing_columns():
    f = pd.DataFrame({"ticker": ["A"], "price": [10.0], "eps": [2.0]})
    out = add_ratios(f)
    assert out["pe"].iloc[0] == 5.0
    assert np.isnan(out["pb"].iloc[0]) and np.isnan(out["div_yield"].iloc[0])
    assert "f_score" not in out


def test_view_with_nan_market_cap(lab):
    v = lab.snapshot("2010-06-30")
    assert len(v) == 20 and v["market_cap"].isna().all()
    assert v["price"].notna().all() and v["pe"].isna().all()
    assert v.loc["AAPL", "sector"] == "Tech"
    assert v["mom_12_1"].notna().all()


def test_price_marked_to_market(lab):
    p = sp500_prices()
    d = pd.Timestamp("2010-06-30")
    v = lab.snapshot(d)
    assert v.loc["KO", "price"] == pytest.approx(p.loc[d, "KO"])


def test_screens_pick_expected_names(lab):
    d = lab.market.trading_day("2005-06-30")
    s = lab.snapshot(d)
    assert lab.picks("momentum_12_1", d, columns=("mom_12_1",)).index.tolist() == \
        s["mom_12_1"].nlargest(5).index.tolist()
    assert lab.picks("low_vol", d, columns=("sector",)).index.tolist() == s["vol_1y"].nsmallest(5).index.tolist()
    # names without 12 months of history are not eligible (AMD etc. all have history, so cut the lab short)
    early = lab.picks("momentum_12_1", "1990-06-01", columns=("sector",))
    assert early.empty


def test_backtest_runs_on_real_data(lab):
    rep = lab.backtest(["momentum_12_1", "low_vol"], start="1995-01-03")
    assert set(rep.table.index) == {"SP500", "momentum_12_1", "low_vol"}
    assert rep.table.loc["low_vol", "Vol"] < rep.table.loc["momentum_12_1", "Vol"]
    assert rep.table.loc["low_vol", "Vol"] < 0.30 and rep.table["CAGR"].notna().all()


# --------------------------------------------------------------------------- Bank of Canada
VALET = {"terms": {"url": "x"}, "seriesDetail": {}, "observations": [
    {"d": "2024-01-02", "FXUSDCAD": {"v": "1.3316"}, "V39079": {"v": "5.00"}},
    {"d": "2024-01-03", "FXUSDCAD": {"v": "1.3350"}, "V39079": {"v": ""}},
    {"d": "2024-01-04", "FXUSDCAD": {"v": "1.3400"}},
]}


def test_boc_load_and_macro_csv(tmp_path):
    p = tmp_path / "valet.json"
    p.write_text(json.dumps(VALET))
    df = boc_valet.load(p)
    assert df.index.tolist() == list(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    assert df["FXUSDCAD"].tolist() == [1.3316, 1.3350, 1.3400]
    assert df["V39079"].iloc[0] == 5.0 and df["V39079"].iloc[1:].isna().all()
    out = boc_valet.to_macro_csv(df, tmp_path / "macro.csv")
    long = pd.read_csv(out)
    assert list(long.columns) == ["date", "series", "value"] and len(long) == 4
    assert long[(long.series == "V39079")].shape[0] == 1


# --------------------------------------------------------------------------- SEC FSDS
def _sub(rows):
    return pd.DataFrame(rows, columns=["adsh", "cik", "name", "sic", "form", "period", "fy", "fp", "filed"])


def _num(rows):
    return pd.DataFrame(rows, columns=["adsh", "tag", "version", "ddate", "qtrs", "uom", "value"])


def _facts(adsh, ddate, **v):
    """num rows for one filing; v maps tag -> value (flow tags qtrs=4, balance sheet qtrs=0)."""
    inst = {"StockholdersEquity", "AssetsCurrent", "LiabilitiesCurrent", "LongTermDebtNoncurrent", "Assets"}
    out = []
    for tag, val in v.items():
        uom = "USD/shares" if tag.startswith(("EarningsPer", "CommonStockDividends")) else \
            "shares" if tag.startswith("WeightedAverage") else "USD"
        out.append((adsh, tag, "us-gaap/2023", ddate, 0 if tag in inst else 4, uom, str(val)))
    return out


@pytest.fixture(scope="module")
def sec():
    sub = _sub([
        ("a-23", "0000000001", "ALPHA CORP", "3571", "10-K", "20231231", "2023", "FY", "20240215"),
        ("a-22", "0000000001", "ALPHA CORP", "3571", "10-K", "20221231", "2022", "FY", "20230215"),
        ("a-q1", "0000000001", "ALPHA CORP", "3571", "10-Q", "20240331", "2024", "Q1", "20240501"),
        ("b-23", "0000000002", "BETA INC", "2834", "10-K", "20230630", "2023", "FY", "20230820"),
        ("z-23", "0000000003", "UNMAPPED", "1000", "10-K", "20231231", "2023", "FY", "20240301"),
    ])
    num = _num(
        _facts("a-23", "20231231", Revenues=1000, EarningsPerShareDiluted=2.0, StockholdersEquity=500,
               WeightedAverageNumberOfDilutedSharesOutstanding=100, AssetsCurrent=300,
               LiabilitiesCurrent=150, LongTermDebtNoncurrent=100, NetIncomeLoss=200, Assets=1000,
               NetCashProvidedByUsedInOperatingActivities=250, CommonStockDividendsPerShareDeclared=0.5,
               OperatingIncomeLoss=260, GrossProfit=400)
        + _facts("a-23", "20221231", Revenues=900, EarningsPerShareDiluted=1.5, WeightedAverageNumberOfDilutedSharesOutstanding=105,
                 AssetsCurrent=200, LiabilitiesCurrent=160, LongTermDebtNoncurrent=120, NetIncomeLoss=150,
                 Assets=800, GrossProfit=300)
        + _facts("a-22", "20221231", SalesRevenueNet=900, EarningsPerShareDiluted=1.5, NetIncomeLoss=150)
        + _facts("a-22", "20211231", EarningsPerShareDiluted=-1.0, NetIncomeLoss=-80)
        + _facts("a-22", "20201231", EarningsPerShareDiluted=0.5, NetIncomeLoss=40)
        + _facts("b-23", "20230630", Revenues=50, NetIncomeLoss=-5, Assets=100)
        + _facts("z-23", "20231231", Revenues=1)
        + [("a-q1", "Revenues", "us-gaap/2023", "20240331", "1", "USD", "999")]
    )
    return sub, num


def test_sec_to_fundamentals(sec):
    sub, num = sec
    f = sec_fsds.to_fundamentals(sub, num, {1: "ALP", 2: "BET"})
    assert list(f.columns) == sec_fsds.OUT_COLS
    assert f[["ticker", "date"]].values.tolist() == [["ALP", pd.Timestamp("2023-02-15")],
                                                      ["BET", pd.Timestamp("2023-08-20")],
                                                      ["ALP", pd.Timestamp("2024-02-15")]]
    a = f[(f.ticker == "ALP") & (f.date == "2024-02-15")].iloc[0]
    assert a["sales"] == 1000 and a["eps"] == 2.0 and a["eps_prev"] == 1.5
    assert a["bvps"] == 5.0 and a["current_ratio"] == 2.0 and a["current_ratio_prev"] == 1.25
    assert a["net_current_assets"] == 150 and a["lt_debt"] == 100
    assert a["roa"] == pytest.approx(0.2) and a["roa_prev"] == pytest.approx(150 / 800)
    assert a["cfo_to_assets"] == pytest.approx(0.25)
    assert a["leverage"] == pytest.approx(0.1) and a["leverage_prev"] == pytest.approx(0.15)
    assert a["gross_margin"] == pytest.approx(0.4) and a["gross_margin_prev"] == pytest.approx(300 / 900)
    assert a["asset_turnover"] == pytest.approx(1.0) and a["asset_turnover_prev"] == pytest.approx(900 / 800)
    assert a["shares"] == 100 and a["shares_prev"] == 105 and a["dividend"] == 0.5 and a["ebit"] == 260
    assert a["invested_capital"] == 600
    assert a["eps_avg3"] == pytest.approx((2.0 + 1.5 - 1.0) / 3)
    assert np.isnan(a["eps_avg3_10y_ago"]) and np.isnan(a["market_cap"]) and np.isnan(a["div_years"])


def test_sec_is_point_in_time_and_counts_losses(sec):
    sub, num = sec
    f = sec_fsds.to_fundamentals(sub, num, {1: "ALP", 2: "BET"})
    old = f[(f.ticker == "ALP") & (f.date == "2023-02-15")].iloc[0]
    assert old["eps"] == 1.5 and np.isnan(old["roa_prev"])   # FY2021 assets unseen
    assert old["eps_prev"] == -1.0                       # 2021 EPS came from the same filing
    assert old["sales"] == 900                           # SalesRevenueNet alias
    # losses need every year in the window: only 3 years of net income seen, so 5y/10y are NaN
    assert np.isnan(old["loss_years_5y"]) and np.isnan(old["loss_years_10y"])
    b = f[f.ticker == "BET"].iloc[0]
    assert b["sales"] == 50 and b["roa"] == pytest.approx(-0.05) and np.isnan(b["eps"])


def test_sec_loss_year_count():
    cik, ni = "0000000009", [-1, 5, -2, 3, 4]       # newest first: fiscal 2023..2019
    sub = _sub([("x", cik, "X", "1", "10-K", "20231231", "2023", "FY", "20240301")])
    rows = []
    for k, v in enumerate(ni):
        rows += _facts("x", f"{2023 - k}1231", NetIncomeLoss=v)
    f = sec_fsds.to_fundamentals(sub, _num(rows), {9: "X"})
    assert f["loss_years_5y"].iloc[0] == 2 and np.isnan(f["loss_years_10y"].iloc[0])


def test_sec_output_feeds_add_ratios(sec):
    sub, num = sec
    f = sec_fsds.to_fundamentals(sub, num, {1: "ALP"})
    f["price"] = 20.0
    r = add_ratios(f)
    assert r.loc[r.date == "2024-02-15", "pe"].iloc[0] == 10.0
