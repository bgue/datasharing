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
                    then swapped back for the original name (the first bar after the window); "second_pick" is the
                    preferred spelling (stored as "similar")
    * Q3 -- the blocklist: a harvested name (and a name whose loss an exit-policy switch realized) is blocked until
      sale + `superficial_days` (`HarvestRun.blocked_until`, `HarvestRun.blocked(date)`). Every re-entry consults it: the
      rebalance hook, the second-pick swap and the exit policy's re-entry. `on_repick` "avoid" (default): a blocked name
      the screen picks, or the exit re-entry would buy back, is replaced by the screen's next-ranked name not held and
      not blocked (the "second pick"; "stay_out" leaves the weight in cash); "allow_denial": the name is bought back and
      the loss denied (the old behaviour of replacement "cash").
    * Q3 -- exit policies: the losses an exit switch realizes (sleeve sold into the parked asset) are recorded with
      `source` "exit" and block the name; the re-entry skips it (second pick) while the window is open.

The records are `Result.harvest` (one row per loss sale; `source` "harvest" = the stage chose it, "screen" = the screen
sold it anyway, "exit" = an exit-policy switch realized it) and the report columns `Harvested/yr`, `TaxDeferred/yr` (`harvest_columns`).
Units. `loss`, `denied`, `net_loss` and `acb_added` are in the SAME units as the tax ledger (`Result.tax`): wealth units
of the account (start = 1.0) after tax was paid out of it, so with `pay_from_portfolio` each record is the pre-tax amount
times the after-tax factor the ledger uses for the year of the sale (the factor at the end of the previous year; 1.0 in
the first year). Per year, sum(loss - denied) x `TaxConfig.capital` of the harvest-source sales is the part of
`tax["realized_losses"]` (negated) that the stage made. The pre-tax amounts are kept in `loss_pre_tax` / `denied_pre_tax`
(`acct_factor` = the factor applied); they are what `Harvested/yr` uses (a share of the pre-tax wealth on the day).
`days_out` is NaN while the name was never bought back (`rebought` is NaN too); `days_out_open` then holds the days to the
end of the data. `HarvestRun.name_days_out(result)` sums them separately.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .serial import serializable

WHEN = ("year_end", "rebalance", "both")
ON_REPICK = ("avoid", "allow_denial")                                                             # Q3
REPLACEMENT = ("cash", "stay_out", "similar", "second_pick")   # Q1: "second_pick" is the preferred alias of "similar"
HARVEST_COLS = ["date", "name", "source", "unrealized_pct", "loss", "denied", "net_loss", "acb_added", "days_out",
                "replacement", "rebought", "window_end", "replacement_name", "blocked_until",   # Q3: last two
                "loss_pre_tax", "denied_pre_tax", "acct_factor", "days_out_open"]              # ledger units; pre-tax amounts; open window
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
    replacement      "cash" | "stay_out" | "similar" / "second_pick" (module docstring)
    on_repick        Q3: "avoid" (default) | "allow_denial": what a re-pick of a name inside its window does (module docstring)
    report_deferral  the report also runs the same portfolio without harvesting once, for `TaxDeferred/yr`
    """
    threshold_pct: float = 0.10
    min_loss: float = 0.0
    when: str = "year_end"
    superficial_days: int = 30
    replacement: str = "cash"
    report_deferral: bool = True
    on_repick: str = "avoid"                                                                     # Q3

    def __post_init__(self):
        if self.replacement == "second_pick":                       # Q1: preferred alias, stored as "similar"
            object.__setattr__(self, "replacement", "similar")
        if self.when not in WHEN:
            raise ValueError(f"when must be one of {WHEN}, got {self.when!r}")
        if self.replacement not in REPLACEMENT:
            raise ValueError(f"replacement must be one of {REPLACEMENT}, got {self.replacement!r}")
        if self.on_repick not in ON_REPICK:                                                           # Q3
            raise ValueError(f"on_repick must be one of {ON_REPICK}, got {self.on_repick!r}")
        if not 0.0 <= self.threshold_pct < 1.0:
            raise ValueError("threshold_pct must be in [0, 1)")
        if self.min_loss < 0 or self.superficial_days < 0:
            raise ValueError("min_loss and superficial_days must be >= 0")

    def describe(self) -> str:
        """Q3: one line for the report and the data card, e.g. "on (threshold 10%, second pick, 30-day window)"."""
        mode = {"cash": "cash", "stay_out": "stay out", "similar": "second pick", "second_pick": "second pick"}[self.replacement]
        extra = ", repurchase allowed (loss denied)" if self.on_repick == "allow_denial" else ""
        return f"on (threshold {self.threshold_pct * 100:g}%, {mode}, {self.superficial_days}-day window{extra})"

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
        self.blocked_until: dict = {}         # Q3: name -> last blocked day (sale + superficial_days); one list for all re-entries
        self._swapped: set = set()            # Q3: names swapped for a second pick at this step (no sale to record)
        self.pending: list = []               # (return date, original name, replacement name)
        self.new_dates: list = []
        self._initiated: dict = {}            # names sold by the stage in this step -> replacement label
        self._basis = None

    # ---- schedule
    def extra_dates(self, index: pd.DatetimeIndex, first, end, exposure: pd.Series | None = None) -> list:
        """The last bar of each calendar year strictly between the first rebalance and the end. Q3: with an exit
        policy (`exposure` = its path) also every bar the exposure rises: the re-entry buys there, so the blocklist
        can swap a blocked name for a second pick."""
        out = []
        if self.cfg.when != "rebalance":
            s = pd.Series(index, index=index)
            out += [d for d in s.groupby(index.year).last() if first < d < end]
        if exposure is not None:
            up = (exposure.reindex(index).diff() > 1e-12).to_numpy()
            out += [d for d in index[up] if first < d < end]
        return sorted(set(out))

    def take_new_dates(self) -> list:
        out, self.new_dates = self.new_dates, []
        return out

    # ---- decisions
    def _units(self, x_dollars: float) -> float:
        return x_dollars / self.taxer.capital

    def _candidates(self, held: pd.Series, d, snap) -> pd.DataFrame:
        return self.taxer.harvest_candidates(held, self._value, self.cfg.threshold_pct, self._units(self.cfg.min_loss),
                                             d, snap)

    # ---- Q3: the blocklist
    @property
    def _enforce(self) -> bool:
        """Is the blocklist kept? ("stay_out" always; otherwise unless the owner allows the denial.)"""
        return self.cfg.replacement == "stay_out" or self.cfg.on_repick == "avoid"

    @property
    def _subst(self) -> bool:
        """Does a blocked name get a second pick (else its weight waits in cash)?"""
        return self.cfg.replacement == "similar" or (self.cfg.replacement == "cash" and self.cfg.on_repick == "avoid")

    def blocked(self, d) -> set:
        """Names that cannot be bought on `d` without making a harvested loss superficial."""
        d = pd.Timestamp(d)
        return {n for n, until in self.blocked_until.items() if d <= until}

    def _block(self, n, d) -> pd.Timestamp | None:
        if not self._enforce:
            return None
        until = d + pd.Timedelta(days=self.cfg.superficial_days)
        self.blocked_until[n] = max(until, self.blocked_until.get(n, until))
        return self.blocked_until[n]

    def _note_second_pick(self, n, r) -> None:
        """The open record of `n` remembers the name that was bought instead."""
        for rec in reversed(self._rows):
            if rec["name"] == n and rec.get("blocked_until") is not None:
                if rec["replacement_name"] is None:
                    rec["replacement_name"] = r
                return

    def _swap_back(self, n, r) -> None:
        """Replacement "similar": hold `r` until the window of `n` is over, then swap `n` back."""
        rd = self.mkt.trading_day(self.blocked_until[n] + pd.Timedelta(days=1))
        self.pending.append((rd, n, r))
        self.new_dates.append(rd)

    def _replacement(self, snap, taken: set):
        for t in self.screen.ranked(snap):
            if t not in taken and t in snap.index:
                return t
        return None

    def _window(self, d, name, repl):
        """Start the window for `name` sold at `d`; with a replacement (mode "similar"), schedule the swap back."""
        self._block(name, d)
        if repl is not None and self.cfg.replacement == "similar":
            self._swap_back(name, repl)

    def on_rebalance(self, d, snap, raw, picks, prev, value, target):
        """After Weighting at a rebalance: returns (picks, target) with the harvested names out."""
        cfg = self.cfg
        self._value, self.pending = value, []
        blocked = self.blocked(d)
        picks = list(picks)
        cand = self._candidates(prev, d, snap) if cfg.when in ("rebalance", "both") and len(prev) else \
            pd.DataFrame(columns=["loss", "pct"])
        drop, wash = [], []
        wash_mode = cfg.replacement == "cash" and not self._enforce       # Q3: "allow_denial" = the old "cash" arithmetic
        for n in picks:
            if n in cand.index:
                (wash if wash_mode and n in raw else drop).append(n)
        drop += [n for n in picks if n in blocked and n not in drop]       # Q3: the blocklist (empty unless enforced)
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
            elif self._subst:                             # Q3: the second pick (replacement "similar", or "cash" + "avoid")
                r = self._replacement(snap, taken)
                if r is not None:
                    taken.add(r)
                    keep.append(r)
            if n in cand.index:                           # a new harvest: its window starts today
                self._initiated[n] = r or "cash"
                self._window(d, n, r)
            elif r is not None:                           # kept out by an earlier window: hold r (until it ends, "similar")
                self._note_second_pick(n, r)
                if cfg.replacement == "similar":
                    self._swap_back(n, r)
        target = self.weighting.target(snap, keep, prev, self.min_holdings)
        return keep, target

    def on_substep(self, d, snap, prev, value, reentry: bool = False):
        """A year-end bar, a swap-back bar or an exit re-entry bar (no screen run): returns the target weights.
        `value` = the equity money (wealth x exposure); `reentry`: the exit policy's exposure rose on this bar."""
        cfg = self.cfg
        self._value = value
        target = prev.copy()
        for rd, o, r in [x for x in self.pending if d >= x[0]]:          # swap back after the window
            self.pending.remove((rd, o, r))
            if r in target.index and o not in target.index and o in snap.index:
                target[o] = target.pop(r)
        if reentry and self._enforce:                                    # Q3: the re-entry skips names inside their window
            blocked = self.blocked(d)
            taken = set(target.index) | blocked
            for n in [n for n in target.index if n in blocked]:
                w = target.pop(n)
                self._swapped.add(n)
                r = self._replacement(snap, taken) if self._subst else None
                if r is not None:
                    taken.add(r)
                    target[r] = w
                    self._note_second_pick(n, r)
                    if cfg.replacement == "similar":
                        self._swap_back(n, r)
        if cfg.when in ("year_end", "both") and len(target):
            blocked = self.blocked(d)
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

    def exit_sale(self, d, names, gains, cost, sold, kept) -> None:
        """Q3: the exit policy sold a share of the sleeve into the parked asset (`TaxStage.segment` calls this):
        per name the gain, the cost sold, the proceeds and the value still held. Losses are realized anyway: recorded
        with source "exit" and the name is blocked for the window."""
        px = self.px.loc[d]
        for n, g, c, sv, kv in zip(names, np.asarray(gains, float), np.asarray(cost, float), np.asarray(sold, float),
                                   np.asarray(kept, float)):
            price = float(px.get(n, np.nan))
            if g < -1e-15 and np.isfinite(price) and price > 0 and sv > 0:
                rec = self._sale(d, n, -g, -g / c if c > 0 else np.nan, sv / price, kv / price, source="exit")
                rec["blocked_until"] = self._block(n, d)

    def post_trade(self, d, value, prev, target, ratio: float = 1.0):
        """After `TaxStage.trade`: record loss sales, apply the superficial-loss rule to sales and purchases.
        Q3: `value` = the equity money; `ratio` = exposure before / after the bar (< 1 when the exit policy re-entered
        on it: that part of the holding was bought by the switch, which the trade does not show)."""
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
            sell_w, buy_w = max(p[n] - t[n], 0.0), max(t[n] - p[n] * ratio, 0.0)
            if sell_w > _EPS and p[n] > 0 and n not in self._swapped:
                frac, sell_val = sell_w / p[n], sell_w * value
                loss = -gain.get(n, 0.0)
                if loss > 1e-15:
                    cost = float(self._basis.get(n, 0.0)) * frac
                    self._sale(d, n, loss, loss / cost if cost > 0 else np.nan, sell_val / price,
                               (p[n] - sell_w) * value / price)
            if buy_w > _EPS and buy_w * value > 1e-15:
                self._buy(d, n, buy_w * value / price)
        self._initiated, self._swapped = {}, set()

    def _record(self, d, n, source, pct, loss, repl, denied=0.0, days_out=None, rebought=None, shares=0.0, held_after=0.0):
        rec = dict(date=d, name=n, source=source, unrealized_pct=pct, loss=loss, denied=0.0, acb_added=0.0,
                   days_out=days_out, replacement=repl, rebought=rebought,
                   window_end=d + pd.Timedelta(days=self.cfg.superficial_days), _shares=shares, _matched=0.0,
                   _held_after=held_after, replacement_name=None, blocked_until=None)
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

    def _sale(self, d, n, loss, pct, shares, held_after, source=None):
        days = self.cfg.superficial_days
        src = source or ("harvest" if n in self._initiated else "screen")
        repl = self._initiated.get(n, "cash" if source == "exit" else "")
        rec = self._record(d, n, src, pct, loss, repl, shares=shares, held_after=held_after)
        if src == "harvest":                                              # Q3: the second pick and the blocklist entry
            rec["replacement_name"] = repl if repl not in ("", "cash") else None
            rec["blocked_until"] = self.blocked_until.get(n)
        before = sum(sh for bd, sh in self.buys.get(n, []) if 0 < (d - bd).days <= days)
        if held_after > _EPS and before > 0:                      # bought inside the 30 days before a partial sale
            self._deny(rec, loss * min(shares, before, held_after) / shares)
            rec["days_out"] = 0
        return rec

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

    def _year_factors(self, curve: pd.Series, after_curve: pd.Series | None) -> dict:
        """year -> the factor `taxes.assess` scales that year's events with (share of pre-tax wealth left at the end of
        the previous year when tax is paid from the portfolio; else 1.0)."""
        out, f = {}, 1.0
        paid = after_curve is not None and getattr(self.taxer.tax, "pay_from_portfolio", False)
        ratio = (after_curve / curve) if paid else None
        for y, s in curve.groupby(curve.index.year):
            out[int(y)] = f
            if paid:
                v = float(ratio.loc[s.index[-1]])
                f = v if np.isfinite(v) else f
        return out

    def finish(self, curve: pd.Series, after_curve: pd.Series | None = None) -> pd.DataFrame:
        """`Result.harvest`: the records as a DataFrame (HARVEST_COLS), days_out filled in. `after_curve` (the after-tax
        curve of the run) puts the amounts in the ledger's units (module docstring); without it they stay pre-tax."""
        end = curve.index[-1]
        fy = self._year_factors(curve, after_curve)
        first = min(fy) if fy else None
        rows = []
        for r in self._rows:
            r = dict(r)
            if r["days_out"] is None:
                if r["_held_after"] > _EPS:
                    r["days_out"] = 0
                elif r["rebought"] is not None:
                    r["days_out"] = (r["rebought"] - r["date"]).days
                else:
                    r["days_out"] = np.nan                       # never bought back: no closed window
            r["days_out_open"] = (end - r["date"]).days if pd.isna(r["days_out"]) else np.nan
            y = r["date"].year
            f = fy.get(y, fy[max((k for k in fy if k < y), default=first)] if fy else 1.0)
            r["acct_factor"] = f
            r["loss_pre_tax"], r["denied_pre_tax"] = r["loss"], r["denied"]
            r["loss"], r["denied"], r["acb_added"] = r["loss"] * f, r["denied"] * f, r["acb_added"] * f
            r["net_loss"] = r["loss"] - r["denied"]
            rows.append({k: r[k] for k in HARVEST_COLS})
        out = pd.DataFrame(rows, columns=HARVEST_COLS)
        out.attrs["wealth"] = curve
        return out

    @staticmethod
    def name_days_out(result, source: str | None = "harvest") -> dict:
        """Name-days the stage kept names out, from `Result.harvest`, split in two: `closed` = days until the name was held
        again (sales whose `days_out` is known), `open` = days from the sale to the end of the data for names never bought
        back (`days_out_open`; a delisted or dropped name). `source` limits the records ("harvest" default, None = all).
        Returns {"closed", "open", "n_closed", "n_open"}."""
        h = result.harvest
        if h is None or h.empty:
            return {"closed": 0, "open": 0, "n_closed": 0, "n_open": 0}
        if source is not None:
            h = h[h["source"] == source]
        c, o = h["days_out"].dropna(), h["days_out_open"].dropna()
        return {"closed": int(c.sum()), "open": int(o.sum()), "n_closed": int(len(c)), "n_open": int(len(o))}


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
    net = (h["loss_pre_tax"] - h["denied_pre_tax"]) if "loss_pre_tax" in h and h["loss_pre_tax"].notna().all() else h["net_loss"]
    return float((net.to_numpy() / w).sum() / yrs)               # pre-tax amounts over pre-tax wealth


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
