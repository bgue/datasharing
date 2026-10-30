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

import inspect
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

import warnings as _warnings

import numpy as np
import pandas as pd

from .serial import serializable
from .harvesting import TaxLossHarvest  # noqa: F401  P2: registers the tag; `TaxConfig.harvest`

ACCOUNTS = ("taxable", "tfsa", "rrsp")
# P3: treaty withholding by the country a dividend is paid from; "default" covers any country not listed
DEFAULT_WITHHOLDING = {"US": 0.15, "default": 0.15}
FX_WARNING = "foreign gains taxed in portfolio currency; load an FX series for CAD basis"
PARK_INCOME_KINDS = ("interest", "dividend_eligible", "dividend_foreign")        # P1
RRSP_UNTAXED_WARNING = "RRSP withdrawals untaxed in this run"                  # P1

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

    def tax_on_interest(self, amount, year):                    # P1: optional protocol method
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

    def tax_on_interest(self, amount, year):                    # P1: interest is ordinary income
        return max(0.0, amount) * self.marginal

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
    other_income    # Q1: other taxable income that sets the bracket, $ a year (constant over the simulation); the
                    portfolio's income stacks on top. The TaxRequest / TUI default is 90000 (the owner's sanity-check income)
    employment_income  # Q1: DEPRECATED alias of `other_income` (warns, same numbers); reads back as None
    marginal_rate   # Q1: alternative to `other_income`: the ordinary marginal rate (fraction) of the bracket; resolved
                    to the $100-grid income whose tax_ca ordinary marginal rate is nearest, so interest/foreign income,
                    eligible dividends and gains all take that bracket's rates. `other_income` then reads back as that income
    deductions      deductions, $ a year (kept; hidden from the TUI)
    eligible_share  share of the Canadian cash dividends that are eligible (rest non-eligible)

    Stacking within a year: salary -> dividends (grossed up) -> foreign income -> capital gains; each layer's tax is the
    increase in the total, so the tax attributed to the investments is total(with) - total(salary only). A negative
    marginal (the dividend tax credit can exceed the bracket tax) is passed through; `assess` never pays less than zero.
    Foreign tax credit (approximation): min(withholding, Canadian tax on the foreign income stacked after salary and
    dividends). The returned tax on foreign income is withholding + Canadian tax net of the credit.
    Interest (P1) is ordinary income stacked on the salary, before the dividends: its tax is total(salary + interest) -
    total(salary); the later layers stack on top of it.
    Accounts: taxable taxes everything; tfsa: no tax, foreign withholding lost; rrsp: no tax while it grows, US-listed
    withholding exempt (the RRSP is DEFERRED, not free: `assess` taxes the balance at withdrawal when the TaxConfig gives
    `rrsp_withdrawal_rate` / `rrsp_withdrawal_income`).
    """
    province: str = "AB"
    year: int = 2025
    other_income: float = 0.0
    deductions: float = 0.0
    eligible_share: float = 1.0
    employment_income: float | None = None                       # Q1: deprecated alias of other_income
    marginal_rate: float | None = None                           # Q1: direct bracket knob

    def __post_init__(self):
        from .tax_ca import JURISDICTIONS, TAX_YEARS
        if self.employment_income is not None:                   # Q1
            _warnings.warn("CanadianTax.employment_income is deprecated: use other_income",
                           DeprecationWarning, stacklevel=3)
            object.__setattr__(self, "other_income", float(self.employment_income))
            object.__setattr__(self, "employment_income", None)
        object.__setattr__(self, "province", str(self.province).upper())
        if self.province not in JURISDICTIONS:
            raise ValueError(f"province must be one of {sorted(JURISDICTIONS)}, got {self.province!r}")
        if int(self.year) not in TAX_YEARS:
            raise ValueError(f"year must be one of {list(TAX_YEARS)}, got {self.year!r}")
        if not 0.0 <= self.eligible_share <= 1.0:
            raise ValueError("eligible_share must be in [0, 1]")
        if self.marginal_rate is not None:                       # Q1
            if not 0.0 <= self.marginal_rate < 1.0:
                raise ValueError("marginal_rate must be a rate in [0, 1)")
            from .tax_ca import income_for_marginal_rate
            object.__setattr__(self, "other_income",
                               income_for_marginal_rate(self.province, int(self.year), float(self.marginal_rate)))

    def _table_year(self, year: int) -> tuple:
        from .tax_ca import TAX_YEARS
        ty = min(TAX_YEARS, key=lambda t: (abs(t - year), -t)) if year not in TAX_YEARS else year
        warn = None if ty == year else f"year {year}: no tax table, used {ty}"
        return ty, warn

    def _compute(self, year, *, extra_ordinary=0.0, **kw):
        from .tax_ca import compute
        return compute(self.province, year, employment_income=self.other_income + extra_ordinary,
                       deductions=self.deductions, **kw)

    def assess_year(self, year, *, eligible_dividends=0.0, non_eligible_dividends=0.0, foreign_income=0.0,
                    foreign_withholding=0.0, capital_gains_net=0.0, account="taxable",
                    interest_income=0.0) -> YearTax:
        ty, warn = self._table_year(int(year))
        warnings = [warn] if warn else []
        wh = max(0.0, foreign_withholding) if foreign_income > 0 else 0.0
        if account == "rrsp":
            return YearTax(0.0, {"dividends": 0.0, "foreign": 0.0, "capital_gains": 0.0, "interest": 0.0,
                                 "warnings": warnings})
        if account == "tfsa":
            return YearTax(wh, {"dividends": 0.0, "foreign": wh, "capital_gains": 0.0, "interest": 0.0,
                                "warnings": warnings})
        # P1: interest is ordinary income stacked on the salary; the other layers stack on salary + interest
        it = max(0.0, interest_income)
        r = self._compute(ty, extra_ordinary=it, eligible_dividends=eligible_dividends,
                          non_eligible_dividends=non_eligible_dividends, foreign_income=foreign_income,
                          foreign_tax_paid=wh, capital_gains=max(0.0, capital_gains_net))
        b = r.breakdown
        tint = b["tax_on_salary"] - self._compute(ty).breakdown["tax_on_salary"] if it > 0 else 0.0
        div, fx, cg = b["tax_on_dividends_marginal"], b["tax_on_foreign_income_marginal"] + wh, b["tax_on_capital_gains_marginal"]
        return YearTax(tint + div + fx + cg, {"dividends": div, "foreign": fx, "capital_gains": cg, "interest": tint,
                                              "foreign_tax_credit": b.get("foreign_tax_credit", 0.0),
                                              "warnings": warnings})

    # per-item calls (the protocol's fallback path; each is the joint path with one layer)
    def tax_on_dividends(self, amount, eligible, year):
        if amount <= 0:
            return 0.0
        e, n = (amount, 0.0) if eligible else (0.0, amount)
        return max(0.0, self.assess_year(year, eligible_dividends=e, non_eligible_dividends=n).total)

    def tax_on_capital_gain(self, gain, year):
        return max(0.0, self.assess_year(year, capital_gains_net=gain).total) if gain > 0 else 0.0

    def tax_on_interest(self, amount, year):                    # P1
        return max(0.0, self.assess_year(year, interest_income=amount).total) if amount > 0 else 0.0

    def tax_on_foreign_dividends(self, amount, withholding_rate, year, account):
        if amount <= 0:
            return 0.0
        return max(0.0, self.assess_year(year, foreign_income=amount, foreign_withholding=amount * withholding_rate,
                                         account=account).total)

    def describe(self) -> dict:
        from .tax_ca import TAX_YEARS
        return {"model": "CanadianTax", "province": self.province, "year": self.year, "tables": list(TAX_YEARS),
                "other_income": self.other_income, "marginal_rate": self.marginal_rate,
                "deductions": self.deductions, "eligible_share": self.eligible_share,
                "stacking": "salary -> dividends -> foreign -> capital gains", "ftc": "approximation",
                "note": "tax_ca tables; not tax advice"}


TAX_MODELS: dict = {"none": NoTax, "placeholder": PlaceholderCanadianTax, "canadian": CanadianTax}   # config by name


@serializable("TaxConfig")
@dataclass(frozen=True)
class TaxConfig:
    """`Config.tax`: how the backtest is taxed. `None` on the Config (the default) means no tax at all.

    model             a `TaxModel` (default `PlaceholderCanadianTax()`)
    account           "taxable" | "tfsa" | "rrsp"
    withholding       P3: treaty withholding rate by the country a dividend is paid from, {"US": 0.15, "default": 0.15}
                      (a dict you give is merged over these); how it bites per account and wrapper is in docs/TAXES.md
    us_withholding    P3: DEPRECATED alias (warns): fills `withholding["US"]`; reads back as None
    macro             P3: a `MacroSeries` whose `fx` (CAD per unit of `fx_currency`) puts gains and dividends of
                      foreign-currency names on a CAD basis; None: gains taxed in the portfolio currency (warning)
    home_currency, fx_currency   P3: the currency taxes are computed in ("CAD") and the one `macro.fx` quotes ("USD")
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
    rrsp_withdrawal_rate  (P1) account "rrsp" is tax-DEFERRED: the whole balance at the end of the simulation is taxed as
                      ordinary income at this marginal rate (an assumption). None and no income: untaxed, with a warning
    rrsp_withdrawal_income  (P1) alternative: the retirement income ($ a year) whose marginal ordinary rate, from the
                      model's province and year in `tax_ca`, is the withdrawal rate. Not both
    park_income_kind  (P1) how the exit policy's parked CASH (park "cash" or a rate) is taxed in a taxable account:
                      "interest" (ordinary income) | "dividend_eligible" | "dividend_foreign". An ExitPolicy's own
                      non-default `park_income_kind` wins. A parked index / ticker series is capital (unchanged)
    """
    model: object = field(default_factory=PlaceholderCanadianTax)
    account: str = "taxable"
    withholding: dict = field(default_factory=lambda: dict(DEFAULT_WITHHOLDING))   # P3
    us_withholding: float | None = None                                              # P3: deprecated alias
    pay_from_portfolio: bool = True
    capital: float | None = None
    foreign: tuple = ()
    foreign_col: str | None = None
    home: str = "CA"
    liquidate_at_end: bool = False
    index_dividend_yield: "float | dict | pd.Series | None" = None            # D1: or {year: yield}
    index_foreign_share: float = 0.0
    # P1: RRSP deferral and the parked-cash income kind
    rrsp_withdrawal_rate: float | None = None
    rrsp_withdrawal_income: float | None = None
    park_income_kind: str = "interest"
    macro: object = None                                                             # P3
    home_currency: str = "CAD"                                                       # P3
    fx_currency: str = "USD"                                                         # P3
    harvest: TaxLossHarvest | None = None                                            # P2: tax-loss harvesting (harvesting.py)

    def __post_init__(self):
        if self.account not in ACCOUNTS:
            raise ValueError(f"account must be one of {ACCOUNTS}, got {self.account!r}")
        wh = {**DEFAULT_WITHHOLDING, **{str(k): float(v) for k, v in dict(self.withholding).items()}}   # P3
        if self.us_withholding is not None:
            _warnings.warn("TaxConfig.us_withholding is deprecated: use withholding={'US': rate}",
                           DeprecationWarning, stacklevel=3)
            wh["US"] = float(self.us_withholding)
            object.__setattr__(self, "us_withholding", None)
        if not all(0.0 <= v < 1.0 for v in wh.values()):
            raise ValueError("withholding rates must be in [0, 1)")
        object.__setattr__(self, "withholding", wh)
        object.__setattr__(self, "foreign", tuple(self.foreign))
        if self.park_income_kind not in PARK_INCOME_KINDS:                       # P1
            raise ValueError(f"park_income_kind must be one of {PARK_INCOME_KINDS}, got {self.park_income_kind!r}")
        if self.rrsp_withdrawal_rate is not None and self.rrsp_withdrawal_income is not None:
            raise ValueError("give rrsp_withdrawal_rate or rrsp_withdrawal_income, not both")
        if self.rrsp_withdrawal_rate is not None and not 0.0 <= self.rrsp_withdrawal_rate < 1.0:
            raise ValueError("rrsp_withdrawal_rate must be a rate in [0, 1)")
        if self.rrsp_withdrawal_income is not None:
            if self.rrsp_withdrawal_income < 0:
                raise ValueError("rrsp_withdrawal_income must be >= 0")
            self.rrsp_rate()                                                      # fail early on an unusable model

    def rrsp_rate(self) -> float | None:
        """P1: the marginal rate the RRSP balance is taxed at on withdrawal, or None (untaxed). From
        `rrsp_withdrawal_income`: the marginal ordinary rate of `tax_ca` at that income, in the model's province and
        year (the model must have them in the tables: CanadianTax)."""
        if self.rrsp_withdrawal_rate is not None:
            return float(self.rrsp_withdrawal_rate)
        if self.rrsp_withdrawal_income is None:
            return None
        from .tax_ca import JURISDICTIONS, TAX_YEARS, marginal_rates
        prov, yr = str(getattr(self.model, "province", "")).upper(), int(getattr(self.model, "year", 2025))
        if prov not in JURISDICTIONS or yr not in TAX_YEARS:
            raise ValueError(f"rrsp_withdrawal_income needs a model with a tax_ca province {sorted(JURISDICTIONS)} and "
                             f"year {list(TAX_YEARS)} (CanadianTax); got province {prov!r}, year {yr}: give "
                             f"rrsp_withdrawal_rate instead")
        return float(marginal_rates(prov, yr, float(self.rrsp_withdrawal_income))["ordinary"])

    def rrsp_convention(self) -> str | None:
        """P1: the report line for the RRSP convention in force (None for other accounts)."""
        if self.account != "rrsp":
            return None
        r = self.rrsp_rate()
        if r is None:
            return RRSP_UNTAXED_WARNING
        src = (f" from retirement income {self.rrsp_withdrawal_income:,.0f}" if self.rrsp_withdrawal_income is not None else "")
        return f"RRSP: deferred, taxed at withdrawal at {100 * r:.1f}% (assumed{src})"

    def tax_line(self) -> str | None:
        """# Q1: the report line "Tax: <account>, <province>, bracket at $<other_income> (marginal ordinary X%, eligible
        dividend Y%, capital gains Z%)" for a tax_ca model (CanadianTax); None for other models."""
        m = self.model
        prov, yr = str(getattr(m, "province", "")).upper(), int(getattr(m, "year", 2025))
        inc = getattr(m, "other_income", None)
        from .tax_ca import JURISDICTIONS, TAX_YEARS, marginal_rates
        if inc is None or prov not in JURISDICTIONS or yr not in TAX_YEARS or not hasattr(m, "assess_year"):
            return None
        r = marginal_rates(prov, yr, float(inc), float(getattr(m, "deductions", 0.0)))
        return (f"Tax: {self.account}, {prov}, bracket at ${inc:,.0f} (marginal ordinary {100 * r['ordinary']:.1f}%, "
                f"eligible dividend {100 * r['eligible_dividend']:.1f}%, capital gains {100 * r['capital_gain']:.1f}%)")

    def harvest_line(self) -> str:
        """# Q1: "Harvesting: off" or "Harvesting: on (threshold 10%, second pick, 30-day superficial-loss window)"."""
        h = self.harvest
        if h is None:
            return "Harvesting: off"
        word = {"similar": "second pick", "second_pick": "second pick", "stay_out": "stay out"}.get(h.replacement, h.replacement)
        return (f"Harvesting: on (threshold {100 * h.threshold_pct:g}%, {word}, "
                f"{h.superficial_days}-day superficial-loss window)")

    def report_lines(self) -> str | None:
        """# Q1: the RRSP convention (if any), the bracket line (if any) and the harvesting line, one per line."""
        lines = [x for x in (self.rrsp_convention(), self.tax_line(), self.harvest_line()) if x]
        return "\n".join(lines) or None

    def rate_for(self, country) -> float:
        """P3: treaty withholding rate on dividends paid from `country`."""
        return float(self.withholding.get(country, self.withholding["default"]))

    def foreign_info(self, tickers, snap: pd.DataFrame) -> pd.DataFrame:
        """P3: per ticker `kind` ("domestic" | "direct": foreign dividends paid to the investor, withheld at source |
        "fund": a Canadian-listed ETF on foreign stocks, withheld inside the fund in every account), `country` the
        dividends come from, `rate` the treaty rate, `exempt` (direct US dividends are exempt in an RRSP),
        `non_eligible` (a Canadian name marked eligible_dividend False), `currency`. Reads `domicile`, `wrapper`,
        `underlying_domicile`, `currency`, `eligible_dividend` and `foreign_col` from the snapshot (Market.view joins
        the dataset's security_meta); a name in `foreign` with no known country counts as US."""
        t = pd.Index(tickers)

        def col(name):
            return snap[name].reindex(t) if name in snap else pd.Series(np.nan, index=t, dtype=object)
        dom, wrap, und, cur, elig = (col(c) for c in ("domicile", "wrapper", "underlying_domicile", "currency",
                                                      "eligible_dividend"))
        fcol = col(self.foreign_col) if self.foreign_col else pd.Series(np.nan, index=t, dtype=object)
        listed = t.isin(list(self.foreign))
        rows = []
        for i in range(len(t)):
            d = dom.iloc[i] if isinstance(dom.iloc[i], str) else (fcol.iloc[i] if isinstance(fcol.iloc[i], str) else None)
            w = wrap.iloc[i] if isinstance(wrap.iloc[i], str) else "stock"
            u = und.iloc[i] if isinstance(und.iloc[i], str) else None
            if w == "etf_ca" and u and u != self.home:
                kind, country = "fund", u
            else:
                country = "US" if w == "etf_us" else (u if (w == "etf_ca" and u) else d)
                if country is None or (country == self.home and listed[i]):
                    country = "US" if listed[i] else self.home
                kind = "direct" if country != self.home else "domestic"
            c = cur.iloc[i] if isinstance(cur.iloc[i], str) else (self.fx_currency if kind == "direct" else self.home_currency)
            e = elig.iloc[i]
            ne = kind == "domestic" and e is not None and e == e and not bool(e)
            rows.append((kind, country, self.rate_for(country) if kind != "domestic" else 0.0,
                         kind == "direct" and country == "US", bool(ne), c))
        return pd.DataFrame(rows, index=t, columns=["kind", "country", "rate", "exempt", "non_eligible", "currency"])

    def foreign_mask(self, tickers, snap: pd.DataFrame) -> np.ndarray:
        """bool per ticker: dividends are foreign."""
        t = pd.Index(tickers)
        m = t.isin(list(self.foreign))
        if self.foreign_col and self.foreign_col in snap:
            m = m | (snap[self.foreign_col].reindex(t).fillna(self.home).to_numpy() != self.home)
        return np.asarray(m, bool)


# --------------------------------------------------------------------------- the yearly engine
P3_EVENT_COLS = ("div_ne", "wh_fx", "div_fund", "wh_fund", "wh_lost")   # P3: see `assess`
EVENT_COLS = ("div_ca", "div_fx", "gain", "loss", "interest") + P3_EVENT_COLS   # pre-tax wealth units (start wealth = 1); loss <= 0
TAX_COLS = ["dividends_ca", "dividends_foreign", "realized_gains", "realized_losses", "interest_income",
            "loss_carry_out", "tax_dividends", "tax_foreign_dividends", "tax_capital_gains", "tax_interest",
            "tax_rrsp_withdrawal", "tax_paid", "cum_tax_paid", "wealth_pre_tax", "wealth_after_tax", "cum_drag"]


TAX_COLS += ["carried_back", "refund"]                       # P2: net capital loss carried back, tax refunded (see assess)
CARRY_BACK_YEARS = 3                                          # P2: CRA: net capital losses go back up to 3 years


def _carry_back(hist: dict, year: int, loss: float) -> tuple:
    """P2: apply a year's net capital `loss` to the taxable gains of the previous `CARRY_BACK_YEARS` simulation years,
    the most recent first. `hist[year] = [taxable gain left, capital gains tax left]` (dollars). The refund of a year is
    its remaining capital gains tax x the share of its remaining gain that the loss takes (exact for a flat rate, an
    underestimate for a stacked progressive model). Returns (loss used, refund, {year: refund})."""
    used, refund, by_year = 0.0, 0.0, {}
    for py in range(year - 1, year - 1 - CARRY_BACK_YEARS, -1):
        h = hist.get(py)
        if h is None or h[0] <= 0 or loss - used <= 0:
            continue
        a = min(loss - used, h[0])
        r = h[1] * a / h[0]
        h[0] -= a
        h[1] -= r
        used += a
        refund += r
        by_year[py] = r
    return used, refund, by_year


def assess(curve: pd.Series, events: pd.DataFrame, tax: TaxConfig, capital: float, warnings=()) -> tuple:
    """Tax per calendar year and the after-tax curve.

    `curve` pre-tax wealth (1.0 at the start); `events` indexed by date with columns `EVENT_COLS`, in the same
    wealth units as `curve` (an event of 0.01 is 1% of the starting wealth). Dollars = units x capital x the
    after-tax factor in force (tax paid so far shrinks the account, and with it the dollar size of later events). Returns
    (DataFrame per year with `TAX_COLS`, after-tax curve Series).

    Per year: dividends are taxed as received; realized gains net against realized losses and against losses
    carried forward from earlier years (carried within the simulation, nominal, no expiry); registered accounts
    (tfsa / rrsp) skip domestic dividend, interest and capital gain tax and their losses are not carried. The tax is
    paid at the year's last bar (the last bar of the data closes the final, possibly part, year).
    P1: interest (the `interest` event column) is ordinary income in a taxable account (`assess_year(interest_income=)`,
    else `model.tax_on_interest`, else untaxed with a warning). An "rrsp" account is DEFERRED: with a withdrawal rate
    (`TaxConfig.rrsp_rate()`) the whole balance left at the final bar (after that year's ordinary tax) is taxed as ordinary
    income at that rate, the after-tax curve drops on the last bar by that share; without one the run keeps the old
    untaxed behaviour and `table.attrs["warnings"]` carries `RRSP_UNTAXED_WARNING`. `table.attrs["convention"]` is the
    report line (`TaxConfig.rrsp_convention()`).

    A model with `assess_year` is assessed jointly per year (dividends split by its `eligible_share`, foreign withholding
    = the stage's `wh_fx` events (P3), gains net of carry-forward; registered accounts get the same call and the
    model zeroes what the account shelters); else the three per-item calls are used. Model warnings (e.g. a year beyond
    its tables) go to `table.attrs["warnings"]`. A negative year tax (credits above the tax) is floored at 0.
    P3: foreign layer. Event columns (wealth units): `div_fx` direct foreign dividends, gross; `wh_fx` their withholding
    where the account makes it a tax (taxable: creditable through the model's FTC; TFSA: lost; RRSP: not here); `div_fund` /
    `wh_fund` gross distribution and withholding INSIDE a Canadian-listed ETF on foreign stocks (every account, never
    creditable: the net distribution is taxed as foreign income, the withholding is a plain cost); `wh_lost` an RRSP's
    withholding on non-US direct dividends (the treaty exemption is US only); `div_ne` non-eligible Canadian dividends
    (a subset of `div_ca`). `warnings` (e.g. the stage's missing-FX warning) are added to `table.attrs["warnings"]`."""
    model, acct = tax.model, tax.account
    ev = events.reindex(columns=list(EVENT_COLS)).fillna(0.0) if len(events) else pd.DataFrame(columns=list(EVENT_COLS), dtype=float)
    by_year = ev.groupby(ev.index.year).sum() if len(ev) else ev
    yrs = curve.index.year
    last_bar = curve.groupby(yrs).apply(lambda s: s.index[-1])
    factor = pd.Series(1.0, index=curve.index)             # share of pre-tax wealth left, bar by bar (portfolio-paid)
    paid_cum = pd.Series(0.0, index=curve.index)           # cumulative $ paid (outside-paid)
    f, carry, cum = 1.0, 0.0, 0.0
    hist, back, year_tax = {}, {}, {}                      # P2: carry-back state; per year (carried back, refund)
    rows, warnings = {}, list(warnings)
    rrsp_rate = tax.rrsp_rate() if acct == "rrsp" else None                             # P1
    if acct == "rrsp" and rrsp_rate is None:
        warnings.append(RRSP_UNTAXED_WARNING)
    joint_interest = hasattr(model, "assess_year") and _takes(model.assess_year, "interest_income")   # P1
    last_year = max(set(yrs))
    for y in sorted(set(yrs)):
        e = by_year.loc[y] if y in by_year.index else pd.Series(0.0, index=list(EVENT_COLS))
        scale = capital * (f if tax.pay_from_portfolio else 1.0)
        dca, dfx, g, l, intr = (float(e[c]) * scale for c in EVENT_COLS[:5])
        dne, whfx, dfund, whfund, whlost = (float(e[c]) * scale for c in P3_EVENT_COLS)   # P3
        fin = dfx + dfund - whfund                          # P3: foreign income stacked: direct gross + fund net
        t_div = t_fx = t_cg = t_int = 0.0
        cb = refund = 0.0                                   # P2
        cb_years = {}
        carry_out = carry
        taxable = 0.0
        if acct == "taxable":
            net = g + l - carry                             # l <= 0
            taxable = max(net, 0.0)
            carry_out = max(-net, 0.0)
            if g + l < 0:                                   # P2: this year's net loss goes back first, the rest forward
                cb, refund, cb_years = _carry_back(hist, int(y), -(g + l))
                carry_out -= cb
        if hasattr(model, "assess_year"):                   # joint path: the layers stack within the year
            sh = float(getattr(model, "eligible_share", 1.0))
            kw = {"interest_income": intr} if joint_interest else {}                   # P1
            yt = model.assess_year(int(y), eligible_dividends=(dca - dne) * sh,
                                   non_eligible_dividends=(dca - dne) * (1 - sh) + dne,
                                   foreign_income=fin, foreign_withholding=whfx,                    # P3
                                   capital_gains_net=taxable, account=acct, **kw)
            bd = yt.breakdown
            t_div, t_fx, t_cg, t_int = (float(bd.get(k, 0.0)) for k in ("dividends", "foreign", "capital_gains", "interest"))
            if intr > 0 and not joint_interest and acct == "taxable":                   # P1: model without the joint kwarg
                t_int = _interest_fallback(model, intr, int(y), warnings)
            warnings.extend(w for w in bd.get("warnings", ()) if w not in warnings)
        else:
            if acct == "taxable":
                t_div = (model.tax_on_dividends(dca - dne, True, int(y)) if dca - dne > 0 else 0.0) \
                    + (model.tax_on_dividends(dne, False, int(y)) if dne > 0 else 0.0)              # P3
                t_cg = model.tax_on_capital_gain(taxable, int(y)) if taxable > 0 else 0.0
            if dfx > 0:                                     # P3: direct foreign dividends at their blended rate
                t_fx = model.tax_on_foreign_dividends(dfx, min(whfx / dfx, 0.999999), int(y), acct)
            if dfund - whfund > 0:                          # P3: fund distributions net of the fund's withholding
                t_fx += model.tax_on_foreign_dividends(dfund - whfund, 0.0, int(y), acct)
            if intr > 0 and acct == "taxable":                                          # P1
                t_int = _interest_fallback(model, intr, int(y), warnings)
        t_fx += whfund + whlost                             # P3: withholding no credit can recover (fund level; RRSP non-US)
        carry = carry_out
        total = max(0.0, float(t_div + t_fx + t_cg + t_int))   # a net credit is not paid out
        end = last_bar[y]
        wealth = capital * float(curve.loc[end]) * (f if tax.pay_from_portfolio else 1.0)
        pay = min(total, wealth) if wealth > 0 else 0.0
        pay -= refund                                       # P2: the carry-back refund is a credit at this year end
        hist[int(y)] = [taxable, t_cg]                      # P2
        back[int(y)] = (cb, refund)
        year_tax[int(y)] = YearTax(pay, {"dividends": t_div, "foreign": t_fx, "capital_gains": t_cg, "interest": t_int,
                                         "carried_back": cb, "refund": refund, "refund_by_year": cb_years})
        wd = 0.0                                                                        # P1: RRSP withdrawal tax, last bar
        if rrsp_rate is not None and y == last_year and wealth > 0:
            wd = rrsp_rate * (wealth - pay if tax.pay_from_portfolio else wealth)
            pay += wd
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
        rows[int(y)] = [dca, dfx + dfund, g, l, intr, carry_out, t_div, t_fx, t_cg, t_int, wd, pay, cum, pre_w, after,
                        1 - after / pre_w if pre_w > 0 else 0.0]
    table = pd.DataFrame.from_dict(rows, orient="index", columns=TAX_COLS[:-2])   # P2: the last two are added below
    table.index.name = "year"
    table["carried_back"] = pd.Series({y: v[0] for y, v in back.items()})        # P2
    table["refund"] = pd.Series({y: v[1] for y, v in back.items()})              # P2
    table.attrs["year_tax"] = year_tax                                           # P2: YearTax per year, breakdown has carried_back / refund
    table.attrs["warnings"] = warnings
    table.attrs["convention"] = tax.report_lines()                                    # P1 / Q1: RRSP convention + bracket + harvesting lines
    after_curve = curve * factor if tax.pay_from_portfolio else curve - paid_cum / capital
    return table, after_curve.rename("after_tax")


def _takes(fn, name: str) -> bool:
    """P1: does the callable accept keyword `name` (or **kwargs)?"""
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())


def _interest_fallback(model, amount: float, year: int, warnings: list) -> float:
    """P1: tax on interest from a model without joint `interest_income`: its `tax_on_interest`, else untaxed + warning."""
    if hasattr(model, "tax_on_interest"):
        return float(model.tax_on_interest(amount, year))
    w = "model has no interest tax (tax_on_interest / assess_year interest_income): interest untaxed"
    if w not in warnings:
        warnings.append(w)
    return 0.0


# --------------------------------------------------------------------------- the passive index, taxed the same way
def index_yield(bench, mkt, tax: TaxConfig, start) -> tuple:
    """(dividend yield per year, note) the passive index is taxed with: the benchmark's own `dividend_yield`, else
    `tax.index_dividend_yield`, else the market-cap weighted dividend yield of the universe at `start` (an
    approximation of what a total-return index series cannot tell: its yield)."""
    y = getattr(bench, "dividend_yield", None)
    if y is not None:
        return float(y), "benchmark dividend_yield"
    if tax.index_dividend_yield is not None:
        if isinstance(tax.index_dividend_yield, (dict, pd.Series)):         # D1: a yield per year {year: yield}
            return pd.Series(tax.index_dividend_yield, dtype=float), "TaxConfig.index_dividend_yield (by year)"
        return float(tax.index_dividend_yield), "TaxConfig.index_dividend_yield"
    info = _index_info(mkt, bench)                                          # D1: the dataset's own yield by year
    if info is not None and info.get("yield") is not None:
        return pd.Series(info["yield"], dtype=float), "dataset dividend yield by year (e.g. Shiller's S&P 500 yield)"
    snap = mkt.view(mkt.trading_day(start))
    cap, dy = snap["market_cap"].fillna(0.0), snap["div_yield"].fillna(0.0).clip(lower=0.0)
    y = float((cap * dy).sum() / cap.sum()) if cap.sum() > 0 else 0.0
    return y, "cap-weighted universe dividend yield at the start (the index series has no yield of its own)"


def _index_info(mkt, bench):
    """D1: `Dataset.index_info[bench.index]` ({"yield": Series by year, "foreign_share": x}) or None."""
    info = getattr(getattr(mkt, "dataset", None), "index_info", None)
    return (info or {}).get(getattr(bench, "index", None))


def tax_buy_and_hold(curve: pd.Series, yield_, tax: TaxConfig, capital: float) -> tuple:
    """Tax a buy-and-hold curve (the passive index): dividends `yield_` a year are paid out and reinvested each bar
    (income on the previous bar's value), taxed as received; gains are realized only if `liquidate_at_end`.
    D1: `yield_` may also be a Series (or dict) of yields by calendar year; a year it lacks takes the nearest earlier
    year's (the first year's before it)."""
    c = (curve / curve.iloc[0]).astype(float)
    dt = np.diff(((c.index - c.index[0]).days / 365.25).to_numpy())
    if isinstance(yield_, (pd.Series, dict)):                                # D1
        ys = pd.Series(yield_, dtype=float).sort_index()
        yrs = c.index.year[:-1]
        yield_ = ys.reindex(ys.index.union(yrs.unique())).ffill().bfill().loc[yrs].to_numpy()
    div = c.to_numpy()[:-1] * yield_ * dt
    fs = tax.index_foreign_share
    ev = pd.DataFrame({"div_ca": np.r_[0.0, div * (1 - fs)], "div_fx": np.r_[0.0, div * fs], "gain": 0.0, "loss": 0.0,
                       "interest": 0.0,
                       "wh_fx": np.r_[0.0, div * fs * (0.0 if tax.account == "rrsp" else tax.rate_for("US"))]},   # P3
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
    info = _index_info(mkt, bench)                                           # D1: the dataset knows the index is foreign
    itax = tax_cfg
    if info and tax_cfg.index_foreign_share == 0.0 and info.get("foreign_share"):
        itax = replace(tax_cfg, index_foreign_share=float(info["foreign_share"]))
    itab, icurve = tax_buy_and_hold(bcurve, y, itax, capital)
    bl = bench.label
    icagr = summarize(icurve)[M.CAGR]
    rows[bl].update({M.AFTERTAX_CAGR: icagr, M.TAXDRAG_YR: rows[bl][M.CAGR] - icagr})
    out = {bl: itab}
    for label, res in taxed.items():
        a = summarize(res.after_tax_curve)[M.CAGR]
        rows[label].update({M.AFTERTAX_CAGR: a, M.TAXDRAG_YR: rows[label][M.CAGR] - a, M.AFTERTAX_EXCESS_YR: a - icagr})
        out[label] = res.tax
    return out
