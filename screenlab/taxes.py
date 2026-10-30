"""Taxes: the `TaxModel` interface, a PLACEHOLDER Canadian model, and the yearly assessment (DESIGN.md J5, ROADMAP Phase 3).

    THE RATES IN `PlaceholderCanadianTax` ARE PLACEHOLDERS. THEY EXIST SO THE TAX STAGE CAN RUN END TO END AND
    FOR TESTS ONLY; USE `CanadianTax` (tax_ca tables) FOR REAL NUMBERS (A `TaxModel`, SEE
    docs/TAXES.md). EVERY RATE MUST BE CHECKED AGAINST THE CRA TABLES (AND THE PROVINCIAL TABLES) FOR THE TAX
    YEAR BEFORE ANY NUMBER IT PRODUCES IS RELIED ON. THIS IS NOT TAX ADVICE.

What lives here
    TaxModel                   the small interface a tax module implements (below)
    NoTax                      every tax is zero
    PlaceholderCanadianTax     placeholder rates as constructor parameters (province, income, gross-up, credits)
    TaxConfig                  `Config.tax`: model + account + US withholding + how tax is paid
    assess(...)                the yearly engine: realized events -> tax per calendar year -> after-tax curve
    register_tax_model(tag)    class decorator: makes a model serializable and findable by tag

The interface (all amounts are dollars of the simulated account, taxes are non-negative dollars)

    tax_on_dividends(amount, eligible, year)                       Canadian dividends received (cash, before gross-up)
    tax_on_capital_gain(gain, year)                                NET taxable capital gain of the year (gain > 0):
                                                                    inclusion rate x marginal rate; the stage carries
                                                                    losses forward, the model never sees a loss
    tax_on_foreign_dividends(amount, withholding_rate, year, account)
                                                                    foreign (US) dividends, gross of withholding; returns
                                                                    ALL tax on them: withholding at source plus Canadian
                                                                    tax net of any foreign tax credit
    describe() -> dict                                              parameters, for reports and run records

Optional joint path (a model may provide it; `assess` then uses it instead of the three per-item calls, so dividends,
foreign income and gains STACK correctly within one year):

    assess_year(year, *, eligible_dividends, non_eligible_dividends, foreign_income, foreign_withholding,
                capital_gains_net, account) -> YearTax(total, breakdown)
                                                                    one calendar year at once, all dollars of the account
                                                                    (cash dividends, foreign income gross of withholding,
                                                                    `foreign_withholding` = dollars withheld at source,
                                                                    `capital_gains_net` = net gain after loss carry-forward);
                                                                    total = tax caused by the investments (salary tax is not
                                                                    included); breakdown has dividends / foreign /
                                                                    capital_gains (sum = total) and optional warnings

`province` is an attribute of the model; `year` is passed to every call so a model can use the rates of that year.
`account` is "taxable", "tfsa" or "rrsp". In "tfsa" / "rrsp" the stage does not call `tax_on_dividends` or
`tax_on_capital_gain` (sheltered); it calls `tax_on_foreign_dividends`, whose `account` argument decides the
withholding (TFSA: withheld and not recoverable; RRSP: exempt on US-listed securities under the treaty).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .serial import serializable

ACCOUNTS = ("taxable", "tfsa", "rrsp")

# illustrative Ontario combined (federal + provincial, surtax included) marginal rates on ordinary income by
# taxable-income threshold. PLACEHOLDER: from memory of a recent year's published table; verify with the CRA / Ontario.
ON_ILLUSTRATIVE_BRACKETS = ((0.0, 0.2005), (51_446.0, 0.2415), (55_867.0, 0.2965), (90_287.0, 0.3148),
                            (102_894.0, 0.3389), (106_732.0, 0.3791), (111_733.0, 0.4341), (150_000.0, 0.4497),
                            (173_205.0, 0.4829), (220_000.0, 0.4985), (246_752.0, 0.5353))


@dataclass(frozen=True)
class YearTax:
    """Result of a joint `assess_year`: total tax in dollars (investment-caused, withholding included) and its split."""
    total: float
    breakdown: dict = field(default_factory=dict)


@runtime_checkable
class TaxModel(Protocol):
    """What a tax module implements (module docstring). Make it serializable with `@register_tax_model("Tag")`."""
    province: str

    def tax_on_dividends(self, amount: float, eligible: bool, year: int) -> float: ...
    def tax_on_capital_gain(self, gain: float, year: int) -> float: ...
    def tax_on_foreign_dividends(self, amount: float, withholding_rate: float, year: int, account: str) -> float: ...
    def describe(self) -> dict: ...


TAX_MODEL_TAGS: dict = {}      # serialization tag -> class, filled by register_tax_model


def register_tax_model(tag: str):
    """Class decorator for a `TaxModel` (a dataclass, or a class with its own `to_dict` / `from_dict`): registers the
    serialization tag (so a RunSpec / run key can carry it) and records the class in `TAX_MODEL_TAGS[tag]`."""
    def wrap(cls):
        cls = serializable(tag)(cls)
        TAX_MODEL_TAGS[tag] = cls
        return cls
    return wrap


@register_tax_model("NoTax")
@dataclass(frozen=True)
class NoTax:
    """Every tax is zero (the account behaves as untaxed)."""
    province: str = ""

    def tax_on_dividends(self, amount, eligible, year):
        return 0.0

    def tax_on_capital_gain(self, gain, year):
        return 0.0

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        return 0.0

    def describe(self) -> dict:
        return {"model": "NoTax"}


@register_tax_model("PlaceholderCanadianTax")
@dataclass(frozen=True)
class PlaceholderCanadianTax:
    """PLACEHOLDER Canadian personal tax: REPLACE WITH THE OWNER'S MODULE; CHECK EVERY RATE AGAINST THE CRA TABLES.

    province       "ON" has illustrative defaults; any other province needs `marginal_rate`, `provincial_dtc` and
                   `provincial_dtc_noneligible` given explicitly
    income         the investor's other taxable income, $, used only to look up the marginal rate in `brackets`
    marginal_rate  combined marginal rate on ordinary income; None = looked up from `brackets` at `income`
    brackets       ((threshold $, combined marginal rate), ...) ascending; default ON_ILLUSTRATIVE_BRACKETS
    eligible_dividend_grossup / federal_dtc / provincial_dtc            eligible Canadian dividends
    noneligible_grossup / federal_dtc_noneligible / provincial_dtc_noneligible   other Canadian dividends
                   the credits are fractions of the GROSSED-UP amount
    capital_gains_inclusion   share of a capital gain that is taxable (0.5)

    Dividends: tax = max(0, grossed_up x marginal_rate - grossed_up x (federal_dtc + provincial_dtc)).
    Capital gain: gain x inclusion x marginal_rate. Foreign dividends: taxable account pays
    amount x max(withholding_rate, marginal_rate) (withholding at source, Canadian tax net of the foreign tax credit
    limited to the Canadian tax); TFSA pays only the withholding; RRSP pays none (treaty exemption, assumed US-listed).
    The marginal rate is constant (no bracket creep from the simulated income), the same in every year.
    """
    province: str = "ON"
    income: float = 100_000.0
    marginal_rate: float | None = None
    brackets: tuple = ON_ILLUSTRATIVE_BRACKETS
    eligible_dividend_grossup: float = 0.38
    federal_dtc: float = 0.150198
    provincial_dtc: float | None = None
    noneligible_grossup: float = 0.15
    federal_dtc_noneligible: float = 0.090301
    provincial_dtc_noneligible: float | None = None
    capital_gains_inclusion: float = 0.5

    def __post_init__(self):
        if self.province != "ON" and (self.marginal_rate is None or self.provincial_dtc is None
                                      or self.provincial_dtc_noneligible is None):
            raise ValueError(f"PlaceholderCanadianTax has illustrative defaults for ON only; for {self.province!r} give "
                             f"marginal_rate, provincial_dtc and provincial_dtc_noneligible (from the CRA / provincial tables)")
        if not 0.0 <= self.capital_gains_inclusion <= 1.0:
            raise ValueError("capital_gains_inclusion must be in [0, 1]")

    @property
    def marginal(self) -> float:
        if self.marginal_rate is not None:
            return float(self.marginal_rate)
        rate = self.brackets[0][1]
        for thr, r in self.brackets:
            if self.income >= thr:
                rate = r
        return float(rate)

    @property
    def _pdtc(self) -> float:
        return 0.10 if self.provincial_dtc is None else float(self.provincial_dtc)

    @property
    def _pdtc_ne(self) -> float:
        return 0.029863 if self.provincial_dtc_noneligible is None else float(self.provincial_dtc_noneligible)

    def tax_on_dividends(self, amount, eligible, year):
        if amount <= 0:
            return 0.0
        if eligible:
            gross, credit = 1 + self.eligible_dividend_grossup, self.federal_dtc + self._pdtc
        else:
            gross, credit = 1 + self.noneligible_grossup, self.federal_dtc_noneligible + self._pdtc_ne
        return max(0.0, amount * gross * (self.marginal - credit))

    def tax_on_capital_gain(self, gain, year):
        return max(0.0, gain) * self.capital_gains_inclusion * self.marginal

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        if amount <= 0 or account == "rrsp":
            return 0.0
        if account == "tfsa":
            return amount * withholding_rate
        return amount * max(withholding_rate, self.marginal)

    def describe(self) -> dict:
        return {"model": "PlaceholderCanadianTax", "PLACEHOLDER": True, "province": self.province,
                "income": self.income, "marginal_rate": self.marginal,
                "eligible_dividend_grossup": self.eligible_dividend_grossup,
                "federal_dtc": self.federal_dtc, "provincial_dtc": self._pdtc,
                "noneligible_grossup": self.noneligible_grossup,
                "federal_dtc_noneligible": self.federal_dtc_noneligible,
                "provincial_dtc_noneligible": self._pdtc_ne,
                "capital_gains_inclusion": self.capital_gains_inclusion,
                "note": "placeholder rates: replace with the owner's module and check against CRA tables for the year"}


@register_tax_model("canadian_tax")
@dataclass(frozen=True)
class CanadianTax:
    """Canadian personal tax from the `tax_ca` tables (federal + AB / BC), a joint per-year model. NOT TAX ADVICE.

    province        "AB" | "BC"
    year            tax year of the tables (2025); calendar years outside the tables use the nearest table year and
                    record a warning (`Result.tax.attrs["warnings"]`)
    employment_income, deductions   the investor's other income and deductions, $ a year (constant over the simulation)
    eligible_share  share of the Canadian cash dividends that are eligible (rest non-eligible)

    Stacking within a year: salary -> dividends (grossed up) -> foreign income -> capital gains; each layer's tax is the
    increase in the total, so the tax attributed to the investments is total(with) - total(salary only). A negative
    marginal (the dividend tax credit can exceed the bracket tax) is passed through; `assess` never pays less than zero.
    Foreign tax credit (approximation): min(withholding, Canadian tax on the foreign income stacked after salary and
    dividends). The returned tax on foreign income is withholding + Canadian tax net of the credit.
    Accounts: taxable taxes everything; tfsa: no tax, foreign withholding lost; rrsp: no tax, US-listed withholding exempt.
    """
    province: str = "AB"
    year: int = 2025
    employment_income: float = 0.0
    deductions: float = 0.0
    eligible_share: float = 1.0

    def __post_init__(self):
        from .tax_ca import JURISDICTIONS, TAX_YEARS
        object.__setattr__(self, "province", str(self.province).upper())
        if self.province not in JURISDICTIONS:
            raise ValueError(f"province must be one of {sorted(JURISDICTIONS)}, got {self.province!r}")
        if int(self.year) not in TAX_YEARS:
            raise ValueError(f"year must be one of {list(TAX_YEARS)}, got {self.year!r}")
        if not 0.0 <= self.eligible_share <= 1.0:
            raise ValueError("eligible_share must be in [0, 1]")

    def _table_year(self, year: int) -> tuple:
        from .tax_ca import TAX_YEARS
        ty = min(TAX_YEARS, key=lambda t: (abs(t - year), -t)) if year not in TAX_YEARS else year
        warn = None if ty == year else f"year {year}: no tax table, used {ty}"
        return ty, warn

    def _compute(self, year, **kw):
        from .tax_ca import compute
        return compute(self.province, year, employment_income=self.employment_income, deductions=self.deductions, **kw)

    def assess_year(self, year, *, eligible_dividends=0.0, non_eligible_dividends=0.0, foreign_income=0.0,
                    foreign_withholding=0.0, capital_gains_net=0.0, account="taxable") -> YearTax:
        ty, warn = self._table_year(int(year))
        warnings = [warn] if warn else []
        wh = max(0.0, foreign_withholding) if foreign_income > 0 else 0.0
        if account == "rrsp":
            return YearTax(0.0, {"dividends": 0.0, "foreign": 0.0, "capital_gains": 0.0, "warnings": warnings})
        if account == "tfsa":
            return YearTax(wh, {"dividends": 0.0, "foreign": wh, "capital_gains": 0.0, "warnings": warnings})
        r = self._compute(ty, eligible_dividends=eligible_dividends, non_eligible_dividends=non_eligible_dividends,
                          foreign_income=foreign_income, foreign_tax_paid=wh, capital_gains=max(0.0, capital_gains_net))
        b = r.breakdown
        div, fx, cg = b["tax_on_dividends_marginal"], b["tax_on_foreign_income_marginal"] + wh, b["tax_on_capital_gains_marginal"]
        return YearTax(div + fx + cg, {"dividends": div, "foreign": fx, "capital_gains": cg,
                                       "foreign_tax_credit": b.get("foreign_tax_credit", 0.0), "warnings": warnings})

    # per-item calls (the protocol's fallback path; each is the joint path with one layer)
    def tax_on_dividends(self, amount, eligible, year):
        if amount <= 0:
            return 0.0
        e, n = (amount, 0.0) if eligible else (0.0, amount)
        return max(0.0, self.assess_year(year, eligible_dividends=e, non_eligible_dividends=n).total)

    def tax_on_capital_gain(self, gain, year):
        return max(0.0, self.assess_year(year, capital_gains_net=gain).total) if gain > 0 else 0.0

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        if amount <= 0:
            return 0.0
        return max(0.0, self.assess_year(year, foreign_income=amount, foreign_withholding=amount * withholding_rate,
                                         account=account).total)

    def describe(self) -> dict:
        from .tax_ca import TAX_YEARS
        return {"model": "CanadianTax", "province": self.province, "year": self.year, "tables": list(TAX_YEARS),
                "employment_income": self.employment_income, "deductions": self.deductions,
                "eligible_share": self.eligible_share,
                "stacking": "salary -> dividends -> foreign -> capital gains", "ftc": "approximation",
                "note": "tax_ca tables; not tax advice"}


TAX_MODELS: dict = {"none": NoTax, "placeholder": PlaceholderCanadianTax, "canadian": CanadianTax}   # config by name


@serializable("TaxConfig")
@dataclass(frozen=True)
class TaxConfig:
    """`Config.tax`: how the backtest is taxed. `None` on the Config (the default) means no tax at all.

    model             a `TaxModel` (default `PlaceholderCanadianTax()`)
    account           "taxable" | "tfsa" | "rrsp"
    us_withholding    withholding rate on foreign dividends (0.15 under the treaty)
    pay_from_portfolio  True: the year's tax is paid from the portfolio at the year's last bar (wealth shrinks, the
                      after-tax curve compounds on what is left); False: paid from outside, the after-tax curve is
                      the pre-tax curve less the cumulative tax paid (no growth on the money paid out)
    capital           starting wealth in $ (taxes are on dollar amounts); None = `Config.capital`
    foreign           tickers whose dividends are foreign (withholding applies)
    foreign_col       snapshot column naming a security's country; a value other than `home` means foreign
    liquidate_at_end  True: realize all unrealized gains on the last date (after-tax value of selling everything)
    index_dividend_yield  yield assumed for a passive index that has none of its own (None = cap-weighted dividend
                      yield of the universe at the start date; see docs/TAXES.md)
    index_foreign_share   share of the passive index's dividends that are foreign
    """
    model: object = field(default_factory=PlaceholderCanadianTax)
    account: str = "taxable"
    us_withholding: float = 0.15
    pay_from_portfolio: bool = True
    capital: float | None = None
    foreign: tuple = ()
    foreign_col: str | None = None
    home: str = "CA"
    liquidate_at_end: bool = False
    index_dividend_yield: float | None = None
    index_foreign_share: float = 0.0

    def __post_init__(self):
        if self.account not in ACCOUNTS:
            raise ValueError(f"account must be one of {ACCOUNTS}, got {self.account!r}")
        if not 0.0 <= self.us_withholding < 1.0:
            raise ValueError("us_withholding must be a rate in [0, 1)")
        object.__setattr__(self, "foreign", tuple(self.foreign))

    def foreign_mask(self, tickers, snap: pd.DataFrame) -> np.ndarray:
        """bool per ticker: dividends are foreign."""
        t = pd.Index(tickers)
        m = t.isin(list(self.foreign))
        if self.foreign_col and self.foreign_col in snap:
            m = m | (snap[self.foreign_col].reindex(t).fillna(self.home).to_numpy() != self.home)
        return np.asarray(m, bool)


# --------------------------------------------------------------------------- the yearly engine
EVENT_COLS = ("div_ca", "div_fx", "gain", "loss")        # pre-tax wealth units (start wealth = 1); loss <= 0
TAX_COLS = ["dividends_ca", "dividends_foreign", "realized_gains", "realized_losses", "loss_carry_out",
            "tax_dividends", "tax_foreign_dividends", "tax_capital_gains", "tax_paid", "cum_tax_paid",
            "wealth_pre_tax", "wealth_after_tax", "cum_drag"]


def assess(curve: pd.Series, events: pd.DataFrame, tax: TaxConfig, capital: float) -> tuple:
    """Tax per calendar year and the after-tax curve.

    `curve` pre-tax wealth (1.0 at the start); `events` indexed by date with columns `EVENT_COLS`, in the same
    wealth units as `curve` (an event of 0.01 is 1% of the starting wealth). Dollars = units x capital x the
    after-tax factor in force (tax paid so far shrinks the account, and with it the dollar size of later events). Returns
    (DataFrame per year with `TAX_COLS`, after-tax curve Series).

    Per year: dividends are taxed as received; realized gains net against realized losses and against losses
    carried forward from earlier years (carried within the simulation, nominal, no expiry); registered accounts
    (tfsa / rrsp) skip domestic dividend and capital gain tax and their losses are not carried. The tax is paid at
    the year's last bar (the last bar of the data closes the final, possibly part, year).

    A model with `assess_year` is assessed jointly per year (dividends split by its `eligible_share`, foreign withholding
    = `tax.us_withholding` x foreign income, gains net of carry-forward; registered accounts get the same call and the
    model zeroes what the account shelters); else the three per-item calls are used. Model warnings (e.g. a year beyond
    its tables) go to `table.attrs["warnings"]`. A negative year tax (credits above the tax) is floored at 0."""
    model, acct = tax.model, tax.account
    ev = events.reindex(columns=list(EVENT_COLS)).fillna(0.0) if len(events) else pd.DataFrame(columns=list(EVENT_COLS), dtype=float)
    by_year = ev.groupby(ev.index.year).sum() if len(ev) else ev
    yrs = curve.index.year
    last_bar = curve.groupby(yrs).apply(lambda s: s.index[-1])
    factor = pd.Series(1.0, index=curve.index)             # share of pre-tax wealth left, bar by bar (portfolio-paid)
    paid_cum = pd.Series(0.0, index=curve.index)           # cumulative $ paid (outside-paid)
    f, carry, cum = 1.0, 0.0, 0.0
    rows, warnings = {}, []
    for y in sorted(set(yrs)):
        e = by_year.loc[y] if y in by_year.index else pd.Series(0.0, index=list(EVENT_COLS))
        scale = capital * (f if tax.pay_from_portfolio else 1.0)
        dca, dfx, g, l = (float(e[c]) * scale for c in EVENT_COLS)
        t_div = t_fx = t_cg = 0.0
        carry_out = carry
        taxable = 0.0
        if acct == "taxable":
            net = g + l - carry                             # l <= 0
            taxable = max(net, 0.0)
            carry_out = max(-net, 0.0)
        if hasattr(model, "assess_year"):                   # joint path: the layers stack within the year
            sh = float(getattr(model, "eligible_share", 1.0))
            yt = model.assess_year(int(y), eligible_dividends=dca * sh, non_eligible_dividends=dca * (1 - sh),
                                   foreign_income=dfx, foreign_withholding=dfx * tax.us_withholding,
                                   capital_gains_net=taxable, account=acct)
            bd = yt.breakdown
            t_div, t_fx, t_cg = (float(bd.get(k, 0.0)) for k in ("dividends", "foreign", "capital_gains"))
            warnings.extend(w for w in bd.get("warnings", ()) if w not in warnings)
        else:
            if acct == "taxable":
                t_div = model.tax_on_dividends(dca, True, int(y)) if dca > 0 else 0.0
                t_cg = model.tax_on_capital_gain(taxable, int(y)) if taxable > 0 else 0.0
            if dfx > 0:
                t_fx = model.tax_on_foreign_dividends(dfx, tax.us_withholding, int(y), acct)
        carry = carry_out
        total = max(0.0, float(t_div + t_fx + t_cg))      # a net credit is not paid out
        end = last_bar[y]
        wealth = capital * float(curve.loc[end]) * (f if tax.pay_from_portfolio else 1.0)
        pay = min(total, wealth) if wealth > 0 else 0.0
        mask = curve.index >= end
        if tax.pay_from_portfolio:
            f_new = f * (1 - pay / wealth) if wealth > 0 else 0.0
            factor[mask] = f_new
            after = capital * float(curve.loc[end]) * f_new
            f = f_new
        else:
            paid_cum[mask] += pay
            after = wealth - (cum + pay)
        cum += pay
        pre_w = capital * float(curve.loc[end])
        rows[int(y)] = [dca, dfx, g, l, carry_out, t_div, t_fx, t_cg, pay, cum, pre_w, after,
                        1 - after / pre_w if pre_w > 0 else 0.0]
    table = pd.DataFrame.from_dict(rows, orient="index", columns=TAX_COLS)
    table.index.name = "year"
    table.attrs["warnings"] = warnings
    after_curve = curve * factor if tax.pay_from_portfolio else curve - paid_cum / capital
    return table, after_curve.rename("after_tax")


# --------------------------------------------------------------------------- the passive index, taxed the same way
def index_yield(bench, mkt, tax: TaxConfig, start) -> tuple:
    """(dividend yield per year, note) the passive index is taxed with: the benchmark's own `dividend_yield`, else
    `tax.index_dividend_yield`, else the market-cap weighted dividend yield of the universe at `start` (an
    approximation of what a total-return index series cannot tell: its yield)."""
    y = getattr(bench, "dividend_yield", None)
    if y is not None:
        return float(y), "benchmark dividend_yield"
    if tax.index_dividend_yield is not None:
        return float(tax.index_dividend_yield), "TaxConfig.index_dividend_yield"
    snap = mkt.view(mkt.trading_day(start))
    cap, dy = snap["market_cap"].fillna(0.0), snap["div_yield"].fillna(0.0).clip(lower=0.0)
    y = float((cap * dy).sum() / cap.sum()) if cap.sum() > 0 else 0.0
    return y, "cap-weighted universe dividend yield at the start (the index series has no yield of its own)"


def tax_buy_and_hold(curve: pd.Series, yield_: float, tax: TaxConfig, capital: float) -> tuple:
    """Tax a buy-and-hold curve (the passive index): dividends `yield_` a year are paid out and reinvested each bar
    (income on the previous bar's value), taxed as received; gains are realized only if `liquidate_at_end`."""
    c = (curve / curve.iloc[0]).astype(float)
    dt = np.diff(((c.index - c.index[0]).days / 365.25).to_numpy())
    div = c.to_numpy()[:-1] * yield_ * dt
    fs = tax.index_foreign_share
    ev = pd.DataFrame({"div_ca": np.r_[0.0, div * (1 - fs)], "div_fx": np.r_[0.0, div * fs], "gain": 0.0, "loss": 0.0},
                      index=c.index)
    if tax.liquidate_at_end:
        g = float(c.iloc[-1]) - (1.0 + float(div.sum()))
        ev.iloc[-1, ev.columns.get_loc("gain" if g >= 0 else "loss")] += g
    return assess(c, ev, tax, capital)


# --------------------------------------------------------------------------- report columns
def report_columns(rows: dict, results: dict, bench, bcurve: pd.Series, cfg, mkt, start, tax_cfg: TaxConfig | None = None) -> dict:
    """Add `AfterTaxCAGR`, `TaxDrag/yr`, `AfterTaxExcess/yr` to the report `rows` ({label: {metric: value}}) of the
    results that were run with a tax config, and for the passive index taxed the same way (same account, same model).
    Returns {label: tax table}, with the index under `bench.label` (empty dict when nothing is taxed). The index's
    yield comes from `index_yield`; its curve is taxed as buy and hold (`tax_buy_and_hold`)."""
    from .metrics import M, summarize
    tax_cfg = tax_cfg or cfg.tax
    taxed = {k: r for k, r in results.items() if r.after_tax_curve is not None}
    if tax_cfg is None or not taxed:
        return {}
    capital = tax_cfg.capital if tax_cfg.capital is not None else getattr(cfg, "capital", 100_000)
    y, _note = index_yield(bench, mkt, tax_cfg, start)
    itab, icurve = tax_buy_and_hold(bcurve, y, tax_cfg, capital)
    bl = bench.label
    icagr = summarize(icurve)[M.CAGR]
    rows[bl].update({M.AFTERTAX_CAGR: icagr, M.TAXDRAG_YR: rows[bl][M.CAGR] - icagr})
    out = {bl: itab}
    for label, res in taxed.items():
        a = summarize(res.after_tax_curve)[M.CAGR]
        rows[label].update({M.AFTERTAX_CAGR: a, M.TAXDRAG_YR: rows[label][M.CAGR] - a, M.AFTERTAX_EXCESS_YR: a - icagr})
        out[label] = res.tax
    return out
