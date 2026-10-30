"""D1: the real US dataset (`Lab.real_us`), its committed mirrors, the `mirror` fetch source and the per-year index yield.
Offline: the mirrors are committed files, the 20 names come from the installed skfolio wheel.

    python -m pytest screenlab/tests/test_real_us.py -q
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab
from screenlab.dataset import _cagr
from screenlab.realdata import cli, fetch
from screenlab.taxes import CanadianTax, TaxConfig, tax_buy_and_hold

pytest.importorskip("skfolio")


@pytest.fixture(scope="module")
def lab() -> Lab:
    return Lab.real_us()


def test_real_us_spans_33_years_with_a_real_total_return_benchmark(lab):
    c = lab.data_card()
    yrs = (pd.Timestamp(c.end) - pd.Timestamp(c.start)).days / 365.25
    assert yrs >= 25 and c.start == "1990-01-02" and c.end == "2022-12-28" and c.n_names == 20
    assert c.benchmark == "SP500_TR" and c.benchmark_kind == "total_return" and c.name == "real_us"
    tr = lab.market.indices["SP500_TR"]
    assert 0.09 <= _cagr(tr) <= 0.11                                   # Shiller TR 1990-2022 is about 9.8%
    assert _cagr(tr) > _cagr(lab.market.indices["SP500"]) + 0.015      # the dividends are in
    assert lab.market.dataset.kind_of("SP500") == "price"
    assert c.survivorship_score > 0.02                                 # 20 survivors beat the index


def test_real_us_macro_covers_the_span(lab):
    fx = lab.macro.get("fx")
    assert fx is not None and fx.first_valid_index() <= pd.Timestamp("1990-01-02") and fx.last_valid_index() >= pd.Timestamp("2022-12-28")
    sub = fx.loc["1990-01-02":"2022-12-28"].dropna()
    assert len(sub) > 8000 and sub.index.to_series().diff().dt.days.max() <= 5 and 0.9 < sub.min() and sub.max() < 1.7
    assert lab.macro.align(lab.market.index).get("fx").notna().all()
    assert set(lab.macro.names) == {"fx", "cpi", "y10"}
    assert lab.macro.get("y10").dropna().between(0, 0.2).all()          # percent -> decimal


def test_real_us_card_warnings(lab):
    w = " | ".join(lab.data_card().warnings())
    assert "survivors" in w and "+10 points a year" in w and "index row is bias-free" in w
    assert "ADJUSTED" in w and "dividend stream" in w and "approximation" in w
    assert "no fundamentals" in w and "momentum, low volatility" in w and "until the owner loads fundamentals" in w
    assert "real S&P 500 total return" in w and "Shiller" in w
    assert "none" in " | ".join(Lab.real_us(dividend_mode="none").data_card().warnings()).split("Dividend mode")[1][:8]


def test_real_us_security_meta(lab):
    m = lab.market.dataset.security_meta
    assert set(m["domicile"]) == {"US"} and set(m["currency"]) == {"USD"} and set(m["wrapper"]) == {"stock"}
    assert not m["eligible_dividend"].any()
    assert lab.data_card().foreign_share == 1.0


def test_dividend_modes():
    a = Lab.real_us(dividend_mode="index_yield").market.fund
    b = Lab.real_us(dividend_mode="none").market.fund
    assert a["div_yield"].between(0.005, 0.05).all() and len(a) > 20 * 300
    assert "dividend" not in b or b["dividend"].isna().all()
    with pytest.raises(ValueError):
        Lab.real_us(dividend_mode="bogus")
    sub = Lab.real_us(start="2000-01-01", end="2010-12-31")
    assert sub.data_card().start == "2000-01-03" and sub.data_card().end == "2010-12-31"


def test_backtest_and_account_compare_run(lab):
    rep = lab.backtest(["momentum_12m"])
    text = str(rep)
    assert "momentum_12m" in text and "SP500_TR" in text
    tx = TaxConfig(model=CanadianTax("AB", other_income=90_000), macro=lab.macro, rrsp_withdrawal_rate=0.30)
    ac = lab.account_compare(["momentum_12m"], tax=tx)
    t = ac.table
    idx = t[t["kind"] == "index"].set_index("account")
    assert set(idx.index) == {"taxable", "tfsa", "rrsp"}
    assert idx.loc["taxable", "TaxDrag/yr"] > 0.003                    # the index pays tax on its real ~2% yield
    assert idx.loc["tfsa", "WHLost/yr"] > 0.001                         # US withholding lost in a TFSA (foreign share 100%)
    none = Lab.real_us(dividend_mode="none").account_compare(["momentum_12m"], tax=tx).table
    p = lambda tab, a: tab[(tab["kind"] == "portfolio") & (tab["account"] == a)].iloc[0]       # noqa: E731
    assert p(t, "taxable")["DivTax$"] > 0 and p(none, "taxable")["DivTax$"] == 0.0


def test_price_screens_only(lab):
    assert set(lab.screens) == {"momentum_12_1", "momentum_6m", "momentum_12m", "low_vol"}


# --------------------------------------------------------------------------- the per-year index yield (taxes.py)
def test_tax_buy_and_hold_accepts_a_yield_by_year():
    idx = pd.bdate_range("2020-01-01", "2023-12-29")
    curve = pd.Series(np.linspace(1.0, 1.5, len(idx)), index=idx)
    tx = TaxConfig(model=CanadianTax("AB", other_income=90_000), account="taxable")
    flat, _ = tax_buy_and_hold(curve, 0.02, tx, 100_000)
    same, _ = tax_buy_and_hold(curve, {y: 0.02 for y in range(2020, 2024)}, tx, 100_000)
    assert same["dividends_received"].sum() == pytest.approx(flat["dividends_received"].sum()) if "dividends_received" in flat \
        else same.iloc[:, 0].sum() == pytest.approx(flat.iloc[:, 0].sum())
    hi, _ = tax_buy_and_hold(curve, pd.Series({2020: 0.01, 2021: 0.01, 2022: 0.04, 2023: 0.04}), tx, 100_000)
    assert hi.iloc[:, 0].sum() > flat.iloc[:, 0].sum() * 0.99          # more income in the later, bigger years


# --------------------------------------------------------------------------- committed mirrors
MIRROR_DIR = fetch.MIRROR_DIR


def test_committed_mirrors_exist_and_have_the_documented_shape():
    s = pd.read_csv(MIRROR_DIR / "shiller_sp500_monthly.csv", parse_dates=["date"])
    assert list(s.columns) == ["date", "sp500", "dividend", "earnings", "cpi", "long_rate"]
    assert s["date"].iloc[0] == pd.Timestamp("1871-01-01") and s["dividend"].last_valid_index() < len(s) - 1
    assert (s["dividend"].dropna() > 0).all()                           # zeros became blanks
    fx = pd.read_csv(MIRROR_DIR / "usdcad_daily.csv", parse_dates=["date"])
    assert list(fx.columns) == ["date", "usdcad"] and fx["date"].iloc[0] <= pd.Timestamp("1990-01-02")
    assert list(pd.read_csv(MIRROR_DIR / "us_cpi_monthly.csv").columns) == ["date", "cpi"]
    assert list(pd.read_csv(MIRROR_DIR / "us_10y_monthly.csv").columns) == ["date", "y10"]
    src = (MIRROR_DIR / "SOURCES.md").read_text()
    for url in (fetch.mirror_url(n) for n in fetch.MIRRORS):
        assert url in src
    assert "PDDL" in src or "Public Domain" in src


def test_mirror_dir_is_not_git_ignored():
    import subprocess
    root = fetch.Path(fetch.__file__).parents[2]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run(["git", "check-ignore", "screenlab/data/mirrors/usdcad_daily.csv", "screenlab/data/mirrors/SOURCES.md"],
                       capture_output=True, text=True, cwd=root)
    assert r.stdout.split() == []


# --------------------------------------------------------------------------- the `mirror` fetch source (monkeypatched download)
SHILLER = ("Date,SP500,Dividend,Earnings,Consumer Price Index,Long Interest Rate,Real Price,Real Dividend,Real Earnings,PE10\n"
           "2023-05-01,4136.2,68.0,180.0,304.1,3.6,1,1,1,1\n2023-06-01,4345.4,68.7,181.2,305.1,3.75,1,1,1,1\n"
           "2023-07-01,4508.1,0.0,0.0,0.0,0.0,0,0,0,0\n")
FX = "Date,Country,Exchange rate\n2024-01-02,Canada,1.33\n2024-01-02,Japan,140\n2024-01-03,Canada,\n2024-01-04,Canada,1.34\n"
CPI = "Date,Index,Inflation\n2024-01-01,308.4,0.3\n2024-02-01,310.3,0.6\n"
Y10 = "Date,Rate\n2024-01-01,4.1\n2024-02-01,4.2\n"


class _Opener:
    def __init__(self, blocked=False):
        self.urls, self.blocked = [], blocked

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        if self.blocked:
            raise OSError("Tunnel connection failed: 403 Forbidden")
        body = SHILLER if "s-and-p-500" in url else FX if "exchange-rates" in url else CPI if "cpi-us" in url else Y10
        r = io.BytesIO(body.encode())
        r.__enter__ = lambda self=r: self
        return r


def test_mirror_source_is_registered_with_its_host():
    assert "mirror" in fetch.ALL_SOURCES and "mirror" not in fetch.SOURCES
    assert fetch.SOURCE_HOSTS["mirror"] == ["raw.githubusercontent.com"]
    out = io.StringIO()
    cli.print_hosts(out)
    assert "raw.githubusercontent.com" in out.getvalue()


def test_fetch_mirrors_writes_the_four_files(tmp_path):
    op = _Opener()
    m = fetch.fetch_mirrors(tmp_path / "mir", opener=op)
    assert m.errors == [] and len(m.files) == 4 and all(u.startswith("https://raw.githubusercontent.com/datasets/") for u in op.urls)
    d = tmp_path / "mir"
    s = pd.read_csv(d / "shiller_sp500_monthly.csv")
    assert s["dividend"].tolist()[:2] == [68.0, 68.7] and s.loc[2, ["dividend", "earnings", "cpi", "long_rate"]].isna().all()
    assert s.loc[2, "sp500"] == 4508.1                                  # the price survives, the zeros do not
    fx = pd.read_csv(d / "usdcad_daily.csv")
    assert list(fx.columns) == ["date", "usdcad"] and len(fx) == 3 and fx["usdcad"].isna().sum() == 1     # Canada only
    assert pd.read_csv(d / "us_cpi_monthly.csv")["cpi"].tolist() == [308.4, 310.3]
    assert pd.read_csv(d / "us_10y_monthly.csv")["y10"].tolist() == [4.1, 4.2]


def test_fetch_free_runs_the_mirror_source(tmp_path):
    m = fetch.fetch_free(tmp_path / "data", sources=["mirror"], opener=_Opener(), mirror_dir=tmp_path / "mir")
    assert m.errors == [] and (tmp_path / "mir" / "usdcad_daily.csv").exists() and m.allow_hosts["mirror"] == ["raw.githubusercontent.com"]


def test_blocked_host_is_reported_not_raised(tmp_path):
    m = fetch.fetch_mirrors(tmp_path / "mir", opener=_Opener(blocked=True))
    assert len(m.errors) == 1 and m.errors[0]["host"] == "raw.githubusercontent.com"


def test_cli_mirrors_subcommand(tmp_path):
    out = io.StringIO()
    assert cli.main(["mirrors", "--dir", str(tmp_path / "m")], out=out, opener=_Opener()) == 0
    assert "usdcad_daily.csv" in out.getvalue() and (tmp_path / "m" / "us_10y_monthly.csv").exists()
    out = io.StringIO()
    assert cli.main(["mirrors", "--dir", str(tmp_path / "m2")], out=out, opener=_Opener(blocked=True)) == 1
    assert "raw.githubusercontent.com" in out.getvalue()


def test_makefile_data_mirrors_target():
    import shutil
    import subprocess
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    root = fetch.Path(fetch.__file__).parents[2]
    out = subprocess.run(["make", "-n", "data-mirrors"], cwd=root, capture_output=True, text=True).stdout
    assert "screenlab.realdata.cli mirrors" in out
