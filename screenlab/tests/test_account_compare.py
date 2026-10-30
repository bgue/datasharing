"""The `account_compare` operation (taxable vs TFSA vs RRSP), its REST / MCP / job exposure, the TUI Accounts tab and the
notebook that uses it. Offline: the synthetic Canadian market and the bundled S&P 500 sample.

    python -m pytest screenlab/tests/test_account_compare.py -q
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from screenlab import Lab
from screenlab.benchmarks import IndexBenchmark
from screenlab.data import add_ratios
from screenlab.dataset import BundledSP500Builder, Dataset
from screenlab.errors import ServerError
from screenlab.operations import OPERATIONS
from screenlab.ops.account_compare import ACCOUNTS, FOREIGN_COLS, METRIC_COLS, treatment
from screenlab.realdata import bundled
from screenlab.taxes import CanadianTax, TaxConfig

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "accounts_and_foreign.ipynb"


def tax(rate=0.30, **kw) -> TaxConfig:
    return TaxConfig(model=CanadianTax("AB", other_income=90_000), account="taxable", rrsp_withdrawal_rate=rate, **kw)


@pytest.fixture(scope="module")
def syn() -> Lab:
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


@pytest.fixture(scope="module")
def us_lab() -> Lab:
    """The bundled 20 US names with an assumed 2% yield (a constant `dividend = 2% of price`, as in the notebook)."""
    ds = BundledSP500Builder().build()
    q = ds.prices.resample("QE").last().dropna(how="all")
    rows = [dict(date=d, ticker=t, price=p, sector=bundled.SECTORS[t], market_cap=np.nan, dividend=0.02 * p)
            for d, r in q.iterrows() for t, p in r.dropna().items()]
    ds2 = Dataset.from_frames(add_ratios(pd.DataFrame(rows)), ds.prices, ds.indices, name="sp500_bundled+2%div",
                              source="test", index_kinds={"SP500": "price"}, pit=None, currency="USD",
                              security_meta=ds.security_meta)
    return Lab.from_dataset(ds2, screens=bundled.price_screens(5),
                            benchmark=IndexBenchmark("SP500", dividend_yield=0.02))


@pytest.fixture(scope="module")
def syn_rep(syn):
    return syn.account_compare(["stable_dividend"], tax=tax(), rebalance_months=12)


@pytest.fixture(scope="module")
def us_rep(us_lab):
    return us_lab.account_compare(["momentum_12_1"], tax=tax(), rebalance_months=12, start="2010-01-04", end="2015-12-31")


def row(rep, name, account) -> pd.Series:
    return rep.table.loc[f"{name} | {account}"]


# ------------------------------------------------------------------ result shape
def test_result_shape_and_registry(syn_rep):
    rep = syn_rep
    assert set(METRIC_COLS) <= set(rep.table.columns) and list(rep.table.columns[:3]) == ["name", "kind", "account"]
    assert list(rep.table.index) == [f"{n} | {a}" for n in (rep.benchmark, "stable_dividend") for a in ACCOUNTS]
    assert list(rep.table["kind"]) == ["index"] * 3 + ["portfolio"] * 3
    assert list(rep.foreign.columns) == list(FOREIGN_COLS)
    assert OPERATIONS["account_compare"].builtin
    # the pre-tax CAGR is the same in every account; the index has no AfterTaxExcess/yr
    assert row(rep, "stable_dividend", "taxable")["CAGR"] == row(rep, "stable_dividend", "rrsp")["CAGR"]
    assert row(rep, rep.benchmark, "tfsa")["AfterTaxExcess/yr"] != row(rep, rep.benchmark, "tfsa")["AfterTaxExcess/yr"]  # NaN
    r = row(rep, "stable_dividend", "taxable")
    assert r["AfterTaxExcess/yr"] == pytest.approx(r["AfterTaxCAGR"] - row(rep, rep.benchmark, "taxable")["AfterTaxCAGR"])
    # rank: 1..3 per name; the best's gap is the lead over the next
    for name in (rep.benchmark, "stable_dividend"):
        ranks = sorted(rep.table.loc[rep.table["name"] == name, "Rank"])
        assert ranks == [1, 2, 3]
        b = rep.best[name]
        top = rep.table[(rep.table["name"] == name) & (rep.table["Rank"] == 1)].iloc[0]
        assert b["account"] == top["account"] and b["gap"] == pytest.approx(top["GapToNext"]) and b["gap"] >= 0
    assert any("not modelled" in n for n in rep.notes) and any("foreign: 0% of names" in n for n in rep.notes)
    assert "Account comparison" in str(rep) and "best for stable_dividend" in str(rep)


def test_deterministic(syn, syn_rep):
    again = syn.account_compare(["stable_dividend"], tax=tax(), rebalance_months=12)
    pd.testing.assert_frame_equal(again.table, syn_rep.table)


def test_tfsa_beats_taxable_on_a_dividend_market(syn_rep):
    rep = syn_rep
    for name in (rep.benchmark, "stable_dividend"):
        assert row(rep, name, "tfsa")["AfterTaxCAGR"] > row(rep, name, "taxable")["AfterTaxCAGR"]
        assert row(rep, name, "tfsa")["TaxDrag/yr"] == pytest.approx(0.0, abs=1e-12)     # Canadian names: nothing to lose
    p = row(rep, "stable_dividend", "taxable")
    assert p["DivTax$"] > 0 and p["GainTax$"] > 0 and p["WithdrawalTax$"] == 0
    assert rep.best["stable_dividend"]["account"] == "tfsa"
    # Canadian names: no foreign withholding anywhere
    assert rep.table[["WHLost/yr", "WHCredited/yr", "WHExempt/yr"]].abs().max().max() == pytest.approx(0.0, abs=1e-12)


# ------------------------------------------------------------------ the RRSP convention
def test_rrsp_row_applies_the_withdrawal_convention(syn, syn_rep):
    rep = syn_rep
    t = row(rep, "stable_dividend", "rrsp")
    assert t["WithdrawalTax$"] > 0 and t["TaxPaid$"] == pytest.approx(t["WithdrawalTax$"])          # nothing taxed while invested
    assert t["AfterTaxCAGR"] < row(rep, "stable_dividend", "tfsa")["AfterTaxCAGR"]
    tab = rep.reports["rrsp"].tax["stable_dividend"]
    end_pre = tab["wealth_pre_tax"].iloc[-1]
    assert tab["tax_rrsp_withdrawal"].iloc[-1] == pytest.approx(0.30 * end_pre, rel=1e-9)             # 30% of the balance
    assert any(n == "RRSP: deferred, taxed at withdrawal at 30.0% (assumed)" for n in rep.notes)
    # a higher rate moves the RRSP down, the other accounts do not move
    hi = syn.account_compare(["stable_dividend"], tax=tax(0.45), rebalance_months=12)
    assert row(hi, "stable_dividend", "rrsp")["AfterTaxCAGR"] < t["AfterTaxCAGR"]
    assert row(hi, "stable_dividend", "tfsa")["AfterTaxCAGR"] == row(rep, "stable_dividend", "tfsa")["AfterTaxCAGR"]


def test_rrsp_without_a_rate_is_warned_and_not_ranked(syn):
    rep = syn.account_compare(["stable_dividend"], tax=tax(None), rebalance_months=12)
    assert any("RRSP withdrawals untaxed" in n for n in rep.notes)
    r = row(rep, "stable_dividend", "rrsp")
    assert math.isnan(r["Rank"]) and r["WithdrawalTax$"] == 0
    assert sorted(rep.table.loc[rep.table["name"] == "stable_dividend", "Rank"].dropna()) == [1, 2]
    assert rep.best["stable_dividend"]["account"] != "rrsp"


# ------------------------------------------------------------------ US names: withholding treatment by account
def test_us_names_withholding_exempt_lost_credited(us_rep):
    rep = us_rep
    name = "momentum_12_1"
    wh = 0.02 * 0.15                                                       # 2% yield x 15% treaty rate, of wealth a year
    tx, tf, rr = (row(rep, name, a) for a in ACCOUNTS)
    assert tx["WHCredited/yr"] == pytest.approx(wh, rel=2e-2) and tx["WHLost/yr"] == 0 and tx["WHExempt/yr"] == 0
    assert tf["WHLost/yr"] == pytest.approx(wh, rel=2e-2) and tf["WHCredited/yr"] == 0 and tf["WHExempt/yr"] == 0
    assert rr["WHExempt/yr"] == pytest.approx(wh, rel=2e-2) and rr["WHLost/yr"] == 0 and rr["WHCredited/yr"] == 0
    # the passive index is a US index: taxed with the data card's foreign share, so it shows the same pattern
    ix = [row(rep, rep.benchmark, a) for a in ACCOUNTS]
    assert ix[0]["WHCredited/yr"] > 0 and ix[1]["WHLost/yr"] > 0 and ix[2]["WHExempt/yr"] > 0
    assert any("foreign dividend share of 100%" in n for n in rep.notes) and any("foreign: 100% of names" in n for n in rep.notes)
    assert any("CAD-basis warning" in n for n in rep.notes)
    # the lost withholding is a drag in the TFSA only; the foreign table has the US class with the three treatments
    assert tf["TaxDrag/yr"] > 0.002
    f = rep.foreign[rep.foreign["portfolio"] == name].iloc[0]
    assert f["class"] == "US stock (direct)" and f["share"] == pytest.approx(1.0) and f["rate"] == 0.15
    assert "creditable" in f["taxable"] and "LOST" in f["tfsa"] and "EXEMPT" in f["rrsp"]


def test_treatment_matrix_classes():
    assert treatment("direct", "US", 0.15)["rrsp"] == "EXEMPT (treaty)"
    assert "LOST (exemption is US only)" in treatment("direct", "GB", 0.10)["rrsp"]
    assert all("LOST" in v for k, v in treatment("fund", "US", 0.15).items())
    assert treatment("domestic", "CA", 0.0)["tfsa"] == "sheltered"


# ------------------------------------------------------------------ requests, REST, MCP, jobs
@pytest.fixture(scope="module")
def reg():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from screenlab.server.registry import LabRegistry
    return LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)}, default="synthetic")


def test_rest_route_and_mcp_tool_come_from_the_registry(reg):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient
    from screenlab.server.mcp_server import build_tools
    from screenlab.server.rest import create_app
    c = TestClient(create_app(reg))
    assert "/labs/{lab}/account_compare" in c.get("/openapi.json").json()["paths"]
    assert "account_compare" in [o["name"] for o in c.get("/operations").json()["operations"]]
    body = {"screens": ["stable_dividend"], "tax": {"rrsp_withdrawal_rate": 0.3}, "config": {"rebalance_months": 12}}
    r = c.post("/labs/synthetic/account_compare", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    cols = j["table"]["columns"]
    assert {"name", "kind", "account", "AfterTaxCAGR", "WHLost/yr", "Rank"} <= set(cols)
    assert len(j["table"]["rows"]) == 6 and set(j["best"]) == {j["benchmark"], "stable_dividend"} and j["notes"]
    assert j["foreign"]["columns"] == list(FOREIGN_COLS) and j["data_card"]["name"] == "synthetic"
    # the request model inherits TaxRequest's fields without `account`
    schema = c.get("/openapi.json").json()["components"]["schemas"]
    fields = set(schema["AccountTaxRequest"]["properties"])
    assert {"province", "other_income", "rrsp_withdrawal_rate", "harvest"} <= fields and "account" not in fields
    tools = build_tools(reg)
    assert "account_compare" in tools
    out = tools["account_compare"](["stable_dividend"], accounts=["tfsa", "taxable"], tax={"rrsp_withdrawal_rate": 0.3},
                                   config={"rebalance_months": 12})
    assert [r_[2] for r_ in out["table"]["rows"][:2]] == ["tfsa", "taxable"] and out["accounts"] == ["tfsa", "taxable"]
    # a job kind too
    from screenlab import jobs
    assert "account_compare" in jobs.KINDS


def test_bad_requests_are_400s(reg):
    from fastapi.testclient import TestClient
    from screenlab.server.rest import create_app
    c = TestClient(create_app(reg))
    r = c.post("/labs/synthetic/account_compare", json={"tax": {"account": "tfsa"}})
    assert r.status_code == 400 and "tax.account" in r.json()["error"]
    assert c.post("/labs/synthetic/account_compare", json={"accounts": ["isa"]}).status_code in (400, 422)
    r = c.post("/labs/synthetic/account_compare", json={"config": {"tax": {"model": "canadian", "account": "tfsa", "province": "AB"}},
                                                         "tax": {"province": "AB"}})
    assert r.status_code == 400 and "not both" in r.json()["error"]
    with pytest.raises(ServerError):
        OPERATIONS["account_compare"].precheck({"tax": {"province": "ON"}})
    with pytest.raises(ServerError):
        Lab.synthetic(freq="ME", n_stocks=30, years=4, seed=1).account_compare(["dogs"], accounts=["tfsa", "tfsa"])


def test_config_tax_is_the_shared_settings_and_account_is_replaced(syn):
    cfg_tax = TaxConfig(model=CanadianTax("BC", other_income=60_000), account="tfsa", rrsp_withdrawal_rate=0.25)
    rep = syn.account_compare(["stable_dividend"], rebalance_months=12, tax=cfg_tax)
    assert rep.tax["model"]["province"] == "BC" and rep.tax["rrsp_convention"].endswith("at 25.0% (assumed)")
    assert list(rep.table["account"][:3]) == list(ACCOUNTS)


# ------------------------------------------------------------------ TUI: session command, tab, chart data
def test_session_accounts_command_view_and_chart_data():
    from screenlab.tests.test_tui import make_registry
    from screenlab.tui import web
    from screenlab.tui.session import Session
    s = Session(make_registry(), own=True)
    try:
        assert "no account comparison yet" in s.view("accounts") and web.chart_data(s, "accounts")["empty"] is True
        s.command("add_scenario", {"screen": "stable_dividend", "run": True, "wait": True, "rebalance_months": 12,
                                    "tax": {"model": "canadian", "province": "AB", "account": "tfsa", "other_income": 90000,
                                            "rrsp_withdrawal_rate": 0.3}})
        r = s.command("accounts", {"wait": True})
        assert r["jobs"][0]["kind"] == "account_compare" and r["jobs"][0]["status"] == "done"
        a = s.analyses["accounts"]["stable_dividend"]
        assert a["accounts"] == ["taxable", "tfsa", "rrsp"] and len(a["table"]["rows"]) == 6
        v = s.command("view", {"tab": "accounts"})["view"]
        assert "stable_dividend | tfsa" in v and "best for stable_dividend: tfsa" in v and "RRSP: deferred" in v
        d = web.chart_data(s, "accounts")
        assert d["empty"] is False and d["accounts"] == ["taxable", "tfsa", "rrsp"]
        assert [x["passive"] for x in d["series"]] == [True, False] and d["series"][1]["color"] == s.scenarios[0].color
        assert all(isinstance(x, float) for x in d["series"][1]["after_tax"])
        assert "accounts" in s.command("snapshot")["analyses"] and "accounts" in s.snapshot()["analyses"]
        s.command("remove_scenario", {"scenario": "stable_dividend"})
        assert s.analyses["accounts"] == {}
    finally:
        s.close()


def test_app_accounts_tab_runs_and_colors():
    pytest.importorskip("textual")
    from rich.text import Text
    from screenlab.tests.test_tui import arun, settle, until, make_registry
    from screenlab.tui.app import ScreenlabApp
    from screenlab.tui.session import Session

    async def go():
        s = Session(make_registry(), own=True)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(190, 60)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "stable_dividend", "run": True, "wait": True, "rebalance_months": 12,
                                        "tax": {"model": "canadian", "province": "AB", "account": "taxable",
                                                "other_income": 90000, "rrsp_withdrawal_rate": 0.3}})
            await settle(pilot, app)
            await pilot.press("A")                                         # key A opens the tab, which starts the comparison
            await until(lambda: app.query_one("#t-accounts").row_count == 6, 30, pilot, "6 account rows")
            await settle(pilot, app)
            assert s.active_tab == "accounts" and app.query_one("#main").active == "accounts"
            t = app.query_one("#t-accounts")
            rows = [[str(c) for c in t.get_row_at(i)] for i in range(t.row_count)]
            assert [r[:2] for r in rows] == [[n, a] for n in ("SYN_CAPWT", "stable_dividend") for a in ACCOUNTS]
            best = t.get_row_at(4)                                         # stable_dividend | tfsa: the best account, green
            assert isinstance(best[1], Text) and "green" in str(best[1].style)
            assert "green" not in str(t.get_row_at(3)[1].style)
            assert app.query_one("#t-foreign").row_count >= 1
            assert "best for stable_dividend: tfsa" in str(app.query_one("#a-notes").render())
        s.close()
    arun(go())


def test_app_accounts_tab_amber_for_lost_withholding(us_lab):
    pytest.importorskip("textual")
    from screenlab.tests.test_tui import arun, settle, until
    from screenlab.server.registry import LabRegistry
    from screenlab.tui.app import ScreenlabApp
    from screenlab.tui.session import Session

    async def go():
        reg2 = LabRegistry({"us": lambda: us_lab}, default="us", mode="thread")
        s = Session(reg2, own=True)
        app = ScreenlabApp(s, control_port=None)
        async with app.run_test(size=(200, 60)) as pilot:
            await settle(pilot, app)
            s.command("add_scenario", {"screen": "momentum_12_1", "rebalance_months": 12, "start": "2010-01-04", "end": "2015-12-31",
                                        "tax": {"model": "canadian", "province": "AB", "account": "taxable", "other_income": 90000,
                                                "rrsp_withdrawal_rate": 0.3}})
            s.command("run", {"scenario": "all", "wait": True})
            await settle(pilot, app)
            s.command("show", {"tab": "accounts"})
            await until(lambda: app.query_one("#t-accounts").row_count == 6, 60, pilot, "6 account rows")
            t = app.query_one("#t-accounts")
            cols = [str(c.label) for c in t.columns.values()]
            j = cols.index("WH lost/yr")
            cell = t.get_row_at(4)[j]                                      # momentum_12_1 | tfsa: withholding lost
            assert "dark_orange" in str(cell.style) and "0.30%" in cell.plain
            assert "dark_orange" not in str(t.get_row_at(3)[j].style)       # taxable: credited, not lost
            f = app.query_one("#t-foreign")
            assert "EXEMPT" in "".join(str(c) for c in f.get_row_at(0)) and f.row_count >= 1
        s.close()
    arun(go())


# ------------------------------------------------------------------ the notebook
def test_notebook_executes_offline(tmp_path):
    """Run the notebook's code cells in a fresh interpreter (Agg backend, magics removed): no kernel needed, offline."""
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NOTEBOOK), 4)
    code = "\n".join(l for c in nb.cells if c.cell_type == "code" for l in "".join(c.source).splitlines()
                     if not l.lstrip().startswith("%"))
    script = tmp_path / "cells.py"
    script.write_text("import matplotlib\nmatplotlib.use('Agg')\n" + code)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(NOTEBOOK.parents[2])})
    assert out.returncode == 0, out.stderr[-2000:]
    assert "best account for momentum_12_1: tfsa" in out.stdout and "foreign: 100% of names" in out.stdout
    assert nb.cells[0].source.startswith("# Where should this portfolio live")
