"""Wording guard: people read "backtest", never "scenario" (docs/DECISIONS.md, 2026-11-01).

The interface still says `scenario` (command `add_scenario`, key `scenario`); this test only looks at strings a person reads.

    python -m pytest screenlab/tests/test_wording.py -q
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from screenlab import Lab
from screenlab.server.registry import LabRegistry
from screenlab.tui import ui
from screenlab.tui.session import Session

TUI = Path(ui.__file__).parent
FILES = ["ui.py", "screen_tab.py", "plan.py", "accounts.py", "web.py", "session.py"]

# Allow-list, string constants that may contain "scenario":
#  - `code font` spans and command names (add_scenario ...) inside a message are interface names
#  - a single token without spaces is an interface name (command, key, tool: add_scenario, scenario_id, "scenarios")
#  - constants used as dict keys, subscripts ([...]["scenario"]), call arguments that name a key or command
#    (.get("scenario"), _cmd("add_scenario", ...), getattr, hasattr) are interface, not text
#  - docstrings and comments are not shown to the user (command docstrings are covered by the help-text checks below)
#  - Monte Carlo "scenario" (resampled market paths) does not occur in these files; add it here if it ever does
KEY_CALLS = {"get", "_cmd", "command", "getattr", "hasattr", "setdefault", "pop", "_emit", "_find", "_find_many"}


def _user_strings(path: Path):
    tree = ast.parse(path.read_text())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        elif isinstance(node, ast.Dict):
            skip.update(id(k) for k in node.keys if k is not None)
        elif isinstance(node, ast.Subscript):
            skip.update(id(n) for n in ast.walk(node.slice))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in KEY_CALLS and node.args:
                skip.add(id(node.args[0]))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            s = node.value
            prose = re.sub(r"`[^`]*`|\b(add|clone|remove)_scenario\b|\bscenario_id\b", "", s)   # interface names in code font
            if "scenario" in prose.lower() and len(s.split()) > 1:     # prose, not a one-token interface name
                yield node.lineno, s


def test_no_user_facing_string_says_scenario():
    bad = [f"{f}:{ln} {s[:70]!r}" for f in FILES for ln, s in _user_strings(TUI / f)]
    assert not bad, "user-facing 'scenario' (say 'backtest'):\n" + "\n".join(bad)


def test_command_docstrings_and_tool_descriptions_say_backtest():
    bad = [n for n in dir(Session) if n.startswith("cmd_") and "scenario" in (inspect.getdoc(getattr(Session, n)) or "").lower()
           .replace("`scenario`", "").replace("add_scenario", "").replace("clone_scenario", "")]
    assert not bad, bad
    from screenlab.server import tui_tools
    src = Path(tui_tools.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef):
            doc = (ast.get_docstring(node) or "").lower()
            for ok in ("add_scenario", "clone_scenario", "remove_scenario", "scenario:", "`scenario`"):
                doc = doc.replace(ok, "")
            assert "scenario" not in doc, node.name


def test_help_add_dialog_and_screen_tab_say_backtest():
    assert "backtest" in ui.HELP.lower() and "scenario" not in ui.HELP.lower()
    title = inspect.signature(ui.AddScreen.__init__).parameters["title"].default
    assert title.startswith("Add backtest")
    assert 'Button("Add as backtest"' in (TUI / "screen_tab.py").read_text()


def test_compare_header_and_errors_say_backtest():
    reg = LabRegistry({"synthetic": lambda: Lab.synthetic(freq="ME", n_stocks=60, years=5, seed=1)},
                      default="synthetic", mode="thread")
    s = Session(reg)
    s.command("add_scenario", {"screen": "dogs", "run": False})
    header = s.view("compare").splitlines()
    assert any("Backtest" in ln for ln in header), header[:6]
    try:
        s.command("select", {"scenario": "ghost"})
    except Exception as e:                                          # SessionError(404, ...)
        assert "unknown backtest 'ghost'" in str(e) and "scenario" not in str(e).lower()
    else:
        raise AssertionError("expected an error")
