# realdata

Adapters that turn public data into screenlab's CSV inputs.

- `ACQUIRE.md` - practical guide: what data is needed, free vs paid vendors, recipes, network allow-list, quality checklist, licences. Start here.
- `sec_fsds.py` - SEC Financial Statement Data Sets to `fundamentals.csv` rows (`read_zip`, `to_fundamentals`).
- `boc_valet.py` - Bank of Canada Valet (FX, prime, CPI) to `macro.csv` (`fetch`, `load`, `to_macro_csv`).
- `bundled.py` - offline sample data (20 S&P 500 names, survivorship-biased; for demos and tests only).

Keep downloaded data in `screenlab/data/` (git-ignored). Do not commit vendor data.
