# Screens: built-in list, specs, Globe and Mail portfolios

Part of the [documentation map](README.md). The short version is in the [main README](../README.md).

## Built-in screens
| screen | idea |
|---|---|
| `dogs` | the 10 highest yielders among the 30 largest names (Dogs of the TSX, Beat the TSX) |
| `graham_defensive` | Graham's defensive investor: size, CR ≥ 2, no losses, a long dividend record, P/E ≤ 15, P/E×P/B ≤ 22.5 |
| `graham_enterprising` | Graham's looser enterprising rules: P/E ≤ 10, P/B ≤ 1.2 |
| `net_nets` | price below net current asset value |
| `piotroski` | F-score ≥ 8 in the cheapest P/B quintile |
| `magic_formula` | Greenblatt's ranking on earnings yield plus return on capital |
| `value_momentum` | low P/E and low P/B, then the best 12-1 momentum |
| `low_vol_dividend` | the least volatile dividend payers among large caps |
| `screaming_value`, `stable_dividend`, `frugal_dividend`, `stable_high_yield`, `basic_buyback`, `momentum_6m`, `momentum_12m` | the Globe and Mail's published portfolios, see below |
| `bluechip_dividend_growth` | rules-based approximation of a discretionary model dividend portfolio, see below |

Every screen above except `graham_*`, `net_nets` and `piotroski` is a JSON spec in `screenlab/specs/`
(see below). The Graham, net-nets and Piotroski screens stay Python: Graham sorts by pass count and then
P/E x P/B, net-nets and Piotroski select with a data-dependent quantile cut and a score, none of which a
stage's filter / rank / keep can express exactly. Their thresholds are keyword arguments (`Screen(params=...)`
in `screens.REGISTRY`); to add a Python screen, write `fn(snapshot, n, **params) -> list[ticker]` and register it.

## Defining screens as specs
A screen is plain data: a universe, derived columns, filters, optional Graham tests and a ranking, as one
stage, or several stages run in order. The built-in spec screens live in `screenlab/specs/globe/*.json` and
`screenlab/specs/classic/*.json`; copy one, edit it, and load it with `lab.define(path)`,
`define_screen(path)` or `load_screens(path)` (a file may hold one spec or a list), or `--screen-file`.

Keys of a single-stage spec: `name`, `n`, `note`, `universe` (`top`, `by`, `sectors`, `exclude_sectors`; the size
cut comes first), `derive` (column -> pandas expression), `filters` (expressions that must all hold), `graham`,
`rank` (column -> `asc` | `desc`; several columns are combined by summing their ranks; rows with a NaN rank
column are dropped). For a funnel give `stages` instead, a list of objects with the same keys plus `keep`. Each
stage runs on the rows the previous one kept, a ranked stage puts them best first, `keep` cuts to the first
`keep`, and the final `n` takes the head. Single-stage keys are sugar for one stage and pick identically.
Leave `keep` off the last stage: `Screen.ranked` (runner-ups) then sees the full ordering.

The Frugal Dividend portfolio (`specs/globe/frugal_dividend.json`), dividend payers among the 300 largest, the
50 lowest 260-day volatility, then the 10 lowest P/E:

    {"name": "frugal_dividend", "n": 10,
     "note": "Follows the Globe's Frugal Dividend: payers, 50 lowest 260-day vol, 10 lowest P/E",
     "stages": [
       {"universe": {"top": 300}, "filters": ["dividend > 0"], "rank": {"vol_260d": "asc"}, "keep": 50},
       {"rank": {"pe": "asc"}}]}

The same in Python: `.then()` opens the next stage, `.keep(n)` cuts the current one, and
`.then_keep(n, by=..., asc=True)` is a new stage that ranks by `by` and keeps n.

    ScreenBuilder("frugal_dividend", n=10).universe(top=300).where("dividend > 0").rank(vol_260d="asc").keep(50) \
        .then().rank(pe="asc").build()

`ScreenSpec.to_dict()` / `ScreenSpec.from_dict()` (and `to_json`) round-trip stages; that dict is what the run
store persists. Reordering stages changes the result (P/E first, then volatility, picks other names).

### Order of the steps in a spec
The steps run in this order: universe (size cut first, then sectors) →
derived columns → filters → Graham tests → rank → take n. Filters are pandas
expressions over the snapshot columns. For anything the spec can't express,
register a plain function as `Screen(name, fn, n)` with
`fn(snapshot, n, **params) -> [tickers]`.

You can also write a screen as a dict or JSON, and load it with
`lab.define(...)` or `--screen-file`:

```json
{"name": "cheap_yield", "n": 10,
 "universe": {"top": 100, "exclude_sectors": ["Fin"]},
 "derive": {"pe_x_pb": "pe * pb"},
 "filters": ["pe < 15", "div_yield > 0.03"],
 "graham": {"preset": "defensive", "max_pe": 18, "min_passes": 6},
 "rank": {"div_yield": "desc", "pe_x_pb": "asc"}}
```

## Globe and Mail portfolios
The Globe's "Inside the Market" columns publish several screen portfolios. We follow the published rules
as closely as the data allows. Each is in `REGISTRY` as a `Screen`, and `GLOBE_PORTFOLIOS` holds ready
`Portfolio` objects with the published rebalance frequency:

    from screenlab import Lab, GLOBE_PORTFOLIOS
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    rep = lab.backtest(list(GLOBE_PORTFOLIOS.values()))
    lab.picks("frugal_dividend", runners_up=2)       # picks + the next 2 by the screen's ranking, `runner_up` column

"300 largest" means the 300 largest names by market cap in our data. Equal weight throughout.

| portfolio | rule | rebalance | source | what our data can't reproduce |
|---|---|---|---|---|
| `screaming_value` | 300 largest, the 10 lowest EV/EBIT | monthly | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-the-screaming-value-portfolio-offers-outsized-returns/) | the true TSX 300 universe; "common stocks only" filtering (trusts, REITs, ETFs); the Globe's EV and EBIT definitions. EBIT <= 0 is excluded |
| `stable_dividend` | dividend payers among the 300 largest, the 20 lowest volatility over 260 trading days | monthly | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-bargain-prices-for-stable-dividend-stocks/) | same universe and filtering gaps; monthly data uses a 12-month window (12 returns), not 260 days |
| `frugal_dividend` | payers among the 300 largest, the 50 lowest 260-day volatility, then the 10 lowest P/E | monthly (annual noted as an alternative) | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-the-frugal-dividend-portfolio/) | same gaps; P/E is trailing price/EPS, negative EPS has no P/E and is skipped. Annual: `lab.backtest([Portfolio("frugal_annual", "frugal_dividend", rebalance_months=12)])` |
| `stable_high_yield` | same first two steps as Frugal, then the 10 highest dividend yields | monthly | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-behold-a-new-high-yield-portfolio-for-dividend-fans/) | same gaps; yield is the trailing dividend over the marked-to-market price |
| `basic_buyback` | the 20 highest buyback yields (percentage reduction in shares outstanding over four quarters) among the 300 largest | monthly | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-a-portfolio-with-the-highest-buyback-yields-on-the-tsx-plus-more/) | `buyback_yield = 1 - shares / shares_prev`, where `shares_prev` must be the count four quarters (one year) earlier; a source with a different lag would shift the measure. Net issuers (yield <= 0) are excluded |
| `momentum_6m`, `momentum_12m` | the 300 largest, the 10 highest prior 6- (12-) month return | monthly | [article](https://www.theglobeandmail.com/investing/markets/inside-the-market/article-should-you-follow-the-crowds-these-momentum-portfolios-tell-the-story/) | plain price return over the window, no skipped month (`mom_6m`, `mom_12m`; the `mom_12_1` column is separate). The bundled S&P 500 lab registers price-only versions with no size cut |
| `bluechip_dividend_growth` | **approximation of a discretionary portfolio** (the Globe's Strategy Lab model dividend portfolio: conservative blue chips with rising dividends, at most 12 stocks, rarely traded): 60 largest, payers with a 10+ year dividend record and EPS above last year's, the 12 lowest 260-day volatility | annual, with `HoldRule(keep_within=2, band=0.25)` | Strategy Lab description | everything discretionary: manager judgment, a true dividend-growth test (we have only the trailing dividend and years paid), and the actual holdings and trade timing. Treat results as a rules-based stand-in, not a replica |

Runner-ups: each published portfolio lists two runner-up stocks. `Lab.picks(screen, asof, runners_up=2)` returns
the picks plus the next two by the screen's ranking (`Screen.ranked`) with a boolean `runner_up` column.
When fewer names qualify than asked for, there may be fewer rows.

New data columns behind these: `mom_6m`, `mom_12m` (plain prior-period total returns), `vol_260d` (std of daily
log returns over the last 260 trading days, annualized by the square root of periods per year; NaN with less than
260 returns; for monthly data it is the 12-month calendar window, identical to `vol_1y`), `ev_ebit`
(enterprise value over EBIT, NaN when EBIT <= 0) and `buyback_yield`. Existing columns are unchanged.

