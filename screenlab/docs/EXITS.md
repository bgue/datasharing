# Exits: signals, policy, extra inputs and calibration

Part of the [documentation map](README.md). The question "does an exit help?" is studied in
[research/CALIBRATION_SP500.md](../research/CALIBRATION_SP500.md): short answer, it cuts drawdowns, costs about
two points a year of return, and calibrated settings do not hold up out of sample.

- **Exit-signal screen** (`exit_signals.py`): a dated risk score built from
  causal signals (trend break, breadth, volatility regime, drawdown,
  valuation stretch, momentum crash, rate shock, dispersion) with
  hysteresis (on at `threshold`, off again only below `threshold −
  hysteresis`, so the flag doesn't flicker). It runs on its own clock,
  separate from the rebalance calendar: `check="D"` (default), `"W-FRI"` or
  `"M"` says how often the flag may change, `lag` is the bars between
  signal and trade, `min_hold_days` stops whipsaw. `lab.exit_eval(...)`
  reports forward returns in flagged vs unflagged states, lead time before
  the largest drawdowns and a derisking overlay; `Config(exit=
  ExitPolicy(screen))` (or `lab.backtest(..., exit=...)`) applies the flag
  day by day inside a backtest without the entry screen ever seeing it.
- **Exit policy** (`exit_policy.py`): the screen says *when*; `ExitPolicy`
  says *what happens*. `park` = cash, a fixed rate, or any loaded series
  (a T-bill or bond ETF total-return index) with its own MER and cost;
  `exposure_when_flagged` (0 = fully out); `reentry` = `"signal"` (flag
  clears), `"next_rebalance"` (stay out until the entry screen's next
  scheduled date) or `"signal_or_rebalance"`; `reentry_delay_days`
  (confirmation), `reentry_steps` (scale back in gradually);
  `rescreen_on_reentry` (re-run the entry screen on the way back in instead
  of returning to the paper portfolio). `lab.backtest(screens, exit=policy,
  with_and_without=True)` runs every screen with and without it (rows
  `name` / `name+exit`, columns `TimeOut`, `Switches/yr`); `Portfolio(name,
  screen, exit=...)` lets portfolios in one report carry different
  policies. Row labels are unique: two entries with the same name are both
  kept as `dogs`, `dogs#2`, ... (a name equal to the benchmark's gets `#2`
  too). `Config.exit` is the only way to attach an exit. CLI: `--exit "TrendBreak:index=SP500,Breadth" --exit-compare
  --exit-park cash --exit-reentry signal`. The entry picks never depend on
  the flag.
- **More exit inputs** (`exit_inputs.py`): correlation spike, new lows minus
  new highs, short/long vol ratio, death cross, distance from 52-week high,
  downside deviation, a trailing stop on the portfolio's own drawdown,
  portfolio-vs-index drawdown, earnings breadth, earnings yield minus rf,
  yield-curve inversion, credit spread. Each declares what data it needs
  and returns NaN without it.
- **Calibration** (`calibration.py`): `lab.calibrate_exit(screen, grid,
  splits=...)` sweeps threshold, hysteresis, cadence, lag, hold period,
  signal weights and signal parameters, scored by Calmar (default), Sharpe,
  CAGR, MaxDD or the flagged/unflagged forward-return gap, walk-forward,
  under constraints (max switches per year, time flagged). It reports
  out-of-sample vs in-sample, **stability** (how often the same point wins
  across windows) and the deflated Sharpe of the winner, and hands back a
  ready `ExitPolicy` (`result.policy`).
