# Real-data mirrors (committed)

Four small files so the box has real US data without any finance host (Stooq, FRED, SEC, Yahoo, Shiller's Yale page
are blocked from the build container; raw.githubusercontent.com is reachable). They are tidied copies of the open
`datasets/*` GitHub repositories (datahub.io "core" collections). Refresh with `make data-mirrors` or
`screenlab-data mirrors` (host: raw.githubusercontent.com). Downloaded 2026-10-30.

| File | Source URL | Rows | Span | Columns |
|---|---|---|---|---|
| `shiller_sp500_monthly.csv` | https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv | 1869 | 1871-01..2026-09 | date, sp500, dividend, earnings, cpi, long_rate |
| `usdcad_daily.csv` | https://raw.githubusercontent.com/datasets/exchange-rates/main/data/daily.csv (Country == "Canada" only) | 14560 (14001 with a rate) | 1971-01-04..2026-10-23 | date, usdcad (CAD per USD) |
| `us_cpi_monthly.csv` | https://raw.githubusercontent.com/datasets/cpi-us/main/data/cpiai.csv | 1363 | 1913-01..2026-08 | date, cpi (CPI-U, 1982-84=100) |
| `us_10y_monthly.csv` | https://raw.githubusercontent.com/datasets/bond-yields-us-10y/main/data/monthly.csv | 881 | 1953-04..2026-08 | date, y10 (percent) |

## What the files contain, and what was changed

* `shiller_sp500_monthly.csv`: Robert Shiller's monthly S&P 500 data (price = monthly average of daily closes,
  dividends and earnings = trailing four-quarter totals interpolated to monthly, per index point). Dividends and
  earnings are populated 1871-01..2023-06, CPI and the 10-year rate to 2023-09. After the Shiller sheet ends the
  repository extends `SP500` with FRED prices and writes 0 in every other column; **those zeros were turned into
  blanks** (missing) here. Nothing else was altered.
* `usdcad_daily.csv`: Federal Reserve H.10 noon buying rates via FRED (as collected by the repository). Only the
  Canada rows were kept, renamed to `usdcad`. 559 days are blank in the source (no quote, holidays) and left blank.
* `us_cpi_monthly.csv`: BLS CPI-U all items, columns renamed (`Inflation` dropped; derive it from the level).
* `us_10y_monthly.csv`: Federal Reserve H.15 10-year Treasury constant maturity, monthly average, percent.

## Licences (as stated in each repository's README / datapackage.json, read 2026-10-30)

* s-and-p-500: "No exact statement on license of original data but given size and factual nature believe one can
  assume these are public domain (and I, the maintainer, explicitly license under the ODC Public Domain Dedication
  and License (PDDL))." The data are Shiller's (http://www.econ.yale.edu/~shiller/data.htm); the README does NOT
  state a non-commercial restriction, but the licence status of the original is only presumed, not confirmed by
  Shiller. Cite: Robert Shiller, *Irrational Exuberance*, Princeton University Press.
* exchange-rates: "Licensed under the Public Domain Dedication and License (assuming either no rights or public
  domain license in source data)." datapackage.json: ODC-PDDL-1.0. Original: Federal Reserve via FRED.
* cpi-us: "made available under the Public Domain Dedication and License v1.0". Original: U.S. BLS.
* bond-yields-us-10y: "Licensed under the Public Domain Dedication and License (assuming either no rights or
  public domain license in source data)." Original: Federal Reserve H.15.

For personal research this is fine. Before redistributing the files beyond a personal copy, check the original
providers' terms (Shiller's page, FRED, BLS).

## Not in the box

No company fundamentals, no dividends per stock, no delisted companies, no Canadian prices. The 20 daily stock
prices and the S&P 500 price index come from the installed `skfolio` wheel (see `realdata/bundled.py`).
