# research/

`calibrate_sp500.py` is the walk-forward exit-calibration study on the bundled S&P 500 data;
`CALIBRATION_SP500.md` is its write-up.

The result tables (`results/*.csv`, `runtime_seconds.txt`) are **not tracked in git** (they are in
`.gitignore`). Regenerate them, about 10 minutes on 4 cores, from the directory that contains
`screenlab/`:

    python -m screenlab.research.calibrate_sp500

`montecarlo_sp500.py` is the Monte Carlo study on the same data (distribution of outcomes and the chance of
trailing the passive index); `montecarlo/MONTECARLO_SP500.md` is its write-up with the four charts (tracked);
`montecarlo/*.csv` are not tracked. Regenerate (under a minute):

    python -m screenlab.research.montecarlo_sp500

`diagnostics.py` runs the Phase 1.5 checks (cap-tiered costs on the all-portfolio Monte Carlo, rebalance-timing luck on the Globe screens, the Basic Buyback turnover check); write-up `diagnostics/DIAGNOSTICS.md`, about 14 minutes: `python -m screenlab.research.diagnostics`.
