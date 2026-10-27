"""Screen definitions.

Each screen takes a point-in-time snapshot `s` (one row per ticker, ratios +
trailing price stats already joined) and returns an ordered list of tickers.
These are recreations of the *families* of screens Rothery regularly runs in
his Globe columns; the thresholds are sensible defaults to tune, not quotes
of any specific article's rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class Screen:
    name: str
    fn: Callable[..., list[str]]
    n: int = 10                     # target number of holdings
    params: dict = field(default_factory=dict)
    note: str = ""

    def __call__(self, s: pd.DataFrame) -> list[str]:
        return self.fn(s, n=self.n, **self.params)

    def with_overrides(self, **kw) -> "Screen":
        """Copy with params changed; keys naming a GrahamTests field update `tests`."""
        params, tests = dict(self.params), self.params.get("tests")
        for k, v in kw.items():
            if k == "n":
                continue
            if tests is not None and k in tests.__dataclass_fields__:
                tests = replace(tests, **{k: v})
            else:
                params[k] = v
        if tests is not None:
            params["tests"] = tests
        return Screen(self.name, self.fn, kw.get("n", self.n), params, self.note)


def _largest(s, k):  # liquidity / size filter used by most screens
    return s.nlargest(k, "market_cap")


# --- Dividend dogs: highest yielders among the biggest names -----------------
def dogs(s, n=10, universe=30):
    u = _largest(s, universe)
    return u[u["div_yield"] > 0].nlargest(n, "div_yield").index.tolist()


# --- Graham tests (The Intelligent Investor, ch. 14 defensive / ch. 15 enterprising)
@dataclass
class GrahamTests:
    """Each test is switched off by setting its threshold to None.

    Size thresholds are in the same currency units as the data ($M here);
    Graham's 1971 figure of $100M sales is roughly $750M today.
    """
    min_sales: float | None = 750
    min_current_ratio: float | None = 2.0       # industrials only
    max_debt_to_nca: float | None = 1.0         # LT debt / net current assets, industrials
    max_util_debt_to_equity: float | None = 2.0  # utilities' substitute for the two tests above
    loss_window: int = 10                       # years examined for deficits (5 or 10)
    max_loss_years: int | None = 0
    min_div_years: int | None = 20              # 0 = "pays a dividend now"
    min_eps_growth: float | None = 0.33         # 3y-avg EPS vs 3y-avg EPS 10 years ago
    max_pe: float | None = 15
    pe_basis: str = "avg3"                      # "avg3" (Graham) or "ttm"
    max_pb: float | None = 1.5
    max_pe_x_pb: float | None = 22.5
    pb_or_product: bool = True                  # Graham: pass P/B *or* P/E x P/B
    min_passes: int | None = None               # None = every enabled test must pass
    utility_sectors: tuple = ("Util",)


DEFENSIVE = GrahamTests()
ENTERPRISING = GrahamTests(
    min_sales=None, min_current_ratio=1.5, max_debt_to_nca=1.1, max_util_debt_to_equity=None,
    loss_window=5, min_div_years=0, min_eps_growth=0.0, max_pe=10, pe_basis="ttm",
    max_pb=1.2, max_pe_x_pb=None, pb_or_product=False)


def graham_report(s: pd.DataFrame, t: GrahamTests = DEFENSIVE) -> pd.DataFrame:
    """Boolean pass/fail per test (one column per enabled test) plus a `passes` count."""
    util = s["sector"].isin(t.utility_sectors)
    pe = s["pe_avg3"] if t.pe_basis == "avg3" else s["pe"]
    r = {}
    if t.min_sales is not None:
        r["size"] = s["sales"] >= t.min_sales
    if t.min_current_ratio is not None:
        r["current_ratio"] = util | (s["current_ratio"] >= t.min_current_ratio)
    if t.max_debt_to_nca is not None or t.max_util_debt_to_equity is not None:
        ind_ok = (True if t.max_debt_to_nca is None else
                  s["lt_debt"] <= t.max_debt_to_nca * s["net_current_assets"].clip(lower=0))
        util_ok = (True if t.max_util_debt_to_equity is None else
                   s["lt_debt"] <= t.max_util_debt_to_equity * s["book_equity"])
        r["debt"] = pd.Series(np.where(util, util_ok, ind_ok), index=s.index).astype(bool)
    if t.max_loss_years is not None:
        r["earnings_stability"] = s[f"loss_years_{t.loss_window}y"] <= t.max_loss_years
    if t.min_div_years is not None:
        r["dividend_record"] = (s["dividend"] > 0) & (s["div_years"] >= t.min_div_years)
    if t.min_eps_growth is not None:
        base = s["eps_avg3_10y_ago"].where(s["eps_avg3_10y_ago"] > 0)
        r["earnings_growth"] = (s["eps_avg3"] / base - 1) >= t.min_eps_growth
    if t.max_pe is not None:
        r["pe"] = pe <= t.max_pe
    pb_ok = None if t.max_pb is None else s["pb"] <= t.max_pb
    prod_ok = None if t.max_pe_x_pb is None else pe * s["pb"] <= t.max_pe_x_pb
    if t.pb_or_product and pb_ok is not None and prod_ok is not None:
        r["pb_or_product"] = pb_ok | prod_ok
    else:
        if pb_ok is not None:
            r["pb"] = pb_ok
        if prod_ok is not None:
            r["pe_x_pb"] = prod_ok
    out = pd.DataFrame(r, index=s.index).fillna(False)
    out["passes"] = out.sum(axis=1)
    return out


def graham(s, n=10, tests: GrahamTests = DEFENSIVE):
    rep = graham_report(s, tests)
    need = tests.min_passes or (rep.shape[1] - 1)
    pe = s["pe_avg3"] if tests.pe_basis == "avg3" else s["pe"]
    ok = rep[rep["passes"] >= need].copy()
    ok["g"] = (pe * s["pb"]).reindex(ok.index).to_numpy()   # (assign() on an empty frame re-adds rows)
    return ok.sort_values(["passes", "g"], ascending=[False, True]).head(n).index.tolist()


def graham_funnel(s: pd.DataFrame, t: GrahamTests = DEFENSIVE) -> pd.Series:
    """How many names survive each test applied cumulatively, in order."""
    rep = graham_report(s, t).drop(columns="passes")
    alive = pd.Series(True, index=s.index)
    out = {"universe": len(s)}
    for c in rep.columns:
        alive &= rep[c]
        out[c] = int(alive.sum())
    return pd.Series(out)


# --- Net-nets: price below net current asset value ---------------------------
def net_nets(s, n=15, min_ncav=1.0):
    ok = (s["ncav_to_mcap"] >= min_ncav) & (s["eps"] > 0)
    return s[ok].nlargest(n, "ncav_to_mcap").index.tolist()


# --- Piotroski F-score applied to the cheapest book-value quintile -----------
def f_score(s: pd.DataFrame) -> pd.Series:
    pts = [s["roa"] > 0, s["cfo_to_assets"] > 0, s["roa"] > s["roa_prev"],
           s["cfo_to_assets"] > s["roa"], s["leverage"] < s["leverage_prev"],
           s["current_ratio"] > s["current_ratio_prev"], s["shares"] <= s["shares_prev"],
           s["gross_margin"] > s["gross_margin_prev"],
           s["asset_turnover"] > s["asset_turnover_prev"]]
    return sum(p.astype(int) for p in pts)


def piotroski(s, n=10, pb_quantile=0.2, min_score=8):
    cheap = s[s["pb"] <= s["pb"].quantile(pb_quantile)]
    sc = f_score(cheap)
    return cheap.assign(f=sc)[sc >= min_score].sort_values(["f", "pb"], ascending=[False, True]) \
        .head(n).index.tolist()


# --- Greenblatt's Magic Formula: rank on earnings yield + return on capital --
def magic_formula(s, n=15, min_cap_rank=200, exclude=("Fin", "Util")):
    u = _largest(s, min_cap_rank)
    u = u[~u["sector"].isin(exclude) & (u["ebit"] > 0)]
    rank = u["earnings_yield"].rank(ascending=False) + u["roic"].rank(ascending=False)
    return rank.nsmallest(n).index.tolist()


# --- Cheap & strong: low P/E + low P/B, then the best 12-1 momentum ----------
def value_momentum(s, n=10, value_pool=40):
    u = s.dropna(subset=["pe", "pb", "mom_12_1"])
    v = (u["pe"].rank() + u["pb"].rank()).nsmallest(value_pool).index
    return u.loc[v].nlargest(n, "mom_12_1").index.tolist()


# --- Low-volatility dividend payers -----------------------------------------
def low_vol_dividend(s, n=15, universe=100):
    u = _largest(s, universe)
    u = u[(u["div_yield"] > 0) & (u["eps"] > 0)].dropna(subset=["vol_1y"])
    return u.nsmallest(n, "vol_1y").index.tolist()


REGISTRY: dict[str, Screen] = {sc.name: sc for sc in [
    Screen("dogs", dogs, 10, note="Top-10 yield from the 30 largest (Dogs/Beat the TSX)"),
    Screen("graham_defensive", graham, 10, {"tests": DEFENSIVE}, "Graham defensive investor"),
    Screen("graham_enterprising", graham, 10, {"tests": ENTERPRISING}, "Graham enterprising investor"),
    Screen("net_nets", net_nets, 15, note="Price < NCAV"),
    Screen("piotroski", piotroski, 10, note="F-score >= 8 in cheapest P/B quintile"),
    Screen("magic_formula", magic_formula, 15, note="Greenblatt EY + ROIC rank"),
    Screen("value_momentum", value_momentum, 10, note="Low P/E+P/B, best 12-1 momentum"),
    Screen("low_vol_dividend", low_vol_dividend, 15, note="Least volatile dividend payers"),
]}
