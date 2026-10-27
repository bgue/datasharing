# research/

`calibrate_sp500.py` is the walk-forward exit-calibration study on the bundled S&P 500 data;
`CALIBRATION_SP500.md` is its write-up.

The result tables (`results/*.csv`, `runtime_seconds.txt`) are **not tracked in git** (they are in
`.gitignore`). Regenerate them, about 10 minutes on 4 cores, from the directory that contains
`screenlab/`:

    python -m screenlab.research.calibrate_sp500
