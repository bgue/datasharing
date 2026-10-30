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


def test_where_to_hold_notebook_runs():
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(str(NB_DIR / "tax_where_to_hold.ipynb"), 4)
    assert nb.cells[0].source.startswith("# Where should this stock portfolio be held")
    out = run_notebook(NB_DIR / "tax_where_to_hold.ipynb")
    assert "synthetic" in out                                  # the data card line printed
    code = "\n".join("".join(c.source) for c in nb.cells if c.cell_type == "code")
    assert code.count("tp.") >= 6 and "tp.index_result" in code      # charts come from research/tax_plots.py, index on every one
    assert "Not tax advice" in nb.cells[-1].source and "UNVERIFIED" in nb.cells[-1].source


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
