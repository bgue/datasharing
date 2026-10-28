"""Terminal UI (Textual) with a control protocol: see tui/README.md and tui/PROTOCOL.md.

`Session` (state and commands) has no Textual import; the app, control endpoint and `main` load lazily."""
from .session import COMMANDS, PALETTE, PASSIVE_COLOR, TABS, Session, SessionError

__all__ = ["Session", "SessionError", "COMMANDS", "TABS", "PALETTE", "PASSIVE_COLOR"]
