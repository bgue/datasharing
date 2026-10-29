"""One-command download of the free datasets (ACQUIRE.md section 2A) into `data/`.

    from screenlab.realdata.fetch import fetch_free
    m = fetch_free("data", sec_user_agent="Jane Doe jane@example.com")

Sources (each isolated: a failure is recorded in the manifest with the host to allow, never raised):

    sec      SEC Financial Statement Data Sets zips + company_tickers.json -> data/raw/sec/, data/fundamentals_sec.csv
    boc      Bank of Canada Valet            -> data/raw/boc/boc.json, data/macro_boc.csv   (long date,series,value)
    fred     FRED fredgraph.csv per series   -> data/raw/fred/*.csv,   data/macro_fred.csv
    stooq    Stooq daily CSV per symbol      -> data/raw/stooq/*.csv,  data/prices_stooq.csv, data/indices_stooq.csv
    statcan  Statistics Canada CPI 18-10-0004-01 -> data/raw/statcan/, data/macro_statcan.csv
    shiller  Shiller monthly S&P 500 (opt-in)   -> data/raw/shiller/, data/indices_shiller.csv, data/macro_shiller.csv
    tiingo   Tiingo adjClose, key TIINGO_API_KEY (opt-in) -> data/raw/tiingo/, data/indices_tiingo.csv, data/prices_tiingo.csv

`shiller` and `tiingo` are the total-return benchmark sources (ACQUIRE.md section 3); they are opt-in
(`--sources shiller,tiingo`, `make data-tr`) and not part of the default `SOURCES`. Every index file written
here has a `kind` column: "price" (Stooq) or "total_return" (Shiller TR, Tiingo adjClose).

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

SOURCES = ("sec", "boc", "fred", "stooq", "statcan")        # the default run
TR_SOURCES = ("shiller", "tiingo")                         # opt-in total-return benchmark sources
ALL_SOURCES = SOURCES + TR_SOURCES
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
# UNVERIFIED (no network when written). shillerdata.com hosts ie_data.xls on a GoDaddy blob URL whose id/version
# changes, so the page is scraped for the current link first; SHILLER_FALLBACK_URL is the form seen in secondary
# sources (id not confirmed) and the old Yale URL reportedly stopped updating in Dec 2025.
SHILLER_PAGE_URL = "https://shillerdata.com/"
SHILLER_FALLBACK_URL = "https://img1.wsimg.com/blobby/go/ie_data.xls"
SHILLER_YALE_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
# Tiingo EOD endpoint is documented at https://www.tiingo.com/documentation/end-of-day ; the Canadian-listed ETF
# symbols below are UNVERIFIED on the free tier.
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={start}&token={token}"
TIINGO_KEY_ENV = "TIINGO_API_KEY"
TIINGO_SIGNUP = "https://www.tiingo.com/"
TIINGO_DEFAULT_TICKERS = ("SPY", "VOO")
# ETF presets: what each fund tracks and its MER as a fraction per year. MERs are as given in the task brief and
# must be re-checked ("check current MER") on the issuer page; only the VOO page was confirmed to exist. A
# total-return series from adjClose is already net of the MER, so use mer=0 for it; charge the MER on a GROSS index
# (SP500_TR from Shiller) to compare it with the fund.
ETF_PROXIES = {
    "XIU": {"tracks": "S&P/TSX 60 (TR via adjusted close)", "mer": 0.0018, "ccy": "CAD", "url": "https://www.blackrock.com/ca/investors/en"},
    "XIC": {"tracks": "S&P/TSX Capped Composite", "mer": 0.0006, "ccy": "CAD", "url": "https://www.blackrock.com/ca/investors/en"},
    "ZCN": {"tracks": "S&P/TSX Capped Composite", "mer": 0.0006, "ccy": "CAD", "url": "https://www.bmogam.com/ca-en/"},
    "VCN": {"tracks": "FTSE Canada All Cap", "mer": 0.0005, "ccy": "CAD", "url": "https://www.vanguardcanada.ca/"},
    "SPY": {"tracks": "S&P 500", "mer": 0.000945, "ccy": "USD", "url": "https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy"},
    "VOO": {"tracks": "S&P 500", "mer": 0.0003, "ccy": "USD", "url": "https://advisors.vanguard.com/investments/products/voo/vanguard-sp-500-etf"},
    "VFV": {"tracks": "S&P 500 (CAD-listed, unhedged)", "mer": 0.0009, "ccy": "CAD", "url": "https://www.vanguardcanada.ca/"},
}
ETF_NOTE = "check current MER on the issuer page (figures from the task brief; issuer URLs other than VOO unverified)"
DOCS_URL = "https://code.claude.com/docs/en/cloud-environments#network-access"
SOURCE_HOSTS = HOSTS = {"sec": ["www.sec.gov"], "boc": ["www.bankofcanada.ca"], "fred": ["fred.stlouisfed.org"],
         "stooq": ["stooq.com"], "statcan": ["www150.statcan.gc.ca"],
         "shiller": ["shillerdata.com", "img1.wsimg.com"], "tiingo": ["api.tiingo.com"]}
NOTES = {"stooq": "Stooq closes are split-adjusted; dividend adjustment is not guaranteed, so `tri` is a price "
                  "index unless you verify the symbol (ACQUIRE.md section 6). indices_stooq.csv is written with "
                  "kind=\"price\" explicitly. Stooq may also require a CAPTCHA "
                  "API key; a non-CSV reply is reported as an error.",
         "shiller": "Monthly (month-start dates), S&P 500 since 1871. SP500_TR chains (P+D/12)/P_prev; Shiller's D is a "
                    "trailing-12-month dividend he interpolates to monthly, so TR is approximate. Months without a "
                    "dividend yet are in SP500_PRICE only. Rate is GS10 in percent. Download URL is unverified.",
         "tiingo": "adjClose is split- and dividend-adjusted (total return, net of the ETF's MER); needs env "
                   "TIINGO_API_KEY (free key). Canadian ETF symbols on Tiingo are unverified.",
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
                indices.append(pd.DataFrame({"date": d["date"], "index": t, "tri": d["close"], "kind": "price"}))
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


# ------------------------------------------------------------------------------------------ shiller
def _shiller_frame(body: bytes) -> pd.DataFrame:
    """ie_data bytes (xlsx, xls or CSV) -> monthly frame indexed by month start with P, D, CPI, RATE columns."""
    if body[:2] == b"PK":
        raw = _read_sheet(body, "openpyxl")
    elif body[:4] == b"\xd0\xcf\x11\xe0":
        raw = _read_sheet(body, "xlrd")
    else:
        raw = pd.read_csv(io.BytesIO(body), header=None, dtype=object, skip_blank_lines=False, on_bad_lines="skip")
    norm = raw.map(lambda v: "".join(str(v).split()).lower() if pd.notna(v) else "")
    hdr = next((i for i in range(min(len(norm), 40)) if norm.iloc[i, 0] == "date" and "p" in set(norm.iloc[i])), None)
    if hdr is None:
        raise FetchError("Shiller sheet has no header row starting with 'Date' and a 'P' column", HOSTS["shiller"][0])
    names = list(norm.iloc[hdr])

    def col(pred):
        return next((j for j, n in enumerate(names) if pred(n)), None)
    idx = {"P": col(lambda n: n == "p"), "D": col(lambda n: n == "d"), "CPI": col(lambda n: n == "cpi"),
           "RATE": col(lambda n: n.startswith("rate"))}
    if idx["P"] is None or idx["D"] is None:
        raise FetchError(f"Shiller sheet lacks P or D columns: {names[:12]}", HOSTS["shiller"][0])
    body_rows = raw.iloc[hdr + 1:]
    num = lambda j: (pd.to_numeric(body_rows.iloc[:, j], errors="coerce") if j is not None
                     else pd.Series(float("nan"), index=body_rows.index))
    date = pd.to_numeric(body_rows.iloc[:, 0], errors="coerce")
    year = date.floordiv(1)
    month = ((date - year) * 100).round()                       # 1871.01 -> 1, 1871.1 -> 10
    ok = date.notna() & month.between(1, 12)
    out = pd.DataFrame({"P": num(idx["P"]), "D": num(idx["D"]), "CPI": num(idx["CPI"]), "RATE": num(idx["RATE"])})[ok]
    out.index = pd.to_datetime({"year": year[ok].astype(int), "month": month[ok].astype(int), "day": 1})
    out = out.sort_index()
    return out[out["P"].notna()]


def _read_sheet(body: bytes, engine: str) -> pd.DataFrame:
    try:
        xl = pd.ExcelFile(io.BytesIO(body), engine=engine)
    except ImportError as e:
        raise FetchError(f"reading this Excel file needs the `{engine}` package ({e}); install it, or save the "
                         f"sheet as .xlsx/.csv and put it in data/raw/shiller/ie_data.xlsx", "") from e
    except Exception as e:
        raise FetchError(f"cannot open the Shiller workbook ({type(e).__name__}: {e})", HOSTS["shiller"][0]) from e
    name = "Data" if "Data" in xl.sheet_names else xl.sheet_names[min(1, len(xl.sheet_names) - 1)]
    return xl.parse(name, header=None, dtype=object)


def shiller_total_return(df: pd.DataFrame) -> pd.Series:
    """tri_t = tri_{t-1} * (P_t + D_t/12) / P_{t-1}, tri = 100 at the first month; stops at the last month with D."""
    d = df.dropna(subset=["D"])
    tri = [100.0]
    for k in range(1, len(d)):
        tri.append(tri[-1] * (d["P"].iloc[k] + d["D"].iloc[k] / 12.0) / d["P"].iloc[k - 1])
    return pd.Series(tri, index=d.index, name="SP500_TR")


def _shiller_urls(page: bytes | None) -> list:
    import re
    urls = []
    if page:
        for u in re.findall(r"https?://[^\s\"'<>)]+", page.decode("utf-8", "replace")):
            if "ie_data" in u.lower() and ".xls" in u.lower():
                urls.append(u.replace("&amp;", "&"))
    return urls + [SHILLER_FALLBACK_URL, SHILLER_YALE_URL]


def _fetch_shiller(m, root, opener, start, report):
    report(0, "shillerdata.com")
    cached = root / "raw" / "shiller" / "ie_data.xlsx"
    body, last = None, None
    try:
        page = _get(SHILLER_PAGE_URL, opener, timeout=60)
    except FetchError as e:
        page, last = None, e
    if page is None and not cached.exists():
        raise last                                   # host blocked: nothing else to try
    for u in _shiller_urls(page) if page is not None else []:
        try:
            body = _get(u, opener, timeout=120)
            break
        except FetchError as e:
            last = e
    if body is None and cached.exists():
        body = cached.read_bytes()                   # a manually placed file
    if body is None:
        raise FetchError(f"could not download ie_data.xls ({last}); URLs are unverified: download it from "
                         f"{SHILLER_PAGE_URL} and save it as {cached}", getattr(last, "host", HOSTS["shiller"][0]))
    df = _shiller_frame(body)
    suffix = ".xlsx" if body[:2] == b"PK" else ".xls" if body[:2] == b"\xd0\xcf" else ".csv"
    if cached.exists() and cached.read_bytes() == body:
        _record(m, "shiller", cached, "cached")
    else:
        _save(m, "shiller", root / "raw" / "shiller" / f"ie_data{suffix}", body)
    if df.empty:                                     # full history is kept: `start` is deliberately not applied
        raise FetchError("Shiller sheet had no usable monthly rows", HOSTS["shiller"][0])
    tri = shiller_total_return(df)
    price = df["P"] / df["P"].iloc[0] * 100
    long = pd.concat([pd.DataFrame({"date": tri.index, "index": "SP500_TR", "tri": tri.to_numpy(), "kind": "total_return"}),
                      pd.DataFrame({"date": price.index, "index": "SP500_PRICE", "tri": price.to_numpy(), "kind": "price"})])
    out = root / "indices_shiller.csv"
    long.to_csv(out, index=False)
    _record(m, "shiller", out, "ok")
    macro = _long({"cpi_us": df["CPI"], "long_rate_us": df["RATE"]})
    if not macro.empty:
        out = root / "macro_shiller.csv"
        macro.to_csv(out, index=False)
        _record(m, "shiller", out, "ok")
    report(1, f"{len(df)} months")


# ------------------------------------------------------------------------------------------ tiingo
def _fetch_tiingo(m, root, opener, tickers, start, api_key, report):
    key = api_key or os.environ.get(TIINGO_KEY_ENV)
    if not key:
        raise FetchError(f"Tiingo needs an API key: get a free one at {TIINGO_SIGNUP} and set env {TIINGO_KEY_ENV} "
                         f"(or pass tiingo_api_key=)", HOSTS["tiingo"][0])
    prices, indices, ok = [], [], 0
    tickers = list(tickers)
    for i, t in enumerate(tickers):
        report(i / max(len(tickers), 1), t)
        try:
            body = _get(TIINGO_URL.format(ticker=t.lower(), start=start, token=key), opener, timeout=60)
            try:
                rows = json.loads(body)
            except ValueError as e:
                raise FetchError(f"{t}: reply is not JSON: {body[:40]!r}", HOSTS["tiingo"][0]) from e
            if not isinstance(rows, list):
                raise FetchError(f"{t}: {str(rows)[:120]}", HOSTS["tiingo"][0])      # e.g. {"detail": "Not found."}
            d = pd.DataFrame(rows)
            if "adjClose" not in d or "date" not in d:
                raise FetchError(f"{t}: no adjClose/date in reply (columns {list(d.columns)})", HOSTS["tiingo"][0])
            d["date"] = pd.to_datetime(d["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
            d["adj"] = pd.to_numeric(d["adjClose"], errors="coerce")
            d = d.dropna(subset=["date", "adj"])
            d = d[d["date"] >= pd.Timestamp(start)].sort_values("date")
            if d.empty:
                raise FetchError(f"{t}: no rows on or after {start}", HOSTS["tiingo"][0])
            _save(m, "tiingo", root / "raw" / "tiingo" / f"{t.upper().replace('^', '_')}.json", body)
            if t.upper() in ETF_PROXIES:
                indices.append(pd.DataFrame({"date": d["date"], "index": t.upper(), "tri": d["adj"],
                                             "kind": "total_return"}))
            else:
                prices.append(pd.DataFrame({"date": d["date"], "ticker": t.upper(), "tri": d["adj"]}))
            ok += 1
        except Exception as e:
            msg = str(e).replace(key, "***")
            _error(m, "tiingo", FetchError(msg, getattr(e, "host", "")), getattr(e, "host", ""))
            if isinstance(e, NetworkError) and not ok:
                break
    if not ok:
        raise FetchError("no Tiingo ticker downloaded", HOSTS["tiingo"][0])
    for frames, name in ((indices, "indices_tiingo.csv"), (prices, "prices_tiingo.csv")):
        if frames:
            out = root / name
            pd.concat(frames).to_csv(out, index=False)
            _record(m, "tiingo", out, "ok")
    report(1, f"{ok}/{len(tickers)} tickers")


def etf_suggestions(dest="data") -> list:
    """`IndexBenchmark(...)` lines for the ETF presets found as indices in `dest`, plus gross-index hints."""
    root, names = Path(dest), set()
    for f in sorted(root.glob("indices*.csv")):
        try:
            names |= set(pd.read_csv(f, usecols=["index"])["index"].astype(str))
        except (ValueError, OSError):
            pass
    out = []
    for n in sorted(names):
        base = n.split(".")[0].upper()
        e = ETF_PROXIES.get(base)
        if e:
            out.append(f'IndexBenchmark("{n}", mer=0.0)  # {base} tracks {e["tracks"]}; adjClose is already net of its '
                       f'MER {e["mer"]:.4%} ({ETF_NOTE})')
    if "SP500_TR" in names:
        out.append(f'IndexBenchmark("SP500_TR", mer={ETF_PROXIES["VOO"]["mer"]})  # gross index: charge a VOO-like MER '
                   f'(or SPY {ETF_PROXIES["SPY"]["mer"]}); {ETF_NOTE}')
    return out


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
               start="2000-01-01", opener=None, progress=None, tiingo_api_key=None,
               tiingo_tickers=TIINGO_DEFAULT_TICKERS) -> Manifest:
    """Download the free datasets into `dest`; never raises for a source failure (see the manifest `errors`).

    `opener(request, timeout=...)` replaces urllib.request.urlopen (tests); `progress(frac, msg)` is called per
    step (default: screenlab.progress.report, a no-op outside a job)."""
    srcs = [s.strip().lower() for s in ([sources] if isinstance(sources, str) else sources) if s.strip()]
    bad = [s for s in srcs if s not in ALL_SOURCES]
    if bad:
        raise ValueError(f"unknown source {bad[0]!r}; use {list(ALL_SOURCES)}")
    tiingo_tickers = [t.strip() for t in ([tiingo_tickers] if isinstance(tiingo_tickers, str) else
                                          (tiingo_tickers or TIINGO_DEFAULT_TICKERS)) if t.strip()]
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
            elif s == "shiller":
                _fetch_shiller(m, root, opener, start, report)
            elif s == "tiingo":
                _fetch_tiingo(m, root, opener, tiingo_tickers, start, tiingo_api_key, report)
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
