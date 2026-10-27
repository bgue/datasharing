"""Errors shared by operations and the servers."""
from __future__ import annotations


class ServerError(Exception):
    """An error with an HTTP-style status (400 bad input, 404 unknown name, 501 not implemented)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message
