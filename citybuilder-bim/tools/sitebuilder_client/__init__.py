"""Synchronous JSON-RPC client for the SiteBuilder control API (docs/05 sections 6 and 7)."""
from .client import DEFAULT_URL, GameApiError, GameClient, method_to_wire

__all__ = ["GameClient", "GameApiError", "DEFAULT_URL", "method_to_wire"]
