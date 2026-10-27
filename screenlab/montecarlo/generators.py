"""Scenario generators: resample a base `Dataset` into synthetic return paths (DESIGN.md K).

A generator is a frozen, serializable dataclass. `paths(base, n_paths, horizon_years, seed)` returns a
`PathSet`: for every path and every synthetic date, the base date its step was copied from
(`source_idx` / `source_dates`), and from that date the whole cross-section's return, every index's
return and the alive flags. Stocks and indices therefore always move on the same resampled dates.
`scenario_dataset(base, path_id, ...)` turns one path into a full `Dataset` (prices = cumulated path
returns, fundamentals = the base snapshots in force on each step's source date, re-dated onto the
synthetic dates), so existing screens and `Market.view` run on it unchanged.

BlockBootstrap(block_months)            fixed calendar blocks of the joint cross-section, wrap-around.
StationaryBootstrap(mean_block_months)  Politis-Romano: geometric block lengths, wrap-around.
RegimeBootstrap(signal_screen, ...)     blocks drawn from the current calm/stressed regime of the base,
                                        regime switching as a Markov chain fitted on the base.
IIDBootstrap()                          single-period resampling (no persistence; reference).

Randomness: only uniform draws laid out path-major, so path `i` of a run does not depend on `n_paths`
(`scenario_dataset(base, 3, seed=s)` equals `paths(base, 10, ..., seed=s)` path 3). Date 0 of every path
is the start anchor: its return is 0 and prices start at 1. Steps copy base returns 1..T-1, so the first
base date is never a source. A name's return on its first base date (or after a gap) is 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from ..dataset import DataCard, Dataset
from ..serial import serializable
from ..universe import AllListed, Membership

NOMINAL_PPY = {True: 252, False: 12}      # daily base -> 252 steps a year, otherwise monthly


# --------------------------------------------------------------------------- outputs
@dataclass(frozen=True, eq=False)
class ScenarioCard(DataCard):
    """A DataCard plus the provenance of a scenario: generator spec, seed and path id."""
    generator: dict = field(default_factory=dict, compare=False)
    seed: int | None = None
    path_id: int | None = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(generator=self.generator, seed=self.seed, path_id=self.path_id)
        return d

    def line(self) -> str:
        g = self.generator.get("__t", "?") if self.generator else "?"
        return f"{super().line()} [{g}, seed {self.seed}, path {self.path_id}]"


@dataclass(frozen=True, eq=False)
class PathSet:
    """Resampled paths. Shapes: P paths, N dates, S names, I indices.

    returns        float [P, N, S]  per-step simple returns, 0 where the name is not alive.
    index_returns  float [P, N, I]  the same dates for every base index (0 before an index starts).
    alive          bool  [P, N, S]  name had a price on its source date (tradable only there).
    source_idx     int   [P, N]     position in base.prices.index each step was copied from.
    source_dates   datetime64 [P, N]  the base date of that position.
    dates          the synthetic DatetimeIndex (N). Step 0 is the start anchor (return 0).
    """
    returns: np.ndarray
    index_returns: np.ndarray
    alive: np.ndarray
    source_idx: np.ndarray
    source_dates: np.ndarray
    dates: pd.DatetimeIndex
    names: tuple
    index_names: tuple
    generator: dict
    seed: int
    base_name: str

    @property
    def n_paths(self) -> int:
        return self.returns.shape[0]

    def levels(self, path_id: int | None = None) -> np.ndarray:
        """Cumulated stock levels (start 1, NaN where not alive); one path or all."""
        r, a = (self.returns, self.alive) if path_id is None else (self.returns[path_id], self.alive[path_id])
        return np.where(a, np.cumprod(1.0 + r, axis=-2), np.nan)

    def index_levels(self, path_id: int | None = None) -> np.ndarray:
        r = self.index_returns if path_id is None else self.index_returns[path_id]
        return np.cumprod(1.0 + r, axis=-2)


# --------------------------------------------------------------------------- base-data context
class _Ctx:
    """Base data as arrays, built once per `paths` call."""

    def __init__(self, base: Dataset):
        px = base.prices.sort_index()
        self.base, self.dates = base, px.index
        self.names, self.T = tuple(px.columns), len(px)
        if self.T < 3:
            raise ValueError("base dataset needs at least 3 dates")
        self.daily = _ppy(px.index) > 100
        self.ppy = _ppy(px.index)
        v = px.to_numpy(float)
        self.valid = ~np.isnan(v)
        prev_ok = np.vstack([np.zeros((1, v.shape[1]), bool), self.valid[:-1]])
        with np.errstate(invalid="ignore", divide="ignore"):
            r = v / np.vstack([np.full((1, v.shape[1]), np.nan), v[:-1]]) - 1
        self.ret = np.where(self.valid & prev_ok & np.isfinite(r), r, 0.0)
        self.index_names = tuple(base.indices.columns)
        if self.index_names:
            ix = base.indices.sort_index().reindex(px.index.union(base.indices.index)).ffill().reindex(px.index)
            iv = ix.to_numpy(float)
            ok = ~np.isnan(iv)
            ok_prev = np.vstack([np.zeros((1, iv.shape[1]), bool), ok[:-1]])
            with np.errstate(invalid="ignore", divide="ignore"):
                ir = iv / np.vstack([np.full((1, iv.shape[1]), np.nan), iv[:-1]]) - 1
            self.iret = np.where(ok & ok_prev & np.isfinite(ir), ir, 0.0)
        else:
            self.iret = np.zeros((self.T, 0))
        self.n_src = self.T - 1                       # source positions are 1..T-1

    def block_len(self, months: float) -> int:
        return max(1, int(round(months / 12.0 * self.ppy)))

    def proxy_returns(self) -> np.ndarray:
        """Returns used for volatility regimes: first index, else equal-weight mean of alive stocks."""
        if self.iret.shape[1]:
            return self.iret[:, 0]
        n = self.valid.sum(axis=1)
        return np.where(n > 0, self.ret.sum(axis=1) / np.maximum(n, 1), 0.0)


def _ppy(index: pd.DatetimeIndex) -> float:
    yrs = (index[-1] - index[0]).days / 365.25
    return (len(index) - 1) / yrs


def _wrap(start: np.ndarray, L: int, n_src: int, n: int) -> np.ndarray:
    """start [P, B] (0-based source offsets) -> positions [P, n] of consecutive blocks of length L, wrapped."""
    pos = (start[:, :, None] + np.arange(L)) % n_src
    return pos.reshape(start.shape[0], -1)[:, :n]


def _floor(u: np.ndarray, n: int) -> np.ndarray:
    return np.minimum((u * n).astype(np.int64), n - 1)


# --------------------------------------------------------------------------- generators
class Generator:
    """Base: subclasses implement `_offsets(ctx, P, N, rng) -> int[P, N]` (0-based offsets into 1..T-1)."""

    def _offsets(self, ctx: _Ctx, P: int, N: int, rng: np.random.Generator) -> np.ndarray:
        raise NotImplementedError

    def spec(self) -> dict:
        return self.to_dict()

    def paths(self, base: Dataset, n_paths: int, horizon_years: float, seed: int = 0,
              start=None, dtype=np.float64) -> PathSet:
        """`n_paths` resampled paths of `horizon_years` (252 steps a year on a daily base, 12 on a monthly one)."""
        ctx = _Ctx(base)
        N = max(2, int(round(horizon_years * NOMINAL_PPY[ctx.daily])))
        rng = np.random.default_rng(seed)
        off = self._offsets(ctx, int(n_paths), N, rng)
        src = off + 1
        r = ctx.ret[src].astype(dtype, copy=False)
        alive = ctx.valid[src]
        r[:, 0, :] = 0.0
        ir = ctx.iret[src].astype(dtype, copy=False)
        ir[:, 0, :] = 0.0
        t0 = pd.Timestamp(start) if start is not None else ctx.dates[0]
        dates = (pd.bdate_range(t0, periods=N) if ctx.daily else pd.date_range(t0, periods=N, freq="ME"))
        return PathSet(r, ir, alive, src, ctx.dates.to_numpy()[src], dates, ctx.names, ctx.index_names,
                       self.to_dict(), int(seed), base.card.name)

    def scenario_dataset(self, base: Dataset, path_id: int, *, pathset: PathSet | None = None,
                         n_paths: int | None = None, horizon_years: float | None = None, seed: int = 0,
                         start=None) -> Dataset:
        """One path as a Dataset. Pass `pathset` (from `paths`), or `horizon_years` (and `seed`, `start`);
        paths do not depend on `n_paths`, so path `path_id` is the same either way."""
        if pathset is None:
            if horizon_years is None:
                raise ValueError("scenario_dataset needs pathset= or horizon_years=")
            pathset = self.paths(base, max(n_paths or 0, path_id + 1), horizon_years, seed, start)
        return _build_dataset(base, pathset, path_id)


def _snapshots(fund: pd.DataFrame):
    f = fund.sort_values("date", kind="stable")
    dates = np.sort(f["date"].unique())
    cache = {}

    def snap(k: int) -> pd.DataFrame:
        if k not in cache:
            cache[k] = f[f["date"] <= dates[k]].groupby("ticker").tail(1)
        return cache[k]
    return dates, snap


def _build_dataset(base: Dataset, ps: PathSet, path_id: int) -> Dataset:
    if not 0 <= path_id < ps.n_paths:
        raise IndexError(f"path_id {path_id} outside 0..{ps.n_paths - 1}")
    dates = ps.dates
    prices = pd.DataFrame(ps.levels(path_id), index=dates, columns=list(ps.names))
    prices.index.name = "date"
    idx = pd.DataFrame(ps.index_levels(path_id), index=dates, columns=list(ps.index_names))
    # fundamentals: the snapshot in force on each step's source date, emitted whenever it changes
    fdates, snap = _snapshots(base.fundamentals)
    src = ps.source_dates[path_id]
    k = np.searchsorted(fdates, src, side="right") - 1
    change = np.r_[True, k[1:] != k[:-1]] & (k >= 0)
    # a block jump to the same snapshot is no change; a jump to another one is
    parts = []
    for t in np.flatnonzero(change):
        s = snap(int(k[t])).copy()
        s["date"] = dates[t]
        parts.append(s)
    fund = (pd.concat(parts, ignore_index=True) if parts else base.fundamentals.iloc[:0].copy())
    uni = base.universe
    if isinstance(uni, Membership):               # date ranges of the base calendar mean nothing here
        uni = AllListed()
    c = base.manifest
    card = ScenarioCard(**{f: getattr(c, f) for f in c.__dataclass_fields__},
                        generator=ps.generator, seed=ps.seed, path_id=int(path_id))
    card = replace(card, source=f"{c.name} resampled", name=f"{c.name} MC#{path_id}",
                   start=str(dates[0].date()), end=str(dates[-1].date()), universe=uni.fingerprint())
    return Dataset(prices, fund, idx, uni, card)


@serializable("BlockBootstrap")
@dataclass(frozen=True)
class BlockBootstrap(Generator):
    """Fixed-length calendar blocks of the joint cross-section, block starts uniform, wrap-around."""
    block_months: float = 12

    def _offsets(self, ctx, P, N, rng):
        L = ctx.block_len(self.block_months)
        B = -(-N // L)
        return _wrap(_floor(rng.random((P, B)), ctx.n_src), L, ctx.n_src, N)


@serializable("StationaryBootstrap")
@dataclass(frozen=True)
class StationaryBootstrap(Generator):
    """Politis-Romano: each step continues the block (next date, wrapped) or restarts with probability 1/mean."""
    mean_block_months: float = 12

    def _offsets(self, ctx, P, N, rng):
        p = 1.0 / ctx.block_len(self.mean_block_months)
        u = rng.random((P, N, 2))
        restart = u[..., 0] < p
        restart[:, 0] = True
        fresh = _floor(u[..., 1], ctx.n_src)
        t = np.arange(N)
        last = np.maximum.accumulate(np.where(restart, t, 0), axis=1)
        return (np.take_along_axis(fresh, last, axis=1) + (t - last)) % ctx.n_src


@serializable("IIDBootstrap")
@dataclass(frozen=True)
class IIDBootstrap(Generator):
    """Single-period resampling of the joint cross-section: no persistence at all."""

    def _offsets(self, ctx, P, N, rng):
        return _floor(rng.random((P, N)), ctx.n_src)


@serializable("RegimeBootstrap")
@dataclass(frozen=True)
class RegimeBootstrap(Generator):
    """Blocks drawn from the current regime (0 calm, 1 stressed) with a Markov switch between blocks.

    Regimes on the base dates: `signal_screen` (an `ExitScreen`; flagged = stressed), or, when None,
    stressed = rolling volatility of the first index (else the equal-weight stock mean) above its
    `stress_quantile` over `vol_months`. The switch is fitted on the base: the base is cut into
    consecutive blocks of `block_months`, each labelled by its majority regime, and transitions between
    consecutive labels are counted (+1 smoothing). `p_stay` overrides it: one float (both regimes) or a
    (calm, stressed) pair of probabilities of staying for another block. The first regime is drawn
    from the chain's stationary distribution.
    """
    signal_screen: object = None
    block_months: float = 6
    p_stay: object = None
    stress_quantile: float = 0.8
    vol_months: float = 3

    def regimes(self, ctx: _Ctx) -> np.ndarray:
        """bool[T] stressed flag per base date."""
        if self.signal_screen is not None:
            from ..data import Market
            st = self.signal_screen.state(Market(ctx.base))
            return st.reindex(ctx.dates).fillna(False).to_numpy(bool)
        r = pd.Series(ctx.proxy_returns())
        w = max(3, ctx.block_len(self.vol_months))
        vol = r.rolling(w, min_periods=w).std().to_numpy()
        thr = np.nanquantile(vol, self.stress_quantile) if np.isfinite(vol).any() else np.inf
        return np.nan_to_num(vol, nan=-np.inf) > thr

    def transition(self, stressed: np.ndarray, L: int) -> np.ndarray:
        """2x2 block-level transition matrix (rows: from calm / stressed)."""
        if self.p_stay is not None:
            a, b = (self.p_stay, self.p_stay) if np.isscalar(self.p_stay) else tuple(self.p_stay)
            return np.array([[a, 1 - a], [1 - b, b]], float)
        n = len(stressed) // L
        lab = (stressed[:n * L].reshape(n, L).mean(axis=1) >= 0.5).astype(int) if n else np.zeros(0, int)
        c = np.ones((2, 2))
        for x, y in zip(lab[:-1], lab[1:]):
            c[x, y] += 1
        return c / c.sum(axis=1, keepdims=True)

    def _offsets(self, ctx, P, N, rng):
        L = ctx.block_len(self.block_months)
        B = -(-N // L)
        stressed = self.regimes(ctx)[1:]                      # aligned to source offsets 0..T-2
        T = self.transition(stressed, L)
        pool = [np.flatnonzero(~stressed), np.flatnonzero(stressed)]
        allp = np.arange(ctx.n_src)
        pool = [p if len(p) else allp for p in pool]
        pi = np.linalg.matrix_power(T, 200)[0]               # stationary distribution
        u = rng.random((P, B, 2))
        reg = np.empty((P, B), np.int64)
        reg[:, 0] = u[:, 0, 0] >= pi[0]
        for b in range(1, B):                                 # loop over blocks, not days
            reg[:, b] = u[:, b, 0] >= T[reg[:, b - 1], 0]
        start = np.empty((P, B), np.int64)
        for g in (0, 1):
            m = reg == g
            start[m] = pool[g][_floor(u[..., 1][m], len(pool[g]))]
        return _wrap(start, L, ctx.n_src, N)
