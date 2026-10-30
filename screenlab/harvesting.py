"""Tax-loss harvesting with the CRA superficial-loss rule (ROADMAP Phase 3, docs/TAXES.md "Loss harvesting").

`Config.tax.harvest = TaxLossHarvest(...)` adds a stage to `backtest.simulate`, after Weighting:

    * at a rebalance (`when` "rebalance" / "both") and/or at the last bar of each calendar year (`when` "year_end" /
      "both") every held name whose unrealized loss is beyond `threshold_pct` (and `min_loss`) is sold, which realizes the
      loss in the TaxStage ledger; harvesting overrides a hold-rule "keep" for that name;
    * the superficial-loss rule is enforced on EVERY loss sale of the run (harvested or screen-driven): buying the same
      name within `superficial_days` calendar days after the sale, or having bought it within that many days before a
      partial sale, denies the loss in proportion to the shares bought; the denied loss is added to the ACB of the
      shares bought (exactly as CRA does) and taken out of the year the loss was realized;
    * `replacement` decides what happens to the money and to the name for the window:
        "cash"      proceeds wait in cash; the screen may buy the name back (inside the window the loss is then denied)
        "stay_out"  proceeds wait in cash and the name is kept out of the picks until the window has passed
        "similar"   as stay_out, but the screen's next-ranked name not held (`Screen.ranked`) is held for the window,
                    then swapped back for the original name (the first bar after the window)

The records are `Result.harvest` (one row per loss sale; `source` "harvest" = the stage chose it, "screen" = the screen
sold it anyway) and the report columns `Harvested/yr`, `TaxDeferred/yr` (`harvest_columns`).
Amounts are pre-tax wealth units (start wealth = 1.0), like the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .serial import serializable

WHEN = ("year_end", "rebalance", "both")
REPLACEMENT = ("cash", "stay_out", "similar")
HARVEST_COLS = ["date", "name", "source", "unrealized_pct", "loss", "denied", "net_loss", "acb_added", "days_out",
                "replacement", "rebought", "window_end"]
HARVESTED_YR = "Harvested/yr"
TAXDEFERRED_YR = "TaxDeferred/yr"
_EPS = 1e-12


@serializable("TaxLossHarvest")
@dataclass(frozen=True)
class TaxLossHarvest:
    """Tax-loss harvesting settings (`Config.tax.harvest`). NOT TAX ADVICE.

    threshold_pct    sell a held name when its unrealized loss exceeds this share of its cost (0.10 = 10%)
    min_loss         ... and the loss is at least this many dollars (pre-tax wealth x `TaxConfig.capital`)
    when             "year_end" (last bar of each calendar year) | "rebalance" | "both"
    superficial_days calendar days before and after a sale in which a repurchase makes the loss superficial (30)
    replacement      "cash" | "stay_out" | "similar" (module docstring)
    report_deferral  the report also runs the same portfolio without harvesting once, for `TaxDeferred/yr`
    """
    threshold_pct: float = 0.10
    min_loss: float = 0.0
    when: str = "year_end"
    superficial_days: int = 30
    replacement: str = "cash"
    report_deferral: bool = True

    def __post_init__(self):
        if self.when not in WHEN:
            raise ValueError(f"when must be one of {WHEN}, got {self.when!r}")
        if self.replacement not in REPLACEMENT:
            raise ValueError(f"replacement must be one of {REPLACEMENT}, got {self.replacement!r}")
        if not 0.0 <= self.threshold_pct < 1.0:
            raise ValueError("threshold_pct must be in [0, 1)")
        if self.min_loss < 0 or self.superficial_days < 0:
            raise ValueError("min_loss and superficial_days must be >= 0")

    def start(self, mkt, taxer, screen, weighting, min_holdings: int) -> "HarvestRun":
        return HarvestRun(self, mkt, taxer, screen, weighting, min_holdings)


class HarvestRun:
    """The stage's state for one `simulate` run (see the module docstring); `simulate` calls the methods in order:
    `extra_dates`, then per step `on_rebalance` or `on_substep`, `take_new_dates`, `pre_trade`, `post_trade`, at the end
    `finish`."""

    def __init__(self, cfg: TaxLossHarvest, mkt, taxer, screen, weighting, min_holdings: int):
        self.cfg, self.mkt, self.taxer, self.screen = cfg, mkt, taxer, screen
        self.weighting, self.min_holdings = weighting, min_holdings
        self.px = mkt.prices
        self.sales: list = []                 # loss sales still open to the superficial-loss rule
        self._rows: list = []                 # every record, in order (the rows of Result.harvest)
        self.buys: dict = {}                  # name -> [(date, shares)]
        self.blocked: dict = {}               # name -> (sale date, last blocked day) for stay_out / similar
        self.pending: list = []               # (return date, original name, replacement name)
        self.new_dates: list = []
        self._initiated: dict = {}            # names sold by the stage in this step -> replacement label
        self._basis = None

    # ---- schedule
    def extra_dates(self, index: pd.DatetimeIndex, first, end) -> list:
        """The last bar of each calendar year strictly between the first rebalance and the end."""
        if self.cfg.when == "rebalance":
            return []
        s = pd.Series(index, index=index)
        last = s.groupby(index.year).last()
        return [d for d in last if first < d < end]

    def take_new_dates(self) -> list:
        out, self.new_dates = self.new_dates, []
        return out

    # ---- decisions
    def _units(self, x_dollars: float) -> float:
        return x_dollars / self.taxer.capital

    def _candidates(self, held: pd.Series, d, snap) -> pd.DataFrame:
        return self.taxer.harvest_candidates(held, self._value, self.cfg.threshold_pct, self._units(self.cfg.min_loss),
                                             d, snap)

    def _active_block(self, d) -> set:
        for n in [n for n, (_, until) in self.blocked.items() if d > until]:
            del self.blocked[n]
        return set(self.blocked)

    def _replacement(self, snap, taken: set):
        for t in self.screen.ranked(snap):
            if t not in taken and t in snap.index:
                return t
        return None

    def _return_date(self, d):
        return self.mkt.trading_day(d + pd.Timedelta(days=self.cfg.superficial_days + 1))

    def _window(self, d, name, repl):
        """Start the window for `name` sold at `d`; with a replacement, schedule the swap back."""
        until = d + pd.Timedelta(days=self.cfg.superficial_days)
        if self.cfg.replacement != "cash":
            self.blocked[name] = (d, until)
        if repl is not None:
            rd = self._return_date(d)
            self.pending.append((rd, name, repl))
            self.new_dates.append(rd)

    def on_rebalance(self, d, snap, raw, picks, prev, value, target):
        """After Weighting at a rebalance: returns (picks, target) with the harvested names out."""
        cfg = self.cfg
        self._value, self.pending = value, []
        blocked = self._active_block(d)
        picks = list(picks)
        cand = self._candidates(prev, d, snap) if cfg.when in ("rebalance", "both") and len(prev) else \
            pd.DataFrame(columns=["loss", "pct"])
        drop, wash = [], []
        for n in picks:
            if n in cand.index:
                (wash if cfg.replacement == "cash" and n in raw else drop).append(n)
        if cfg.replacement != "cash":
            drop += [n for n in picks if n in blocked and n not in drop]
        for n in wash:                                # sold and rebought at once: the whole loss is denied
            self._record(d, n, "harvest", float(cand.loc[n, "pct"]), float(cand.loc[n, "loss"]), "none",
                         denied=float(cand.loc[n, "loss"]), days_out=0, rebought=d)
        if not drop:
            return picks, target
        keep = [n for n in picks if n not in drop]
        taken = set(keep) | set(prev.index) | set(cand.index) | blocked | set(drop)
        extra = [t for t in raw if t not in picks and t not in blocked and t not in cand.index]       # fresh picks a hold-rule "keep" had displaced
        for n in drop:
            r = None
            if n not in raw:                              # only the hold rule kept it: the screen's own pick takes its place
                if extra:
                    keep.append(extra.pop(0))
            elif cfg.replacement == "similar":
                r = self._replacement(snap, taken)
                if r is not None:
                    taken.add(r)
                    keep.append(r)
            if n in cand.index:                           # a new harvest: its window starts today
                self._initiated[n] = r or "cash"
                self._window(d, n, r)
            elif r is not None:                           # kept out by an earlier window: hold r until it ends
                rd = self._return_date(self.blocked[n][0])
                self.pending.append((rd, n, r))
                self.new_dates.append(rd)
        target = self.weighting.target(snap, keep, prev, self.min_holdings)
        return keep, target

    def on_substep(self, d, snap, prev, value):
        """A year-end bar or a swap-back bar (no screen run): returns the target weights."""
        cfg = self.cfg
        self._value = value
        target = prev.copy()
        for rd, o, r in [x for x in self.pending if d >= x[0]]:          # swap back after the window
            self.pending.remove((rd, o, r))
            if r in target.index and o not in target.index and o in snap.index:
                target[o] = target.pop(r)
        if cfg.when in ("year_end", "both") and len(target):
            blocked = self._active_block(d)
            cand = self._candidates(target, d, snap)
            taken = set(target.index) | set(cand.index) | blocked
            for n in cand.index:
                w = target.pop(n)
                r = None
                if cfg.replacement == "similar":
                    r = self._replacement(snap, taken)
                    if r is not None:
                        taken.add(r)
                        target[r] = w
                self._initiated[n] = r or "cash"
                self._window(d, n, r)
        return target

    # ---- the ledger
    def pre_trade(self):
        """Before `TaxStage.trade`: remember the basis and capture the per-name gains the trade records (the sink
        `TaxStage._gains` receives them in the order of the names sold)."""
        self._basis = self.taxer.basis.copy()
        self._seen = None
        orig = self.taxer._gains

        def spy(date, gains):
            self._seen = np.asarray(gains, float).copy()
            orig(date, gains)
        self.taxer._gains = spy

    def post_trade(self, d, value, prev, target):
        """After `TaxStage.trade`: record loss sales, apply the superficial-loss rule to sales and purchases."""
        self.taxer.__dict__.pop("_gains", None)
        names = prev.index.union(target.index)
        p, t = prev.reindex(names, fill_value=0.0), target.reindex(names, fill_value=0.0)
        px = self.px.loc[d]
        sold = [n for n in names if (p[n] - t[n]) * value > 1e-15]       # the order of the gains `trade` recorded
        gain = dict(zip(sold, self._seen)) if self._seen is not None and len(self._seen) == len(sold) else {}
        for n in names:
            price = float(px.get(n, np.nan))
            if not np.isfinite(price) or price <= 0:
                continue
            sell_w, buy_w = max(p[n] - t[n], 0.0), max(t[n] - p[n], 0.0)
            if sell_w > _EPS and p[n] > 0:
                frac, sell_val = sell_w / p[n], sell_w * value
                loss = -gain.get(n, 0.0)
                if loss > 1e-15:
                    cost = float(self._basis.get(n, 0.0)) * frac
                    self._sale(d, n, loss, loss / cost if cost > 0 else np.nan, sell_val / price,
                               (p[n] - sell_w) * value / price)
            if buy_w > _EPS:
                self._buy(d, n, buy_w * value / price)
        self._initiated = {}

    def _record(self, d, n, source, pct, loss, repl, denied=0.0, days_out=None, rebought=None, shares=0.0, held_after=0.0):
        rec = dict(date=d, name=n, source=source, unrealized_pct=pct, loss=loss, denied=0.0, acb_added=0.0,
                   days_out=days_out, replacement=repl, rebought=rebought,
                   window_end=d + pd.Timedelta(days=self.cfg.superficial_days), _shares=shares, _matched=0.0,
                   _held_after=held_after)
        if denied > 0:                    # a wash (sold and rebought at once): never in the ledger, the ACB stays as it is
            rec["denied"] = rec["acb_added"] = denied
        else:
            self.sales.append(rec)
        self._rows.append(rec)
        return rec

    def _deny(self, rec, amount):
        amount = min(amount, rec["loss"] - rec["denied"])
        if amount <= 0:
            return
        rec["denied"] += amount
        rec["acb_added"] += amount
        self.taxer.deny_loss(rec["name"], rec["date"], amount)

    def _sale(self, d, n, loss, pct, shares, held_after):
        days = self.cfg.superficial_days
        src = "harvest" if n in self._initiated else "screen"
        rec = self._record(d, n, src, pct, loss, self._initiated.get(n, ""), shares=shares, held_after=held_after)
        before = sum(sh for bd, sh in self.buys.get(n, []) if 0 < (d - bd).days <= days)
        if held_after > _EPS and before > 0:                      # bought inside the 30 days before a partial sale
            self._deny(rec, loss * min(shares, before, held_after) / shares)
            rec["days_out"] = 0

    def _buy(self, d, n, shares):
        days = self.cfg.superficial_days
        left = shares
        for rec in self.sales:
            if rec["name"] != n or rec["date"] >= d:
                continue
            if rec["rebought"] is None:
                rec["rebought"] = d
            if left > _EPS and (d - rec["date"]).days <= days and rec["_shares"] > 0 and rec["loss"] - rec["denied"] > 1e-15:
                use = min(left, rec["_shares"] - rec["_matched"])
                if use > 0:
                    rec["_matched"] += use
                    left -= use
                    self._deny(rec, rec["loss"] * use / rec["_shares"])
        self.buys.setdefault(n, []).append((d, shares))

    def finish(self, curve: pd.Series) -> pd.DataFrame:
        """`Result.harvest`: the records as a DataFrame (HARVEST_COLS), days_out filled in."""
        end = curve.index[-1]
        rows = []
        for r in self._rows:
            r = dict(r)
            if r["days_out"] is None:
                r["days_out"] = 0 if r["_held_after"] > _EPS else \
                    ((r["rebought"] if r["rebought"] is not None else end) - r["date"]).days
            r["net_loss"] = r["loss"] - r["denied"]
            rows.append({k: r[k] for k in HARVEST_COLS})
        out = pd.DataFrame(rows, columns=HARVEST_COLS)
        out.attrs["wealth"] = curve
        return out


def harvested_per_year(res) -> float:
    """Net losses the stage realized per year as a share of the wealth on the day (mean over the run's years)."""
    h = res.harvest
    if h is None or h.empty:
        return 0.0
    h = h[h["source"] == "harvest"]
    yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
    if h.empty or yrs <= 0:
        return 0.0
    w = res.curve.reindex(pd.DatetimeIndex(h["date"]), method="pad").to_numpy()
    return float((h["net_loss"].to_numpy() / w).sum() / yrs)


def harvest_columns(rows: dict, results: dict, items, market, start, end, run) -> None:
    """Add `Harvested/yr` and `TaxDeferred/yr` to the report `rows` of the results that were run with
    `Config.tax.harvest`; the passive index row has neither. `items` = [(label, screen, config, ...)]; `run` =
    `backtest.run`. `TaxDeferred/yr` = (tax paid without harvesting - tax paid with it) / starting capital / years,
    the same portfolio run once more without the stage (only when `report_deferral`)."""
    for label, sc, c, *_ in items:
        res, tx = results[label], c.tax
        if tx is None or tx.harvest is None or res.harvest is None:
            continue
        rows[label][HARVESTED_YR] = harvested_per_year(res)
        if tx.harvest.report_deferral:
            base = run(sc, market, replace(c, tax=replace(tx, harvest=None)), start, end)
            capital = tx.capital if tx.capital is not None else getattr(c, "capital", 100_000)
            yrs = (res.curve.index[-1] - res.curve.index[0]).days / 365.25
            rows[label][TAXDEFERRED_YR] = float(base.tax["cum_tax_paid"].iloc[-1] - res.tax["cum_tax_paid"].iloc[-1]) \
                / capital / yrs
