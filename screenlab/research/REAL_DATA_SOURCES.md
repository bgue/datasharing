# Real data sources verified reachable from the container

Verified 2026-10-30 by downloading. Reachable hosts: `raw.githubusercontent.com`,
`github.com/<o>/<r>/releases/download/...` (release assets, 302 to
`release-assets.githubusercontent.com`), `media.githubusercontent.com/media/...`
(Git LFS content, for repos NOT blocked by the proxy), PyPI. Everything else tried returned 000 (blocked).
Scratch copies of every file below: `/tmp/claude-0/-home-user-datasharing/eb0cfb9f-d5be-583b-9aad-83405d27ccbb/scratchpad/d2/`.

## Ranked by usefulness

### 1. Johnbrick123/sp500-data (per-stock prices + cash dividends 1968-2026, point-in-time S&P 500)
- URL: `https://github.com/Johnbrick123/sp500-data/releases/download/data/prices.parquet` (235,457,961 bytes, downloaded in 9 s)
- Also: `.../releases/download/data/membership_intervals.parquet` (36 KB, 1,801 rows: ticker,start,end) and `.../no_trade_days.csv` (203 KB). README: `https://raw.githubusercontent.com/Johnbrick123/sp500-data/main/README.md`
- Span: 1968-01-02 to 2026-10-28; 12,293,158 rows; 1,895 tickers (1,742 ever S&P 500 members, dead names carry suffix like `ABI-200811`).
- Columns: `date, ticker, open, high, low, close, volume, adj_close, dividends, stock_splits, source`.
- Dividends: 148,918 rows with dividends>0 across 1,658 tickers; ~2,000-2,900 dividend rows every year from 1968. Per-share cash amounts. Sources by row: crsp / crsp-splice (1968-2006, ~80k div rows), yahoo (58k), tiingo, wiki (Quandl WIKI, 1983-2018). 1968-1995 has no open/high/low/volume.
- Spot checks: KO quarterly 1968-03-08 0.002734 (split-adjusted units); XOM 2019 quarterly 0.82/0.87/0.87/0.87 (correct); median dividend/close per payment 0.66% (~2.7% annual), median by year 1.3% in 1974-80, 0.4-0.5% 1998-2022. Plausible.
- Licence: NONE stated in repo (LICENSE 404). Data is partly derived from CRSP ("free CRSP file", per README), Yahoo, Tiingo, Quandl WIKI. Redistribution rights are unclear: use locally, do not republish the file.
- Load: `pip install pyarrow`; `pd.read_parquet(path, columns=[...])` (read only needed columns, file is large in memory) or DuckDB straight from the URL.
- Caveats: (a) `close` is as stored per `source` (Yahoo split-adjusted, Tiingo/WIKI/CRSP as traded); use `adj_close` for returns, and mixed-source dividends/splits for tax work need per-ticker care. (b) CRSP-era dividends look split-adjusted to today's share units. (c) Outliers exist: max dividend/close = 28.96, some tiny same-quarter extra rows (XOM 1970-02-17 0.005859); filter yield>0.25 and dedupe within ~20 days. (d) Universe is S&P 500 members only (large caps), not all US stocks. (e) 3,019 member-days are not real trades (halts), see `no_trade_days.csv`. (f) Some sector coverage for 2025-26 verified only against Nasdaq. (g) Only price/dividend: NO fundamentals. (h) The author's claim of 99.99% agreement with CRSP is the author's own, not independently verified here.
- Use for: taxing dividends per stock (best), dividend-yield and momentum screens with survivorship-free membership, USD. Not Canadian.

### 2. Kenneth French library, as mirrored on GitHub (US, from 1926/1927)
All values are percent per month. Downloaded and parsed.
| File | URL (raw.githubusercontent.com/...) | Span | Contents |
|---|---|---|---|
| 10 momentum deciles, VW and EW monthly, plus annual, #firms, size | `pauloguimaraes871/port_eff/main/data/10_Portfolios_Prior_12_2.csv` (311 KB) | 1927-01 to 2025-06 | Sections: VW monthly (line 10), EW monthly (1196), annual VW/EW, firm counts, size. Columns `Lo PRIOR, PRIOR 2..9, Hi PRIOR`. Source CRSP 202506 vintage |
| 10 momentum deciles VW only, older | `mlettau/Data/master/MFE230E/week-6/10_Portfolios_Prior_12_2.CSV` (67 KB) | 1927-01 to 2016-12 | 1,080 rows |
| Dividend-yield sorts (<=0, Lo30/Med40/Hi30, quintiles, deciles Lo10..Hi10) | `a91quaini/SparsePortfolioSelection/main/data-raw/managed_portfolios_monthly/Portfolios_Formed_on_D-P.csv` (208 KB) | 1927-07 to 2024-01, 1,159 months | 19 columns. One section only (appears VW; not confirmed EW/VW) |
| Book-to-market deciles etc. | same dir `Portfolios_Formed_on_BE-ME.csv` (210 KB) | 1926-07 to 2024-01 | same 19 columns |
| E/P, CF/P | same dir `Portfolios_Formed_on_E-P.csv`, `Portfolios_Formed_on_CF-P.csv` (156 KB each) | 1951-07 to 2024-01 | same 19 columns; files differ (md5 checked) |
| Also in that dir | `Portfolios_Formed_on_ME/NI/AC/OP/BETA/VAR/RESVAR.csv`, `25_Portfolios_*`, `17_Industry_Portfolios.csv` (169 KB), `25_Portfolios_ME_Prior_12_2.csv` (242 KB), `..._Prior_1_0`, `..._Prior_60_13`, `F-F_Research_Data_5_Factors_2x3.csv` (1963-07 to 2023-12) | | |
| Mkt-RF, SMB, HML, RF + annual | `BuyRisk/buyrisk.github.io/main/data/sources/french/F-F_Research_Data_Factors.csv` (52 KB) | 1926-07 to 2026-05 (1,199 months) | CRSP 202605 vintage, freshest |
| Momentum factor Mom | `BuyRisk/buyrisk.github.io/main/data/sources/french/F-F_Momentum_Factor.csv` (22 KB) | 1927-01 to 2026-05 (1,193 months) | |
| 5 factors 2x3 | `BuyRisk/.../F-F_Research_Data_5_Factors_2x3.csv` (51 KB) | 1963-07 to 2026-05 | |
| North America (US+Canada) 3 factors | `a91quaini/SparsePortfolioSelection/main/data-raw/managed_portfolios_international_monthly/North_America_3_Factors.csv` (19 KB) | 1990-07 to 2025-10 (35 y) | also `North_America_25_Portfolios_ME_Prior_12_2.csv`, `_ME_BE-ME`, `_ME_OP`, `_ME_INV`, 6-portfolio versions; 1990+ |
- Licence: Fama/French copyright notice in each file ("Copyright 2026 Eugene F. Fama and Kenneth R. French"); no reuse licence. Use for benchmarking, cite the source, do not republish.
- Load: skip header text; rows are `YYYYMM, v1, v2...`; sections end at a blank line; -99.99 / -999 = missing; convert percent to decimal. Strip whitespace in cells (`skipinitialspace=True`).
- Caveat: the 10-momentum file in `a91quaini` is not present (only 25 size x momentum); use the `port_eff` file. Different vintages differ slightly in history (CRSP revisions); do not splice vintages without checking overlap.
- Use for: momentum, value (B/M, E/P, CF/P), dividend-yield decile benchmarks, 1926+ in USD.

### 3. AQR factors with a Canada column (Canadian investor)
- `https://raw.githubusercontent.com/aditya160509/asu-honors/main/stock-sim/data/factors/Quality-Minus-Junk-Factors-Monthly__HML_Devil.csv` (316 KB): HML Devil (book-to-market value factor), columns per country incl. `CAN`, `USA`. CAN: 501 months 1984-07 to 2026-03 (41.7 y); USA: 1926-07 to 2026-03.
- `.../Quality-Minus-Junk-Factors-Monthly__QMJ_Factors.csv` (261 KB): quality factor. CAN: 441 months 1989-07 to 2026-03; USA 1957-07 to 2026-03.
- Format: pandas-exported Excel sheet; ~18 lines of header text, then `DATE` header row (line 19) and `MM/DD/YYYY` rows; values are decimal monthly returns; 23 countries. Self-financing long/short excess returns in the factor's own currency basis (see AQR Data Sources tab, not in the CSV).
- Licence: AQR copyright notices ("Copyright (c) 2013 Cliff Asness and Andrea Frazzini"); AQR's own site is blocked here so the mirror is third-party and unverified against the original. No UMD / Value-Momentum-Everywhere / BAB files found in any mirror.
- Use for: Canadian value and quality factor history beyond 25 y (value only).

### 4. Small supporting files
- `QuantConnect/Tutorials/master/Data/F-F_Research_Data_Factors.CSV` (53 KB): 1926-07 to 2020-07, Mkt-RF,SMB,HML,RF; redundant with BuyRisk.
- Python wheel `skfolio` ships `skfolio/datasets/data/sp500_dataset.csv.gz`, `factors_dataset.csv.gz`, `sp500_index.csv.gz` (already secured by another agent, 1990-2022).

## Dead ends (what was tried, status)

| Idea | Result |
|---|---|
| Quandl WIKI full mirrors on GitHub | `kmfranz/trading_pairs/WIKI_PRICES.csv` is Git LFS (235,562,224 bytes), downloadable via `https://media.githubusercontent.com/media/kmfranz/trading_pairs/master/WIKI_PRICES.csv` (206 on range request) but spans only 2014-01-02 to 2016-12-19 (3 y), 14 columns incl. `ex-dividend`, `split_ratio`. Not 25 y. Other "WIKI_PRICES.csv" hits (SamSafe/DisasterBot, adrianiurca/nyse-visualizer, faberAndro/Experiments, 14 KB) are ticker lists only (3,199 tickers); `mqharris614/data-compression/.../wiki_prices.csv` is 4 stocks 2007-2016. The WIKI data inside Johnbrick123 above (1983-2018, source=wiki) is the only usable WIKI-derived long history found |
| Kaggle "Huge Stock Market Dataset" mirrors (`aapl.us.txt`) | Search hits exist (e.g. HelesMichal/Stock_Market_Project) but not downloaded; no dividend column anyway |
| PyPI wheels | `pip download --no-deps` ok for famafrench, ffn, quantstats, pyfinance, bt, finquant, yahoo_fin, pandas-datareader, riskfolio-lib, PyPortfolioOpt, alphalens-reloaded, statsmodels, skfolio, zipline-reloaded. NO long per-stock/factor data in any: only skfolio (20 US stocks), zipline `SPY_benchmark.csv` and `treasury_curves.csv`, statsmodels demo sets. `getfamafrench`, `empyrical`, `pyfolio`: no matching distribution / build error. `famafrench` is a downloader (needs Dartmouth, blocked) |
| Git LFS ERP repo (fernando-duarte/ERP Input/*.CSV: 10_Portfolios_Prior_12_2, D-P) | raw gives 131-byte LFS pointers; `media.githubusercontent.com/media/fernando-duarte/ERP/...` 404. LFS only worked for kmfranz |
| `github.com/kmfranz/.../raw/...` and api.github.com style paths | 403 "GitHub access to this repository is not enabled for this session" (only /releases/download/ and media. hosts work, and only for some repos) |
| Fundamentals (book value, earnings, sales, buybacks) per stock, 25 y | NOT FOUND. SEC EDGAR 000; AQR, NASDAQ Data Link, FRED, StatCan, Bank of Canada, Zenodo, Hugging Face, OSF, Dataverse, archive.org, CRAN all 000. GitHub searches ("fundamentals dataset", "simfin bulk", ratios/key_metrics csv) found only small or 2019+ sets (`Busineessfree/us-quarterly-results`, 2019-2026, CC BY-NC 4.0, not downloaded; `Ishaanred/yahoo-finance-dataset` snapshot). Nothing with 25 y. SEC Financial Statement Data Sets would be 2009+ only anyway |
| TSX/Canadian per-stock prices or dividends | NOT FOUND. TSX composite index price history on GitHub: `hodacnghia/Graduation-thesis/master/%5EGSPTSE.csv` (344 KB) covers 1999-12-31 to 2018-05-04 (4,656 rows, price index only, Yahoo OHLC). Other ^GSPTSE copies are 1 to 6 years. No TSX dividends found. The only Canadian long series are the AQR CAN columns above and the Fed USDCAD already secured |
| GitHub code search for French momentum/D-P mirrors | worked (via the GitHub MCP search tool, not curl); useful query: `filename:<French file name>`; repository contents of non-session repos cannot be listed with get_file_contents (denied) |

## Suggested loading order for screenlab
1. Dividend taxation and dividend-yield/momentum screens: Johnbrick123 parquet (filter `adj_close`, `dividends`, membership via `membership_intervals`), after cleaning yield outliers.
2. Benchmarks: French 10 momentum deciles, D-P deciles, B/M deciles, Mom factor, FF3/FF5 (USD).
3. Canada: AQR HML Devil CAN (1984+) and QMJ CAN (1989+) plus Fed USDCAD to convert US series to CAD.
4. Still missing: any per-stock fundamentals, any TSX per-stock history.
