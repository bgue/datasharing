# Service layer

One `LabRegistry` (`registry.py`) holds named Labs, built lazily and cached: `synthetic`
(`Lab.synthetic()`), `sp500` (bundled data, needs skfolio) and anything you `register(...)` /
`register_csv(name, fundamentals, prices, indices)`. The default lab is `sp500` if skfolio is installed,
else `synthetic`. REST and MCP are thin wrappers over the same registry methods, so the JSON is identical.

Install: `pip install -r rothery_screener/requirements-server.txt` (fastapi, uvicorn, httpx, mcp).
All are optional and imported only by the server modules.

## JSON shapes
- Tables: `{"columns": [...], "rows": [[...], ...]}`; a DataFrame's index is the first column(s).
- Curves: `{"name": [["2001-02-28", 1.0], ...]}`, downsampled to at most `max_points` (default 500).
- NaN / inf / NaT become `null`; dates are ISO strings.
- Errors: `{"error": msg}` with 400 (bad input), 404 (unknown lab/screen), 501 (`exit/calibrate` when
  `Lab.calibrate_exit` is absent). MCP tools raise an error whose text starts with the same `[status]`.

## REST
    python -m rothery_screener.server.rest --port 8000        # or: uvicorn rothery_screener.server.rest:app

| Endpoint | Body / query |
|---|---|
| `GET /labs`, `GET /signals` | |
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
or `"statsmodels"`. Signals are `{"type": "TrendBreak", "window_months": 10}` (see `GET /signals`).

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

## MCP (stdio)
    python -m rothery_screener.server.mcp_server
    claude mcp add rothery -- python -m rothery_screener.server.mcp_server

Tools mirror the endpoints with the same names and arguments: `list_labs, list_screens, define_screen,
picks, backtest, timing, graham, sweep, significance, extended, exit_evaluate, exit_calibrate`, plus
`describe_columns` (the fundamentals column contract) and `list_signals`. Every tool takes an optional
`lab`. Works with both `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`).

Labs live in the server process: a screen added with `define_screen` stays until the process exits.
For custom data: `default_registry().register_csv("mine", "fund.csv", "prices.csv", "indices.csv")`
before starting, or build your own `LabRegistry` and `create_app(reg)` / `create_server(reg)`.

Tests: `python -m pytest rothery_screener/tests/test_server.py -q` (skipped when fastapi/httpx/mcp are absent).
