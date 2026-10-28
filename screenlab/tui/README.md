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
`s` save the screen as ScreenSpec JSON, `e` export Compare as markdown + CSV, `T` light/dark, `1`-`8` tabs,
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

## Code
`session.py` state and commands (no Textual import, used by the tests), `app.py` the Textual app and `main`,
`control.py` the HTTP endpoint. Tests: `python -m pytest screenlab/tests/test_tui.py`.
