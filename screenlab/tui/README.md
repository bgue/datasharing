# screenlab-tui: interactive terminal and browser app

For trying an idea, changing it, running it again and comparing with what it replaced and with the passive index.
Every number comes from the same operations, job manager and run store as the command line, notebooks and servers.

```
screenlab-tui                          # terminal; default lab sp500 (synthetic if skfolio is missing)
screenlab-tui --lab synthetic --store none
screenlab-tui --serve [--host localhost --port 8000]     # same app in a browser (textual-serve)
screenlab-tui --theme textual-light    # any Textual theme: nord, gruvbox, tokyo-night, ...
screenlab-tui --control-port 0         # no control endpoint (default 8765, 127.0.0.1 only)
```
Install: `pip install -e ".[tui]"` (Textual, textual-plotext, textual-serve; `.[all]` includes it).

## The screen
* Top: the data card line (name, span, names, benchmark, point-in-time, survivorship), warnings in colour
  (price-index benchmark red, survivorship yellow), the store path, and a progress bar while jobs run.
* Left: **Scenarios** (the passive index is pinned first, grey, and cannot be removed; every scenario keeps its
  colour for good) and the **parameter editor** (screen, holdings, rebalance months, cost bps, commission, MER,
  cash rate, dates, trend exit switch) with **Apply**.
* Right tabs: **Compare** (passive row first; CAGR, Vol, Sharpe, MaxDD, Excess/yr, CI5/CI95 when a bootstrap was
  run, TimeOut/Switches for exits), **Curves** (log wealth in scenario colours), **Timing** (offset distribution,
  tranche), **Picks**, **Exit** (forward returns flagged vs not, overlay stats), **Monte Carlo** (quantile table
  and median fan), **Runs** (the store; Enter re-runs a stored run and checks it reproduces), **Log**.

## Refining a scenario
Select a scenario, edit a field, press **F2** (or the Apply button, or Enter in a field). Apply never edits:
it creates `dogs v2` from `dogs` with the changed fields, shows a line such as
`dogs -> dogs v2: cost_bps 10.0 -> 30.0, n None -> 15`, runs it, and Compare shows `dogs`, `dogs v2` and the
passive index together. Versions are kept until you delete them (`d`).

## Keys
`a` add, `c` clone, `r` run selected, `R` run all not yet run, `d` delete, `x` toggle trend exit (then F2),
`F2` Apply, `m` Monte Carlo (selected scenario; price-rank screens only), `t` timing study, `p` picks,
`s` save the screen as ScreenSpec JSON, `e` export Compare as markdown + CSV, `w` charts in the browser,
`T` light/dark, `1`-`8` tabs,
`?` help, `q` quit. The mouse works too. In a field, `F2` or Enter applies.

## Control endpoint (for agents and scripts)
While running, the app serves `http://127.0.0.1:8765` (`GET /health /state /view`, `POST /command`). Set
`SCREENLAB_TUI_TOKEN` before starting to require a token. The contract is [PROTOCOL.md](PROTOCOL.md); the MCP tools
`tui_*` (server/tui_tools.py) forward to it. Try it:
```
curl -s localhost:8765/health
curl -s -X POST localhost:8765/command -d '{"cmd":"add_scenario","args":{"screen":"dogs","run":true}}'
curl -s localhost:8765/view
```
In browser mode (`--serve`) each browser tab starts its own app process, and only one of them can hold the control port.

## Charts in the browser
The terminal plots are coarse. Press `w` (or open `http://127.0.0.1:8765/charts`) for interactive Plotly charts of
the same session, in a browser tab beside the terminal: tabs **Curves** (log wealth), **Compare** (CAGR and Excess/yr
with the bootstrap interval), **Timing** (CAGR by start offset, tranche), **Monte Carlo** (fan with 25-75% and 5-95%
bands, CAGR and drawdown histograms against the index) and **Exit** (forward returns flagged vs not). Hover shows the
values, drag zooms (double-click resets), clicking a legend entry hides a scenario, **PNG** saves a chart, and each
tab has a table of the numbers. Scenario colours are the session's fixed colours; the passive index is grey and its
legend entry is locked unless you tick "allow hiding the index". The page takes its colours from the active Textual
theme (`GET /theme`) and redraws when the session changes (server-sent events on `GET /events`): add, run or edit in
the terminal and the charts follow. Histograms are binned in the app (same bins for every portfolio and the index);
no per-path data is sent.
* Plotly.js comes from `https://cdn.plot.ly/plotly-2.35.2.min.js`. Offline: set `SCREENLAB_PLOTLY_JS` to a local
  `plotly.min.js` path (served at `/plotly.js`) or to another URL before starting the TUI.
* `--serve`: the terminal runs in the browser (canvas), so charts cannot sit inside it. The footer shows
  `http://127.0.0.1:8765/`, a split page with the terminal on the left and the charts on the right (drag the divider;
  the position is remembered). `w` shows the URL there instead of opening a browser. The terminal inside that page is
  a new app process (one per browser tab), so its charts follow the process that holds the control port.
* With `SCREENLAB_TUI_TOKEN` set, open the page as `/charts?token=<token>` (the page passes it on to its requests).
* Needs the control endpoint (`--control-port 0` turns the charts off). Histograms are only available for Monte Carlo
  runs computed in this session (a result reloaded from the run store has the table and fan only).
* Endpoints: `GET /charts`, `GET /charts/data?tab=curves|compare|timing|montecarlo|exit`, `GET /theme`,
  `GET /events`, `GET /` (with `--serve`). Code: `web.py` (data and binning), `static/charts.html`,
  `static/index.html`.

## Code
`session.py` state and commands (no Textual import, used by the tests), `app.py` the Textual app and `main`,
`control.py` the HTTP endpoint, `web.py` + `static/` the browser charts. Tests: `python -m pytest screenlab/tests/test_tui.py`.
