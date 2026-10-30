"""Simulation pipeline (DESIGN.md J5): stage protocols, grouped Config, plug-in stages, `simulate_paths`.

    python -m pytest screenlab/tests/test_pipeline.py -q
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
import pytest

from screenlab import Config, ExitPolicy, ExitScreen, Market, TrendBreak, SyntheticBuilder
from screenlab.backtest import run, simulate
from screenlab.benchmarks import IndexBenchmark
from screenlab.construction import HoldRule, StandardWeighting
from screenlab.dataset import Dataset
from screenlab.exit_policy import PolicyOverlay
from screenlab.exit_signals import Breadth
from screenlab.montecarlo import BlockBootstrap, PathSet
from screenlab.pipeline import (Accrual, CashAccrual, CostModel, Overlay, PathWealth, ScreenSelection, Selection,
                                StandardCosts, Weighting, register_price_rank, simulate_paths)
from screenlab.realdata.bundled import price_screens
from screenlab import progress
from screenlab.screens import Screen
from screenlab.serial import from_dict, serializable, to_dict

TOL = 1e-10


# --------------------------------------------------------------------------- a small hand-built world
def roa_top(s, n=3):
    return s["roa"].dropna().nlargest(n).index.tolist()


def make_world(freq="ME", T=48, N=40, S=8, P=3, seed=1, consistent=False):
    """(base Dataset, PathSet) with hand-built paths: a name delists inside the base, names die and come
    back inside the paths, one name never trades. `consistent`: alive = the base's tradability on the source
    date, as the generators produce (needed by selection='fixed')."""
    rng = np.random.default_rng(seed)
    bd = pd.date_range("2010-01-31", periods=T, freq="ME") if freq == "ME" else pd.bdate_range("2010-01-04", periods=T)
    names = [f"S{i}" for i in range(S)]
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.01, 0.05 if freq == "ME" else 0.01, (T, S)), axis=0),
                      index=bd, columns=names)
    px.iloc[int(T * 0.65):, 2] = np.nan
    fund = pd.DataFrame({"date": bd[0], "ticker": names, "price": 100.0, "market_cap": 1e3, "sector": "X",
                         "roa": rng.normal(size=S)})
    idx = pd.DataFrame({"IDX": np.cumprod(1 + rng.normal(0.008, 0.04 if freq == "ME" else 0.008, T))}, index=bd)
    base = Dataset.from_frames(fund, px, idx)
    dates = (pd.date_range("2020-01-31", periods=N, freq="ME") if freq == "ME"
             else pd.bdate_range("2020-01-06", periods=N))
    sd = 0.06 if freq == "ME" else 0.012
    r = rng.normal(0.008, sd, (P, N, S))
    r[:, 0] = 0
    sidx = rng.integers(1, T, (P, N))
    if consistent:
        alive = (~np.isnan(px.to_numpy()))[sidx]
    else:
        r[0, :N * 5 // 8, 3] += 0.04                      # the names that vanish are strong, so momentum holds them
        r[1, :, 5] += 0.04
        r[2, :N * 3 // 4, 1] += 0.04
        alive = np.ones((P, N, S), bool)
        alive[0, N * 5 // 8:, 3] = False                  # dies while it may be held
        alive[1, N // 2:N // 2 + 4, 5] = False            # gone for a while, then back
        alive[2, N * 3 // 4:, 1] = False
        alive[1, :, 7] = False                            # never trades
    ir = rng.normal(0.007, 0.04 if freq == "ME" else 0.008, (P, N, 1))
    ir[:, 0] = 0
    k = N // 2                                            # a crash and a rebound so TrendBreak trips
    ir[:, k:k + 6] = -0.08 if freq == "ME" else -0.02
    ir[:, k + 6:k + 12] = 0.05 if freq == "ME" else 0.015
    ps = PathSet(np.where(alive, r, 0.0), ir, alive, sidx, bd.to_numpy()[sidx], dates, tuple(names), ("IDX",),
                 {"__t": "BlockBootstrap"}, 0, "hand")
    return base, ps


def scenario_curve(base, ps, p, screen, cfg, start=None):
    ds = BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)
    return run(screen, Market(ds), cfg, start=ps.dates[0] if start is None else start)


def assert_paths_match(out: PathWealth, base, ps, screen, cfg, start=None):
    """simulate_paths == `run` on every path's scenario dataset, to 1e-10, curve for curve."""
    start_t = ps.dates[0] if start is None else pd.Timestamp(start)
    for p in range(ps.n_paths):
        res = scenario_curve(base, ps, p, screen, cfg, start)
        c = res.curve
        got = out.portfolio[p][ps.dates.get_indexer(c.index)]
        assert np.isfinite(got).all()
        np.testing.assert_allclose(got, c.to_numpy(), rtol=TOL, atol=TOL)
        if out.exposure is not None:
            np.testing.assert_allclose(out.exposure[p][ps.dates.get_indexer(c.index)], res.exposure.to_numpy(), atol=1e-12)
        mkt = Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps))
        b = IndexBenchmark("IDX").curve(mkt, cfg, start_t)
        np.testing.assert_allclose(out.index[p][ps.dates.get_indexer(b.index)], b.to_numpy(), rtol=TOL, atol=TOL)


MOM = price_screens(3)["momentum_12_1"]
POL = ExitPolicy(ExitScreen([TrendBreak("IDX", window_months=4)], threshold=0.5, check="ME", lag=1),
                 reentry_steps=2, reentry_delay_days=10, switch_cost_bps=7)
FRICTION = dict(cost_bps=20, commission=5, capital=20_000, mer=0.002, cash_rate=0.02, delist_return=-0.4)


@pytest.fixture(scope="module")
def world():
    return make_world()


# --------------------------------------------------------------------------- Config: groups and aliases
def test_config_groups_and_flat_aliases():
    c = Config(rebalance_months=6, cost_bps=10, commission=2.0, mer=0.001, cash_rate=0.03, weighting="cap",
               max_weight=0.2, hold_rule=HoldRule(band=0.1), delist_return=-0.5, min_holdings=4)
    assert isinstance(c.costs, StandardCosts) and isinstance(c.construction, StandardWeighting)
    assert isinstance(c.cash, CashAccrual) and c.exit is None
    assert (c.costs.cost_bps, c.costs.commission, c.costs.mer) == (10, 2.0, 0.001)
    assert (c.construction.weighting, c.construction.max_weight) == ("cap", 0.2)
    assert (c.cash.cash_rate, c.cash.delist_return) == (0.03, -0.5)
    # attribute access reads through to the stages
    assert (c.cost_bps, c.commission, c.capital, c.mer, c.cash_rate, c.delist_return) == (10, 2.0, 100_000, 0.001, 0.03, -0.5)
    assert (c.weighting, c.max_weight, c.sector_cap, c.hold_rule.band) == ("cap", 0.2, None, 0.1)
    assert (c.rebalance_months, c.min_holdings) == (6, 4)
    # the defaults are today's defaults
    d = Config()
    assert d.flat() == dict(rebalance_months=12, cost_bps=25.0, commission=0.0, capital=100_000, mer=0.0,
                            delist_return=-0.30, min_holdings=3, cash_rate=0.0, weighting="equal", max_weight=None,
                            sector_cap=None, hold_rule=None, exit=None)


def test_config_replace_setattr_and_errors():
    c = Config(cost_bps=10)
    c2 = replace(c, cost_bps=40, cash_rate=0.01, exit=POL)
    assert (c2.cost_bps, c2.cash_rate, c2.exit) == (40, 0.01, POL) and c.cost_bps == 10 and c.exit is None
    assert replace(c2, exit=None).exit is None and replace(c2, exit=None).cost_bps == 40
    assert c == Config(cost_bps=10) and c != c2
    c.max_weight = 0.3
    assert c.construction.max_weight == 0.3
    with pytest.raises(TypeError, match="unexpected keyword argument 'nonsense'"):
        Config(nonsense=1)
    with pytest.raises(AttributeError):
        c.nonsense
    # a stage given whole plus flat overrides of its fields
    k = Config(costs=StandardCosts(cost_bps=5, commission=1.0), mer=0.01)
    assert (k.cost_bps, k.commission, k.mer) == (5, 1.0, 0.01)


def test_config_serialization_matches_the_flat_form():
    assert Config().to_dict() == {"__t": "Config"}
    c = Config(rebalance_months=6, cost_bps={"A": 50, "_default": 5}, mer=0.002, hold_rule=HoldRule(keep_within=2.0),
               exit=POL)
    d = c.to_dict()
    assert {"rebalance_months", "cost_bps", "mer", "hold_rule", "exit"} == set(d) - {"__t"}
    assert "costs" not in d and "construction" not in d
    back = Config.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d and back.cost_bps == {"A": 50, "_default": 5} and back.hold_rule.keep_within == 2.0
    assert from_dict(d).to_dict() == d
    # an int is not the float default: kept, exactly as before the refactor
    assert Config(cost_bps=25).to_dict() == {"__t": "Config", "cost_bps": 25}


def test_run_keys_unchanged_for_unchanged_inputs():
    from .test_operations import test_run_keys_identical_to_before_the_refactor as keys
    keys()


def test_stage_classes_serialize_and_satisfy_the_protocols():
    for st in (StandardCosts(cost_bps=3, mer=0.01), CashAccrual(0.02, -0.5),
               StandardWeighting("inv_vol", 0.1, None, HoldRule(band=0.1))):
        d = to_dict(st)
        assert from_dict(json.loads(json.dumps(d))) == st
    assert isinstance(ScreenSelection(), Selection) and isinstance(StandardCosts(), CostModel)
    assert isinstance(StandardWeighting(), Weighting) and isinstance(CashAccrual(), Accrual)
    assert isinstance(PolicyOverlay(POL), Overlay)


# --------------------------------------------------------------------------- plug-in stages
@serializable("FlatFeeCost")
@dataclass(frozen=True)
class FlatFeeCost:
    """Custom CostModel in user code: a fixed $ fee per rebalance (against `capital`), no MER."""
    fee: float = 10.0
    capital: float = 10_000.0

    def trade_cost(self, dw, snap, value):
        if isinstance(dw, pd.DataFrame):                      # many paths
            return np.full(len(dw), self.fee) / (self.capital * np.asarray(value))
        return self.fee / (self.capital * value)

    def holding_drag(self, tickers, snap):
        return np.zeros(len(tickers))


def flat_market(years=3):
    d = pd.date_range("2020-01-31", periods=12 * years + 1, freq="ME")
    names = list("ABCD")
    px = pd.DataFrame(1.0, index=d, columns=names)
    fund = pd.DataFrame({"date": d[0], "ticker": names, "market_cap": 1.0, "roa": [4.0, 3, 2, 1]})
    return Market(fund, px), Screen("roa3", roa_top, 3), d


def test_custom_cost_stage_through_config_exact():
    mkt, screen, d = flat_market()
    cfg = Config(rebalance_months=12, costs=FlatFeeCost(fee=10, capital=10_000))
    assert isinstance(cfg.costs, FlatFeeCost) and cfg.rebalance_months == 12
    res = run(screen, mkt, cfg, start=d[0])
    n_reb = len(res.holdings)
    assert n_reb == 3
    v = 1.0
    for _ in range(n_reb):                                    # 10 / 10_000 of the current value, every rebalance
        v *= 1 - 10 / (10_000 * v)
    assert res.curve.iloc[-1] == pytest.approx(v, abs=1e-12)
    assert res.costs.iloc[0] == pytest.approx(10 / 10_000)
    # the default model charges nothing on these flat prices once invested: the plug-in is what costs here
    plain = run(screen, mkt, Config(cost_bps=0), start=d[0])
    assert plain.curve.iloc[-1] == pytest.approx(1.0)
    # it survives replace(), serialization and a different run key
    assert replace(cfg, rebalance_months=6).costs == cfg.costs
    dd = cfg.to_dict()
    assert dd["costs"]["__t"] == "FlatFeeCost" and "cost_bps" not in dd
    assert Config.from_dict(json.loads(json.dumps(dd))) == cfg
    assert dd != Config(cost_bps=25).to_dict()
    with pytest.raises(TypeError, match="no field"):
        Config(costs=FlatFeeCost(), cost_bps=5)


def test_custom_cost_stage_in_simulate_paths_matches_scenario_runs(world):
    base, ps = world
    cfg = Config(rebalance_months=3, costs=FlatFeeCost(fee=25, capital=5_000), cash_rate=0.01)
    out = simulate_paths(MOM, cfg, ps, base=base)
    assert_paths_match(out, base, ps, MOM, cfg)


def test_custom_selection_and_accrual_stages():
    mkt, screen, d = flat_market()

    class FirstTwo:
        def picks(self, mkt, screen, d):
            return ["A", "B"]

    res = simulate(screen, mkt, Config(min_holdings=2, cost_bps=0), [d[0]], selection=FirstTwo())
    assert res.holdings[d[0]] == ["A", "B"]

    @dataclass(frozen=True)
    class Rich:
        cash_rate: float = 0.10
        delist_return: float = 0.0
        cash_factor = CashAccrual.cash_factor
        cash_returns = CashAccrual.cash_returns
        delist = CashAccrual.delist

    res = simulate(screen, mkt, Config(min_holdings=4, cost_bps=0, cash=Rich()), [d[0]])    # 3 picks < 4: cash
    yrs = (d[-1] - d[0]).days / 365.25
    assert res.curve.iloc[-1] == pytest.approx(1.1 ** yrs)


# --------------------------------------------------------------------------- simulate_paths == simulate
@pytest.mark.parametrize("rebalance_months", [1, 3, 12])
@pytest.mark.parametrize("exit_", [None, POL], ids=["no_exit", "trend_break"])
def test_momentum_per_path_equals_simulate_on_each_path(world, rebalance_months, exit_):
    base, ps = world
    cfg = Config(rebalance_months=rebalance_months, exit=exit_, **FRICTION)
    out = simulate_paths(MOM, cfg, ps, base=base)
    assert out.selection == "per_path" and out.portfolio.shape == (3, 40) == out.index.shape
    assert_paths_match(out, base, ps, MOM, cfg)
    if exit_ is not None:
        assert out.exposure.min() == 0.0 and out.exposure.max() == 1.0       # the policy really traded
        assert (out.exposure < 1).any(axis=1).all()


def test_the_hand_built_paths_exercise_delisting_costs_and_the_cash_fallback(world):
    base, ps = world
    a = simulate_paths(MOM, Config(rebalance_months=1, **FRICTION), ps, base=base)
    b = simulate_paths(MOM, Config(rebalance_months=1, **{**FRICTION, "delist_return": -0.9}), ps, base=base)
    c = simulate_paths(MOM, Config(rebalance_months=1, **{**FRICTION, "mer": 0.05}), ps, base=base)
    assert all((a.portfolio[p] != b.portfolio[p]).any() for p in range(3))   # a held name died on every path
    assert (a.portfolio[:, -1] > c.portfolio[:, -1]).all()
    assert np.allclose(a.portfolio[:, :12], a.portfolio[:, :1] * (1 + 0.02) ** (np.arange(12) / 12)[None, :], rtol=1e-2)
    assert (a.turnover[:, 0] == 0).all() and (a.turnover[:, 12:] > 0).any()   # cash first year: nothing to buy


@pytest.mark.parametrize("screen_name", ["momentum_6m", "momentum_12m", "low_vol"])
def test_other_price_ranks_per_path(world, screen_name):
    base, ps = world
    screen = price_screens(3)[screen_name]
    cfg = Config(rebalance_months=2, exit=POL, **FRICTION)
    assert_paths_match(simulate_paths(screen, cfg, ps, base=base), base, ps, screen, cfg)


def test_later_start_leaves_nan_before_it(world):
    base, ps = world
    start = ps.dates[14]
    cfg = Config(rebalance_months=2, **FRICTION)
    out = simulate_paths(MOM, cfg, ps, base=base, start=start, start_wealth=250.0)
    assert np.isnan(out.portfolio[:, :14]).all() and (out.portfolio[:, 14] == 250.0).all()
    assert (out.index[:, 14] == 250.0).all()
    res = scenario_curve(base, ps, 1, MOM, cfg, start)
    got = out.portfolio[1][ps.dates.get_indexer(res.curve.index)] / 250.0
    np.testing.assert_allclose(got, res.curve.to_numpy(), rtol=TOL)


def test_index_wealth_applies_mer_and_one_off_cost(world):
    base, ps = world
    out = simulate_paths(MOM, Config(rebalance_months=12), ps, base=base, index_mer=0.01, index_cost_bps=50)
    mkt = Market(BlockBootstrap(12).scenario_dataset(base, 2, pathset=ps))
    b = IndexBenchmark("IDX", mer=0.01, cost_bps=50).curve(mkt, Config(), ps.dates[0])
    np.testing.assert_allclose(out.index[2], b.to_numpy(), rtol=TOL)
    lv = np.cumprod(1 + ps.index_returns[2, :, 0])
    assert out.index[2, 0] == 1.0 and out.index[2, -1] < lv[-1]


def test_raw_arrays_equal_pathset(world):
    base, ps = world
    cfg = Config(rebalance_months=3, exit=POL, **FRICTION)
    a = simulate_paths(MOM, cfg, ps, base=base)
    b = simulate_paths(MOM, cfg, returns=ps.returns, alive=ps.alive, index_returns=ps.index_returns, dates=ps.dates,
                       names=ps.names, index_names=ps.index_names)
    np.testing.assert_allclose(a.portfolio, b.portfolio, rtol=TOL)
    np.testing.assert_allclose(a.index, b.index, rtol=TOL)
    with pytest.raises(ValueError):
        simulate_paths(MOM, cfg)


def test_fixed_selection_equals_simulate_when_paths_are_consistent():
    base, ps = make_world(consistent=True)
    for screen in (Screen("roa3", roa_top, 3), Screen("roa_w", roa_top, 4)):
        for exit_ in (None, POL):
            cfg = Config(rebalance_months=2, exit=exit_, weighting="equal", max_weight=0.3, **FRICTION)
            out = simulate_paths(screen, cfg, ps, base=base)
            assert out.selection == "fixed"
            assert_paths_match(out, base, ps, screen, cfg)
    with pytest.raises(ValueError, match="base"):
        simulate_paths(screen, Config(), ps)


@pytest.mark.parametrize("hr", [HoldRule(keep_within=2.0), HoldRule(band=0.25), HoldRule(keep_within=2.0, band=0.25)],
                         ids=["keep", "band", "keep+band"])
def test_fixed_selection_with_a_hold_rule_equals_simulate_on_each_scenario(hr):
    """The hold rule (keep_within / band) runs per path on the drifted weights, exactly as `simulate` does."""
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 3, 4, seed=2)
    screen = Screen("roa5", roa_top, 5)
    for m in (3, 12):
        cfg = Config(rebalance_months=m, hold_rule=hr, **FRICTION)
        out = simulate_paths(screen, cfg, ps, base=base, selection="fixed")
        assert out.selection == "fixed" and out.hold_rule_applied is True
        for p in range(3):
            res = run(screen, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=ps.dates[0])
            np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(),
                                       rtol=TOL)
    plain = simulate_paths(screen, Config(rebalance_months=3, **FRICTION), ps, base=base, selection="fixed")
    assert plain.hold_rule_applied is False
    held = simulate_paths(screen, Config(rebalance_months=3, hold_rule=hr, **FRICTION), ps, base=base, selection="fixed")
    assert not np.allclose(plain.portfolio, held.portfolio, equal_nan=True)         # the rule really trades differently


@pytest.mark.parametrize("hr", [HoldRule(keep_within=2.0), HoldRule(band=0.25), HoldRule(keep_within=2.0, band=0.25)],
                         ids=["keep", "band", "keep+band"])
@pytest.mark.parametrize("which", ["momentum_12_1", "spec_mom_6m"])
def test_per_path_selection_with_a_hold_rule_equals_simulate_on_each_scenario(hr, which):
    """Per path: rank on the path's own prices, retain (keep_within * n of that ranking), band on its drifted
    weights, equal weight: what `simulate` does on the scenario dataset."""
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 4, 5, seed=2)
    screen = price_screens(6)["momentum_12_1"] if which == "momentum_12_1" else _spec_rank(1000, "mom_6m", n=6)
    traded = False
    for m in (3, 12):
        cfg = Config(rebalance_months=m, hold_rule=hr, **FRICTION)
        out = simulate_paths(screen, cfg, ps, base=base)                       # auto -> per_path
        assert out.selection == "per_path" and out.hold_rule_applied is True
        for p in range(4):
            res = run(screen, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=ps.dates[0])
            np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(),
                                       rtol=TOL)
        plain = simulate_paths(screen, Config(rebalance_months=m, **FRICTION), ps, base=base)
        assert plain.hold_rule_applied is False
        traded |= not np.allclose(plain.portfolio, out.portfolio, equal_nan=True)
    assert traded                                                              # the rule really trades differently


def test_one_year_horizon_with_trendbreak_exit_equals_simulate():
    """A path shorter than the exit signal's history (all-NaN TrendBreak) used to give a [0]-wide exposure."""
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 3, 2, seed=2)                     # 1 warm-up year + 1 year
    pol = ExitPolicy(ExitScreen([TrendBreak("SYN_CAPWT")]))
    screen = Screen("roa5", roa_top, 5)
    cfg = Config(rebalance_months=12, exit=pol, **FRICTION)
    start = ps.dates[0] + pd.DateOffset(months=12)
    out = simulate_paths(screen, cfg, ps, base=base, selection="fixed", start=start)
    assert out.exposure.shape == (3, len(ps.dates)) and (out.exposure == 1.0).all()
    for p in range(3):
        res = run(screen, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=start)
        np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(),
                                   rtol=TOL)


def _spec_rank(top, rank_col, direction="desc", filters=(), n=5):
    from screenlab.spec import ScreenSpec
    return ScreenSpec("spec_rank", n=n, universe={"top": top} if top else {}, filters=list(filters),
                      rank={rank_col: direction}).build()


@pytest.mark.parametrize("col,direction", [("mom_6m", "desc"), ("mom_12m", "desc"), ("vol_260d", "asc"), ("vol_1y", "asc"),
                                           ("mom_12_1", "desc")])
def test_spec_price_rank_per_path_equals_simulate_on_each_scenario(col, direction):
    """A size cut (here wide enough to admit everyone) + static filter + one trailing rank, per path == `run`."""
    from screenlab.pipeline import price_rank_of
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 3, 5, seed=2)
    screen = _spec_rank(1000, col, direction, filters=["dividend > 0"])
    pr = price_rank_of(screen)
    assert (pr.column, pr.direction, pr.top, pr.n) == (col, direction, 1000, 5)
    cfg = Config(rebalance_months=3, **FRICTION)
    out = simulate_paths(screen, cfg, ps, base=base)
    assert out.selection == "per_path" and out.hold_rule_applied is False
    for p in range(3):
        res = run(screen, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=ps.dates[0])
        np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(), rtol=TOL)


def test_spec_price_rank_size_cut_restricts_the_ranked_names():
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 4, 5, seed=2)
    cfg = Config(rebalance_months=12, cost_bps=0)
    wide = simulate_paths(_spec_rank(1000, "mom_6m"), cfg, ps, base=base)
    cut = simulate_paths(_spec_rank(8, "mom_6m"), cfg, ps, base=base)
    assert cut.selection == "per_path" and np.isfinite(cut.portfolio[:, -1]).all()
    assert not np.allclose(wide.portfolio, cut.portfolio, equal_nan=True)


def test_price_rank_of_rejects_what_is_not_one():
    from screenlab.pipeline import price_rank_of
    from screenlab.spec import ScreenSpec, Stage
    assert price_rank_of(_spec_rank(300, "mom_12m")).top == 300 and price_rank_of(_spec_rank(None, "vol_1y", "asc")).top is None
    assert price_rank_of(_spec_rank(300, "mom_12m", filters=["price > 3"])) is None            # a price filter
    assert price_rank_of(_spec_rank(300, "pe", "asc")) is None                                  # not a trailing column
    assert price_rank_of(ScreenSpec("two", rank={"mom_6m": "desc", "mom_12m": "desc"}).build()) is None
    two_stage = ScreenSpec("vp", n=5, stages=[Stage(universe={"top": 50}, rank={"vol_260d": "asc"}, keep=20),
                                              Stage(rank={"pe": "asc"})]).build()
    assert price_rank_of(two_stage) is None
    assert price_rank_of(ScreenSpec("vp1", n=5, stages=[Stage(universe={"top": 50}, filters=["dividend > 0"]),
                                                         Stage(rank={"vol_260d": "asc"})]).build()).column == "vol_260d"
    assert price_rank_of(Screen("roa5", roa_top, 5)) is None
    # per_path refuses a screen that is not one, loudly
    base, ps = make_world(consistent=True)
    with pytest.raises(NotImplementedError, match="price rank"):
        simulate_paths(two_stage, Config(), ps, base=base, selection="per_path")


def test_daily_paths_with_weekly_exit_checks():
    base, ps = make_world(freq="B", T=420, N=340, S=8, P=3, seed=5)
    pol = ExitPolicy(ExitScreen([TrendBreak("IDX", window_months=2)], threshold=0.5, check="W-FRI", lag=1),
                     reentry="signal_or_rebalance", exposure_when_flagged=0.5, reentry_steps=3)
    for ex in (None, pol):
        cfg = Config(rebalance_months=1, exit=ex, **FRICTION)
        out = simulate_paths(MOM, cfg, ps, base=base)
        assert_paths_match(out, base, ps, MOM, cfg)
    assert 0.5 in out.exposure and (out.exposure < 1).any()


@pytest.mark.parametrize("kw", [
    dict(),
    dict(reentry="next_rebalance"),
    dict(reentry="signal_or_rebalance", reentry_delay_days=40),
    dict(exposure_when_flagged=0.4, reentry_steps=3, reentry_delay_days=5),
    dict(screen=dict(lag=0, check="D")),
    dict(screen=dict(lag=3, check="QE", threshold=0.4, hysteresis=0.3)),
    dict(screen=dict(min_hold_days=70, check="ME")),
])
def test_vectorized_exposure_equals_the_scalar_state_machine(world, kw):
    base, ps = world
    scr_kw = dict(threshold=0.5, check="ME", lag=1)
    scr_kw.update(kw.pop("screen", {}))
    pol = ExitPolicy(ExitScreen([TrendBreak("IDX", window_months=4), TrendBreak("IDX", window_months=7)], **scr_kw), **kw)
    cfg = Config(rebalance_months=3, exit=pol)
    out = simulate_paths(MOM, cfg, ps, base=base)
    from screenlab.backtest import schedule
    for p in range(ps.n_paths):
        mkt = Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps))
        want = pol.exposure(mkt, out.rebalance_dates)
        np.testing.assert_array_equal(out.exposure[p], want.to_numpy())


def test_min_votes_exposure_equals_scalar(world):
    base, ps = world
    es = ExitScreen([TrendBreak("IDX", window_months=3), TrendBreak("IDX", window_months=9)], threshold=0.5, min_votes=2,
                    check="ME")
    pol = ExitPolicy(es)
    out = simulate_paths(MOM, Config(rebalance_months=4, exit=pol), ps, base=base)
    for p in range(ps.n_paths):
        mkt = Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps))
        np.testing.assert_array_equal(out.exposure[p], pol.exposure(mkt, out.rebalance_dates).to_numpy())


def test_real_generator_paths_per_path_and_fixed():
    """BlockBootstrap paths of a synthetic monthly base, fundamentals travelling with the dates."""
    base = SyntheticBuilder(n_stocks=40, years=12, freq="ME", seed=4).build()
    ps = BlockBootstrap(12).paths(base, 3, 4, seed=2)
    pol = ExitPolicy(ExitScreen([TrendBreak("SYN_CAPWT", window_months=4)], threshold=0.5, check="ME"))
    for ex in (None, pol):
        cfg = Config(rebalance_months=1, exit=ex, **FRICTION)
        out = simulate_paths(MOM, cfg, ps, base=base)
        for p in range(3):
            res = run(MOM, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=ps.dates[0])
            np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(),
                                       rtol=TOL)
        # a fundamentals screen (roa is not marked to market): fixed mode == the scenario run
        screen = Screen("roa5", roa_top, 5)
        out = simulate_paths(screen, cfg, ps, base=base, selection="fixed")
        for p in range(3):
            res = run(screen, Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps)), cfg, start=ps.dates[0])
            np.testing.assert_allclose(out.portfolio[p][ps.dates.get_indexer(res.curve.index)], res.curve.to_numpy(),
                                       rtol=TOL)


# --------------------------------------------------------------------------- limits are loud
def test_unsupported_inputs_raise_not_implemented(world):
    base, ps = world
    breadth = ExitPolicy(ExitScreen([Breadth()]))
    with pytest.raises(NotImplementedError, match="Breadth"):
        simulate_paths(MOM, Config(exit=breadth), ps, base=base)
    resc = ExitPolicy(ExitScreen([TrendBreak("IDX")]), rescreen_on_reentry=True)
    with pytest.raises(NotImplementedError, match="rescreen_on_reentry"):
        simulate_paths(MOM, Config(exit=resc), ps, base=base)
    with pytest.raises(NotImplementedError, match="not among"):
        simulate_paths(MOM, Config(exit=ExitPolicy(ExitScreen([TrendBreak("SP500")]))), ps, base=base)
    with pytest.raises(NotImplementedError, match="equal weight"):
        simulate_paths(MOM, Config(weighting="inv_vol"), ps, base=base, selection="per_path")
    with pytest.raises(NotImplementedError, match="equal weight"):
        simulate_paths(MOM, Config(max_weight=0.5), ps, base=base, selection="per_path")
    # auto falls back to fixed when the weighting cannot run per path (caps, non-equal weight); a hold rule runs per path
    from screenlab.pipeline import auto_selection
    assert auto_selection(MOM, Config(max_weight=0.5)) == "fixed" and auto_selection(MOM, Config(sector_cap=0.4)) == "fixed"
    assert auto_selection(MOM, Config(weighting="inv_vol")) == "fixed"
    assert auto_selection(MOM, Config(hold_rule=HoldRule(band=0.1))) == "per_path" and auto_selection(MOM, Config()) == "per_path"
    with pytest.raises(NotImplementedError, match="callable"):
        simulate_paths(MOM, Config(cost_bps=lambda s: s["market_cap"] * 0), ps, base=base)
    with pytest.raises(NotImplementedError, match="registered price rank"):
        simulate_paths(Screen("roa3", roa_top, 3), Config(), ps, base=base, selection="per_path")
    with pytest.raises(ValueError, match="selection"):
        simulate_paths(MOM, Config(), ps, base=base, selection="nope")


def test_a_custom_signal_with_compute_paths_and_a_custom_price_rank(world):
    base, ps = world

    class Always:
        name = "Always"
        needs = ("index",)

        def compute(self, mkt, macro=None):
            return pd.Series(1.0, index=mkt.index)

        def compute_paths(self, dates, levels_of):
            return np.ones((len(dates), levels_of("IDX").shape[0]))

    pol = ExitPolicy(ExitScreen([Always()], threshold=0.5, check="D", lag=0))
    out = simulate_paths(MOM, Config(rebalance_months=3, exit=pol), ps, base=base)
    assert (out.exposure[:, 1:] == 0).all()                                  # flagged from the first bar
    for p in range(ps.n_paths):
        mkt = Market(BlockBootstrap(12).scenario_dataset(base, p, pathset=ps))
        np.testing.assert_array_equal(out.exposure[p], pol.exposure(mkt, out.rebalance_dates).to_numpy())

    def best_roa_vol(s, n=3):                                          # same ranking as low_vol under another name
        return s["vol_1y"].dropna().nsmallest(n).index.tolist()
    register_price_rank(best_roa_vol, "vol_1y", largest=False)
    custom = Screen("quiet", best_roa_vol, 3)
    cfg = Config(rebalance_months=3, **FRICTION)
    assert_paths_match(simulate_paths(custom, cfg, ps, base=base), base, ps, custom, cfg)


# --------------------------------------------------------------------------- progress and cancellation survive
def test_progress_and_cancel_inside_the_rebalance_loops(world):
    base, ps = world
    seen = []
    cfg = Config(rebalance_months=3)
    with progress.job_context(lambda f, m: seen.append(f)):
        res = run(MOM, Market(BlockBootstrap(12).scenario_dataset(base, 0, pathset=ps)), cfg, start=ps.dates[0])
    assert len(seen) == len(res.holdings) and seen == sorted(seen)
    seen.clear()
    with progress.job_context(lambda f, m: seen.append(f)):
        out = simulate_paths(MOM, cfg, ps, base=base)
    assert len(seen) == len(out.rebalance_dates)
    ev = threading.Event()
    ev.set()
    with progress.job_context(None, ev):
        with pytest.raises(progress.Cancelled):
            simulate_paths(MOM, cfg, ps, base=base)
        with pytest.raises(progress.Cancelled):
            run(MOM, Market(BlockBootstrap(12).scenario_dataset(base, 0, pathset=ps)), cfg, start=ps.dates[0])


def test_many_paths_run_vectorized(world):
    """Path count scales the arrays, not a Python loop: 600 paths of 15 years in a couple of seconds."""
    base = SyntheticBuilder(n_stocks=60, years=15, freq="ME", seed=6).build()
    ps = BlockBootstrap(12).paths(base, 600, 15, seed=1)
    out = simulate_paths(MOM, Config(rebalance_months=1), ps, base=base)
    assert out.portfolio.shape == (600, 180) and np.isfinite(out.portfolio).all()
    assert np.isfinite(out.index).all() and (out.turnover.sum(axis=1) > 0).all()


# --------------------------------------------------------------------------- cap_tiered costs in simulate_paths
def test_cap_tiered_cost_in_simulate_paths_equals_simulate_on_each_scenario():
    from screenlab.backtest import cap_tiered
    base, ps = make_world(consistent=True)
    caps = [50, 300, 3_000, 30_000, 60, 2_500, 400, 20_000]            # a factor ~2.5 clear of the 1000 / 10000 edges (scenario caps drift with the path)
    base.fundamentals["market_cap"] = caps * (len(base.fundamentals) // len(caps))
    tiers = cap_tiered({0: 100, 1_000: 50, 10_000: 25})
    for kw in (dict(rebalance_months=3), dict(rebalance_months=12)):
        cfg = Config(cost_bps=tiers, **kw)
        for screen, sel in ((Screen("roa3", roa_top, 3), "fixed"), (MOM, "per_path")):
            out = simulate_paths(screen, cfg, ps, base=base, selection=sel)
            assert_paths_match(out, base, ps, screen, cfg)
        flat = simulate_paths(MOM, Config(cost_bps=25, **kw), ps, base=base)
        assert not np.allclose(out.portfolio, flat.portfolio)           # the tiers really price names differently


def test_cap_tiered_bps_vectorized_matches_the_snapshot_call():
    from screenlab.backtest import cap_tiered
    t = cap_tiered({0: 100, 1_000: 50, 10_000: 25})
    cap = np.array([np.nan, 0, 999.9, 1_000, 9_999, 10_000, 1e6])
    snap = pd.DataFrame({"market_cap": cap}, index=list("abcdefg"))
    np.testing.assert_array_equal(t(snap).to_numpy(), t.bps_for_caps(cap))
    assert t.bps_for_caps(cap).tolist() == [100, 100, 100, 50, 50, 25, 25]


def test_cap_tiered_needs_base_and_other_callables_still_raise():
    from screenlab.backtest import cap_tiered
    base, ps = make_world()
    with pytest.raises(ValueError, match="cap_tiered"):
        simulate_paths(MOM, Config(cost_bps=cap_tiered({0: 10})), ps)
    with pytest.raises(NotImplementedError, match="arbitrary"):
        simulate_paths(MOM, Config(cost_bps=lambda s: s["market_cap"] * 0), ps, base=base)
