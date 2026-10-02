"""Synthetic BIM generators: one elements.json per sector (seed 42)."""
from __future__ import annotations

from typing import Any, Callable

from . import civil, healthcare, industrial

GENERATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "industrial": industrial.generate,
    "civil": civil.generate,
    "healthcare": healthcare.generate,
}

__all__ = ["GENERATORS", "industrial", "civil", "healthcare"]
