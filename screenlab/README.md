# screenlab

screenlab backtests rules-based stock screens as equal-weight portfolios, the way The Globe and Mail
publishes its screen portfolios: pick the stocks by a written rule, hold them until the next rebalance,
then repeat. Every result is set against a **passive, buy-and-hold index**, so the question is always
"did the rule beat just owning the index, after costs?" You can drive it from the command line, a
notebook, a REST API, Claude (MCP) or Python.

> **State of the data (read this first).** The only data bundled with the code is 20 large S&P 500 stocks
> that all survived to 2022, plus a price-only S&P 500 index, and a made-up (synthetic) market for testing.
> Results on these show how the machinery works, **not whether a screen has an edge**: survivors flatter
> every strategy (about +10 points a year here) and the price-only index understates the benchmark.
> The program prints warnings to that effect. For real, point-in-time data see
> [realdata/ACQUIRE.md](realdata/ACQUIRE.md); for an honest list of what the numbers can and cannot
> support, see [CRITIQUE.md](CRITIQUE.md).

## Quick start (5 minutes)
Needs Python 3.10 or newer and about 1.5 GB of disk. Details and troubleshooting:
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

**1. Get the code and open a terminal in its folder** (the one containing `pyproject.toml`).
```
git clone -b screenlab https://github.com/bgue/datasharing.git
cd datasharing
```
No git? Download the ZIP of the `screenlab` branch from GitHub, unzip it and open a terminal there.

**2. Make a private Python environment ("venv") and switch it on.** macOS / Linux:
```
python3 -m venv .venv && source .venv/bin/activate
```
Windows PowerShell:
```
py -3 -m venv .venv; .venv\Scripts\Activate.ps1
```
Your prompt now starts with `(.venv)`. Each new terminal window needs the activate step again.

**3. Install** (about 1.5 minutes; `.[all]` includes the sample data, notebook, servers and tests):
```
python -m pip install --upgrade pip
pip install -e ".[all]"
```

**4. First run** on the made-up market (takes a couple of seconds):
```
screenlab-run --freq monthly --only dogs,screaming_value
```

**5. The real S&P 500 sample** (20 stocks, rebalanced monthly):
```
screenlab-run --data sp500 --only momentum_12_1,low_vol --rebalance 1
```

**6. Open the notebook** (a browser tab opens; choose Run, then Run All Cells; about a minute):
```
jupyter lab screenlab/notebooks/explore_runs.ipynb
```

**7. Start the REST API**, then open <http://127.0.0.1:8000/docs> in a browser (Ctrl+C stops it):
```
screenlab-rest
```

**8. Let Claude drive it** (Claude Code; macOS / Linux, then restart the Claude Code session):
```
claude mcp add screenlab -- "$(which python)" -m screenlab.server.mcp_server
```
Windows and Claude Desktop: see section 8 of [GETTING_STARTED.md](docs/GETTING_STARTED.md).

Check the install any time with `screenlab-run --version`. `screenlab-run --help` lists every option.

## How to read the output
This is the command from step 5, trimmed:
```
Backtest 1991-02-04 to 2022-12-28, rebalance every 1m, benchmark SP500
Data: sp500_bundled, 1990-01-02..2022-12-28, 20 names, benchmark SP500 (price), PIT n/a, delisted no, survivorship +10.1%/yr
WARNING: benchmark SP500 is a price index (dividends left out) but the portfolios are total return, ...
WARNING: survivorship score +10.1%/yr: ... results are likely flattered.

                CAGR    Vol Sharpe   MaxDD Worst12m  AvgN BeatBenchYrs Excess/yr Turnover Costs/yr
momentum_12_1  22.8%  23.8%   0.98  -47.2%   -40.6%  5.00        81.2%     15.0%    25.0%     1.5%
low_vol        12.7%  15.3%   0.86  -35.1%   -28.3%  5.00        65.6%      4.9%     7.2%     0.4%
SP500           7.8%  18.4%   0.50  -56.8%   -48.8%

Current picks as of 2022-12-28
  momentum_12_1        XOM, CVX, RRC, MRK, LLY
```
- **`Data:` line (the data card)** says what you tested on: synthetic or real, whether fundamentals were
  known at the time (`PIT`), whether dead companies are included (`delisted`), and a measured
  `survivorship` estimate in points a year.
- **`WARNING` lines** are the program refusing to let you fool yourself. Take them seriously.
- **Columns.** `CAGR` compound annual return; `Vol` volatility; `Sharpe` return per unit of risk;
  `MaxDD` worst peak-to-trough fall; `Worst12m` worst rolling year; `AvgN` average holdings;
  `BeatBenchYrs` share of years the portfolio beat the index; **`Excess/yr`** average yearly return above the
  passive index (the number that matters, after costs); `Turnover` share traded per year; `Costs/yr` the
  yearly drag from trading costs. The row with no `AvgN` (`SP500`) is the passive index itself.
- **Exit rows.** With an exit rule (see below) two more columns appear: `TimeOut`, the share of time the
  portfolio sat out in cash, and `Switches/yr`, how often it moved in or out. Rows ending `+exit` are the
  same screen with the exit rule applied.
- **Current picks** are the stocks the screen would hold today.

## What you can do
Every command works on the synthetic data (add `--data sp500` for the real sample). Detail is in
[docs/REFERENCE.md](docs/REFERENCE.md) (all flags, benchmark, data files) and [docs/SCREENS.md](docs/SCREENS.md).

**Compare screens, costs and rebalance schedules.** The built-in list is in [docs/SCREENS.md](docs/SCREENS.md).
```
screenlab-run --freq monthly --only dogs,piotroski --rebalance 6 --cost-bps 20 --mer 0.002
```

**Test timing luck**: does the start date matter? Reruns a screen from every possible rebalance day.
```
screenlab-timing --freq monthly --only dogs --stride 3
```

**Change a rule** without writing code, e.g. loosen Graham's P/E limit and see which stocks survive each test:
```
screenlab-run --freq monthly --only graham_defensive --set graham_defensive.max_pe=18 --graham-report
```

**Define your own screen in JSON.** Save this as `my_screen.json`, then run it:
```json
{"name": "cheap_yield", "n": 10,
 "universe": {"top": 100, "exclude_sectors": ["Fin"]},
 "filters": ["pe < 15", "div_yield > 0.03"],
 "rank": {"div_yield": "desc", "pb": "asc"}}
```
```
screenlab-run --freq monthly --screen-file my_screen.json --only cheap_yield,dogs
```
Keys, multi-stage funnels and the Globe and Mail portfolios: [docs/SCREENS.md](docs/SCREENS.md).

**Add an exit rule** (go to cash when the market looks weak, come back when it clears). Add `--exit-compare`
to see each screen with and without it. Whether this helps is studied in
[research/CALIBRATION_SP500.md](research/CALIBRATION_SP500.md): it cuts the worst losses, not returns.
```
screenlab-run --freq monthly --only dogs --exit "TrendBreak,Breadth" --exit-compare
```
Signals, policy options and calibration: [docs/EXITS.md](docs/EXITS.md).

**Use Python** (the same engine; full tour in [docs/REFERENCE.md](docs/REFERENCE.md)):
```python
from screenlab import Lab
lab = Lab.synthetic(freq="ME")                       # or: from screenlab.realdata.bundled import sp500_lab
rep = lab.backtest(["dogs", "screaming_value"], rebalance_months=6, cost_bps=20)
print(rep)                                           # the table; rep.table and rep.curves are raw data
print(lab.picks("dogs"))                             # today's holdings with key ratios
```

**Store and compare runs; run long jobs in the background.** The REST and MCP servers record every run in
`~/.screenlab/runs.sqlite`; in Python, give the lab a store and submit jobs:
```python
from screenlab import Lab, SQLiteStore
lab = Lab.synthetic(freq="ME")
lab.name, lab.store = "synthetic", SQLiteStore("runs.sqlite")
lab.backtest(["dogs"], rebalance_months=6)           # recorded
job = lab.submit("timing", screen="dogs", stride=3)  # runs in the background
res = job.result()                                   # wait for it
print(job.status, job.cache_hit)                     # done False; run the identical call again: done True, instant
print(res.table.head())
```
How runs are keyed, cached and queried: [docs/SERVERS_AND_STORAGE.md](docs/SERVERS_AND_STORAGE.md). The
notebook walks through it with plots.

**Use real data.** Your own CSV files: `screenlab-run --csv fundamentals.csv prices.csv [indices.csv]`
(file layout in [docs/REFERENCE.md](docs/REFERENCE.md)); where to buy or download real Canadian and US data:
[realdata/ACQUIRE.md](realdata/ACQUIRE.md).

## Where things are
| I want to... | Read |
|---|---|
| install, fix an error, connect Claude | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| see the map of all documents | [docs/README.md](docs/README.md) |
| every flag, the benchmark rules, the data files, backtest mechanics | [docs/REFERENCE.md](docs/REFERENCE.md) |
| the built-in screens, spec format, Globe and Mail portfolios | [docs/SCREENS.md](docs/SCREENS.md) |
| exit signals, exit policy, calibration | [docs/EXITS.md](docs/EXITS.md) |
| REST, MCP, background jobs, the run database | [docs/SERVERS_AND_STORAGE.md](docs/SERVERS_AND_STORAGE.md), [server/README.md](server/README.md) |
| what the results can and cannot support | [CRITIQUE.md](CRITIQUE.md) |
| what is done and what is next | [ROADMAP.md](ROADMAP.md) |

Run the tests with `pytest -q screenlab/tests -p no:cacheprovider` (about two minutes).
