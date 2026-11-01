# Screen library: ten more built-in screens, "needs" on every screen, and named expressions

Owner's words: "I could only find momentum variants as prebuilt screens in the tui... build the top 10 here. We might need a way to define an expression in screen-building and then use it, eh?"

Why only momentum showed: the TUI starts on the bundled S&P 500 sample (20 names, daily prices, no dividends, no fundamentals), and a lab only registers the screens its data can run. Sixteen screens exist; that dataset offers four. This round does three things: (1) ten new built-in screens, (2) every screen declares what it needs and the Add/Load dialogs show what a dataset lacks instead of hiding it, (3) named expressions that can be defined once and used in any screen.

Depends on the screen-builder round (docs/SCREEN_BUILDER.md: `exclude`, helpers `pct q z log sqrt abs isna coalesce`, `check_expression`, `screen_schema/preview/save/load`, the Screen tab, stages in the form). Start after it has landed.

## 1. Availability ("needs") on every screen

- `Screen.requires() -> set[str]`: the snapshot columns a spec uses, found by parsing every `universe.by`, `derive`, `filters`, `exclude` and `rank` expression with `ast` (names that are not derived in the spec, not helpers, not constants; named expressions are expanded first). Function screens declare `requires` in their registration or report `None` (unknown, treated as available).
- `Lab.screen_status() -> {name: {"available": bool, "missing": [str], "group": "Globe and Mail|Classic|Mine", "note": str}}`. A column counts as present when it is in the snapshot and has any non-null value on the last full snapshot date.
- New spec key `universe.top_optional: bool` (default false): when true and `market_cap` is absent, the size cut is skipped instead of making the screen unavailable. The card of a run says "no size cut: market cap not in the data". Used by the price-only screens below.
- The Add-scenario and Load dialogs in the TUI list every registered screen in three groups: "Available on this dataset", "Needs data this dataset lacks" (dimmed, with `needs: ev_ebit, roic`), and "Mine". Choosing a dimmed one shows the missing columns and where they come from (synthetic lab, `make data-sec`, your own CSV). REST/MCP: `GET /screens` and the `screens` command return the same status.
- Where a registry screen is dimmed on the bundled sample, it still builds and previews on any lab that has the columns.

## 2. New snapshot columns (price-derived, from the price frame, point-in-time)

- `high_52w`: the highest close over the previous 252 trading days including the as-of day.
- `sma_200`: the 200-trading-day simple average close.
- Both appear in `COLUMN_INFO` with descriptions and work on every dataset that has prices (bundled sample, real panel, synthetic).

## 3. The ten screens (all spec JSON under `specs/classic/`; name, rule, n, `top_optional` where noted)

1. `momentum_12_1`: 300 largest, the 10 highest 12-1 month return (skips the latest month); `top_optional`; needs `mom_12_1`.
2. `low_vol`: 300 largest, the 20 lowest 260-day volatility, no dividend filter; `top_optional`; needs `vol_260d`.
3. `high_52w`: 300 largest, the 10 closest to their 52-week high (`price / high_52w`, highest first); `top_optional`.
4. `trend_momentum`: 300 largest, only names above their 200-day average (`price > sma_200`), the 10 highest 12-1 return; `top_optional`.
5. `small_dogs`: stage 1: 30 largest, payers, the 10 highest dividend yields, keep 10; stage 2: the 5 lowest prices (O'Higgins). Needs `div_yield`, `price`.
6. `large_low_pe`: 30 largest, positive P/E, the 10 lowest P/E. Note: a Stingy Investor page appears to list a "Dow 30 low P/E" portfolio whose rules could not be read here; this is the generic version, not a replica.
7. `shareholder_yield`: 300 largest, the 20 highest dividend yield plus buyback yield (named expression `shy`). Needs `div_yield`, `buyback_yield`.
8. `value_composite`: 500 largest, excluding Fin; positive P/E, P/B, EV/EBIT and sales; composite rank (best first) of `pb` asc, `pe` asc, `ps` asc (named expression), `ev_ebit` asc, `shy` desc; 25 names (after O'Shaughnessy's value composite; EV/EBIT stands in for EV/EBITDA).
9. `gross_profitability`: 300 largest, excluding Fin and Util, the 20 highest gross profit over assets (`gpa = gross_margin * asset_turnover`, named expression) (Novy-Marx).
10. `safe_yield`: 300 largest, EPS positive, payout (`dividend / eps`, named expression) under 0.6, the 20 highest dividend yields.

Each carries a `note` (rule, source, what it ignores) and `group: "Classic"`. A test parametrised over the registry's spec screens checks: builds, `requires()` matches what the spec uses, previews on the synthetic lab with a non-empty funnel, round-trips through the Screen tab form with identical picks.

## 4. Named expressions ("my columns")

Define an expression once, name it, use it anywhere a column name is allowed (derive, include, exclude, rank, `universe.by`).

- Storage: `<screens folder>/columns.json` (`SCREENLAB_SCREENS` or `~/.screenlab/screens`): `{"shy": {"expr": "coalesce(div_yield, 0) + coalesce(buyback_yield, 0)", "note": "dividend plus buyback yield"}}`.
- Starter columns shipped in code, read-only, shown under "Starter columns": `shy`, `ps` (`market_cap / sales`), `payout` (`dividend / eps`), `gpa` (`gross_margin * asset_turnover`), `pct_of_high` (`price / high_52w`), `ey_minus_rate` is NOT included (no rate column).
- Rules: the name must be a valid identifier, must not collide with a snapshot column, a helper or a starter column; the expression must pass `check_expression` against the snapshot names plus other named expressions; definitions may use other named expressions; cycles are rejected with the cycle path in the message. Evaluating on the last full snapshot must not raise; a column that is null for every name is accepted with a warning.
- Resolution: at preview, save, run and `requires()` time, every named expression a spec uses (and its dependencies, in dependency order) is inlined into `derive` of each stage that uses it, as an ordinary derived column, ahead of the spec's own derived columns. So a saved screen is self-contained and reproducible: its text, its run key and its fingerprint contain the formulas, and later edits to the library never change an old run. The editor keeps the references by name; only the saved/run spec is expanded.
- Operations (registry): `column_define(name, expr, note="", overwrite=False)`, `column_delete(name)`; `screen_schema` lists them in groups "Starter columns" and "My columns" with `expr`, coverage and range on the as-of snapshot. `screen_preview` accepts `uses` expansion transparently and reports which named expressions a spec used.
- TUI (Screen tab): schema panel shows the two groups; Enter inserts the name; a button "Save row as my column" turns the selected Derived row into a named expression (asks for a note); `Del` on a "My columns" row deletes it after a confirmation line; the funnel notes name the expansion ("shy = coalesce(div_yield, 0) + ...").
- Out of scope: per-user sharing, parameters (`x(col, n)`), multi-line functions.
