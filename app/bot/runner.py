"""The bot loop: poll a channel, run commands, reply."""
from __future__ import annotations

import asyncio
import logging

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

    async def run_forever(self) -> None:
        self._running = True
        await self.channel.start()
        log.info("bot listening on the %s channel", self.channel.name)
        try:
            while self._running:
                try:
                    messages = await self.channel.poll()
                except Exception as exc:  # noqa: BLE001
                    log.warning("poll failed: %s: %s", type(exc).__name__, exc)
                    messages = []

                for message in messages:
                    reply = await self.dispatcher.handle(message)
                    if not reply:
                        continue
                    try:
                        await self.channel.send(reply)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("reply failed: %s: %s", type(exc).__name__, exc)

                await asyncio.sleep(self.interval)
        finally:
            await self.channel.stop()

    def stop(self) -> None:
        self._running = False
