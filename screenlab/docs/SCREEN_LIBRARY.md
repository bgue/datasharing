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

## Implemented (LA, engine side)

Code: `columns.py` (new: `ColumnLibrary`, `STARTER`, `expand_spec`, `closure`, `define_column`, `delete_column`), `spec.py`
(`size_cut_skipped`, `skips_size_cut`, `spec_columns`, `graham_columns`, `expr_names`; `ScreenSpec.build(library=None)` expands),
`screens.py` (`Screen.requires()` / `requires_list()`, `register_requires`, `FUNCTION_REQUIRES`, `GROUPS`, `source_spec`), `api.py`
(`Lab.screen_status`, `Lab.add_library_screens`), `data.py` (`high_52w`, `sma_200`, `COLUMN_INFO`), `ops/screen_builder.py`
(`column_define`, `column_delete`, `named` in `screen_schema`, `uses` in `screen_preview`, `top_optional`), `server/registry.py`
(`list_screens` rows), `ops/backtest.py` (card note), `specs/classic/*.json` (ten files). Tests: `tests/test_screen_library.py`.
User docs: `docs/SCREENS.md` "Screens that say what they need, and your own columns".

JSON shapes for clients (the TUI round):

- `lab.screen_status()` and the rows of `GET /labs/{lab}/screens` / MCP `list_screens` (`{"lab", "screens": [...]}`, built-ins in
  registry order, then the lab's own): `{"name", "n", "note", "params", "available": bool, "missing": [column, in order of use],
  "group": "Globe and Mail"|"Classic"|"Mine", "runnable": bool}`; `note` ends with "; no size cut: market cap not in the data" when
  a `top_optional` cut is skipped. `lab.screen_status()` itself is `{name: {"available", "missing", "group", "note", "runnable"}}`.
- `screen_schema` gains `"named": [{name, group: "Starter columns"|"My columns", kind, dtype, coverage, min, p50, max, example,
  description (the note), expr, missing: [snapshot columns the formula needs that the data lacks], error?}]`; `columns` is unchanged.
- `screen_preview` gains `"uses": [{name, group, expr, note}]` (dependency order, first use first) and a `"named": group` key on
  the funnel rows of inlined columns (`kind "derive"`, `step "derive <name>"`, `expr` the formula). A spec with no named
  expression has `uses: []` and no `named` keys. Cycle in the library: `ok: false`, `errors[0].where == "spec"`.
- `column_define(name, expr, note="", overwrite=False)` -> `{name, expr, note, group: "My columns", path, replaced, uses: [names it
  uses, dependencies first], warnings: [str], coverage, asof}`; 400 bad name / bad expression / cycle (message has the path) /
  expression raises on the last full snapshot; 409 name is a snapshot column, helper or starter column, or taken without `overwrite`.
  `column_delete(name)` -> `{name, deleted: true, expr, path}`; 404 none of yours, 409 starter. Both are untracked builtins.
- `screen_load(name)` of a built-in returns the file as written (named expressions by name); a saved or previewed spec is expanded.
- `ScreenSpec.build()` and `screen_save` store the expanded spec; the editor should keep names and call `screen_preview` (which expands).

### DEV
- **`group`** is not a key of the spec JSON (`ScreenSpec` has no such field and would reject it): it comes from the folder
  (`specs/globe` = "Globe and Mail", `specs/classic` = "Classic"), exposed as `screens.GROUPS`.
- **`named` list in `screen_schema`**, not rows appended to `columns` (an existing test pins `columns` to the snapshot columns and the
  Screen tab builds its pickers from it).
- **Golden**: `tests/golden/synthetic_all_screens.json` gained the ten new rows (70 lines added, no line changed): that test
  backtests every screen of the synthetic lab. `tests/test_golden.py` is untouched; the bundled-sample golden is unchanged.
- **Bundled sample**: `lab.screens` stays the four price-only function screens (`momentum_12_1`, `momentum_6m`, `momentum_12m`,
  `low_vol`: n 5, no size cut, golden-pinned) plus the library screens its data can run (`high_52w`, `trend_momentum`, added by
  `Lab.add_library_screens()` in `sp500_lab` and `Lab.real_us`). Where a lab defines a screen of the same name as a built-in, the lab's
  wins, so `momentum_12_1` and `low_vol` there are the old functions, not the 300-largest specs. `test_real_us.py::test_price_screens_only`
  was updated for the two added names.
- **Real panel**: `panel_screens()` keeps stripping the size cut of other screens but leaves a `top_optional` cut in place (the engine
  skips it and the card says so); `gross_margin`, `asset_turnover` and a few other columns the panel lacks were added to its
  `_NEEDS_FUNDAMENTALS` so `gross_profitability` is not offered there. `small_dogs` is offered (it needs `div_yield`, `price`) with the
  panel's usual "no size cut" note.
- **`requires()`** of a screen with a non-optional `top` includes `market_cap` (or `by`) and `sector` when sector lists are used; for the
  `top_optional` ones it does not. `small_dogs` therefore needs `market_cap`, `div_yield`, `price`.
- **`universe.by` as a named expression** gets a derive-only stage in front (the size cut runs before `derive`), which turns a one-stage
  spec into two stages.
- **Window definitions**: `high_52w` / `sma_200` need the whole window (252 / 200 closes including the day), else missing. They are
  computed on the total-return index and rescaled to the units of `price` (`price * level / today's level`), so `price / high_52w`
  is exact. Monthly data: 12-month high, 10-month average.
- **`shy`** treats a missing part as 0 (as specified), so a name with neither dividend nor buyback data has `shy = 0`, not missing.
- `Lab.screen_status()` has an extra key `runnable` (the lab can run the screen by name); `list_screens` rows carry it too.
- A `top_optional` screen is never a Monte Carlo "price rank" (`pipeline.price_rank_of` refuses the unknown universe key), so its picks
  stay fixed across paths.

## Implemented: TUI (LB)
Code: `tui/screen_tab.py` (pure: `needs_text`, `can_pick`, `status_groups`, `first_run_hint`, `loadable_names`, `named_row`, `named_names`, `uses_line`,
`named_step`; pane: schema rows of `named`, Delete, Save as my column, `uses` line, red cycle message, Load groups), `tui/ui.py` (`AddScreen(groups=, dim=,
footer=)`, `action_add`), `tui/session.py` (`screen_status`, `column_define`, `column_delete`, `screen_load` `loadable`, `screen_add` refuses a spec the
dataset cannot run), `tui/remote.py`. Commands in `tui/PROTOCOL.md`; user text in `tui/README.md`. Tests: `tests/test_tui_screen.py` (LB block).

- Add and Load: "Available on this dataset" (selectable; Globe and Mail first, then Classic), "Needs data this dataset lacks" (disabled rows,
  `name   needs: a, b, c, d +N more`), "Your saved screens" (Mine, dimmed when not available); a footer explains the dimming. On the bundled S&P 500 sample:
  6 available, 20 dimmed.
- Schema panel: starter and my columns beneath the data columns; `missing` rows dimmed with `needs: ...`; Delete twice removes a my column; Save as my column
  (+ Overwrite after a 409). Funnel: `uses` one-liner; derive rows labelled by group; cycle error red.
- Run / Add as scenario on a spec that needs missing columns: 409 `'x' cannot run on this dataset: needs: ...` (nothing saved or added).

### DEV
- **Load lists loadable (spec) screens only**: the Python function screens (`momentum_6m`, `momentum_12m`, `graham_*`, ...) cannot open in the editor, so they
  are in the Add dialog but not in Load. Dimmed built-ins that are spec files are in Load.
- **Existing test changed**: `test_app_load_dialog_groups_and_stage_bar` expected the headings Globe and Mail / Classic; the Load dialog now has one
  "Available on this dataset" heading (Globe first, then Classic), so the heading and index lookups there changed.
- **Missing-columns check** is the session's (`screen_add` compares `Screen.requires_list()` of the built spec with the last full snapshot), not a new engine
  operation. It applies to `screen_add` only; `add_scenario` of a spec still runs on whatever the data has.
- **Engine needs**: no engine change required. Wish: `screen_preview` could return `missing` for the spec (today the preview shows "pe is missing for 100% of names"
  in the funnel note and the schema coverage is 0), and a `GET`-style `column_list` so a client need not read `screen_schema.named`.
