"""account_compare: the same portfolio and the passive index in a taxable account, a TFSA and an RRSP (`Lab.account_compare`).

# Q2: Phase 3 "Comparing accounts". Nothing here is a new tax rule. The operation runs the backtest operation once per
account with `TaxConfig.account` replaced, reads the after-tax columns and the tax ledger of each run, and lines them up:
which account suits this portfolio, what each one costs in foreign withholding, and what it ignores (contribution room,
the RRSP deduction refund, AMT). REST routes, MCP tool, jobs and the store come from the operation registry.
"""
from __future__ import annotations

import dataclasses
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
import pandas as pd

from ..errors import ServerError
from ..jsonout import DEFAULT_MAX_POINTS, scalar, table
from ..metrics import M
from ..operations import Operation, arg, register_operation
from . import _request as R
from ._request import BenchmarkRequest, ConfigOverrides, ExitRequest, clean, config_overrides, parse_date

ACCOUNTS = R.TAX_ACCOUNTS
# the column order of the comparison table (after name / kind / account)
METRIC_COLS = (M.CAGR, M.AFTERTAX_CAGR, M.TAXDRAG_YR, M.AFTERTAX_EXCESS_YR, "WHLost/yr", "WHCredited/yr", "WHExempt/yr",
               "DivTax$", "GainTax$", "WithdrawalTax$", "TaxPaid$", "TaxDeferred/yr", "Rank", "GapToNext")
FOREIGN_COLS = ("portfolio", "class", "kind", "country", "share", "rate", "taxable", "tfsa", "rrsp")
NOT_MODELLED = ("not modelled: contribution room (a TFSA / RRSP holds only what the room allows), the RRSP deduction "
                "refund on the way in, AMT, and the choice of which assets to put where beyond this portfolio; "
                "same pre-tax money in each account")


# ---------------------------------------------------------------------------------------------------
# request: the tax settings are TaxRequest's, minus `account` (which this operation varies)
# ---------------------------------------------------------------------------------------------------
def _account_tax_request():
    """`TaxRequest` without `account`, built from its fields at import time so a field added there is inherited here
    (the model defaults to canadian / AB: this operation is about Canadian accounts)."""
    import typing
    hints = typing.get_type_hints(R.TaxRequest)
    out = []
    for f in dataclasses.fields(R.TaxRequest):
        if f.name == "account":
            continue
        default = {"model": "canadian", "province": "AB"}.get(f.name, f.default)
        fld = dataclasses.field(default=default, metadata=dict(f.metadata)) if f.default_factory is dataclasses.MISSING \
            else dataclasses.field(default_factory=f.default_factory, metadata=dict(f.metadata))
        out.append((f.name, hints[f.name], fld))
    cls = dataclasses.make_dataclass("AccountTaxRequest", out)
    cls.__module__ = __name__
    cls.__doc__ = ("Tax settings shared by every account: the fields of `config.tax` except `account` "
                   "(default model canadian, province AB).")
    return cls


AccountTaxRequest = _account_tax_request()


# ---------------------------------------------------------------------------------------------------
# capturing the tax ledger (the per-year table does not carry the wh_* event columns)
# ---------------------------------------------------------------------------------------------------
_SPY = {"n": 0, "orig": None}
_SPY_LOCK = threading.RLock()


def _spy(curve, events, tax, capital, warnings=()):
    """Stand-in for `taxes.assess` that keeps the pre-tax curve and the event ledger on the returned table's attrs."""
    tab, after = _SPY["orig"](curve, events, tax, capital, warnings=warnings)
    tab.attrs["ledger"] = (curve, events)
    return tab, after


@contextmanager
def _ledger_capture():
    from .. import taxes
    with _SPY_LOCK:
        if _SPY["n"] == 0:
            _SPY["orig"], taxes.assess = taxes.assess, _spy
        _SPY["n"] += 1
    try:
        yield
    finally:
        with _SPY_LOCK:
            _SPY["n"] -= 1
            if _SPY["n"] == 0:
                taxes.assess = _SPY["orig"]


def _wealth_years(curve: pd.Series) -> float:
    """Integral of the pre-tax wealth over time in wealth-years (start wealth = 1): events / this = a yearly rate
    on the average wealth."""
    dt = np.diff(((curve.index - curve.index[0]).days / 365.25).to_numpy())
    return float((curve.to_numpy()[:-1] * dt).sum())


def _ledger_sums(tab: pd.DataFrame) -> tuple:
    """(event column sums, wealth-years) of a tax table captured by `_ledger_capture`; (None, None) without it."""
    led = tab.attrs.get("ledger") if tab is not None else None
    if led is None:
        return None, None
    curve, ev = led
    from ..taxes import EVENT_COLS
    s = ev.reindex(columns=list(EVENT_COLS)).fillna(0.0).sum() if len(ev) else pd.Series(0.0, index=list(EVENT_COLS))
    wy = _wealth_years(curve) if len(curve) > 1 else 0.0
    return s, wy


def _withholding(tab, account: str, us_rate: float, gross_direct) -> dict:
    """Withholding as a yearly share of the average wealth, by what happens to it in `account`:
    lost (nothing recovers it), credited (taxable: direct withholding is creditable through the foreign tax credit),
    exempt (RRSP: US direct dividends are not withheld at all; what the treaty saved)."""
    s, wy = _ledger_sums(tab)
    nan = {"WHLost/yr": np.nan, "WHCredited/yr": np.nan, "WHExempt/yr": np.nan}
    if s is None or wy <= 0:
        return nan
    if account == "taxable":
        lost, cred, ex = s["wh_fund"], s["wh_fx"], 0.0
    elif account == "tfsa":
        lost, cred, ex = s["wh_fx"] + s["wh_fund"], 0.0, 0.0
    else:                                                              # rrsp: wh_fx is zero by construction
        lost, cred = s["wh_lost"] + s["wh_fund"], 0.0
        ex = max((gross_direct if gross_direct is not None else s["div_fx"] * us_rate) - s["wh_lost"], 0.0)
    return {"WHLost/yr": lost / wy, "WHCredited/yr": cred / wy, "WHExempt/yr": ex / wy}


def _gross_direct_withholding(tab) -> float | None:
    """What the direct foreign dividends would be withheld at, from a run in a non-RRSP account (pre-tax wealth units)."""
    s, _ = _ledger_sums(tab)
    return None if s is None else float(s["wh_fx"])


def _tax_dollars(tab) -> dict:
    if tab is None or not len(tab):
        return {"DivTax$": np.nan, "GainTax$": np.nan, "WithdrawalTax$": np.nan, "TaxPaid$": np.nan}
    t = tab
    return {"DivTax$": float(t["tax_dividends"].sum() + t["tax_foreign_dividends"].sum()),
            "GainTax$": float(t["tax_capital_gains"].sum()),
            "WithdrawalTax$": float(t["tax_rrsp_withdrawal"].sum()),
            "TaxPaid$": float(t["tax_paid"].sum())}


# ---------------------------------------------------------------------------------------------------
# the foreign treatment matrix
# ---------------------------------------------------------------------------------------------------
def treatment(kind: str, country: str, rate: float, home: str = "CA") -> dict:
    """What a dividend of this class does in each account (the matrix of docs/TAXES.md, Foreign holdings)."""
    pc = f"{100 * rate:g}%"
    if kind == "domestic":
        return {"taxable": "gross-up and dividend tax credit", "tfsa": "sheltered", "rrsp": "sheltered; taxed at withdrawal"}
    if kind == "fund":
        return {"taxable": f"{pc} withheld inside the fund, LOST; net taxed as foreign income",
                "tfsa": f"{pc} withheld inside the fund, LOST", "rrsp": f"{pc} withheld inside the fund, LOST"}
    return {"taxable": f"{pc} withheld, creditable (FTC)", "tfsa": f"{pc} withheld, LOST",
            "rrsp": "EXEMPT (treaty)" if country == "US" else f"{pc} withheld, LOST (exemption is US only)"}


def _class_label(kind: str, country: str, wrapper: str) -> str:
    if kind == "domestic":
        return "Canadian stock" if wrapper in ("stock", "") else f"Canadian {wrapper}"
    if kind == "fund":
        return f"Canadian ETF on {country} stocks"
    return f"{country} {'ETF (US-listed)' if wrapper == 'etf_us' else 'stock'} (direct)"


def foreign_table(results: dict, market, tc, bench_label: str, index_share: float, max_dates: int = 60) -> pd.DataFrame:
    """One row per (portfolio, class of holding): the share of its positions, and the treatment in each account.
    Positions are counted at the rebalance dates (equal weight per name); the index row uses the assumed foreign share."""
    rows = []
    for label, res in results.items():
        dates = sorted(res.holdings)
        if len(dates) > max_dates:
            dates = [dates[i] for i in np.unique(np.linspace(0, len(dates) - 1, max_dates).round().astype(int))]
        count: dict = {}
        total = 0
        for d in dates:
            names = list(res.holdings[d])
            if not names:
                continue
            snap = market.view(d)
            info = tc.foreign_info(names, snap)
            wrap = snap["wrapper"].reindex(info.index).fillna("stock") if "wrapper" in snap else pd.Series("stock", index=info.index)
            for t, r in info.iterrows():
                key = (_class_label(r["kind"], r["country"], str(wrap.get(t, "stock"))), r["kind"], r["country"], float(r["rate"]))
                count[key] = count.get(key, 0) + 1
                total += 1
        for (cls, kind, country, rate), n in sorted(count.items(), key=lambda kv: -kv[1]):
            tr = treatment(kind, country, rate)
            rows.append((label, cls, kind, country, n / total, rate, tr["taxable"], tr["tfsa"], tr["rrsp"]))
    if index_share > 0 or not rows:
        for cls, kind, country, share in (("US stock (direct)", "direct", "US", index_share),
                                          ("Canadian stock", "domestic", tc.home, 1.0 - index_share)):
            if share > 0:
                tr = treatment(kind, country, tc.rate_for(country) if kind != "domestic" else 0.0)
                rows.append((bench_label + " (assumed split)", cls, kind, country, share,
                             tc.rate_for(country) if kind != "domestic" else 0.0, tr["taxable"], tr["tfsa"], tr["rrsp"]))
    return pd.DataFrame(rows, columns=list(FOREIGN_COLS))


# ---------------------------------------------------------------------------------------------------
# the result
# ---------------------------------------------------------------------------------------------------
@dataclass
class AccountReport:
    table: pd.DataFrame                  # one row per (portfolio | index) x account; index "<name> | <account>"
    foreign: pd.DataFrame                # class of holding x treatment per account (FOREIGN_COLS)
    best: dict                           # name -> {"account", "AfterTaxCAGR", "next", "gap"} (None if not comparable)
    notes: list
    benchmark: str                       # label of the passive index
    accounts: tuple
    tax: dict = field(default_factory=dict)         # the shared settings: model description, withholding, RRSP convention
    reports: dict = field(default_factory=dict)     # account -> BacktestReport (curves, per-year tax tables)
    data_card: object = None

    def __str__(self):
        card = "\n".join([self.data_card.line(), *self.data_card.warnings()]) + "\n" if self.data_card is not None else ""
        t = self.table.drop(columns=["name", "kind", "account"]).copy().astype(object)
        for c in t.columns:
            t[c] = self.table[c].map(lambda x, c=c: fmt_cell(c, x))
        best = "".join(f"best for {n}: {b['account']}" + (f", {100 * b['gap']:+.2f} points/yr over {b['next']}" if b.get("next") else "")
                       + "\n" for n, b in self.best.items() if b)
        return (card + "Account comparison (same pre-tax portfolio in each account)\n" + t.to_string() + "\n" + best
                + "".join(f"note: {n}\n" for n in self.notes))


PCT_COLS = (M.CAGR, M.AFTERTAX_CAGR, M.TAXDRAG_YR, M.AFTERTAX_EXCESS_YR, "WHLost/yr", "WHCredited/yr", "WHExempt/yr",
            "TaxDeferred/yr", "GapToNext")


def fmt_cell(name: str, x) -> str:
    """A table cell as text: yearly rates as percent with two decimals, dollars with separators, rank as an integer."""
    if x is None or pd.isna(x):
        return ""
    if name in PCT_COLS:
        return f"{100 * x:.2f}%"
    if name.endswith("$"):
        return f"{x:,.0f}"
    return f"{int(x)}" if name == "Rank" else f"{x:.2f}"


def _rank(df: pd.DataFrame, exclude: set) -> tuple:
    """(Rank, GapToNext) columns, per name, by AfterTaxCAGR high to low among the comparable accounts."""
    rank = pd.Series(np.nan, index=df.index)
    gap = pd.Series(np.nan, index=df.index)
    best: dict = {}
    for name, g in df.groupby("name", sort=False):
        ok = g[~g["account"].isin(exclude) & g[M.AFTERTAX_CAGR].notna()].sort_values(M.AFTERTAX_CAGR, ascending=False)
        for i, (ix, r) in enumerate(ok.iterrows()):
            rank[ix] = i + 1
            if i + 1 < len(ok):
                gap[ix] = r[M.AFTERTAX_CAGR] - ok[M.AFTERTAX_CAGR].iloc[i + 1]
        if len(ok):
            top = ok.iloc[0]
            nxt = ok.iloc[1] if len(ok) > 1 else None
            best[name] = {"account": top["account"], M.AFTERTAX_CAGR: float(top[M.AFTERTAX_CAGR]),
                          "next": None if nxt is None else nxt["account"],
                          "gap": None if nxt is None else float(top[M.AFTERTAX_CAGR] - nxt[M.AFTERTAX_CAGR])}
        else:
            best[name] = None
    return rank, gap, best


def report_json(rep: AccountReport, max_points=DEFAULT_MAX_POINTS) -> dict:
    return {"benchmark": rep.benchmark, "accounts": list(rep.accounts),
            "table": table(rep.table, index=False), "foreign": table(rep.foreign, index=False),
            "best": {k: ({kk: scalar(vv) for kk, vv in v.items()} if v else None) for k, v in rep.best.items()},
            "notes": list(rep.notes), "tax": rep.tax}


def _extract_report(obj, j, out):
    out.summary[j("table")] = obj.table.drop(columns=["name", "kind", "account"])


@register_operation(builtin=True)
class AccountCompare(Operation):
    """Compare taxable, TFSA and RRSP for the same portfolios: after-tax return, foreign withholding, RRSP deferral."""
    name = "account_compare"
    registry_order = ("screens", "accounts", "tax", "start", "end", "config", "lab", "max_points", "exit",
                      "benchmark", "allow_price_benchmark")
    takes_max_points = False

    @dataclass
    class Args:
        screens: list[str] | None = arg(None, "screen names (default: every screen of the lab)")
        accounts: list[Literal["taxable", "tfsa", "rrsp"]] | None = arg(None, "accounts to compare (default all three)")
        tax: AccountTaxRequest | None = arg(None, "tax settings shared by every account (the fields of config.tax except "
                                                 "account); default: canadian model, Alberta, other_income 90000")
        start: str | None = arg(None, "first formation date (default: 13 months into the data)")
        end: str | None = arg(None, "last date")
        config: ConfigOverrides | None = arg(None, "Config overrides (rebalance_months, cost_bps, ...); its `tax`, if given "
                                                   "and `tax` is not, is the shared tax settings")
        exit: ExitRequest | None = arg(None, "exit policy applied to every screen")
        benchmark: str | BenchmarkRequest | None = arg(None, "index name, or {index, dividend_yield, mer, cost_bps}, replacing the lab's passive benchmark")
        allow_price_benchmark: bool = arg(False, "keep a price-index benchmark (excess return is then overstated); by default it is refused")

    # ---- computation
    def run(self, lab, screens=None, accounts=None, tax=None, start=None, end=None, benchmark=None, exit=None,
            allow_price_benchmark=None, **cfg) -> AccountReport:
        """Run the same screens and the passive index under each account (default taxable, TFSA, RRSP) and compare.

        `tax` is a `TaxConfig` whose `account` is replaced in turn (default: `config.tax` of the call or the lab, else the
        canadian model for Alberta with other income of 90,000 a year). Returns an `AccountReport`: `.table` (one row per
        portfolio or index and account), `.foreign`, `.best`, `.notes`. Pure computation on the lab's data: no network."""
        from .. import taxes
        accts = tuple(accounts) if accounts else ACCOUNTS
        bad = [a for a in accts if a not in ACCOUNTS]
        if bad or len(set(accts)) != len(accts):
            raise ServerError(400, f"accounts must be distinct names from {list(ACCOUNTS)}, got {list(accts)}")
        base = tax or cfg.pop("tax", None) or lab.config.tax or R._tax_config({"model": "canadian", "province": "AB",
                                                                                "account": "taxable"})
        cfg.pop("tax", None)
        if not isinstance(base, taxes.TaxConfig):
            raise ServerError(400, f"`tax` must be a TaxConfig or a tax object, got {type(base).__name__}")
        card = lab.data_card(benchmark)
        notes: list = []
        share = getattr(card, "foreign_share", None)
        if base.index_foreign_share == 0.0 and share:
            base = replace(base, index_foreign_share=float(share))
            notes.append(f"passive index taxed with a foreign dividend share of {100 * share:.0f}% (the data card's share of "
                         f"foreign names); set `index_foreign_share` to change it")
        bt = lab_op("backtest")
        reports, ledgers = {}, {}
        with _ledger_capture():
            for a in accts:
                tc = replace(base, account=a)
                reports[a] = bt.run(lab, screens=screens, start=start, end=end, benchmark=benchmark, exit=exit,
                                    allow_price_benchmark=allow_price_benchmark, tax=tc, **cfg)
        us_rate = base.rate_for("US")
        rows, results = [], {}
        first = reports[accts[0]]
        labels = [first.benchmark_label, *first.results]
        gross = {}
        for lab_name in labels:                      # direct withholding at source, from a run that does not exempt it
            for a in accts:
                if a != "rrsp":
                    tab = reports[a].tax.get(lab_name)
                    gross[lab_name] = _gross_direct_withholding(tab)
                    break
        for lab_name in labels:
            is_index = lab_name == first.benchmark_label
            for a in accts:
                rep = reports[a]
                r = rep.table.loc[lab_name]
                tab = rep.tax.get(lab_name)
                row = {"name": lab_name, "kind": "index" if is_index else "portfolio", "account": a,
                       M.CAGR: r.get(M.CAGR), M.AFTERTAX_CAGR: r.get(M.AFTERTAX_CAGR), M.TAXDRAG_YR: r.get(M.TAXDRAG_YR),
                       M.AFTERTAX_EXCESS_YR: np.nan if is_index else r.get(M.AFTERTAX_EXCESS_YR, np.nan),
                       **_withholding(tab, a, us_rate, gross.get(lab_name)), **_tax_dollars(tab),
                       "TaxDeferred/yr": r.get("TaxDeferred/yr", np.nan) if a == "taxable" else np.nan}
                rows.append(row)
        for rep in reports.values():                 # keep the tables free of the captured ledger
            for tab in rep.tax.values():
                tab.attrs.pop("ledger", None)
            for res in rep.results.values():
                if res.tax is not None:
                    res.tax.attrs.pop("ledger", None)
        df = pd.DataFrame(rows)
        df.index = [f"{n} | {a}" for n, a in zip(df["name"], df["account"])]
        untaxed_rrsp = "rrsp" in accts and replace(base, account="rrsp").rrsp_rate() is None
        df["Rank"], df["GapToNext"], best = _rank(df, {"rrsp"} if untaxed_rrsp else set())
        # notes
        if card is not None and card.foreign_line():
            notes.append(card.foreign_line())
        elif share is None:
            notes.append("data card: no security metadata, so a name is foreign only if listed in TaxConfig.foreign or "
                         "foreign_col; the foreign treatment below may be missing")
        if "rrsp" in accts:
            conv = replace(base, account="rrsp").rrsp_convention()
            if untaxed_rrsp:
                notes.append(f"{conv}: RRSP shows growth with no tax at withdrawal and is left out of Rank; give "
                             f"rrsp_withdrawal_rate (or rrsp_withdrawal_income)")
            else:
                notes.append(conv)
        warns = []
        for rep in reports.values():
            for tab in rep.tax.values():
                warns += [w for w in tab.attrs.get("warnings", ()) if w not in warns and w != taxes.RRSP_UNTAXED_WARNING]
        yrs = [w for w in warns if re.match(r"year \d{4}: ", w)]                       # one line for a run of years
        if yrs:
            notes.append(f"years {yrs[0][5:9]}-{yrs[-1][5:9]}: " + yrs[0].split(": ", 1)[1])
        notes += [("CAD-basis warning: " + w) if w == taxes.FX_WARNING else w for w in warns if w not in yrs]
        if getattr(base, "harvest", None) is not None:
            notes.append("loss harvesting is on: TaxDeferred/yr is shown for the taxable account only (a harvested loss has no "
                         "tax value inside a TFSA or an RRSP)")
        notes.append(NOT_MODELLED)
        fr = foreign_table(first.results, lab.market, base, first.benchmark_label, base.index_foreign_share)
        desc = base.model.describe() if hasattr(base.model, "describe") else {"model": type(base.model).__name__}
        info = {"model": scalar_dict(desc), "withholding": dict(base.withholding),
                "rrsp_convention": replace(base, account="rrsp").rrsp_convention(),
                "index_foreign_share": base.index_foreign_share, "capital": base.capital}
        return AccountReport(df, fr, best, notes, first.benchmark_label, accts, info, reports, first.data_card)

    # ---- request -> run keywords
    def build(self, lab, a) -> dict:
        cfg = config_overrides(a.config)
        if a.exit is not None and not isinstance(a.exit, dict):
            raise ServerError(400, "`exit` must be an object (see list_signals)")
        pol = R.exit_policy(lab, a.exit) if a.exit else None
        bm, _ = R.benchmarks(a.benchmark, None)
        tax = a.tax
        if tax is not None and cfg.get("tax") is not None:
            raise ServerError(400, "give the tax settings as `tax` or as `config.tax`, not both")
        if tax is not None:
            if not isinstance(tax, dict):
                raise ServerError(400, "`tax` must be an object (the fields of config.tax except account)")
            if "account" in tax:
                raise ServerError(400, "`tax.account` is not allowed: the operation sets it for each account "
                                       "(use `accounts` to choose them)")
            d = {"model": "canadian", **tax, "account": "taxable"}
            if d["model"] == "canadian":
                d.setdefault("province", "AB")
            cfg["tax"] = R._tax_config(d)
        elif cfg.get("tax") is None:
            cfg.pop("tax", None)
        t = cfg.pop("tax", None)
        return clean(dict(screens=a.screens, accounts=a.accounts, tax=t, start=parse_date("start", a.start),
                          end=parse_date("end", a.end), benchmark=bm, exit=pol,
                          allow_price_benchmark=bool(a.allow_price_benchmark) or None, **cfg))

    def to_json(self, result, max_points=DEFAULT_MAX_POINTS, *, lab=None, kw=None):
        return report_json(result, max_points)


def lab_op(name: str):
    from ..operations import OPERATIONS
    return OPERATIONS[name]


def scalar_dict(d: dict) -> dict:
    return {str(k): (scalar_dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, (list, tuple)) else scalar(v)))
            for k, v in d.items()}


def _register_extractors():
    from .. import store
    store.register_extractor("AccountReport", _extract_report)


_register_extractors()
