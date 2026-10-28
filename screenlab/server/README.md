# Service layer

One `LabRegistry` (`registry.py`) holds named Labs, built lazily and cached: `synthetic`
(`Lab.synthetic()`), `sp500` (bundled data, needs skfolio) and anything you `register(...)` /
`register_csv(name, fundamentals, prices, indices)`. The default lab is `sp500` if skfolio is installed,
else `synthetic`. REST and MCP are thin wrappers over the same registry methods, so the JSON is identical.

Install: `pip install -r screenlab/requirements-server.txt` (fastapi, uvicorn, httpx, mcp).
All are optional and imported only by the server modules.

## JSON shapes
- Tables: `{"columns": [...], "rows": [[...], ...]}`; a DataFrame's index is the first column(s).
- Curves: `{"name": [["2001-02-28", 1.0], ...]}`, downsampled to at most `max_points` (default 500).
- NaN / inf / NaT become `null`; dates are ISO strings.
- Errors: `{"error": msg}` with 400 (bad input), 404 (unknown lab/screen), 501 (`exit/calibrate` when
  `Lab.calibrate_exit` is absent). MCP tools raise an error whose text starts with the same `[status]`.

## REST
    python -m screenlab.server.rest --port 8000        # or: uvicorn screenlab.server.rest:app

| Endpoint | Body / query |
|---|---|
| `GET /labs`, `GET /signals`, `GET /operations` | operations with their typed arguments |
| `GET /labs/{lab}/screens`, `GET /labs/{lab}/columns` | |
| `POST /labs/{lab}/screens` | ScreenSpec JSON |
| `GET /labs/{lab}/picks/{screen}?asof=` | |
| `POST /labs/{lab}/backtest` | `screens, start, end, config, max_points` |
| `POST /labs/{lab}/timing` | `screen, mode, stride, start, end, config` |
| `GET /labs/{lab}/graham?preset=&asof=&overrides=<json>` | |
| `POST /labs/{lab}/sweep` | `screen, grid, objective, start, end, splits` |
| `POST /labs/{lab}/significance` | `screens, start, end, config, n, seed` |
| `POST /labs/{lab}/extended` | `macro, screens, spread_over_prime, leverage, ...` |
| `POST /labs/{lab}/exit/evaluate` | `signals, weights, threshold, min_votes, hysteresis, target, horizons, macro` |
| `POST /labs/{lab}/exit/calibrate` | as evaluate plus `grid, objective, splits, constraints` |

`config` holds Config overrides (rebalance_months, cost_bps, commission, capital, mer, delist_return,
min_holdings, cash_rate). `macro` is `[{date, series, value}]` records, a `{series: {date: value}}` map,
or `"statsmodels"`. Signals are `{"type": "TrendBreak", "window_months": 10}` (calendar months) or `{"type": "Breadth", "days": 138}` (trading days); `GET /signals` lists every signal's parameters, units and deprecated names. Request bodies are typed per operation (see `/openapi.json`); an operation registered in code is routed automatically.

    curl localhost:8000/labs
    curl -X POST localhost:8000/labs/synthetic/screens -H 'content-type: application/json' \
      -d '{"name":"cheap","n":8,"universe":{"top":100},"filters":["pe < 20"],"rank":{"div_yield":"desc"}}'
    curl 'localhost:8000/labs/synthetic/picks/cheap?asof=2005-06-30'
    curl -X POST localhost:8000/labs/synthetic/backtest -H 'content-type: application/json' \
      -d '{"screens":["dogs","cheap"],"config":{"rebalance_months":6,"cost_bps":20},"max_points":200}'
    curl 'localhost:8000/labs/synthetic/graham?preset=defensive&overrides=%7B%22max_pe%22%3A18%7D'
    curl -X POST localhost:8000/labs/synthetic/sweep -H 'content-type: application/json' \
      -d '{"screen":"dogs","grid":{"dogs.n":[5,10],"cost_bps":[10,50]},"objective":"Sharpe"}'
    curl -X POST localhost:8000/labs/synthetic/exit/evaluate -H 'content-type: application/json' \
      -d '{"signals":[{"type":"TrendBreak"},{"type":"Breadth"}],"threshold":0.7}'

## Asynchronous runs
`POST /jobs` takes `{"kind": "timing", "lab": "sp500", "args": {...}}` where `kind` is one of backtest, timing,
sweep, significance, extended, exit_evaluate, exit_calibrate, graham, picks and `args` are the body of the
matching synchronous endpoint (GET endpoints: `graham` takes `preset`/`asof`/`overrides`, `picks` takes
`screen`/`asof`). The response is `{job_id, run_key, status, cache_hit, progress, message, ...}`. An identical
finished run in the store comes back at once with `cache_hit: true` (pass `force: true` to recompute); an
identical running run is attached to. `GET /jobs/{id}` polls (`progress` 0-1), `GET /jobs/{id}/result` returns the
same JSON as the synchronous endpoint (409 while running, failed or cancelled), `DELETE /jobs/{id}` cancels,
`GET /jobs?status=done` lists. The MCP tools are `submit_run, run_status, run_result, cancel_run, list_runs`.
Job results use the default `max_points`.

Persistence: `create_app()` / `default_registry()` open `SCREENLAB_STORE` (default `~/.screenlab/runs.sqlite`; `none`
disables) and every run, synchronous or not, is recorded with its metrics. `LabRegistry(factories, store=,
mode="process"|"thread", workers=, sync_timeout=)` configures a custom registry (process mode needs importable
factories; tests use `mode="thread"` and a temporary store). Synchronous endpoints stop after `sync_timeout`
seconds (env `SCREENLAB_SYNC_TIMEOUT`, default 120, 0 = unlimited) with a 408 telling the client to use `/jobs`.
Every result JSON carries `trials_in_family` and `trials_scope`.

## MCP (stdio)
    python -m screenlab.server.mcp_server
    claude mcp add screenlab -- python -m screenlab.server.mcp_server

Tools mirror the endpoints with the same names and arguments: `list_labs, list_screens, define_screen,
picks, backtest, timing, graham, sweep, significance, extended, exit_evaluate, exit_calibrate`, plus
`describe_columns` (the fundamentals column contract), `list_signals`, `list_operations`, one tool for every registered operation, and the run tools `submit_run, run_status,
run_result, cancel_run, list_runs`. Every tool takes an optional
`lab`. Works with both `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`).

Labs live in the server process: a screen added with `define_screen` stays until the process exits.
For custom data: `default_registry().register_csv("mine", "fund.csv", "prices.csv", "indices.csv")`
before starting, or build your own `LabRegistry` and `create_app(reg)` / `create_server(reg)`.

### Driving the TUI from Claude
The `tui_*` tools steer a running terminal UI (and read what the user sees) through its local control endpoint
(`tui/PROTOCOL.md`). They are thin forwarders: no analysis, no REST routes. To compute without a UI keep using
`backtest`, `timing`, `montecarlo`...; to change or read what is on the user's screen use `tui_*`.

    screenlab-tui --control-port 8765            # in a terminal; the endpoint binds 127.0.0.1 only
    export SCREENLAB_TUI_URL=http://127.0.0.1:8765   # client side, optional (this is the default)
    export SCREENLAB_TUI_TOKEN=...               # optional; must match the token the TUI was started with

Tools: `tui_status, tui_state, tui_view, tui_command(cmd, args)` and the wrappers `tui_add_scenario, tui_set_params,
tui_run, tui_compare, tui_show, tui_montecarlo, tui_export`. Example calls:

    tui_status()                                              # {"running": true, "lab": ..., ...} or a start hint
    tui_add_scenario("dogs", params={"rebalance_months": 12, "cost_bps": 10}, run=True)
    tui_set_params({"cost_bps": 25}, scenario="dogs", run=True)   # creates "dogs v2", keeps "dogs"
    tui_run("all", ci=True, wait=True)
    tui_compare()                                             # the Compare table, passive index first
    tui_show("curves"); tui_view()                            # switch tab, then read it as text

With no TUI running the tools fail with a 503 `ServerError` that includes `screenlab-tui --control-port 8765`
(`tui_status` returns `running: false` instead). 400/404 from the TUI keep their status and message.

Tests: `python -m pytest screenlab/tests/test_server.py -q` (skipped when fastapi/httpx/mcp are absent).
