"""D3: the point-in-time S&P 500 panel (`Lab.real_sp500`). The cleaning-rule and download tests are offline; the rest
need the local panel files (`make data-panel`, LOCAL USE ONLY) and are skipped without them.

    python -m pytest screenlab/tests/test_sp500_panel.py -q
"""
from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab
from screenlab.realdata import sp500_panel as sp
from screenlab.taxes import CanadianTax, TaxConfig

needs_panel = pytest.mark.skipif(not sp.available(), reason="panel files absent (make data-panel)")


# --------------------------------------------------------------------------- offline
def _frame():
    d = pd.to_datetime
    rows = [("2001-01-02", "A", 10.0, 10.0, 0.0), ("2001-01-03", "A", 10.0, 10.0, 0.2),
            ("2001-01-10", "A", 10.0, 10.0, 0.2),            # same amount 7 days later: duplicate, dropped
            ("2001-04-10", "A", 10.0, 10.0, 0.2),            # same amount 3 months later: kept
            ("2001-01-04", "B", 4.0, 4.0, 3.0),              # 75% of the close: outlier, dropped
            ("2001-01-05", "B", 4.0, 4.0, 0.1),
            ("2001-01-05", "C", 0.0, 1.0, 0.0),              # close 0: row dropped
            ("2001-01-05", "D", 5.0, np.nan, 0.0)]           # adj_close NaN: row dropped
    df = pd.DataFrame(rows, columns=["date", "ticker", "close", "adj_close", "dividends"])
    df["date"] = d(df["date"])
    return df


def test_clean_rows_rules_and_counts():
    out, st = sp.clean_rows(_frame())
    assert st["rows_bad_price"] == 2 and st["div_rows_in"] == 5
    assert st["div_dropped_outlier"] == 1 and st["div_dropped_duplicate"] == 1 and st["div_rows_kept"] == 3
    a = out[out["ticker"] == "A"].set_index("date")["dividends"]
    assert a["2001-01-03"] == 0.2 and a["2001-01-10"] == 0.0 and a["2001-04-10"] == 0.2
    assert out.loc[out["ticker"] == "B", "dividends"].tolist() == [0.0, 0.1]
    assert set(out["ticker"]) == {"A", "B"}


def test_ttm_dividends_is_a_365_day_sum():
    idx = pd.to_datetime(["2001-01-03", "2001-04-10", "2001-07-10", "2002-01-02", "2002-04-11"])
    div = pd.DataFrame({"A": [0.2, 0.2, 0.2, 0.0, 0.0]}, index=idx)
    t = sp.ttm_dividends(div)["A"]
    assert t.tolist() == pytest.approx([0.2, 0.4, 0.6, 0.6, 0.2])     # by 2002-04-11 the Jan and Apr 2001 payments have aged out


def test_member_mask_start_inclusive_end_exclusive():
    idx = pd.bdate_range("2001-01-01", "2001-01-12")
    tb = pd.DataFrame({"ticker": ["A", "B"], "start": pd.to_datetime(["2001-01-03", "2001-01-01"]),
                       "end": pd.to_datetime(["2001-01-08", None])})
    m = sp.member_mask(tb, idx, ["A", "B"])
    assert m[:, 1].all() and m[:, 0].sum() == 3 and not m[idx.get_loc("2001-01-02"), 0] and not m[idx.get_loc("2001-01-08"), 0]


def test_download_streams_hashes_and_writes_manifest(tmp_path):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []

    def opener(req, timeout=None):
        seen.append(req.full_url)
        return R(b"bytes of " + req.full_url.rsplit("/", 1)[1].encode())

    man = sp.download(tmp_path / "panel", opener=opener)
    assert [e["file"] for e in man["files"]] == list(sp.FILES) and all(u.startswith(sp.BASE_URL) for u in seen)
    on_disk = json.loads((tmp_path / "panel" / "MANIFEST.json").read_text())
    assert on_disk["files"][0]["sha256"] == __import__("hashlib").sha256(b"bytes of prices.parquet").hexdigest()
    assert "LOCAL USE ONLY" in on_disk["licence"]
    sp.download(tmp_path / "panel", opener=lambda *a, **k: pytest.fail("existing files must not be fetched again"))


def test_panel_source_is_registered_and_blocked_host_is_an_error_entry(tmp_path):
    from screenlab.realdata import fetch

    def blocked(req, timeout=None):
        raise OSError("403 blocked")

    assert "panel" in fetch.ALL_SOURCES and "github.com" in fetch.HOSTS["panel"]
    m = fetch.fetch_free(str(tmp_path), sources=("panel",), opener=blocked)
    assert m.errors and m.errors[0]["source"] == "panel" and m.errors[0]["host"] == "github.com"


def test_panel_dir_is_git_ignored():
    import subprocess
    root = sp.PANEL_DIR.parents[1]
    r = subprocess.run(["git", "check-ignore", "-q", "screenlab/data/panel/prices.parquet"], cwd=root.parent)
    assert r.returncode == 0


def test_missing_panel_is_a_clear_error(tmp_path):
    with pytest.raises(sp.PanelMissing, match="make data-panel"):
        sp.load(dir=tmp_path)


# --------------------------------------------------------------------------- the real panel
@pytest.fixture(scope="module")
def lab() -> Lab:
    return Lab.real_sp500(start="1990-01-01", end="2022-12-31")


@needs_panel
def test_span_universe_and_card(lab):
    c = lab.data_card()
    assert (pd.Timestamp(c.end) - pd.Timestamp(c.start)).days / 365.25 >= 25
    assert c.name == "sp500_panel" and c.benchmark_kind == "total_return" and c.delisted_included and c.n_delisted > 100
    assert abs(c.survivorship_score) < 0.02                         # point-in-time membership: no hindsight bias
    w = " | ".join(c.warnings())
    for s in ("local use only: unlicensed mixed-source panel",
              "no market cap / fundamentals", "dividend outliers filtered", "source mix by era", "delisting haircut"):
        assert s in w
    n = len(lab.market.universe.members(pd.Timestamp("2000-06-30")))
    assert 480 <= n <= 520
    m = lab.market.dataset.security_meta
    assert set(m["domicile"]) == {"US"} and set(m["currency"]) == {"USD"} and not m["eligible_dividend"].any()


@needs_panel
def test_dividend_yield_median_2010(lab):
    f = lab.market.fund
    y = f[(f["date"].dt.year == 2010) & (f["dividend"] > 0)]["div_yield"]
    assert 0.01 <= y.median() <= 0.03
    assert f["div_yield"].dropna().median() < 0.02 and f["dividend"].dropna().min() >= 0.0
    assert lab.market.dataset.card.notes and "dividend outliers filtered: " in " ".join(lab.data_card().warnings())
    assert f["market_cap"].isna().all()


@needs_panel
def test_leavers_stay_priced_and_delisted_names_end(lab):
    px = lab.market.prices
    uni = lab.market.universe
    before, after = pd.Timestamp("2008-09-12"), pd.Timestamp("2008-09-30")
    gone = set(uni.members(before)) - set(uni.members(after))
    assert gone                                                      # names left the index in that window
    assert all(c in px.columns for c in gone)                        # but remain priced (sellable)
    last = px.apply(lambda c: c.last_valid_index())
    assert (last < px.index[-1]).sum() > 100                         # delisted names: prices end


@needs_panel
def test_backtest_and_account_compare_after_tax(lab, capsys):
    rep = lab.backtest(["momentum_12m", "stable_high_yield", "dogs"], start="1990-01-01", end="2022-12-31")
    print(rep)
    text = str(rep)
    for k in ("momentum_12m", "stable_high_yield", "dogs", "SP500_"):
        assert k in text
    tx = TaxConfig(model=CanadianTax("AB", other_income=90_000), macro=lab.macro, rrsp_withdrawal_rate=0.30)
    ac = lab.account_compare(["dogs"], tax=tx)
    t = ac.table
    assert {"taxable", "tfsa", "rrsp"} <= set(t["account"])
    for col in ("TaxDrag/yr", "DivTax$"):
        assert col in t.columns
    p = t[(t["kind"] == "portfolio") & (t["account"] == "taxable")].iloc[0]
    assert p["DivTax$"] > 0                                          # real per-name dividends are taxed


@needs_panel
def test_monthly_frequency_and_cache(tmp_path):
    a = sp.load("2015-01-01", "2019-12-31", freq="ME")
    b = sp.load("2015-01-01", "2019-12-31", freq="ME")
    assert b.stats.get("from_cache") and a.prices.shape == b.prices.shape
    assert a.prices.index.to_series().dt.is_month_end.mean() > 0.6   # month-end bars (last trading day)
    assert len(a.prices) in (59, 60)
