"""The tax notebooks execute offline (code cells run in a fresh interpreter, Agg backend, magics removed) and say what
they are meant to say.

    python -m pytest screenlab/tests/test_tax_notebooks.py -q -p no:cacheprovider
"""
from __future__ import annotations

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


def test_foreign_holdings_notebook_runs():
    out = run_notebook(NB_DIR / "tax_foreign_holdings.ipynb")
    assert "agrees with the documented matrix (docs/TAXES.md): True" in out        # ledger == documented withholding matrix
    assert "= -6.50%" in out and "= +9.25%" in out                                  # the CAD-basis arithmetic, asserted in-cell
    assert "load an FX series for CAD basis" in out                                 # the no-FX warning is shown
    assert "foreign: 100% of names" in out                                          # the real-data pointer ran
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_foreign_holdings.ipynb"), 4)
    code = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code")
    assert "Buy and hold" in code and "Summary" in code and "What the evidence says" in code       # base case, summary, closing paragraph
    assert code.count("legend(") >= 6 and "tp.index_result" in code                                   # every figure has a legend; index base in Section 4


def test_where_to_hold_notebook_runs():
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_where_to_hold.ipynb"), 4)
    assert nb.cells[0].source.startswith("# Where should this stock portfolio be held")
    out = run_notebook(NB_DIR / "tax_where_to_hold.ipynb")
    assert "synthetic" in out                                  # the data card line printed
    code = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code")
    assert code.count("tp.") >= 6 and "tp.index_result" in code      # charts come from research/tax_plots.py, index on every one
    assert "Not tax advice" in nb.cells[-1].source and "UNVERIFIED" in nb.cells[-1].source
    assert "## Summary" in code and code.count("What the evidence says") >= 4                      # summary on top, a closing paragraph in every section
    assert code.count("baseline=") >= 5 and "Buy and hold the index (after tax)" in code             # the common base case on every chart


def test_loss_harvesting_notebook_runs():
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_loss_harvesting.ipynb"), 4)
    assert nb.cells[0].source.startswith("# Tax-loss harvesting")
    out = run_notebook(NB_DIR / "tax_loss_harvesting.ipynb")
    assert "harvesting: on (threshold 10%, second pick, 30-day window)" in out
    assert "carry-back refund: case 1 $13,000, case 2 $18,000; carried forward in case 2: $30,000" in out    # hand-built, exact
    assert "flat tax rate 20%" in out and "change in growth a year" in out
    assert "Allow denial" in out and "exit" in out                                                          # the variants and the exit table ran
    assert "Not tax advice" in nb.cells[0].source and "TaxDeferred/yr" in nb.cells[-1].source
    assert "Buy and hold A" in out and "for buy and hold of the index" in out                                     # the base case is in every table
    # composition: a summary right under the title, computed from the notebook's own variables, and a conclusion in each section
    assert "## Summary" in "".join(nb.cells[1].source) and 'md(f"""' in "".join(nb.cells[1].source)
    text = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "markdown")
    prose = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code" and "".join(c.source).startswith('md(f"""'))
    assert (text + prose).count("What the evidence says") >= 8
    for tic in ("let's", "Let's", "note that", "Note that", "!", "-> "):
        assert tic not in text + prose, tic
    # every chart has a legend and the base case: the library's legend= / baseline= or an explicit legend call, and a grey dashed line
    for c in nb.cells:
        src = "".join(c.source)
        if c.cell_type == "code" and "plt.show()" in src:
            assert "legend" in src or "tp." in src, src[:80]
            assert "INDEX_COL" in src or "baseline=" in src or "legend(handles" in src, src[:80]
