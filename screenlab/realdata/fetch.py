"""One-command download of the free datasets (ACQUIRE.md section 2A) into `data/`.

    from screenlab.realdata.fetch import fetch_free
    m = fetch_free("data", sec_user_agent="Jane Doe jane@example.com")

Sources (each isolated: a failure is recorded in the manifest with the host to allow, never raised):

    sec      SEC Financial Statement Data Sets zips + company_tickers.json -> data/raw/sec/, data/fundamentals_sec.csv
    boc      Bank of Canada Valet            -> data/raw/boc/boc.json, data/macro_boc.csv   (long date,series,value)
    fred     FRED fredgraph.csv per series   -> data/raw/fred/*.csv,   data/macro_fred.csv
    stooq    Stooq daily CSV per symbol      -> data/raw/stooq/*.csv,  data/prices_stooq.csv, data/indices_stooq.csv
    statcan  Statistics Canada CPI 18-10-0004-01 -> data/raw/statcan/, data/macro_statcan.csv

`data/MANIFEST.json` records what is on disk. Nothing here has been run against the real hosts (the
development environment had no network); every parser is tested on fixture bytes through an injectable
`opener`. Stooq is price-only unless the symbol is dividend-adjusted, so its `tri` is a price index.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from .. import progress as _progress
from . import boc_valet, sec_fsds

SOURCES = ("sec", "boc", "fred", "stooq", "statcan")
FRED_SERIES = ("CPIAUCSL", "DGS3MO", "DGS10", "DPRIME", "DEXCAUS", "BAMLC0A0CM")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}&i=d"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# UNVERIFIED (no network when written): StatCan's documented full-table CSV zip is
# https://www150.statcan.gc.ca/n1/tbl/csv/<pid8>-eng.zip ; the `downloadDbLoadingData-nonTraduit.action?pid=...`
# form needs a selectedMembers query that ACQUIRE.md does not document, so it is not used.
STATCAN_CPI_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/18100004-eng.zip"
INDEX_PROXIES = ("^SPX", "SPY.US")             # written to indices_stooq.csv, the rest to prices_stooq.csv
DEFAULT_TICKERS = ("XIU.TO", "XIC.TO", "SPY.US", "^SPX")
DOCS_URL = "https://code.claude.com/docs/en/cloud-environments#network-access"
SOURCE_HOSTS = HOSTS = {"sec": ["www.sec.gov"], "boc": ["www.bankofcanada.ca"], "fred": ["fred.stlouisfed.org"],
         "stooq": ["stooq.com"], "statcan": ["www150.statcan.gc.ca"]}
NOTES = {"stooq": "Stooq closes are split-adjusted; dividend adjustment is not guaranteed, so `tri` is a price "
                  "index unless you verify the symbol (ACQUIRE.md section 6). Stooq may also require a CAPTCHA "
                  "API key; a non-CSV reply is reported as an error.",
         "fred": "FRED rates are in percent; divide by 100 yourself. BAMLC0A0CM is an ICE index: check its notice.",
         "statcan": "STATCAN_CPI_URL is unverified.",
         "sec": "Coverage is US filers only; company_tickers.json holds current tickers only."}


class FetchError(Exception):
    """A source step failed; `host` is what to allow in the environment's network settings."""

    def __init__(self, msg, host=""):
        super().__init__(msg)
        self.host = host


class NetworkError(FetchError):
    """The request itself failed (blocked host, 403, DNS, timeout): the rest of that source will fail too."""


@dataclass
class Manifest:
    files: list = field(default_factory=list)       # {source, path, bytes, status, sha256}
    errors: list = field(default_factory=list)      # {source, host, error}
    allow_hosts: dict = field(default_factory=dict)  # source -> [host]
    labs: list = field(default_factory=list)        # {name, fundamentals, prices, indices}
    macro: list = field(default_factory=list)       # macro csv paths on disk (long date,series,value)
    notes: dict = field(default_factory=dict)       # source -> caveat
    dest: str = "data"
    created: str = ""

    def to_dict(self) -> dict:
        return {"dest": self.dest, "created": self.created, "files": self.files, "errors": self.errors,
                "allow_hosts": self.allow_hosts, "labs": self.labs, "macro": self.macro, "notes": self.notes}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(**{k: d[k] for k in ("files", "errors", "allow_hosts", "labs", "macro", "notes", "dest", "created")
                      if k in d})

    @classmethod
    def load(cls, dest="data") -> "Manifest":
        p = Path(dest) / "MANIFEST.json"
        if not p.exists():
            raise FileNotFoundError(f"{p} not found; run `screenlab-data free` first")
        return cls.from_dict(json.loads(p.read_text()))

    def hosts_flat(self) -> list:
        return sorted({h for hs in self.allow_hosts.values() for h in hs})

    def result(self) -> dict:
        """The JSON the `fetch_data` operation returns (shape fixed; the TUI Data tab reads it)."""
        return {"manifest": {"files": [{k: f.get(k) for k in ("source", "path", "bytes", "status")} for f in self.files],
                             "errors": list(self.errors), "allow_hosts": self.hosts_flat()},
                "labs": [dict(l) for l in self.labs]}


# ------------------------------------------------------------------------------------------ plumbing
def _get(url: str, opener, headers=None, timeout: int = 120) -> bytes:
    """GET `url` through `opener(request, timeout=)` (default urllib.request.urlopen, looked up at call time)."""
    req = urllib.request.Request(url, headers=headers or {})
    op = opener or urllib.request.urlopen
    host = urlparse(url).hostname or ""
    try:
        with op(req, timeout=timeout) as r:
            body = r.read()
    except Exception as e:                       # blocked host, 403, DNS, timeout, proxy refusal ...
        raise NetworkError(f"{type(e).__name__}: {e}", host) from e
    if not isinstance(body, (bytes, bytearray)):
        raise FetchError(f"unexpected non-bytes body from {host}", host)
    return bytes(body)


def _save(m: Manifest, source: str, path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    _record(m, source, path, "ok")


def _record(m: Manifest, source: str, path: Path, status: str) -> None:
    b = path.read_bytes() if path.exists() else b""
    m.files.append({"source": source, "path": str(path), "bytes": len(b), "status": status,
                    "sha256": hashlib.sha256(b).hexdigest() if b else ""})


def _error(m: Manifest, source: str, e: Exception, host: str = "") -> None:
    host = host or getattr(e, "host", "") or (HOSTS[source][0] if source in HOSTS else "")
    m.errors.append({"source": source, "host": host, "error": str(e) or type(e).__name__})


def default_periods(n: int = 12, today: _dt.date | None = None) -> list:
    """The last `n` completed calendar quarters as 'YYYYqN', oldest first."""
    t = today or _dt.date.today()
    y, q = t.year, (t.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out[::-1]


def _long(frames: dict) -> pd.DataFrame:
    """{series: Series indexed by date} -> long `date,series,value`, NaN dropped."""
    parts = [s.dropna().rename("value").rename_axis("date").reset_index().assign(series=k)[["date", "series", "value"]]
             for k, s in frames.items() if len(s.dropna())]
    if not parts:
        return pd.DataFrame(columns=["date", "series", "value"])
    return pd.concat(parts).sort_values(["series", "date"]).reset_index(drop=True)


# ------------------------------------------------------------------------------------------ sources
def _fetch_sec(m, root, opener, ua, periods, report):
    ua = ua or os.environ.get("SEC_USER_AGENT")
    if not ua or "@" not in ua:
        raise FetchError("SEC requires a User-Agent with your name and email: pass sec_user_agent='Your Name "
                         "you@example.com' or set SEC_USER_AGENT", HOSTS["sec"][0])
    headers = {"User-Agent": ua}
    raw = root / "raw" / "sec"
    periods = list(periods) if periods else default_periods()
    n = len(periods) + 2
    tk = raw / "company_tickers.json"
    report(0, "company_tickers.json")
    if tk.exists() and tk.stat().st_size:
        _record(m, "sec", tk, "cached")
    else:
        _save(m, "sec", tk, _get(SEC_TICKERS_URL, opener, headers))
    try:
        doc = json.loads(tk.read_text())
        cik2t = {str(v["cik_str"]): v["ticker"] for v in (doc.values() if isinstance(doc, dict) else doc)}
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError(f"company_tickers.json has an unexpected shape: {e}", HOSTS["sec"][0]) from e
    zips, failed = [], 0
    for i, p in enumerate(periods):
        report((i + 1) / n, f"{p}.zip")
        z = raw / f"{p}.zip"
        try:
            if z.exists() and z.stat().st_size:
                _record(m, "sec", z, "cached")
            else:
                _save(m, "sec", z, _get(sec_fsds.URL.format(period=p), opener, headers))
            zips.append(z)
        except FetchError as e:
            failed += 1
            _error(m, "sec", FetchError(f"{p}: {e}", e.host), e.host)
            if isinstance(e, NetworkError) and not zips:    # host blocked: do not try the other periods
                break
    if not zips:
        raise FetchError("no SEC zip could be downloaded", HOSTS["sec"][0])
    parts = []
    for z in zips:
        try:
            sub, num = sec_fsds.read_zip(z)
            parts.append(sec_fsds.to_fundamentals(sub, num, cik2t))
        except Exception as e:                  # corrupt zip, missing sub.txt/num.txt
            _error(m, "sec", FetchError(f"{z.name}: cannot parse: {e}"), "")
    if not parts:
        raise FetchError("no SEC zip could be parsed")
    fund = (pd.concat(parts).drop_duplicates(["date", "ticker"]).sort_values(["ticker", "date"]))
    out = root / "fundamentals_sec.csv"
    fund.to_csv(out, index=False)
    _record(m, "sec", out, "ok")
    report(1, f"{len(fund)} filings")


def _fetch_boc(m, root, opener, start, report):
    series = list(boc_valet.SERIES)
    url = f"{boc_valet.BASE}/{','.join(series)}/json?start_date={start}"
    report(0, "Valet")
    body = _get(url, opener, timeout=60)
    try:
        df = boc_valet.parse(json.loads(body))
    except (ValueError, KeyError) as e:
        raise FetchError(f"unexpected Valet reply: {e}", HOSTS["boc"][0]) from e
    _save(m, "boc", root / "raw" / "boc" / "boc.json", body)
    if df.empty:
        raise FetchError("Valet returned no observations", HOSTS["boc"][0])
    out = boc_valet.to_macro_csv(df, root / "macro_boc.csv")
    _record(m, "boc", out, "ok")
    report(1, f"{df.shape[1]} series")


def _fetch_fred(m, root, opener, start, report):
    frames, ok = {}, 0
    for i, sid in enumerate(FRED_SERIES):
        report(i / len(FRED_SERIES), sid)
        try:
            body = _get(FRED_URL.format(id=sid), opener, timeout=60)
            d = pd.read_csv(io.BytesIO(body))
            if d.shape[1] < 2:
                raise FetchError(f"{sid}: unexpected columns {list(d.columns)}", HOSTS["fred"][0])
            s = pd.Series(pd.to_numeric(d.iloc[:, 1], errors="coerce").to_numpy(),
                          index=pd.to_datetime(d.iloc[:, 0], errors="coerce"))
            s = s[s.index.notna() & (s.index >= pd.Timestamp(start))]
            _save(m, "fred", root / "raw" / "fred" / f"{sid}.csv", body)
            frames[sid], ok = s, ok + 1
        except Exception as e:
            _error(m, "fred", FetchError(f"{sid}: {e}", getattr(e, "host", "")), getattr(e, "host", ""))
            if isinstance(e, NetworkError) and not ok:
                break                           # host blocked: skip the rest
    if not frames:
        raise FetchError("no FRED series downloaded", HOSTS["fred"][0])
    long = _long(frames)
    out = root / "macro_fred.csv"
    long.to_csv(out, index=False)
    _record(m, "fred", out, "ok")
    report(1, f"{len(frames)} series")


def stooq_symbol(ticker: str) -> str:
    """Stooq's symbol for our ticker: lower case, TSX '.TO' -> '.ca' (unverified), '^SPX' -> '^spx'."""
    t = ticker.lower()
    return t[:-3] + ".ca" if t.endswith(".to") else t


def _fetch_stooq(m, root, opener, tickers, start, report):
    prices, indices, ok = [], [], 0
    tickers = list(tickers)
    for i, t in enumerate(tickers):
        report(i / max(len(tickers), 1), t)
        try:
            body = _get(STOOQ_URL.format(sym=stooq_symbol(t)), opener, timeout=60)
            head = body[:64].decode("utf-8", "replace").lower()
            if not head.startswith("date,"):
                raise FetchError(f"{t}: reply is not CSV (Stooq may require an API key or CAPTCHA): "
                                 f"{body[:40]!r}", HOSTS["stooq"][0])
            d = pd.read_csv(io.BytesIO(body))
            d.columns = [c.strip().lower() for c in d.columns]
            d["date"] = pd.to_datetime(d["date"], errors="coerce")
            d["close"] = pd.to_numeric(d["close"], errors="coerce")
            d = d.dropna(subset=["date", "close"])
            d = d[d["date"] >= pd.Timestamp(start)].sort_values("date")
            if d.empty:
                raise FetchError(f"{t}: no rows on or after {start}", HOSTS["stooq"][0])
            _save(m, "stooq", root / "raw" / "stooq" / f"{t.replace('^', '_')}.csv", body)
            if t.upper() in INDEX_PROXIES:
                indices.append(pd.DataFrame({"date": d["date"], "index": t, "tri": d["close"]}))
            else:
                prices.append(pd.DataFrame({"date": d["date"], "ticker": t, "tri": d["close"]}))
            ok += 1
        except Exception as e:
            _error(m, "stooq", FetchError(str(e), getattr(e, "host", "")), getattr(e, "host", ""))
            if isinstance(e, NetworkError) and not ok:
                break
    if not ok:
        raise FetchError("no Stooq symbol downloaded", HOSTS["stooq"][0])
    if prices:
        out = root / "prices_stooq.csv"
        pd.concat(prices).to_csv(out, index=False)
        _record(m, "stooq", out, "ok")
    if indices:
        out = root / "indices_stooq.csv"
        pd.concat(indices).to_csv(out, index=False)
        _record(m, "stooq", out, "ok")
    report(1, f"{ok}/{len(tickers)} symbols")


def _fetch_statcan(m, root, opener, start, report):
    report(0, "CPI 18-10-0004-01")
    body = _get(STATCAN_CPI_URL, opener, timeout=180)
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv") and "metadata" not in n.lower())
            d = pd.read_csv(io.BytesIO(z.read(name)), dtype=str)
        prod = next(c for c in d.columns if c.lower().startswith("products"))
        d = d[(d["GEO"] == "Canada") & (d[prod] == "All-items")]
        s = pd.Series(pd.to_numeric(d["VALUE"], errors="coerce").to_numpy(),
                      index=pd.to_datetime(d["REF_DATE"], errors="coerce"))
    except (zipfile.BadZipFile, StopIteration, KeyError) as e:
        raise FetchError(f"unexpected StatCan table layout ({type(e).__name__}: {e}); "
                         f"STATCAN_CPI_URL is unverified", HOSTS["statcan"][0]) from e
    s = s[s.index.notna() & (s.index >= pd.Timestamp(start))].sort_index()
    if s.dropna().empty:
        raise FetchError("StatCan table had no Canada All-items rows", HOSTS["statcan"][0])
    _save(m, "statcan", root / "raw" / "statcan" / "18100004-eng.zip", body)
    out = root / "macro_statcan.csv"
    _long({"CPI_CA_STATCAN": s}).to_csv(out, index=False)
    _record(m, "statcan", out, "ok")
    report(1, f"{len(s)} months")


# ------------------------------------------------------------------------------------------ labs
def discover_labs(dest="data") -> tuple:
    """(lab specs, macro csv paths) that the files currently on disk allow."""
    root = Path(dest)
    f, p, i = root / "fundamentals_sec.csv", root / "prices_stooq.csv", root / "indices_stooq.csv"
    labs = []
    if f.exists() and p.exists():
        labs.append({"name": "sec_us", "fundamentals": str(f.resolve()), "prices": str(p.resolve()),
                     "indices": str(i.resolve()) if i.exists() else None})
    macro = [str(x) for x in sorted(root.glob("macro_*.csv"))]
    return labs, macro


def register_labs(registry, manifest: "Manifest | None" = None, dest="data") -> list:
    """Register the manifest's buildable labs on a `LabRegistry` (`register_csv`); returns their names."""
    specs = manifest.labs if manifest is not None else discover_labs(dest)[0]
    for s in specs:
        registry.register_csv(s["name"], s["fundamentals"], s["prices"], s.get("indices"))
    return [s["name"] for s in specs]


# ------------------------------------------------------------------------------------------ main
def fetch_free(dest="data", sources=SOURCES, sec_user_agent=None, periods=None, tickers=DEFAULT_TICKERS,
               start="2000-01-01", opener=None, progress=None) -> Manifest:
    """Download the free datasets into `dest`; never raises for a source failure (see the manifest `errors`).

    `opener(request, timeout=...)` replaces urllib.request.urlopen (tests); `progress(frac, msg)` is called per
    step (default: screenlab.progress.report, a no-op outside a job)."""
    srcs = [s.strip().lower() for s in ([sources] if isinstance(sources, str) else sources) if s.strip()]
    bad = [s for s in srcs if s not in SOURCES]
    if bad:
        raise ValueError(f"unknown source {bad[0]!r}; use {list(SOURCES)}")
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    tickers = [t.strip() for t in ([tickers] if isinstance(tickers, str) else (tickers or DEFAULT_TICKERS)) if t.strip()]
    rep = progress or _progress.report
    prev = {}
    try:
        prev = Manifest.load(dest).to_dict()
    except (FileNotFoundError, ValueError, TypeError):
        pass
    m = Manifest(dest=str(root), created=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    n = len(srcs)
    for k, s in enumerate(srcs):
        def report(f, msg="", k=k, s=s):
            rep((k + min(max(f, 0.0), 1.0)) / n, f"{s}: {msg}" if msg else s)
        m.allow_hosts[s] = list(HOSTS[s])
        if s in NOTES:
            m.notes[s] = NOTES[s]
        try:
            _progress.check_cancel()
            if s == "sec":
                _fetch_sec(m, root, opener, sec_user_agent, periods, report)
            elif s == "boc":
                _fetch_boc(m, root, opener, start, report)
            elif s == "fred":
                _fetch_fred(m, root, opener, start, report)
            elif s == "stooq":
                _fetch_stooq(m, root, opener, tickers, start, report)
            else:
                _fetch_statcan(m, root, opener, start, report)
        except _progress.Cancelled:
            raise
        except Exception as e:                  # one source never takes the run down
            _error(m, s, e)
        rep((k + 1) / n, f"{s} done")
    # keep earlier results of sources not run now
    for key in ("files", "errors"):
        getattr(m, key).extend(x for x in prev.get(key, []) if x.get("source") not in srcs)
    for s, h in prev.get("allow_hosts", {}).items():
        m.allow_hosts.setdefault(s, h)
    for s, t in prev.get("notes", {}).items():
        m.notes.setdefault(s, t)
    m.labs, m.macro = discover_labs(dest)
    (root / "MANIFEST.json").write_text(m.to_json())
    return m
