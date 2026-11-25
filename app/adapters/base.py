"""Adapter contracts shared by the TikTok backends."""
from __future__ import annotations

from typing import Any, Protocol


class UnsupportedOperation(RuntimeError):
    """Raised for operations that are not enabled in this adapter."""


class BackendUnavailable(RuntimeError):
    """Raised when a backend cannot run (missing browser, missing session)."""


class TikTokBackend(Protocol):
    """Minimal contract every backend implements."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def fetch_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET a TikTok API path and return the decoded JSON body."""
        ...

    async def fetch_page(self, path: str) -> dict[str, Any]:
        """Return the server-rendered data blob for a page."""
        ...
