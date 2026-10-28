"""fetch_data: the free-dataset downloader, its operation, labs and CLI (fixture bytes through a fake opener; no network).

    python -m pytest screenlab/tests/test_fetch_data.py -q
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
import zipfile

import pandas as pd
import pytest

from screenlab.api import Lab
from screenlab.realdata import cli, fetch
from screenlab.realdata.fetch import Manifest, fetch_free, register_labs
from screenlab.server.registry import LabRegistry
from screenlab.tests.test_dataset import tiny
from screenlab.tests.test_realdata import _facts, _num, _sub

UA = "Jane Doe jane@example.com"
VALET = {"observations": [{"d": "2024-01-02", "FXUSDCAD": {"v": "1.33"}, "V80691311": {"v": "7.2"}},
                          {"d": "2024-01-03", "FXUSDCAD": {"v": "1.34"}, "V80691311": {"v": "7.2"}}]}
TICKERS_JSON = {"0": {"cik_str": 1, "ticker": "ALP", "title": "ALPHA"}, "1": {"cik_str": 2, "ticker": "BET", "title": "B"}}
STOOQ = "Date,Open,High,Low,Close,Volume\n2023-12-29,1,1,1,10.0,5\n2024-01-02,1,1,1,11.0,5\n1999-01-04,1,1,1,3.0,5\n"
FRED = "observation_date,{id}\n2023-12-01,3.1\n2024-01-01,.\n2024-02-01,3.3\n"
STATCAN = ("REF_DATE,GEO,DGUID,Products and product groups,UOM,VALUE\n"
           "2023-12,Canada,x,All-items,2002=100,157.1\n2024-01,Canada,x,All-items,2002=100,157.5\n"
           "2024-01,Ontario,x,All-items,2002=100,150.0\n2024-01,Canada,x,Food,2002=100,190.0\n")


def sec_zip() -> bytes:
    sub = _sub([("a-23", "0000000001", "ALPHA", "3571", "10-K", "20231231", "2023", "FY", "20240215")])
    num = _num(_facts("a-23", "20231231", Revenues=1000, EarningsPerShareDiluted=2.0, Assets=1000, NetIncomeLoss=200))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("sub.txt", sub.to_csv(sep="\t", index=False))
        z.writestr("num.txt", num.to_csv(sep="\t", index=False))
    return b.getvalue()


def statcan_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("18100004.csv", STATCAN)
        z.writestr("18100004_MetaData.csv", "x\n")
    return b.getvalue()


class FakeOpener:
    """Stands in for urlopen: routes by URL, records requests, can block hosts."""

    def __init__(self, blocked=(), stooq=STOOQ):
        self.calls, self.blocked, self.stooq = [], set(blocked), stooq

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.calls.append((url, dict(getattr(req, "headers", {}))))
        host = url.split("/")[2]
        if host in self.blocked:
            raise OSError("Tunnel connection failed: 403 Forbidden")
        if "company_tickers" in url:
            body = json.dumps(TICKERS_JSON)
        elif "financial-statement-data-sets" in url:
            body = sec_zip()
        elif "bankofcanada" in url:
            body = json.dumps(VALET)
        elif "fredgraph" in url:
            body = FRED.format(id=url.rsplit("=", 1)[1])
        elif "stooq" in url:
            body = self.stooq
        elif "statcan" in url:
            body = statcan_zip()
        else:
            raise AssertionError(url)
        body = body.encode() if isinstance(body, str) else body
        return _Resp(body)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def run(tmp_path, opener=None, **kw):
    kw.setdefault("sec_user_agent", UA)
    kw.setdefault("periods", ["2023q4"])
    return fetch_free(tmp_path / "data", opener=opener or FakeOpener(), **kw)


# --------------------------------------------------------------------------- sources
def test_every_source_parses_its_fixture(tmp_path):
    o = FakeOpener()
    m = run(tmp_path, o)
    d = tmp_path / "data"
    assert m.errors == []
    f = pd.read_csv(d / "fundamentals_sec.csv")
    assert f["ticker"].tolist() == ["ALP"] and f["eps"].iloc[0] == 2.0
    boc = pd.read_csv(d / "macro_boc.csv")
    assert list(boc.columns) == ["date", "series", "value"] and set(boc.series) == {"FXUSDCAD", "V80691311"}
    fred = pd.read_csv(d / "macro_fred.csv")
    assert set(fred.series) == set(fetch.FRED_SERIES) and len(fred) == 2 * len(fetch.FRED_SERIES)   # "." dropped
    prices = pd.read_csv(d / "prices_stooq.csv")
    assert set(prices.ticker) == {"XIU.TO", "XIC.TO"} and list(prices.columns) == ["date", "ticker", "tri"]
    assert prices["date"].min() == "2023-12-29" and prices["tri"].max() == 11.0      # start filter, Close
    idx = pd.read_csv(d / "indices_stooq.csv")
    assert set(idx["index"]) == {"SPY.US", "^SPX"} and list(idx.columns) == ["date", "index", "tri"]
    sc = pd.read_csv(d / "macro_statcan.csv")
    assert sc.series.unique().tolist() == ["CPI_CA_STATCAN"] and sc.value.tolist() == [157.1, 157.5]
    urls = [u for u, _ in o.calls]
    assert any(u.endswith("/2023q4.zip") for u in urls) and "https://stooq.com/q/d/l/?s=xic.ca&i=d" in urls
    assert all(h.get("User-agent") == UA for u, h in o.calls if "sec.gov" in u)
    assert (d / "raw" / "sec" / "2023q4.zip").exists() and (d / "raw" / "boc" / "boc.json").exists()


def test_sec_without_user_agent_is_an_error_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    o = FakeOpener()
    m = run(tmp_path, o, sources=("sec",), sec_user_agent=None)
    assert [e["source"] for e in m.errors] == ["sec"] and m.errors[0]["host"] == "www.sec.gov"
    assert "User-Agent" in m.errors[0]["error"] and o.calls == []
    monkeypatch.setenv("SEC_USER_AGENT", UA)                     # the environment variable is honoured
    assert run(tmp_path, o, sources=("sec",), sec_user_agent=None).errors == []


def test_blocked_host_is_isolated_and_names_the_host_to_allow(tmp_path):
    m = run(tmp_path, FakeOpener(blocked={"fred.stlouisfed.org", "stooq.com"}))
    assert {e["source"]: e["host"] for e in m.errors} == {"fred": "fred.stlouisfed.org", "stooq": "stooq.com"}
    assert "403" in m.errors[0]["error"]
    ok = {f["source"] for f in m.files if f["status"] == "ok"}
    assert {"sec", "boc", "statcan"} <= ok and not ok & {"fred", "stooq"}
    assert "fred.stlouisfed.org" in m.allow_hosts["fred"] and "stooq.com" in m.hosts_flat()
    assert [l["name"] for l in m.labs] == []                      # no prices -> no sec_us lab


def test_stooq_non_csv_reply_is_an_error(tmp_path):
    m = run(tmp_path, FakeOpener(stooq="Get your apikey: <html>"), sources=("stooq",))
    assert m.errors and m.errors[0]["source"] == "stooq" and "not CSV" in m.errors[0]["error"]


def test_partial_failure_keeps_other_series(tmp_path):
    class Half(FakeOpener):
        def __call__(self, req, timeout=None):
            if "id=DGS10" in req.full_url:
                raise OSError("boom")
            return super().__call__(req, timeout)
    m = run(tmp_path, Half(), sources=("fred",))
    assert len(m.errors) == 1 and "DGS10" in m.errors[0]["error"]
    assert "DGS10" not in set(pd.read_csv(tmp_path / "data" / "macro_fred.csv").series)


def test_unknown_source_raises_and_cached_zip_is_reused(tmp_path):
    with pytest.raises(ValueError, match="unknown source"):
        run(tmp_path, sources=("nope",))
    o = FakeOpener()
    run(tmp_path, o, sources=("sec",))
    n = len(o.calls)
    m = run(tmp_path, o, sources=("sec",))
    assert len(o.calls) == n and {f["status"] for f in m.files} >= {"cached"}


def test_manifest_json_written_and_merged(tmp_path):
    m = run(tmp_path, sources=("boc",))
    p = tmp_path / "data" / "MANIFEST.json"
    d = json.loads(p.read_text())
    assert {"files", "errors", "allow_hosts", "labs"} <= set(d)
    assert {"source", "path", "bytes", "status", "sha256"} <= set(d["files"][0]) and len(d["files"][0]["sha256"]) == 64
    m2 = run(tmp_path, sources=("fred",))                       # a later partial run keeps the boc entries
    assert {f["source"] for f in m2.files} >= {"boc", "fred"}
    assert Manifest.load(tmp_path / "data").to_dict() == m2.to_dict()
    assert m.allow_hosts["boc"] == ["www.bankofcanada.ca"]


def test_default_periods():
    import datetime as dt
    p = fetch.default_periods(5, dt.date(2026, 2, 10))
    assert p == ["2024q4", "2025q1", "2025q2", "2025q3", "2025q4"]


def test_data_dirs_are_git_ignored():
    import subprocess
    root = fetch.Path(fetch.__file__).parents[2]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout (e.g. unpacked from the zip)")
    r = subprocess.run(["git", "check-ignore", "data/x.csv", "screenlab/data/x.csv"], capture_output=True, text=True,
                       cwd=root)
    assert r.stdout.split() == ["data/x.csv", "screenlab/data/x.csv"]


# --------------------------------------------------------------------------- labs
def _tiny_csvs(tmp_path):
    ds = tiny()
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    ds.fundamentals.to_csv(d / "fundamentals_sec.csv", index=False)
    ds.prices.reset_index().melt("date", var_name="ticker", value_name="tri").dropna().to_csv(d / "prices_stooq.csv", index=False)
    ds.indices.reset_index().melt("date", var_name="index", value_name="tri").to_csv(d / "indices_stooq.csv", index=False)
    return d


def test_sec_us_lab_appears_when_fundamentals_and_prices_exist(tmp_path):
    m = run(tmp_path)
    assert [l["name"] for l in m.labs] == ["sec_us"]
    spec = m.labs[0]
    assert spec["fundamentals"].endswith("fundamentals_sec.csv") and spec["indices"].endswith("indices_stooq.csv")
    assert any(p.endswith("macro_boc.csv") for p in m.macro)


def test_register_labs_registers_a_working_lab(tmp_path):
    d = _tiny_csvs(tmp_path)
    m = Manifest(dest=str(d))
    m.labs, m.macro = fetch.discover_labs(d)
    reg = LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1)}, default="synthetic")
    assert register_labs(reg, m) == ["sec_us"]
    assert "sec_us" in reg.factories
    lab = reg.get("sec_us")
    assert lab.data_card().benchmark == "BM" and lab.data_card().n_names == 3


# --------------------------------------------------------------------------- operation
SHAPE_ARGS = {"sources": ["boc", "stooq"], "dest": None, "sec_user_agent": UA, "tickers": ["XIU.TO", "^SPX"]}


def check_shape(out, dest):
    assert set(out) == {"manifest", "labs"} and set(out["manifest"]) == {"files", "errors", "allow_hosts"}
    assert all(set(f) == {"source", "path", "bytes", "status"} for f in out["manifest"]["files"])
    assert all(isinstance(h, str) for h in out["manifest"]["allow_hosts"])
    assert {f["source"] for f in out["manifest"]["files"]} == {"boc", "stooq"} and out["manifest"]["errors"] == []
    json.dumps(out)


@pytest.fixture()
def patched(monkeypatch):
    o = FakeOpener()
    monkeypatch.setattr(urllib.request, "urlopen", o)
    return o


def test_operation_via_lab_and_registry_shape(tmp_path, patched):
    from screenlab.operations import OPERATIONS
    op = OPERATIONS["fetch_data"]
    assert op.long_running and op.builtin and not op.tracked_entry
    dest = str(tmp_path / "d")
    lab = Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1)
    out = lab.run("fetch_data", sources=["boc", "stooq"], dest=dest, tickers=["XIU.TO", "^SPX"])
    check_shape(out, dest)
    reg = LabRegistry({"synthetic": lambda: lab}, default="synthetic", sync_timeout=0, mode="thread")
    out = reg.fetch_data(sources=["boc", "stooq"], dest=dest, tickers=["XIU.TO", "^SPX"])
    check_shape(out, dest)
    assert out["labs"] == []                                     # no SEC fundamentals in this run


def test_operation_registers_labs_through_the_registry_sync_and_job(tmp_path, patched):
    dest = str(tmp_path / "d")
    reg = LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1)},
                      default="synthetic", sync_timeout=0, mode="thread")
    args = {"dest": dest, "sec_user_agent": UA, "periods": ["2023q4"], "tickers": ["XIU.TO", "^SPX"]}
    out = reg.run_operation("fetch_data", args)
    assert [l["name"] for l in out["labs"]] == ["sec_us"] and set(out["labs"][0]) == {"name", "fundamentals", "prices", "indices"}
    assert "sec_us" in reg.factories
    reg.factories.pop("sec_us")
    reg._fetched.clear()
    job = reg.submit_run("fetch_data", args, force=True)
    for _ in range(100):
        if reg.run_status(job["job_id"])["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert reg.run_status(job["job_id"])["status"] == "done"
    res = reg.run_result(job["job_id"])
    assert set(res) == {"manifest", "labs"} and "sec_us" in reg.factories


def test_operation_over_rest_and_mcp(tmp_path, patched):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from screenlab.server.mcp_server import build_tools
    from screenlab.server.rest import create_app
    reg = LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1)},
                      default="synthetic", sync_timeout=0, mode="thread")
    c = TestClient(create_app(reg))
    body = {"sources": ["boc", "stooq"], "dest": str(tmp_path / "r"), "tickers": ["XIU.TO", "^SPX"]}
    r = c.post("/labs/synthetic/fetch_data", json=body)
    assert r.status_code == 200, r.text
    check_shape(r.json(), None)
    ops = {o["name"]: o for o in c.get("/operations").json()["operations"]}
    assert ops["fetch_data"]["long_running"] is True
    t = build_tools(reg)
    check_shape(t["fetch_data"](sources=["boc", "stooq"], dest=str(tmp_path / "m"), tickers=["XIU.TO", "^SPX"]), None)


def test_operation_reports_progress_per_source(tmp_path, patched):
    from screenlab import progress
    seen = []
    with progress.job_context(lambda f, msg: seen.append((f, msg))):
        Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1).run(
            "fetch_data", sources=["boc", "stooq"], dest=str(tmp_path / "p"), tickers=["XIU.TO"])
    fr = [f for f, _ in seen]
    assert fr == sorted(fr) and fr[-1] == 1.0 and any(m.startswith("stooq") for _, m in seen)


def test_operation_failure_is_data_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", FakeOpener(blocked={"www.bankofcanada.ca"}))
    out = Lab.synthetic(freq="ME", n_stocks=40, years=4, seed=1).run("fetch_data", sources=["boc"], dest=str(tmp_path / "x"))
    assert out["manifest"]["errors"][0]["host"] == "www.bankofcanada.ca"
    assert out["manifest"]["allow_hosts"] == ["www.bankofcanada.ca"]


# --------------------------------------------------------------------------- CLI
def test_cli_hosts(capsys):
    assert cli.main(["hosts"]) == 0
    out = capsys.readouterr().out
    for h in ("www.sec.gov", "www.bankofcanada.ca", "fred.stlouisfed.org", "stooq.com", "www150.statcan.gc.ca"):
        assert h in out
    assert "https://code.claude.com/docs/en/cloud-environments#network-access" in out


def test_cli_free_then_status(tmp_path, capsys):
    d = str(tmp_path / "cli")
    rc = cli.main(["free", "--dest", d, "--sec-user-agent", UA, "--sources", "sec,stooq", "--tickers", "XIU.TO,^SPX",
                   "--periods", "2023q4"], opener=FakeOpener())
    out = capsys.readouterr().out
    assert rc == 0 and "sec_us" in out and "prices_stooq.csv" in out
    assert cli.main(["status", "--dest", d]) == 0
    out = capsys.readouterr().out
    assert "labs that can be built" in out and "sec_us:" in out and "fundamentals_sec.csv" in out


def test_cli_status_without_manifest_and_blocked_run(tmp_path, capsys):
    assert cli.main(["status", "--dest", str(tmp_path / "none")]) == 1
    assert "screenlab-data free" in capsys.readouterr().out
    rc = cli.main(["free", "--dest", str(tmp_path / "b"), "--sources", "boc"],
                  opener=FakeOpener(blocked={"www.bankofcanada.ca"}))
    out = capsys.readouterr().out
    assert rc == 1 and "allow www.bankofcanada.ca" in out and "none (needs" in out


def test_makefile_data_free_command():
    import shutil
    import subprocess
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    root = fetch.Path(fetch.__file__).parents[2]
    out = subprocess.run(["make", "-n", "data-free", "SEC_USER_AGENT=x y@z"], cwd=root, capture_output=True, text=True).stdout
    assert 'screenlab.realdata.cli free --dest "data"' in out and '--sec-user-agent "x y@z"' in out
    assert "--sources sec,boc,fred,stooq,statcan" in out and "--tickers XIU.TO,XIC.TO,SPY.US,^SPX" in out
    assert "help" in subprocess.run(["make"], cwd=root, capture_output=True, text=True).stdout
