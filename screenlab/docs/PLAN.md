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
