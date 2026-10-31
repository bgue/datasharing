"""Plan module: a dated investment plan on a configured strategy, against the passive index (docs/PLAN.md).

    res = lab.plan(...)  /  run_plan(lab, PlanSpec("dogs", "1990-01-02", initial=10_000, periodic=500, growth="cpi"))
    res.metrics        # rows strategy / index / difference, columns METRICS
    res.series         # daily dollars: contributed, strategy_pre / _after_tax, index_pre / _after_tax, cpi_deflator, ..._real, cash

# PA: everything here is additive; `Config`, `simulate` and the tax stage behave as before without flows.

THE ARITHMETIC (dollars of the plan; every value is the close of the day BEFORE that day's flow)

Schedule. Flow k of a periodic stream is dated `anchor + k periods` (months by `DateOffset`, so the 31st clamps),
executed on the NEXT trading day (`searchsorted(side="left")`). The first periodic contribution is on `start` (k = 0). The
amount of a stream is `periodic * f(date)`, f = (1 + growth)^n with n = anniversaries of the anchor already passed, or
`cpi(last anniversary) / cpi(anchor)` for growth="cpi". Periodic contributions stop at `withdraw_from`; withdrawals
`withdrawals * f` run from `withdraw_from`. Negative `extra` flows are withdrawals. A flow executed on the last bar of
the run is not made (`applied` False in `PlanResult.flows`).

Strategy leg (units ledger over `backtest.simulate(..., flows=)`). With V the portfolio before the flow, e the equity
share (1 without an exit policy), w the drifted weights, t the strategy's target weights at its last rebalance, c_i the
one-way cost rate of name i (CostModel):
    contribution A, invest   buy_i = t_i * A * e      cost_i = c_i * buy_i      holding_i = w_i V e + buy_i - cost_i
                             V' = V + A - sum cost_i  cost base_i += buy_i      (parked share: A (1 - e) joins the parked asset)
                             on a rebalance date A is added to V and w is scaled by V/(V+A): the rebalance trade buys it.
    contribution A, hold     cash += A; cash grows at the Accrual rate (interest taxed as ordinary income each bar) and
                             joins the portfolio on the next rebalance date.
    withdrawal |A|           cash first; the rest is a pro rata sale of gross G with G - cost(G) = |A| (fixed point; the
                             investor receives |A|), V' = V - G, every holding sold by the share s = G / V,
                             realized gain_i = (s w_i V e - cost_i) - s * cost base_i, base_i *= 1 - s.
Units: NAV u (1.0 on the first date) moves with the flow-free return u_t = u_(t-1) * V_t / (V_(t-1) + F_(t-1)), F the
external flow executed on t-1 (costs stay in the return); units bought = F / u. Hence V_t = (sum units) * u_t exactly.
Flat prices, zero tax: V_T = sum of contributions - sum of costs, exactly.

Taxes. The plan runs the TaxStage with capital = 1 (ledger in dollars) and `pay_from_portfolio=False`: tax is paid from
outside the account, so a later contribution is not shrunk by earlier tax (the ledger stays homogeneous). After-tax value =
pre-tax value less the cumulative tax paid (no growth is credited on money paid out) and, in an RRSP, less
`rrsp_withdrawal_rate` x the balance (the value of cashing it in); RRSP withdrawals are taxed at that rate on the amount
received (taken out of the withdrawal: TaxPaid, XIRRAfterTax and the flows frame carry it, the after-tax value series does not). `liquidate_at_end` is forced on: the final gains are realized. Without `Config.tax` nothing is taxed.

Index leg. The benchmark's total-return series net of `index_mer` (daily accrual (1 - mer)^years) is bought with the same
flows; a purchase of A buys A (1 - bps/1e4) / P units, a withdrawal sells G = |A| / (1 - bps/1e4). `taxes.tax_buy_and_hold_flows`
taxes it: dividends at the index yield (`index_yield()`, by year) on the previous bar's value, reinvested into the cost
base, withholding per account as in `tax_buy_and_hold`, gains on withdrawals and at the end on average cost.

Metrics. XIRR: Excel convention (days / 365) on investor flows (contributions negative, withdrawals received positive,
terminal value positive); after-tax: RRSP withdrawals net of tax, terminal after tax; real: every flow divided by the CPI
deflator of its date (start-date dollars). TWR = CAGR of the flow-free NAV. Alpha / Beta: OLS of strategy monthly excess
(over the monthly risk-free rate) on index monthly excess, alpha x 12; Sharpe = mean excess / std x sqrt(12); TrackingError
and InfoRatio on the monthly difference. Goal tests use the after-tax value.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest
from .backtest import Config
from .dataset import DataCard
from .exit_policy import as_overlay
from .macro import cpi_deflator, pad_align
from .pipeline import StandardCosts
from .serial import serializable

DATA = Path(__file__).resolve().parent / "data"
US_CPI_WARNING = "after-inflation returns use US CPI (expediency, see DECISIONS.md)"
FREQ = {"biweekly": pd.DateOffset(days=14), "monthly": pd.DateOffset(months=1),
        "quarterly": pd.DateOffset(months=3), "annual": pd.DateOffset(years=1)}
KINDS = ("initial", "periodic", "extra", "withdrawal")


@serializable("CashFlow")
@dataclass(frozen=True)
class CashFlow:
    """One dated flow, positive = contribution, negative = withdrawal."""
    date: str
    amount: float
    note: str = ""


@serializable("PlanSpec")
@dataclass(frozen=True)
class PlanSpec:
    """A dated plan. Docs/PLAN.md has the contract. DEV: `start` is the second field (a dataclass cannot put a required
    field after fields with defaults); build it with keywords as before."""
    screen: "str | dict"                       # configured strategy (name in the registry, or a spec dict)
    start: str                                 # first investment date
    config: Config | None = None               # rebalance, costs, exit policy, tax (account!), harvesting; Lab default if None
    end: str | None = None                     # anticipated end date; None = last data bar
    initial: float = 0.0                       # lump sum on `start`
    periodic: float = 0.0                      # contribution per period (first one on `start`)
    frequency: str = "monthly"                 # "biweekly" | "monthly" | "quarterly" | "annual"
    growth: "float | str" = 0.0                # annual growth of the periodic amount, or "cpi"
    extra: tuple = ()                          # CashFlow: extraordinary investments (and dated withdrawals, negative)
    withdrawals: float = 0.0                   # periodic withdrawal per period once `withdraw_from` is reached
    withdraw_from: str | None = None           # start of the decumulation phase (periodic contributions stop there)
    withdraw_growth: "float | str" = 0.0       # growth of the periodic withdrawal, or "cpi"
    cash_timing: str = "invest"                # "invest" (next trading day, pro rata to target weights) | "hold" (until next rebalance)
    index_mer: float = 0.0005                  # passive fund MER (annual); index trading cost from the benchmark (default 0)
    goal: float | None = None                  # target final value, nominal, after tax
    risk_free: "str | float" = "us10y"         # "us10y" | "ca10y" | constant annual rate
    inflation: "str | float" = "auto"          # "auto" (CA CPI if loaded, else US CPI) | "us" | "ca" | constant annual rate
    label: str = ""

    def __post_init__(self):
        if self.frequency not in FREQ:
            raise ValueError(f"frequency must be one of {sorted(FREQ)}, got {self.frequency!r}")
        if self.cash_timing not in ("invest", "hold"):
            raise ValueError(f"cash_timing must be 'invest' or 'hold', got {self.cash_timing!r}")
        for g in (self.growth, self.withdraw_growth):
            if isinstance(g, str) and g != "cpi":
                raise ValueError(f"growth must be a number or 'cpi', got {g!r}")


METRICS = [  # metrics.py registers these (LinkML-guarded)
    "Contributed", "FinalPreTax", "FinalAfterTax", "FinalReal", "FinalAfterTaxReal", "Gain",
    "XIRR", "XIRRAfterTax", "XIRRReal", "XIRRAfterTaxReal",
    "TWR", "TWRAfterTax",
    "Alpha", "Beta", "Sharpe", "Vol", "MaxDD", "TrackingError", "InfoRatio",
    "TaxPaid", "TaxDrag/yr", "CostDrag/yr", "MERDrag/yr", "YearsToGoal", "GoalShortfall",
]


@dataclass
class PlanResult:
    spec: PlanSpec
    flows: pd.DataFrame            # one row per planned flow: date, amount, kind, executed, note, applied, received, flow_tax, strategy_units, index_units
    series: pd.DataFrame           # daily dollars (see module doc) + strategy_unit / index_unit (flow-free NAVs)
    monthly: pd.DataFrame          # month-end flow-free returns: strategy, index, rf (monthly)
    metrics: pd.DataFrame          # rows strategy / index / difference
    tax: "pd.DataFrame | None"     # per-year tax table; columns MultiIndex (leg in {strategy, index}, TAX_COLS)
    card: DataCard
    goal: "dict | None"
    extras: dict = field(default_factory=dict)   # internals metrics_table needs: costs, MER drag, rrsp rate, risk-free name


# ============================================================================================ schedule
def _cpi_at(cpi: "pd.Series | None", d) -> float:
    if cpi is None:
        raise ValueError("indexing by 'cpi' needs a CPI series")
    v = cpi.dropna().asof(pd.Timestamp(d))
    return float(v if v == v else cpi.dropna().iloc[0])


def _factor(growth, anchor: pd.Timestamp, date: pd.Timestamp, cpi) -> float:
    """Indexing of a stream: (1 + growth)^n or CPI(last anniversary) / CPI(anchor), n = anniversaries passed."""
    n = (date.year - anchor.year) - int((date.month, date.day) < (anchor.month, anchor.day))
    if isinstance(growth, str):
        return _cpi_at(cpi, anchor + pd.DateOffset(years=n)) / _cpi_at(cpi, anchor)
    return (1.0 + float(growth)) ** n


def _dates(anchor: pd.Timestamp, freq: str, until: pd.Timestamp, stop: "pd.Timestamp | None" = None) -> list:
    step, out, k = FREQ[freq], [], 0
    while True:
        d = anchor + (step * k if freq == "biweekly" else pd.DateOffset(**{kk: vv * k for kk, vv in step.kwds.items()}))
        if d > until or (stop is not None and d >= stop):
            return out
        out.append(d)
        k += 1


def _execute(index: pd.DatetimeIndex, dates) -> pd.DatetimeIndex:
    """Next trading day on or after each date; NaT after the last bar."""
    i = index.searchsorted(pd.DatetimeIndex(dates), side="left")
    return index[np.minimum(i, len(index) - 1)].where(i < len(index))


def schedule(spec: PlanSpec, index: pd.DatetimeIndex, cpi: "pd.Series | None") -> pd.DataFrame:
    """The dated flows: columns date (as scheduled), amount (signed), kind, note, executed (next trading day in `index`,
    NaT when the date is after the last bar), sorted by executed date. Runs to `spec.end` (None: the last bar of `index`),
    also beyond the index (the Monte Carlo replays it on longer paths)."""
    start = pd.Timestamp(spec.start)
    until = pd.Timestamp(spec.end) if spec.end else pd.Timestamp(index[-1])
    if until < start:
        raise ValueError(f"plan end {until.date()} is before start {start.date()}")
    wf = pd.Timestamp(spec.withdraw_from) if spec.withdraw_from else None
    rows = []
    if spec.initial:
        rows.append((start, float(spec.initial), "initial", ""))
    if spec.periodic:
        for d in _dates(start, spec.frequency, until, wf):
            rows.append((d, float(spec.periodic) * _factor(spec.growth, start, d, cpi), "periodic", ""))
    if spec.withdrawals and wf is not None:
        for d in _dates(wf, spec.frequency, until):
            rows.append((d, -float(spec.withdrawals) * _factor(spec.withdraw_growth, wf, d, cpi), "withdrawal", ""))
    for cf in spec.extra:
        d = pd.Timestamp(cf.date)
        if start <= d <= until:
            rows.append((d, float(cf.amount), "extra" if cf.amount >= 0 else "withdrawal", cf.note))
    df = pd.DataFrame(rows, columns=["date", "amount", "kind", "note"])
    df["executed"] = _execute(index, df["date"]) if len(df) else pd.DatetimeIndex([])
    order = {k: i for i, k in enumerate(KINDS)}
    return (df.assign(_o=df["kind"].map(order)).sort_values(["executed", "date", "_o"], kind="stable", na_position="last")
            .drop(columns="_o").reset_index(drop=True))


# ============================================================================================ XIRR
def xirr(dates, amounts, guess: float = 0.1) -> float:
    """Money-weighted annual return of dated flows (Excel XIRR: days / 365). Investor sign: money in negative, money out
    and the terminal value positive. Bracketing bisection on the NPV (no derivative, no starting-value trouble); the root
    nearest `guess` when several. NaN when all flows have one sign (-1.0 when nothing comes back)."""
    d = pd.DatetimeIndex(dates)
    a = np.asarray(amounts, float)
    keep = a != 0
    d, a = d[keep], a[keep]
    if len(a) < 2 or not (a > 0).any():
        return -1.0 if len(a) and (a < 0).any() else float("nan")
    if not (a < 0).any():
        return float("nan")
    t = ((d - d.min()).days.to_numpy()) / 365.0

    def npv(r):
        return float((a * np.power(1.0 + r, -t)).sum())
    grid = np.concatenate([[-0.999999], np.linspace(-0.99, 0.0, 34)[:-1], np.linspace(0.0, 1.0, 51), np.geomspace(1.0, 1000.0, 40)[1:]])
    vals = np.array([npv(r) for r in grid])
    roots = []
    for i in np.flatnonzero(np.sign(vals[:-1]) * np.sign(vals[1:]) <= 0):
        lo, hi, flo = grid[i], grid[i + 1], vals[i]
        if vals[i] == 0:
            roots.append(lo)
            continue
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            fm = npv(mid)
            if (fm > 0) == (flo > 0):
                lo, flo = mid, fm
            else:
                hi = mid
            if hi - lo < 1e-15:
                break
        roots.append(0.5 * (lo + hi))
    if not roots:
        return float("nan")
    return float(min(roots, key=lambda r: abs(r - guess)))


# ============================================================================================ macro series
def _read_long(path: Path, series: str) -> "pd.Series | None":
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    low = {c.lower(): c for c in df.columns}
    dcol = low.get("date") or df.columns[0]
    if "series" in low and "value" in low:
        sub = df[df[low["series"]].astype(str).str.upper() == series.upper()]
        if sub.empty:
            return None
        return pd.Series(sub[low["value"]].to_numpy(float), index=pd.to_datetime(sub[dcol])).sort_index()
    for c in df.columns:
        if c.upper() == series.upper() or (series.upper() == "CPI" and c.lower() in ("cpi", "cpi_ca_statcan", "value")) \
                or (series.upper() != "CPI" and c.lower() == "value" and series.upper() in path.stem.upper()):
            return pd.Series(pd.to_numeric(df[c], errors="coerce").to_numpy(), index=pd.to_datetime(df[dcol])).dropna().sort_index()
    return None


def load_ca_cpi() -> "pd.Series | None":
    """Canadian all-items CPI when a StatCan series is loaded under data/statcan/ (`make data-statcan`), else None."""
    cands = sorted((DATA / "statcan").glob("*.csv")) + [DATA / "macro_statcan.csv"]
    for p in cands:
        if p.exists():
            for name in ("CPI_CA_STATCAN", "CPI"):
                s = _read_long(p, name)
                if s is not None and len(s.dropna()):
                    return s
    return None


def load_ca10y() -> "pd.Series | None":
    """Government of Canada 10-year yield (BoC Valet V39055, percent -> decimal) when present under data/boc/."""
    for p in sorted((DATA / "boc").glob("*.csv")) + [DATA / "macro_boc.csv"]:
        if p.exists():
            s = _read_long(p, "V39055")
            if s is not None and len(s.dropna()):
                return s / 100.0
    return None


def _us_cpi(lab) -> "pd.Series":
    m = getattr(lab, "macro", None)
    if m is not None and getattr(m, "cpi", None) is not None:
        return m.cpi.dropna()
    d = pd.read_csv(DATA / "mirrors" / "us_cpi_monthly.csv", parse_dates=["date"])
    return pd.Series(d["cpi"].to_numpy(float), index=d["date"])


def _us10y() -> "pd.Series":
    d = pd.read_csv(DATA / "mirrors" / "us_10y_monthly.csv", parse_dates=["date"])
    return pd.Series(d["y10"].to_numpy(float) / 100.0, index=d["date"])


def resolve_cpi(lab, inflation, index: pd.DatetimeIndex, warnings: list) -> "tuple[pd.Series, str]":
    """(CPI level series, name) for `PlanSpec.inflation`; appends the plan's warnings."""
    if not isinstance(inflation, str):
        yrs = (index - index[0]).days.to_numpy() / 365.25
        return pd.Series(100.0 * (1.0 + float(inflation)) ** yrs, index=index), f"constant {float(inflation):.1%}/yr"
    if inflation not in ("auto", "us", "ca"):
        raise ValueError(f"inflation must be 'auto', 'us', 'ca' or a rate, got {inflation!r}")
    ca = load_ca_cpi() if inflation in ("auto", "ca") else None
    if ca is not None:
        return ca, "StatCan all-items CPI (Canada)"
    if inflation == "ca":
        warnings.append("inflation='ca' but no StatCan CPI is loaded under data/statcan/ (make data-statcan): US CPI used")
    if inflation in ("auto", "ca"):
        warnings.append(US_CPI_WARNING)
    return _us_cpi(lab), "US CPI (data/mirrors/us_cpi_monthly.csv)"


def resolve_rf(risk_free, warnings: list) -> "tuple[pd.Series, str]":
    """(annual risk-free yield series or constant Series, name)."""
    if not isinstance(risk_free, str):
        return pd.Series([float(risk_free)], index=[pd.Timestamp("1900-01-01")]), f"constant {float(risk_free):.2%}/yr"
    if risk_free == "ca10y":
        s = load_ca10y()
        if s is not None:
            return s, "Government of Canada 10-year (BoC V39055)"
        warnings.append("risk_free='ca10y' but BoC V39055 is not under data/boc/ (make data-boc): US 10-year yield used")
    elif risk_free != "us10y":
        raise ValueError(f"risk_free must be 'us10y', 'ca10y' or a rate, got {risk_free!r}")
    return _us10y(), "US 10-year Treasury yield (data/mirrors/us_10y_monthly.csv; expediency, see DECISIONS.md)"


def _monthly_rf(rf: pd.Series, month_ends: pd.DatetimeIndex, start: pd.Timestamp) -> pd.Series:
    """Monthly rate (1 + y)^(1/12) - 1 of the yield known at the start of each month (the previous month end)."""
    prev = pd.DatetimeIndex([start]).append(month_ends[:-1])
    s = rf.dropna()
    y = np.array([s.asof(d) if d >= s.index[0] else s.iloc[0] for d in prev], float)
    return pd.Series((1.0 + y) ** (1.0 / 12.0) - 1.0, index=month_ends, name="rf")


# ============================================================================================ legs
def _nav(pre: pd.Series, ext: pd.Series) -> pd.Series:
    """Flow-free NAV: u_t = u_(t-1) * pre_t / (pre_(t-1) + F_(t-1)); F = external flow executed on t-1 (the first date's
    flow is already in `pre`)."""
    f = ext.reindex(pre.index).fillna(0.0)
    f.iloc[0] = 0.0
    base = (pre.shift(1) + f.shift(1)).to_numpy()
    p = pre.to_numpy()
    r = np.where(base > 1e-12, p / np.where(base > 1e-12, base, 1.0) - 1.0, 0.0)
    r[0] = 0.0
    return pd.Series(np.cumprod(1.0 + r), index=pre.index)


def _after_tax(pre: pd.Series, table: pd.DataFrame, rrsp_rate: "float | None") -> pd.Series:
    """Pre-tax value less the tax paid so far (each year's tax at that year's last bar, minus the final RRSP balance tax,
    which the haircut `rrsp_rate` x balance represents all along). RRSP withdrawal tax is not here: it is taken out of the
    withdrawals received (TaxPaid, XIRRAfterTax, `PlanResult.flows.flow_tax`)."""
    paid = pd.Series(0.0, index=pre.index)
    last = pre.index.to_series().groupby(pre.index.year).last()
    other = table["tax_paid"] - table["tax_rrsp_withdrawal"].where(table.index == table.index.max(), 0.0)
    for y, v in other.items():
        if y in last.index:
            paid[pre.index >= last[y]] += float(v)
    return pre * (1.0 - (rrsp_rate or 0.0)) - paid


def _mer_drag(res, cfg, mkt) -> float:
    """Average annual MER of the holdings (equal weight within a rebalance, time-weighted between rebalances)."""
    ds = [d for d in res.holdings if len(res.holdings[d])]
    if not ds:
        return 0.0
    ends = list(ds[1:]) + [res.curve.index[-1]]
    w = np.array([(e - d).days for d, e in zip(ds, ends)], float)
    m = np.array([float(np.mean(cfg.costs.holding_drag(res.holdings[d], mkt.view(d)))) if hasattr(cfg.costs, "holding_drag") else 0.0
                  for d in ds])
    return float((m * w).sum() / w.sum()) if w.sum() > 0 else 0.0


def run_plan(lab, spec: PlanSpec) -> PlanResult:
    """The straight calculation: the strategy leg and the index leg on the same dated flows, tax, real values, metrics."""
    from .taxes import _index_info, index_ledger, index_yield, tax_buy_and_hold_flows
    mkt = lab.market
    index = mkt.index
    warnings: list = []
    infos: list = []
    cfg0 = spec.config if spec.config is not None else lab.config
    screen = lab.screen(spec.screen)
    bench = lab.benchmark_for(what="a plan")
    cpi, cpi_name = resolve_cpi(lab, spec.inflation, index, warnings)
    rf, rf_name = resolve_rf(spec.risk_free, warnings)

    # ---- the window: end past the data runs to the last bar and says so
    last_bar = index[-1]
    end_req = pd.Timestamp(spec.end) if spec.end else None
    if end_req is not None and end_req > last_bar:
        warnings.append(f"plan end {end_req.date()} is past the last data bar {last_bar.date()}: computed to the last bar "
                        f"{last_bar.date()} (the Monte Carlo continues to {end_req.date()})")
        end_eff = last_bar
    else:
        end_eff = mkt.trading_day(end_req, "prev") if end_req is not None else last_bar
    sched = schedule(replace(spec, end=str(end_eff.date())), index, cpi)
    sched = sched[sched["executed"].notna()].copy()
    if sched.empty:
        raise ValueError("the plan has no flows")
    start_bar = sched["executed"].min()
    if sched.loc[sched["executed"] == start_bar, "amount"].sum() <= 0:
        raise ValueError("the plan's first flow must be a contribution (initial or periodic)")
    sched["applied"] = (sched["executed"] < end_eff)
    sched.loc[sched["executed"] == start_bar, "applied"] = True
    plan_flows = sched[sched["applied"]].groupby("executed")["amount"].sum()

    # ---- tax: dollars of the plan, paid from outside the account, final gains realized
    cfg = cfg0
    tx = cfg0.tax
    if tx is not None:
        tx = replace(tx, capital=1.0, pay_from_portfolio=False, liquidate_at_end=True)
        cfg = replace(cfg, tax=tx)
        infos.append("tax paid from outside the account (after-tax value = pre-tax value less tax paid so far); "
                     "final gains realized; see plan.py")
    else:
        warnings.append("no Config.tax: every plan figure is pre-tax and after-tax equals pre-tax")
    if isinstance(cfg.costs, StandardCosts):
        cfg = replace(cfg, capital=1.0)                          # commission is dollars against a dollar portfolio

    # ---- strategy leg
    rebal = backtest.schedule(mkt, start_bar, cfg.rebalance_months, 0, end_eff)
    fdf = pd.DataFrame({"amount": plan_flows})
    pol = as_overlay(cfg.exit)
    if pol is None:
        res = backtest.simulate(screen, mkt, cfg, rebal, end_eff, flows=fdf, flow_mode=spec.cash_timing)
    else:
        curve0 = None
        if pol.needs_curve:
            curve0 = backtest.simulate(screen, mkt, replace(cfg, exit=None, tax=None), rebal, end_eff).curve
        expo = pol.exposure(mkt, rebal, curve0)
        if pol.rescreen_on_reentry:
            rebal = sorted(set(rebal) | {d for d in pol.reentry_dates if rebal[0] < d < end_eff})
        res = backtest.simulate(screen, mkt, cfg, rebal, end_eff, expo, flows=fdf, flow_mode=spec.cash_timing)
    pre = res.curve
    win = pre.index
    ext = res.flows["amount"]                                    # net external flow per executed date (withdrawals as received)
    rrsp_rate = tx.rrsp_rate() if tx is not None and tx.account == "rrsp" else None
    flow_tax = (-ext.clip(upper=0.0) * (rrsp_rate or 0.0))
    if tx is not None:
        tab_s = res.tax.copy()
        tab_s.attrs = dict(res.tax.attrs)
        after = _after_tax(pre, tab_s, rrsp_rate)
        for w in res.tax.attrs.get("warnings", ()):
            if w not in warnings:
                warnings.append(w)
    else:
        tab_s, after = None, pre.copy()

    # ---- index leg
    ib = replace(bench, mer=float(spec.index_mer), cost_bps=0.0)
    gcurve = ib.curve(mkt, cfg, start_bar, end_eff).reindex(win).ffill()
    ipre, ibought, irecv, _held = index_ledger(gcurve, plan_flows, float(bench.cost_bps))
    pf = plan_flows.reindex(win).fillna(0.0)
    iext = pd.Series(np.where(pf.to_numpy() < 0, -irecv.to_numpy(), pf.to_numpy()), index=win)
    if tx is not None:
        y, ynote = index_yield(bench, mkt, tx, start_bar)
        info = _index_info(mkt, bench)
        itax = tx
        if info and tx.index_foreign_share == 0.0 and info.get("foreign_share"):
            itax = replace(tx, index_foreign_share=float(info["foreign_share"]))
        tab_i, _ipre, _iafter = tax_buy_and_hold_flows(gcurve, plan_flows, y, itax, float(bench.cost_bps))
        iflow_tax = (-iext.clip(upper=0.0) * (rrsp_rate or 0.0))
        iafter = _after_tax(ipre, tab_i, rrsp_rate)
        infos.append(f"index dividend yield: {ynote}")
    else:
        tab_i, iafter, iflow_tax = None, ipre.copy(), pd.Series(0.0, index=win)

    # ---- tax table: the withdrawal tax joins the year's RRSP column
    tax_tab = None
    if tx is not None:
        parts = {}
        for leg, tab, ft in (("strategy", tab_s, flow_tax), ("index", tab_i, iflow_tax)):
            t = tab.copy()
            add = ft[ft > 0].groupby(ft[ft > 0].index.year).sum()
            if len(add):
                for yv, v in add.items():
                    if yv in t.index:
                        t.loc[yv, ["tax_rrsp_withdrawal", "tax_paid"]] += v
                t["cum_tax_paid"] = t["tax_paid"].cumsum()
                t["wealth_after_tax"] = t["wealth_pre_tax"] - t["cum_tax_paid"]
                t["cum_drag"] = 1.0 - t["wealth_after_tax"] / t["wealth_pre_tax"].where(t["wealth_pre_tax"] > 0)
            parts[leg] = t
        tax_tab = pd.concat(parts, axis=1)

    # ---- series
    defl_raw = pad_align(cpi, win).bfill()
    defl = cpi_deflator(defl_raw).rename("cpi_deflator")
    if cpi.dropna().index[-1] < win[-1] - pd.Timedelta(days=100):
        warnings.append(f"CPI series ends {cpi.dropna().index[-1].date()}, before the plan end {win[-1].date()}: last value carried forward")
    contributed = ext.reindex(win).fillna(0.0).cumsum().rename("contributed")
    s_unit, i_unit = _nav(pre, ext), _nav(ipre, iext)
    series = pd.DataFrame({
        "contributed": contributed, "strategy_pre": pre, "strategy_after_tax": after,
        "index_pre": ipre, "index_after_tax": iafter, "cpi_deflator": defl,
        "strategy_real": pre / defl, "index_real": ipre / defl,
        "strategy_after_tax_real": after / defl, "index_after_tax_real": iafter / defl,
        "cash": res.cash_curve.reindex(win).fillna(0.0), "strategy_unit": s_unit, "index_unit": i_unit})

    # ---- flows frame (per planned flow)
    fl = sched.copy()
    tot = fl[fl["applied"]].groupby("executed")["amount"].transform("sum")
    share = (fl["amount"] / tot.reindex(fl.index)).where(tot.reindex(fl.index).abs() > 1e-12, 0.0).fillna(0.0)
    fl["ext"] = fl["executed"].map(ext).fillna(0.0) * share * fl["applied"]
    fl["received"] = np.where(fl["amount"] < 0, -fl["ext"], 0.0)
    fl["flow_tax"] = fl["received"] * (rrsp_rate or 0.0)
    fl["strategy_units"] = (fl["ext"] / fl["executed"].map(s_unit)).fillna(0.0)
    fl["index_units"] = (fl["executed"].map(ibought).fillna(0.0) * share * fl["applied"])
    fl = fl.drop(columns="ext")

    # ---- monthly returns
    def monthly(unit):
        m = unit.groupby(unit.index.to_period("M")).last()
        r = m / m.shift(1).fillna(1.0) - 1.0
        r.index = unit.index.to_series().groupby(unit.index.to_period("M")).last().to_numpy()
        return r
    ms, mi = monthly(s_unit), monthly(i_unit)
    mon = pd.DataFrame({"strategy": ms, "index": mi})
    mon["rf"] = _monthly_rf(rf, pd.DatetimeIndex(mon.index), win[0])

    # ---- the card
    card = lab.data_card()
    notes = [f"plan: inflation series {cpi_name}", f"plan: risk-free series {rf_name}"] + [f"plan: {i}" for i in infos] \
        + [f"WARNING: {w}" for w in warnings]
    card = dataclasses.replace(card, notes=tuple(card.notes) + tuple(notes))
    # ---- costs
    cost_d = 0.0
    if res.costs is not None and len(res.costs):
        cost_d += float((res.costs * pre.reindex(res.costs.index).ffill()).sum())
    cost_d += float(res.flows["cost"].sum())
    i_cost_d = float((plan_flows.abs().sum()) * bench.cost_bps / 1e4)
    extras = {"cost_dollars": {"strategy": cost_d, "index": i_cost_d}, "mer": {"strategy": _mer_drag(res, cfg, mkt), "index": float(spec.index_mer)},
              "rrsp_rate": rrsp_rate, "rf_name": rf_name, "cpi_name": cpi_name, "taxed": tx is not None}
    result = PlanResult(spec, fl, series, mon, pd.DataFrame(), tax_tab, card, None, extras)
    result.metrics = metrics_table(result)
    if spec.goal is not None:
        hit = series.index[(series["strategy_after_tax"] >= spec.goal).to_numpy()]
        result.goal = {"reached": hit[0] if len(hit) else None,
                       "years": float((hit[0] - win[0]).days / 365.25) if len(hit) else None,
                       "final_vs_goal": float(series["strategy_after_tax"].iloc[-1] - spec.goal)}
    return result


# ============================================================================================ metrics
def _cagr(unit: pd.Series) -> float:
    yrs = (unit.index[-1] - unit.index[0]).days / 365.25
    return float(unit.iloc[-1] ** (1.0 / yrs) - 1.0) if yrs > 0 and unit.iloc[-1] > 0 else float("nan")


def _years_to(series: pd.Series, goal: "float | None") -> float:
    if goal is None:
        return float("nan")
    hit = series.index[(series >= goal).to_numpy()]
    return float((hit[0] - series.index[0]).days / 365.25) if len(hit) else float("nan")


def capm_stats(rs, ri, rf, benchmark: bool = False) -> dict:
    """Alpha (annualised OLS intercept of the monthly excess return of `rs` on that of `ri`), Beta, Sharpe
    (mean excess / std x sqrt(12)), Vol (std x sqrt(12)), TrackingError and InfoRatio of `rs` against `ri`; `rf` the monthly
    risk-free rate. `benchmark=True`: `rs` is the index itself (Alpha 0, Beta 1, tracking error 0)."""
    rs, ri, rf = (np.asarray(x, float) for x in (rs, ri, rf))
    n = len(rs)
    nan = float("nan")
    out = dict(Alpha=nan, Beta=nan, Sharpe=nan, Vol=nan, TrackingError=nan, InfoRatio=nan)
    if n < 3:
        return out
    ex, eb = rs - rf, ri - rf
    if np.std(ex) > 0:
        out["Sharpe"] = float(ex.mean() / ex.std(ddof=1) * np.sqrt(12))
    out["Vol"] = float(rs.std(ddof=1) * np.sqrt(12))
    if benchmark:
        out.update(Alpha=0.0, Beta=1.0, TrackingError=0.0)
        return out
    if np.std(eb) > 0:
        beta, alpha = np.polyfit(eb, ex, 1)
        out["Alpha"], out["Beta"] = float(alpha * 12), float(beta)
    d = rs - ri
    te = float(d.std(ddof=1) * np.sqrt(12))
    out["TrackingError"] = te
    out["InfoRatio"] = float(d.mean() * 12 / te) if te > 0 else nan
    return out


def metrics_table(result: PlanResult) -> pd.DataFrame:
    """Rows strategy / index / difference, columns METRICS, from a PlanResult's series, flows, monthly returns and tax."""
    s, fl, mon, spec, ex = result.series, result.flows, result.monthly, result.spec, result.extras
    T, t0 = s.index[-1], s.index[0]
    yrs = (T - t0).days / 365.25
    app = fl[fl["applied"]]
    rate = ex.get("rrsp_rate") or 0.0
    # investor flows by executed date: contributions out of pocket, withdrawals received
    inv = pd.DataFrame({"d": app["executed"], "a": np.where(app["amount"] > 0, app["amount"], -app["received"])}).groupby("d")["a"].sum()
    inv_at = pd.DataFrame({"d": app["executed"], "a": np.where(app["amount"] > 0, app["amount"], -app["received"] * (1 - rate))}).groupby("d")["a"].sum()
    defl = s["cpi_deflator"]
    dd = defl.reindex(inv.index)
    rows = {}
    ms, mi, rf = mon["strategy"].to_numpy(), mon["index"].to_numpy(), mon["rf"].to_numpy()
    for leg in ("strategy", "index"):
        pre, aft, unit = s[f"{leg}_pre"], s[f"{leg}_after_tax"], s[f"{leg}_unit"]
        contributed = float(s["contributed"].iloc[-1])
        fin, fa = float(pre.iloc[-1]), float(aft.iloc[-1])
        dT = float(defl.iloc[-1])

        def irr(flows, term, deflate=False):
            f = -flows.copy()
            if deflate:
                f = f / defl.reindex(f.index)
            tv = term / (dT if deflate else 1.0)
            return xirr(list(f.index) + [T], list(f.to_numpy()) + [tv])
        r = unit.to_numpy()
        ratio_at = (aft / pre.where(pre > 1e-12)).fillna(1.0)
        unit_at = unit * ratio_at / float(ratio_at.iloc[0] or 1.0)
        taxp = float(result.tax[(leg, "tax_paid")].sum()) if result.tax is not None else 0.0
        x = dict(Contributed=contributed, FinalPreTax=fin, FinalAfterTax=fa, FinalReal=fin / dT, FinalAfterTaxReal=fa / dT,
                 Gain=fin - contributed,
                 XIRR=irr(inv, fin), XIRRAfterTax=irr(inv_at, fa), XIRRReal=irr(inv, fin, True), XIRRAfterTaxReal=irr(inv_at, fa, True),
                 TWR=_cagr(unit), TWRAfterTax=_cagr(unit_at))
        x.update(capm_stats(ms if leg == "strategy" else mi, mi, rf, benchmark=(leg == "index")))
        x["MaxDD"] = float((unit / unit.cummax() - 1.0).min())
        avg_bal = float(pre.mean()) or float("nan")
        x["TaxPaid"] = taxp
        x["TaxDrag/yr"] = x["TWR"] - x["TWRAfterTax"]
        x["CostDrag/yr"] = ex["cost_dollars"][leg] / avg_bal / yrs if yrs > 0 else float("nan")
        x["MERDrag/yr"] = float(ex["mer"][leg])
        x["YearsToGoal"] = _years_to(aft, spec.goal)
        x["GoalShortfall"] = max(spec.goal - fa, 0.0) if spec.goal is not None else float("nan")
        rows[leg] = x
    tab = pd.DataFrame(rows).T.reindex(columns=METRICS).astype(float)
    tab.loc["difference"] = tab.loc["strategy"] - tab.loc["index"]
    return tab
