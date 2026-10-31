# Plan module: a dated investment plan on a configured strategy, against the index

Owner's ask: "a portfolio assembled and backtested by selecting a configured strategy as of a start date, a periodic investment, optional extraordinary investments, and an anticipated end date. A straight-up calculation, compared to the same investment dates and amounts with a passive index fund. An optional Monte Carlo risk/return study. On all, alpha / beta / Sharpe / pre-tax return / post-tax return / post-inflation return etc."

Decisions already taken are in `DECISIONS.md` (risk-free rate, CPI, cash timing, headline return). Scope of version 1: contributions with growth or CPI indexing, extraordinary dated investments, a decumulation phase of dated or periodic withdrawals, a goal amount with years-to-goal and shortfall probability. Not in version 1: contribution-room checks.

## Contract (shared by the three implementation agents)

```python
# screenlab/plan.py
@dataclass(frozen=True) @serializable
class CashFlow:            # one dated flow, positive = contribution, negative = withdrawal
    date: str; amount: float; note: str = ""

@dataclass(frozen=True) @serializable
class PlanSpec:
    screen: str | dict                 # configured strategy (name in the registry, or a spec dict)
    config: Config | None = None       # rebalance, costs, exit policy, tax (account!), harvesting; Lab default if None
    start: str                         # first investment date
    end: str | None = None             # anticipated end date; None = last data bar
    initial: float = 0.0               # lump sum on `start`
    periodic: float = 0.0              # contribution per period
    frequency: str = "monthly"         # "biweekly" | "monthly" | "quarterly" | "annual"
    growth: float | str = 0.0          # annual growth of the periodic amount, or "cpi"
    extra: tuple[CashFlow, ...] = ()   # extraordinary investments (and dated withdrawals, negative)
    withdrawals: float = 0.0           # periodic withdrawal per period once `withdraw_from` is reached
    withdraw_from: str | None = None   # start of the decumulation phase
    withdraw_growth: float | str = 0.0 # growth of the periodic withdrawal, or "cpi"
    cash_timing: str = "invest"        # "invest" (next trading day, pro rata to target weights) | "hold" (until next rebalance)
    index_mer: float = 0.0005          # passive fund MER (annual); index trading cost from Config (default 0)
    goal: float | None = None          # target final value, nominal
    risk_free: str | float = "us10y"   # "us10y" | "ca10y" | constant
    inflation: str | float = "auto"    # "auto" (CA CPI if loaded, else US CPI) | "us" | "ca" | constant
    label: str = ""

@dataclass
class PlanResult:
    spec: PlanSpec
    flows: pd.DataFrame            # date, amount, kind ("initial"|"periodic"|"extra"|"withdrawal"), executed date, strategy/index units bought
    series: pd.DataFrame           # daily: contributed (cumulative net), strategy_pre, strategy_after_tax, index_pre, index_after_tax,
                                   #        cpi_deflator, strategy_real, index_real, cash (uninvested, "hold" mode)
    monthly: pd.DataFrame          # month-end time-weighted returns: strategy, index, rf (monthly), used for alpha/beta/Sharpe
    metrics: pd.DataFrame          # rows strategy / index / difference; columns = METRICS below
    tax: pd.DataFrame | None       # per-year tax table of the strategy run (Result.tax) and of the index (tax_buy_and_hold with flows)
    card: DataCard                 # with the plan's own warnings (CPI series used, rf series used, end date past data, etc.)
    goal: dict | None              # {"reached": date|None, "years": float|None, "final_vs_goal": float}

METRICS = [  # metrics.py gains these (guarded by the LinkML schema: update it and the drift test)
  "Contributed", "FinalPreTax", "FinalAfterTax", "FinalReal", "FinalAfterTaxReal", "Gain",
  "XIRR", "XIRRAfterTax", "XIRRReal", "XIRRAfterTaxReal",        # money-weighted, annualised
  "TWR", "TWRAfterTax",                                           # time-weighted CAGR over the plan window
  "Alpha", "Beta", "Sharpe", "Vol", "MaxDD", "TrackingError", "InfoRatio",   # on monthly TWR vs the index; alpha annualised
  "TaxPaid", "TaxDrag/yr", "CostDrag/yr", "MERDrag/yr", "YearsToGoal", "GoalShortfall",
]

def schedule(spec: PlanSpec, index: pd.DatetimeIndex, cpi: pd.Series | None) -> pd.DataFrame   # the dated flows, next-trading-day executed
def run_plan(lab: Lab, spec: PlanSpec) -> PlanResult                                            # the straight calculation
def plan_montecarlo(lab: Lab, spec: PlanSpec, n_paths=1000, generator="block", seed=0, horizon_end=None) -> PlanMC
```

```python
# screenlab/montecarlo/plan_mc.py
@dataclass
class PlanMC:
    terminal: pd.DataFrame      # per path: strategy_pre, strategy_after_tax, index_pre, index_after_tax, strategy_real, index_real, xirr_strategy, xirr_index, maxdd_strategy, maxdd_index
    paired: pd.Series           # strategy_after_tax - index_after_tax per path
    quantiles: pd.DataFrame     # 5/25/50/75/95 of each terminal column
    p_beat_index: float; p_shortfall: float | None   # vs goal, after tax
    sequence: pd.DataFrame      # sequence-of-returns study: terminal wealth when the worst 12 months fall early / middle / late in the plan
    card: ScenarioCard
```

Mechanics agreed:
- Strategy leg: the existing `backtest.simulate` pipeline runs the screen with the `Config`; the plan layer injects flows. In `invest` mode a contribution on executed date d buys the current target weights pro rata (new tax lots, trading cost per security via CostModel); in `hold` mode it sits in cash (Accrual cash rate) and joins at the next rebalance. Withdrawals sell pro rata (realizing gains; RRSP withdrawals taxed at the withdrawal rate per `TaxConfig`; TFSA none; taxable gains via the ledger). Unit-value accounting: the plan keeps a units ledger so the existing per-$1 curve and tax ledger stay valid between flows (each flow re-bases the running value; the TaxStage sees flows as external cash via a new `external_flow(date, amount)` hook on `TaxStage`).
- Index leg: same dated flows into the benchmark total-return series less `index_mer`, with `tax_buy_and_hold` extended to dated lots (dividends at the index yield, withholding per account, gains on withdrawal and at the end using average cost).
- Inflation: `MacroSeries.cpi`; `"auto"` picks CA when a StatCan series is loaded, else US (warning).
- Risk-free: monthly series from `us_10y_monthly.csv` (or BoC V39055 when present) converted to a monthly rate; Sharpe = (mean excess)/(std) × sqrt(12); alpha/beta = OLS of strategy monthly excess on index monthly excess, alpha annualised.
- End date past the data: the straight calculation stops at the last bar and says so; the Monte Carlo continues to `end`.
- Monte Carlo: resample JOINT strategy/index monthly (or daily) returns from the straight run's history with the existing generators (block/stationary/regime/iid), replay the same flow schedule on each path; tax on each path approximated with the plan's own effective tax rates by year (dividend / gain / withdrawal), stated as an approximation on the card.
- Operation `plan` in the registry (REST/MCP/jobs/store/openapi by registry; `RunKind` enum in the LinkML schema), `plan_montecarlo` operation likewise. TUI Plan tab: form (strategy, start, end, lump sum, periodic + frequency + growth, extras table, withdrawals, goal, account from the Tax section, cash timing), chart of contributed vs strategy vs index (pre- and after-tax), metrics table, MC fan + histograms via the Plotly panel.
- Notebook `notebooks/plan_dca.ipynb` on `Lab.real_sp500(start="1990-01-01", end="2022-12-30")`: $10,000 initial, $500 a month growing with CPI, two extraordinary investments, decumulation from 2018, dogs vs the index in TFSA / RRSP / taxable, with the owner's writing standard (legends, base case, summary, "What the evidence says").

## Monte Carlo: implemented

<!-- PB: montecarlo/plan_mc.py, research/plan_plots.py, tests/test_plan_mc.py -->
`plan_montecarlo(lab, spec, n_paths=1000, generator="block", seed=0, horizon_end=None, cpi="joint", block_months=12)` in `montecarlo/plan_mc.py` (the frame-level core is `simulate(monthly, flows, horizon_end, rates=..., goal=...)`; `replay`, `xirr`, `FlowGrid`, `TaxRates` are public). Figures: `research/plan_plots.py` (`fan_chart`, `terminal_hist`, `paired_hist`, `sequence_bars`, `reversal_bars`, `figures`).

Method and approximations (all repeated on `PlanMC.card.notes`):
- History: the straight run's `result.monthly` (strategy, index, rf) plus monthly inflation from `result.series["cpi_deflator"]`. Monthly, not daily: it is the series alpha/beta/Sharpe use, 12-month blocks stay 12 steps, and biweekly flows are handled inside the month (a flow at day fraction `f` of month `k` grows by `(1+r_k)^(1-f)`).
- The four columns are resampled jointly as one small monthly `Dataset` by the existing generators (`block` 12 months default, `stationary`, `regime`, `iid`, or a generator object). Per-path streams: path `k` depends on `seed` and `k` only, not on `n_paths` or the horizon.
- Horizon = `horizon_end`, else `spec.end`, else the data end; months past the data are resampled (card warning). The flow schedule (`plan.schedule`) is computed once and is identical on every path; CPI-indexed flows follow the history extended at its mean inflation. CPI itself is resampled JOINTLY with the returns for the real columns (`cpi="mean"` holds it at the historical mean rate).
- Tax is an approximation, not a re-simulation: per calendar year, (dividend + foreign + interest tax) and (realized-gain tax net of refunds) as a share of that year's mean pre-tax wealth, read from the straight run's tax table (`result.tax` as a DataFrame for the strategy, or a dict with "strategy" / "index" tables), applied as a monthly drag with tax paid from the portfolio. Years past the table repeat the last year. TFSA: none. RRSP: the straight run's drag plus `rrsp_withdrawal_rate` on the end balance. Taxable with `liquidate_at_end`: end gain (wealth less net contributions at cost) taxed at the run's effective realized-gain rate (reinvested dividends are not added to cost: conservative). Without a tax config, after tax = pre-tax. A leg with no tax table is untaxed and the card says so. Withdrawals in the plan carry no extra explicit gain tax (inside the drag); wealth floors at zero.
- Outputs: `terminal` (after-tax columns; `*_real` deflated by the path's CPI to start dollars; `xirr_*` after tax; `maxdd_*` time-weighted pre-tax), `paired`, `quantiles` (5/25/50/75/95), `p_beat_index` (strategy >= index after tax), `p_shortfall` vs `spec.goal` (nominal, after tax), `p_shortfall_real` (real terminal below the goal deflated by the mean end deflator), `p_shortfall_index`, `fan`.
- Sequence study: each path's worst rolling 12 months of the strategy, grouped by whether the window's centre falls in the first / middle / last third of the plan (`sequence`: n, median and mean terminal wealth of both legs, mean paired difference, shortfall). `reversal`: the historical returns in order versus the same returns reversed, same flows (deterministic).

## Straight calculation: implemented

<!-- PA: plan.py, backtest.simulate(flows=), pipeline.TaxStage.external_flow, taxes.tax_buy_and_hold_flows, metrics.py plan block, tests/test_plan.py -->
`plan.py`: `CashFlow`, `PlanSpec`, `PlanResult`, `METRICS`, `schedule`, `run_plan`, `metrics_table`, plus helpers `xirr(dates, amounts)`, `capm_stats(rs, ri, rf)`, `resolve_cpi`, `resolve_rf`. The exact arithmetic of the units ledger, the tax conventions and the metrics is in the module docstring. All plan behaviour is additive: `simulate(..., flows=None)` is byte-identical (golden unchanged).

Where things are: `backtest.simulate(screen, mkt, cfg, rebal, end, exposure, selection, flows=None, flow_mode="invest")` (`flows` = DataFrame indexed by executed trading day, column `amount`; the first formation date needs a positive flow = the starting value, the curve is then in dollars; `Result.flows` and `Result.cash_curve` carry the log and the "hold" cash). `TaxStage.external_flow(...)` and `TaxStage.hold_cash_interest(...)`. `taxes.index_ledger` and `taxes.tax_buy_and_hold_flows(growth, flows, yield_, tax, cost_bps)`. Plan metrics are registered in `metrics.METRICS` / `M.PLAN_*` and in `schema/runstore.linkml.yaml` (`StandardMetric`).

DEV (names the other agents use are unchanged; these are the deviations):
- `PlanSpec` field ORDER: `start` is the second field, right after `screen` (a dataclass cannot put a required field after fields with defaults). Always build it with keywords. Names, types and defaults are as in the contract.
- `TaxStage.external_flow(date, amount, value, e, prev, target, cost=None, trade_follows=False)`, not `(date, amount, prices)`: the stage already holds the market (`bind`), so prices come from it; it needs the portfolio value, equity share and weights to book basis and gains.
- `simulate` also takes `flow_mode` ("invest" | "hold"); `Result` has two new optional fields (`flows`, `cash_curve`).
- Tax conventions of the plan (needed so that the ledger stays homogeneous when money keeps arriving): the plan forces `TaxConfig(capital=1.0, pay_from_portfolio=False, liquidate_at_end=True)` and `StandardCosts.capital=1.0` on a copy of the Config. Tax is paid from outside the account: after-tax value = pre-tax value less the tax paid so far (no growth credited on tax paid), in an RRSP also less `rrsp_withdrawal_rate` x balance. RRSP withdrawals are taxed at that rate on the amount received and taken out of the withdrawal (`TaxPaid`, `XIRRAfterTax`, `flows.flow_tax`; not in the after-tax value series). Without `Config.tax`, after-tax = pre-tax (card warning).
- The first periodic contribution is on `start` itself (k = 0), together with `initial`; periodic contributions stop at `withdraw_from`; a flow executed on the last bar is not made (`flows.applied` False); flows dated after the last bar have `executed` NaT in `schedule` (the Monte Carlo passes a longer index). A negative `extra` is `kind="withdrawal"`.
- XIRR is the Excel convention (days / 365). Series values are the close BEFORE that day's flow (the strategy curve cannot show the post-flow value of its own day); `strategy_real` / `index_real` are PRE-tax values deflated, with `*_after_tax_real` added; extra series columns `strategy_unit` / `index_unit` (flow-free NAVs) are what the monthly returns and MaxDD use.
- `PlanResult.tax` is one DataFrame with MultiIndex columns `(leg, column)`, leg in {"strategy", "index"}, columns = `taxes.TAX_COLS`, index = year. `PlanResult.flows` has one row per planned flow with `date, amount, kind, note, executed, applied, received, flow_tax, strategy_units, index_units`. `PlanResult.extras` (new, optional) holds what `metrics_table` needs to recompute: cost dollars, MER drag, RRSP rate, series names.
- `plan_montecarlo` is not in `plan.py` (it lives in `montecarlo/plan_mc.py`).
- `metrics.py`: the `Sharpe` description now says that in a plan the risk-free series is the one named on the card.

Inflation and risk-free sources: StatCan CPI is read from `data/statcan/*.csv` (columns `date,cpi`, or long `date,series,value`) or `data/macro_statcan.csv` (`CPI_CA_STATCAN`); BoC V39055 from `data/boc/*.csv` (percent) or `data/macro_boc.csv`. The US series come from `lab.macro.cpi` when the lab has one, else `data/mirrors/`. `risk_free="us10y"` uses the yield known at the start of each month, monthly rate (1+y)^(1/12)-1; "ca10y" falls back to US with a card warning when V39055 is missing.

Limits: `CostDrag/yr` is trading cost dollars over the average balance and years (approximate); `MERDrag/yr` for the strategy is the equal-weight mean MER of the holdings, time-weighted between rebalances. `TWRAfterTax` scales the NAV by after-tax/pre-tax value (an approximation). Flows combined with tax-loss harvesting run (flow dates between harvest dates are flow-only steps) but are not covered by an exact test; with an exit policy a contribution is split into equity and parked cash at the exposure of that day.
