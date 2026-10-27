# Data acquisition checklist

Network from the research environment is blocked for every data host below. For each
source: allow the host in the environment network policy (or download elsewhere and copy the
file in), save into a `data/` folder, then load with the matching adapter. Nothing here has
been downloaded yet.

## Free sources

- [ ] **SEC Financial Statement Data Sets** (US point-in-time fundamentals)
  - URL: `https://www.sec.gov/files/dera/data/financial-statement-data-sets/{YYYY}q{N}.zip`, 2009q2 onward
  - Cost / licence: free, public domain; SEC asks for a descriptive User-Agent with contact email and <= 10 requests/s
  - Fills: sales, eps (+prev, avg3, avg3 10y ago), bvps, dividend, current_ratio, lt_debt, net_current_assets, roa, cfo_to_assets, leverage, shares, margins, turnover, ebit, invested_capital, loss_years_*; `date` = filing date
  - Not filled: price, market_cap, enterprise_value, sector, div_years
  - Also need a cik -> ticker map: `https://www.sec.gov/files/company_tickers.json`
  - Hosts to allow: `www.sec.gov`
  - Adapter: `realdata/sec_fsds.py` (`fetch`, `read_zip`, `to_fundamentals`)
- [ ] **Bank of Canada Valet**
  - URL: `https://www.bankofcanada.ca/valet/observations/{SERIES}/json?start_date=&end_date=`
  - Series: FXUSDCAD (USD/CAD), V80691311 (prime), V41690973 (CPI all-items, monthly), V39079 (overnight target)
  - Cost / licence: free; Bank of Canada terms of use, attribution
  - Fills: macro series (FX, risk-free proxy, inflation) for extended metrics
  - Hosts: `www.bankofcanada.ca`
  - Adapter: `realdata/boc_valet.py` (`fetch`, `load`, `to_macro_csv`)
- [ ] **Statistics Canada, CPI table 18-10-0004** (all-items CPI, monthly, provincial detail)
  - URL: `https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810000401` (CSV download link on the page)
  - Cost / licence: free, Statistics Canada Open Licence
  - Fills: inflation deflator (cross-check of Valet V41690973)
  - Hosts: `www150.statcan.gc.ca`
  - Adapter: none yet; write date,series,value CSV by hand
- [ ] **FRED** (St. Louis Fed)
  - URL: `https://fred.stlouisfed.org/graph/fredgraph.csv?id={ID}`; IDs: CPIAUCSL, DPRIME, DEXCAUS, DGS3MO
  - Cost / licence: free; series carry their own source terms (these four are public-domain government data); an API key is needed only for the JSON API
  - Fills: US CPI, prime rate, USD/CAD, 3-month T-bill (risk-free) for macro tables
  - Hosts: `fred.stlouisfed.org`
  - Adapter: none yet; csv is `DATE,{ID}` and maps directly to the macro csv
- [ ] **Stooq** (free daily prices, including TSX ETFs, e.g. `xiu.ca`, `xic.ca`)
  - URL: `https://stooq.com/q/d/l/?s=xiu.ca&i=d`
  - Cost / licence: free for personal use, no redistribution; prices are split-adjusted, not guaranteed dividend-adjusted, so check before treating as total return
  - Fills: prices / index proxies
  - Hosts: `stooq.com`
  - Adapter: none yet

## Paid sources

- [ ] **EODHD** (fundamentals + prices, global incl. TSX)
  - URL: `https://eodhd.com/api/...`; cost: paid, roughly USD 20-100/month depending on plan
  - Licence: personal / internal use; redistribution forbidden
  - Fills: prices incl. adjusted close, fundamentals (check point-in-time filing dates; many fields are restated, which creates look-ahead)
  - Hosts: `eodhd.com`
- [ ] **TMX, S&P Capital IQ, Refinitiv / LSEG** (institutional)
  - Cost: five-figure annual subscriptions; enterprise licence
  - Fills: point-in-time Canadian fundamentals, delisted names (survivorship), index constituents history
  - Access: vendor feed or file delivery, not a public host

## Index total-return series

- S&P/TSX Composite TR is not free. Use proxies: `XIC` / `XIU` adjusted close (iShares, from Stooq or EODHD), with a note that the ETF MER (about 0.06% for XIC) is already deducted, so set the benchmark `mer=0`.
- S&P 500 TR: proxy with `SPY` adjusted close, or FRED `SP500` (price only, no dividends).
