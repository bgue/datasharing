"""RunSpec serialization, canonical JSON and run keys (DESIGN.md I1, I4)."""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from screenlab import (equal_weight_top, Screen, Breadth, Config, ExitPolicy, ExitScreen, GrahamTests, HoldRule, IndexBenchmark,
                              Lab, MacroSeries, REGISTRY, ScreenBuilder, ScreenSpec, TrendBreak, backtest,
                              cap_tiered, equal_weight_top)
from screenlab import exit_signals, runspec as R
from screenlab.exit_policy import Portfolio
from screenlab.sweep import Grid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def lab():
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def rt(x):
    """Through real JSON text and back."""
    return R.from_dict(json.loads(json.dumps(R.to_dict(x))))


def curve(lab, cfg, screen="dogs", months=None):
    sc = lab.screen(screen)
    return backtest.run(sc, lab.market, cfg).curve


def policy():
    return ExitPolicy(ExitScreen([TrendBreak("SYN_CAPWT"), Breadth()], check="W-FRI", threshold=0.6),
                      exposure_when_flagged=0.25, park=0.02, park_mer=0.001, reentry="signal_or_rebalance",
                      reentry_delay_days=10, reentry_steps=2, rescreen_on_reentry=True)


# ---------------------------------------------------------------- round trips reproduce curves
@pytest.mark.parametrize("cfg", [
    Config(rebalance_months=6, cost_bps=40.0, commission=5.0),
    Config(cost_bps=cap_tiered({0: 75, 1_000: 30, 10_000: 10}), mer=cap_tiered({0: 0.005, 5_000: 0.001})),
    Config(cost_bps={"_default": 20.0, "T0001": 100.0}, mer=pd.Series({"T0001": 0.01, "_default": 0.002})),
    Config(weighting="inv_vol", max_weight=0.2, sector_cap=0.5, hold_rule=HoldRule(keep_within=2.0, band=0.2)),
    Config(rebalance_months=6, exit=policy(), cash_rate=0.01),
], ids=["flat", "cap_tiered", "per_security", "construction", "exit_policy"])
def test_config_roundtrip_reproduces_curve(lab, cfg):
    cfg2 = rt(cfg)
    assert isinstance(cfg2, Config) and cfg2.rebalance_months == cfg.rebalance_months
    a, b = curve(lab, cfg), curve(lab, cfg2)
    pd.testing.assert_series_equal(a, b, check_exact=True)


def test_defaults_omitted_and_tiers_stored_as_data():
    d = R.to_dict(Config(cost_bps=cap_tiered({0: 75, 1000: 30})))
    assert set(d) == {"__t", "cost_bps"}
    assert d["cost_bps"] == {"__t": "cap_tiered", "tiers": [[0.0, 75.0], [1000.0, 30.0]]}
    assert R.to_dict(Config()) == {"__t": "Config"}


def test_cap_tiered_survives_with_its_behaviour(lab):
    snap = lab.snapshot()
    f = cap_tiered({0: 75, 1_000: 30})
    f2 = rt(Config(cost_bps=f)).cost_bps
    pd.testing.assert_series_equal(f(snap), f2(snap))


@pytest.mark.parametrize("name", sorted(exit_signals.BUILTINS))
def test_every_builtin_signal_roundtrips(name):
    cls = exit_signals.BUILTINS[name]
    sig = cls()
    assert rt(sig) == sig
    d = R.to_dict(sig)
    assert d == {"__t": "Signal", "type": name, "params": {}}


def test_signal_with_params_and_exit_screen():
    es = ExitScreen([TrendBreak("X", window_months=6), Breadth(window_days=100)], weights={"Breadth": 2.0},
                    threshold=0.65, min_votes=1, hysteresis=0.2, check="ME", lag=2, min_hold_days=20)
    assert rt(es) == es


def test_exit_policy_roundtrip_equal():
    p = policy()
    p2 = rt(p)
    assert p2.screen == p.screen
    for f in ("exposure_when_flagged", "park", "park_mer", "reentry", "reentry_delay_days", "reentry_steps",
              "rescreen_on_reentry"):
        assert getattr(p2, f) == getattr(p, f)


def test_portfolio_and_benchmark_roundtrip():
    pf = Portfolio("dogs_x", "dogs", exit=policy(), rebalance_months=6, cost_bps=10)
    pf2 = rt(pf)
    assert (pf2.name, pf2.screen, pf2.config_overrides) == ("dogs_x", "dogs", {"rebalance_months": 6, "cost_bps": 10})
    assert pf2.exit.screen == pf.exit.screen
    b = IndexBenchmark("SYN_CAPWT", mer=0.0006, name="idx")
    assert rt(b) == b
    assert R.to_dict(equal_weight_top(60)) == {"__t": "ScreenBenchmark", "top": 60}
    assert rt(equal_weight_top(40)).screen.n == 40
    assert rt(Grid({"dogs.n": [5, 10]})).spec == {"dogs.n": [5, 10]}


def test_screens_builtin_overrides_and_specs(lab):
    s = REGISTRY["graham_defensive"].with_overrides(n=5, max_pe=12, min_passes=5)
    d = R.to_dict(s)
    assert d == {"__t": "Screen", "name": "graham_defensive", "overrides": {"n": 5, "max_pe": 12, "min_passes": 5}}
    s2 = rt(s)
    assert s2.n == 5 and s2.params["tests"].max_pe == 12
    assert R.to_dict(REGISTRY["dogs"]) == {"__t": "Screen", "name": "dogs"}
    assert lab.market.picks(s2, lab.market.index[-1]) == lab.market.picks(s, lab.market.index[-1])

    b = (ScreenBuilder("cheap").universe(top=60).where("pe < 15").graham("defensive", max_pe=20)
         .rank(div_yield="desc").take(7).build())
    d = R.to_dict(b)
    assert d["spec"]["name"] == "cheap" and d["spec"]["n"] == 7 and "fn" not in json.dumps(d)
    b2 = rt(b)
    assert lab.market.picks(b2, lab.market.index[-1]) == lab.market.picks(b, lab.market.index[-1])
    assert ScreenSpec(**{k: v for k, v in rt(ScreenSpec("z", 3, filters=["pe<9"])).__dict__.items()}).n == 3


def test_scalars_dates_tuples_and_series():
    x = {"start": pd.Timestamp("2005-03-31"), "h": (1, 3, 12), "k": {0: 75, 1000: 30}, "f": float("nan"),
         "np": np.float64(2.5), "s": pd.Series([1.0, np.nan, 3.0], index=pd.date_range("2000-01-31", periods=3, freq="ME"), name="c")}
    y = rt(x)
    assert y["start"] == x["start"] and y["h"] == (1, 3, 12) and y["k"] == {0: 75, 1000: 30}
    assert np.isnan(y["f"]) and y["np"] == 2.5
    pd.testing.assert_series_equal(y["s"], x["s"], check_freq=False)
    assert R.to_dict(R.to_dict(x)) == R.to_dict(x)                     # idempotent


def test_macro_by_reference():
    m = MacroSeries(pd.DataFrame({"cpi": [100.0, 101.0]}, index=pd.to_datetime(["2000-01-31", "2000-02-29"])))
    assert not R.RunSpec("extended", "d", {"macro": m}).persistable
    R.macro_ref(m, "test_macro")
    d = R.to_dict(m)
    assert d["ref"] == "test_macro" and "cpi" not in json.dumps(d)           # values are not stored
    assert rt(m) is m                                                      # resolved from the registry
    m2 = MacroSeries(m.frame * 2)
    R.macro_ref(m2, "test_macro")
    assert R.to_dict(m2)["hash"] != d["hash"]                              # changed data changes the key


# ---------------------------------------------------------------- persistable
def test_callables_mark_spec_unpersistable():
    ok = R.RunSpec("backtest", "d", {"screens": ["dogs"], "cost_bps": 10})
    assert ok.persistable and ok.reason is None and ok.run_key("ds") is not None
    for bad in ({"weighting": lambda s: s}, {"cost_bps": lambda snap: snap["market_cap"] * 0},
                {"screens": [Screen("mine", lambda s, n: [], 3)]}):
        sp = R.RunSpec("backtest", "d", {"screens": ["dogs"], **bad} if "screens" not in bad else bad)
        assert not sp.persistable, bad
        assert sp.reason and sp.run_key("ds") is None
        assert sp.to_dict()["persistable"] is False
        with pytest.raises(R.Unpersistable):
            sp.build_args()
    d = R.to_dict(Config(weighting=lambda s: s))
    assert R.unpersistable_reasons(d) and "lambda" in R.unpersistable_reasons(d)[0]


def test_spec_json_roundtrip_and_build(lab):
    sp = R.RunSpec("backtest", "synthetic", {"screens": ["dogs", Portfolio("h", "dogs", exit=policy())],
                                             "rebalance_months": 6, "start": pd.Timestamp("2002-01-31")},
                   seed=3, label="x", tags=["a", "b"])
    sp2 = R.RunSpec.from_json(sp.to_json())
    assert sp2.kind == "backtest" and sp2.tags == ("a", "b") and sp2.label == "x" and sp2.seed == 3
    assert sp2.run_key("ds") == sp.run_key("ds")
    kw = sp2.build_args()
    assert isinstance(kw["screens"][1], Portfolio) and kw["start"] == pd.Timestamp("2002-01-31")


# ---------------------------------------------------------------- keys
def test_run_key_stable_under_ordering_and_numeric_spelling():
    a = R.RunSpec("sweep", "d", {"screen": "dogs", "grid": {"dogs.n": [5, 10], "cost_bps": [10, 50]},
                                 "horizons": (1, 3)})
    b = R.RunSpec("sweep", "d", {"horizons": [1, 3], "grid": {"cost_bps": [10.0, 50], "dogs.n": [5, 10]},
                                 "screen": "dogs"}, label="other label", tags=["t"])
    assert a.run_key("ds", "v1") == b.run_key("ds", "v1")
    assert a.canonical() == b.canonical()
    base = a.run_key("ds", "v1")
    assert R.RunSpec("sweep", "d", a.args, seed=1).run_key("ds", "v1") != base
    assert R.RunSpec("timing", "d", a.args).run_key("ds", "v1") != base
    assert a.run_key("other", "v1") != base and a.run_key("ds", "v2") != base
    assert R.RunSpec("sweep", "d", {**a.args, "screen": "graham_defensive"}).run_key("ds", "v1") != base


def test_run_key_identical_across_processes():
    code = (
        "import json, sys\n"
        "from screenlab import Lab, ExitPolicy, ExitScreen, TrendBreak, Config, cap_tiered\n"
        "from screenlab import runspec as R\n"
        "from screenlab.exit_policy import Portfolio\n"
        "lab = Lab.synthetic(freq='ME', n_stocks=60, years=6, seed=3)\n"
        "sp = R.RunSpec('backtest', 'syn', {'screens': ['dogs', Portfolio('h', 'dogs', exit=ExitPolicy(ExitScreen([TrendBreak('SYN_CAPWT')])))],\n"
        "     'cost_bps': cap_tiered({0: 75, 1000: 30}), 'horizons': {1, 3, 12}, 'k': {1: 'a', 2: 'b'}})\n"
        "print(json.dumps([sp.run_key(R.dataset_id(lab.market)), R.dataset_id(lab.market)]))\n")
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
    outs = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**env, "PYTHONHASHSEED": str(s)},
                           check=True).stdout for s in (1, 2)]
    assert outs[0] == outs[1] and len(json.loads(outs[0])[0]) == 64
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)
    assert json.loads(outs[0])[1] == R.dataset_id(lab.market)               # also equals this process's id


def test_dataset_id_cached_and_sensitive(lab):
    from screenlab.data import Market
    m = lab.market
    d1 = R.dataset_id(m)
    assert d1 == m._screenlab_dataset_id and R.dataset_id(m) is m._screenlab_dataset_id
    prices = m.prices.copy()
    prices.iloc[5, 3] *= 1.0001
    m2 = Market(m.fund.copy(), prices, m.indices.copy())
    assert R.dataset_id(m2) != d1
    m3 = Market(m.fund.copy(), m.prices.copy(), m.indices.copy())
    assert R.dataset_id(m3) == d1
    fund = m.fund.copy()
    fund.loc[fund.index[10], "sales"] += 1
    assert R.dataset_id(Market(fund, m.prices.copy(), m.indices.copy())) != d1


def test_code_version(monkeypatch):
    R.code_version.cache_clear()
    v = R.code_version()
    assert v.startswith("0.1.0") or v[0].isdigit()
    monkeypatch.setenv("SCREENLAB_CODE_VERSION", "test-9")
    R.code_version.cache_clear()
    assert R.code_version() == "test-9"
    R.code_version.cache_clear()


def test_family_and_screen_names():
    sp = R.RunSpec("backtest", "sp500", {"screens": ["dogs", Portfolio("h", "magic_formula", exit=policy())]})
    assert sp.screen_names() == ["dogs", "magic_formula"]
    assert sp.family() == "backtest|sp500|dogs+magic_formula"
    assert R.RunSpec("timing", "sp500", {"screen": "dogs"}).family() == "timing|sp500|dogs"
    ex = R.RunSpec("exit_calibrate", "sp500", {"exit_screen": ExitScreen([TrendBreak(), Breadth()]), "grid": {}})
    assert ex.family() == "exit_calibrate|sp500|exit:Breadth+TrendBreak"


# ---------------------------------------------------------------- lab context in the key
def key_of(lab_, **args):
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs"], **args})
    return R.run_key_for(spec, R.dataset_id(lab_.market), R.lab_context(lab_))


def mk(**kw):
    return Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3, **kw)


def test_lab_config_benchmark_and_screens_change_the_key():
    a, b = mk(), mk()
    assert key_of(a) == key_of(b) and key_of(a) is not None                     # identical labs dedup
    assert key_of(mk(config=Config(cost_bps=50.0))) != key_of(a)                # config
    assert key_of(mk(config=Config(cost_bps=cap_tiered({0: 75, 1000: 30})))) not in (key_of(a), None)
    assert key_of(mk(benchmark=IndexBenchmark("SYN_CAPWT", mer=0.01))) != key_of(a)
    assert key_of(mk(compare=(equal_weight_top(20),))) != key_of(a)
    # a redefined screen with the same name; an unrelated extra screen also changes the lab, a built-in does not
    c = mk()
    c.define(ScreenBuilder("dogs").universe(top=20).rank(div_yield="desc").take(5))
    assert key_of(c) != key_of(a)
    d = mk()
    d.define(ScreenBuilder("dogs").universe(top=20).rank(div_yield="desc").take(5))
    assert key_of(c) == key_of(d)
    e = mk()
    e.screens["dogs"] = REGISTRY["dogs"].with_overrides(universe=15)
    assert key_of(e) != key_of(a)


def test_python_function_lab_parts_make_the_key_none():
    f = mk()
    f.screens["mine"] = Screen("mine", lambda s, n: list(s.index[:n]), 3)
    assert key_of(f) is not None                                                 # not used by this call
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["mine"]})
    assert R.run_key_for(spec, "d", R.lab_context(f)) is None
    assert R.lab_context(mk(config=Config(weighting=lambda s: s))).reasons
    assert key_of(mk(config=Config(weighting=lambda s: s))) is None
