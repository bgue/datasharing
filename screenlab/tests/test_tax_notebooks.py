"""The tax notebooks execute offline (code cells run in a fresh interpreter, Agg backend, magics removed) and say what
they are meant to say.

    python -m pytest screenlab/tests/test_tax_notebooks.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

NB_DIR = Path(__file__).resolve().parents[1] / "notebooks"


def run_notebook(path, timeout=300) -> str:
    """Execute the code cells of a notebook in a fresh interpreter; return its stdout. Fails with the stderr tail."""
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(path), 4)
    code = "\n".join(l for c in nb.cells if c.cell_type == "code" for l in "".join(c.source).splitlines()
                     if not l.lstrip().startswith("%"))
    script = Path(os.environ.get("TMPDIR", "/tmp")) / f"{Path(path).stem}_cells_{os.getpid()}.py"
    script.write_text("import matplotlib\nmatplotlib.use('Agg')\n" + code)
    try:
        out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(Path(path).resolve().parents[2])})
    finally:
        script.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


PANEL_PRICES = Path(__file__).resolve().parents[1] / "data" / "panel" / "prices.parquet"      # W2: the real panel (make data-panel), local use only


def test_foreign_holdings_notebook_runs():                       # W2: rebuilt on the real S&P 500 panel 1990-2022 and real USDCAD
    import re
    import time
    if not PANEL_PRICES.exists():
        pytest.skip("the S&P 500 panel is absent (make data-panel, local use only)")
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_foreign_holdings.ipynb"), 4)
    first = nb.cells[0].source
    assert nb.cells[0].cell_type == "markdown" and "unlicensed mixed-source dataset" in first       # the licence is the first thing read
    assert "make data-panel" in first and "local use" in first and "not redistributed" in first
    t0 = time.time()
    out = run_notebook(NB_DIR / "tax_foreign_holdings.ipynb", timeout=90)                           # with the on-disk cache: well under 90 s
    assert time.time() - t0 <= 90
    assert "Data: sp500_panel, 1990-01-02..2022-12-30" in out and "foreign: 100% of names" in out       # the real-data card ran
    assert "agrees with the documented matrix (docs/TAXES.md): True" in out                         # ledger == documented withholding matrix
    assert "etf_us and the US stock itself identically: True" in out                                # the US-listed ETF is the stock itself
    assert "load an FX series for CAD basis" in out and "no tax table, used 2025" in out            # the FX warning and the 2025-tables note are shown
    code_cells = [(i, "".join(c.source)) for i, c in enumerate(nb.cells) if c.cell_type == "code"]
    code = "\n".join(src for _, src in code_cells)
    text = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "markdown")
    prose = "\n".join(m.group(2) for m in re.finditer(r"say\(f?(\"\"\"|\'\'\')(.*?)\1\)", code, re.S))
    assert "Lab.real_sp500(start=\"1990-01-01\", end=\"2022-12-30\")" in code                        # exactly these arguments: the cache is hit
    # the Summary: two computed paragraphs, right after the (hidden) setup, before Section 1
    summ = [i for i, src in code_cells if "## Summary" in src]
    assert len(summ) == 1 and 'say(f"""' in dict(code_cells)[summ[0]]
    sec1 = next(i for i, c in enumerate(nb.cells) if c.cell_type == "markdown" and "".join(c.source).startswith("## 1."))
    assert summ[0] < sec1 and all(not "".join(nb.cells[j].source).startswith("##") for j in range(1, summ[0]))
    body = dict(code_cells)[summ[0]].split("## Summary", 1)[1]
    paras = [p for p in body.split("\n\n") if p.strip().startswith("**")]
    assert len(paras) == 2 and paras[0].startswith("**What withholding costs.**") and paras[1].startswith("**What the currency did.**")
    assert "{usd(CAP)}" in paras[0] and "{pts(" in paras[0] and "{usd(" in paras[1]                   # the numbers come from variables
    # composition: each of the five analysis sections ends in "What the evidence says" and opens with a question
    secs = []
    for c in nb.cells:
        src = "".join(c.source)
        if c.cell_type == "markdown" and src.startswith("## "):
            secs.append([src])
        elif secs:
            secs[-1].append(src)
    assert len(secs) == 6
    for sec in secs:
        assert "What the evidence says" in "\n".join(sec), sec[0][:40]
        assert "question" in sec[0], sec[0][:40]
    assert (text + code).count("What the evidence says") >= 5
    for tic in ("let's", "Let's", "note that", "Note that", "!", "-> "):
        assert tic not in text + prose, tic
    # every figure has a legend that names every element, and the base case: buy and hold of the index (or the same trades in US dollars)
    plots = [(i, src) for i, src in code_cells if "plt.show()" in src]
    assert len(plots) >= 7
    for i, src in plots:
        assert "legend" in src, src[:80]
        assert "buy and hold" in src.lower() or "baseline=" in src or "base case" in src, src[:80]
    # executed in place: every figure and the Summary are saved as outputs
    for i, _ in plots:
        assert any("image/png" in o.get("data", {}) for o in nb.cells[i].get("outputs", [])), i
    assert any("Summary" in o.get("data", {}).get("text/markdown", "") for o in nb.cells[summ[0]].get("outputs", []))
    assert "Not tax advice" in nb.cells[0].source


def test_where_to_hold_notebook_runs():
    from screenlab.realdata import sp500_panel
    if not sp500_panel.available():                        # the real S&P 500 panel is local-use-only data (make data-panel)
        pytest.skip("S&P 500 panel files absent (make data-panel)")
    import re
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_where_to_hold.ipynb"), 4)
    assert nb.cells[0].source.startswith("# Where should this stock portfolio be held")
    first = nb.cells[0].source
    assert "make data-panel" in first and "unlicensed" in first and "local use only" in first and "not redistributed" in first
    out = run_notebook(NB_DIR / "tax_where_to_hold.ipynb")
    assert "sp500_panel" in out and "1248 names" in out and "delisted yes (631)" in out and "synthetic" not in out    # real data, the card printed
    code = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code")
    assert code.count("tp.") >= 6 and "tp.index_result" in code      # charts come from research/tax_plots.py, index on every one
    assert "Not tax advice" in nb.cells[-1].source and "UNVERIFIED" in nb.cells[-1].source
    assert "## Summary" in code and code.count("What the evidence says") >= 5                      # summary on top, a closing paragraph in every section
    assert code.count("baseline=") >= 5 and "Buy and hold the index (after tax)" in code             # the common base case on every chart
    # the prose: markdown cells plus the f-string paragraphs written from the run's own numbers
    text = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "markdown")
    prose = "\n".join(m for c in nb.cells if c.cell_type == "code" for m in re.findall(r"say\(f\'\'\'(.*?)\'\'\'\)", "".join(c.source), re.S))
    assert "## Summary" in prose and prose.index("## Summary") == prose.find("##") and (text + prose).count("What the evidence says") >= 5
    for tic in ("let's", "Let's", "note that", "Note that", "!", "-> "):
        assert tic not in text + prose, tic
    # every chart has a legend and the base case: the library's legend= / baseline= or an explicit legend call, and a grey dashed line
    plots = 0
    for c in nb.cells:
        src = "".join(c.source)
        if c.cell_type == "code" and "def show(" not in src and ("show(fig)" in src or "plt.show()" in src):
            plots += 1
            assert "legend" in src or "tp." in src, src[:80]
            assert "INDEX_COL" in src or "baseline=" in src or "legend(handles" in src, src[:80]
    assert plots >= 9
    assert nb.cells[3].cell_type == "code" and "## Summary" in "".join(nb.cells[3].source)           # the summary is the first thing after the title


def test_loss_harvesting_notebook_runs():
    panel = Path(__file__).resolve().parents[1] / "data" / "panel" / "prices.parquet"
    if not panel.exists():
        pytest.skip("point-in-time S&P 500 panel not downloaded (make data-panel): the harvesting notebook runs on it")   # W3
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_loss_harvesting.ipynb"), 4)
    assert nb.cells[0].source.startswith("# Tax-loss harvesting")
    out = run_notebook(NB_DIR / "tax_loss_harvesting.ipynb", timeout=600)
    assert "harvesting: on (threshold 10%, second pick, 30-day window)" in out
    assert "carry-back refund: case 1 $13,000, case 2 $18,000; carried forward in case 2: $30,000" in out    # hand-built, exact
    assert "flat tax rate 20%" in out and "change in growth a year" in out
    assert "Allow denial" in out and "exit" in out                                                          # the variants and the exit table ran
    assert "Not tax advice" in nb.cells[0].source and "TaxDeferred/yr" in nb.cells[-1].source
    assert "Buy and hold A" in out and "for buy and hold of the index" in out                                     # the base case is in every table
    # real data: the data card line printed, 1,248 names and 631 delisted, the licence said in the first cell, the 2025 tables stated
    assert "sp500_panel, 1990-01-02..2022-12-30" in out and "delisted yes (631)" in out and "benchmark SP500_TR" in out
    first = nb.cells[0].source
    assert "unlicensed" in first and "make data-panel" in first and "local use only" in first and "2025" in first
    code_all = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code")
    assert 'Lab.real_sp500(start="1990-01-01", end="2022-12-30")' in code_all and "Lab.synthetic" not in code_all   # exact arguments: the cache is hit
    assert all(s in code_all for s in ('"dogs"', '"momentum_12m"', '"stable_high_yield"')) and "second_pick" in code_all
    # composition: a summary right under the title, computed from the notebook's own variables, and a conclusion in each section
    summary = "".join(nb.cells[1].source)
    assert "## Summary" in summary and 'md(f"""' in summary
    sumtxt = summary[summary.index("## Summary"):]
    paras = sumtxt.split("\n\n")
    assert len([p for p in paras if p.startswith("**What")]) == 2                                          # two paragraphs: cost, then worth
    assert "name-days" in sumtxt and "denied" in sumtxt and "Carry-back refunds" in sumtxt and "SP500_TR" in sumtxt
    text = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "markdown")
    prose = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code" and "".join(c.source).startswith('md(f"""'))
    assert (text + prose).count("What the evidence says") >= 6
    for tic in ("let's", "Let's", "note that", "Note that", "!", "-> "):
        assert tic not in text + prose + sumtxt, tic
    # every chart has a legend and the base case: the library's legend= / baseline= or an explicit legend call, and a grey dashed line
    n_plots = 0
    for c in nb.cells:
        src = "".join(c.source)
        if c.cell_type == "code" and "plt.show()" in src:
            n_plots += 1
            assert "legend" in src or "tp." in src, src[:80]
            assert "INDEX_COL" in src or "baseline=" in src or "legend(handles" in src, src[:80]
    assert n_plots >= 10


def test_plan_notebook_runs():                                   # PC: the plan notebook on the real S&P 500 panel, 1990-2022
    import re
    import time
    if not PANEL_PRICES.exists():
        pytest.skip("the S&P 500 panel is absent (make data-panel, local use only)")
    nbformat = pytest.importorskip("nbformat")
    path = NB_DIR / "plan_dca.ipynb"
    nb = nbformat.read(str(path), 4)
    first = nb.cells[0].source
    assert nb.cells[0].cell_type == "markdown" and "LOCAL USE ONLY" in first and "unlicensed" in first      # the licence is the first thing read
    assert "make data-panel" in first and "local use" in first and "not redistributed" in first and "Not tax advice" in first
    t0 = time.time()
    out = run_notebook(path, timeout=420)                        # with the on-disk cache: under three minutes
    assert time.time() - t0 <= 240
    for tbl in range(1, 8):
        assert f"Table {tbl}." in out
    code_cells = [(i, "".join(c.source)) for i, c in enumerate(nb.cells) if c.cell_type == "code"]
    code = "\n".join(src for _, src in code_cells)
    assert 'Lab.real_sp500(start=START, end=END)' in code and 'START, END = "1990-01-01", "2022-12-30"' in code   # exactly these arguments: the cache is hit
    assert "lab.plan(" in code and "plan_mc.plan_montecarlo(" in code and "result=plans[a]" in code and "generator=\"history\"" in code and 'growth="cpi"' in code and "withdrawals=3_000" in code
    assert 'CashFlow("1999-03-01", 25_000' in code and 'CashFlow("2009-03-01", 15_000' in code and 'withdraw_from="2018-01-01"' in code
    assert "rrsp_withdrawal_rate=rrsp_rate" in code and "rrsp_rate=0.30" in code and "other_income=90_000" in code and "GOAL = 1_500_000" in code
    # the Summary: two computed paragraphs, right after the setup and the runs, before Section 1
    summ = [i for i, src in code_cells if "## Summary" in src]
    assert len(summ) == 1 and 'say(f\'\'\'' in dict(code_cells)[summ[0]]
    sec1 = next(i for i, c in enumerate(nb.cells) if c.cell_type == "markdown" and "".join(c.source).startswith("## 1."))
    assert summ[0] < sec1 and all(not "".join(nb.cells[j].source).startswith("##") for j in range(1, summ[0]))
    body = dict(code_cells)[summ[0]].split("## Summary", 1)[1]
    paras = [p for p in body.split("\n\n") if p.strip().startswith("**")]
    assert len(paras) == 2 and paras[0].startswith("**What the plan produced.**") and paras[1].startswith("**What it means for the goal and the risk.**")
    assert "{usd(" in paras[0] and "{pc(" in paras[0] and "mt.p_beat_index" in paras[1] and "yrs_goal" in paras[1]       # the numbers come from variables
    # composition: seven sections, each opening with a question and ending in "What the evidence says"
    secs = []
    for c in nb.cells:
        src = "".join(c.source)
        if c.cell_type == "markdown" and src.startswith("## "):
            secs.append([src])
        elif secs:
            secs[-1].append(src)
    assert len(secs) == 7
    for sec in secs:
        assert "What the evidence says" in "\n".join(sec), sec[0][:40]
        assert "question" in sec[0], sec[0][:40]
    text = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "markdown")
    prose = "\n".join(m.group(2) for m in re.finditer(r"say\(f?(\"\"\"|\'\'\')(.*?)\1\)", code, re.S))
    for tic in ("let's", "Let's", "note that", "Note that", "!", "-> "):
        assert tic not in text + prose, tic
    # the XIRR / TWR paragraph, the reading guide (US stand-ins, 2025 tables, delisting haircut, licence), the base case in every figure
    assert "XIRR" in secs[2][0] and "TWR" in secs[2][0] and "time-weighted" in secs[2][0] and "money-weighted" in secs[2][0]
    guide = "\n".join(secs[6])
    assert all(s in guide for s in ("US CPI", "US 10-year", "DECISIONS.md", "2025", "haircut of 30%", "do not commit"))
    # V1: the figures are interactive Altair charts (research/plan_charts.py): no matplotlib; every plot cell calls pch.* and
    # shows the chart; the run prints one "[chart]" line per figure naming its legend entries, the index base case among them;
    # the saved outputs carry the Vega-Lite 5 spec (tooltips, legend, index series) and a PNG fallback in the same output
    assert "matplotlib" not in code and "plt." not in code and "pp." not in code
    plot_cells = [(i, src) for i, src in code_cells if re.search(r"^show\(", src, re.M)]
    n_fig = sum(len(re.findall(r"^show\(", src, re.M)) for _, src in plot_cells)
    assert n_fig >= 8 and len(plot_cells) >= 8 - 3                                               # figures 2 and 5 draw three panels, 6-8 several
    for i, src in plot_cells:
        assert "pch." in src or "panels(" in src, src[:80]                                       # drawn by plan_charts
    lines = [l for l in out.splitlines() if l.startswith("[chart]")]
    assert len(lines) == n_fig, (len(lines), n_fig)
    assert all("Buy and hold the index" in l for l in lines), lines                               # the base case in every figure's legend
    saved = nbformat.read(str(path), 4)                                                           # the outputs saved in the notebook
    bundles = [o for c in saved.cells if c.cell_type == "code" for o in c.get("outputs", [])
               if o.get("output_type") == "display_data" and "application/vnd.vegalite.v5+json" in o.get("data", {})]
    assert len(bundles) == n_fig, len(bundles)
    for o in bundles:
        spec = json.dumps(o["data"]["application/vnd.vegalite.v5+json"])
        assert "image/png" in o["data"] and len(o["data"]["image/png"]) > 1000                      # a static fallback beside the interactive chart
        assert '"tooltip"' in spec and '"legend"' in spec and "Buy and hold the index" in spec and "strokeDash" in spec or "xOffset" in spec
    assert path.stat().st_size < 5_000_000                                                        # month-end data keeps the notebook small
    assert "Buy and hold the index" in text and '"Buy and hold the index"' in code
