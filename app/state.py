"""A tiny key/value store for the operator's own state.

Schedules, aliases and a few switches live here rather than in their own
tables — they are small, they are read and written as a unit, and they must
survive a restart.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal
from app.models import AppState

log = logging.getLogger(__name__)


class StateStore:
    """Reads and writes JSON blobs in the ``app_state`` table."""

    async def load_state(self, key: str) -> dict | None:
        async with SessionLocal() as db:
            row = await db.get(AppState, key)
            if row is None:
                return None
            return dict(row.value or {})

    async def save_state(self, key: str, value: dict) -> None:
        async with SessionLocal() as db:
            row = await db.get(AppState, key)
            if row is None:
                row = AppState(key=key, value=value)
                db.add(row)
            else:
                row.value = value
            await db.commit()

    async def delete_state(self, key: str) -> bool:
        async with SessionLocal() as db:
            row = await db.get(AppState, key)
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def get_flag(self, key: str, default: Any = None) -> Any:
        state = await self.load_state(key)
        if not state:
            return default
        return state.get("value", default)

    async def set_flag(self, key: str, value: Any) -> None:
        await self.save_state(key, {"value": value})


__all__ = ["StateStore"]
