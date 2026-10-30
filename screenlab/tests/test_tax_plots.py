"""Smoke tests of research/tax_plots.py on a tiny synthetic run: every function draws and returns its Axes; nothing is written.

    python -m pytest screenlab/tests/test_tax_plots.py -q
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from screenlab import Lab
from screenlab.backtest import Result
from screenlab.harvesting import HARVEST_COLS
from screenlab.research import tax_plots as tp
from screenlab.taxes import TAX_COLS, CanadianTax, TaxConfig


@pytest.fixture(scope="module")
def rep():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=6, seed=3)
    tax = TaxConfig(model=CanadianTax("AB", other_income=90_000), account="taxable", rrsp_withdrawal_rate=0.30)
    return lab.account_compare(["stable_dividend"], tax=tax, rebalance_months=12)


@pytest.fixture(scope="module")
def hand():
    """A hand-built Result with a tax table and harvest records (no backtest needed)."""
    idx = pd.date_range("2020-01-31", periods=48, freq="ME")
    curve = pd.Series(np.exp(np.linspace(0, 0.5, 48) + 0.1 * np.sin(np.arange(48))), index=idx)
    tab = pd.DataFrame(0.0, index=[2020, 2021, 2022, 2023], columns=TAX_COLS)
    tab["tax_dividends"], tab["tax_capital_gains"], tab["tax_interest"] = 100.0, [0, 300, -50, 200], 20.0
    tab["tax_rrsp_withdrawal"] = [0, 0, 0, 900.0]
    h = pd.DataFrame([
        dict(date=idx[10], name="A", source="harvest", unrealized_pct=-0.2, loss=-0.02, denied=0.0, net_loss=-0.02,
             acb_added=0.0, days_out=31, replacement="B", rebought=True, window_end=idx[11], replacement_name="B", blocked_until=idx[11]),
        dict(date=idx[20], name="C", source="screen", unrealized_pct=-0.1, loss=-0.01, denied=0.005, net_loss=-0.005,
             acb_added=0.005, days_out=0, replacement="", rebought=False, window_end=idx[21], replacement_name=None, blocked_until=idx[21]),
        dict(date=idx[30], name="D", source="exit", unrealized_pct=-0.3, loss=-0.03, denied=0.0, net_loss=-0.03,
             acb_added=0.0, days_out=60, replacement="cash", rebought=False, window_end=idx[32], replacement_name=None, blocked_until=None),
    ], columns=HARVEST_COLS)
    return Result(name="hand", curve=curve, tax=tab, after_tax_curve=curve * 0.9, harvest=h)


def test_after_tax_curves(rep, hand):
    r = rep.reports["taxable"]
    ax = tp.after_tax_curves({"picks": r.results["stable_dividend"], "index": tp.index_result(r)})
    assert ax.get_yscale() == "log" and len(ax.lines) == 4
    assert any(ln.get_linestyle() == "--" for ln in ax.lines)
    plt.close("all")


def test_tax_drag_bars(rep):
    t = rep.table.copy()
    t["label"] = t["account"] + ":" + t["kind"]
    ax = tp.tax_drag_bars(t)
    assert len(ax.patches) == 2 * len(t)
    plt.close("all")


def test_tax_paid_stack(hand, rep):
    ax = tp.tax_paid_stack(hand)
    assert len(ax.patches) >= 4
    tp.tax_paid_stack(rep.reports["tfsa"].results["stable_dividend"])      # nothing taxed: still draws
    plt.close("all")


def test_marginal_rate_curves():
    ax = tp.marginal_rate_curves(("AB", "BC"), 2025, range(30_000, 150_001, 30_000))
    assert len(ax.lines) >= 8
    plt.close("all")


def test_withholding_matrix(rep):
    ax = tp.withholding_matrix(rep.foreign)
    assert len(ax.texts) == 3 * len(rep.foreign.drop_duplicates("class"))
    fr = rep.foreign.iloc[[0]].copy()
    fr["class"], fr["taxable"], fr["tfsa"], fr["rrsp"] = "US stock (direct)", "15% withheld, creditable (FTC)", "15% withheld, LOST", "EXEMPT (treaty)"
    ax = tp.withholding_matrix(fr)
    assert [t.get_text() for t in ax.texts[:3]] == ["15% credited", "15% lost", "exempt"]
    plt.close("all")


def test_harvest_timeline(hand):
    ax = tp.harvest_timeline(hand)
    assert len(ax.collections) >= 3
    plt.close("all")


def test_sensitivity_lines():
    df = pd.DataFrame({"rate": [0.2, 0.3, 0.4], "rrsp": [0.05, 0.04, 0.03], "idx": [0.06, 0.05, 0.04]})
    ax = tp.sensitivity_lines(df, "rate", {"rrsp": "RRSP", "idx": "index"}, title="t")
    assert len(ax.lines) == 2
    plt.close("all")


# R1: legend=, baseline= and refund bars -----------------------------------------------------------------------------
def test_legend_switch_and_baseline(rep, hand):
    r = rep.reports["taxable"]
    idx = tp.index_result(r)
    ax = tp.after_tax_curves({"picks": r.results["stable_dividend"]}, baseline=idx)
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    assert tp.BASELINE_LABEL in labels and any(ln.get_linestyle() == "--" and ln.get_color() == tp.INDEX_COL for ln in ax.lines)
    ax = tp.after_tax_curves({"picks": r.results["stable_dividend"]}, baseline=idx, legend=False)
    assert ax.get_legend() is None
    ax = tp.tax_paid_stack(hand, legend=False)
    assert ax.get_legend() is None
    ax = tp.harvest_timeline(hand, baseline=tp.index_result(r), legend=False, after_tax=True)
    assert ax.get_legend() is None and any(ln.get_linestyle() == "--" for ln in ax.lines)
    ax = tp.harvest_timeline(hand, baseline=tp.index_result(r))
    assert "Buy and hold the index (before tax)" in [t.get_text() for t in ax.get_legend().get_texts()]
    plt.close("all")


def test_tax_paid_stack_refund_bars_are_negative(hand):
    t = hand.tax.copy()
    t["refund"] = [0.0, 0.0, 400.0, 0.0]
    ax = tp.tax_paid_stack(Result(name="r", curve=hand.curve, tax=t))
    assert min(p.get_height() for p in ax.patches) == -400.0
    assert "Refund (loss carried back)" in [t_.get_text() for t_ in ax.get_legend().get_texts()]
    plt.close("all")
