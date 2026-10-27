# Review of `screenlab` before the UI

*Reviewer's note, October 2026. Everything below was checked against the code at commit `448253b` and by running it: the full test suite (208 tests, 54 s, all pass), the bundled S&P 500 lab, the synthetic market, the REST app, and a small walk-forward sweep. Numbers quoted are from those runs. No package code was changed.*

## Verdict

The core of this tool is good and worth keeping: a genuinely point-in-time data model, an exact and well-tested cost model, a hard rule that every result is measured against a passive index, strictly causal exit signals with a causality test, cheap walk-forward evaluation, and a research write-up (`research/CALIBRATION_SP500.md`) that reaches the honest conclusion that calibrated exits do not pay. But the tool cannot support a decision about a real portfolio today, and the reason is data, not code: the only real dataset is 20 stocks that survived to 2022 (holding them equal-weight returned 17.5%/yr against 7.4% for the index, so survivorship alone is worth about ten points a year), measured against a price-only S&P 500 that understates the benchmark by roughly two points a year. The statistical machinery cannot see either problem: on that data it reports momentum beating the index by 14 points a year with t = 5.1 and a deflated Sharpe of 1.00. Meanwhile the research surface has grown to roughly five times what the data can support (14 config fields, 20 exit signals with 41 parameters, 11 exit-policy fields, 8 exit-screen fields, 15 Graham thresholds), with several overlapping ways to say the same thing and a handful of silent-failure bugs. My recommendation: before any UI, (1) fix the small bugs and remove the aliases, (2) make data correctness a first-class, visible property of every result (total-return benchmark, survivorship and point-in-time flags on the data itself), (3) freeze exit calibration as a diagnostic and ship at most one fixed exit rule, and (4) build a UI v1 that exposes screens, costs, rebalance schedule and side-by-side comparison against the passive index, and nothing else. That is a smaller tool than the one that exists, and a far more trustworthy one.

## 1. Fitness for purpose

**Can results today support a decision about a real portfolio? No.** Three things stand between the tool and that use:

| Needed | State today | Where |
|---|---|---|
| A universe that includes the companies that failed, were delisted or acquired | Only 20 survivors (`realdata/bundled.py`); the synthetic market has delistings but is synthetic | `realdata/`, `data.py` |
| A total-return benchmark | The bundled `SP500` is a price index while the 20 stocks are adjusted close (dividends in). `IndexBenchmark` has no notion of price vs total return | `benchmarks.py`, `realdata/bundled.py` |
| Point-in-time fundamentals for the market the owner invests in (TSX) | None loaded. The SEC adapter (`realdata/sec_fsds.py`) is correctly built on filing dates but is tested only on a two-company fixture; Canadian fundamentals need a paid source (`realdata/ACQUIRE.md`) | `realdata/` |

What would have to be true first: a Canadian (or at least a complete US) dataset with delisted names and index membership history; a total-return series (or a dividend-yield proxy) for every benchmark; fundamentals dated by when they became public; and at least one reconciliation of a published Globe screen portfolio against the tool's picks and returns (ROADMAP.md already lists this; it is the acceptance test for the whole data layer). Until then every number the tool produces on real data should be read as "the pipeline runs," which is what `ROADMAP.md` itself says.

**What is already decision-grade:** the *mechanics*. Cost, MER, commission, delisting and benchmark arithmetic are pinned by exact tests on hand-built markets (`tests/test_api.py`, `tests/test_passive_benchmark.py`); the two-pass exit simulation reproduces the standalone overlay to the last digit (the research note cross-checks CAGR, Sharpe, MaxDD and switch count); and the rebalance-timing study (`timing.py`) is a real and underused insight: it shows how much of any annual-rebalance result is luck of the start date.

## 2. Methodological risks, ranked by how much they could change a conclusion

| # | Risk | What I measured | Effect on conclusions | Fix |
|---|---|---|---|---|
| 1 | **Survivorship** in the only real data | Equal-weight buy-and-hold of the 20 names, 1990–2022: **17.5%/yr** vs **7.4%** for the index. `momentum_12_1` monthly: 22.8%, "Excess/yr" 15.0%; `lab.significance` gives CI [10%, 18%], t = 5.1, DSR 1.00 | Any "screen X beats the index" statement on this data is dominated by the universe, not the screen. The research note says so; the API does not | Load a universe with delistings; compute and display a survivorship score per dataset (share of names alive at the end, share of index membership covered) |
| 2 | **Price-index benchmark vs total-return portfolios** | `SP500` price CAGR 7.39% over the full span, 7.8% over the backtest window; S&P 500 dividends averaged about 2%/yr over the period | Every `Excess/yr`, `BeatBenchYrs`, bootstrap CI and t-stat against `SP500` is inflated by roughly 2 points a year; the exit study's "passive S&P 500" rows are too low. Nothing in `IndexBenchmark` records which kind of series it is | Add `kind="price" / "total_return"` to the index contract; warn (or refuse) when a total-return portfolio is compared to a price index; allow a dividend-yield series to build an approximate TR |
| 3 | **Point-in-time and look-ahead** | The data model is right in principle (`Market._snapshot` takes the latest row with `date <= asof`). But: the synthetic market dates fundamentals on the same day as the prices they derive from (no reporting lag), so synthetic Graham/Piotroski results are mildly optimistic; `Market.view` marks price with the total-return index (dividends inflate "price," so P/E and P/B are slightly too high, acknowledged in a comment); `passive_index` scales the *latest* market cap back along each stock's TRI, exact only if share counts never change; CSV users are asked to apply their own lag (`README` "Data contract"). The exit signals pass a truncation test (0 mismatches on three dates) | Small today; becomes material the moment real fundamentals arrive (restatements, late filers) | Keep `date = filed` as the contract (as the SEC adapter does); add a separate price-only series to `Market`; make the lag a loader parameter with a visible default |
| 4 | **Overfitting and multiple testing** | Knob count: 14 `Config` fields, 8 `ExitScreen`, 11 `ExitPolicy`, 15 `GrahamTests`, 20 signals / 41 parameters, screen params on top. A 9-point walk-forward sweep of momentum on the bundled data: OOS/IS = 0.68, one point won all 5 windows, and `summarize_trials` reports DSR = 1.00 and Haircut 0.07 for every row because survivorship swamps the cross-trial spread. `Lab.sweep` and the server's `sweep` do **not** attach DSR; the user must remember `summarize_trials`. Nothing counts trials across calls or sessions | The deflation machinery is sound (`stats.py` is clean and hand-checked) but it only deflates for the trials it is told about, and in the UI nobody will tell it. `docs/BAYESIAN_SWEEPS.md` gives exactly the right advice; the API does not enforce it | Make trial counting a property of the session (every backtest increments it), show it on every result, and attach DSR to every sweep by default |
| 5 | **The calibration findings** | `CALIBRATION_SP500.md`: calibrated exits lowered CAGR in 32/32 portfolio tests (about -3 points/yr, 19/48 significantly), settings won only ~27% of windows, OOS/IS 0.60; a fixed trend rule cut max drawdown 25–55% at about -1.5 points/yr before interest on cash, statistically indistinguishable from no exit. The note correctly flags that the DSR reported by `calibrate_exit` is the Sharpe of the *stocks*, not of the exit | This is the most important result in the repository, and it argues against exposing calibration to a user at all | Make "no exit" the default (already the case in `Config`), ship the fixed trend rule as an optional *drawdown-control* setting with that label, and keep `calibrate_exit` as a diagnostic behind a flag |
| 6 | **Cost realism** | Defaults: 25 bps one-way per security, 5 bps per unit of exposure switched, cash at 0%, benchmark MER 0, no taxes. `cap_tiered` exists but its tiers are placeholders. The exit study's "parked money earns 2.5%" row moves CAGR by +0.7 points, so the cash assumption alone is as large as the effect being measured | For large-cap TSX screens the defaults are plausible; for net-nets and small caps they are optimistic; for a taxable Canadian account an exit that switches twice a year has a tax cost the tool cannot see | Make cash earn a loaded T-bill series by default; add a taxes line to the roadmap (already there) before anyone runs an exit strategy on a taxable account |
| 7 | **What the synthetic market validates** | `data.synthetic_market` builds in a value premium (0.0015 × cheap per month), a quality premium (0.001 × quality), and 30% of a persistent momentum factor; returns are Gaussian with no fat tails or crashes. On it, `magic_formula` shows Excess/yr 4.0%, CI [0.2%, 7.8%], DSR 0.73; `graham_defensive` -3.6% | It validates arithmetic, causality, plumbing, and that the statistics behave sensibly. It cannot validate any economic claim, any exit signal's value (there are no regimes to detect), or survivorship realism | Keep it for tests only; never show synthetic results in the UI except behind an explicit "demo data" banner |

## 3. Engineering

### What is solid
- **Layering.** `data.py` (point-in-time `Market`) → `screens.py`/`spec.py` → `backtest.py` → `metrics.py` → `api.py` (`Lab`) is clean, and `Lab` really is the only integrator. The declarative `ScreenSpec` (JSON in, `Screen` out) is exactly the object a UI needs, and it round-trips (`tests/test_api.py::test_spec_round_trip_json_dict_builder`).
- **The passive-benchmark rule** is enforced in one place (`Lab.passive`, `Lab._extras`) and tested from four directions (`tests/test_passive_benchmark.py`).
- **Walk-forward by slicing one curve** (`sweep._Evaluator`, `calibration.calibrate_exit`) is both correct (picks use only past data) and cheap: 9 sweep points with 5 windows ran in 19 s.
- **Signals are causal by construction** (`rolling_percentile` trailing window including the day; `_MonthlySampled` pads month-ends forward) and the causality test (`tests/test_exit_signals.py::test_signals_are_causal`) perturbs future prices and checks day-t scores. `rolling_percentile` is a Python loop but takes 0.13 s on 6,300 days; a three-signal `exit_eval` takes 0.5 s.
- **The research script** (`research/calibrate_sp500.py`) is reproducible (fixed seeds, parallel, writes every table), and the write-up is written for the owner, not for the authors.

### Coherence after rapid growth
The exit feature was added in three rounds (overlay → policy → inputs) and each round kept the previous API alive. The result is **five ways to specify an exit** (`Config.exit`, `Config.exit_overlay`, `Lab.backtest(exit=)`, `Portfolio(exit=)`, `lab.config.exit`), **two overlay engines** (`exit_signals.overlay` for a plain flag, `exit_policy.policy_overlay` for a policy, plus the in-`simulate` path), **two evaluation entry points** (`evaluate` / `evaluate_state`), and **three ways for a signal to say what it needs** (a `uses_curve` attribute, a `needs` tuple in `exit_inputs.py`, or signature introspection via `inspect` in `exit_signals.uses_curve`). `_exit_of` in `backtest.py` converts the alias at run time; `calibration.py` builds both an `ExitOverlay` and an `ExitPolicy` for every result. None of this is wrong, but each layer is a place for the next bug, and the UI would have to pick one path anyway.

Other conventions worth unifying: `lab.benchmark` accepts an `IndexBenchmark`, a `ScreenBenchmark` (silently demoted to a compare row) or, after construction, a bare string; `Lab.screen(name)` falls through to `define_screen(name)` for any unknown string, which is why an unknown screen produces a JSON error (below); and `rebalance_months`-style config overrides travel as `**kw` through four layers with no validation until pandas complains.

### Confirmed bugs (minimal repros, all on `Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)`)

| # | Symptom | Repro | Severity |
|---|---|---|---|
| B1 | **Same-named portfolios silently collapse to one row**; the later one wins | `lab.backtest([Portfolio("dogs", "dogs", rebalance_months=6), "dogs"]).table.index` → `['SYN_CAPWT', 'dogs']` (also `["dogs", "dogs"]`) | High for a comparison UI: a scenario can vanish from a report with no error |
| B2 | **`with_and_without=True` is ignored** when the exit comes from `lab.config.exit_overlay` | `Lab(mkt, config=Config(exit_overlay=ExitOverlay(es))).backtest(["dogs"], with_and_without=True).table.index` → one `dogs` row, with `TimeOut` 0.364 | Medium: the "+exit" comparison the owner wants is missing, silently |
| B3 | **Both `exit` and `exit_overlay` set**: the overlay is ignored without a warning | `Config(exit=ExitPolicy(es, 0.5), exit_overlay=ExitOverlay(es, 0.0))` → curve equals the policy run | Low, but it is the alias problem in miniature |
| B4 | **Unknown screen name raises `JSONDecodeError`** (SDK) and REST returns `400 "JSONDecodeError: Expecting value"` instead of 404 | `lab.picks("no_such_screen")`; `GET /labs/syn/picks/zzz` | Medium: every UI error path goes through this |
| B5 | **`benchmark="top60"` works in the CLI and the server but not the SDK** | `lab.backtest(["dogs"], benchmark="top60")` → `KeyError: no index or ticker named 'top60'` | Low |
| B6 | **REST leaks internal messages**: a `config` containing `exit` collides with the `exit=` keyword in `LabRegistry.backtest` | `POST /labs/syn/backtest {"screens":["dogs"],"config":{"exit":"x"}}` → `400 "bad request body: LabRegistry._call() got multiple values for keyword argument..."`; `{"config":{"rebalance_months":"twelve"}}` → `"invalid literal for int() with base 10: ''"` | Low, but typed request models would remove the whole class |
| B7 | **Stale documentation**: `CALIBRATION_SP500.md` ("Software issue found, not fixed") says `calibrate_exit` drops curve-based signals. That is fixed at HEAD: `ExitScreen([PortfolioStop(0.15)]).components(mkt, curve=c).shape` → `(8313, 1)` and the calibration table now shows `FracFlagged` 0.22 and 100 switches. The note (and its fixed-stop reference table) should be updated | Doc only |

### Hazards that are not bugs yet
- **Shared mutable registry.** `LabRegistry.define_screen` writes into the one cached `Lab`; `reg.define_screen({"name": "dogs", ...})` replaces the built-in `dogs` for every client until the process restarts (verified). Any per-user UI needs per-session labs or immutable base labs plus session overlays.
- **Unbounded caches.** `Market._views` holds one 119 KB DataFrame per rebalance date forever: 24 views after one annual backtest, 277 after a monthly one, 537 after a stride-21 timing study. A daily-stride timing study on the default synthetic market (252 offsets × 25 years) would cache on the order of 6,000 views, roughly 700 MB, in a long-running server. `ExitPolicy._cache` and `ExitOverlay._cache` are keyed on `id(mkt)`. None are bounded or evictable.
- **Concurrency.** Six threads running backtests on one `Lab` through the registry gave identical results and no errors in my probe, but the caches are plain dicts touched without the lock (`_lock` only guards `get` and `define_screen`). FastAPI runs sync endpoints in a thread pool, so this will be exercised in production.
- **Everything is synchronous.** Measured on this machine: 8 screens annual on the default synthetic market 6.2 s; monthly 66 s; `timing("dogs", stride=21)` 15.6 s (so a daily sweep is about 5 minutes per screen, not the 70 s the README quotes); 2 screens monthly on the bundled data 11.8 s; `significance` 7.3 s; the full calibration study 10 minutes on 4 cores. The REST layer blocks for all of it.
- **Repository hygiene.** Two research CSVs of 4.0 MB and 4.8 MB are tracked in git; `.gitignore` is two lines. Fine for now, painful in a year.

### Test suite: what it proves and what it does not
208 tests in 54 s is a good suite for a research sketch, and its strengths are real: exact arithmetic for costs, MER, commissions, delisting, benchmark MER/cost, overlay returns, clipping, hold rules, Newey–West and PSR by hand; causality tests for every signal; both daily and monthly frequencies; CLIs and both servers exercised end to end. What it does not prove:
- **No golden regression on a full backtest.** The DESIGN note says "existing tests pin [the curves]"; in fact no test pins the end value or a hash of a complete synthetic backtest, so a subtle change to `simulate` that preserves the hand-built cases would pass.
- **No data-quality guards are tested because none exist**: nothing asserts that comparing a total-return portfolio to a price index is flagged, or that a survivor-only universe is flagged.
- **No test for B1/B2/B4**: duplicate names, config-level alias with `with_and_without`, or the error type for unknown names.
- **Real-data tests check shapes and ordering only** (`tests/test_realdata.py`: picks equal `nlargest`, `Vol < 0.30`), never a value anyone could reconcile with an outside source.
- **Behavioural tests on synthetic data test the synthetic generator as much as the code**: e.g. "the calibrated screen flags both crashes" on a hand-built index is a mechanics test, not evidence a signal works.

## 4. Scope: cut, freeze, simplify before a UI

Is the tool trying to do too much? Yes, in a specific sense: the research surface outruns the evidence base. On 20 survivors and a price index, the difference between a 10-month and a 12-month trend window is not measurable, yet the tool offers it as a grid dimension, and the server offers it to any client.

| Action | Item | Why |
|---|---|---|
| **Cut** | `ExitOverlay` / `Config.exit_overlay` alias; `backtest._exit_of` | One exit object (`ExitPolicy`). Fixes B3 by construction |
| Cut | `bayes_search` (optuna, not installed) | `docs/BAYESIAN_SWEEPS.md` already argues against it; keep the doc as the rationale |
| Cut | `rescreen_on_reentry`, `reentry_steps`, `reentry_delay_days` from the public surface | The study found re-entry mode stable (82%) and the ramp/delay options did not earn their place; each is a two-pass code path |
| Cut (hide) | 12 extra exit inputs → keep `TrendBreak`, `VolRegime`, `Breadth`, `DrawdownFromPeak` | "More signals did not help" (research note). Macro signals (`CreditSpread`, `YieldCurve`, `RateShock`) return NaN without data the tool cannot load yet |
| **Freeze** | `calibrate_exit`, `sweep`, `significance` as SDK/diagnostic tools | Keep and maintain; do not put them behind buttons in v1 |
| Freeze | `extended` metrics (real, FX, financing, leverage) | Correct and closed-form tested, but they need macro data the owner does not have and the leverage curve is a foot-gun in a UI |
| Freeze | MCP server | It mirrors REST; harden REST first, then re-derive MCP from the same typed models |
| **Simplify** | Error model: `ServerError` with 404 for unknown names everywhere (`Lab.screen` should not fall through to JSON parsing) | B4, B6 |
| Simplify | One signal-requirements convention (`needs` tuple, declared, no `inspect`) | Three conventions today |
| Simplify | Bound and key the caches explicitly (LRU on `Market._views`, policy caches keyed on a data-snapshot id rather than `id(mkt)`) | Server longevity |

Keep without change: the data model, the cost model, screens and specs, the backtester, the passive-benchmark rule, the timing study, `stats.py`, the walk-forward slicer, the fixed trend exit as a single optional rule.

## 5. UI readiness

**What the UI should expose in v1**
- A *scenario* = one screen (built-in or `ScreenSpec` from a form), a rebalance period, a cost profile (bps, commission, MER, cash rate), a start/end, and the dataset.
- Side-by-side comparison of up to ~6 scenarios, the passive index always present as the first row and the first curve, with `Excess/yr`, `BeatBenchYrs`, `MaxDD`, `Worst12m`, `Turnover`, `Costs/yr`.
- The timing distribution (min / 5% / median / 95% / max of CAGR and excess across start dates) and the tranche blend, as the standard "how much is luck" panel.
- Current picks with the key ratios, and the Graham funnel.

**What it should not expose in v1**: calibration, sweeps and Bayesian search, extended/leverage metrics, the 12 extra exit inputs, policy re-entry details, synthetic data (except behind a "demo" banner), and anything from `lab.config` that the user did not set on the scenario.

**Guardrails so a user cannot fool themselves**
1. The passive index row and curve cannot be removed or hidden; the report title names the benchmark and its kind (price / total return).
2. A **data card** on every result: dataset name and snapshot id, date span, number of names, share of names alive at the end (survivorship score), whether fundamentals are point-in-time, whether the benchmark is total return. Red banner when the benchmark is price-only or the universe is survivor-only, in words ("this universe contains only companies that still exist; results are biased upward").
3. **In-sample vs out-of-sample** shown as two numbers whenever a parameter was chosen by looking at results; a plain backtest is labelled "in-sample (this is a fit, not a forecast)".
4. A **trial counter** per session, shown next to every result ("you have looked at 23 variants this session"), feeding `summarize_trials` so DSR and the Bonferroni haircut are computed on the right count.
5. Confidence intervals, not point estimates, for `Excess/yr` (the bootstrap already exists); hide Sharpe decimals beyond two.
6. Exit strategies, if present at all, appear as a single toggle "drawdown control (fixed trend rule)" with the research note's own words: *reduces the worst falls, does not raise return, costs about 1.5 points a year before interest on cash*.
7. Costs are never zero by default and the cost profile is printed on the report.

**What the server/API needs before it can back a UI** (all blockers)
- **Typed request and response models** (pydantic) for every endpoint. Today the OpenAPI schema for `POST /labs/{lab}/backtest` is `{"type": "object", "additionalProperties": true}` and the response schema is `{}`; a UI cannot be generated or validated against that. `ScreenSpec`, `Config`, `ExitPolicy` are already dataclasses and map directly.
- **Async jobs** with ids, progress and cancellation for backtests over ~2 s, timing studies, sweeps and significance (a monthly backtest of 8 screens is 66 s; a daily timing study is minutes).
- **Result persistence** keyed by (screen spec hash, config hash, dataset snapshot id, code version), so comparisons are reproducible and the trial counter has something to count.
- **Per-session state**: a read-only base lab per dataset plus a per-session overlay of defined screens; no client-visible mutation of shared labs.
- **Dataset manifests**: the data card above has to come from somewhere; each registered lab needs provenance (source, licence, download date, kind of index, survivorship score) stored alongside it.
- Bounded caches and a memory budget per worker.

## 6. Roadmap

Data correctness comes before features; the UI phases cannot start until the blockers marked **[UI blocker]** are done.

### Phase 0: Freeze and fix (about a week)
Fix B1–B6; remove the `ExitOverlay` alias and `_exit_of`; make `Lab.screen` raise `KeyError("unknown screen")`; route all server errors through `ServerError`; add a golden-value regression test for one full synthetic backtest and one bundled-data backtest; update `CALIBRATION_SP500.md` for B7; add an LRU bound to `Market._views`; move `research/results/*.csv` out of git or into LFS. **[UI blocker: B1, B4]**
*Done when:* the repros in section 3 fail to reproduce, the suite still passes, and a `Portfolio` named like a screen produces two rows.

### Phase 1: Data correctness **[UI blocker]**
1. Benchmark kind: `IndexBenchmark(kind=...)`; refuse or warn on price-vs-TR comparisons; accept a dividend-yield series to approximate TR; load a US TR proxy (SPY adjusted close) and the Canadian proxies (XIC/XIU) from Stooq once the hosts are allowed.
2. Dataset manifest and survivorship score computed at load time; shown by `print(report)` and in JSON.
3. Universe history: index membership and delisting proceeds instead of a flat `delist_return`; a complete US universe via SEC FSDS as the first real fundamentals; Canadian fundamentals from EODHD or another PIT source.
4. Reconciliation: reproduce one published Globe screen portfolio (picks and 12-month return) within a stated tolerance.
*Done when:* the bundled-data report prints a red survivorship warning and a price-index warning; a US backtest runs on a universe that includes delisted names with a TR benchmark; and one Globe portfolio reconciles.

### Phase 2: Service hardening **[UI blocker]**
Typed models and OpenAPI; async jobs; result store keyed by spec/config/data/code; per-session labs; trial counter; bounded caches; a minimal auth token. MCP regenerated from the same models.
*Done when:* a generated client (from OpenAPI) can run a 60 s backtest without a blocking request, retrieve it later by id, and the OpenAPI document contains no `additionalProperties: true` bodies.

### Phase 3: UI v1 (scenarios and comparisons)
Scenario form (screen, rebalance, costs, dates, dataset), comparison table and curves with the passive index pinned, timing-luck panel, picks and Graham funnel, data card, trial counter, CI on excess. Demo data only behind a banner.
*Done when:* an owner-level user can build two scenarios, compare them against the index, see the data warnings, and export the report; and no number on screen is computable without a benchmark row.

### Phase 4: Exit as drawdown control
One toggle, the fixed trend rule from the research note, labelled as such; the "with and without" rows from `Lab.backtest(with_and_without=True)`; cash earns a loaded T-bill series. Calibration stays an SDK diagnostic.
*Done when:* toggling the rule adds a `+exit` row with `TimeOut` and `Switches/yr`, and the UI text states that it lowers drawdown and does not raise return.

### Phase 5: Later
Taxes (TFSA/RRSP/taxable), scheduled picks and paper portfolios (ROADMAP.md sections 2–4), sweeps and significance behind an "advanced" mode with the trial counter wired in, extended metrics once Bank of Canada data is loaded.

### Measured reference numbers
| What | Result |
|---|---|
| Test suite | 208 passed, 54 s |
| `Lab.synthetic()` build / 8 screens annual / monthly | 0.9 s / 6.2 s / 66 s |
| `timing("dogs", stride=21)` | 15.6 s for 13 offsets (daily stride ≈ 5 min) |
| Bundled S&P: 2 screens monthly / annual | 11.8 s / 0.5 s |
| `exit_eval` (3 signals) / `significance` (8 screens) / 9-point walk-forward sweep | 0.5 s / 7.3 s / 19 s |
| Cached view size | 119 KB; 277 views after one monthly backtest |
| S&P 500 price index CAGR 1990–2022 | 7.39% (7.8% over 1991-02 to 2022-12) |
| Equal-weight buy-and-hold of the 20 bundled survivors | 17.5%/yr |
| `momentum_12_1` monthly on bundled data | CAGR 22.8%, Excess/yr 15.0%, CI [10%, 18%], t 5.1, DSR 1.00 |
| Walk-forward sweep (n × rebalance, 9 points) | OOS/IS 0.68; DSR 1.00 on every row |
