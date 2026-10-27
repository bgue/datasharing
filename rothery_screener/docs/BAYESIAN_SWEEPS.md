# Should you tune screens with Bayesian search?

Short answer: not by default. Use a coarse grid (or random search), judge it
walk-forward, and deflate the winner's Sharpe for the number of things you
tried. Reach for Bayesian search only for a few continuous construction
knobs, and only when it is scored out of sample.

## What Bayesian search is

Instead of testing every combination, it fits a cheap model of "parameters ->
score" to the backtests run so far and picks the next setting it expects to be
best (the TPE sampler in `optuna`). It is the same idea as an analyst who tries
a few yields cutoffs, notices the trend, and tries the next one further out,
done by machine.

## Where it helps

- **Fewer backtests.** With more than about three parameters a grid explodes.
  A daily-data backtest takes seconds and a rebalance-timing sweep takes
  minutes, so 30 smart trials beat 500 grid points.
- **Mixed knobs.** It handles continuous settings (a weight cap, a volatility
  lookback) and discrete ones (number of holdings) together.
- **It tells you which parameters matter**, not just which setting won.

## Where it hurts, for this tool in particular

- **The score is mostly noise.** An annual-rebalance screen produces about 20
  yearly observations per backtest. The score surface is nearly flat with random
  bumps, and the search happily climbs a bump. The more cleverly it climbs, the
  better the in-sample number and the worse the odds it repeats.
- **It hides the number of trials.** A 5x5 grid makes it obvious you tried 25
  things. A Bayesian loop that "converged" after 40 runs has an unclear
  effective trial count, and the deflated Sharpe ratio needs that count. Under-
  count it and the result looks better than it is.
- **Not reproducible.** Change the seed and you get a different "best" setting.
  A grid gives the same answer every time and a table you can read.
- **Graham-style screens are thresholds** (P/E under 15, ten years of
  dividends). A coarse grid on a handful of thresholds is interpretable: you can
  see that results are stable from 12 to 18, or not. That is worth more than the
  last half percent a smarter search finds.

## Recommendation

1. Default: `run_sweep` over a small `Grid` (or `random_search` if the grid is
   large) with `splits=` set so the answer is walk-forward: choose on the
   training years, score on the following year, roll forward.
2. Always report `summarize_trials`: the deflated Sharpe ratio (DSR) and the
   Bonferroni-style haircut. Count every variant you looked at, including ones
   you dropped by eye.
3. Judge by the OOS/IS ratio in `df.attrs["summary"]`. Near 1 is healthy; near
   0 or negative means the in-sample winner was luck.
4. Bayesian (`bayes_search`, needs `pip install optuna`) only for continuous
   construction parameters (bands, caps, volatility lookbacks), with `splits=`
   so the loop is maximizing the training windows and the test windows stay
   untouched.

## Worked example: the "dogs" screen

Parameters worth varying: `n` (holdings: 5, 10, 15), `universe` (how many of the
largest names to pick yields from: 20, 30, 50) and `rebalance_months` (6, 12).
That is 18 points, small enough for a grid.

```python
from rothery_screener import Lab
from rothery_screener.sweep import Grid, run_sweep
from rothery_screener.stats import summarize_trials, bootstrap_excess

lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
grid = Grid({"dogs.n": [5, 10, 15], "dogs.universe": [20, 30, 50],
             "rebalance_months": [6, 12]})          # dotted -> screen, bare -> Config
tbl = run_sweep(lab, "dogs", grid, objective="Excess/yr",
                splits=dict(train_years=5, test_years=1))
print(summarize_trials(tbl)[["dogs.n", "dogs.universe", "rebalance_months",
                              "Excess/yr", "OOS", "Sharpe", "DSR", "Haircut"]])
print(tbl.attrs["summary"])     # mean OOS, mean train objective, OOS/IS
```

How to read it: the best in-sample row is tempting, but check three things.
Neighbours (n of 5 versus 10) should look similar; a lone spike is noise.
`OOS` is what the rule chosen on each training window then earned the next
year. And DSR changes the verdict as follows.

*Illustration (made-up round numbers, not output from the synthetic market):*
ten years of monthly returns, the best of the 18 settings has an annualized
Sharpe of 0.65, and the 18 settings' Sharpes differ from one another by about
0.26 (standard deviation). Taken alone, that Sharpe has a 98% chance of being
truly positive (the ordinary probabilistic Sharpe ratio, which is what
`deflated_sharpe` returns for a single trial). But among 18 skill-less trials
with that much scatter you would expect the luckiest to show a Sharpe near 0.49
anyway. Measured against that hurdle the DSR is about 0.71: still more likely
real than not, but short of the 0.95 most people want before acting. The
conclusion moves from "clearly works" to "promising, needs more evidence" without
the strategy changing at all. Had you also tried 30 other ideas before this
sweep, pass `n_trials=48` and it drops further.

Finally run `bootstrap_excess(report_curve, bench_curve, curves=True)` on the
chosen setting for a confidence interval on the yearly excess return, and
`t_stat_excess` for a Newey-West t-statistic that allows for autocorrelated
returns. With only ten years of data, expect wide intervals; that is the honest
answer, not a flaw in the method.
