#!/usr/bin/env python3
"""Run the self-chat bot.

The bot reads commands from a TikTok chat channel and answers in the same
channel. Today the working channel is the comment thread of one video:
post a video, comment a command on it, and the bot replies with a comment.

    # post a video, then point the bot at it
    python tools/bot.py --channel comment --video-id 7684252586286927135

    # only accept commands from your own user id
    python tools/bot.py --channel comment --video-id <id> --allow 7712199357596075015

Commands are prefixed with the marker (default "!"), e.g. "!help", "!profile tatemcrae".
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.tiktok import TikTokAdapter  # noqa: E402
from app.bot import BotEngine, BotRunner, CommentChannel, Dispatcher  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402


async def run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await init_db()

    adapter = TikTokAdapter()
    await adapter.start()
    engine = BotEngine(adapter)

    allowed = {a for a in (args.allow or []) if a}
    dispatcher = Dispatcher(engine, allowed_authors=allowed, cooldown=args.cooldown)

    channel = CommentChannel(engine, args.video_id, marker=args.marker)
    runner = BotRunner(engine, channel, dispatcher, interval=args.interval)

    print(f"bot listening on the {channel.name} channel of video {args.video_id}")
    print(f"prefix commands with {args.marker!r} — try {args.marker}help")
    if not settings.allow_write_ops:
        print("write commands are DISABLED (ALLOW_WRITE_OPS=false)")

    try:
        await runner.run_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        await adapter.stop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", choices=["comment", "dm"], default="comment")
    ap.add_argument("--video-id", help="video whose comment thread is the chat")
    ap.add_argument("--marker", default="/", help="command prefix")
    ap.add_argument("--allow", nargs="*", help="user ids allowed to run commands")
    ap.add_argument("--interval", type=float, default=15.0, help="seconds between polls")
    ap.add_argument("--cooldown", type=float, default=2.0, help="seconds between commands")
    args = ap.parse_args()

    if args.channel == "comment" and not args.video_id:
        ap.error("--video-id is required for the comment channel")
    if args.channel == "dm":
        print("the DM channel needs a working IM conversation — see the notes in README.md")
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
