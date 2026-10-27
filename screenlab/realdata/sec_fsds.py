"""SEC Financial Statement Data Sets -> our fundamentals table.

Quarterly zips (https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip)
hold tab-separated `sub.txt` (adsh, cik, name, sic, form, period, fy, fp, filed) and `num.txt`
(adsh, tag, version, ddate, qtrs, uom, value). One output row per annual filing, with
`date = filed` (the knowable date, so no extra reporting lag). Fields that cannot be derived
from the supplied rows stay NaN.
"""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd

URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{period}.zip"

TAGS = {
    "sales": ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "eps": ["EarningsPerShareDiluted"],
    "equity": ["StockholdersEquity"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "ca": ["AssetsCurrent"],
    "cl": ["LiabilitiesCurrent"],
    "lt_debt": ["LongTermDebtNoncurrent"],
    "ni": ["NetIncomeLoss"],
    "assets": ["Assets"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "dividend": ["CommonStockDividendsPerShareDeclared"],
    "ebit": ["OperatingIncomeLoss"],
    "gross": ["GrossProfit"],
}
INSTANT = {"equity", "ca", "cl", "lt_debt", "assets"}      # balance-sheet items (qtrs == 0)
OUT_COLS = ["date", "ticker", "price", "market_cap", "sector", "eps", "eps_prev", "eps_avg3",
            "eps_avg3_10y_ago", "loss_years_5y", "loss_years_10y", "sales", "bvps", "dividend",
            "div_years", "current_ratio", "current_ratio_prev", "lt_debt", "net_current_assets",
            "roa", "roa_prev", "cfo_to_assets", "leverage", "leverage_prev", "shares", "shares_prev",
            "gross_margin", "gross_margin_prev", "asset_turnover", "asset_turnover_prev", "ebit",
            "enterprise_value", "invested_capital"]


def fetch(period: str, dest, user_agent: str | None = None) -> str:
    """Download e.g. '2024q1' to `dest` (a file path).

    The SEC requires a descriptive User-Agent naming you and a contact email, e.g.
    "Jane Doe jane@example.com"; pass it as `user_agent` or set SEC_USER_AGENT."""
    import os
    user_agent = user_agent or os.environ.get("SEC_USER_AGENT")
    if not user_agent or "@" not in user_agent:
        raise ValueError("SEC requires a User-Agent with your name and email: "
                         "fetch(..., user_agent='Your Name you@example.com') or set SEC_USER_AGENT")
    from urllib.request import Request, urlopen
    req = Request(URL.format(period=period), headers={"User-Agent": user_agent})
    with urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return str(dest)


def read_zip(path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(sub, num) DataFrames from a saved quarterly zip."""
    with zipfile.ZipFile(path) as z:
        sub = pd.read_csv(io.BytesIO(z.read("sub.txt")), sep="\t", dtype=str)
        num = pd.read_csv(io.BytesIO(z.read("num.txt")), sep="\t", dtype=str)
    return sub, num


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")


class _Facts:
    """Point-in-time lookup of facts: value of a field at a period end, as known on a filing date."""

    def __init__(self, sub, num):
        sub = sub[["adsh", "cik", "form", "filed"]].copy()
        sub["cik"] = sub["cik"].astype(str).str.lstrip("0")
        sub["filed"] = _date(sub["filed"])
        n = num.copy()
        if "coreg" in n:                                # consolidated figures only
            n = n[n["coreg"].isna() | (n["coreg"].astype(str).str.strip() == "")]
        n = n.merge(sub[["adsh", "cik", "filed"]], on="adsh")
        n["ddate"] = _date(n["ddate"])
        n["qtrs"] = pd.to_numeric(n["qtrs"], errors="coerce")
        n["value"] = pd.to_numeric(n["value"], errors="coerce")
        self.data = {}
        for field, tags in TAGS.items():
            q = 0 if field in INSTANT else 4
            for rank, tag in enumerate(tags):
                d = n[(n["tag"] == tag) & (n["qtrs"] == q)].dropna(subset=["value", "ddate"])
                d = d.assign(rank=rank)[["cik", "ddate", "filed", "value", "rank"]]
                self.data.setdefault(field, []).append(d)
        self.data = {k: pd.concat(v) for k, v in self.data.items()}
        self.sub = sub

    def get(self, cik, field, ddate, filed, years_back=0):
        """Value of `field` for the year ending `years_back` years before `ddate`, known by `filed`."""
        d = self.data[field]
        target = ddate - pd.DateOffset(years=years_back)
        m = d[(d["cik"] == cik) & (d["filed"] <= filed) & ((d["ddate"] - target).abs() <= pd.Timedelta(days=10))]
        if m.empty:
            return np.nan
        return m.sort_values(["rank", "filed"]).groupby("rank").tail(1).iloc[0]["value"]

    def count_losses(self, cik, ddate, filed, window):
        """Years with negative annual net income in the `window` years to `ddate`; NaN if any year is unseen."""
        vals = [self.get(cik, "ni", ddate, filed, k) for k in range(window)]
        return np.nan if any(np.isnan(v) for v in vals) else float(sum(v < 0 for v in vals))


def _div(a, b):
    return a / b if b and not np.isnan(a) and not np.isnan(b) else np.nan


def to_fundamentals(sub: pd.DataFrame, num: pd.DataFrame, tickers_map: dict, forms=("10-K",)) -> pd.DataFrame:
    """Annual-filing fundamentals (columns as fundamentals.csv). `tickers_map` is cik -> ticker."""
    facts = _Facts(sub, num)
    tmap = {str(int(k)): v for k, v in tickers_map.items()}
    s = facts.sub.merge(sub[["adsh", "period"]], on="adsh")
    s["period"] = _date(s["period"])
    s = s[s["form"].isin(forms) & s["cik"].isin(tmap)].dropna(subset=["period", "filed"])
    rows = []
    for r in s.itertuples():
        g = lambda f, k=0, p=r.period: facts.get(r.cik, f, p, r.filed, k)
        row = dict.fromkeys(OUT_COLS, np.nan)
        row.update(date=r.filed, ticker=tmap[r.cik])

        def yr(k):   # balance-sheet and flow ratios for the year k years back
            assets, ni = g("assets", k), g("ni", k)
            sales, shares = g("sales", k), g("shares", k)
            return dict(assets=assets, roa=_div(ni, assets), shares=shares,
                        lev=_div(g("lt_debt", k), assets), turn=_div(sales, assets),
                        cr=_div(g("ca", k), g("cl", k)), gm=_div(g("gross", k), sales))
        cur, prev = yr(0), yr(1)
        eps3 = [g("eps", k) for k in range(3)]
        eps3_old = [g("eps", k + 10) for k in range(3)]
        row.update(
            eps=g("eps"), eps_prev=g("eps", 1), sales=g("sales"), dividend=g("dividend"),
            bvps=_div(g("equity"), cur["shares"]), current_ratio=cur["cr"], current_ratio_prev=prev["cr"],
            lt_debt=g("lt_debt"), net_current_assets=g("ca") - g("cl"),
            roa=cur["roa"], roa_prev=prev["roa"], cfo_to_assets=_div(g("cfo"), cur["assets"]),
            leverage=cur["lev"], leverage_prev=prev["lev"], shares=cur["shares"], shares_prev=prev["shares"],
            gross_margin=cur["gm"], gross_margin_prev=prev["gm"], asset_turnover=cur["turn"],
            asset_turnover_prev=prev["turn"], ebit=g("ebit"),
            invested_capital=g("equity") + g("lt_debt"),
            loss_years_5y=facts.count_losses(r.cik, r.period, r.filed, 5),
            loss_years_10y=facts.count_losses(r.cik, r.period, r.filed, 10),
        )
        if not any(np.isnan(eps3)):
            row["eps_avg3"] = float(np.mean(eps3))
        if not any(np.isnan(eps3_old)):
            row["eps_avg3_10y_ago"] = float(np.mean(eps3_old))
        rows.append(row)
    out = pd.DataFrame(rows, columns=OUT_COLS)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)
