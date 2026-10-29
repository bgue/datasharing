"""`screenlab-data`: download the free datasets and see what they allow.

    screenlab-data free --dest data --sec-user-agent "Jane Doe jane@example.com" --sources sec,boc,fred
    screenlab-data status [--dest data]
    screenlab-data free --sources shiller,tiingo      # total-return benchmarks (Tiingo: env TIINGO_API_KEY)
    screenlab-data hosts
"""
from __future__ import annotations

import argparse
import os
import sys

from . import fetch


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _print_manifest(m, out, dest=None) -> None:
    print(f"data folder: {m.dest}   fetched: {m.created or '-'}", file=out)
    for f in m.files:
        print(f"  {f['status']:<7} {f['source']:<8} {f['bytes']:>12,} B  {f['path']}", file=out)
    if m.errors:
        print("errors:", file=out)
        for e in m.errors:
            print(f"  {e['source']:<8} {e['error']}" + (f"   [allow {e['host']}]" if e.get("host") else ""), file=out)
    print("labs that can be built:", file=out)
    for l in m.labs:
        print(f"  {l['name']}: fundamentals={l['fundamentals']} prices={l['prices']} indices={l['indices']}", file=out)
    if not m.labs:
        print("  none (needs fundamentals_sec.csv from `sec` and prices_stooq.csv from `stooq`)", file=out)
    if m.macro:
        print("macro files: " + ", ".join(m.macro), file=out)
    for s, n in m.notes.items():
        print(f"note {s}: {n}", file=out)
    print(f"ETF presets ({fetch.ETF_NOTE}):", file=out)
    for t, e in fetch.ETF_PROXIES.items():
        print(f"  {t:<4} {e['tracks']:<40} MER {e['mer']:.4%}  {e['ccy']}  {e['url']}", file=out)
    if dest:
        for line in fetch.etf_suggestions(dest):
            print("  suggest: " + line, file=out)


def print_hosts(out=None) -> None:
    out = out or sys.stdout
    print("Hosts to allow (environment settings, Network access, Allowed domains):", file=out)
    for s in fetch.ALL_SOURCES:
        print(f"  {s:<8} {', '.join(fetch.HOSTS[s])}", file=out)
    print(f"Docs: {fetch.DOCS_URL}", file=out)
    print("Hostnames come from the adapters; the StatCan, Shiller and Tiingo-Canada details are unverified (realdata/fetch.py).", file=out)


def main(argv=None, out=None, opener=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="screenlab-data", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    fr = sub.add_parser("free", help="download the free datasets")
    fr.add_argument("--dest", default="data")
    fr.add_argument("--sec-user-agent", default=None, help="'Name email' (or env SEC_USER_AGENT)")
    fr.add_argument("--sources", default=",".join(fetch.SOURCES), help="comma list of: " + ", ".join(fetch.ALL_SOURCES) +
                    " (default: " + ",".join(fetch.SOURCES) + ")")
    fr.add_argument("--tiingo-tickers", default=",".join(fetch.TIINGO_DEFAULT_TICKERS),
                    help="Tiingo tickers (ETF presets -> indices_tiingo.csv, others -> prices_tiingo.csv)")
    fr.add_argument("--tiingo-api-key", default=None, help="or env TIINGO_API_KEY")
    fr.add_argument("--tickers", default=",".join(fetch.DEFAULT_TICKERS))
    fr.add_argument("--periods", default=None, help="SEC quarters, e.g. 2023q1,2023q2 (default last 12)")
    fr.add_argument("--start", default="2000-01-01")
    st = sub.add_parser("status", help="show the manifest and the labs that can be built")
    st.add_argument("--dest", default="data")
    sub.add_parser("hosts", help="print the network allow-list")
    a = ap.parse_args(argv)
    if a.cmd == "hosts":
        print_hosts(out)
        return 0
    if a.cmd == "status":
        try:
            m = fetch.Manifest.load(a.dest)
        except FileNotFoundError as e:
            print(e, file=out)
            return 1
        m.labs, m.macro = fetch.discover_labs(a.dest)
        _print_manifest(m, out, a.dest)
        return 0
    try:
        m = fetch.fetch_free(a.dest, _csv(a.sources) or fetch.SOURCES, a.sec_user_agent, _csv(a.periods),
                             _csv(a.tickers) or fetch.DEFAULT_TICKERS, a.start, opener,
                             progress=lambda f, msg: print(f"[{f:4.0%}] {msg}", file=out),
                             tiingo_api_key=a.tiingo_api_key, tiingo_tickers=_csv(a.tiingo_tickers))
    except ValueError as e:
        print(f"error: {e}", file=out)
        return 2
    _print_manifest(m, out, a.dest)
    if m.errors:
        print(f"{len(m.errors)} error(s); allow: {', '.join(m.hosts_flat())}  ({fetch.DOCS_URL})", file=out)
    return 1 if m.errors and not [f for f in m.files if f["status"] in ("ok", "cached")] else 0


if __name__ == "__main__":
    raise SystemExit(main())
