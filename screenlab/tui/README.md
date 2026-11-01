# screenlab-tui: interactive terminal and browser app

For trying an idea, changing it, running it again and comparing with what it replaced and with the passive index.
Every number comes from the same operations, job manager and run store as the command line, notebooks and servers.

```
screenlab-tui                          # terminal; default lab sp500 (synthetic if skfolio is missing)
screenlab-tui --lab synthetic --store none
screenlab-tui --serve [--host localhost --port 8000]     # same app in a browser (textual-serve), one shared session
screenlab-tui --remote http://127.0.0.1:8765             # a second view on a running TUI's session
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
  and median fan), **Runs** (the store; Enter re-runs a stored run and checks it reproduces), **Log**, **Data**
  (key `D`, see below). The parameter editor also has a **Dataset** Select (every dataset in the registry, including ones
  the downloader added; the Screen Select follows it).

## Data tab (key `D`)
Lists the datasets the session can use (registry labs with their data card line and warnings: price-index benchmark,
survivorship) and has a **Download free datasets** form: SEC contact (`Your Name you@example.com`, required by the SEC,
prefilled from `SCREENLAB_SEC_USER_AGENT`), sources (SEC, Bank of Canada, FRED, Stooq, Statistics Canada), destination
folder, tickers, start date and SEC periods. The button submits the operation `fetch_data` through the normal job path
(`registry.submit_run`), so the header progress bar moves, and the tab then shows the manifest (source, path, bytes, status),
the errors, and any new dataset, which also appears in the editor's Dataset Select. The form lists the network
allow-list hostnames the downloads need (for a sandbox or cloud environment that restricts outbound hosts). If the
operation `fetch_data` is not registered the tab says "downloader not installed" and the button is disabled. The same from a
script: commands `datasets` and `fetch_data` ([PROTOCOL.md](PROTOCOL.md)).

## Accounts tab (key `A`)
Which account suits the selected scenario: the same pre-tax portfolio, and the passive index, in a taxable account, a TFSA and
an RRSP, after tax (the operation `account_compare`, [../docs/TAXES.md](../docs/TAXES.md) "Comparing accounts"). Opening the
tab runs it once per scenario version (the button runs it again); it uses the scenario's Tax section as the shared settings
(bracket, RRSP withdrawal rate, harvesting) and ignores the account chosen there. The table marks the best account by after-tax
CAGR in green and foreign withholding that is lost in amber; below it the foreign treatment matrix (share of positions by
domicile and wrapper, and what each account does with the withholding) and the notes (RRSP convention, CAD-basis warning, what
is not modelled). In the browser chart panel the Accounts tab draws after-tax CAGR by account, portfolio against index.
Command `accounts` ([PROTOCOL.md](PROTOCOL.md)).

## Plan tab (key `P`)
A dated investment plan on the selected scenario's strategy, against the passive index fed the same dollars on the same days
([../docs/PLAN.md](../docs/PLAN.md)). The form takes the start and end dates, a lump sum, a periodic contribution with its frequency and
growth (a number or `cpi`), extraordinary investments (type a date and an amount, Add row; select a row, Remove selected),
withdrawals with the date they start and their growth, a goal in dollars, the cash timing (`invest`: a contribution buys the target
weights the next trading day; `hold`: it waits in cash until the next rebalance) and the index's MER. Contribution phases (the amount available to invest by phase of life) are a table of from year, to year, amount per period and note, with the owner's base case
as the default (years 0-10 $500, 10-13 $3,000, 13-20 $1,000 a month; Add row, Update selected, Remove selected); the "Use phases" switch (on by default) replaces and greys out the flat periodic field, Flat copies the flat periodic into one phase, and after a run a line shows the count and total contributed in each phase. The strategy and the account
are those of the selected scenario (its Tax section; none = before tax). Run plan shows the metrics table (strategy, index,
difference; green where the strategy wins), a terminal chart of money contributed against the strategy and the index, before and
after tax, with a text sparkline of each line, and the goal line. Run MC adds the quantile table of after-tax terminal wealth,
the probability of beating the index and of missing the goal, a fan chart, the sequence-of-returns table and text histograms.
In the browser chart panel the Plan tab draws the same as Plotly figures. Commands `plan` and `plan_mc` ([PROTOCOL.md](PROTOCOL.md)).

## Screen tab (key `0`)
Define a screen and see what it does before running anything ([../docs/SCREEN_BUILDER.md](../docs/SCREEN_BUILDER.md)). It is the first tab,
and a new session (no scenarios) opens on it. The left column is the form: name, note, dataset (read-only) and the as-of date with
< year / year > buttons; the universe (top N by a numeric column, include sectors, exclude sectors, pick count n); and four tables, Derived
columns (`name = expression`), Include criteria, Exclude criteria and Rank (column, asc or desc), each with Add / Update / Remove (select a
row with Enter to edit it; Enter in an expression box adds it). The right column has the buttons (Load, New, Save, Add as scenario, Run), the
status line (save paths, errors), the funnel preview (one row per step: expression, rows in, rows out, and for include criteria how many names it
would keep alone; amber where a criterion keeps every name or drops every name, red rows for errors with the message and a did-you-mean
suggestion), the picks, and the schema panel (every column with group, type, coverage, range and description; type in the filter box; Enter on
a row inserts the name into the expression box you were in) with the expression helpers (`pct`, `q`, `z`, `log`, `sqrt`, `abs`, `isna`,
`coalesce`). The preview runs by itself about 0.6 s after an edit; F5 or the Preview button runs it now. Include keeps the rows where the
expression is true, exclude drops the rows where it is true, rank sums the ranks (best first); a missing value (NaN) fails an include
criterion but is not dropped by an exclude criterion. Ctrl+S (or Save) writes `<name>.json` to `SCREENLAB_SCREENS` (default
`~/.screenlab/screens`) and registers the screen, so it is in the Add dialog now and after a restart. Add as scenario saves, adds and selects
the scenario and shows Compare; Run also runs it. Load lists the spec screens (the built-in ones too) and fills the form. The form is shared
state (`analyses.screen_form`), so the tab works in `--serve` and `--remote` sessions. Commands `set_screen_form`, `screen_schema`,
`screen_preview`, `screen_save`, `screen_load`, `screen_add` ([PROTOCOL.md](PROTOCOL.md)).

Stages (SB3): a screen can have several stages (a funnel such as Frugal Dividend: payers, the 50 lowest volatility, the 10 lowest P/E). A stage bar
above the form has `+ Add stage` (and, with two or more stages, one button per stage and `- Remove stage`); with one stage only `+ Add stage`
shows. Each stage has its own Derived, Include, Exclude and Rank tables and, except the last, a Keep box (how many go on to the next stage; the
last stage's cut is the pick count). The universe block belongs to stage 1 (a later stage shows it only if the loaded screen gives it one), and
the funnel is grouped under a heading per stage. Graham tests of a loaded screen are not editable and show as one read-only line. The help
shows a "Starting points" line with every Globe and classic screen and what it does; **Load** groups them as "Globe and Mail", "Classic", "Your saved
screens". A loaded built-in carries its rebalance months and hold rule (bluechip: annual, keep within 2x, 25% band) into Add as scenario and Run;
the scenario editor has the two hold-rule boxes. Commands take `stage`, `add_stage`, `remove_stage`, `select_stage`.

## Refining a scenario
Select a scenario, edit a field, press **F2** (or the Apply button, or Enter in a field). Apply never edits:
it creates `dogs v2` from `dogs` with the changed fields, shows a line such as
`dogs -> dogs v2: cost_bps 10.0 -> 30.0, n None -> 15`, runs it, and Compare shows `dogs`, `dogs v2` and the
passive index together. Versions are kept until you delete them (`d`).

## Keys
`a` add, `c` clone, `r` run selected, `R` run all not yet run, `d` delete, `x` toggle trend exit (then F2),
`F2` Apply, `m` Monte Carlo (selected scenario; price-rank screens only), `t` timing study, `p` picks,
`s` save the screen as ScreenSpec JSON, `e` export Compare as markdown + CSV, `w` charts in the browser,
`D` Data tab, `A` Accounts tab, `0` Screen tab (`F5` preview, `Ctrl+S` save the screen), `T` light/dark, `1`-`9` tabs,
`?` help, `q` quit. The mouse works too. In a field, `F2` or Enter applies.

## Guardrails (trials, frozen scenarios, hypotheses)
* **Trials / DSR** columns in Compare (and in the browser chart panel's Compare table): Trials = parameter sets run in the
  row's family (dataset + screen, plus `@H1` when linked), or the run store's `trials_in_family` when larger; DSR = deflated
  Sharpe of the row against the Sharpe the luckiest of that many skill-less variants would show (`stats.expected_max_sharpe`,
  `probabilistic_sharpe`; variance across the family's Sharpes, floored at 1/(obs-1)). The header and `view` show
  "trials this session: N". Removing a version never lowers a count.
* **`f` / `freeze`**: lock the version; editor read-only (Apply refuses, `c` clones). Versions or clones derived from it are
  candidates (marked `candidate`), each adds one trial to the frozen row. `unfreeze` exists, logs a line, and leaves the
  candidates flagged.
* **Hypotheses tab (`H`)**: parses `research/PREREGISTERED.md` (override with env `SCREENLAB_PREREG`). `l` / `link`
  attaches the selected scenario (all its versions) to a hypothesis id; the tab shows met / not met for its latest result.

## Control endpoint (for agents and scripts)
While running, the app serves `http://127.0.0.1:8765` (`GET /health /state /view`, `POST /command`). Set
`SCREENLAB_TUI_TOKEN` before starting to require a token. The contract is [PROTOCOL.md](PROTOCOL.md); the MCP tools
`tui_*` (server/tui_tools.py) forward to it. Try it:
```
curl -s localhost:8765/health
curl -s -X POST localhost:8765/command -d '{"cmd":"add_scenario","args":{"screen":"dogs","run":true}}'
curl -s localhost:8765/view
```
In browser mode (`--serve`) the endpoint belongs to the parent process, see the next section.

## Browser mode: one shared session
`textual-serve` starts one app process per browser tab. So that the tabs, the chart panel and the control endpoint do not
drift apart, `screenlab-tui --serve` keeps **one `Session` in the parent process** together with the control endpoint
(always on in this mode; `--control-port 0` picks a free port). Each app process is started as
`screenlab-tui --remote http://127.0.0.1:<port>` and holds a `RemoteSession` ([remote.py](remote.py)): the same interface as
`Session` (`command`, `snapshot`, `view`, `seq`, `poll`, `await_jobs`) forwarded over HTTP, with change detection by
long-poll (`GET /seq?after=`) and `GET /state` only when the seq moved. So every tab shows the same scenarios, selected
scenario, active tab and theme, a scenario added in one tab appears in the others, and the charts, curl and the MCP `tui_*`
tools act on that same session. Jobs and the run store live in the parent. Set `SCREENLAB_TUI_TOKEN` before starting and
the children use it too (inherited environment). In the terminal nothing changes: a local `Session`, endpoint optional.
`app.py` has no HTTP code; it works against either session type. A tab that is closed leaves the session untouched;
stopping `--serve` ends it.

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
  the position is remembered). `w` shows the URL there instead of opening a browser. Every terminal inside that page is
  an app process on the same shared session (previous section), so the charts and all terminals show the same scenarios.
* With `SCREENLAB_TUI_TOKEN` set, open the page as `/charts?token=<token>` (the page passes it on to its requests).
* Needs the control endpoint (`--control-port 0` turns the charts off). Histograms are only available for Monte Carlo
  runs computed in this session (a result reloaded from the run store has the table and fan only).
* Endpoints: `GET /charts`, `GET /charts/data?tab=curves|compare|timing|montecarlo|exit`, `GET /theme`,
  `GET /events`, `GET /` (with `--serve`). Code: `web.py` (data and binning), `static/charts.html`,
  `static/index.html`.

## Code
`session.py` state and commands (no Textual import, used by the tests), `remote.py` the HTTP client with the same
interface, `app.py` the Textual app and `main`, `control.py` the HTTP endpoint (and `HeadlessHost` for the parent), `web.py` + `static/` the browser charts. Tests: `python -m pytest screenlab/tests/test_tui.py`.
