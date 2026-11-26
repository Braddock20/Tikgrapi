#!/usr/bin/env python3
"""Run the chat bot on its own, without the API.

    python tools/bot.py --channel comment
    python tools/bot.py --channel dm
    python tools/bot.py --channel auto --video-id 7712427397584260373

The bot polls the channel, runs the commands it finds and replies in the
same place. It is the same code the service runs — this is the way to run
it on a machine that is not always on.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run(args) -> int:
    from app.adapters.tiktok import TikTokAdapter
    from app.bot.aliases import AliasStore
    from app.bot.channel import CommentChannel, DmChannel, MultiChannel
    from app.bot.dispatcher import Dispatcher
    from app.bot.engine import BotEngine
    from app.bot.runner import BotRunner
    from app.config import settings
    from app.scheduler import Scheduler
    from app.state import StateStore

    adapter = TikTokAdapter()
    await adapter.start()
    engine = BotEngine(adapter)

    store = StateStore()
    aliases = AliasStore(store)
    await aliases.load()

    dispatcher = Dispatcher(
        engine,
        allowed_authors=settings.bot_allowed_author_set,
        cooldown=settings.bot_cooldown,
    )
    scheduler = Scheduler(dispatcher=dispatcher, store=store)
    await scheduler.load()

    comment = CommentChannel(engine, video_id=args.video_id, marker=settings.bot_marker)
    dm = DmChannel(engine, marker=settings.bot_marker)
    if args.channel == "dm":
        channel = dm
    elif args.channel == "comment":
        channel = comment
    else:
        channel = MultiChannel([dm, comment])

    dispatcher.services = {
        "scheduler": scheduler,
        "aliases": aliases,
        "dispatcher": dispatcher,
        "channel": channel,
    }

    runner = BotRunner(engine, channel, dispatcher, interval=args.interval)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    print(f"listening on the {channel.name} channel — press Ctrl-C to stop")
    task = asyncio.create_task(runner.run_forever())
    scheduler_task = asyncio.create_task(scheduler.run_forever(30.0))

    try:
        await stop.wait()
    finally:
        runner.stop()
        scheduler.stop()
        for pending in (task, scheduler_task):
            try:
                await asyncio.wait_for(pending, timeout=5)
            except Exception:  # noqa: BLE001
                pass
        await adapter.stop()
    print("stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the chat bot")
    parser.add_argument("--channel", default="auto", choices=["auto", "comment", "dm"])
    parser.add_argument("--video-id", default="", help="the video whose comments are the chat")
    parser.add_argument("--interval", type=float, default=20.0, help="seconds between polls")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
