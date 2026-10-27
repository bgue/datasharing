"""Progress and cancellation hook for long loops (DESIGN.md I2).

Library code calls `report(frac, msg)` and `check_cancel()` inside its long loops (rebalance dates, timing
offsets, sweep points, calibration points). Both are no-ops unless a job context is active, so the
synchronous SDK pays nothing and has no new dependency. A job runner activates them:

    with progress.job_context(callback, cancel_event):
        lab.timing("dogs", stride=1)

`callback(frac, msg)` receives progress in [0, 1]; `cancel_event` is anything with `is_set()` (a
threading.Event, a multiprocessing Event, or a polling wrapper). `report` also checks the cancel flag, so
one call per iteration is enough; `check_cancel` is for loops that have nothing to report.

Nested loops compose with `scaled(lo, hi)`: inside it, a loop reporting 0..1 maps onto [lo, hi] of the
outer range, so a backtest of three screens can give each screen a third of the bar.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator


class Cancelled(Exception):
    """Raised inside a running job when its cancel flag is set."""


@dataclass
class _Ctx:
    callback: Callable[[float, str], None] | None
    cancel: object | None             # has is_set()
    lo: float = 0.0
    hi: float = 1.0
    label: str = ""


_CTX: contextvars.ContextVar[_Ctx | None] = contextvars.ContextVar("screenlab_progress", default=None)


def active() -> bool:
    """Is a job context active in this thread / task?"""
    return _CTX.get() is not None


def check_cancel() -> None:
    """Raise `Cancelled` if the active job was asked to stop. No-op outside a job context."""
    c = _CTX.get()
    if c is not None and c.cancel is not None and c.cancel.is_set():
        raise Cancelled("job cancelled")


def report(frac: float, msg: str = "") -> None:
    """Report progress `frac` (0..1 of the current range) and check for cancellation. No-op outside a job."""
    c = _CTX.get()
    if c is None:
        return
    if c.cancel is not None and c.cancel.is_set():
        raise Cancelled("job cancelled")
    if c.callback is not None:
        f = min(1.0, max(0.0, float(frac)))
        c.callback(c.lo + (c.hi - c.lo) * f, f"{c.label}: {msg}" if c.label and msg else (c.label or msg))


@contextmanager
def job_context(callback: Callable[[float, str], None] | None = None, cancel_event=None):
    """Activate `report` / `check_cancel` for the enclosed block (in this thread only)."""
    tok = _CTX.set(_Ctx(callback, cancel_event))
    try:
        yield
    finally:
        _CTX.reset(tok)


@contextmanager
def scaled(lo: float, hi: float, label: str = ""):
    """Map reports made inside the block onto [lo, hi] of the enclosing range, and prefix their messages
    with `label` ("offset 3/12: dogs: rebalance 2/6"). No-op outside a job."""
    c = _CTX.get()
    if c is None:
        yield
        return
    span = c.hi - c.lo
    tok = _CTX.set(_Ctx(c.callback, c.cancel, c.lo + span * lo, c.lo + span * hi,
                        f"{c.label}: {label}" if c.label and label else (label or c.label)))
    try:
        yield
    finally:
        _CTX.reset(tok)


def track(items: Iterable, total: int | None = None, msg: str = "") -> Iterator:
    """Iterate `items`, reporting `i / total` before each item (and checking cancel). Plain iteration
    outside a job context."""
    if total is None:
        items = list(items)
        total = len(items)
    for i, x in enumerate(items):
        report(i / max(total, 1), f"{msg} {i + 1}/{total}".strip())
        yield x
