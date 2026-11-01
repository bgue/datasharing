# Screen builder: define a screen in the TUI

Owner's ask: "an initial UI tab to set up a screen - universe to start with, what criteria to include, what criteria to exclude." Decisions: no SQL; keep the pandas-style expressions and make them better; one form layout (no SQL mode).

The engine already defines screens (spec.py: `ScreenSpec`, `ScreenBuilder`, JSON; universe `top`/`by`/`exclude_sectors`, `derive`, `filters`, `graham`, `rank`, `n`, multi-stage `stages`). What was missing: a way to see the universe schema, an *exclude* list, per-criterion feedback, validation with suggestions, a save path that survives a restart, and a TUI tab.

## Spec additions (additive; existing specs, run keys and golden tests must not change)

- `exclude: list[str]` on a stage and on the one-stage sugar: expressions; a row is dropped when the expression is true. NaN is never excluded (include filters drop NaN, exclude filters keep it). Serialised only when non-empty, so existing fingerprints and `run_key`s are byte-identical.
- `universe.include_sectors: list[str]` next to `exclude_sectors`.
- Expression helpers available in `derive`, `filters`, `exclude` and `rank` columns, in addition to the snapshot columns: `pct(col)` percentile rank 0..1 among the rows entering the stage, `q(col, p)` the p-quantile among those rows, `z(col)` z-score among those rows, `log`, `sqrt`, `abs`, `isna(col)`, `coalesce(a, b)`. Everything else in an expression is a column name, a number, a string, a list or tuple literal for `in`/`not in`, arithmetic, comparison, `and`/`or`/`not`.
- Expression safety: a new `check_expression(text, names)` parses with `ast` and allows only the constructs above (no attribute access, no calls other than the helpers, no `@`, no dunder names, no subscripts). It runs in the new operations below. Built-in specs must pass it; if any does not, report it and leave `rule_screen` unchanged.

## Operations (registry: REST, MCP, jobs, store by registration; `RunKind` enum and SAMPLES entries as for earlier ops; none of them is a tracked trial)

```python
screen_schema(asof: str | None = None) -> {
  "asof": iso date, "n_names": int, "dataset": str,
  "columns": [{"name", "group", "kind": "number|text|bool|date", "dtype", "coverage": 0..1, "min", "p50", "max",
               "example", "description"}],     # every snapshot column; text columns: top values instead of min/p50/max
  "helpers": [{"name": "pct", "signature": "pct(col)", "description": "..."} ...]
}

screen_preview(spec: dict, asof: str | None = None, show: int = 15) -> {
  "ok": bool, "asof": iso, "n_names": int,
  "errors": [{"where": "universe|derive.<name>|filters[i]|exclude[i]|rank.<col>", "message": str, "suggestion": str | None}],
  "funnel": [{"step": str, "kind": "universe|derive|include|exclude|rank|take", "expr": str | None,
              "rows_in": int, "rows_out": int, "alone": int | None,     # alone: rows an include criterion would keep by itself on rows_in
              "note": str | None}],                                     # e.g. "keeps every name", "drops every name", "pe is missing for 38% of names"
  "picks": [{"rank": int, "ticker": str, "sector": str | None, "values": {col: value}}],   # `values`: the columns the criteria and the ranking use
  "n_picks": int, "stages": int
}

screen_save(spec: dict, folder: str | None = None, overwrite: bool = False) -> {"name", "path", "registered": True}
screen_load(name: str) -> {"name", "spec": dict, "note": str}      # a registered spec screen as an editable spec; 400 for function screens
```

- The preview runs on the as-of snapshot of the active lab (point-in-time `Market.view`); `asof=None` means the last date with a full snapshot. It never trades or runs a backtest.
- Errors are collected, not raised: unknown column names get a did-you-mean suggestion (difflib against the schema and derived names), syntax errors name the position, a non-boolean filter says so.
- `screen_save` writes `<folder>/<name>.json` (default folder: `SCREENLAB_SCREENS` or `~/.screenlab/screens`), refuses to overwrite a built-in name or (without `overwrite`) an existing file, and registers the screen in the lab (`lab.define`). At startup the TUI session, REST and MCP labs load every `*.json` in that folder into `lab.screens`, so a saved screen is in the Add dialog after a restart. Invalid files are skipped with a log line, never fatal.

## TUI: the Screen tab (`tui/screen_tab.py`)

First in the tab bar, key `0` (keys `1`-`9` keep their meaning), opened first when the session has no scenarios.
- Header: name, note, dataset (read-only), as-of date with previous/next year buttons.
- Universe: top N, by (numeric column Select), include sectors, exclude sectors.
- Tables with add / edit / remove rows: Derived columns (name = expression), Include criteria, Exclude criteria, Rank (column, asc|desc), plus pick count n.
- Schema panel: filterable table of columns with type, coverage, range and description; Enter on a row inserts the column name into the focused expression box; a short helper cheat-sheet below it.
- Funnel preview (auto after an edit with a short debounce, also Preview / F5): one row per step with rows in, rows out, and for include criteria the "alone" count; cells amber when a criterion keeps everything or drops everything, red on an error with the message and suggestion; below it the picks table.
- Buttons: Load a screen into the editor (the Add dialog list, spec screens only), New, Save (Ctrl+S), Add as scenario (saves, then adds and selects the scenario and switches to Compare), Run (add and run).
- Everything goes through session commands (`screen_schema`, `screen_preview`, `screen_save`, `screen_load`, `screen_add`) so the tab works in `--serve` and `--remote` sessions and `tui_command` drives it over MCP. The form state is in `session.analyses["screen_form"]`.

## Implemented (SB1, engine side)

Code: `spec.py` (exclude, `include_sectors`, helpers, `check_expression`, `ExpressionError`, `screens_folder`,
`load_saved_screens`), `data.py` (`COLUMN_INFO`), `ops/screen_builder.py` (the four operations and
`preview_screen`), `server/registry.py` and `jobs.py` (load saved screens when a lab is built). Tests:
`tests/test_spec.py`, `tests/test_screen_builder.py`. User docs: `docs/SCREENS.md` "Exclude criteria, helpers and saved screens".

Shapes are as in the contract above. Names and JSON keys did not change. Details a client may rely on:

- **Routes.** POST `/labs/{lab}/<op>` for all four; also GET `/labs/{lab}/screen_schema?asof=` and GET
  `/labs/{lab}/screen_load/{name}`. MCP tools `screen_schema(asof, lab)`, `screen_preview(spec, asof, show, lab)`,
  `screen_save(spec, folder, overwrite, lab)`, `screen_load(name, lab)`. The `spec` argument is typed (REST 400 for an
  unknown key, a wrong type or a missing `spec`); problems with the expressions are data in `errors`, not a 4xx.
- **Status codes.** `screen_save`: 400 invalid spec (the message names the first error) or bad name, 409 built-in name
  (also any non-spec screen defined in the lab) or an existing file without `overwrite`. `screen_load`: 404 unknown
  screen, 400 for a Python function screen. A name-only spec in `screen_preview` that names a registered spec screen
  previews that screen (n may be overridden); for a function screen it is a 400.
- **`COLUMN_INFO`** (data.py): `{column: {"group", "description"}}`; groups: identity, price and size, income, balance
  sheet, valuation, quality, momentum, volatility, security. A column of a dataset that is not in it appears in
  `screen_schema` with group "other".
- **`screen_schema` columns:** text and bool columns also carry `"top": [{"value", "count"}]` (five most common);
  `min`/`p50`/`max` are null for them. Dates give min/p50/max as ISO dates.
- **`asof=None`** = the latest trading day on which at least half as many names have a price as on the busiest of
  the last 20 days (`ops.screen_builder.last_full_asof`). An explicit date snaps back to a trading day.
- **Funnel rows**, in order per stage: `universe` (always for stage 1: "universe: all names" when nothing is set; for
  later stages only when the stage has a universe), one `derive` per derived column (`note` = "<col> is missing for
  N% of names" when it has gaps), one `include` per filter (with `alone`), one `exclude` per exclude criterion
  (`alone` null), an `include` row for Graham ("graham tests (defensive, at least 4 passes)", no `expr`), a `rank` row
  (rows with a missing rank column drop out; `note` says which column), a `take` row for a stage's `keep`, and the
  final `take` row ("take N", `step` is not prefixed). In a multi-stage spec the `step` text of a stage's rows starts
  with "stage k: ". `note` joins "keeps every name" / "drops every name" with "<col> is missing for N% of names" (the
  column with the most gaps among the rows entering the step). A criterion with an error stays in the funnel as a row
  that keeps everything with `note` "error: ...". `picks` is empty and `n_picks` 0 when `ok` is false.
- **`values`** of a pick: every column used by a universe `by`, derive, filters, exclude or rank (derived columns
  included), in order of first use.
- **Saved file** is `{"name", "n", "note", <non-empty ScreenSpec keys>}`; `load_saved_screens(lab, folder=None)` never
  replaces a screen already in `lab.screens`, so built-ins cannot be shadowed.

### DEV (differences from the contract or things SB2 must know)

- **DEV: extra `where` labels.** Besides `universe`, `derive.<name>`, `filters[i]`, `exclude[i]`, `rank.<col>` the
  errors can carry `graham`, `n`, `spec` (structure: both `stages` and single-stage keys, unknown stage key) and, in a
  multi-stage spec, the same labels prefixed `stages[k].` (k from 0), e.g. `stages[1].exclude[0]`.
- **DEV: the expression whitelist has one extra form**: the argument-free `col.notna()` / `col.isna()` (also
  `notnull`, `isnull`). The built-in `specs/classic/value_momentum.json` is written `pe.notna() & pb.notna() & ...`;
  with the strict whitelist it would fail. `rule_screen` is unchanged. With this form all built-in specs
  (13 derive and filter expressions across the 12 spec screens) pass `check_expression`; without it value_momentum would not.
- **DEV: helper reference rows.** `pct/q/z` use the rows left after the stage's universe cut (derive, filters and
  exclude all see those same rows, not the survivors of the previous filter). Row-wise expressions give the same
  result as before. `pct` is `rank(pct=True)` (1/N for the lowest, 1.0 for the highest, average ties).
- **DEV: rank keys stay column names** (the contract could be read as allowing helper expressions as rank keys); derive
  an expression first, then rank by the derived column.
- **DEV: not a trial.** `picks` and `graham` do count as tracked runs today; the new operations are made untracked with
  `tracked_entry = False` and `tracking_args() -> None` (so `lab.screen_preview(...)` and the REST/MCP/job routes record
  nothing). `lab.last_trials()` stays unchanged by them and their JSON carries no `data_card` or `trials_in_family`.
- **DEV: `lab.run("screen_load", name=...)` clashes** with `Lab.run(name, **args)`; use `lab.screen_load(name)`, the REST
  route, the MCP tool or `LabRegistry.run_operation`.
- A saved screen is visible to background jobs in a spawned worker only if the worker built its lab after the save.
- `ScreenSpec.build()` omits `exclude` from `params` when it is empty, and `Stage.exclude` follows `Stage.to_dict`
  (omitted when empty), so every existing run key and fingerprint is byte-identical.

## Implemented: stages and built-ins
# SB3: the Screen tab edits multi-stage screens, every built-in round-trips, and a built-in's backtest settings travel with it.

**Stages in the form.** `analyses.screen_form.stages` is a list of stages, each `{top, by, include_sectors, exclude_sectors, derive,
include, exclude, rank, keep, graham, universe_extra}`; `current` is the stage the tab shows. The one-stage keys (`top`, `by`,
`include_sectors`, `exclude_sectors`, `derive`, `include`, `exclude`, `rank`, `extra`) mirror stage 0, so older callers keep working.
`form_to_spec` writes the one-stage keys for one stage without a `keep`, and `stages` otherwise; `spec_to_form` reads either. The tab has a
stage bar (`Stage 1 | Stage 2 | ... | + Add stage | - Remove stage`; with one stage only `+ Add stage` shows, so the single-stage look is
unchanged). Each stage has its own four tables; every stage but the last has a "keep" box (the last stage's cut is the pick count `n`,
and setting `keep` on the last stage is refused). The universe block (top N, by, sectors) shows on stage 1, and on a later stage only when
the loaded spec gives that stage a `universe`. Graham tests are never edited: they stay in their stage untouched and show as a read-only
line, "Graham tests: preset defensive, min passes 6". The preview funnel is grouped under a heading per stage ("Stage 2: rank vol_260d asc,
keep 50"). Commands: `set_screen_form` takes `stage` (the stage the fields and rows act on, default 0; a row operation may carry its own
`stage`), `add_stage` (true, or `{"index": k}` to insert), `remove_stage` (index), `select_stage` (index), `keep`; at most 8 stages. Adding a
stage after the last gives the old last stage a `keep` (50, or 2n when larger); removing the last stage clears the new last one's.

**Every built-in round-trips.** `tests/test_tui_screen.py` is parametrized over the registry's spec screens (the 8 Globe and 4 classic ones, and any
later addition): `screen_load(name)` -> form -> `form_to_spec` equals the JSON file once defaults are written out (`by` next to `top`, `sectors`
read as `include_sectors`), and the built screen gives identical picks and identical `ranked()` order on the synthetic lab at three dates, and on
`Lab.real_sp500` when the local panel exists (the built-in against the form-built screen; fundamentals screens skip there). The Graham tests and any
unknown universe keys are carried through untouched.

**Settings carried with a built-in.** A spec holds the screen only; the rebalance months and hold rule of a Globe portfolio live in
`GLOBE_PORTFOLIOS`. `screen_load(name)` now records them in `form.settings` (`{"rebalance_months": 12, "hold_rule": {"keep_within": 2.0,
"band": 0.25}}` for `bluechip_dividend_growth`; `{"rebalance_months": 1}` for the monthly ones; `{}` for the classic ones and for New). `screen_add`
(Add as scenario, Run, and with `save` under a new name) puts them in the scenario's params unless the call passes its own value
(`screen_add {"rebalance_months": 6}`); they apply to the form's own spec, not to a different `spec` argument. `hold_rule` is a new
scenario parameter (`{keep_within, band}`, validated like `config.hold_rule`) and is sent as `config.hold_rule` to backtest, Monte Carlo and plan
runs; the scenario editor shows it in two boxes. Limit: the saved JSON file does not hold the settings (no spec field exists for them);
load the saved screen again and the settings are gone until set in the editor.

**Starting points and the Load dialog.** `screen_load` with no name returns `groups` (`Globe and Mail`, `Classic`, `Your saved screens`, in
that order, built-ins in their historical order, the third from the saved-screens folder), `screens` and `starting_points`, the one-line
help text shown at the top of the tab: each Globe and classic screen by name with a description drawn from its spec `note`.

