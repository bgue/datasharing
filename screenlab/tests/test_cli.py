"""Command-line entry points: --version, --data, MCP --help."""
import subprocess
import sys

import pytest

import screenlab
from screenlab import run, timing


def test_version_attribute():
    assert isinstance(screenlab.__version__, str) and screenlab.__version__.count(".") >= 1


@pytest.mark.parametrize("main", [run.main, timing.main])
def test_version_flag(main, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"screenlab {screenlab.__version__}"


def test_data_defaults_to_synthetic():
    ap_args = run.argparse.ArgumentParser()
    run.add_common_args(ap_args)
    assert ap_args.parse_args([]).data == "synthetic"


def test_data_rejects_unknown_choice(capsys):
    with pytest.raises(SystemExit) as e:
        run.main(["--data", "nope"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_csv_and_data_sp500_conflict():
    with pytest.raises(SystemExit, match="mutually exclusive"):
        run.main(["--data", "sp500", "--csv", "f.csv", "p.csv"])


def test_data_sp500_runs():
    pytest.importorskip("skfolio")
    rep = run.main(["--data", "sp500", "--only", "momentum_12_1,low_vol", "--rebalance", "12"])
    assert {"momentum_12_1", "low_vol", "SP500"} <= set(rep.table.index)


def test_timing_data_sp500_runs(capsys):
    pytest.importorskip("skfolio")
    tables = timing.main(["--data", "sp500", "--only", "low_vol", "--stride", "60"])
    assert len(tables) == 1 and len(tables[0]) > 1


def test_data_sp500_without_skfolio_is_a_clear_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "skfolio" or name.startswith("skfolio."):
            raise ImportError("No module named 'skfolio'")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(SystemExit) as e:
        run.main(["--data", "sp500"])
    assert "skfolio" in str(e.value) and '.[data]' in str(e.value)


def test_mcp_help_exits_instead_of_serving():
    out = subprocess.run([sys.executable, "-m", "screenlab.server.mcp_server", "--help"],
                         capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert out.returncode == 0
    assert "usage:" in out.stdout and "--transport" in out.stdout
