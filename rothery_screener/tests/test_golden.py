"""Golden-value regression tests: full backtests whose headline numbers are pinned in tests/golden/*.json.

A change to `simulate`, the metrics, the data model or a screen that moves any of these numbers must be
deliberate. After checking that the change is intended, rewrite the files with

    ROTHERY_UPDATE_GOLDEN=1 python -m pytest rothery_screener/tests/test_golden.py -q
    (or: python -m pytest rothery_screener/tests/test_golden.py --update-golden)

and review the diff of tests/golden/*.json.

    python -m pytest rothery_screener/tests/test_golden.py -q
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from rothery_screener import ExitPolicy, ExitScreen, Lab, TrendBreak

GOLDEN = Path(__file__).parent / "golden"
COLS = ["CAGR", "Vol", "MaxDD", "Excess/yr", "Turnover"]
DECIMALS = 6


def snapshot(rep) -> dict:
    """{row: {col: value rounded to 1e-6 or None for NaN}} for the headline columns of a report."""
    out = {}
    for name, row in rep.table.iterrows():
        out[str(name)] = {c: (None if math.isnan(float(row[c])) else round(float(row[c]), DECIMALS)) for c in COLS}
    return out


def check_golden(request, name: str, got: dict):
    path = GOLDEN / f"{name}.json"
    if request.config.getoption("--update-golden") or os.environ.get("ROTHERY_UPDATE_GOLDEN") == "1":
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
        return
    want = json.loads(path.read_text())
    assert sorted(got) == sorted(want), "rows changed"
    for row in want:
        for c in COLS:
            g, w = got[row][c], want[row][c]
            if w is None:
                assert g is None, f"{name} {row} {c}: expected NaN, got {g}"
            else:
                assert g == pytest.approx(w, abs=2e-6), f"{name} {row} {c}: {g} != {w}"


def test_golden_synthetic_all_builtin_screens(request):
    lab = Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)
    rep = lab.backtest()                                   # every built-in screen, default Config
    assert len(rep.table) == len(lab.screens) + 1
    check_golden(request, "synthetic_all_screens", snapshot(rep))


def test_golden_bundled_sp500_with_and_without_trend_exit(request):
    pytest.importorskip("skfolio")
    from rothery_screener.realdata.bundled import sp500_lab
    lab = sp500_lab()
    # the fixed, uncalibrated trend-only exit of research/CALIBRATION_SP500.md
    pol = ExitPolicy(ExitScreen([TrendBreak("SP500")], threshold=0.8, hysteresis=0.1, check="W-FRI",
                                min_hold_days=10), exposure_when_flagged=0.0, park="cash", switch_cost_bps=5.0,
                     reentry="signal")
    rep = lab.backtest(["momentum_12_1", "low_vol"], rebalance_months=1, start="1991-02-01",
                       exit=pol, with_and_without=True)
    assert list(rep.table.index) == ["SP500", "momentum_12_1", "momentum_12_1+exit", "low_vol", "low_vol+exit"]
    check_golden(request, "bundled_sp500", snapshot(rep))
