"""Fixtures for the jobs tests: an importable lab factory plus fake run kinds.

Importing this module registers the kinds, so a spawned worker that unpickles `make_lab` gets them too.
"""
from __future__ import annotations

import threading
import time

import pandas as pd

from screenlab import progress
from screenlab.api import Lab
from screenlab.jobs import register_kind

STATE = {"count": 0, "steps": 0}
GATE = threading.Event()


def make_lab() -> Lab:
    return Lab.synthetic(freq="ME", n_stocks=150, years=10, seed=3)


def _loop(lab, n=100, sleep=0.01, tag="x"):
    """Long loop with the real hook pattern: report, then work."""
    STATE["steps"] = 0
    for i in range(n):
        progress.report(i / n, f"step {i + 1}/{n}")
        time.sleep(sleep)
        STATE["steps"] += 1
    return pd.DataFrame({"steps": [float(n)]}, index=[tag])


def _count(lab, x=1):
    STATE["count"] += 1
    return pd.DataFrame({"x": [float(x)], "calls": [float(STATE["count"])]}, index=["r"])


def _fail(lab, msg="boom"):
    progress.report(0.1, "about to fail")
    raise ValueError(msg)


def _gate(lab, x=0):
    progress.report(0.1, "waiting")
    GATE.wait(10)
    progress.check_cancel()
    return pd.DataFrame({"x": [float(x)]}, index=["g"])


for _k, _f in (("fx_loop", _loop), ("fx_count", _count), ("fx_fail", _fail), ("fx_gate", _gate)):
    register_kind(_k, _f)


def make_daily_lab() -> Lab:
    return Lab.synthetic(freq="B", n_stocks=60, years=6, seed=3)
