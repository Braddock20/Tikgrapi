"""Message -> command -> reply.

The dispatcher is deliberately strict about who it listens to and what it
runs: it only answers the operator, it never answers its own messages, and
write commands need both the engine switch and the operator's pause switch
to be off.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.bot.commands import COMMANDS, WRITE_COMMANDS, CommandContext, resolve

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 3000


@dataclass
class InboundMessage:
    """One message the bot received."""

    id: str
    text: str
    author_id: str = ""
    author_name: str = ""
    conversation_id: str = ""
    timestamp: float = field(default_factory=time.time)
    is_self: bool = False
    channel: str = ""


class Dispatcher:
    """Runs commands on behalf of the operator."""

    def __init__(
        self,
        engine: Any,
        allowed_authors: set[str] | None = None,
        cooldown: float = 2.0,
        services: dict[str, Any] | None = None,
    ) -> None:
        self.engine = engine
        self.allowed_authors = allowed_authors or set()
        self.cooldown = cooldown
        self.services = services or {}
        self._seen: dict[str, float] = {}
        self._last_command = 0.0
        self.stats = {"handled": 0, "ignored": 0, "errors": 0, "denied": 0}

    # -- housekeeping ----------------------------------------------------
    def _is_new(self, message_id: str) -> bool:
        """True the first time we see a message id."""
        if message_id in self._seen:
            return False
        self._seen[message_id] = time.time()
        # keep the cache small
        if len(self._seen) > 500:
            cutoff = time.time() - 3600
            self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
        return True

    def _authorised(self, message: InboundMessage) -> bool:
        if not self.allowed_authors:
            return True
        return message.author_id in self.allowed_authors or message.is_self

    # -- main entry ------------------------------------------------------
    async def handle(self, message: InboundMessage) -> str | None:
        """Return the reply to send, or None to stay silent."""
        text = (message.text or "").strip()
        if not text:
            return None

        if not self._is_new(message.id):
            return None
        if message.is_self and message.author_id and not self.allowed_authors:
            # our own reply echoing back into the thread
            return None
        if not self._authorised(message):
            self.stats["ignored"] += 1
            self.stats["denied"] += 1
            return None

        # The channel decides what counts as a command (it filters on the
        # marker); here we only need the first token to name one.
        token, _, rest = text.partition(" ")
        command = resolve(token)
        if command is None:
            # An alias may stand in for a command the operator uses often.
            aliases = self.services.get("aliases")
            if aliases is not None:
                target = aliases.resolve(token)
                if target:
                    parts = target.split(None, 1)
                    command = resolve(parts[0])
                    if command is not None:
                        tail = parts[1] if len(parts) > 1 else ""
                        rest = f"{tail} {rest}".strip()
        if command is None:
            self.stats["ignored"] += 1
            return None

        if command.write and not await self.engine.writes_allowed():
            return (
                f"'{command.name}' changes account state and writes are "
                "disabled (ALLOW_WRITE_OPS=false or the bot is paused)."
            )

        now = time.time()
        wait = self.cooldown - (now - self._last_command)
        if wait > 0:
            # Never block the event loop — the bot is asyncio all the way
            # down and a blocking sleep here stalls every other task.
            await asyncio.sleep(wait)
        self._last_command = time.time()

        args = rest.split()
        ctx = CommandContext(
            engine=self.engine,
            author_id=message.author_id,
            author_name=message.author_name,
            services=self.services,
        )
        try:
            reply = await command.handler(ctx, args)
            self.stats["handled"] += 1
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            log.exception("command %s failed", command.name)
            reply = f"{command.name} failed: {type(exc).__name__}: {exc}"

        reply = (reply or "").strip()
        if len(reply) > MAX_REPLY_CHARS:
            reply = reply[: MAX_REPLY_CHARS - 3] + "…"
        return reply or None

    async def run_text(self, text: str, author_id: str = "", author_name: str = "") -> str | None:
        """Run a command from a string — used by the API, the web UI and the
        scheduler, so there is exactly one command path."""
        message = InboundMessage(
            id=f"inline-{time.time_ns()}",
            text=text,
            author_id=author_id,
            author_name=author_name,
        )
        return await self.handle(message)


def help_text() -> str:
    lines = ["commands:"]
    for command in COMMANDS.values():
        lines.append(f"{command.usage} — {command.summary}")
    return "\n".join(lines)


__all__ = ["Dispatcher", "InboundMessage", "help_text", "WRITE_COMMANDS"]
