# Documentation map

Paths are relative to the `screenlab/` package folder unless stated. Read top to bottom if you are new: install first, then the front-page README, then whatever you need.

| Document | For | Read it when |
|---|---|---|
| [docs/GETTING_STARTED.md](GETTING_STARTED.md) | Anyone installing for the first time, no programming needed | You have just got the code and need a working venv, a first run, the notebook, the REST API or the Claude connection. |
| [README.md](../README.md) | Everyone | The front page: what screenlab is, a 5-minute quick start, how to read the output, and a menu of things to do. |
| [docs/REFERENCE.md](REFERENCE.md) | Users who want the details | Every command-line flag, the benchmark rules, the CSV data contract, backtest mechanics, timing studies, Graham tests, the `Lab` SDK tour, and research features (construction, statistics and sweeps, extended metrics). |
| [docs/SCREENS.md](SCREENS.md) | Users of the screens | You want the list of built-in screens, how to define your own as JSON or in Python, or the Globe and Mail portfolios with their source articles and what our data cannot reproduce. |
| [docs/EXITS.md](EXITS.md) | Users testing exit rules | You want the exit signals, the exit policy (where the money goes, when it returns), the extra exit inputs and walk-forward calibration. |
| [docs/SERVERS_AND_STORAGE.md](SERVERS_AND_STORAGE.md) | Power users and tool builders | You want the REST and MCP servers in outline, background jobs, run keys, the SQLite run store and trial counting. |
| [notebooks/README.md](../notebooks/README.md) | Notebook users | You want to open `explore_runs.ipynb` (saved runs) or `montecarlo.ipynb` (range of outcomes vs the index) and understand what each writes and where. |
| [server/README.md](../server/README.md) | Developers and tool builders | You want the REST endpoints, MCP tools, JSON shapes, async jobs and the `SCREENLAB_STORE` setting. |
| [schema/runstore.linkml.yaml](../schema/runstore.linkml.yaml) | Anyone querying the run database (SQL) | You want the tables and columns of `runs.sqlite` and what each unit means. |
| [realdata/ACQUIRE.md](../realdata/ACQUIRE.md) | The owner deciding where real data comes from | You want to move beyond the 20-stock sample: which vendors, what to buy, and the survivorship and point-in-time pitfalls. |
| [CRITIQUE.md](../CRITIQUE.md) | The owner and any reviewer | You want an honest statement of what the results can and cannot support today, before relying on them. |
| [DESIGN.md](../DESIGN.md) | Contributors | You are changing the research modules (data adapters, sweeps, exit signals) and need the module conventions. |
| [ROADMAP.md](../ROADMAP.md) | The owner and contributors | You want to know what is done, what is next, and what is deliberately left out. |
| [research/CALIBRATION_SP500.md](../research/CALIBRATION_SP500.md) | Investors judging exit rules | You want the walk-forward study of whether an exit screen helps on the S&P 500 sample, with its limits. |
| [docs/BAYESIAN_SWEEPS.md](BAYESIAN_SWEEPS.md) | Anyone tuning screen parameters | You are tempted to optimise thresholds automatically; read this for when that is and is not worth it. |
