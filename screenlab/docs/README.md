# Documentation map

Paths are relative to the `screenlab/` package folder unless stated. Read top to bottom if you are new.

| Document | For | Read it when |
|---|---|---|
| [docs/GETTING_STARTED.md](GETTING_STARTED.md) | Anyone installing for the first time, no programming needed | You have just got the code and need a working venv, a first run, the notebook, the REST API or the Claude connection. |
| [README.md](../README.md) | Users of the screens and the Python SDK | You want to know what each built-in screen does, how to define your own, costs and benchmarks, and the `Lab` API. |
| [notebooks/README.md](../notebooks/README.md) | Notebook users | You want to open `explore_runs.ipynb` and understand what it writes and where. |
| [server/README.md](../server/README.md) | Developers and tool builders | You want the REST endpoints, MCP tools, JSON shapes, async jobs and the `SCREENLAB_STORE` setting. |
| [schema/runstore.linkml.yaml](../schema/runstore.linkml.yaml) | Anyone querying the run database (SQL) | You want the tables and columns of `runs.sqlite` and what each unit means. |
| [realdata/ACQUIRE.md](../realdata/ACQUIRE.md) | The owner deciding where real data comes from | You want to move beyond the 20-stock sample: which vendors, what to buy, and the survivorship and point-in-time pitfalls. |
| [CRITIQUE.md](../CRITIQUE.md) | The owner and any reviewer | You want an honest statement of what the results can and cannot support today, before relying on them. |
| [DESIGN.md](../DESIGN.md) | Contributors | You are changing the research modules (data adapters, sweeps, exit signals) and need the module conventions. |
| [ROADMAP.md](../ROADMAP.md) | The owner and contributors | You want to know what is done, what is next, and what is deliberately left out. |
| [research/CALIBRATION_SP500.md](../research/CALIBRATION_SP500.md) | Investors judging exit rules | You want the walk-forward study of whether an exit screen helps on the S&P 500 sample, with its limits. |
| [docs/BAYESIAN_SWEEPS.md](BAYESIAN_SWEEPS.md) | Anyone tuning screen parameters | You are tempted to optimise thresholds automatically; read this for when that is and is not worth it. |
