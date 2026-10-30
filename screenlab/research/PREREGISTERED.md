# Pre-registered hypotheses

**Why this file exists.** A backtest tool makes it cheap to try fifty variants in an afternoon, and the best of fifty
looks good by luck alone (the argument is in [docs/BAYESIAN_SWEEPS.md](../docs/BAYESIAN_SWEEPS.md); the deflated Sharpe in
the TUI's Compare tab prices it). The honest defence is to write down, before the first real-data run, the few claims
that matter, each with its metric and its pass mark. A hypothesis is then supported or refuted by the first run that
follows its own definition; changing the screen, the threshold or the horizon afterwards means writing a new
hypothesis, not editing the old one. Link a TUI scenario to a hypothesis (`link` command, key `l`) and every variant
you try is counted as a trial against it.

**Rules.**
- Write the block and its date *before* looking at real-data results. Do not edit a block after its first run; only
  change `status` (open, supported, refuted).
- The benchmark must be a total-return series. A price-only index is allowed only as an approximation, with the
  dividend yield stated (for example "price index plus 2.8%/yr yield approximation").
- Costs are stated, never zero. Exit policy is `none` unless the hypothesis is about the exit.
- `threshold` is an operator and a number, `>= 0.02` or `> 0` (a `%` suffix is accepted); `metric` is a Compare column
  (`Excess/yr`, `CAGR`, `Sharpe`, `MaxDD`, `DSR`, ...). A result shorter than the stated `horizon` cannot meet it.

**The three blocks below are EXAMPLES** for the Globe and Mail portfolios (screens in
[docs/SCREENS.md](../docs/SCREENS.md)), written to show the format. Replace them with your own before a real-data run;
the TUI says so while only examples are present.

## H1: EXAMPLE - Frugal Dividend (annual) beats XIC after costs
- name: Frugal Dividend, rebalanced annually, beats XIC after costs over 15 years
- screen: frugal_dividend, rebalance_months 12, n 10
- dataset: Canadian large caps with point-in-time fundamentals and delisted names (not yet loaded)
- benchmark: XIC.TO total return (adjusted close); if only a price index is available, price plus its 2.8%/yr dividend yield, stated in the run
- horizon: 15 years
- metric: Excess/yr
- threshold: >= 0.01
- costs: 25 bps one-way, no commission, MER 0
- exit: none
- written: 2026-10-30
- status: open

## H2: EXAMPLE - Stable Dividend has a shallower worst fall than the index
- name: Stable Dividend (monthly) has a maximum drawdown no deeper than -30% over 15 years
- screen: stable_dividend, rebalance_months 1, n 10
- dataset: Canadian large caps with point-in-time fundamentals and delisted names (not yet loaded)
- benchmark: XIC.TO total return (adjusted close)
- horizon: 15 years
- metric: MaxDD
- threshold: >= -0.30
- costs: 25 bps one-way, no commission, MER 0
- exit: none
- written: 2026-10-30
- status: open

## H3: EXAMPLE - 12-month momentum earns a Sharpe above the index over 10 years
- name: Momentum 12 (annual) has a Sharpe ratio of at least 0.50 over 10 years
- screen: momentum_12m, rebalance_months 12, n 10
- dataset: Canadian large caps with point-in-time fundamentals and delisted names (not yet loaded)
- benchmark: XIC.TO total return (adjusted close)
- horizon: 10 years
- metric: Sharpe
- threshold: >= 0.50
- costs: 25 bps one-way, no commission, MER 0
- exit: none
- written: 2026-10-30
- status: open

<!-- Template: copy this block, remove the comment markers, number it H4, H5, ... and fill every field.

## H4: short claim in words
- name: the claim as one sentence
- screen: screen name or spec file, rebalance months, holdings
- dataset: the dataset and its snapshot
- benchmark: total-return series (or approximation with the yield stated)
- horizon: 15 years
- metric: Excess/yr
- threshold: >= 0.01
- costs: bps one-way, commission, MER
- exit: none
- written: YYYY-MM-DD
- status: open
-->
