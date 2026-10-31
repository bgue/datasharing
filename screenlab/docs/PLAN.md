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
    tax_from_outside: bool = False     # True: tax paid from money outside the account (the older basis); default: from the portfolio
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
def plan_montecarlo(lab: Lab, spec: PlanSpec, n_paths=1000, generator="block", seed=0, horizon_end=None, result: PlanResult | None = None) -> PlanMC   # generator also "history"
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
- Tax (portfolio-paid straight run; see `tax_from_outside` below for the other basis): the replay pays, in every December and at the last simulated month, the share of the portfolio the straight run paid in tax that year (`PlanResult.extras["settle"]`, tax over the value after the day's flow), and at the end the RRSP balance tax or, taxable, `gain_eff` x (wealth less net contributions) with `gain_eff` calibrated so that the history gives the straight run's liquidation tax. `generator="history"` replays the recorded months in order and every flow grows by the straight run's own daily NAV: the terminal after-tax value equals `FinalAfterTax` to rounding (tested to 1e-9 relative for TFSA, RRSP and taxable). With `tax_from_outside` the older approximation below applies and the card says the bases differ. Older approximation: per calendar year, (dividend + foreign + interest tax) and (realized-gain tax net of refunds) as a share of that year's mean pre-tax wealth, read from the straight run's tax table (`result.tax` as a DataFrame for the strategy, or a dict with "strategy" / "index" tables), applied as a monthly drag with tax paid from the portfolio. Years past the table repeat the last year. TFSA: none. RRSP: the straight run's drag plus `rrsp_withdrawal_rate` on the end balance. Taxable with `liquidate_at_end`: end gain (wealth less net contributions at cost) taxed at the run's effective realized-gain rate (reinvested dividends are not added to cost: conservative). Without a tax config, after tax = pre-tax. A leg with no tax table is untaxed and the card says so. Withdrawals in the plan carry no extra explicit gain tax (inside the drag); wealth floors at zero.
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
- Tax conventions of the plan (needed so that the ledger stays homogeneous when money keeps arriving): the plan forces `TaxConfig(capital=1.0, pay_from_portfolio=not spec.tax_from_outside, liquidate_at_end=True)` and `StandardCosts.capital=1.0` on a copy of the Config (`plan.plan_tax_config`). **Default: the tax is paid FROM the portfolio** (`DECISIONS.md`, 2026-10-31): `simulate(flows=)` with `pay_from_portfolio` assesses each calendar year on the events so far at the year's last bar (`TaxStage.year_pay`), sells that share of every holding and of the cost bases (`TaxStage.scale`, `Result.tax_payments`), so `Result.curve` is the portfolio as it was and the tax table is `settled` (`taxes.assess(settled=True)`: `wealth_pre_tax` = the portfolio before that year's tax). The last year's tax, the tax on the gains realized at the end and, in an RRSP, `rrsp_withdrawal_rate` x (balance less that tax) come out on the last bar; in an RRSP the series also carries the haircut (the value of cashing in). The index leg does the same in `taxes.tax_buy_and_hold_flows` (fund units and cost base shrink). Units: `series["strategy_unit_after_tax"]` is the NAV with the flows (and the tax) in it, `flows.strategy_units` = flow / that NAV, V = (sum of units) x NAV exactly; `series["strategy_unit"]` is the pre-tax NAV (the payments treated as outflows) and `strategy_pre` its units ledger, the same plan with no tax (it differs from an untaxed run by the second-order effect of flow costs on a smaller portfolio). `PlanSpec.tax_from_outside=True` restores the older basis (after-tax value = pre-tax value less the tax paid so far, no growth credited; RRSP also less the haircut), floors the after-tax value at zero and lists the dates on which the tax paid exceeded the portfolio on the card and in `PlanResult.extras["floor_dates"]`. RRSP withdrawals are taxed at the rate on the amount received and taken out of the withdrawal (`TaxPaid`, `XIRRAfterTax`, `flows.flow_tax`; not in the after-tax value series). Without `Config.tax`, after-tax = pre-tax (card warning). Speed: a flow or tax-payment step between rebalances trades at that day's prices and reads costs and tax metadata from the last full snapshot (no `market.view` per flow): cold start of the 33-year dogs plan on the real panel 60 s -> 16 s.
- The first periodic contribution is on `start` itself (k = 0), together with `initial`; periodic contributions stop at `withdraw_from`; a flow executed on the last bar is not made (`flows.applied` False); flows dated after the last bar have `executed` NaT in `schedule` (the Monte Carlo passes a longer index). A negative `extra` is `kind="withdrawal"`.
- XIRR is the Excel convention (days / 365). Series values are the close BEFORE that day's flow (the strategy curve cannot show the post-flow value of its own day); `strategy_real` / `index_real` are PRE-tax values deflated, with `*_after_tax_real` added; extra series columns `strategy_unit` / `index_unit` (flow-free NAVs) are what the monthly returns and MaxDD use.
- `PlanResult.tax` is one DataFrame with MultiIndex columns `(leg, column)`, leg in {"strategy", "index"}, columns = `taxes.TAX_COLS`, index = year. `PlanResult.flows` has one row per planned flow with `date, amount, kind, note, executed, applied, received, flow_tax, strategy_units, index_units`. `PlanResult.extras` (new, optional) holds what `metrics_table` needs to recompute: cost dollars, MER drag, RRSP rate, series names.
- `plan_montecarlo` is not in `plan.py` (it lives in `montecarlo/plan_mc.py`).
- `metrics.py`: the `Sharpe` description now says that in a plan the risk-free series is the one named on the card.

Inflation and risk-free sources: StatCan CPI is read from `data/statcan/*.csv` (columns `date,cpi`, or long `date,series,value`) or `data/macro_statcan.csv` (`CPI_CA_STATCAN`); BoC V39055 from `data/boc/*.csv` (percent) or `data/macro_boc.csv`. The US series come from `lab.macro.cpi` when the lab has one, else `data/mirrors/`. `risk_free="us10y"` uses the yield known at the start of each month, monthly rate (1+y)^(1/12)-1; "ca10y" falls back to US with a card warning when V39055 is missing.

Limits: `CostDrag/yr` is trading cost dollars over the average balance and years (approximate); `MERDrag/yr` for the strategy is the equal-weight mean MER of the holdings, time-weighted between rebalances. `TWRAfterTax` scales the NAV by after-tax/pre-tax value (an approximation). Flows combined with tax-loss harvesting run (flow dates between harvest dates are flow-only steps) but are not covered by an exact test; with an exit policy a contribution is split into equity and parked cash at the exposure of that day.

## Operations, TUI and notebook

<!-- V2: phases -->
**Phases.** `PlanSpec.phases` is a tuple of `Phase(years_from, years_to, amount, note)`: the periodic amount by phase of life, in plan years counted from `start`, half-open [from, to); `years_to=None` runs to `withdraw_from` or the end. Years convert to months (`round(12 x years)`), the dates stay on the plan's own cadence (the same monthly grid as the flat stream, so a phase starting at year 10 pays on the 10-year anniversary), and a gap between phases pays nothing. When non-empty, `phases` replaces `periodic` (the flat amount is ignored and the card warns once); `schedule()` emits kind `periodic` with the phase note. Validation: `years_from >= 0`, `years_to` after `years_from`, amounts >= 0, ascending and non-overlapping (an open-ended phase must be last). Indexing: `phase_growth="indexed"` (default) multiplies each phase amount by the same factor as the flat stream, counted from the plan start, so with `growth="cpi"` the amount is in start-date dollars and is raised with CPI at each anniversary of `start` (a $3,000 phase in year 10 pays $3,000 x CPI(year 10) / CPI(start)); `"nominal"` pays the amount as written. With `phases=()` nothing changes. The `plan` operation takes `phases: [{years_from, years_to, amount, note}]` and `phase_growth` (a bad field is a 400 naming it); the Plan tab has a Contribution phases table, a "Use phases" switch (default ON, greys out the flat periodic field), a Flat button and a per-phase total line after a run. The tab's default form is the owner's illustrative base case: years 0-10 $500, 10-13 $3,000, 13-20 $1,000 a month.

<!-- PC: ops/plan.py, tui/plan.py, notebooks/plan_dca.ipynb, tests/test_plan_ops.py, tests/test_tui_plan.py -->
**Operations.** `plan` and `plan_montecarlo` are built-in operations (`ops/plan.py`): `lab.plan(...)`, `POST /labs/{lab}/plan`, MCP tool `plan`, job kinds `plan` and `plan_montecarlo`, and the store (`RunKind` in the LinkML schema), all generated from the registry. The request (`PlanRequest`) has every `PlanSpec` field as a documented argument, in this order: `screen` (a screen name of the lab), `start`, `end`, `initial`, `periodic`, `frequency`, `growth` (number or `"cpi"`), `extra` (a list of `{date, amount, note}`), `withdrawals`, `withdraw_from`, `withdraw_growth`, `cash_timing`, `index_mer`, `goal`, `risk_free`, `inflation`, `label`, then `tax` (the `TaxRequest` of the backtest: model, account, province, other_income, rrsp_withdrawal_rate, ...; without it every figure is pre-tax) and `config` (`ConfigOverrides`). `plan_montecarlo` adds `n_paths`, `generator` (`block`, `stationary`, `regime`, `iid`), `seed` and `horizon_end`; `plan` adds `daily`.

```python
lab.plan("dogs", "1990-01-02", tax=TaxConfig(...), initial=10_000, periodic=500, growth="cpi",
         extra=[CashFlow("1999-03-01", 25_000)], withdrawals=3_000, withdraw_from="2018-01-01", goal=1_500_000)   # PlanResult
lab.plan_montecarlo("dogs", "1990-01-02", tax=..., n_paths=500, seed=1)                                          # PlanMC
```

`plan` returns JSON `{spec, metrics, series, daily, flows, tax, goal, card, data_card}`: `metrics` is the three-row table (strategy, index, difference) with the 25 columns of `METRICS`; `series` is the wealth table (`contributed`, `strategy_pre`, `strategy_after_tax`, `index_pre`, `index_after_tax`, the real columns, `cash`) at month-ends, or every bar with `daily=true`; `flows` is one row per planned flow; `tax` is the per-year table with columns `strategy.<col>` and `index.<col>`; `goal` is `{reached, years, final_vs_goal}`; `card.warnings` carries the plan's own warnings (CPI and risk-free series used, 2025 tables, end past the data). `plan_montecarlo` returns `{spec, n_paths, seed, quantiles, p_beat_index, p_shortfall, p_shortfall_real, p_shortfall_index, goal, sequence, paired, reversal, fan, histograms, card}`. Bad fields are 400s that name the field (unknown screen, bad dates, `end` before `start`, `withdrawals` without `withdraw_from`, a plan with no contribution, `tax` and `config.tax` together, ...). The store keeps the metrics table (rows `strategy` / `index` / `difference`) of a plan, and the probabilities, quantiles and sequence table of a Monte Carlo study.

One Monte Carlo (2026-10-31 consolidation): `ops/plan.py` no longer has its own `monte_carlo()` / `split_tax()`; the operation calls `plan_mc.plan_montecarlo`, whose `_tax_table` now splits the `(leg, column)` MultiIndex of `PlanResult.tax` per leg, and which takes `result: PlanResult | None = None` (the straight run is not repeated: the notebook and `PlanMonteCarlo.run(result=)` pass theirs). `PlanRequest` / `lab.plan(...)` gain `tax_from_outside`; `generator` gains `"history"`. `screen` is a screen name (or the JSON text of a spec), not an object, so the request schema has no open object.

**TUI.** The Plan tab (key `P`, `tui/plan.py`): a form (start, end, lump sum, periodic with frequency and growth, an extras table with add and remove rows, withdrawals with a start date and growth, goal, cash timing, index MER, and for the Monte Carlo the number of paths, the resampler and the seed), the strategy and the account from the selected scenario (its Tax section), two buttons (Run plan, Run MC), the metrics table (strategy, index, difference; green where the strategy wins), a terminal chart of contributed against strategy and index, pre-tax and after tax, with a text sparkline beneath it, and for the Monte Carlo the quantile table, a fan chart, the sequence table and text histograms of terminal wealth. In the browser panel the Plan tab draws the same as Plotly figures (lines, the goal, the extraordinary flows as markers, the fan, the histograms). Commands `plan` and `plan_mc` (`tui_command`, see `tui/PROTOCOL.md`).

**Notebook.** `notebooks/plan_dca.ipynb` runs the plan of the owner's ask on the real S&P 500 panel (`Lab.real_sp500(start="1990-01-01", end="2022-12-30")`, local use only): $10,000 on 1990-01-02, $500 a month indexed to CPI, $25,000 in March 1999 and $15,000 in March 2009, $3,000 a month withdrawn from January 2018, the dogs against the index in a TFSA, an RRSP (30% withdrawal rate) and a taxable account (Alberta, other income $90,000), a goal of $1,500,000, and a 500-path Monte Carlo study. `tests/test_tax_notebooks.py::test_plan_notebook_runs` runs it (skipped when the panel is absent).

