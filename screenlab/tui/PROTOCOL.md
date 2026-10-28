# screenlab-tui control protocol (v1)

The running TUI (`screenlab-tui`) serves a small local JSON-over-HTTP endpoint so that a script or an
agent (the MCP tools `tui_*` in `server/tui_tools.py`) can drive the same state a person sees. Every
command is also a method of `screenlab.tui.session.Session` (`session.command(cmd, args)`), so it can be
tested without a terminal. The protocol adds no analysis: runs go through `operations.OPERATIONS`, the
JobManager and the store. The command names below are the contract and are not renamed.

## Transport

* Default `http://127.0.0.1:8765` (`screenlab-tui --control-port N`, `0` = off). Bind address is always
  loopback. Clients use env `SCREENLAB_TUI_URL` to override.
* Token: if env `SCREENLAB_TUI_TOKEN` is set in the TUI process, every request must carry
  `Authorization: Bearer <token>` (or header `X-Screenlab-Token: <token>`); otherwise HTTP 401
  `{"error": "unauthorized"}`. `GET /health` needs no token.
* All bodies are JSON (UTF-8). NaN/inf are `null`. Dates are ISO strings.

| Request | Answer |
|---|---|
| `GET /health` | `{"ok": true, "version": "0.1.0", "protocol": 1, "lab": "synthetic", "dataset": "<data card line>", "store": "<path or null>", "control_port": 8765}` |
| `GET /state` | `Session.snapshot()` (below) |
| `GET /view` | `{"view": "<plain text of the active tab>", "tab": "compare"}` |
| `GET /charts`, `GET /charts/data?tab=`, `GET /theme`, `GET /events`, `GET /` (`--serve`) | browser chart panel (HTML page, tab JSON, theme colours, server-sent events); see README "Charts in the browser". `?token=` is accepted for the browser. |
| `POST /command` body `{"cmd": "<name>", "args": {...}}` | the command's result (an object) |

Errors: `{"error": "<message>"}` with HTTP 400 (bad or missing argument, invalid state), 404 (unknown
command, scenario, job or run), 401 (token), 408 (`wait` timed out; the jobs keep running), 500 (internal).
A command never leaves the TUI in a half-changed state.

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

### `show`
Args: `{"tab": "compare"}`, one of `compare curves timing picks exit montecarlo runs log`. Switches the
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
Args: `{"theme": "textual-dark"}` (any Textual theme name, e.g. `textual-light`). Result `{"theme": "..."}`.
(Not applied in a headless Session; it is stored and applied by the app.)

### `snapshot` / `view`
`snapshot` (also `GET /state`) -> the whole state:
```json
{"protocol": 1, "seq": 17, "lab": "synthetic", "labs": ["synthetic", "sp500"],
 "data_card": {"name": "...", "start": "...", "n_names": 150, "benchmark": "SYN_CAPWT", "benchmark_kind": "total_return", "...": "..."},
 "data_line": "Data: ...", "warnings": ["WARNING: ..."], "store": null,
 "active_tab": "compare", "selected": "s2", "theme": "textual-dark",
 "passive": {"id": "passive", "label": "SYN_CAPWT", "color": "#6B6B6B"},
 "scenarios": [{"id": "s1", "name": "dogs", "version": 1, "label": "dogs", "color": "#0072B2", "params": {},
                "status": "done", "job": {"job_id": "...", "status": "done", "progress": 1.0},
                "metrics": {"CAGR": 0.095, "Excess/yr": 0.012}, "diff_from_previous": null}],
 "jobs": [], "mc": {"n_paths": 500, "horizon_years": [5, 10], "generator": null, "seed": 0},
 "analyses": {"timing": {}, "montecarlo": null, "exit": {}, "picks": {}},
 "log": ["12:00:01 added dogs", "..."]}
```
`seq` increases on every state change (cheap change detection). Scenario `status` is one of `new`
(never run), `queued`, `running`, `done`, `failed`, `cancelled`.
`view` (also `GET /view`) -> `{"tab": "compare", "view": "<text>"}`: a plain-text rendering of what the active
tab shows (title, data card, warnings, the table with colour names, progress), for an agent to "read the screen".
