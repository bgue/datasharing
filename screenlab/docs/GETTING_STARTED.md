# Getting started with screenlab

For: an investor who is comfortable typing a few commands in a terminal but is not a programmer.
Time: about 10 minutes, most of it waiting for downloads.

Every command below was run on Linux (Python 3.13, a brand-new virtual environment) unless marked
**(not executed here)**. The macOS commands are identical to Linux. The Windows commands are the
standard documented equivalents; they were not run in this environment.

## 0. The 30-second picture

screenlab runs rules-based stock screens (for example "Dogs of the Dow") as equal-weight portfolios,
backtests them against a passive index, and prints a table. You can drive it four ways, all installed by
the same command:

| Way | You type | Good for |
|---|---|---|
| Command line | `screenlab-run ...` | quick comparisons |
| Notebook | `jupyter lab ...` | clicking through, plots, no code writing |
| REST API | `screenlab-rest`, then a browser page | tools and scripts |
| MCP server | registered once in Claude | asking Claude to run backtests for you |

A **virtual environment** ("venv") is just a private folder of Python packages for this one project. It
keeps screenlab from disturbing anything else on your computer, and you can delete it any time.

## 1. What you need

1. **Python 3.10 or newer** (3.13 was used for the checks here). Check:
   ```
   python3 --version
   ```
   On Windows the command is `py --version` or `python --version`. Install from https://www.python.org/downloads/
   if it is missing or older than 3.10; on Windows tick "Add python.exe to PATH" in the installer.
2. **git** (to download and later update the code), or the zip file of the code, which needs no git.
3. About 1.5 GB of free disk for the full install (the notebook and Jupyter are most of that) and an
   internet connection for the install step. After that everything runs offline.

## 2. Get the code

With git (recommended; lets you update later):
```
git clone -b screenlab https://github.com/bgue/datasharing.git
cd datasharing
```
Without git: on the GitHub page choose the `screenlab` branch, click Code, then Download ZIP, unzip it,
and open a terminal in the unzipped `datasharing` folder (the one that contains `pyproject.toml`).

All commands below are run from that folder. Check you are in the right place:
```
ls pyproject.toml
```
(Windows: `dir pyproject.toml`.)

## 3. Create and activate the virtual environment

**macOS / Linux**
```
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell** (not executed here)
```
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```
If PowerShell answers "running scripts is disabled on this system", allow scripts for your user once, then
retry the activate line:
```
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**Windows cmd.exe** (not executed here)
```
py -3 -m venv .venv
.venv\Scripts\activate.bat
```

When activation worked, your prompt starts with `(.venv)`. **Every new terminal window needs the activate
line again** (the `cd datasharing` too). That is the most common reason for "command not found".

Upgrade pip first; an old pip is the second most common problem:
```
python -m pip install --upgrade pip
```

## 4. Install

Full install (everything: bundled S&P 500 data, notebook, REST and MCP servers, tests, Bayesian search):
```
pip install -e ".[all]"
```
Took about 1.5 minutes on a fast connection. The `-e` ("editable") means the folder you cloned is the
installed program, so `git pull` updates it with no reinstall. The quotes matter in zsh (macOS).

Lighter variants (choose one; you can add more later by running another `pip install -e ".[...]"`):

| Command | You get | Needs it for |
|---|---|---|
| `pip install -e .` | pandas, numpy, the CLI, the SDK, synthetic data | `screenlab-run` on synthetic data and your own CSVs |
| `pip install -e ".[data]"` | adds skfolio, statsmodels | bundled real S&P 500 data and US macro series |
| `pip install -e ".[server]"` | adds fastapi, uvicorn, httpx, mcp | REST API and MCP server |
| `pip install -e ".[notebook]"` | adds jupyterlab, ipykernel, matplotlib, nbclient, nbformat | the notebook |
| `pip install -e ".[bayes]"` | adds optuna | `bayes_search` (see `screenlab/docs/BAYESIAN_SWEEPS.md`) |
| `pip install -e ".[dev]"` | adds pytest, pyyaml | running the tests |

Combine extras with commas: `pip install -e ".[data,notebook]"`.

Check it worked:
```
python -c "import screenlab; print('ok')"
```
It prints `ok`. `screenlab-run --version` prints the installed version (for example `screenlab 0.1.0`).

## 5. First run

### 5a. Synthetic data (no downloads)
```
screenlab-run --freq monthly --only dogs,screaming_value
```
(`python -m screenlab.run --freq monthly --only dogs,screaming_value` does the same.) It takes about 1.5
seconds and prints:

```
Backtest 2001-02-28 to 2024-12-31, rebalance every 12m, benchmark SYN_CAPWT
Data: synthetic, 2000-01-31..2024-12-31, 250 names, benchmark SYN_CAPWT (total_return), PIT yes, delisted yes (51), survivorship +0.1%/yr

                 CAGR    Vol Sharpe   MaxDD Worst12m   AvgN BeatBenchYrs Excess/yr Turnover Costs/yr
SYN_CAPWT        6.4%  18.5%   0.43  -53.6%   -33.7%                                                
screaming_value  3.8%  20.3%   0.29  -73.4%   -36.6%  10.00        37.5%     -2.6%    78.1%     0.4%
dogs             3.6%  19.9%   0.27  -68.7%   -39.2%  10.00        29.2%     -2.8%    61.4%     0.3%

Current picks as of 2024-12-31
  dogs                 T011, T121, T003, T161, T087, T134, T183, T200, T209, T150
  screaming_value      T167, T022, T218, T148, T034, T110, T080, T151, T038, T237
```
The synthetic market is random and made up, so these numbers mean nothing about real stocks; it exists to
test the machinery. Drop `--freq monthly` for daily data (slower, more realistic for timing studies).

### 5b. Real S&P 500 sample data
```
screenlab-run --data sp500 --only momentum_12_1,low_vol --rebalance 1
```
Needs the `data` extra (included in `all`); without it the command stops with a message telling you to
install it. Takes about 10 seconds and prints (trimmed):
```
Backtest 1991-02-04 to 2022-12-28, rebalance every 1m, benchmark SP500 +2.0% div (approx. TR)
Data: sp500_bundled, 1990-01-02..2022-12-28, 20 names, benchmark SP500 (approx_total_return: price index + assumed 2.0%/yr dividends), PIT n/a, delisted no, survivorship +7.9%/yr
WARNING: benchmark SP500 is an approximate total return: the price index plus an assumed constant 2.0%/yr dividend yield, ...
WARNING: survivorship score +7.9%/yr: ... results are likely flattered.

                               CAGR    Vol Sharpe   MaxDD Worst12m  AvgN BeatBenchYrs Excess/yr Turnover Costs/yr
momentum_12_1                 22.8%  23.8%   0.98  -47.2%   -40.6%  5.00        81.2%     12.9%    25.0%     1.5%
low_vol                       12.7%  15.3%   0.86  -35.1%   -28.3%  5.00        56.2%      2.8%     7.2%     0.4%
SP500 +2.0% div (approx. TR)   9.9%  18.4%   0.61  -55.5%   -47.8%
```
The same in Python, if you prefer (`--data sp500` is the command-line form of this):
```python
from screenlab.realdata.bundled import sp500_lab
lab = sp500_lab()
rep = lab.backtest(["momentum_12m", "low_vol"], rebalance_months=12, cost_bps=20)
print(rep)
print(lab.picks("momentum_12m"))
```
The bundled sample has only 20 hand-picked large companies that are still around. It only supports the
price-only screens (`momentum_12_1`, `momentum_6m`, `momentum_12m`, `low_vol`); asking for `dogs` here gives
`KeyError: 'unknown screen: dogs'`. For real fundamentals see `screenlab/realdata/ACQUIRE.md`.

### 5c. How to read the output
- **The `Data:` line (the data card)** says what you are testing on. `synthetic` = fake. `PIT yes/n/a` =
  whether fundamentals were known at the time (point in time). `delisted yes (51)` = dead companies are
  included; `delisted no` = only survivors, so results are flattered. `survivorship +10.1%/yr` is a measured
  estimate of how much.
- **WARNING lines** are the program refusing to let you fool yourself. The two above say: the benchmark
  leaves out dividends, and the data is survivors only. Take them seriously.
- **Table columns.** `CAGR` compound annual return; `Vol` annualised volatility; `Sharpe` return per unit
  of volatility; `MaxDD` worst peak-to-trough fall; `Worst12m` worst rolling 12 months; `AvgN` average number
  of holdings; `BeatBenchYrs` share of years the portfolio beat the benchmark; `Excess/yr` average yearly
  return above the benchmark; `Turnover` share of the portfolio traded per year; `Costs/yr` yearly drag from
  trading costs. The first row (`SYN_CAPWT`, `SP500`) is the passive benchmark.
- **Current picks** are the names the screen would hold today.

### 5d. Timing study
Does the start month matter? This runs each possible start offset:
```
screenlab-timing --freq monthly --only dogs --stride 3
```
About 3 seconds. Prints the spread of results across start dates (best and worst "anniversary") and a blended
"tranche" result.

Helpful flags for both commands (`--data sp500` switches to the real sample): `screenlab-run --help`; examples: `--rebalance 6`, `--cost-bps 20`,
`--mer 0.002`, `--set graham_defensive.max_pe=18`, `--graham-report`, `--csv fundamentals.csv prices.csv`.

## 6. The notebook
```
jupyter lab screenlab/notebooks/explore_runs.ipynb
```
A browser tab opens. Choose Run, then Run All Cells. It takes about 55 seconds, runs a few backtests, stores
them in a small database, and shows tables and charts. It writes `screenlab/notebooks/runs.sqlite` (git
ignores it). Close with Ctrl+C in the terminal. Details: `screenlab/notebooks/README.md`.

To run it without a browser (this is how it was verified; the original file is left untouched):
```
mkdir -p /tmp/nbtest && cp screenlab/notebooks/explore_runs.ipynb /tmp/nbtest/ && cd /tmp/nbtest
jupyter nbconvert --to notebook --execute explore_runs.ipynb --output out.ipynb
```

## 7. REST API and its interactive docs page
```
screenlab-rest
```
(or `python -m screenlab.server.rest --port 8000`). Leave this terminal open; the server runs until you press
Ctrl+C. Open http://127.0.0.1:8000/docs in a browser: a page listing every endpoint where you can click "Try it
out". From a second terminal (activate the venv there too is not needed for curl):
```
curl http://127.0.0.1:8000/labs
curl -X POST http://127.0.0.1:8000/labs/synthetic/backtest -H 'content-type: application/json' \
  -d '{"screens":["dogs"],"config":{"rebalance_months":6,"cost_bps":20},"max_points":5}'
```
The first returns `{"default":"sp500","labs":[{"name":"synthetic",...},{"name":"sp500",...}]}`; the second
returns JSON with a `table` of the same numbers as the command line. The server listens on this computer only
(127.0.0.1); use `screenlab-rest --host 0.0.0.0` only if you understand that exposes it to your network.
Another port: `screenlab-rest --port 8001`. All endpoints: `screenlab/server/README.md`.

## 7b. Interactive terminal app (TUI)
```
screenlab-tui --lab synthetic
```
A full-screen app in the terminal: press `a` to add a scenario (pick a screen), `r` to run it, change a number in the
editor on the left and press `F2` (Apply) to get a new version, for example `dogs v2`, which is run and shown beside
`dogs` and the grey passive index in the Compare tab (the Curves tab draws them on a log scale in the same
colours). `?` lists the keys and `q` quits. To use it in a browser instead: `screenlab-tui --serve`, then open
http://localhost:8000 (`--port` changes it). While it runs it also listens on 127.0.0.1:8765 so that Claude (the
`tui_*` MCP tools) can drive the same screen; in a terminal `--control-port 0` turns that off. Details: `screenlab/tui/README.md`.

### Charts in the browser
Press `w` in the TUI: your browser opens interactive charts (hover for values, drag to zoom, click the legend to hide
a scenario, a PNG button on each chart) with tabs Curves, Compare, Timing, Monte Carlo and Exit. They show the same
scenarios in the same colours (the passive index is grey), take the colours of the TUI theme, and redraw by themselves
when you run or change something in the terminal. The page loads Plotly.js from the internet; offline, set
`SCREENLAB_PLOTLY_JS` to a local `plotly.min.js` file before starting. With `screenlab-tui --serve` the footer shows
a page that puts the terminal and the charts side by side (drag the divider). In that mode the program keeps one
shared session: every browser tab running the terminal, the chart panel and Claude's `tui_*` tools show and change the
same scenarios, so nothing drifts apart when you open a second tab. In a plain terminal the control endpoint is
optional (on by default; `--control-port 0` turns the charts off). Details: `screenlab/tui/README.md`.

### Data tab: datasets and free downloads
Press `D` for the Data tab. It lists the datasets the app can use, each with its data card and warnings (a price-index
benchmark, survivorship), and a **Download free datasets** form: type your name and email for the SEC (it requires a
contact), tick the sources (SEC, Bank of Canada, FRED, Stooq, Statistics Canada), and press the button. It runs as a
normal job (progress bar at the top), then shows what was downloaded; a dataset it builds shows up in the list and in the
Dataset choice of the scenario editor. The form also lists the website names (hosts) the downloads need, which is what to
allow if your environment restricts outbound connections. If the downloader is not installed the tab says so.

## 8. Connect the MCP server to Claude
The MCP server lets Claude call screenlab itself ("backtest dogs vs screaming value on monthly data"). It
talks to Claude over standard input/output; you never run it by hand. You need the **full path to the venv's
Python** so Claude does not use some other Python.

Find the path (from the `datasharing` folder, venv active):
```
python -c "import sys; print(sys.executable)"
```
Example result on Linux/macOS: `/home/you/datasharing/.venv/bin/python`. On Windows it looks like
`C:\Users\you\datasharing\.venv\Scripts\python.exe` **(not executed here)**.

**Claude Code** (one line, then restart the Claude Code session). Linux/macOS:
```
claude mcp add screenlab -- /home/you/datasharing/.venv/bin/python -m screenlab.server.mcp_server
```
Windows **(not executed here)**:
```
claude mcp add screenlab -- C:\Users\you\datasharing\.venv\Scripts\python.exe -m screenlab.server.mcp_server
```
Check with `claude mcp list`. Remove with `claude mcp remove screenlab`.

**Claude Desktop** **(not executed here)**: open Settings, Developer, Edit Config. This opens
`claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`).
Add this, with your own path (on Windows write backslashes doubled: `C:\\Users\\you\\...`):
```json
{
  "mcpServers": {
    "screenlab": {
      "command": "/home/you/datasharing/.venv/bin/python",
      "args": ["-m", "screenlab.server.mcp_server"]
    }
  }
}
```
If the file already has an `mcpServers` section, add only the `"screenlab": {...}` entry inside it. Quit and
reopen Claude Desktop fully.

Verified here: the server answers the MCP `initialize` handshake with `serverInfo.name` = `screenlab`.
`screenlab-mcp --help` (or `python -m screenlab.server.mcp_server --help`) prints usage and exits. Without
`--help` the program starts the server and waits silently for input from a client; if you ever start it by
hand, press Ctrl+C or Ctrl+D.

On macOS / Linux, with the venv active, you can let the shell fill in the path: `claude mcp add screenlab --
"$(which python)" -m screenlab.server.mcp_server`.

## 9. Where results are stored, and how to turn it off
The REST and MCP servers record every run (settings, metrics, a data fingerprint) in a SQLite file:
`~/.screenlab/runs.sqlite` (macOS/Linux: your home folder; Windows: `C:\Users\you\.screenlab\runs.sqlite`).
Nothing leaves your machine. This is also how repeated identical runs come back instantly.

Change the location or turn it off with the environment variable `SCREENLAB_STORE`:
```
export SCREENLAB_STORE=/some/folder/my_runs.sqlite     # another file
export SCREENLAB_STORE=none                            # no storage at all ("", off also work)
```
Windows PowerShell **(not executed here)**: `$env:SCREENLAB_STORE = "none"`; cmd: `set SCREENLAB_STORE=none`.
These last until the terminal closes. For Claude Desktop add an `"env": {"SCREENLAB_STORE": "none"}` entry next
to `command`. To start clean, delete the file. The command-line tools and plain SDK do not write there unless
you set `SCREENLAB_STORE`.

## 10. Run the tests
```
pytest -q screenlab/tests -p no:cacheprovider
```
Needs the `dev` extra (included in `all`). Takes roughly 2 minutes. Green means the math is intact; the
tests include "golden" reference results for the synthetic and bundled data. Add `-x` to stop at the first
failure.

## 11. Update, uninstall
Update (venv active, in the `datasharing` folder):
```
git pull
pip install -e ".[all]"
```
The second line is only needed if dependencies changed. Without git: download a new zip, replace the folder,
and run the install line again.

Leave the venv: `deactivate`. Uninstall completely: delete the `datasharing` folder (which holds `.venv`), and
optionally `~/.screenlab/`. Remove only the program but keep the venv: `pip uninstall screenlab`.

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `screenlab-run: command not found` (Windows: "not recognized") | The venv is not active. Run the activate line from section 3; the prompt must show `(.venv)`. Or call it directly: `.venv/bin/screenlab-run` (Windows `.venv\Scripts\screenlab-run.exe`). |
| Using **uv**: `No module named pip`, then `error: externally-managed-environment` | uv environments have no pip on purpose. Install with uv instead: `uv pip install -e ".[all]"`, or skip activation entirely with `uv run --all-extras screenlab-run --freq monthly --only dogs`. Need a newer Python? `uv venv --python 3.12`. Want pip inside anyway? `uv venv --seed`. |
| Ubuntu / Debian / WSL: `.venv/bin/python: No module named pip`, then `error: externally-managed-environment` | The system Python is missing its venv/pip packages, so the venv was created without pip and `pip` fell through to the protected system Python. Fix: `deactivate`, `rm -rf .venv`, `sudo apt update && sudo apt install -y python3-venv python3-pip`, then `python3 -m venv .venv && source .venv/bin/activate`, `python -m pip install --upgrade pip`, `python -m pip install -e ".[all]"`. Always use `python -m pip` so the venv's pip is the one used. On WSL, work under your Linux home (`~/screenlab`), not `/mnt/c/...`: installs and tests are several times faster there. |
| `ModuleNotFoundError: No module named 'screenlab'` | Same cause, or you installed in another venv. Activate, then `pip install -e ".[all]"` from the `datasharing` folder. |
| `ERROR: Package 'screenlab' requires a different Python: 3.9.x not in '>=3.10'` | Python too old. Install 3.10+ and recreate the venv: delete `.venv`, then `python3.12 -m venv .venv` (Windows `py -3.12 -m venv .venv`). |
| `ERROR: file:///... does not appear to be a Python project` or "neither setup.py nor pyproject.toml" | You are in the wrong folder. `cd` into the one that contains `pyproject.toml` (not the inner `screenlab` folder). |
| pip errors about "editable mode" or `setuptools` | Old pip. `python -m pip install --upgrade pip`, retry. |
| `zsh: no matches found: .[all]` | macOS zsh: keep the quotes, `pip install -e ".[all]"`. |
| PowerShell: "running scripts is disabled" | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, answer Y, activate again. Or use cmd.exe. |
| `ImportError: skfolio is not installed` | Install the data extra: `pip install -e ".[data]"`. |
| REST: `[Errno 98] error while attempting to bind on address ('127.0.0.1', 8000): address already in use` (Windows errno 10048) | Port 8000 is taken (often an earlier `screenlab-rest` still running). Stop it with Ctrl+C in its terminal, or use `screenlab-rest --port 8001`. |
| REST/MCP: `uvicorn is not installed` or `No module named 'mcp'` | `pip install -e ".[server]"`. |
| `KeyError: 'unknown screen: dogs'` on the S&P 500 lab | That lab has only price-only screens (section 5b). |
| Notebook says kernel not found / missing module | Start Jupyter from the venv (`jupyter lab` after activating) so it uses the venv's Python. |
| Windows: pip fails with a path-too-long error (`OSError: [Errno 2] No such file or directory` on a very long path) | Windows long-path limit. Clone to a short path such as `C:\src\datasharing`, and enable long paths: run PowerShell as administrator, `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force`, reboot. **(not executed here)** |
| Claude Desktop does not show screenlab | Use the venv's full python path in the JSON, double the backslashes on Windows, quit Claude Desktop fully and reopen, and check the file is valid JSON. |
| Run prints the table twice and exits with status 1 (seen only when using the original `main` directly as a console script) | Fixed by the `screenlab-run` and `screenlab-timing` wrappers in `screenlab/_cli.py`. |
| Tests or the CLI fail with `ImportError: cannot import name ...` straight after `git pull` | Normal if you pulled while code was being changed; pull again later, or use the last commit that passes. |

Rows above that show a pip or Windows message word-for-word are the standard messages and were not reproduced here. If something fails that is not here, copy the last 15 lines of the error and ask.

## 13. Where next
See `screenlab/docs/README.md` for the map of every document. Start with `screenlab/README.md` (what you can do,
and how to read the output), then `screenlab/docs/SCREENS.md` for the screens and `screenlab/docs/REFERENCE.md`
for every flag and the Python API.
