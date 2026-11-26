"""The bot loop: poll a channel, run commands, reply.

The runner owns the long-lived pieces — the chat channel, the dispatcher and
the services the commands reach for (the scheduler, the aliases) — so the
rest of the engine only has to know about one object.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.bot.channel import ChatChannel
from app.bot.dispatcher import Dispatcher

log = logging.getLogger(__name__)


class BotRunner:
    """Polls a channel on an interval and answers what it finds."""

    def __init__(
        self,
        engine,
        channel: ChatChannel,
        dispatcher: Dispatcher,
        interval: float = 15.0,
    ) -> None:
        self.engine = engine
        self.channel = channel
        self.dispatcher = dispatcher
        self.interval = interval
        self._running = False
        self._task: asyncio.Task | None = None
        self.last_poll: float = 0.0
        self.polls = 0
        self.replies = 0
        self.consecutive_failures = 0
        # The interval grows when the channel keeps failing, so a broken
        # channel does not turn into a request storm against TikTok.
        self.max_interval = max(interval * 8, 120.0)

    async def run_forever(self) -> None:
        self._running = True
        await self.channel.start()
        log.info("bot listening on the %s channel", self.channel.name)
        try:
            while self._running:
                await self._cycle()
                await asyncio.sleep(self._sleep_for())
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await self.channel.stop()
            except Exception:  # noqa: BLE001
                pass

    async def _cycle(self) -> None:
        self.polls += 1
        self.last_poll = time.time()
        try:
            messages = await self.channel.poll()
        except Exception as exc:  # noqa: BLE001
            log.warning("poll failed: %s: %s", type(exc).__name__, exc)
            self.consecutive_failures += 1
            return

        self.consecutive_failures = 0
        for message in messages:
            reply = await self.dispatcher.handle(message)
            if not reply:
                continue
            try:
                sent = await self.channel.send(reply)
            except Exception as exc:  # noqa: BLE001
                log.warning("reply failed: %s: %s", type(exc).__name__, exc)
                continue
            if sent:
                self.replies += 1

    def _sleep_for(self) -> float:
        """Back off gently when the channel misbehaves."""
        if self.consecutive_failures <= 0:
            return self.interval
        factor = min(self.consecutive_failures, 6)
        return min(self.max_interval, self.interval * (2 ** factor))

    def start_task(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run_forever())
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "channel": self.channel.name,
            "interval": self.interval,
            "polls": self.polls,
            "replies": self.replies,
            "last_poll": self.last_poll,
            "consecutive_failures": self.consecutive_failures,
            "dispatcher": dict(self.dispatcher.stats),
        }


__all__ = ["BotRunner"]
