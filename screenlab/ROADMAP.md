# Roadmap

## Done (sketch)
- Eight built-in screens: Dogs, Graham defensive and enterprising, net-nets,
  Piotroski, Magic Formula, value + momentum, low-vol dividend.
- Screen definition through a builder or JSON/dict specs: universe, derived
  columns, filters, Graham tests, composite rank.
- Equal-weight periodic-rebalance backtester that runs on daily or monthly
  data.
- Per-security trading cost (flat, cap-tiered or per ticker), flat
  commissions, per-security MER, and a delisting haircut.
- Buy-and-hold index benchmark with its own MER and trading cost (default 0),
  or a screen as the benchmark.
- Daily-start timing study (phase and start modes) with a tranche blend.
- `Lab` SDK, CLIs, and a pytest suite that runs through the SDK.

- Real data: bundled S&P 500 prices + index and US CPI/T-bill; Bank of
  Canada and SEC FSDS adapters tested on fixtures (`realdata/ACQUIRE.md`).
- Construction: cap / inverse-vol weights, position and sector caps, hold
  rules and bands, union and blended portfolios.
- Statistics: block-bootstrap CI, Newey–West t-stat, deflated Sharpe; grid /
  random / optional Bayesian sweeps with walk-forward scoring.
- Extended metrics: real, home-currency, net-of-financing (prime + spread,
  leverage), excess over time-varying rf, geometric decomposition.
- Exit-signal screen with causal signals, hysteresis, its own decision
  cadence (daily default), in-backtest overlay, and walk-forward
  calibration with stability and constraints.
- Exit policy (park in cash / rate / series, re-entry rules, rescreen on
  re-entry), per-portfolio toggle with side-by-side backtests, twelve more
  exit inputs.
- REST (FastAPI) and MCP servers over the `Lab` SDK.
- Phase 0: bugs B1–B7 fixed, `ExitOverlay` alias removed, golden tests,
  bounded caches, async jobs with progress/cancel, optional SQLite run
  store with trial counting.
- Phase 0.5: datasets with data cards and universes; one signal-window
  convention; multi-stage JSON screen specs; operations registry (one
  module per analysis, REST/MCP generated); self-serializing types; metric
  registry; simulation as pluggable stages with a vectorized `simulate_paths`.
- Monte Carlo: block / stationary / regime / iid bootstraps; `montecarlo`
  operation with per-path re-ranking for price-rank screens (incl. hold
  rules) and fixed picks otherwise; S&P 500 and all-portfolio studies.
- Textual TUI (terminal or browser) with scenario versions, pinned passive
  index, a Plotly chart panel themed from the TUI, one shared session in
  `--serve` mode, a Data tab, and MCP `tui_*` control tools.
- Free-dataset downloader (`make data-free`, `screenlab-data`, `fetch_data`
  operation): SEC, Bank of Canada, FRED, Stooq, StatCan, Shiller, Tiingo.
- Benchmark kind rule: price-index vs total-return comparisons are refused
  unless a dividend assumption or a TR series is supplied; the bundled
  S&P 500 defaults to an approximate TR benchmark.
- Packaging (`pyproject.toml`, console scripts, `Makefile`), verified
  getting-started guide, docs map, LinkML schema of the run store,
  notebooks.

The phases below follow the review in `CRITIQUE.md` §6 and the owner's
later suggestions. Data correctness comes before features. "Blocker" marks
what has to be done before any result is used for a real decision.

## Phase 1: Real data **[blocker for any real decision]**
1. **First real run of the downloader** (owner's machine; every host is
   blocked in the cloud environment): `make data-free SEC_USER_AGENT="Name
   email"` and `make data-tr`, then fix whatever the manifest reports.
   Expected first-contact failures: Stooq CAPTCHA key, the StatCan URL, the
   SEC zip layout, the Shiller file link, Tiingo's Canadian coverage.
2. **Total-return benchmarks from real data:** build `SP500_TR` from Shiller,
   SPY/VOO adjusted closes from Tiingo, XIC/XIU for Canada; register them
   with their MERs. Extend the kind check to `compare=` rows and named
   `exit_eval` / `calibrate_exit` targets (the two known gaps).
3. **Reconcile one published Globe portfolio:** same screen, real TSX data,
   compare picks and the 12-month return with the column within a stated
   tolerance. Until this passes, the Globe screens are faithful to their
   descriptions, not verified against their results.
4. **Universe history:** index membership and delisting proceeds instead
   of the flat `delist_return`; "common stocks only" filtering; Canadian
   point-in-time fundamentals (EODHD cheapest; TMX / Capital IQ / Refinitiv
   proper; Norgate for survivorship-free prices).
5. **Dataset manifest in the store** with the survivorship score, shown on
   every result (card exists; persistence of the card is done; the score's
   threshold is still a heuristic).

*Done when:* a US backtest runs on a universe that includes delisted names
against a real TR benchmark with no warnings; one Globe portfolio
reconciles; the data card shows a survivorship score near zero.

## Phase 1.5: Make the existing studies honest
- **Realistic costs:** re-run the all-portfolio study with the cap-tiered
  cost model (50–100 bps for small caps) to see which monthly Globe
  portfolios survive real trading.
- **Timing luck on the Globe screens:** run the rebalance-timing study on
  the eight Globe portfolios; report how much of each return is the start
  month.
- **Check one suspicious number:** Basic Buyback monthly trailing the index
  in 92% of futures; inspect its turnover before believing it (likely a
  cost artefact of buyback yield reshuffling the whole portfolio).
- **Stop adding screens, signals and synthetic-market studies** until real
  data is in: 20 signals and 25 portfolios are already more than the data
  can rank, and the synthetic market has validated the machinery.

## Phase 2: Guardrails against fooling yourself
- **Trial counter in the TUI:** show `trials_in_family` and the deflated
  Sharpe on every Compare row; the TUI makes 50 variants an afternoon's
  work.
- **Frozen scenarios:** once a scenario is marked frozen, later Apply
  versions are flagged as out-of-sample candidates and counted by the
  deflated Sharpe; distinguishes exploring from testing a conclusion.
- **Pre-registration file:** `research/PREREGISTERED.md` listing the three
  or four hypotheses that matter (e.g. "Frugal Dividend annual beats XIC
  after costs over 15 years"), each with its metric and threshold, written
  before the first real-data run; the trial counter references it.
- **Benchmark kind everywhere:** extend the refusal to compare rows and
  named targets (from Phase 1 item 2).

## Phase 3: Taxes (pulled forward)
The owner has a separate Canadian dividend/salary tax module to contribute.
- **Integrate it as a `TaxModel` stage** in the simulation pipeline (DESIGN
  §J5 already has pluggable stages): account types taxable / TFSA / RRSP;
  eligible-dividend gross-up and credit from the owner's module; extend it
  to capital gains (inclusion rate, realized on each rebalance and on exit
  switches, so the exit policy's trading cost includes tax); US withholding
  on US dividends in each account type; provincial rates as parameters.
- **Report after-tax metrics** beside pre-tax ones (after-tax CAGR, tax
  drag per year), with the passive index taxed the same way on the same
  account type.
- **Expect rankings to move:** dividend screens vs momentum differ
  materially in a taxable account; the exit policy becomes tax-expensive.

*Scaffolded (docs/TAXES.md):* `TaxModel` interface (`tax_on_dividends`,
`tax_on_capital_gain`, `tax_on_foreign_dividends`, `describe`; `register_tax_model`
to serialize a model), `Config.tax = TaxConfig(model, account, us_withholding, ...)`,
`pipeline.TaxStage` (average-cost gains on each rebalance and exit switch, loss
carry-forward, dividend income from the snapshot yield, tax paid from the
portfolio at each year end), `Result.tax` / `after_tax_curve`, report columns
`AfterTaxCAGR`, `TaxDrag/yr`, `AfterTaxExcess/yr` with the passive index taxed the
same way, taxable / TFSA / RRSP with US withholding by account. No tax by default
(golden tests unchanged). Monte Carlo with a tax config raises NotImplementedError.

*Done:* `CanadianTax` (taxes.py, over `screenlab/tax_ca`): AB and BC, 2025 tables, joint yearly assessment (salary ->
dividends -> foreign -> gains), eligible / non-eligible split, approximate foreign tax credit, taxable / TFSA / RRSP,
loss carry-forward, `TAX_MODELS` by name; the owner's AB numbers are reproduced for a dividend-only portfolio and for
dividends plus a gain (tests/test_taxes.py). `PlaceholderCanadianTax` stays for tests only.

*Done (P1):* [x] RRSP is tax-deferred (`TaxConfig.rrsp_withdrawal_rate` / `rrsp_withdrawal_income`: the balance is
taxed as ordinary income on the last bar; after-tax curve, `AfterTaxCAGR` and the equally-taxed index follow; the report
states the convention, or warns "RRSP withdrawals untaxed in this run"); [x] cash and parked-cash interest taxed as ordinary
income in taxable accounts (`park_income_kind`); [x] rebalance trading costs in the ACB (buy side) and out of the proceeds
(sell side); [x] return of capital (`roc` fundamentals column) reduces the ACB, excess over the ACB is a gain.

*Done (P3):* [x] per-security metadata (`Dataset.security_meta`, `securities.csv`, data card line); [x] withholding by
country and wrapper (`TaxConfig.withholding`, Canadian ETFs on US stocks lose it in every account, RRSP exempt for US only,
`us_withholding` deprecated); [x] gains, ACB and dividends on a CAD basis from a `MacroSeries` FX series, with a warning
without one (docs/TAXES.md "Foreign holdings").

*Done (P2):* [x] tax-loss harvesting (`harvesting.TaxLossHarvest`, `Config.tax.harvest`: year-end and/or rebalance, threshold and
min loss, overrides a hold-rule keep) with the CRA superficial-loss rule (30 days before and after, denial in proportion to
the shares bought back, denied loss added to the ACB; `replacement` "cash" / "stay_out" / "similar" via `Screen.ranked`);
[x] `Result.harvest`, report columns `Harvested/yr` and `TaxDeferred/yr`; [x] 3-year loss carry-back in `taxes.assess`
(most recent year first, refund as a credit at the loss year's end, forward carry kept); tests/test_harvesting.py;
docs/TAXES.md "Loss harvesting".
*Done (Q3):* [x] harvesting is explicit (`TaxLossHarvest.describe()`); [x] second pick (`replacement="second_pick"`/"similar"; at a
rebalance a re-picked name inside its window is replaced by the next-ranked unblocked name, `on_repick` "avoid" default /
"allow_denial"); [x] one blocklist (`HarvestRun.blocked_until`, `blocked(date)`) honoured by the rebalance, the second pick and
the exit policy's re-entry; [x] harvesting with an exit policy (exit-switch losses recorded with `source` "exit"); `Result.harvest`
columns `replacement_name`, `blocked_until`.

*Done (Q2):* [x] account comparison (`account_compare`: the same portfolio and the passive index in taxable / TFSA / RRSP,
after-tax CAGR, tax drag, after-tax excess over the equally taxed index, withholding lost / credited / exempt, tax by kind,
RRSP deferral, rank, foreign treatment matrix; REST, MCP, jobs, TUI Accounts tab, `notebooks/accounts_and_foreign.ipynb`;
docs/TAXES.md "Comparing accounts"; tests/test_account_compare.py).

*Remains:* more provinces and tax years (years without a table reuse the nearest one with a warning), pension income and
RRSP contribution room, AMT, a refund of negative net tax, wiring the model choice into REST / TUI. *Open
approximations:* dividend dates and amounts (yield held between rebalances), RRSP withdrawal at one flat marginal rate (no contribution refund),
index yield when the series is total return without one, no tax on the final unrealized gain unless `liquidate_at_end`.

*Done when:* a backtest can be run as taxable / TFSA / RRSP and the after-tax excess return over the equally-taxed index is
reported, with the owner's module's numbers reproduced for a dividend-only portfolio. (Met for AB 2025.)

## Phase 4: Service and UI hardening (only if the tool is shared)
Decide first whether screenlab stays a personal workbench or becomes
something other investors use. If personal, skip most of this.
- Per-session labs over read-only base labs; auth token; thread-safety of
  the shared `--serve` session under two people applying changes at once.
- Python 3.11 / 3.12 test run (claimed 3.10+, tested on 3.13 only).
- Faster TUI start: lazy-import Monte Carlo and server modules.
- Exit as a single "drawdown control" toggle in the UI (the fixed trend
  rule from `research/CALIBRATION_SP500.md`), calibration stays an SDK
  diagnostic.

## Phase 5: Later
- Credit-spread and yield-curve exit inputs once FRED data is loaded;
  intraday exit cadence once intraday bars exist.
- Extended metrics (real, FX, financing) on Bank of Canada data.
- HTML report export; scheduled picks and paper portfolios.
- Delete the old `rothery-screener` branch on GitHub (unchanged duplicate).
