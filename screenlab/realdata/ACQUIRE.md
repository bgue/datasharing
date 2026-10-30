# Data acquisition guide (Canada-first)

For an individual investor who is comfortable with spreadsheets but is not a programmer.
It tells you what data screenlab needs, where to get it, what it costs, and how to turn it into
the three CSV files the tool loads. Nothing here has been downloaded or run against real data yet.

**How to read the claims.** Facts about vendors carry a link. Where I could not confirm a fact
from a vendor page (many vendor sites were not reachable from the research environment, so
several facts rest on search-result snippets or third-party listings), the line says
**unverified, check before buying**. Prices are quoted as seen; none is a quote. Check the
vendor's own page on the day you buy. Checked on 2026-10-27.

---

## What ships in the box, real

`Lab.real_us()` runs on real US data with no network and no download. What is real, what is missing, and how your own
downloads replace it:

| Piece | Real source | Span |
|---|---|---|
| 20 US large caps, daily | skfolio's bundled sample: ADJUSTED close (dividends and splits folded into price) | 1990-01-02 .. 2022-12-28 |
| S&P 500 total return `SP500_TR` | skfolio's daily S&P 500 PRICE index + Robert Shiller's monthly dividend yield (dividend / price of the month) accrued daily | same |
| USDCAD daily, US CPI, 10-year yield | Federal Reserve / BLS series, committed in `screenlab/data/mirrors/` (licences, URLs, row counts: `SOURCES.md` there) | 1971 / 1913 / 1953 to 2026 |

Refresh the mirrors with `make data-mirrors` (or `screenlab-data mirrors`); the only host needed is
`raw.githubusercontent.com`.

**Missing, on purpose:** company fundamentals (no P/E, yield, book value: only price screens, momentum and low
volatility, run); delisted companies (the 20 names are 2022 survivors, so an equal-weight basket of them beats the
index by about 8 points a year: judge a screen against the `SP500_TR` row, which is bias-free, and against the
survivor basket, not against zero); Canadian stocks; each name's actual dividends. `dividend_mode="index_yield"`
(default) gives every name the index's yield of the month so dividend tax and US withholding appear in the tax
ledger, an approximation; `dividend_mode="none"` treats all return as price (dividends untaxed, tax understated).
`lab.data_card().warnings()` prints all of this on every report. The index is taxed with the real Shiller yield by year
(`TaxConfig.index_dividend_yield` also takes `{year: yield}`).

**Replacing it with your own data:**
1. `make data-free SEC_USER_AGENT="Your Name you@example.com"` (SEC fundamentals, Stooq prices, FRED / Bank of Canada
   macro; section 2A) and `make data-tr` (Shiller and Tiingo total-return benchmarks, section 3) on a machine that can
   reach those hosts, then `Lab.from_csv(...)` on the files in `data/` (section 4).
2. A `securities.csv` (ticker, domicile, listing, currency, wrapper, underlying_domicile, eligible_dividend) beside
   them tells the tax stage which names are foreign, which are Canadian-listed ETFs and which dividends are eligible
   (`docs/TAXES.md`); without it every name is assumed Canadian.
3. Pass your macro as `TaxConfig(macro=...)` for the CAD basis of US names (the real-data lab keeps its own in `lab.macro`).

---

## 1. What the tool needs and why

screenlab loads three files (see `data.py` and `Lab.from_csv(fundamentals, prices, indices)`):

| File | Long-format columns | Role |
|---|---|---|
| `fundamentals.csv` | `date,ticker,` then the columns below | What was **knowable** on `date`. Latest row with `date <= asof` is used. |
| `prices.csv` | `date,ticker,tri` | Total-return index per stock (dividends reinvested). Daily for timing studies, monthly otherwise. |
| `indices.csv` | `date,index,tri` | Benchmark series (optional but you want it). |
| `macro.csv` (optional) | `date,series,value` | `MacroSeries.from_csv` / `from_boc_csv`; used by `lab.extended`. |

Dataset-to-column map:

| Dataset | Columns it fills (names as in `data.py`) | Screens that need it | Min. history | Point-in-time? | Delisted names? |
|---|---|---|---|---|---|
| Daily or monthly adjusted prices per stock | `tri` in prices.csv; `price` in fundamentals | All (every backtest) | 10+ years; 20+ is better | Not applicable, but adjusted close must be dividend-adjusted | **Yes, essential**, or results are inflated |
| Shares outstanding / market cap history | `market_cap`, `shares`, `shares_prev` | Screaming Value, Stable/Frugal Dividend, Stable High-Yield, Basic Buyback (`buyback_yield = 1 - shares/shares_prev`), universe cut (`top=300`) | 10+ years | **Yes** (as reported at the time) | **Yes** |
| Income statement | `eps`, `eps_prev`, `eps_avg3`, `eps_avg3_10y_ago`, `loss_years_5y`, `loss_years_10y`, `sales`, `ebit`, `gross_margin`(+`_prev`) | Graham (needs `sales, eps_avg3, eps_avg3_10y_ago, loss_years_*`), Magic Formula, Screaming Value (`ev_ebit`), Piotroski | 11 years for the Graham 10-year tests, 3+ for others | **Yes**; restated figures leak the future | **Yes** |
| Balance sheet | `bvps`, `current_ratio`(+`_prev`), `lt_debt`, `net_current_assets`, `leverage`(+`_prev`), `invested_capital`, `enterprise_value` | Graham, net-nets (`ncav_to_mcap`), Piotroski, Magic Formula (`roic`) | 3+ years | **Yes** | **Yes** |
| Cash flow / returns | `roa`(+`_prev`), `cfo_to_assets`, `asset_turnover`(+`_prev`) | Piotroski F-score | 2+ years | **Yes** | **Yes** |
| Dividends | `dividend`, `div_years` | Dogs, Stable/Frugal Dividend, Stable High-Yield, Graham (uninterrupted dividends) | 20 years for the Graham dividend-record test | Dividends declared are public at announcement; fine | **Yes** |
| Sector | `sector` | Sector caps, `exclude_sectors=["Fin"]` | Current classification is acceptable | Not critical | Yes |
| Benchmark total return | `indices.csv` | Every "beats the index" statement | Same span as the prices | n/a | n/a (use a broad index) |
| Macro | `macro.csv` | `lab.extended` (real, FX, financing, rf) | Same span | n/a | n/a |

Price-only screens (6/12-month momentum, low volatility) need only `prices.csv`; the
`realdata/bundled.py` helpers show how a price-only fundamentals table is built.

**Why the three flags matter** ([CRITIQUE.md](../CRITIQUE.md) section 2):
- *Delisted names:* without them, an equal-weight buy-and-hold of 20 surviving names earned 17.5%/yr vs 7.4% for the index on the bundled sample. That gap is the data, not the screen.
- *Point-in-time:* restated or "as of today" fundamentals let a 1999 backtest see 2001 numbers.
- *Reporting lag:* a fiscal year ending 31 Dec is not public until the filing. Dating it at period end is look-ahead.

---

## 2. Three paths, compared

| | A. Free / low-cost starter | B. Paid retail-grade | C. Institutional |
|---|---|---|---|
| Cost | Free (your time) | Roughly USD 20 to 150 per month per vendor, as listed (see below; verify) | Library access free for students/staff; otherwise five-figure licences (unverified) |
| Effort | High: stitch several sources yourself | Medium: one API or download, then mapping | Medium to high: export from WRDS/Capital IQ screens |
| Canadian fundamentals | **None** | Some (EODHD, FMP Premium tier; check depth) | Yes (Compustat, Capital IQ) |
| US fundamentals | Yes, from 2009 | Yes | Yes, back to the 1960s or earlier (verify per product) |
| Delisted names | US filers who still file; no price history for delisted tickers | Varies by vendor (see 2B) | Yes (Compustat/CRSP-style) |
| Point-in-time quality | Good: SEC filing date is the knowable date | Mixed: usually restated "latest" statements; check | Compustat Snapshot is point-in-time; the standard Compustat file is restated (verify which you get) |
| TSX total-return index | Proxy only (ETF adjusted close) | Proxy, or index data at extra cost | S&P/TSX TR via Capital IQ/LSEG |
| Redistribution | No for prices (Stooq); public for SEC/StatCan | No | No |

### 2A. Free / low-cost starter (US-centred)

| Source | What you get | Cost / key / terms | Link |
|---|---|---|---|
| SEC Financial Statement Data Sets | US XBRL fundamentals: `sub` (one row per filing, includes `filed` date) and `num` (every reported number). Adapter: `screenlab.realdata.sec_fsds` | Free. The SEC asks automated clients to send a descriptive User-Agent with a contact email and to stay under 10 requests/second ([SEC: accessing EDGAR data](https://www.sec.gov/os/accessing-edgar-data)). Coverage begins with 2009 filings ([SEC data sets page](https://www.sec.gov/data-research/financial-statement-notes-data-sets); the 2009 start is **unverified**, check the page) | [Financial Statement Data Sets](https://www.sec.gov/data/financial-statement-data-sets) |
| SEC company tickers | CIK to ticker map (`https://www.sec.gov/files/company_tickers.json`, used by the adapter docs) | Free; contains **current** tickers only, so delisted filers will not map. Unverified exact current URL, check before use | [Accessing EDGAR data](https://www.sec.gov/os/accessing-edgar-data) |
| Stooq | Daily prices; TSX and US tickers (`xic.ca`, `aapl.us`) | Free for personal use. **Now reported to require an API key obtained via CAPTCHA, with an undisclosed daily hit quota** ([pandas-datareader issue](https://github.com/pydata/pandas-datareader/issues/1012), [Fund Manager forum, Apr 2026](https://fundmanagersoftware.com/forum/viewtopic.php?p=28786)). Terms of use and dividend-adjustment not verified: **unverified, check before relying** | [Stooq profile](https://providers.apievangelist.com/providers/stooq/) |
| FRED | `CPIAUCSL`, `DGS3MO`, `DGS10`, `DPRIME`, `BAMLC0A0CM` (credit spread), `DEXCAUS` | Free for personal non-commercial use with credit and copyright notices kept ([FRED legal](https://fred.stlouisfed.org/legal/terms/)); the JSON API needs a key and separate [API terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html). Whether the plain `fredgraph.csv` link needs no key: **unverified**. `BAMLC0A0CM` is an ICE index and may carry a copyright notice on its FRED page: check before sharing | [FRED](https://fred.stlouisfed.org/) |
| Bank of Canada Valet | `FXUSDCAD`, prime `V80691311`, CPI `V41690973`, overnight `V39079` (the adapter's series list). No authentication ([API guide listing](https://jentic.com/apis/bankofcanada)); series labels: look them up at `https://www.bankofcanada.ca/valet/lists/series/json` | Free. Terms of use: **unverified**, read the Bank's site terms. The existing adapter `screenlab.realdata.boc_valet` fetches and converts | [Valet client docs](https://github.com/tylercroberts/pyvalet) |
| Statistics Canada CPI | Table 18-10-0004-01 (monthly, not seasonally adjusted, 2002=100, formerly CANSIM 326-0020) | Free under the Statistics Canada Open Licence ([table page](https://www150.statcan.gc.ca/n1/en/cansim/326-0020); a 2014 publication states the open-licence basis, current licence text **unverified**) | [Table 18-10-0004-01](https://www150.statcan.gc.ca/n1/en/cansim/326-0020) |

**What path A cannot give you:**
1. Canadian fundamentals. The SEC data sets cover US-registered filers only (Canadian cross-listed companies that file 10-K are included; most TSX names file on SEDAR+ and are not).
2. Delisted TSX names with price history.
3. A TSX total-return index (use ETF proxies, section 3).
4. Free shares-outstanding history for market cap before a filing exists. You must compute `market_cap = price x shares` from filing share counts.

Path A is enough to test the **US** screens on 2009 onward with a US-only universe, and to check your pipeline end to end.

### 2B. Paid retail-grade (Canada + US)

| Vendor | Coverage relevant to you | Price as seen | Delisted | Point-in-time | Licence |
|---|---|---|---|---|---|
| [EODHD](https://eodhd.com/financial-apis/review-delisted-stock-companies-data) | EOD prices, fundamentals, dividends, splits; TSX tickers use `.TO`; Canadian fundamentals sourced from SEDAR reports per [its B2B page](https://eodhd.com/lp/b2b-solution) | A third-party listing shows fundamentals feed USD 59.99/month (49.99 annual), EOD All World USD 19.99/month, All-in-One USD 99.99/month ([apis.io listing](https://apis.io/plans/eodhd/eodhd-plans-pricing/)); **unverified, check eodhd.com** | Delisted symbol lists exist per exchange (1,656 for Toronto in the doc snapshot). Fundamentals only for companies delisted after 2018; EOD only for earlier ([EODHD doc](https://eodhd.com/financial-apis/review-delisted-stock-companies-data)). That caveat is the biggest survivorship limit for a 15-year TSX test | Statements are the latest vendor version, not as-first-filed: **unverified**; test with a few names. Commercial use needs a different plan | Personal use; no redistribution (verify terms) |
| [Financial Modeling Prep](https://site.financialmodelingprep.com/insights/platform/how-to-choose-the-right-financial-modeling-prep-plan-for-your-workflow) | Starter is US-only; Premium (USD 59/month, third-party figure) is the first tier listed with Canada coverage and 30 years of history; TSX tickers not explicitly confirmed ([search summary of FMP guide and third-party sites](https://multiples.vc/compare/fmp-api-alternatives)) | USD 22 / 59 / 149 per month as seen, billed annually per third parties: **unverified** | Unverified, check | Unverified | Check personal-use terms |
| Sharadar (via Nasdaq Data Link) | **US only**: prices and fundamentals; described as free of survivorship bias, including delisted tickers, with prices since 1998 ([QuantRocket on Sharadar](https://www.quantrocket.com/pricing/data/sharadar)). Price shown only behind "View Pricing" on the product page ([Nasdaq Data Link help](https://help.data.nasdaq.com/article/568-how-much-does-nasdaq-data-link-data-cost-how-do-i-find-pricing)): **price unverified** | Unverified | Yes (per QuantRocket) | Sharadar's as-reported dimensions exist per QuantRocket's example code; exact definitions **unverified** | Professional users must buy via Nasdaq Data Link; personal terms unverified |
| [Norgate Data](https://norgatedata.com/) | Survivorship-bias-free US, Australian and **Canadian** stocks. Canadian Silver: 10 years, currently listed only; Gold: 20 years plus current fundamentals; **Platinum trial page lists delisted securities, fundamentals and historical index constituents** ([subscribe](https://norgatedata.com/subscribe), [trial](https://norgatedata.com/subscribe/freetrial.php)). Subscriptions are 6 or 12 months, no monthly plan ([Norgate](https://norgatedata.com/)) | Canadian Platinum price not found; US Platinum listed at USD 346.50 / 6 months or USD 630 / 12 months. Check [packages](https://norgatedata.com/stockmarketpackages.php) | Delisted stocks stored under last known name and ticker ([FAQ](https://norgatedata.com/data-package-faq.php)) | Fundamentals are "current", i.e. not point-in-time: **unverified**. Strongest on **price history + constituents**, weak on historical fundamentals | Windows software feed; export terms unverified |
| Tiingo | US-centred; paid fundamentals add-on per [a third-party R package](https://business-science.r-universe.dev/riingo/doc/manual.html); Canadian coverage, delisted coverage and price: **unverified, check before buying** | About USD 30/month per a comparison site: **unverified** | Unverified | Unverified | Unverified |

**Practical reading for a Canadian investor.** No retail vendor above is verified to give *point-in-time Canadian fundamentals with delisted TSX companies back 15 years*. The best-supported combination found in this review:
- **Norgate Canadian Platinum** for survivorship-free TSX prices, delisted names and index-constituent history, **plus**
- **EODHD or FMP** (or a university terminal, path C) for fundamentals, accepting that fundamentals are restated and that EODHD fundamentals for pre-2018 delistings do not exist.

Ask each vendor's support, before paying, for: (1) a delisted TSX name from 2012 to 2016 with its 2013 annual statements, (2) whether statements carry a first-filed date, (3) personal-use export terms.

### 2C. Institutional

| Source | When it is worth it | Access |
|---|---|---|
| S&P Capital IQ / Compustat | You need point-in-time Canadian fundamentals, delisted TSX names and index history to publish-quality standards | Many Canadian university libraries provide Compustat through WRDS to current students, faculty and staff: [McMaster](https://library.mcmaster.ca/databases/computstat), [SFU](https://databases.lib.sfu.ca/record/61245147700003610), [U of T (staff, faculty, graduate students; Canadian and US companies)](https://mdl-data.library.utoronto.ca/items/show/24). Access is for current members only; alumni usually lose it |
| LSEG / Refinitiv, Bloomberg | You already have a terminal (some libraries have Bloomberg Labs) | Per-seat licence; export rules restrict bulk redistribution (verify) |
| [TMX Datalinx](https://tmxinfoservices.com/corporate-reference-data/historical-data) | You want exchange-grade TSX history: stock prices for TSX securities back to 1985 in the online tool; history to Oct 1998 for TSX, Mar 2009 for TSXV; delisted issues dropped from daily files after two calendar months, so use the history product | Online tool CAD 0.04 per data item; subscription or ad hoc via SFTP; contact marketdata@tmx.com. Product list: [TMX webstore](https://www.tmxwebstore.com/products/trading-summary-daily). Index constituent history **unverified** |

If you are a student, staff member or faculty, the library route is the cheapest way to get survivorship-free point-in-time Canadian data. Check the library page for your own university.

---

## 3. Benchmarks: total-return proxies

`indices.csv` needs a **total-return** series, and each index carries a `kind` (`price` or `total_return`). A price index understates the market by the dividend yield, about 2-3%/yr for Canadian equities (about 2%/yr for the S&P 500; [CRITIQUE.md](../CRITIQUE.md) risk 2), so a total-return portfolio compared with a price index looks about that much better every year. `screenlab-data free --sources shiller,tiingo` (or `make data-tr`) fetches real total-return series:

| Source | Cost | Frequency / history | Writes | Notes |
|---|---|---|---|---|
| `shiller` | free, no key | **monthly**, S&P 500 since 1871 | `indices_shiller.csv`: `SP500_TR` (`total_return`), `SP500_PRICE` (`price`); `macro_shiller.csv`: `cpi_us`, `long_rate_us` | `tri_t = tri_{t-1} * (P_t + D_t/12) / P_{t-1}`. Shiller's `D` is a trailing 12-month dividend that he **interpolates** to months, so the TR is a good approximation, not a ledger. Months with no dividend yet are in `SP500_PRICE` only. The rate is GS10 in percent. Full history is kept (`--start` is not applied). |
| `tiingo` | free key (`TIINGO_API_KEY`, sign up at tiingo.com) | **daily**, US tickers | `indices_tiingo.csv` (ETF presets such as SPY, VOO; `total_return`), `prices_tiingo.csv` (stocks) | `adjClose` is split- and dividend-adjusted, already **net of the ETF's MER** (use `mer=0`). Canadian ETFs (XIU.TO etc.) on Tiingo are **unverified**. Missing key: an error entry with this hint, never an exception. |
| `stooq` | free | daily | `indices_stooq.csv` with `kind=price` | Close only: a **price** index. |
| vendors (EODHD, FMP, TMX, Norgate; section 2B/2C) | paid | daily | your own `indices.csv` | The route for **Canadian** TR (S&P/TSX Composite TR): XIC/XIU/ZCN/VCN adjusted close from a vendor, or the index provider's TR series. |

ETF presets live in `fetch.ETF_PROXIES` (ticker, what it tracks, MER, issuer page); `screenlab-data status` prints them and suggests `IndexBenchmark(...)` lines. **Check each MER on the issuer page before use** (the table is from the project brief; XIU is quoted as 0.17% by some third-party pages). Rule of thumb: an ETF `adjClose` is already net of MER (`mer=0`); a gross index such as `SP500_TR` should be charged the MER of the fund you would actually hold.

| Want | Proxy | Notes |
|---|---|---|
| S&P 500 TR, long history | `SP500_TR` (Shiller) | Monthly, 1871+, gross of fees |
| S&P 500 TR, daily | `SPY`, `VOO` (Tiingo); `VFV` CAD-listed | USD vs CAD matters: match your stocks' currency |
| S&P/TSX Composite TR | `XIC`, `ZCN`, or `VCN` (FTSE Canada All Cap, a different index) adjusted close from a vendor | Closest to the Globe's TSX universe |
| S&P/TSX 60 TR | `XIU` adjusted close | Large caps only |

Inception dates matter: ETFs start in the 1990s or 2000s (verify on the provider's page), so an ETF proxy cannot reach back before then. Use Shiller for long US history.

**Fallback: the dividend-yield approximation** (only when no TR series exists for the index). `tri_t = tri_{t-1} * (1 + r_price + y/252)` for daily data (`y/12` monthly) with an average yield `y` from the index provider. It is an approximation, **good to roughly 2% a year at best**: the yield moves over time and the S&P 500 yield alone varied between about 1% and 4% across decades, so label the series as approximate and prefer a real TR source.

**Building a TR series from price and distributions** (spreadsheet recipe):
1. Columns: `date`, `close`, `div_per_unit` (the distribution on its ex-date, 0 otherwise).
2. Daily return: `r = (close + div_per_unit) / prev_close - 1`.
3. `tri` = 100 on the first date; each next row `tri = prev_tri * (1 + r)`.
4. Use **unadjusted** closes with this recipe, or adjusted closes with no distributions, never both (that double counts).
5. Sanity check: over ten years, `tri` CAGR should exceed price CAGR by roughly the trailing dividend yield.

**Unverified (no network when written):** the Shiller download link (shillerdata.com hosts `ie_data.xls` on a rotating blob URL, so the page is scraped for the current link; the fallback constants in `fetch.py` are unconfirmed, and the old Yale URL is reported to have stopped updating); `.xls` needs the `xlrd` package, which is not in the `data` extra (if absent, save the sheet as `.xlsx` or CSV to `data/raw/shiller/ie_data.xlsx` and re-run); Tiingo free-tier coverage of Canadian ETFs; all issuer URLs except VOO.

---

## 4. Step-by-step recipes

**One-command path (free sources).** From the repository root:
```
make data-free SEC_USER_AGENT="Your Name you@example.com"      # or: screenlab-data free --sec-user-agent "..."
make data-status      # what is in data/ and which labs can be built
make data-hosts       # hosts to allow in the cloud environment
```
This runs Steps 1 (SEC), 2 (Stooq prices, index proxies `^SPX` and `SPY.US`) and 3 (Bank of Canada, FRED, StatCan CPI) below
for you (`screenlab/realdata/fetch.py`), writes `data/fundamentals_sec.csv`, `prices_stooq.csv`, `indices_stooq.csv`,
`macro_boc.csv`, `macro_fred.csv`, `macro_statcan.csv` and `data/MANIFEST.json`, and reports every blocked host by name. The TUI's Data tab does the
same. It has **not been run against the real hosts** (the development environment had no network), the StatCan URL is
**unverified**, and Stooq's CAPTCHA key rule may make the Stooq step fail. The steps below remain the manual route and
the way to add paid data.

Do all work in a folder called `data/` (kept out of git, see section 7). You need Python with the repo's dependencies (`pandas`). If you do not program, ask someone to run steps 4-9 once; the download steps 1-3 are browser clicks.

### Step 0. Folder layout
```
screenlab/data/
  raw/sec/            # SEC quarterly zips
  raw/prices/         # per-ticker price files
  raw/boc/            # Bank of Canada JSON
  fundamentals.csv  prices.csv  indices.csv  macro.csv
```

### Step 1. SEC fundamentals (US)
1. Open the [data sets page](https://www.sec.gov/data/financial-statement-data-sets) in a browser and save each `YYYYqN.zip` into `data/raw/sec/`. Save the **quarterly** zips (the SEC also describes newer monthly/notes variants; the adapter expects `sub.txt` and `num.txt` inside the zip, which is **unverified** for the newest files).
2. Save `company_tickers.json` from the SEC (see 2A) into the same folder.
3. Note: the SEC requires your name and email in every automated request. Pass them to the adapter, `sec_fsds.fetch("2024q1", "data/sec/2024q1.zip", user_agent="Your Name you@example.com")`, or set the environment variable `SEC_USER_AGENT`; the adapter refuses to download without one. Downloading the zips by hand in a browser also works.
4. Run:
```python
import json, glob, pandas as pd
from screenlab.realdata import sec_fsds as s

cik2t = {str(v["cik_str"]): v["ticker"] for v in json.load(open("data/raw/sec/company_tickers.json")).values()}
parts = []
for z in sorted(glob.glob("data/raw/sec/*.zip")):
    sub, num = s.read_zip(z)
    parts.append(s.to_fundamentals(sub, num, cik2t))
fund = pd.concat(parts).drop_duplicates(["date", "ticker"]).sort_values(["ticker", "date"])
fund.to_csv("data/fundamentals_sec_only.csv", index=False)
```
`date` is the SEC filing date, so no extra lag is needed. The adapter leaves `price, market_cap, sector, div_years, enterprise_value` empty; step 5 fills them. If `company_tickers.json` has changed shape, inspect the file first (**unverified**).

### Step 2. Prices
Pick one:
- **Stooq (free, check the key rule first):** get the API key from the site, then download one CSV per ticker (`xic.ca`, `aapl.us`) into `data/raw/prices/`. Stooq closes are split-adjusted; dividend adjustment is **not guaranteed**, so test (section 6) before treating them as total return.
- **EODHD / FMP / Norgate:** export per-ticker adjusted close (dividend and split adjusted) as CSV. For EODHD, TSX tickers end in `.TO` and delisted tickers need the `delisted=1` listing ([EODHD doc](https://eodhd.com/financial-apis/review-delisted-stock-companies-data)).

Combine into the long format:
```python
import glob, os, pandas as pd
rows = []
for f in glob.glob("data/raw/prices/*.csv"):
    t = os.path.basename(f).split(".csv")[0].upper()
    d = pd.read_csv(f)                                  # columns vary by vendor: edit the two names below
    d = d.rename(columns={"Date": "date", "Adj Close": "adj"})   # use the dividend-adjusted close column
    d["ticker"] = t
    rows.append(d[["date", "ticker", "adj"]])
p = pd.concat(rows)
p["date"] = pd.to_datetime(p["date"])
p = p.sort_values(["ticker", "date"])
p["tri"] = p["adj"] / p.groupby("ticker")["adj"].transform("first") * 100   # level only matters relatively
p[["date", "ticker", "tri"]].to_csv("data/prices.csv", index=False)
```
**Keep delisted tickers**: do not drop a ticker because it has no data today. If a vendor's adjusted close stops on the delisting date, leave the series ending there; `Config.delist_return` is charged when a holding disappears. Do not forward-fill past the last price.

### Step 3. Benchmark and macro
```python
# benchmark: XIC adjusted close (or the TR you built in section 3)
b = p[p.ticker == "XIC.TO"][["date", "tri"]].assign(index="XIC_TR")[["date", "index", "tri"]]
b.to_csv("data/indices.csv", index=False)
```
Bank of Canada (adapter exists; needs `www.bankofcanada.ca` open):
```python
from screenlab.realdata import boc_valet as b
b.fetch(["FXUSDCAD", "V80691311", "V41690973", "V39079"], start="2000-01-01", cache_path="data/raw/boc/boc.json")
b.to_macro_csv(b.load("data/raw/boc/boc.json"), "data/macro.csv")
```
Load later with `MacroSeries.from_boc_csv("data/macro.csv")` (maps `FXUSDCAD` to fx, prime percent to decimal, CPI). Note `V39079` is described by the adapter as the overnight target rate; confirm series labels at the Valet list URL in 2A.

FRED series by hand (browser download of each series as CSV): rename columns to `date,series,value` (series = `DGS3MO` etc.) and stack with the file above; `MacroSeries.from_csv` accepts it. For the 3-month T-bill as risk-free, remember percent vs decimal (the Bank of Canada loader divides by 100; FRED rates are also in percent and you should divide yourself).

### Step 4. Canadian fundamentals (from a paid vendor or library export)
Map the vendor's fields to the `data.py` columns. Minimum viable set for the Globe-style screens: `price, market_cap, shares, shares_prev, eps, sales, ebit, enterprise_value, dividend, sector`. Add `bvps, current_ratio, lt_debt, net_current_assets` for Graham/net-nets and the Piotroski group for F-score. Compute in a spreadsheet:
- `enterprise_value = market_cap + total debt + preferred + minority interest - cash` (the Globe's exact definition is unknown, see README note on `screaming_value`; document yours).
- `shares_prev` = shares four quarters earlier.
- `eps_avg3` = mean of the last three annual EPS; `eps_avg3_10y_ago` = the same average ten years before; `loss_years_5y` / `_10y` = count of loss years.
- `div_years` = consecutive years of dividends paid.

### Step 5. Apply the reporting lag
Use the **filing date** whenever the source gives it. If it gives only the fiscal period end, set:

`date = period_end + lag`

```python
import pandas as pd
f = pd.read_csv("data/fundamentals_vendor.csv", parse_dates=["period_end"])
LAG_DAYS = 120        # annual: conservative; quarterly: 60
f["date"] = f["period_end"] + pd.to_timedelta(LAG_DAYS, unit="D")
```
A conservative default is 90 to 120 days for annual statements and 45 to 60 days for quarterly ones. Canadian filing deadlines differ by issuer size (check the securities rules for your names; I did not verify them). When in doubt, use the longer lag: it costs a little return and removes look-ahead.

If price, market cap and enterprise value only exist at the statement date, the tool re-marks them with the price index each day (see `data.py`); you may leave daily values blank and supply them at each fundamentals row.

### Step 6. Merge SEC (or vendor) fundamentals with prices
Fill `price` and `market_cap` on each fundamentals row from `prices.csv`/shares at that `date` (use the last price on or before `date`):
```python
import pandas as pd
f = pd.read_csv("data/fundamentals_sec_only.csv", parse_dates=["date"]).sort_values("date")
px = pd.read_csv("data/raw/prices_unadjusted.csv", parse_dates=["date"]).sort_values("date")  # raw close, not adjusted
m = pd.merge_asof(f, px, on="date", by="ticker", direction="backward")
f["price"] = m["close"]
f["market_cap"] = f["price"] * f["shares"]
f.to_csv("data/fundamentals.csv", index=False)
```
`price` in fundamentals must be a real price (not a total-return index); P/E and yield are computed from it.

### Step 7. Build the "TSX 300 at each date" universe
You do **not** build a fixed list. The tool applies `universe(top=300)` on the `market_cap` column of each snapshot, so the 300 largest are chosen at each rebalance date. For that to be right:
1. `fundamentals.csv` must contain **every listed company** (including those later delisted), each with a `market_cap` row dated before every rebalance in which it could qualify. Rows are carried forward until the next filing.
2. Restrict to common stocks before loading: drop ETFs, trusts, income funds and units (the README lists this as a gap for `screaming_value`).
3. Use `market_cap` in **CAD**. Convert USD-denominated listings using FX from step 3, or drop them, otherwise the ranking mixes currencies.
4. Spot-check one past date (for example June 2012): sort by `market_cap` and confirm names you remember as large then (names that were later acquired) appear in the top 300.

### Step 8. Load
```python
from screenlab.api import Lab
lab = Lab.from_csv("data/fundamentals.csv", "data/prices.csv", "data/indices.csv")
```
(or simply `from screenlab import Lab`). Then run the checks in section 6 before any backtest.

### Step 9. Daily vs monthly
Daily prices cost more disk and time but are required for timing studies; resample to month-end for a first pass: `p.groupby("ticker").resample("ME", on="date").last()`.

---

## 5. Network allow-list for this cloud environment

Per the [cloud environment docs](https://code.claude.com/docs/en/cloud-environments#network-access) (read before adding anything): environment settings, **Network access**, **Allowed domains**. Add only the hosts you use. Hostnames below come from the repo's adapters and the links above; confirm each by trying one download.

| Source | Hosts to allow |
|---|---|
| SEC data sets, tickers JSON | `www.sec.gov` |
| Bank of Canada Valet | `www.bankofcanada.ca` |
| Statistics Canada CPI | `www150.statcan.gc.ca` |
| FRED (CSV links) | `fred.stlouisfed.org` |
| FRED JSON API (needs key) | `api.stlouisfed.org` |
| Stooq | `stooq.com` (also the CAPTCHA key page; bulk host unverified) |
| Shiller S&P 500 (`shiller`) | `shillerdata.com`, `img1.wsimg.com` (file host **unverified**) |
| Tiingo (`tiingo`) | `api.tiingo.com` |
| EODHD | `eodhd.com` |
| Financial Modeling Prep | `financialmodelingprep.com` (host name for the API is **unverified**; the docs site is `site.financialmodelingprep.com`) |
| Nasdaq Data Link (Sharadar) | `data.nasdaq.com` (**unverified**) |
| Norgate | Windows desktop software, not an API reachable from the cloud: download elsewhere and copy files in |
| TMX | SFTP/web store: download elsewhere and copy files in |

Alternative: skip network access entirely. Download in your own browser, put the files in `screenlab/data/`, and push or upload them to the environment. This is also the safer route for paid data (your API key never enters the cloud environment).

---

## 6. Data-quality checklist (before you trust a result)

Run these in order; fix before moving on.

1. **Survivorship test.** Compute an equal-weight, rebalanced portfolio of *all* names in your universe and compare to the index (`XIC` TR). Equal-weight small/mid caps usually beat cap-weight by 1-3%/yr, not 10. A gap of 5+ points a year, or the bundled sample's 17.5% vs 7.4%, means delisted names are missing. Also report: share of names alive on the last date (should be well under 100% over 15 years) and the number of names per year (a TSX-300 universe should stay near 300, not shrink).
2. **Split and dividend adjustment.** For 3 tickers with known splits/special dividends: confirm no single-day return beyond +/-40% unless it was real; confirm `tri` CAGR beats raw-price CAGR by about the yield for a dividend payer; confirm a non-payer shows equal values. Stooq in particular: check that adjusted close matches a second source for a dividend-heavy name.
3. **Reporting-lag check.** Pick one company and compare its `date` in `fundamentals.csv` with the actual filing date on the issuer's site or the SEC filing index. `date` must be on or after the filing. Plot the median gap `date - period_end` across all rows; it should be 45 to 120 days, never 0.
4. **Duplicates and ticker changes.** Check `fundamentals.csv` for repeated `(date, ticker)`; check that a ticker re-used by a different company after a merger does not link two companies' histories. Prefer a vendor unique ID (CIK, ISIN, vendor code) in the ticker column. Norgate stores delisted stocks under last known name/ticker ([FAQ](https://norgatedata.com/data-package-faq.php)), so old ticker symbols can differ from what you remember.
5. **Currency mixing.** TSX cross-listed companies appear as `XYZ.TO` (CAD) and `XYZ` (USD). Keep one listing per company and one currency in `market_cap`. FX series from the Bank of Canada can convert a USD column.
6. **Value sanity.** No negative prices; `shares` positive; `market_cap` within a plausible range per sector; `ev_ebit` not NaN for most profitable names; `eps_avg3_10y_ago` present for names older than 11 years if you run Graham.
7. **Index kind.** Confirm the benchmark is total return (section 3). Compare with a published figure: the XIC factsheet's trailing 10-year annualized return vs your series.
8. **Reconcile one published Globe portfolio.** Pick one screen (for example the `screaming_value` article linked in `docs/SCREENS.md`), choose the same start date and holdings count, and compare the picks and the first-year return with the article. Differences of a few names are normal given EV/EBIT definitions; a completely different list means the universe or EV is wrong. This is the Phase 1 acceptance test in the roadmap.

---

## 7. Licences: what you may commit or share

| Source | May you redistribute raw data? |
|---|---|
| SEC data sets | Public data; fine to redistribute, but keep them out of git by size |
| Bank of Canada, Statistics Canada | Open government data with attribution (StatCan Open Licence; Bank terms **unverified**) |
| FRED | Personal non-commercial use, credit and keep copyright notices ([FRED terms](https://fred.stlouisfed.org/legal/terms/)); some series carry third-party copyright (e.g. BAMLC0A0CM: check) |
| Stooq, EODHD, FMP, Sharadar, Norgate, Tiingo, TMX, Compustat/Capital IQ/WRDS | **No.** Vendor terms typically allow personal or internal use only; library licences restrict to current members; treat all as non-redistributable (verify per contract) |

Rules of thumb:
- Never commit raw vendor files, API keys, or derived `fundamentals.csv` / `prices.csv` to git or share them in a public notebook.
- Safe to commit: your scripts, the data dictionary, summary statistics, backtest conclusions, and hashes of the files.
- The repo `.gitignore` now ignores `data/`. Keep API keys in environment variables (cloud environment secrets), not in notebooks.
- If you publish results, state the vendor in a footnote; most terms permit citing aggregate results but not sharing underlying series.

---

## Open items (could not verify here)

- Exact current Sharadar, Norgate Canadian and Tiingo prices; Canadian coverage for Tiingo and FMP (TSX tickers).
- Whether EODHD fundamentals are point-in-time or restated; FMP and Norgate fundamentals ditto.
- Stooq terms, current API-key mechanics, and dividend adjustment.
- The StatCan full-table CSV URL used by `realdata/fetch.py` (`https://www150.statcan.gc.ca/n1/tbl/csv/18100004-eng.zip`), the Stooq `.ca` symbol suffix for TSX names, and the FRED `fredgraph.csv` no-key access.
- Bank of Canada Valet terms of use and the labels for `V80691311`, `V41690973`, `V39079`.
- Whether the newest SEC data-set zips still contain `sub.txt`/`num.txt` and the exact 2009 start.
- Canadian securities filing deadlines used in the lag defaults.
- ETF inception dates and MERs (XIC, XIU, ZCN, VCN, VFV).
