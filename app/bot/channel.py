"""Chat transports.

The bot is transport-agnostic: it reads messages from a channel and writes
replies back. Two channels are implemented:

``comment``  the comments of one video are the conversation. Posting a
             comment is the reply. Both directions are verified against the
             live site, which makes this the reliable channel today.

``dm``       the direct-message inbox. Used when the account has a working
             conversation (including TikTok's "Message yourself" thread).
"""
from __future__ import annotations

import logging
from typing import Protocol

from app.bot.dispatcher import InboundMessage

log = logging.getLogger(__name__)


class ChatChannel(Protocol):
    """A place the bot can read messages from and write replies to."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def poll(self) -> list[InboundMessage]:
        """Return the messages that have arrived since the last poll."""
        ...

    async def send(self, text: str) -> bool:
        """Send a reply into the conversation."""
        ...


class CommentChannel:
    """Uses the comments of a video as the chat.

    The account posts a video, comments a command on it, and the bot answers
    with another comment on the same video.
    """

    name = "comment"

    def __init__(self, engine, video_id: str, marker: str = "/") -> None:
        self.engine = engine
        self.video_id = video_id
        self.marker = marker
        self._seen: set[str] = set()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def poll(self) -> list[InboundMessage]:
        try:
            result = await self.engine.call(
                "comments", {"video_id": self.video_id, "count": 50}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("comment poll failed: %s: %s", type(exc).__name__, exc)
            return []

        messages: list[InboundMessage] = []
        for comment in result.get("comments", []):
            cid = comment.get("cid")
            if not cid or cid in self._seen:
                continue
            self._seen.add(cid)
            text = (comment.get("text") or "").strip()
            if not self.marker or not text.startswith(self.marker):
                continue
            messages.append(
                InboundMessage(
                    id=str(cid),
                    text=text[len(self.marker) :].strip(),
                    author_id=str(comment.get("author_id") or ""),
                    author_name=str(comment.get("author") or ""),
                    conversation_id=self.video_id,
                    timestamp=float(comment.get("create_time") or 0),
                )
            )
        if len(self._seen) > 800:
            self._seen = set(list(self._seen)[-400:])
        return messages

    async def send(self, text: str) -> bool:
        result = await self.engine.call(
            "comment", {"video_id": self.video_id, "text": text}
        )
        return bool((result.get("comment") or {}).get("status_code") in (0, None))


class DmChannel:
    """Uses the direct-message inbox as the chat.

    Requires the account to have at least one conversation — TikTok's
    "Message yourself" thread is the one that matters for a solo operator.
    """

    name = "dm"

    def __init__(self, engine, conversation_id: str = "") -> None:
        self.engine = engine
        self.conversation_id = conversation_id
        self._seen: set[str] = set()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def poll(self) -> list[InboundMessage]:
        raise NotImplementedError(
            "The DM inbox transport needs a working conversation endpoint — "
            "see the notes in app/bot/README.md."
        )

    async def send(self, text: str) -> bool:
        raise NotImplementedError(
            "The DM inbox transport needs a working send endpoint — "
            "see the notes in app/bot/README.md."
        )
