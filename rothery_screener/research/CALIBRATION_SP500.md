# Does an exit screen help on real S&P 500 data? A walk-forward calibration study

*Data: 20 large S&P 500 stocks plus the S&P 500 index, daily, 1990-2022 (bundled with the package). Script: `calibrate_sp500.py` (about 10 minutes to run, fixed seeds). Raw tables: `results/*.csv`.*

## Bottom line

* **Do not use calibrated exit settings as the default.** Letting the software pick the best exit settings on 8-10 years of history and then trading them on the next 3-4 years produced a worse result than doing nothing on return (CAGR lower in 32 of 32 portfolio tests) and, on the S&P 500 itself, a lower Sharpe ratio as well. The settings it picked changed from window to window (the same setting won only about 27% of windows on average).
* **What the exit does reliably is cut the worst losses.** In 30 of 32 portfolio tests the maximum drawdown was smaller with the exit, by 16 percentage points on average (for example the momentum portfolio's worst fall of -47% became about -31% to -44%). The price is roughly 2 percentage points a year of return (before crediting any interest on the cash). It also means being out of the market 22-39% of the time and trading about 2 times a year.
* **A simple fixed rule did better than any calibrated rule.** One setting chosen in advance (trend-only exit: leave when the S&P 500 is weak against its own 10-month average, weekly check, 10-day minimum hold, go fully to cash, come back when the signal clears) had about the same return as no exit, a Sharpe ratio 0.18 to 0.26 higher on both portfolios, and roughly 25-55% smaller maximum drawdowns (e.g. -47% to -34% for momentum, -38% to -18% for low-vol). Calibration beat this fixed rule on Sharpe in only 2 of the 48 comparisons.
* **But the evidence is thin.** The gains come from a handful of crashes (2000-02, 2008, 2020, 2022). The statistical tests cannot separate the fixed rule's return from "no exit" (it is neither better nor worse in a way we can detect), and nothing here proves the Sharpe gain would repeat.
* **Recommendation:** make "no exit" the default. If a default exit is wanted for risk control (not for return), offer the fixed trend-only rule above as an *optional drawdown-control setting*, described as such, with a note that it does not raise return. No calibrated policy is robust enough to recommend. Calibrate on this kind of data only as a diagnostic, not to pick settings.

## What we did, in plain words

1. **Two portfolios.** Each month the package picks 5 stocks out of the 20: either the five with the best 12-month-minus-1-month price momentum, or the five least volatile. Each is held equal-weighted and refreshed monthly (February 1991 onward). The benchmark is always the passive S&P 500 index, bought and held.
2. **Exit screens.** An exit screen scores market risk every day from 0 to 1 and raises a flag when the score is high. When flagged, the portfolio is sold down (to cash, or half) and bought back when the flag clears. The flag runs on its own clock and does not know about the monthly stock picks. Four exit screens were tried:
   * `trend`: S&P 500 below its 10-month average.
   * `trend_vol`: trend plus a high-volatility regime.
   * `trend_breadth_vol`: trend, volatility, and the share of the 20 stocks that are below their 200-day average.
   * `broad`: those three plus distance from the 52-week high, a spike in the stocks moving together, and new lows minus new highs.
3. **Settings tried.** For each screen: the score threshold (0.6-0.9), the gap before the flag clears (0.05-0.2), how often the flag may change (daily or weekly), a minimum holding period (0 or 10 days), how much to sell (all, or half), when to come back (when the signal clears, or at the next monthly rebalance), a confirmation delay (0 or 10 days) and whether to return in one step or two. That is 768 combinations; each calibration tried a fixed random 256 of them. At most 4 switches a year were allowed. Trading cost was 5 basis points per unit traded; cash earned 0% (a sensitivity at 2.5% is shown).
4. **Walk-forward test (the honest part).** The settings are chosen using only an 8-year "training" window (second check: 10 years), judged by Calmar (return divided by worst drawdown) or in a second pass by Sharpe. The chosen settings are then traded on the following 4 years (second check: 3 years), which the choice never saw. Then the window slides forward. The out-of-sample stretches are joined into one continuous track record. All the tables below use only those out-of-sample years: **1999-2019** (8/4 split, portfolios), **2001-2022** (10/3 split, portfolios), and 1998-2022 / 2000-2021 for the S&P 500 index as target.
5. **Comparisons.** For each portfolio: the same portfolio without an exit, the passive S&P 500, and the exit version. We also report a **fixed default exit** that was written down before looking at any results (the `trend` screen at threshold 0.8, hysteresis 0.1, weekly check, 10-day hold, fully to cash, re-enter on signal) and was never fitted to the data.

## Results: out-of-sample, stitched

Columns: compound annual growth, volatility, Sharpe (0% cash), worst peak-to-trough fall, worst rolling 12 months, share of days with less than full exposure, and exposure changes per year. "Calibrated exit" rows use the Calmar objective and the 8-year/4-year split; the settings differ window to window, so each row is a *process*, not one setting.

**momentum_12_1, split 8/4, objective Calmar; OOS 1999-02-02 to 2019-02-01**

| Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr |
|---|---|---|---|---|---|---|---|
| No exit (same portfolio) | 14.2% | 22.9% | 0.69 | -46.7% | -39.0% | 0.0% | 0.0 |
| Passive S&P 500 (price index) | 3.8% | 19.1% | 0.29 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500, approx. total return (+2%/yr) | 5.9% | 19.1% | 0.40 | -55.5% | -47.8% | 0.0% | 0.0 |
| Calibrated exit: trend | 11.1% | 17.6% | 0.69 | -30.8% | -22.0% | 31.0% | 2.2 |
| Calibrated exit: trend_vol | 11.8% | 18.7% | 0.69 | -43.5% | -34.3% | 27.4% | 2.0 |
| Calibrated exit: trend_breadth_vol | 12.1% | 19.2% | 0.69 | -41.8% | -36.8% | 22.5% | 2.5 |
| Calibrated exit: broad | 9.1% | 18.8% | 0.56 | -42.7% | -35.2% | 25.8% | 2.9 |
| Fixed default exit (trend only, not calibrated) | 15.2% | 17.7% | 0.89 | -33.9% | -29.0% | 25.8% | 1.9 |
|   same, parked money earns 2.5%/yr | 15.9% | 17.7% | 0.92 | -33.6% | -28.1% | 25.8% | 1.9 |

**low_vol, split 8/4, objective Calmar; OOS 1999-02-02 to 2019-02-01**

| Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr |
|---|---|---|---|---|---|---|---|
| No exit (same portfolio) | 8.1% | 14.8% | 0.60 | -37.9% | -31.4% | 0.0% | 0.0 |
| Passive S&P 500 (price index) | 3.8% | 19.1% | 0.29 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500, approx. total return (+2%/yr) | 5.9% | 19.1% | 0.40 | -55.5% | -47.8% | 0.0% | 0.0 |
| Calibrated exit: trend | 7.2% | 10.2% | 0.73 | -23.8% | -16.2% | 24.2% | 2.1 |
| Calibrated exit: trend_vol | 5.8% | 8.0% | 0.74 | -16.7% | -12.8% | 39.2% | 1.7 |
| Calibrated exit: trend_breadth_vol | 6.4% | 9.1% | 0.73 | -14.0% | -10.2% | 39.1% | 1.6 |
| Calibrated exit: broad | 7.2% | 10.3% | 0.73 | -18.9% | -12.8% | 27.8% | 2.1 |
| Fixed default exit (trend only, not calibrated) | 7.9% | 10.0% | 0.81 | -17.5% | -15.3% | 25.8% | 1.9 |
|   same, parked money earns 2.5%/yr | 8.6% | 10.0% | 0.87 | -17.1% | -14.8% | 25.8% | 1.9 |

**SP500, split 8/4, objective Calmar; OOS 1998-01-05 to 2022-01-03**

| Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr |
|---|---|---|---|---|---|---|---|
| No exit (same portfolio) | 6.9% | 19.6% | 0.44 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500 (price index) | 6.9% | 19.6% | 0.44 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500, approx. total return (+2%/yr) | 9.0% | 19.6% | 0.54 | -55.5% | -47.8% | 0.0% | 0.0 |
| Calibrated exit: trend | 5.4% | 12.4% | 0.49 | -37.7% | -20.2% | 32.1% | 3.6 |
| Calibrated exit: trend_vol | 5.5% | 11.9% | 0.51 | -41.7% | -28.2% | 30.6% | 2.2 |
| Calibrated exit: trend_breadth_vol | 3.4% | 10.9% | 0.36 | -28.8% | -25.5% | 35.0% | 3.1 |
| Calibrated exit: broad | 4.3% | 11.8% | 0.41 | -44.6% | -32.5% | 31.1% | 3.5 |
| Fixed default exit (trend only, not calibrated) | 6.5% | 12.4% | 0.57 | -29.8% | -25.4% | 24.3% | 2.1 |
|   same, parked money earns 2.5%/yr | 7.1% | 12.4% | 0.61 | -25.2% | -24.4% | 24.3% | 2.1 |

Second check, 10-year training / 3-year test windows (same objective):

**momentum_12_1, split 10/3, objective Calmar; OOS 2001-02-02 to 2022-02-01**

| Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr |
|---|---|---|---|---|---|---|---|
| No exit (same portfolio) | 17.0% | 22.6% | 0.81 | -46.7% | -39.0% | 0.0% | 0.0 |
| Passive S&P 500 (price index) | 5.9% | 19.5% | 0.39 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500, approx. total return (+2%/yr) | 8.0% | 19.5% | 0.49 | -55.5% | -47.8% | 0.0% | 0.0 |
| Calibrated exit: trend | 14.0% | 17.2% | 0.85 | -25.5% | -22.0% | 29.7% | 1.9 |
| Calibrated exit: trend_vol | 14.7% | 17.2% | 0.89 | -26.5% | -21.4% | 30.2% | 1.6 |
| Calibrated exit: trend_breadth_vol | 13.5% | 17.3% | 0.82 | -27.4% | -22.1% | 28.8% | 2.1 |
| Calibrated exit: broad | 11.8% | 16.2% | 0.77 | -25.7% | -20.5% | 37.0% | 3.1 |
| Fixed default exit (trend only, not calibrated) | 16.7% | 17.2% | 0.99 | -24.6% | -10.8% | 23.5% | 1.5 |
|   same, parked money earns 2.5%/yr | 17.4% | 17.2% | 1.02 | -24.2% | -10.3% | 23.5% | 1.5 |

**low_vol, split 10/3, objective Calmar; OOS 2001-02-02 to 2022-02-01**

| Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr |
|---|---|---|---|---|---|---|---|
| No exit (same portfolio) | 8.8% | 14.9% | 0.64 | -37.9% | -31.4% | 0.0% | 0.0 |
| Passive S&P 500 (price index) | 5.9% | 19.5% | 0.39 | -56.8% | -48.8% | 0.0% | 0.0 |
| Passive S&P 500, approx. total return (+2%/yr) | 8.0% | 19.5% | 0.49 | -55.5% | -47.8% | 0.0% | 0.0 |
| Calibrated exit: trend | 8.1% | 10.3% | 0.81 | -16.5% | -13.7% | 24.0% | 1.3 |
| Calibrated exit: trend_vol | 7.1% | 10.2% | 0.73 | -21.0% | -16.2% | 29.6% | 1.4 |
| Calibrated exit: trend_breadth_vol | 8.4% | 10.1% | 0.85 | -17.9% | -8.1% | 26.4% | 0.9 |
| Calibrated exit: broad | 7.9% | 9.8% | 0.82 | -14.4% | -12.8% | 24.5% | 3.0 |
| Fixed default exit (trend only, not calibrated) | 8.4% | 9.5% | 0.89 | -16.7% | -8.8% | 23.5% | 1.5 |
|   same, parked money earns 2.5%/yr | 9.0% | 9.5% | 0.96 | -16.1% | -8.4% | 23.5% | 1.5 |

Notes on reading these tables:

* **The passive S&P 500 here is a price index (no dividends)**, while the stocks use adjusted prices (dividends included). The "approx. total return" row adds 2% a year to give a fairer yardstick; it is an approximation, not data. Even on that basis the momentum portfolio's lead comes from stock selection among surviving winners (see caveats), not from the exit.
* Moving money out of the market earns 0% in the main rows. The row with 2.5% on parked money shows the effect of paying interest: about +0.7 percentage point of CAGR.

### Is the exit better than no exit? (statistical tests)

Block-bootstrap (12-month blocks, 2,000 draws, seed 0) and Newey-West t-statistics on the daily out-of-sample difference "exit minus no exit", all 48 calibrated runs (4 screens x 3 targets x 2 splits x 2 objectives):

* Average difference: **-3.0 percentage points a year** in favour of *no exit* (all 48 negative).
* 19 of 48 have a 90% bootstrap interval entirely below zero; **none** has one above zero. Typical t-statistic is -1.4.
* The fixed default exit: average -1.5 points a year (-0.9 with 2.5% interest on cash), none significantly different from zero in either direction (typical t between -1 and 0).

So: calibrated exits significantly cost return in many cases; the fixed rule is statistically indistinguishable from no exit on return, with a visible reduction in drawdown and volatility (which this test, an average-return test, does not measure).

Versus the passive S&P 500 (8/4 split, calibrated exit): momentum portfolio +4.9 to +7.7 points a year (t about 1.4-2.3), low-vol portfolio +0.3 to +1.9 points (t under 0.7, not significant). That difference is mostly the entry screen (and survivorship, below), not the exit; the no-exit momentum portfolio is already far ahead.

### Deflated Sharpe and number of trials

Each calibration tried 256 settings; across four screens that is 1,024 policies per target and split (about 12,300 policy evaluations over the 48 runs; 768 distinct settings per screen exist). The deflated Sharpe ratio (DSR, `calibrate_exit`'s own `dsr`, which deflates the chosen setting's full-history Sharpe against the spread of Sharpes across the 256) is 0.88-1.00 in every run (median 1.00). **Read that with care:** it only says the chosen strategy's full-history Sharpe is positive and not just the luckiest of 256 near-identical results. Those Sharpes are dominated by the stocks, not the exit, and the full-history figure includes the training years. It says nothing about whether the exit adds value; the out-of-sample comparison above is the real test. The probabilistic Sharpe of the stitched out-of-sample series (against zero) is 0.58-1.00.

## Stability of the selected settings

* The most frequent setting won on average **27% of windows** (range 14%-43%, with only 5-7 windows per run). A stable setting would win most windows.
* Parameter by parameter, the share of windows choosing the most common value (random choice among two values would give 50%): re-entry mode 82%, exposure when flagged 77% (nearly always fully to cash), check frequency 73%, confirmation delay 74%, ramp steps 73%, minimum hold 69%, **hysteresis 59% and threshold 60%** (four values for threshold would be 25% by chance, so these are weakly stable). Details in `results/param_stability.csv`.
* The modal choices were typically: fully to cash, re-enter when the signal clears, no ramp, threshold 0.6-0.9 (varies by run).
* **In-sample versus out-of-sample.** The pick's out-of-sample score divided by its training score averaged 0.60 (0.57 for the 8/4 split, 0.67 for 10/3).
* In only **36% of windows** (30% for 8/4, 42% for 10/3) did the picked setting beat "no exit" on its own objective. Of all 256 settings, about 50-60% beat no exit on average out-of-sample objective, i.e. a random pick is about a coin flip against no exit on Calmar/Sharpe, while it is clearly better on drawdown.
* Window-by-window example (`trend_breadth_vol` on momentum, 8/4, Calmar): the picks flip between threshold 0.9 / 0.7 / 0.7 / 0.8 / 0.8, between half and full exit, and between daily and weekly checks (`results/walk_forward_windows.csv`).

## What holds up and what does not

Holds up (consistent in both splits, both objectives, all four screens):
* The exit lowers volatility and the worst drawdown in almost every case (30 of 32 portfolio tests; 100% for the fixed default).
* It costs return before interest on cash; with 2.5% on cash, the fixed trend rule roughly breaks even on CAGR.
* More signals did not help: `broad` was the weakest on momentum, and trend-only was as good as or better than the multi-signal screens. The simple trend rule had the best out-of-sample behaviour once fixed.

Does not hold up:
* Calibration. The fitted settings do not repeat, lose to the unfitted default in 46 of 48 runs on Sharpe, and the calibrated momentum Sharpe is no better than no exit (0.69 vs 0.69).
* A return gain from exiting. None was detected anywhere.
* The S&P 500 index itself as the target: calibrated exits reduced the Sharpe ratio on average by 0.06 and CAGR by 2.5 points (8/4 split: `trend` 5.4% vs 6.9% for buy-and-hold).

Other checks:
* **Calmar versus Sharpe objective:** results are similar (average Sharpe change +0.02 for both; slightly less return give-up with Sharpe, -2.1 vs -2.5 points).
* **Fixed portfolio stop** (leave when the portfolio is 10/15/20% below its own peak; fixed settings, not calibrated; the calibrated version is in the last section): it reduced return a lot (e.g. momentum 22.5% to 13.1% full-period at 15%) for little drawdown benefit (-47% to -44%), and Sharpe fell. It whipsaws. Table: `results/fixed_portfolio_stop_reference.csv`. Not recommended. Calibrating the stop did not rescue it (see "Calibrated portfolio stop" below).

## Look-ahead checks

* All signals use a trailing window only (percentiles are ranked against the past ten years including the current day). I recomputed the flag and the exposure on data truncated at three dates (June 2000, December 2008, March 2015) and compared them with the full-data values up to that date: **0 mismatches** in 2,654-6,341 days each (`results/causality_check.csv`).
* Each day's flag decides the next bar's exposure (one-bar lag). The picked settings for a test window use only the training window's results.
* Cross-check of the engine: the policy overlay used for calibration gives exactly the same curve as running the exit inside `Lab.backtest` (momentum portfolio, default policy: CAGR 19.95%, Sharpe 1.04, MaxDD -37.5%, 60 switches in both).
* Not eliminated: the first walk-forward windows start in 1990-91 when the ten-year percentile history is still short (signals are blank for the first ~20 observations and noisy for years). Also, the fixed rule was written into the script before the first run and never tuned, but a 10-month trend rule is a well-known idea that was published knowing about the 2000 and 2008 crashes, so it is not a pure blind test.

## Caveats

* **Twenty survivors.** The 20 stocks are today's well-known large caps (Apple, Microsoft, UNH, Lilly...). Picking among them in 1991 with hindsight inflates the portfolios (momentum: 22.5% CAGR over 1991-2022 vs 7.8% for the index price series), and the "breadth" and "stocks moving together" signals measure only these 20 names.
* **One market, few episodes.** There are about four major drawdowns in 33 years. Five to seven walk-forward windows per run are too few to estimate stability precisely; the difference between 27% and, say, 50% stability is within the noise.
* **Benchmark is price-only** (see above). Cash earns 0% in the main tables, which understates the exit's returns; interest of about 2.5% has been shown for the fixed rule only.
* The test windows overlap in market conditions across the 48 runs, so the runs are not independent evidence; treat them as one finding repeated, not 48 findings.
* Costs: 5 bps per unit traded on the whole-portfolio exposure. Taxes, bid-ask in small caps and cash-account frictions are ignored; real-world taxable-account trading of 2 exits a year would reduce the exit's net benefit.
* Transaction timing is end-of-day signal, next-bar execution.

## What was trimmed

* Grid: 768 combinations in full; each calibration used the same fixed random sample of 256 (`sweep.random_search`, seed 0), to stay under the time limit. Runs take about 10 minutes on 4 cores.
* The portfolio-stop screen is not one of the four screens in the 48 runs above. It is covered by a separate, smaller calibration (two portfolios, 8/4 split, Calmar) described in the last section, plus the fixed-setting reference.

## Calibrated portfolio stop (curve-based signals now calibrate)

An earlier version of this study found that `calibrate_exit` never handed the portfolio curve to curve-based signals (`PortfolioStop`, `PortfolioVsIndex`), so they silently produced no score, the screen never flagged and every grid point equalled buy-and-hold. **That is fixed**: `calibrate_exit` now passes the target curve to the signals (a drawdown from the running peak only looks backward, so using the curve inside a training window is causal), and the research script's stitching step does the same. The check from the old note now gives a real screen: `ExitScreen([PortfolioStop(0.15)]).components(mkt, curve=c).shape` is `(8313, 1)` and the calibration table shows `FracFlagged` up to 0.42 and 2-4 switches a year.

Re-run, same recipe as the main study (`PortfolioStop(0.15)`, the same 256 sampled grid points of the 768-point grid, 8-year train / 4-year test, Calmar objective, at most 4 switches a year, 5 bps per unit traded, cash earning 0%), out of sample 1999-02-02 to 2019-02-01, five windows each (`stop_calibration()` in `calibrate_sp500.py`; about 1 minute):

| Portfolio | Strategy | CAGR | Vol | Sharpe | MaxDD | Worst 12m | Time out | Switches/yr | Stability |
|---|---|---|---|---|---|---|---|---|---|
| momentum_12_1 | No exit | 14.2% | 22.9% | 0.69 | -46.7% | -39.0% | 0.0% | 0.0 | |
| momentum_12_1 | Calibrated portfolio stop | 7.5% | 16.9% | 0.51 | -45.9% | -30.2% | 38.4% | 3.7 | 20% |
| low_vol | No exit | 8.1% | 14.8% | 0.60 | -37.9% | -31.4% | 0.0% | 0.0 | |
| low_vol | Calibrated portfolio stop | 5.8% | 11.8% | 0.54 | -27.1% | -20.6% | 19.1% | 2.0 | 20% |

**Reading:** calibrating the stop does not make it work. For the momentum portfolio it gave up half the return (14.2% to 7.5%) for almost no reduction in the worst fall (-46.7% to -45.9%) and a lower Sharpe; for low-vol it cut the worst fall (-37.9% to -27.1%) but also lowered return and Sharpe. The picked settings changed from window to window (the same setting won 1 window in 5). This agrees with the fixed-setting reference above (a stop on the portfolio's own drawdown whipsaws), and it is worse than the fixed trend-only rule on both portfolios. The recommendation in the bottom line is unchanged. These two runs use the same data and windows as the main study and are not independent evidence.

*Files: `results/oos_summary.csv` (all 48 runs, every metric), `oos_tests.csv`, `grid_tables.csv` (every setting's full-period and out-of-sample numbers), `walk_forward_windows.csv`, `param_stability.csv`, `oos_exit_curves.csv`, `causality_check.csv`, `fixed_portfolio_stop_reference.csv` (the CSVs are not tracked in git; regenerate with `python -m rothery_screener.research.calibrate_sp500`, see `README.md` in this folder).*
