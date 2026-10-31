"""plan_montecarlo: Monte Carlo risk/return study of a dated investment plan (docs/PLAN.md).

# PB: whole module.

Method. The straight run (`plan.run_plan`) supplies the joint MONTHLY history of the strategy, the passive index,
the risk-free rate and CPI (`result.monthly`, `result.series["cpi_deflator"]`). The four columns are turned into a
small monthly `Dataset` (one "name" each) and resampled TOGETHER by the existing scenario generators
(`montecarlo/generators.py`), so on every path the strategy, the index, the risk-free rate and CPI move on the same
resampled months. The plan's dated flows (`plan.schedule`, computed once, identical on every path) are replayed on
each path for the strategy and for the index. Per-path random streams are the generators' own: path `k` depends on
`seed` and `k` only.

Monthly, not daily: the straight run's `monthly` is the plan's own month-end time-weighted series (the one alpha,
beta and Sharpe use); a 12-month block over 252-step years would be a 252-day block with no gain for a plan whose
unit of analysis is the year; and 1000 paths x 30 years x 4 series stays small. A flow dated inside a month
(biweekly flows, a mid-month start) is placed at its day fraction `f` of the month and grows by `(1 + r)**(1 - f)`
for that month's return `r`, so intra-month timing is kept without daily returns.

Tax is approximated, not re-simulated (no lots, no screens on the path): the straight run's tax table gives, per
calendar year, the dividend / foreign / interest tax and the realized-gain tax as a share of that year's mean
pre-tax wealth; on every path the after-tax wealth is the pre-tax recursion less the same monthly drag (tax paid
from the portfolio). Account: TFSA no drag; RRSP the straight run's (usually nil) drag and, at the end, the balance
less `rrsp_withdrawal_rate`; taxable, plus the tax on unrealized gains at the end when `liquidate_at_end`
(average cost: gain = wealth - net contributions, taxed at the straight run's realized-gain effective rate).
CPI is resampled JOINTLY with the returns (default; `cpi="mean"` holds it at the history's mean monthly rate); the
flow schedule itself is fixed (CPI-indexed flows follow the history extended at its mean inflation).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from .generators import (BlockBootstrap, IIDBootstrap, RegimeBootstrap, ScenarioCard, StationaryBootstrap)

QUANTILES = (5, 25, 50, 75, 95)
LEGS = ("strategy", "index")
GROUPS = ("first", "middle", "last")
TERMINAL_COLS = ["strategy_pre", "strategy_after_tax", "index_pre", "index_after_tax", "strategy_real",
                 "index_real", "xirr_strategy", "xirr_index", "maxdd_strategy", "maxdd_index"]
APPROX = ("Plan Monte Carlo approximations: monthly resampled returns (flows inside a month grow geometrically from "
          "their day fraction); tax = the straight run's effective rates by calendar year applied as a drag on "
          "wealth, no lots or path-dependent harvesting; mid-plan withdrawals pay no extra gain tax (carried by the "
          "drag); the flow schedule is the same on every path; terminal wealth cannot go below zero.")


# --------------------------------------------------------------------------- outputs
@dataclass
class PlanMC:
    """terminal  per path: strategy_pre, strategy_after_tax, index_pre, index_after_tax, strategy_real, index_real
                 (after tax, deflated by the path's CPI to plan-start dollars), xirr_* (money-weighted, after tax),
                 maxdd_* (time-weighted pre-tax drawdown).
    paired    strategy_after_tax - index_after_tax per path.
    quantiles 5/25/50/75/95 of each terminal column.
    p_beat_index  share of paths with strategy_after_tax >= index_after_tax; p_shortfall: share with
                 strategy_after_tax < goal (nominal); p_shortfall_real: real terminal < goal / mean end deflator.
    sequence  by where the path's worst 12 months fall (first / middle / last third of the plan).
    reversal  deterministic historical path, returns in order vs reversed, same flows.
    fan       month-end quantiles of after-tax wealth (strategy_pN, index_pN) and `contributed` (net).
    """
    terminal: pd.DataFrame
    paired: pd.Series
    quantiles: pd.DataFrame
    p_beat_index: float
    p_shortfall: float | None
    sequence: pd.DataFrame
    card: ScenarioCard
    reversal: pd.DataFrame | None = None
    fan: pd.DataFrame | None = None
    p_shortfall_real: float | None = None
    p_shortfall_index: float | None = None
    goal: float | None = None
    n_paths: int = 0
    seed: int = 0
    warnings: list = field(default_factory=list)

    def __str__(self):
        q = self.quantiles[["strategy_after_tax", "index_after_tax"]].round(0)
        s = [self.card.line(), f"P(strategy >= index, after tax) = {self.p_beat_index:.1%}"]
        if self.p_shortfall is not None:
            s.append(f"P(shortfall vs goal {self.goal:,.0f}) strategy {self.p_shortfall:.1%}, "
                     f"index {self.p_shortfall_index:.1%}")
        return "\n".join(s + [q.to_string()] + list(self.warnings))


@dataclass(frozen=True)
class FlowGrid:
    """Flows on the monthly grid: step k covers month k (1-based), `frac` in [0, 1] is the day fraction of the
    month at which the flow happens, `amount` > 0 contribution, < 0 withdrawal."""
    step: np.ndarray
    frac: np.ndarray
    amount: np.ndarray

    @classmethod
    def make(cls, step, frac, amount) -> "FlowGrid":
        return cls(np.asarray(step, int), np.asarray(frac, float), np.asarray(amount, float))


@dataclass(frozen=True)
class TaxRates:
    """One leg's tax approximation. `div` / `gain`: annual tax as a share of mean pre-tax wealth, by calendar year."""
    account: str = "taxable"
    div: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    gain: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    gain_eff: float = 0.0                 # tax per $ of realized gain (end-of-plan liquidation)
    rrsp: float = 0.0                     # RRSP withdrawal rate on the end balance
    liquidate: bool = False

    def monthly_drag(self, years: np.ndarray) -> np.ndarray:
        d = (self.div.add(self.gain, fill_value=0.0)).sort_index()
        if not len(d):
            return np.zeros(len(years))
        full = d.reindex(range(min(int(d.index.min()), int(years.min())), max(int(d.index.max()), int(years.max())) + 1))
        full = full.ffill().bfill().clip(0, 0.99)
        a = full.loc[np.asarray(years, int)].to_numpy()
        return 1.0 - (1.0 - a) ** (1.0 / 12.0)


# --------------------------------------------------------------------------- the replay kernel
def replay(r: np.ndarray, grid: FlowGrid, rates: TaxRates | None = None, dates: pd.DatetimeIndex | None = None):
    """Wealth of one leg on paths `r` [P, N] of monthly returns (column 0 is the anchor, ignored), flows `grid`.

    Returns pre [P, N], after [P, N] (pre less the tax drag; no liquidation), basis [P] (net contributions at cost).
    v_k = v_{k-1}(1 + r_k) + sum_i a_i (1 + r_k)^(1 - f_i), floored at 0.
    """
    r = np.atleast_2d(np.asarray(r, float))
    P, N = r.shape
    drag = np.zeros(N)
    if rates is not None and dates is not None:
        drag = rates.monthly_drag(dates.year.to_numpy())
    by = {}
    for k, f, a in zip(grid.step, grid.frac, grid.amount):
        if 1 <= k < N:
            by.setdefault(int(k), []).append((float(f), float(a)))
    pre = np.zeros((P, N))
    aft = np.zeros((P, N))
    v = np.zeros(P)
    w = np.zeros(P)
    basis = np.zeros(P)
    for k in range(1, N):
        g = 1.0 + r[:, k]
        v = v * g
        w = w * g
        for f, a in by.get(k, ()):
            amt = a * g ** (1.0 - f)
            if a < 0:
                with np.errstate(invalid="ignore", divide="ignore"):
                    cut = np.where(w > 0, np.minimum(1.0, -amt / np.where(w > 0, w, 1.0)), 1.0)
                basis = basis * (1.0 - cut)
            else:
                basis = basis + a
            v = np.maximum(v + amt, 0.0)
            w = np.maximum(w + amt, 0.0)
        w = w * (1.0 - drag[k])
        pre[:, k] = v
        aft[:, k] = w
    return pre, aft, basis


def terminal_after_tax(after_end: np.ndarray, basis: np.ndarray, rates: TaxRates | None) -> np.ndarray:
    if rates is None or rates.account == "tfsa":
        return after_end
    if rates.account == "rrsp":
        return after_end * (1.0 - rates.rrsp)
    if rates.liquidate:
        return after_end - rates.gain_eff * np.maximum(after_end - basis, 0.0)
    return after_end


def xirr(grid: FlowGrid, end_value: np.ndarray, n_steps: int, iters: int = 60) -> np.ndarray:
    """Money-weighted annual return per path: sum_i a_i (1 + x)^(T - t_i) = W_end, t_i = (k - 1 + f)/12 years,
    T = (N - 1)/12. Vectorized bisection on x in [-0.99, 10]; -1 when the end value is 0; NaN without flows."""
    W = np.asarray(end_value, float)
    if not len(grid.amount):
        return np.full(W.shape, np.nan)
    T = (n_steps - 1) / 12.0
    e = T - (grid.step - 1 + grid.frac) / 12.0
    a = grid.amount
    lo = np.full(W.shape, -0.99)
    hi = np.full(W.shape, 10.0)

    def h(x):
        return ((1.0 + x)[:, None] ** e[None, :] * a[None, :]).sum(axis=1) - W
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        up = h(mid) > 0
        hi = np.where(up, mid, hi)
        lo = np.where(up, lo, mid)
    x = 0.5 * (lo + hi)
    return np.where(W <= 0, -1.0, x)


def _maxdd(r: np.ndarray) -> np.ndarray:
    c = np.cumprod(1.0 + r, axis=1)
    return (c / np.maximum.accumulate(c, axis=1) - 1.0).min(axis=1)


def _worst12_group(r: np.ndarray) -> np.ndarray:
    """Third (0 first, 1 middle, 2 last) of the plan in which each path's worst rolling 12-month return starts."""
    P, N = r.shape
    M = N - 1
    if M < 12:
        raise ValueError(f"the sequence study needs at least 12 months, got {M}")
    c = np.concatenate([np.zeros((P, 1)), np.cumsum(np.log1p(r[:, 1:]), axis=1)], axis=1)
    win = c[:, 12:] - c[:, :-12]                       # window of months s..s+11 -> index s-1
    s = win.argmin(axis=1) + 1                          # first month (1-based) of the worst window
    centre = (s + 5.5) / M
    return np.minimum((3 * centre).astype(int), 2)


# --------------------------------------------------------------------------- tax rates from the straight run
def _tax_table(tab, leg: str):
    if tab is None:
        return None
    if isinstance(tab, dict):
        if leg in tab:
            return tab[leg]
        for k, v in tab.items():
            if (leg == "index") == ("index" in str(k).lower() or "passive" in str(k).lower()):
                return v
        return None
    return tab if leg == "strategy" else None


def rates_from_table(tab: pd.DataFrame | None, pre: pd.Series, cfg_tax: Any) -> TaxRates | None:
    """Effective rates from a `taxes.assess` table (TAX_COLS) and the leg's daily pre-tax wealth in dollars."""
    if tab is None or cfg_tax is None or not len(tab):
        return None
    yrs = pre.groupby(pre.index.year).mean()
    w = yrs.reindex(tab.index.astype(int)).replace(0, np.nan)
    col = lambda c: tab[c].fillna(0.0) if c in tab else pd.Series(0.0, index=tab.index)
    div = (col("tax_dividends") + col("tax_foreign_dividends") + col("tax_interest")).set_axis(w.index) / w
    cg = (col("tax_capital_gains") - col("refund")).set_axis(w.index)
    gain = cg / w
    liquidate = bool(getattr(cfg_tax, "liquidate_at_end", False))
    if liquidate and len(gain) > 1:
        gain.iloc[-1] = gain.iloc[-2]                        # the last year carries the liquidation: not a yearly drag
    realized = (col("realized_gains") + col("realized_losses")).clip(lower=0).sum()
    gain_eff = float(col("tax_capital_gains").sum() / realized) if realized > 0 else 0.0
    acct = getattr(cfg_tax, "account", "taxable")
    rr = 0.0
    if acct == "rrsp":
        f = getattr(cfg_tax, "rrsp_rate", None)
        rr = float(f() if callable(f) else (getattr(cfg_tax, "rrsp_withdrawal_rate", 0.0) or 0.0)) or 0.0
    return TaxRates(acct, div.fillna(0.0), gain.fillna(0.0), gain_eff, rr, liquidate)


# --------------------------------------------------------------------------- generators and grids
def make_generator(generator="block", block_months: float = 12):
    if not isinstance(generator, str):
        return generator
    g = {"block": lambda: BlockBootstrap(block_months=block_months),
         "stationary": lambda: StationaryBootstrap(mean_block_months=block_months),
         "regime": lambda: RegimeBootstrap(block_months=min(6, block_months)),
         "iid": lambda: IIDBootstrap()}.get(generator)
    if g is None:
        raise ValueError(f"generator must be block|stationary|regime|iid or a generator object, got {generator!r}")
    return g()


def month_grid(first_month_end: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """E_0 (the month-end before the first history month) ... the month-end of `end`'s month."""
    e0 = (pd.Timestamp(first_month_end).to_period("M").start_time - pd.Timedelta(days=1)).normalize()
    last = pd.Timestamp(end).to_period("M").end_time.normalize()
    return pd.date_range(e0, last, freq="ME")


def flow_grid(flows: pd.DataFrame, E: pd.DatetimeIndex, warnings: list | None = None) -> FlowGrid:
    """Flows (`date`, `amount`; a column `executed` / `executed_date` wins over `date`) on the month grid E."""
    if flows is None or not len(flows):
        return FlowGrid.make([], [], [])
    dcol = next((c for c in ("executed", "executed_date", "exec_date", "date") if c in flows), "date")
    d = pd.to_datetime(flows[dcol]).reset_index(drop=True)
    k = (d.dt.year - E[0].year) * 12 + (d.dt.month - E[0].month)
    ok = ((k >= 1) & (k < len(E))).to_numpy()
    if warnings is not None and (~ok).any():
        warnings.append(f"{int((~ok).sum())} flow(s) fall outside the simulated months and are dropped")
    k = k.to_numpy()[ok]
    dd = d[ok].reset_index(drop=True)
    lo, hi = E[k - 1].to_numpy(), E[k].to_numpy()
    frac = (dd.to_numpy() - lo) / (hi - lo)
    return FlowGrid.make(k, np.clip(frac.astype(float), 0.0, 1.0), flows["amount"].to_numpy(float)[ok])


# --------------------------------------------------------------------------- the study on a history
def _history_dataset(monthly: pd.DataFrame, E0: pd.Timestamp):
    from ..dataset import Dataset
    from ..realdata.bundled import price_only_fundamentals
    idx = pd.DatetimeIndex([E0, *monthly.index])
    lev = pd.concat([pd.DataFrame(1.0, index=idx[:1], columns=monthly.columns),
                     (1.0 + monthly).cumprod()])
    lev.index = idx
    lev.index.name = "date"
    ix = lev[["index"]].copy()
    return Dataset.from_frames(price_only_fundamentals(lev), lev, ix, name="plan history",
                               source="straight plan run (monthly strategy / index / rf / cpi)")


def simulate(monthly: pd.DataFrame, flows: pd.DataFrame, horizon_end, *, rates=None, goal=None, n_paths=1000,
             generator="block", seed=0, cpi="joint", block_months: float = 12, account_note: str = "",
             warnings=None) -> PlanMC:
    """The study from frames. `monthly`: month-end DatetimeIndex, columns strategy, index, rf, cpi (monthly simple
    returns; cpi = monthly inflation); `flows`: date/amount; `horizon_end`: last date simulated; `rates`:
    {"strategy": TaxRates|None, "index": TaxRates|None}."""
    warnings = list(warnings or [])
    rates = rates or {}
    monthly = monthly[["strategy", "index", "rf", "cpi"]].astype(float)
    if cpi == "mean":
        monthly = monthly.assign(cpi=monthly["cpi"].mean())
    elif cpi != "joint":
        raise ValueError("cpi must be 'joint' or 'mean'")
    gen = make_generator(generator, block_months)
    E = month_grid(monthly.index[0], pd.Timestamp(horizon_end))
    N = len(E)
    base = _history_dataset(monthly, E[0])
    ps = gen.paths(base, int(n_paths), N / 12.0, seed, start=E[0])
    R = ps.returns                                           # [P, N, 4]: strategy, index, rf, cpi (step 0 = 0)
    assert R.shape[1] == N, (R.shape, N)
    grid = flow_grid(flows, E, warnings)
    out = _evaluate(R[:, :, 0], R[:, :, 1], R[:, :, 3], grid, E, rates, goal)
    reversal = _reversal(monthly, flows, rates, E[0], warnings)
    card = _card(base, gen, seed, warnings + ([account_note] if account_note else []), cpi, N - 1)
    return PlanMC(terminal=out["terminal"], paired=out["paired"], quantiles=out["quantiles"],
                  p_beat_index=out["p_beat"], p_shortfall=out["p_short"], sequence=out["sequence"], card=card,
                  reversal=reversal, fan=out["fan"], p_shortfall_real=out["p_short_real"],
                  p_shortfall_index=out["p_short_index"], goal=goal, n_paths=int(n_paths), seed=int(seed),
                  warnings=list(card.notes))


def _evaluate(rs, ri, rc, grid, E, rates, goal) -> dict:
    rs_, ri_ = rates.get("strategy"), rates.get("index")
    pre_s, aft_s, b_s = replay(rs, grid, rs_, E)
    pre_i, aft_i, b_i = replay(ri, grid, ri_, E)
    N = len(E)
    ts = terminal_after_tax(aft_s[:, -1], b_s, rs_)
    ti = terminal_after_tax(aft_i[:, -1], b_i, ri_)
    defl = np.cumprod(1.0 + rc, axis=1)[:, -1]
    term = pd.DataFrame({
        "strategy_pre": pre_s[:, -1], "strategy_after_tax": ts, "index_pre": pre_i[:, -1], "index_after_tax": ti,
        "strategy_real": ts / defl, "index_real": ti / defl,
        "xirr_strategy": xirr(grid, ts, N), "xirr_index": xirr(grid, ti, N),
        "maxdd_strategy": _maxdd(rs), "maxdd_index": _maxdd(ri)})
    term.index.name = "path"
    paired = (term["strategy_after_tax"] - term["index_after_tax"]).rename("paired")
    q = term.quantile([p / 100 for p in QUANTILES])
    q.index = list(QUANTILES)
    q.index.name = "pct"
    p_short = p_short_i = p_real = None
    if goal is not None:
        p_short = float((term["strategy_after_tax"] < goal).mean())
        p_short_i = float((term["index_after_tax"] < goal).mean())
        p_real = float((term["strategy_real"] < goal / defl.mean()).mean())
    grp = _worst12_group(rs) if N - 1 >= 12 else np.full(len(rs), -1)      # under a year: no study (empty rows)
    rows = {}
    for g, name in enumerate(GROUPS):
        m = grp == g
        t = term[m]
        rows[name] = {"n": int(m.sum()),
                      "strategy_median": t["strategy_after_tax"].median(), "strategy_mean": t["strategy_after_tax"].mean(),
                      "index_median": t["index_after_tax"].median(), "index_mean": t["index_after_tax"].mean(),
                      "paired_mean": (t["strategy_after_tax"] - t["index_after_tax"]).mean(),
                      "p_shortfall": float((t["strategy_after_tax"] < goal).mean()) if goal is not None and m.any() else np.nan}
    seq = pd.DataFrame(rows).T.astype({"n": int})
    seq.index.name = "worst_12m_in"
    contributed = np.cumsum(np.bincount(grid.step, weights=grid.amount, minlength=N)[:N])
    fan = {"contributed": contributed}
    for nm, a in (("strategy", aft_s), ("index", aft_i)):
        qs = np.percentile(a, QUANTILES, axis=0)
        for p, row in zip(QUANTILES, qs):
            fan[f"{nm}_p{p}"] = row
    fan = pd.DataFrame(fan, index=E)
    return dict(terminal=term, paired=paired, quantiles=q, p_beat=float((term["strategy_after_tax"] >= term["index_after_tax"]).mean()),
                p_short=p_short, p_short_index=p_short_i, p_short_real=p_real, sequence=seq, fan=fan)


def _reversal(monthly, flows, rates, E0, warnings) -> pd.DataFrame:
    """Deterministic: the history's own returns in order vs the same returns reversed, same flows."""
    E = month_grid(monthly.index[0], monthly.index[-1])
    grid = flow_grid(flows, E, None)
    rows = {}
    for name, order in (("forward", slice(None)), ("reversed", slice(None, None, -1))):
        a = {}
        for leg in LEGS:
            r = np.r_[0.0, monthly[leg].to_numpy()[order]][None, :]
            pre, aft, b = replay(r, grid, rates.get(leg), E)
            a[f"{leg}_pre"] = float(pre[0, -1])
            a[f"{leg}_after_tax"] = float(terminal_after_tax(aft[:, -1], b, rates.get(leg))[0])
        rows[name] = a
    df = pd.DataFrame(rows).T
    df.loc["reversed_minus_forward"] = df.loc["reversed"] - df.loc["forward"]
    df["contributed"] = float(grid.amount.sum())
    df.index.name = "order"
    return df


def _card(base, gen, seed, warnings, cpi, months) -> ScenarioCard:
    c = base.manifest
    notes = tuple(dict.fromkeys([*c.notes, APPROX, f"CPI: {'resampled jointly with the returns' if cpi == 'joint' else 'held at its historical mean monthly rate'}",
                                 *warnings]))
    f = {k: getattr(c, k) for k in c.__dataclass_fields__ if k != "notes"}
    return ScenarioCard(**f, notes=notes, generator=gen.to_dict(), seed=int(seed), path_id=None)


# --------------------------------------------------------------------------- the public entry point
def _first(df: pd.DataFrame, names):
    return next((c for c in names if c in df), None)


def plan_montecarlo(lab, spec, n_paths: int = 1000, generator="block", seed: int = 0, horizon_end=None,
                    cpi: str = "joint", block_months: float = 12) -> PlanMC:
    """Monte Carlo study of `spec` (a `plan.PlanSpec`) on `lab`: see the module doc. Horizon = `horizon_end`, else
    `spec.end`, else the end of the straight run's history."""
    from .. import plan as _plan
    res = _plan.run_plan(lab, spec)
    m = res.monthly
    if m is None or len(m) < 3:
        raise ValueError("the straight run has fewer than 3 months of history")
    warn = []
    ser = res.series
    if "cpi_deflator" in ser and ser["cpi_deflator"].notna().any():
        dm = ser["cpi_deflator"].resample("ME").last().ffill()
        infl = dm.pct_change()
        infl = infl.reindex(m.index).fillna(0.0)
    else:
        infl = pd.Series(0.0, index=m.index)
        warn.append("no CPI series in the straight run: real values equal nominal")
    monthly = pd.DataFrame({"strategy": m["strategy"], "index": m["index"], "rf": m["rf"], "cpi": infl}).dropna(subset=["strategy", "index"])
    monthly["rf"] = monthly["rf"].fillna(0.0)
    data_end = monthly.index[-1]
    end = pd.Timestamp(horizon_end or spec.end or data_end)
    if end > data_end:
        warn.append(f"horizon {end.date()} is past the last data month {data_end.date()}: the extra months are resampled")
    # one schedule for every path: the dates run to the horizon, CPI-indexed flows follow the history extended at its mean rate
    days = pd.bdate_range(pd.Timestamp(spec.start), end)
    cp = None
    if "cpi_deflator" in ser:
        d = ser["cpi_deflator"].dropna()
        mean_m = float(monthly["cpi"].mean())
        extra = pd.bdate_range(d.index[-1] + pd.Timedelta(days=1), end)
        ext = d.iloc[-1] * (1.0 + mean_m) ** (((extra - d.index[-1]).days) / 30.4375) if len(extra) else pd.Series(dtype=float)
        cp = pd.concat([d, pd.Series(np.asarray(ext), index=extra)]) if len(extra) else d
    sched = _plan.schedule(spec, days, cp)
    sched = sched[pd.to_datetime(sched[_first(sched, ("executed", "executed_date", "exec_date", "date"))]) <= end]
    cfg = getattr(spec, "config", None) or getattr(lab, "config", None)
    tax_cfg = getattr(cfg, "tax", None)
    rates = {}
    if tax_cfg is None:
        warn.append("no tax config: after-tax = pre-tax")
    for leg in LEGS:
        rates[leg] = rates_from_table(_tax_table(getattr(res, "tax", None), leg), ser[f"{leg}_pre"], tax_cfg)
        if tax_cfg is not None and rates[leg] is None:
            warn.append(f"no {leg} tax table in the straight run: that leg is untaxed on the paths")
    return simulate(monthly, sched, end, rates=rates, goal=getattr(spec, "goal", None), n_paths=n_paths,
                    generator=generator, seed=seed, cpi=cpi, block_months=block_months, warnings=warn)
