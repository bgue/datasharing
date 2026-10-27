"""Monte Carlo scenario generators (DESIGN.md K)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from screenlab import BundledSP500Builder, SyntheticBuilder
from screenlab.data import Market
from screenlab.exit_signals import ExitScreen, VolRegime
from screenlab.montecarlo import (BlockBootstrap, IIDBootstrap, PathSet, RegimeBootstrap, ScenarioCard,
                                  StationaryBootstrap)
from screenlab.montecarlo.generators import _Ctx
from screenlab.screens import REGISTRY
from screenlab.serial import from_dict, to_dict


@pytest.fixture(scope="module")
def base():
    return SyntheticBuilder(n_stocks=30, years=10, seed=3).build()


@pytest.fixture(scope="module")
def monthly():
    return SyntheticBuilder(n_stocks=30, years=15, freq="ME", seed=4).build()


GENS = [BlockBootstrap(12), StationaryBootstrap(12), RegimeBootstrap(block_months=6), IIDBootstrap()]
IDS = ["block", "stationary", "regime", "iid"]


@pytest.mark.parametrize("g", GENS, ids=IDS)
def test_determinism_and_shapes(base, g):
    a = g.paths(base, 20, 5, seed=1)
    b = g.paths(base, 20, 5, seed=1)
    c = g.paths(base, 20, 5, seed=2)
    assert np.array_equal(a.source_idx, b.source_idx) and np.array_equal(a.returns, b.returns)
    assert not np.array_equal(a.source_idx, c.source_idx)
    assert a.returns.shape == (20, 5 * 252, 30) and a.alive.shape == a.returns.shape and a.alive.dtype == bool
    assert a.index_returns.shape == (20, 5 * 252, 1)
    assert a.source_dates.shape == (20, 5 * 252) and len(a.dates) == 5 * 252
    assert a.dates[0] == base.prices.index[0] and a.index_names == ("SYN_CAPWT",)
    assert (a.returns[:, 0] == 0).all()


def test_start_date_and_monthly_calendar(monthly):
    ps = BlockBootstrap(12).paths(monthly, 5, 10, seed=0, start="2030-01-31")
    assert ps.returns.shape[1] == 120 and ps.dates[0] == pd.Timestamp("2030-01-31")
    assert (ps.dates[1] - ps.dates[0]).days in (28, 29, 30, 31)


@pytest.mark.parametrize("g", GENS, ids=IDS)
def test_stocks_and_indices_share_source_dates(base, g):
    ps = g.paths(base, 8, 3, seed=5)
    px, ix = base.prices, base.indices["SYN_CAPWT"]
    p, t = 3, slice(1, None)
    s = ps.source_idx[p]
    ir = ix.pct_change().to_numpy()
    assert np.allclose(ps.index_returns[p, t, 0], ir[s[t]])
    assert np.array_equal(ps.source_dates[p], px.index.to_numpy()[s])
    r = px.pct_change().to_numpy()
    both = ps.alive[p, t] & ~np.isnan(r[s[t]])
    assert np.allclose(ps.returns[p, t][both], r[s[t]][both])


def test_block_structure(base):
    ps = BlockBootstrap(12).paths(base, 10, 6, seed=2)
    L = _Ctx(base).block_len(12)
    n_src = len(base.prices) - 1
    d = np.diff(ps.source_idx, axis=1)
    for p in range(10):
        for b in range(0, ps.source_idx.shape[1], L):
            blk = ps.source_idx[p, b:b + L]
            step = np.diff(blk)
            assert ((step == 1) | (step == 1 - n_src)).all()     # contiguous, wrapping at the end
    assert (d != 1).any()                                        # but blocks do jump


def test_stationary_block_lengths(base):
    ps = StationaryBootstrap(6).paths(base, 200, 10, seed=2)
    n_src = len(base.prices) - 1
    cont = ((np.diff(ps.source_idx, axis=1) - 1) % n_src) == 0
    L = _Ctx(base).block_len(6)
    mean_len = 1 / (1 - cont.mean())
    assert 0.8 * L < mean_len < 1.25 * L                      # geometric, mean ~ 6 months


def test_iid_has_no_persistence(base):
    ps = IIDBootstrap().paths(base, 50, 4, seed=1)
    n_src = len(base.prices) - 1
    cont = ((np.diff(ps.source_idx, axis=1) - 1) % n_src) == 0
    assert cont.mean() < 0.01


@pytest.mark.parametrize("g", GENS, ids=IDS)
def test_mean_return_close_to_base(base, g):
    ps = g.paths(base, 400, 10, seed=7)
    ctx = _Ctx(base)
    ix = base.indices["SYN_CAPWT"].pct_change().dropna()
    got = ps.index_returns[:, 1:, 0].mean()
    se = ix.std() / np.sqrt(ps.index_returns[:, 1:, 0].size / 20)    # generous: blocks are dependent
    assert abs(got - ix.mean()) < 5 * se
    w = ps.alive[:, 1:]
    bw = ctx.valid[1:]
    assert abs(ps.returns[:, 1:][w].mean() - ctx.ret[1:][bw].mean()) < 5e-4


def test_alive_mask_respected(base):
    ps = BlockBootstrap(12).paths(base, 10, 8, seed=3)
    valid = base.prices.notna().to_numpy()
    assert np.array_equal(ps.alive, valid[ps.source_idx])
    assert (ps.returns[~ps.alive] == 0).all()
    assert not ps.alive.all() and ps.alive.any()                    # synthetic market has delistings
    lv = ps.levels(0)
    assert np.isnan(lv[~ps.alive[0]]).all() and np.isfinite(lv[ps.alive[0]]).all()


def test_scenario_dataset_runs_market_and_screen(base):
    g = BlockBootstrap(12)
    ps = g.paths(base, 6, 4, seed=11, start="2010-01-04")
    ds = g.scenario_dataset(base, 3, pathset=ps)
    # path id stable w.r.t. n_paths: regenerating a smaller set gives the same scenario
    ds2 = g.scenario_dataset(base, 3, horizon_years=4, seed=11, start="2010-01-04")
    pd.testing.assert_frame_equal(ds.prices, ds2.prices)
    assert ds.prices.index.equals(ps.dates) and (ds.prices.iloc[0].dropna() == 1).all()
    assert np.allclose(ds.indices.iloc[:, 0].pct_change().to_numpy()[1:], ps.index_returns[3, 1:, 0])
    mk = Market(ds)
    asof = ds.prices.index[600]
    v = mk.view(asof)
    assert len(v) > 0 and "pe" in v and "mom_12_1" in v
    assert set(v.index) <= set(ds.prices.columns[ds.prices.loc[asof].notna()])
    picks = mk.picks(next(iter(REGISTRY.values())), asof)
    assert isinstance(picks, list)
    # fundamentals values are the base snapshot of the source date, re-dated to the synthetic date
    f = ds.fundamentals
    assert f["date"].between(ds.prices.index[0], ds.prices.index[-1]).all()
    t = int(np.flatnonzero(ps.source_dates[3] > np.datetime64("2003-06-01"))[0])
    src = pd.Timestamp(ps.source_dates[3][t])
    bf = base.fundamentals
    bs = bf[bf["date"] <= src].sort_values("date").groupby("ticker").tail(1).set_index("ticker")
    fs = f[f["date"] == ps.dates[t]].set_index("ticker")
    if len(fs):                                                   # t is a snapshot change point
        pd.testing.assert_series_equal(fs["eps"].sort_index(), bs.loc[fs.index, "eps"].sort_index(), check_names=False)


def test_scenario_dataset_monthly_and_bundled():
    ds = BundledSP500Builder().build()
    g = StationaryBootstrap(12)
    sc = g.scenario_dataset(ds, 1, n_paths=2, horizon_years=5, seed=0)
    assert sc.prices.shape == (5 * 252, 20) and list(sc.indices.columns) == ["SP500"]
    assert Market(sc).view(sc.prices.index[400]).shape[0] > 0


def test_datacard_carries_base_warnings():
    ds = BundledSP500Builder().build()
    g = BlockBootstrap(12)
    sc = g.scenario_dataset(ds, 2, horizon_years=3, seed=9)
    c, b = sc.card, ds.card
    assert isinstance(c, ScenarioCard)
    assert c.warnings() == b.warnings() and any("price index" in w for w in c.warnings())
    assert c.benchmark_kind == b.benchmark_kind == "price" and c.survivorship_score == b.survivorship_score
    assert c.source == f"{b.name} resampled" and c.seed == 9 and c.path_id == 2
    assert c.generator == {"__t": "BlockBootstrap"} or c.generator["__t"] == "BlockBootstrap"
    d = c.to_dict()
    assert d["warnings"] == b.warnings() and d["path_id"] == 2 and d["generator"]["__t"] == "BlockBootstrap"
    json.dumps(d, default=str)
    assert sc.with_index("EXTRA", sc.indices["SP500"]).card.path_id == 2    # survives card rebuilds


def test_regime_bootstrap_uses_regimes(base):
    g = RegimeBootstrap(block_months=6, p_stay=(0.95, 0.9), stress_quantile=0.7)
    ctx = _Ctx(base)
    st = g.regimes(ctx)
    assert 0.2 < st.mean() < 0.4
    ps = g.paths(base, 300, 10, seed=4)
    stressed_share = st[ps.source_idx].mean()
    # chain with stay (.95,.9): stationary stressed share = .05/(.05+.1) = 1/3 of blocks
    assert 0.2 < stressed_share < 0.5
    assert np.allclose(g.transition(st[1:], 126), [[0.95, 0.05], [0.1, 0.9]])
    fitted = RegimeBootstrap().transition(st[1:], 126)
    assert np.allclose(fitted.sum(axis=1), 1)


def test_regime_bootstrap_with_exit_screen(base):
    scr = ExitScreen([VolRegime(index="SYN_CAPWT", days=63)], threshold=0.7)
    g = RegimeBootstrap(scr, block_months=3)
    st = g.regimes(_Ctx(base))
    assert st.dtype == bool and 0 < st.mean() < 1
    ps = g.paths(base, 10, 3, seed=1)
    assert ps.returns.shape == (10, 756, 30)
    assert np.array_equal(ps.source_idx, g.paths(base, 10, 3, seed=1).source_idx)


@pytest.mark.parametrize("g", GENS + [RegimeBootstrap(ExitScreen([VolRegime(index="X", days=21)]), 3, (0.9, 0.8))],
                         ids=IDS + ["regime_screen"])
def test_serialization_round_trip(g):
    d = to_dict(g)
    assert d["__t"] == type(g).__name__
    j = json.loads(json.dumps(d))
    g2 = from_dict(j)
    assert type(g2) is type(g) and to_dict(g2) == d
    if not isinstance(g, RegimeBootstrap):
        assert g2 == g


def test_serialized_generator_reproduces_paths(base):
    g = from_dict(json.loads(json.dumps(to_dict(StationaryBootstrap(9)))))
    assert np.array_equal(g.paths(base, 5, 2, seed=3).source_idx,
                          StationaryBootstrap(9).paths(base, 5, 2, seed=3).source_idx)
    assert BlockBootstrap().block_months == 12 and to_dict(BlockBootstrap()) == {"__t": "BlockBootstrap"}


def test_speed_1000_paths_25_years_daily_20_names():
    import time
    ds = SyntheticBuilder(n_stocks=20, years=25, seed=1).build()
    t = time.perf_counter()
    ps = BlockBootstrap(12).paths(ds, 1000, 25, seed=0)
    dt = time.perf_counter() - t
    assert ps.returns.shape == (1000, 6300, 20) and isinstance(ps, PathSet)
    assert dt < 15
