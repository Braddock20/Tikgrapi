"""The engine as the bot sees it.

Wraps the adapter with the two pieces of state a chat bot needs: whether
writes are currently allowed, and whether the session is still good.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.db import SessionLocal
from app.models import AppState
from app.sessions import current_session_meta


class BotEngine:
    """Adapter + operator switches, in one object."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    async def call(self, operation: str, params: dict) -> dict:
        return await self.adapter.call(operation, params)

    async def writes_paused(self) -> bool:
        async with SessionLocal() as db:
            state = await db.get(AppState, "writes_paused")
            return bool(state and state.value.get("paused"))

    async def writes_allowed(self) -> bool:
        """True when a write command may run.

        The adapter enforces the same rule again at execution time — this
        check exists so the bot can explain *why* a command was refused.
        """
        if not settings.allow_write_ops:
            return False
        return not await self.writes_paused()

    async def set_paused(self, paused: bool) -> None:
        async with SessionLocal() as db:
            state = await db.get(AppState, "writes_paused")
            if state is None:
                state = AppState(key="writes_paused", value={"paused": paused})
                db.add(state)
            else:
                state.value = {"paused": paused}
            await db.commit()

    async def session_meta(self) -> dict | None:
        return await current_session_meta()

    # -- the video the chat runs on --------------------------------------
    async def video_id(self) -> str:
        """The video the comment channel talks on.

        It is remembered rather than guessed: the account's own video list
        is not always readable (TikTok answers with an empty body from a
        datacenter IP), so the operator pins one video and the bot uses it.
        """
        async with SessionLocal() as db:
            state = await db.get(AppState, "bot_video_id")
            if state is None:
                return ""
            return str((state.value or {}).get("video_id") or "")

    async def set_video_id(self, video_id: str) -> None:
        async with SessionLocal() as db:
            state = await db.get(AppState, "bot_video_id")
            if state is None:
                state = AppState(key="bot_video_id", value={"video_id": video_id})
                db.add(state)
            else:
                state.value = {"video_id": video_id}
            await db.commit()
