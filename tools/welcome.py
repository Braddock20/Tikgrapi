#!/usr/bin/env python3
"""Say hello on TikTok, once.

    python tools/welcome.py
    python tools/welcome.py --channel comment --video-id 7712427397584260373
    python tools/welcome.py --text "hello from the engine"

The message goes to the direct-message inbox when TikTok's messaging service
answers for this IP, and to a comment on the account's own newest video
otherwise. A comment is always available, and it is the channel the bot
listens on — so the reply loop closes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run(args) -> int:
    from app.adapters.tiktok import TikTokAdapter
    from app.bot.channel import CommentChannel, DmChannel, MultiChannel
    from app.bot.engine import BotEngine
    from app.bot.welcome import build_welcome_async, deliver_welcome
    from app.config import settings

    adapter = TikTokAdapter()
    try:
        await adapter.start()
    except Exception as exc:  # noqa: BLE001
        print(f"could not start the client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    engine = BotEngine(adapter)
    try:
        text = args.text or await build_welcome_async(
            engine, marker=settings.bot_marker, interval=int(settings.bot_poll_interval)
        )

        comment = CommentChannel(engine, video_id=args.video_id, marker=settings.bot_marker)
        dm = DmChannel(engine, marker=settings.bot_marker)
        if args.channel == "dm":
            channel = dm
        elif args.channel == "comment":
            channel = comment
        else:
            channel = MultiChannel([dm, comment])

        await channel.start()
        ok, detail = await deliver_welcome(channel, text)

        if ok:
            print(f"sent: {detail}")
            print("---- the message ----")
            print(text)
            return 0

        print(f"could not send it: {detail}", file=sys.stderr)
        print("---- the message ----")
        print(text)
        return 1
    finally:
        await adapter.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the welcome message")
    parser.add_argument("--channel", default="auto", choices=["auto", "comment", "dm"])
    parser.add_argument("--video-id", default="", help="the video to comment on")
    parser.add_argument("--text", default="", help="use this text instead")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
