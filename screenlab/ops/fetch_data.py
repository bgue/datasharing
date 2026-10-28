"""fetch_data: download the free datasets into `data/` (see ../realdata/fetch.py, ../realdata/ACQUIRE.md)."""
from __future__ import annotations

from dataclasses import dataclass

from ..operations import Operation, arg, register_operation


@register_operation(builtin=True)
class FetchData(Operation):
    """Download the free datasets (SEC, Bank of Canada, FRED, Stooq, StatCan) into a data folder and list the labs they allow."""
    name = "fetch_data"
    long_running = True
    tracked_entry = False               # no trial counting, nothing recorded as a run of the lab
    takes_max_points = False

    @dataclass
    class Args:
        sources: list[str] | None = arg(None, "subset of sec, boc, fred, stooq, statcan (default: all)")
        dest: str = arg("data", "folder to write into (raw/ below it, plus the CSVs and MANIFEST.json)")
        sec_user_agent: str | None = arg(None, "SEC User-Agent 'Your Name you@example.com' (or env SEC_USER_AGENT)")
        tickers: list[str] | None = arg(None, "Stooq tickers (default XIU.TO, XIC.TO, SPY.US, ^SPX)")
        start: str = arg("2000-01-01", "first date kept for prices and macro series")
        periods: list[str] | None = arg(None, "SEC quarters such as '2023q1' (default: the last 12 completed quarters)")

    def run(self, lab, sources=None, dest: str = "data", sec_user_agent=None, tickers=None,
            start: str = "2000-01-01", periods=None, opener=None) -> dict:
        """Download the free datasets; returns {"manifest": {files, errors, allow_hosts}, "labs": [...]}.
        A failing source is an entry in `errors` (with the host to allow), never an exception."""
        from ..realdata import fetch
        m = fetch.fetch_free(dest, tuple(sources) if sources else fetch.SOURCES, sec_user_agent, periods,
                             tuple(tickers) if tickers else fetch.DEFAULT_TICKERS, start, opener)
        return m.result()

    # the result is already JSON, carries no data card or trial count, and stores no metrics
    def to_json(self, result, max_points=None, *, lab=None, kw=None):
        return result

    def result_json(self, result, max_points=None, *, lab=None, kw=None):
        return result

    def extract(self, result):
        from ..store import Extracted
        return Extracted()

    def metrics(self, result) -> list:
        return []
