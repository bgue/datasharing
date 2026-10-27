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

The phases below follow the review in `CRITIQUE.md` §6, which has the
reasoning and measured numbers. Data correctness comes before features, and
items marked **[UI blocker]** must be done before the interactive UI starts.

## Phase 0: Freeze, fix, and make runs asynchronous and storable **[UI blocker]**
- **Bugs:** fix B1–B6 from `CRITIQUE.md` §3 (duplicate portfolio names
  collapse, `with_and_without` ignored via the alias, unknown screen gives
  a JSON error, `benchmark="top60"` fails in the SDK, server error leaks).
  Update `research/CALIBRATION_SP500.md` now that curve-based signals
  calibrate (B7).
- **Simplify:** remove the `ExitOverlay` / `exit_overlay` alias so there is
  one way to attach an exit, and route all server errors through
  `ServerError`.
- **Guard:** add golden-value regression tests for one full synthetic
  backtest and one bundled-data backtest. Put an LRU bound on
  `Market._views`, and move `research/results/*.csv` out of git.
- **Asynchronous runs** (DESIGN.md §I2): a `JobManager` on a process pool,
  with progress, cancel, and dedup by run key. `lab.submit(...)` in the
  SDK, `/jobs` endpoints in REST, and run tools in MCP. Synchronous calls
  stay as they are.
- **Optional persistence** (DESIGN.md §I1, §I3):
  - Every run is a serializable `RunSpec` with a stable `run_key` (inputs +
    dataset fingerprint + code version).
  - An optional SQLite `RunStore` keeps inputs, output metrics and
    optionally curves. It is off in the SDK by default and on in the
    server.
  - The store offers `find` / `compare` / `rerun`, and keeps a trial
    counter that feeds the deflated Sharpe.

*Done when:* the bug repros no longer reproduce; a `Portfolio` named like a
screen produces two rows; a REST client can start a daily timing study,
poll progress, disconnect and fetch the result later, with an identical
second request returning from the store; every result carries its trial
count; and the suite passes with persistence off.

## Phase 0.5: Extension refactors (prerequisites for Monte Carlo)
See DESIGN.md §J. Run in waves; golden tests must stay identical.
- **Wave 1:** datasets and universes (J1); one signal-window convention (J2).
- **Wave 2:** multi-stage screen specs with the Globe portfolios as specs
  (J3); operations registry, self-serializing types and a metric registry
  (J4).
- **Wave 3:** simulation as pluggable stages that can run over many price
  paths (J5).

*Done when:* adding an operation, a data source, a metric or a
multi-stage screen each touches one module, and the golden tests are
unchanged.

## Phase 1: Data correctness **[UI blocker]**
- **Network:** open www.sec.gov, www.bankofcanada.ca, stooq.com (or
  eodhd.com) and fred.stlouisfed.org, and run the adapters for real.
- **Benchmark kind:** `IndexBenchmark(kind="price"|"total_return")`, with
  a warning (or refusal) when a price index is compared against
  total-return portfolios. Load TR proxies: SPY adjusted close, and
  XIC/XIU for Canada.
- **Dataset manifest and survivorship score:** computed at load and stored
  in the run store's `datasets` table. Shown on every result.
- **Universe history:** index membership and delisting proceeds. SEC FSDS
  as the first real point-in-time fundamentals, then a Canadian source
  (EODHD cheapest; TMX / Capital IQ / Refinitiv proper).
- **Reconciliation:** reproduce one published Globe screen portfolio
  within a stated tolerance.

*Done when:* bundled-data results carry red survivorship and price-index
warnings; a US backtest runs on a universe that includes delisted names
against a TR benchmark; and one Globe portfolio reconciles.

## Phase 2: Service hardening **[UI blocker]**
- **Typed models:** request/response models (pydantic) and a real OpenAPI
  document, with MCP tools generated from the same models.
- **Session state:** per-session labs over read-only base labs, so a
  screen defined by one client no longer changes it for everyone.
- **Auth:** a minimal token.
- **Concurrency:** thread-safe caches.

*Done when:* a client generated from the OpenAPI document can run a
backtest job and retrieve it later, and the document has no untyped
bodies.

## Phase 3: UI v1 (scenarios and comparisons)
- **Scenarios:** a form for screen, rebalance period, costs, dates and
  dataset, then compare up to about 6 scenarios side by side.
- **Pinned benchmark:** the passive index is always the first row and
  curve.
- **Panels:** a timing-luck panel, current picks and the Graham funnel.
- **Guardrails:** a data card on every result, a trial counter, and
  confidence intervals on excess return.
- **Saved runs:** come from the run store.
- **Left out:** calibration, sweeps, leverage and financing metrics, and
  the extra exit inputs.

*Done when:* a user can build two scenarios, compare them against the
index, see the data warnings, reopen yesterday's runs, and export a report.

## Phase 4: Exit as drawdown control
- **One toggle:** the fixed trend rule from `research/CALIBRATION_SP500.md`,
  labelled as drawdown control, not a return enhancer.
- **Rows:** "with and without" rows in the comparison.
- **Cash:** earns a loaded T-bill series.
- **Calibration:** stays an SDK diagnostic.

*Done when:* the toggle adds a `+exit` row with time out and switches per
year, and the UI states that it lowers drawdown and does not raise return.

## Phase 4.5: Monte Carlo
See DESIGN.md §K. Scenario generators (block / stationary / regime
bootstrap, factor simulation) built as datasets, a `montecarlo` operation
running as a job over many paths, outcome distributions for every portfolio
and the passive index on the same paths, with the data card's survivorship
warning carried through.

*Done when:* a user can ask "how often does this portfolio trail the index
over 10 years" and get a distribution with the passive index on identical
paths, stored and comparable.

## Phase 5: Later
- **Taxes:** taxable, TFSA and RRSP accounts.
- **Credit and yield-curve exit inputs:** once FRED is reachable.
- **Intraday exit cadence:** once intraday bars exist.
- **Charts and an HTML report.**
- **Advanced mode:** sweeps and significance tests, with the trial counter
  wired in.
- **Extended metrics:** on Bank of Canada data.
- **Tracking:** scheduled picks and paper portfolios.
