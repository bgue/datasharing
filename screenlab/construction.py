"""Portfolio construction: weighting, weight caps, turnover control, blends.

Pure functions over pandas objects; `backtest.simulate` calls them when the
matching `Config` fields are set (defaults reproduce plain equal weight).

    Config(weighting="inv_vol", max_weight=0.15, sector_cap=0.4,
           hold_rule=HoldRule(keep_within=2.0, band=0.2))
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .screens import Screen
from .serial import serializable


# --- weight functions: f(snapshot rows of the picks) -> weights summing to 1 --
def equal_weights(snap: pd.DataFrame) -> pd.Series:
    return pd.Series(1 / len(snap), index=snap.index)


def _normalize(x: pd.Series, snap: pd.DataFrame) -> pd.Series:
    x = x.astype(float).clip(lower=0)
    tot = x.sum()
    return x / tot if np.isfinite(tot) and tot > 0 else equal_weights(snap)


def cap_weights(snap: pd.DataFrame) -> pd.Series:
    """Proportional to market cap (missing cap counts as zero)."""
    return _normalize(snap["market_cap"].fillna(0.0), snap)


def inv_vol_weights(snap: pd.DataFrame) -> pd.Series:
    """Proportional to 1 / vol_1y; names with no usable vol get the average valid vol."""
    vol = snap["vol_1y"].where(snap["vol_1y"] > 0)
    vol = vol.fillna(vol.mean())
    return _normalize(1.0 / vol, snap)


WEIGHTINGS: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "equal": equal_weights, "cap": cap_weights, "inv_vol": inv_vol_weights}


def weight_fn(weighting) -> Callable[[pd.DataFrame], pd.Series]:
    if callable(weighting):
        return weighting
    try:
        return WEIGHTINGS[weighting]
    except KeyError:
        raise ValueError(f"unknown weighting {weighting!r}; use one of {sorted(WEIGHTINGS)} or a callable")


def target_weights(snap: pd.DataFrame, picks, weighting="equal", max_weight=None, sector_cap=None) -> pd.Series:
    """Weights for `picks` (summing to <= 1; any shortfall is cash)."""
    rows = snap.reindex(pd.Index(picks))
    w = weight_fn(weighting)(rows)
    w = pd.Series(w, dtype=float).reindex(rows.index).fillna(0.0).clip(lower=0)
    if w.sum() > 1 + 1e-9:
        w = w / w.sum()
    if max_weight is not None or sector_cap is not None:
        w = clip_weights(w, max_weight, sector_cap, rows["sector"] if "sector" in rows else None)
    return w


# --- clipping -----------------------------------------------------------------
def clip_weights(w: pd.Series, max_weight: float | None = None, sector_cap: float | None = None,
                 sectors: pd.Series | None = None, tol: float = 1e-12) -> pd.Series:
    """Cap each name at `max_weight` and each sector at `sector_cap`.

    Excess is handed to the still-unclipped names pro rata to their current
    weights, repeating until nothing is over a cap. If every name ends up
    clipped (e.g. n * max_weight < 1) the remainder stays in cash, so the
    result may sum to less than 1.
    """
    w = w.astype(float).copy()
    if w.empty:
        return w
    if w.sum() > 1:
        w = w / w.sum()
    if sector_cap is not None:
        if sectors is None:
            raise ValueError("sector_cap needs a sector for every name")
        sectors = sectors.reindex(w.index).fillna("?")
    frozen = pd.Series(False, index=w.index)
    for _ in range(4 * len(w) + 10):
        changed = False
        if max_weight is not None:
            over = (w > max_weight + tol) & ~frozen
            if over.any():
                w[over] = max_weight
                frozen |= over
                changed = True
        if sector_cap is not None:
            tot = w.groupby(sectors).transform("sum")
            over = tot > sector_cap + tol
            if over.any():
                w[over] = w[over] * (sector_cap / tot[over])
                frozen |= over
                changed = True
        free = 1.0 - w.sum()
        open_ = ~frozen & (w > 0)
        if free > tol and open_.any():
            w[open_] += free * w[open_] / w[open_].sum()
            changed = True
        if not changed:
            break
    return w


# --- turnover control -----------------------------------------------------------
@serializable("HoldRule")
@dataclass
class HoldRule:
    """keep_within: keep a current holding while it ranks within keep_within * n.
    band: leave a name untraded while its drifted weight is within +-band (relative) of target."""
    keep_within: float | None = None
    band: float | None = None

    def keep(self, picks: Sequence[str], ranked: Sequence[str], held, n: int | None = None) -> list[str]:
        """`picks` with held names that still rank within keep_within * n retained.

        The list keeps the size of `picks`; the best-ranked fresh picks fill the rest.
        """
        picks = list(picks)
        if not self.keep_within or not len(held):
            return picks
        n = n or len(picks)
        limit = int(np.ceil(self.keep_within * n - 1e-9))
        pos = {t: i for i, t in enumerate(ranked)}
        kept = sorted((t for t in held if pos.get(t, limit) < limit), key=pos.get)[:len(picks)]
        ks = set(kept)
        out = kept + [t for t in picks if t not in ks][:len(picks) - len(kept)]
        order = {t: i for i, t in enumerate(picks)}
        return sorted(out, key=lambda t: (order.get(t, len(order)), pos.get(t, len(pos))))

    def apply_band(self, target: pd.Series, prev: pd.Series) -> pd.Series:
        """Freeze names within the band at their drifted weight; rescale the rest to fill."""
        if not self.band or prev.empty or target.empty:
            return target
        p = prev.reindex(target.index)
        frozen = p.notna() & ((p - target).abs() <= self.band * target + 1e-12)
        if not frozen.any() or frozen.all():
            return target
        out = target.copy()
        out[frozen] = p[frozen]
        room = target.sum() - out[frozen].sum()
        rest = ~frozen
        if room <= 0:
            return target
        out[rest] = target[rest] * room / target[rest].sum()
        return out


# --- the default Weighting stage (pipeline.Weighting) -------------------------
@serializable("Weighting")
@dataclass(frozen=True)
class StandardWeighting:
    """Weighting scheme + per-name / per-sector caps + hold rule: what `Config.construction` holds by default.

    weighting   "equal" | "cap" | "inv_vol" | f(snapshot.loc[picks]) -> weights
    max_weight  per-name cap; excess redistributed pro rata, shortfall held in cash
    sector_cap  per-sector cap, same treatment
    hold_rule   turnover control (keep_within / band)"""
    weighting: object = "equal"
    max_weight: float | None = None
    sector_cap: float | None = None
    hold_rule: HoldRule | None = None

    def retain(self, picks, screen, snap, prev, min_holdings):
        hr = self.hold_rule
        if hr and hr.keep_within and len(picks) >= min_holdings and len(prev):
            return hr.keep(picks, screen.ranked(snap), prev.index, screen.n)
        return picks

    def target(self, snap, picks, prev, min_holdings):
        if len(picks) < min_holdings:
            target = pd.Series(dtype=float)
        elif self.weighting == "equal" and self.max_weight is None and self.sector_cap is None:
            target = pd.Series(1 / len(picks), index=picks)
        else:
            target = target_weights(snap, picks, self.weighting, self.max_weight, self.sector_cap)
        if self.hold_rule and self.hold_rule.band:
            target = self.hold_rule.apply_band(target, prev)
        return target


# --- screen of screens --------------------------------------------------------
def union_screen(screens: Sequence[Screen], n: int = 30, name: str | None = None) -> Screen:
    """Union of several screens' picks: dedup'd, round-robin ordered, capped at n."""
    screens = list(screens)

    def fn(s, n=n, screens=screens):
        lists = [sc(s) for sc in screens]
        out, seen = [], set()
        for i in range(max((len(x) for x in lists), default=0)):
            for lst in lists:
                if i < len(lst) and lst[i] not in seen:
                    seen.add(lst[i])
                    out.append(lst[i])
        return out[:n]

    return Screen(name or "union_" + "_".join(sc.name for sc in screens), fn, n, {"screens": screens},
                  "Union of " + ", ".join(sc.name for sc in screens))


def blend(results: Mapping[str, "Result"], weights, rebalance_months: int = 12, name: str = "blend") -> "Result":
    """Fixed-weight blend of several Results' curves, rebalanced between sleeves.

    `weights` is a dict keyed like `results` or a sequence in its order; it is
    normalized. Sleeves drift with their own curves and are reset to target
    every `rebalance_months` (calendar offsets from the common start). Sleeve
    costs are already inside each curve; the sleeve rebalance itself is free.
    Holdings are the union of the sleeves' holdings; turnover is the one-way
    sleeve-weight turnover at each reset.
    """
    from .backtest import Result
    keys = list(results)
    w = pd.Series(weights if isinstance(weights, Mapping) else dict(zip(keys, weights)), dtype=float)
    w = (w.reindex(keys) / w.reindex(keys).sum())
    if w.isna().any():
        raise ValueError("weights must cover every result")
    idx = None
    for r in results.values():
        idx = r.curve.index if idx is None else idx.intersection(r.curve.index)
    if len(idx) < 2:
        raise ValueError("curves share fewer than two dates")
    curves = pd.DataFrame({k: results[k].curve.reindex(idx) for k in keys})
    resets = [0]
    j = 1
    while True:
        i = int(idx.searchsorted(idx[0] + pd.DateOffset(months=rebalance_months * j)))
        if i >= len(idx) - 1:
            break
        if i > resets[-1]:
            resets.append(i)
        j += 1
    parts, value, turn = [], 1.0, {}
    bounds = resets + [len(idx) - 1]
    prev_w = None
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = curves.iloc[a:b + 1]
        sleeves = seg / seg.iloc[0] * w
        total = sleeves.sum(axis=1)
        turn[idx[a]] = 0.0 if prev_w is None else float((w - prev_w).abs().sum() / 2)
        parts.append(value * total.iloc[(0 if a == 0 else 1):])
        prev_w = sleeves.iloc[-1] / total.iloc[-1]
        value *= total.iloc[-1]
    curve = pd.concat(parts)
    holdings = {}
    for d in idx[resets]:
        names = []
        for r in results.values():
            ds = [x for x in r.holdings if x <= d]
            names += r.holdings[max(ds)] if ds else []
        holdings[d] = list(dict.fromkeys(names))
    turnover = pd.Series(turn)
    return Result(name, curve, holdings, turnover, pd.Series(0.0, index=turnover.index))
