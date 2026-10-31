# screenlab-tui control protocol (v1)

The running TUI (`screenlab-tui`) serves a small local JSON-over-HTTP endpoint so that a script or an
agent (the MCP tools `tui_*` in `server/tui_tools.py`) can drive the same state a person sees. Every
command is also a method of `screenlab.tui.session.Session` (`session.command(cmd, args)`), so it can be
tested without a terminal. The protocol adds no analysis: runs go through `operations.OPERATIONS`, the
JobManager and the store. The command names below are the contract and are not renamed.

## Transport

* Default `http://127.0.0.1:8765` (`screenlab-tui --control-port N`, `0` = off; with `--serve` the endpoint is the shared
  session and always on, `0` picks a free port). Bind address is always
  loopback. Clients use env `SCREENLAB_TUI_URL` to override.
* Token: if env `SCREENLAB_TUI_TOKEN` is set in the TUI process, every request must carry
  `Authorization: Bearer <token>` (or header `X-Screenlab-Token: <token>`); otherwise HTTP 401
  `{"error": "unauthorized"}`. `GET /health` needs no token.
* All bodies are JSON (UTF-8). NaN/inf are `null`. Dates are ISO strings.

| Request | Answer |
|---|---|
| `GET /health` | `{"ok": true, "version": "0.1.0", "protocol": 1, "lab": "synthetic", "dataset": "<data card line>", "store": "<path or null>", "control_port": 8765, "seq": 17, "home_url": "http://127.0.0.1:8765/charts", "serve": false, "headless": false}` (`serve`: `--serve` mode; `headless`: the endpoint belongs to the shared-session parent, no UI in this process) |
| `GET /state` | `Session.snapshot()` (below) |
| `GET /view[?tab=<tab>]` | `{"view": "<plain text of the tab (default: the active one)>", "tab": "compare"}` |
| `GET /seq?after=<seq>&timeout=<s>` | long-poll: answers `{"seq": n}` as soon as `Session.seq != after`, or after `timeout` seconds (max 30; `0` = answer now). The cheap change detector for clients that mirror the state (`RemoteSession`): fetch `GET /state` only when `seq` moved. |
| `POST /await` body `{"result": {"jobs": [...]}, "timeout": 120}` | waits (in the HTTP thread; the UI keeps running) until the jobs of a command result are final and returns that result with the final job statuses, exactly like `wait` does (408 on timeout). |
| `GET /charts`, `GET /charts/data?tab=`, `GET /theme`, `GET /events`, `GET /` (`--serve`) | browser chart panel (HTML page, tab JSON, theme colours, server-sent events); see README "Charts in the browser". `?token=` is accepted for the browser. |
| `POST /command` body `{"cmd": "<name>", "args": {...}}` | the command's result (an object) |

Errors: `{"error": "<message>"}` with HTTP 400 (bad or missing argument, invalid state), 404 (unknown
command, scenario, job or run), 401 (token), 408 (`wait` timed out; the jobs keep running), 500 (internal).
A command never leaves the TUI in a half-changed state.

## One shared session (`--serve`)

`screenlab-tui --serve` runs the control endpoint **in the parent process, which owns the one `Session`** (no UI there;
`control.HeadlessHost` marshals calls and keeps `Session.poll()` going). textual-serve starts one app process per browser
tab; each is started as `screenlab-tui --remote http://127.0.0.1:<control port>` and talks to the parent over this very
protocol through `remote.RemoteSession` (token from `SCREENLAB_TUI_TOKEN`, which the children inherit). So every tab, the
chart panel, curl and the MCP tools see and change the same scenarios, tab, selection and theme. `RemoteSession` has the
`Session` interface the app uses (`command(cmd, args)`, `snapshot()`, `view(tab)`, `seq`, `poll()`, `await_jobs()`, and the
read-only attributes the app draws from) and detects changes with `GET /seq?after=` (a watcher thread), fetching `GET /state`
only when the seq moved. `screenlab-tui --remote URL` also joins a TUI running in another terminal.

## Concepts

* **Scenario** = one version of one portfolio recipe. It has `id` (`"s1"`, stable, never reused), `name`
  (`"dogs"`), `version` (1, 2, ...), `label` (`"dogs"` for v1, `"dogs v2"` after that) and a fixed
  `color` (Okabe-Ito order `#0072B2 #56B4E9 #D55E00 #E69F00 #009E73 #CC79A7 #F0E442 #7B3294`, then
  further distinct hues; assigned at creation, never reassigned or recycled, also after removal).
  The **passive index** is pinned first (`"id": "passive"`, grey `#6B6B6B`) and cannot be removed.
* `set_params` never edits a scenario: it creates the next version (`dogs v2`) and keeps `dogs`. The
  Compare tab shows all versions plus the passive index.
* **Scenario reference** (`scenario` argument): an `id` (`"s2"`), a `label` (`"dogs v2"`) or a bare name
  (`"dogs"` = its latest version). Omitted = the selected scenario.
* **Params** of a scenario (all optional on input; `null` = unset / lab default):

| param | type | meaning |
|---|---|---|
| `screen` | string or ScreenSpec object | built-in/defined screen name, or an inline ScreenSpec (`specs/*.json` shape) |
| `lab` | string | dataset name in the registry (default: the session's lab) |
| `n` | int | number of holdings (spec screens and rule screens only) |
| `rebalance_months` | int | months between rebalances |
| `cost_bps` | number | one-way cost in bps |
| `commission` | number | flat $ per trade and side |
| `mer` | number | annual expense ratio |
| `cash_rate` | number | annual return on cash |
| `exit` | object or null | an exit policy request, same shape as `exit` of the `backtest` operation: `{"signals": [{"type": "TrendBreak", "index": "SYN_CAPWT"}], "threshold": 0.8, "park": "cash", ...}` |
| `start`, `end` | ISO date | backtest window |
| `tax` | object or null | tax the run, same shape as `config.tax` of the `backtest` operation: `{"model": "placeholder"\|"canadian"\|"none", "account": "taxable"\|"tfsa"\|"rrsp", "province": "AB"\|"BC", "year": 2025, "other_income": 90000, "rrsp_withdrawal_rate": 0.3, "harvest": false, "harvest_threshold_pct": 0.10, "harvest_replacement": "second_pick", ...}` (`marginal_rate` replaces `other_income`; `employment_income` is a deprecated alias). Bad values are 400 (`config.tax.province must be one of ['AB', 'BC'], got 'ON'`). The editor's Tax section (account, province, other income, RRSP withdrawal rate, harvest checkbox, threshold %, replacement, year) sets it (province blank = `placeholder`, else `canadian`) |

* **Job info** (`job`): `{"job_id", "kind", "status": "queued|running|done|failed|cancelled", "progress": 0..1,
  "message", "error", "cache_hit"}`.
* `wait` (bool) on run-type commands: block until the jobs finish (or `timeout` seconds, default 120, then
  408). Default `false` except for `picks`. Without `wait`, poll `GET /state` (`jobs`, scenario `status`).

## Commands

### `add_scenario`
Args: `{"name": "dogs", "screen": "dogs", ...params, "run": false, "ci": false}`. `name` defaults to the screen name
(made unique with `#2`, `#3`); `screen` is required (name or ScreenSpec object). `run: true` also runs it (`ci: true` adds the bootstrap-CI job, as in `run`).
Result: `{"scenario": <scenario object>, "jobs": [...]}` (`jobs` only with `run`).
```json
{"cmd": "add_scenario", "args": {"screen": "dogs", "rebalance_months": 12, "cost_bps": 10, "run": true}}
-> {"scenario": {"id": "s1", "name": "dogs", "version": 1, "label": "dogs", "color": "#0072B2",
    "params": {"screen": "dogs", "rebalance_months": 12, "cost_bps": 10}, "status": "queued"},
    "jobs": [{"job_id": "...", "kind": "backtest", "scenario": "dogs", "status": "queued"}]}
```

### `clone_scenario`
Args: `{"scenario": "dogs", "name": "dogs_6m"}` (`name` optional). Copies the params of the referenced
version into a new scenario family at version 1 with a new colour. Result: `{"scenario": {...}}`.

### `set_params`
Args: `{"scenario": "dogs", "params": {"cost_bps": 25, "n": 15}, "run": false, "ci": false}`. Creates the next version
of that scenario's family from the referenced version with `params` merged (`null` clears a param) and
selects it. If nothing changes: 400 `no parameter changed`.
Result: `{"scenario": <new version>, "previous": "dogs", "diff": [{"param": "cost_bps", "from": 10, "to": 25}],
"diff_line": "dogs -> dogs v2: cost_bps 10 -> 25, n 10 -> 15", "jobs": [...]}`.

### `remove_scenario`
Args: `{"scenario": "dogs v2"}`. Removes that version only (its colour is retired). The passive index and
a scenario with a running job (cancel first) give 400. Result: `{"removed": "dogs v2", "scenarios": ["dogs"]}`.

### `run`
Args: `{"scenario": "dogs v2" | ["dogs", "dogs v2"] | "all", "ci": false, "force": false, "wait": false,
"timeout": 120}`. Default scenario: the selected one; `"all"` = every scenario without a finished run.
Submits one `backtest` job per scenario through the JobManager (thread mode by default), so progress is
reported; `ci: true` also submits a `significance` job (bootstrap CI of Excess/yr). Identical finished runs
come back at once (`cache_hit`) unless `force`.
Result: `{"jobs": [{"scenario": "dogs v2", "scenario_id": "s2", "job_id": "...", "kind": "backtest", "status": "running", "progress": 0.1, "cache_hit": false}]}`;
with `wait` the statuses are final and `"errors": {"dogs v2": "<message>"}` lists failures.

### `compare`
Args: `{"scenarios": ["dogs", "dogs v2"] | null, "ci": false}` (default: every scenario with a result).
Pure read; does not run anything. Result:
```json
{"benchmark": "SYN_CAPWT",
 "columns": ["CAGR", "Vol", "Sharpe", "MaxDD", "Excess/yr", "CI5", "CI95", "TimeOut", "Switches/yr"],
 "rows": [
   {"id": "passive", "label": "SYN_CAPWT", "color": "#6B6B6B", "passive": true, "version": null,
    "values": {"CAGR": 0.081, "Vol": 0.14, "Sharpe": 0.55, "MaxDD": -0.31, "Excess/yr": null}},
   {"id": "s1", "label": "dogs", "color": "#0072B2", "passive": false, "version": 1,
    "values": {"CAGR": 0.095, "Excess/yr": 0.012, "CI5": -0.01, "CI95": 0.03}},
   {"id": "s2", "label": "dogs v2", "color": "#56B4E9", "passive": false, "version": 2, "values": {}}],
 "missing": ["dogs v3"], "diffs": ["dogs -> dogs v2: cost_bps 10 -> 25"]}
```
`columns` are the metric names of the backtest table (those present). `CI5/CI95` appear only after a
`significance` job (`run` with `ci: true`). `missing` lists scenarios without a finished result.

### `timing`
Args: `{"scenario": "dogs", "mode": "phase", "stride": 1, "wait": false}`. Submits a `timing` job for the
scenario's screen and config. Result `{"jobs": [...]}`. The finished study is in `snapshot.analyses.timing`
(`{label: {"table", "distribution", "tranche"}}`) and shown on the Timing tab.

### `montecarlo`
Args: `{"scenarios": ["dogs"] | null, "n_paths": 500, "horizon_years": [5, 10], "generator": {"type": "BlockBootstrap", "block_months": 12}, "seed": 0, "wait": false}`
(all optional; the settings are remembered in the session, see `snapshot.mc`). Submits a `montecarlo` job
with the `montecarlo` operation's arguments; `screens` are the scenarios' screens. Result `{"jobs": [...]}`;
the quantile table and fan are in `snapshot.analyses.montecarlo`.

### `exit_eval`
Args: `{"scenario": "dogs", "wait": false, ...exit_evaluate operation args}`. Without `signals` the
scenario's `exit` params are used (400 if it has none). Extra args (`threshold`, `horizons`, `target`,
`policy`, ...) go to the `exit_evaluate` operation unchanged. Result `{"jobs": [...]}`; result in
`snapshot.analyses.exit`.

### `picks`
Args: `{"scenario": "dogs", "asof": null, "columns": null, "wait": true}`. Names the scenario's screen
selects (latest date by default). Result with `wait` (default): `{"scenario", "asof", "columns": [...], "rows": [[...]]}`
(first column `ticker`); shown on the Picks tab.

### `accounts`
Args: `{"scenario": "dogs", "accounts": ["taxable", "tfsa", "rrsp"], "force": false, "wait": false}` (all optional; `accounts`
default all three). Submits an `account_compare` job for the scenario's screen, window, config and exit, with the scenario's
`tax` param (if any) as the shared tax settings (its `account` is ignored: the operation runs each account; no `tax` = the
canadian model, Alberta, other income 90000). Result `{"jobs": [...]}`; the finished comparison is
`snapshot.analyses.accounts[<scenario label>]` = `{"benchmark", "accounts", "table": {"columns", "rows"}, "foreign": {...},
"best": {name: {"account", "AfterTaxCAGR", "next", "gap"}}, "notes": [...], "tax": {...}}` (the `account_compare` result,
see docs/TAXES.md "Comparing accounts") and the Accounts tab (key `A`; opening the tab starts the job once per scenario
version). Browser chart: `/charts/data?tab=accounts` (grouped bars of after-tax CAGR by account).

### `plan` and `plan_mc`
Args of `plan`: `{"scenario": "dogs", "start": null, "end": null, "initial": 10000, "periodic": 500, "frequency": "monthly", "growth": 0.0 | "cpi", "extra": [{"date", "amount", "note"}], "use_phases": true, "phases": [{"years_from", "years_to", "amount", "note"}], "phase_growth": "indexed" | "nominal", "add_phase": {"years_from", "years_to", "amount", "note"}, "remove_phase": 0, "set_phase": {"index": 0, "amount": 600}, "flat_phase": false, "add_extra": {"date", "amount", "note"}, "remove_extra": 0, "withdrawals": 0, "withdraw_from": null, "withdraw_growth": 0.0, "cash_timing": "invest" | "hold", "index_mer": 0.0005, "goal": null, "run": true, "force": false, "wait": false}` (all optional). The values are remembered in the session (`snapshot.analyses.plan_form`, which also holds the Monte Carlo settings `n_paths`, `generator`, `seed`); `run: false` only edits the form (`add_extra` appends one extraordinary flow, `remove_extra` deletes the row at that index, `extra` replaces the table). V2: contribution phases are amount per period by plan year, half-open [years_from, years_to) (`years_to` null = until `withdraw_from` or the end); the default form is the owner's base case (0-10 $500, 10-13 $3,000, 13-20 $1,000 a month) with `use_phases` true, and then the flat `periodic` is not sent; `phases` replaces the table, `add_phase` / `remove_phase` / `set_phase` edit it, `flat_phase` replaces it by one phase of the form's `periodic` and turns `use_phases` on, `use_phases: false` restores the flat plan; overlapping phases are refused (400). Submits a `plan` job for the scenario's screen, config and `tax` param (the account) with the form; `start` defaults to the scenario's start, else 13 months into the data. A scenario with an exit policy is refused (400). `plan_mc` takes the same fields plus `n_paths`, `generator` (`block`, `stationary`, `regime`, `iid`) and `seed`, and submits a `plan_montecarlo` job. Result `{"form": {...}, "jobs": [...]}`; the results are `snapshot.analyses.plan[<scenario label>]` = `{"plan": {"spec", "metrics", "series", "flows", "tax", "goal", "card", "daily"}, "mc": {"spec", "n_paths", "seed", "quantiles", "p_beat_index", "p_shortfall", "p_shortfall_real", "p_shortfall_index", "goal", "sequence", "paired", "reversal", "fan", "histograms", "card"}}` (the `plan` and `plan_montecarlo` results, see docs/PLAN.md; a new `plan` result drops a Monte Carlo result of a different plan), shown on the Plan tab (key `P`). Browser chart: `/charts/data?tab=plan` (`plans`: per scenario `x`, `series` {contributed, strategy_pre, strategy_after_tax, index_pre, index_after_tax}, `flows`, `metrics` rows with `better`, and `mc` with the fan and histograms).

### `show`
Args: `{"tab": "compare"}`, one of `compare curves timing picks exit montecarlo runs log data hypotheses accounts plan`. Switches the
visible tab. Result `{"tab": "compare"}`.

### `select`
Args: `{"scenario": "dogs v2"}`. Selects the scenario (editor and Curves highlight). Result `{"selected": "s2", "label": "dogs v2"}`.

### `save_spec`
Args: `{"scenario": "dogs", "path": null}`. Writes the scenario's screen as ScreenSpec JSON (rule screens
only). Default path `./<name>.json`. Result `{"path": "...", "spec": {...}}`.

### `export`
Args: `{"path": "screenlab_export", "format": "both"}` (`markdown`, `csv` or `both`). Writes the Compare table
(`<path>.md`, `<path>.csv`) and the data card + warnings in the markdown. Result `{"files": ["screenlab_export.md", "screenlab_export.csv"]}`.

### `history`
Args: `{"kind": null, "screen": null, "limit": 20}`. Browse the store (`SQLiteStore.find`): finished runs,
newest first; with a NullStore the list is empty. Result `{"store": "<path or null>", "runs": [{"run_key", "kind", "dataset", "screen", "status", "label", "created_at"}]}`.
Optional `{"compare": ["<run_key>", ...]}` instead returns `{"columns", "rows"}` from `store.compare`, and
`{"rerun": "<run_key>"}` re-executes a stored run and returns `{"run_key", "match": true, "differences": [...]}`.

### `jobs` / `cancel`
`jobs` args `{}` -> `{"jobs": [job info with "scenario"]}`. `cancel` args `{"scenario": "dogs"}` or `{"job_id": "..."}` ->
`{"cancelled": ["<job_id>"]}`.

### `set_theme`
Args: `{"theme": "textual-dark", "variables": {"background": "#121212", "primary": "#0178D4", ...}}` (any Textual theme
name, e.g. `textual-light`; `variables` is optional: the theme's colours, which an app pushes so the chart panel can match
it when the endpoint has no UI of its own). Result `{"theme": "..."}`.
(Not applied in a headless Session; it is stored and applied by the app.)

### `curves`
Args: `{}`. `{label: {"color", "points": [[iso_date, wealth], ...]}}`, wealth normalised to 1.0 at each curve's start,
passive index first (what the Curves tab draws).

### `screens`
Args: `{"lab": null}`. `{"lab": "synthetic", "screens": ["dogs", ...]}`: screen names of a dataset (default: the session's).

### `datasets`
Args: `{}`. What the Data tab shows:
```json
{"labs": [{"name": "synthetic", "default": true, "session": true, "loaded": true,
           "line": "Data: synthetic, ... benchmark SYN_CAPWT ...", "warnings": ["WARNING: ..."], "error": null}],
 "downloader": true, "running": null, "last_fetch": null,
 "sources": ["sec", "boc", "fred", "stooq", "statcan"],
 "hosts": {"sec": ["www.sec.gov", "data.sec.gov"], "boc": ["www.bankofcanada.ca"], "...": []},
 "allow_hosts": ["data.sec.gov", "..."],
 "defaults": {"dest": "data", "start": "2000-01-01", "sec_user_agent": "", "tickers": ["XIU.TO"]}}
```
One entry per registry lab (a lab that cannot be built has `line: null` and `error`). `downloader` is false when the
operation `fetch_data` is not registered ("downloader not installed"). `running` is the fetch job in flight, `last_fetch`
the last result (`{"manifest", "labs", "registered": [...]}` or `{"error"}`). `hosts` are the network allow-list hostnames
per source (the downloader's own table when it exports one).

### `fetch_data`
Args: `{"sources": ["sec", "boc", "fred", "stooq", "statcan"], "dest": "data", "sec_user_agent": "Name email",
"tickers": ["XIU.TO"], "start": "2000-01-01", "periods": ["2024q1"], "force": false, "wait": false}`. Submits the
operation `fetch_data` through the normal job path (`registry.submit_run("fetch_data", args)`), so there is progress and a
result like any run. `sec_user_agent` is required when `sec` is among the sources (the SEC rejects anonymous clients;
400 otherwise); 501 `downloader not installed` when the operation is not registered. The operation's result is
`{"manifest": {"files": [{"source", "path", "bytes", "status"}], "errors": [...], "allow_hosts": [...]}, "labs":
[{"name", "fundamentals", "prices", "indices"}]}`; when the job finishes the session keeps it as `last_fetch` and registers
each listed lab it does not already know (`registry.register_csv`), so `datasets` lists it and the scenario editor's Dataset
Select (and the `lab` param) offers it. Result: `{"jobs": [...]}`; with `wait` also `"fetch": <last_fetch>`.

### `freeze` / `unfreeze`
Args: `{"scenario": "dogs"}`. `freeze` makes that version frozen (`frozen: true` in the scenario object and snapshot): the app
shows a lock and a read-only editor. `set_params` or `clone_scenario` from a frozen version (or from one of its
candidates) creates a scenario with `candidate: true`, `candidate_of: "<frozen label>"`; each candidate adds one trial to the
frozen row's `Trials`, so its `DSR` drops. `unfreeze` clears the flag, logs a line, and returns `{"scenario", "candidates"}`;
candidates stay flagged.

### `link` / `hypotheses`
`hypotheses` (no args) -> `{"path", "exists", "warnings", "hypotheses": [{"id": "H1", "example": true, "name", "screen",
"dataset", "benchmark", "horizon", "metric", "threshold", "costs", "exit", "written", "status", "linked": [{"scenario",
"label", "metric", "threshold", "value", "met": true|false|null, "reason"}], "met": true|false|null}]}`, parsed from
`research/PREREGISTERED.md` (or `Session(prereg=...)`, env `SCREENLAB_PREREG`). `met` is null with a `reason` when the metric
is missing or the result covers less than the stated horizon. `link` args `{"scenario": "dogs", "hypothesis": "H1"}`
(`null` unlinks; 404 for an unknown id) links every version of that scenario; its trial family becomes `<lab>:<screen>@H1`.

### Compare columns `Trials` and `DSR`
`compare` rows carry `values.Trials` (int) and `values.DSR` (0..1 or absent when not computable), plus `frozen`, `candidate`,
`candidate_of`, `hypothesis`, `family` and `mark`; the result has `trials_total` (variants run this session). The passive row
has neither. Snapshot: `trials: {"total", "families": {family: count}}`, `hypotheses` (as above), and per scenario `frozen`,
`candidate`, `candidate_of`, `hypothesis`, `family`, `trials`, `dsr`.

### Compare columns `AfterTaxCAGR` and `TaxDrag/yr`
When a scenario has a `tax` param, `compare` adds `values.AfterTaxCAGR` and `values.TaxDrag/yr` to its row and to the passive
row (taxed the same way as the first taxed scenario; the passive row also carries `tax`). Untaxed rows lack them. The web
Compare data (`/data/compare`) has the same `columns`/`rows` plus `tax: {label: params}`. `snapshot` scenario `params.tax`,
the exports (CSV column, Markdown `tax` line) and `view` (`Tax: dogs v2 taxable AB ...`) carry the tax params.

### `snapshot` / `view`
`snapshot` (also `GET /state`) -> the whole state:
```json
{"protocol": 1, "seq": 17, "lab": "synthetic", "labs": ["synthetic", "sp500"],
 "data_card": {"name": "...", "start": "...", "n_names": 150, "benchmark": "SYN_CAPWT", "benchmark_kind": "total_return", "...": "..."},
 "data_line": "Data: ...", "warnings": ["WARNING: ..."], "store": null,
 "active_tab": "compare", "selected": "s2", "theme": "textual-dark", "screens": ["dogs", "..."],
 "data": {"downloader": true, "running": null, "last_fetch": null},
 "passive": {"id": "passive", "label": "SYN_CAPWT", "color": "#6B6B6B"},
 "scenarios": [{"id": "s1", "name": "dogs", "version": 1, "label": "dogs", "color": "#0072B2", "params": {},
                "status": "done", "job": {"job_id": "...", "status": "done", "progress": 1.0},
                "metrics": {"CAGR": 0.095, "Excess/yr": 0.012}, "diff_from_previous": null}],
 "jobs": [], "mc": {"n_paths": 500, "horizon_years": [5, 10], "generator": null, "seed": 0},
 "analyses": {"timing": {}, "montecarlo": null, "exit": {}, "picks": {}, "accounts": {}, "plan": {}, "plan_form": {...}},
 "log": ["12:00:01 added dogs", "..."]}   // the last 200 log lines
```
`seq` increases on every state change (cheap change detection). Scenario `status` is one of `new`
(never run), `queued`, `running`, `done`, `failed`, `cancelled`.
`view` (also `GET /view`) -> `{"tab": "compare", "view": "<text>"}`: a plain-text rendering of what the active
tab shows (title, data card, warnings, the table with colour names, progress), for an agent to "read the screen".
