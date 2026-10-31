"""Smoke tests for the interactive plan charts (research/plan_charts.py, Altair / Vega-Lite 5). # V1: whole file.

Each function returns a chart whose spec validates, has a tooltip on its mark layers and a legend naming the series, and the
index base case is a grey dashed series. Synthetic lab; no network, no panel.

    python -m pytest screenlab/tests/test_plan_charts.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json

import pytest

alt = pytest.importorskip("altair")
pytest.importorskip("vl_convert")

from screenlab import Lab
from screenlab.montecarlo import plan_mc
from screenlab.plan import CashFlow
from screenlab.research import plan_charts as pch
from screenlab.taxes import CanadianTax, TaxConfig

KW = dict(end="2008-12-31", initial=10000, periodic=500, growth="cpi", extra=[CashFlow("2004-03-01", 5000, "bonus")],
          withdrawals=800, withdraw_from="2007-01-01", goal=90000)


@pytest.fixture(scope="module")
def runs():
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=10, seed=3)

    def run(**o):
        return lab.plan("dogs", "2002-01-31", tax=TaxConfig(model=CanadianTax("AB"), macro=lab.macro, account="taxable"), **{**KW, **o})
    r, h = run(), run(cash_timing="hold")
    mc = plan_mc.plan_montecarlo(lab, r.spec, n_paths=40, seed=5, result=r, horizon_end="2011-12-31")
    return r, h, mc


def _layers(spec):
    """Every unit spec (a dict with a mark) anywhere in a Vega-Lite spec."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "mark" in o and "encoding" in o:
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(spec)
    return out


def _mark(u):
    return u["mark"]["type"] if isinstance(u["mark"], dict) else u["mark"]


def _legend_entries(spec):
    names = pch.series_names(spec)
    for u in _layers(spec):                                   # a legend can also come from a fill / shape / size encoding
        for ch in ("color", "shape"):
            e = u["encoding"].get(ch)
            if isinstance(e, dict) and e.get("legend") not in (None,) and isinstance(e.get("scale", {}).get("domain"), list):
                for n in e["scale"]["domain"]:
                    if n not in names:
                        names.append(n)
    return names


def _check(chart, needs_index=True, rule_or_point=True, dashed=True):
    assert isinstance(chart, (alt.Chart, alt.LayerChart, alt.HConcatChart, alt.VConcatChart, alt.FacetChart))
    spec = chart.to_dict()                                    # validates against the Vega-Lite 5 schema
    assert spec["$schema"].startswith("https://vega.github.io/schema/vega-lite/v5")
    units = _layers(spec)
    assert units
    # a tooltip on every mark that draws data (bars, points, rules, lines, areas), so no individual value is anonymous
    tipped = [u for u in units if _mark(u) in ("bar", "point", "rule", "circle", "arc") and "tooltip" in u["encoding"]]
    assert tipped
    if rule_or_point:
        assert any(_mark(u) in ("point", "rule", "bar") and u["encoding"].get("tooltip") for u in units)
    assert all(u["encoding"]["tooltip"] for u in units if _mark(u) in ("point", "rule", "bar") and "tooltip" in u["encoding"])
    entries = _legend_entries(spec)
    assert entries                                            # a legend naming the series
    txt = json.dumps(spec)
    if needs_index == "text":                                 # a facet header names the index panel instead of a legend entry
        assert "Buy and hold the index" in txt
    elif needs_index:
        assert any("Buy and hold the index" in n for n in entries), entries
    if needs_index is True:
        assert INDEX_GREY in txt and (not dashed or "strokeDash" in txt)     # grey (bars cannot be dashed), dashed for lines
    assert len(json.dumps(spec)) < 400_000                    # month-ends, bins: the data stays small
    return spec, units


INDEX_GREY = pch.INDEX


def test_wealth_chart(runs):
    r, _, _ = runs
    for kw in ({}, {"after_tax": False}, {"pretax": True, "log": False, "mark_goal": True, "flows": False}):
        spec, units = _check(pch.wealth_chart(r, **kw))
    spec, units = _check(pch.wealth_chart(r, flows=True))
    assert any(_mark(u) == "rule" and u["encoding"].get("tooltip") for u in units)              # the hover rule
    assert any(any(t.get("field") == "kind" for t in u["encoding"]["tooltip"]) for u in units if "tooltip" in u["encoding"])  # flow markers
    tips = [t["title"] for u in units if _mark(u) == "rule" for t in u["encoding"]["tooltip"]]
    assert any("gain" in t.lower() for t in tips) and any("Date" == t for t in tips)
    assert "params" in json.dumps(spec) and "bind" in json.dumps(spec)                              # .interactive(): zoom and pan
    assert "Goal $90k" in pch.series_names(spec)


def test_metrics_and_tax_and_flows(runs):
    r, _, _ = runs
    spec, _ = _check(pch.metrics_chart(r), dashed=False)
    assert "xOffset" in json.dumps(spec)
    _check(pch.tax_chart(r), needs_index="text", dashed=False)
    _check(pch.flows_chart(r))


def test_monte_carlo_charts(runs):
    _, _, mc = runs
    _check(pch.fan_chart(mc))
    _check(pch.fan_chart(mc, log=False))
    spec, units = _check(pch.terminal_hist(mc))
    tips = [t["title"] for u in units if _mark(u) == "bar" for t in u["encoding"]["tooltip"]]
    assert "Paths in the bin" in tips and "Share of paths" in tips and "Bin from" in tips
    _check(pch.paired_hist(mc))
    _check(pch.sequence_chart(mc), dashed=False)
    _check(pch.reversal_chart(mc), dashed=False)


def test_invest_vs_hold_and_png(runs, tmp_path):
    r, h, _ = runs
    c = pch.invest_vs_hold_chart(r, h)
    _check(c)
    out = pch.save_png(c, tmp_path / "ivh.png")
    assert (tmp_path / "ivh.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    b = pch.mimebundle(c)
    assert set(b) == {"application/vnd.vegalite.v5+json", "image/png", "text/plain"} and len(b["image/png"]) > 1000


def test_show_outside_a_kernel_names_the_legend(runs, capsys):
    pch.show(pch.reversal_chart(runs[2]))
    assert "[chart]" in capsys.readouterr().out
