"""Service layer: one `LabRegistry` shared by a REST app (rest.py) and an MCP server (mcp_server.py).

`fastapi`, `uvicorn`, `httpx` and `mcp` are optional and imported only by the modules that need them.
"""
from .registry import LabRegistry, ServerError, default_registry

__all__ = ["LabRegistry", "ServerError", "default_registry"]
