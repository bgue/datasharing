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
