"""screenlab-tui entry point. Parses arguments first, so `--help` and `--version` never import Textual (or plotext);
the Textual app itself lives in `tui/ui.py` and is loaded on first use (`from screenlab.tui.app import ScreenlabApp`
still works through the module `__getattr__`)."""
from __future__ import annotations

import argparse


def _version() -> str:
    from importlib import metadata
    try:
        return metadata.version("screenlab")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="screenlab-tui", description="Interactive terminal UI for screenlab.")
    ap.add_argument("--version", action="version", version=f"screenlab-tui {_version()}")
    ap.add_argument("--lab", help="dataset name (default: sp500 when skfolio is installed, else synthetic)")
    ap.add_argument("--store", help="run store SQLite path, or 'none' (default: SCREENLAB_STORE or ~/.screenlab/runs.sqlite)")
    ap.add_argument("--control-port", type=int, default=8765, help="control endpoint port on 127.0.0.1; 0 = off "
                    "(with --serve the endpoint is the shared session and always on: 0 picks a free port)")
    ap.add_argument("--serve", action="store_true", help="serve the app in a browser (textual-serve) instead of the terminal")
    ap.add_argument("--remote", metavar="URL", help="be a client of a running screenlab-tui control endpoint "
                    "(its session) instead of building one; token from SCREENLAB_TUI_TOKEN. Used by --serve for "
                    "each browser tab; also joins a terminal TUI from a second terminal")
    ap.add_argument("--host", default="localhost", help="with --serve: host (default localhost)")
    ap.add_argument("--port", type=int, default=8000, help="with --serve: port (default 8000)")
    ap.add_argument("--theme", help="Textual theme name, e.g. textual-dark, textual-light, nord, gruvbox")
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    from . import ui
    ui.run(args, ap)


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    from . import ui
    try:
        return getattr(ui, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


if __name__ == "__main__":
    main()
