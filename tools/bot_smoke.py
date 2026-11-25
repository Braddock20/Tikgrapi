#!/usr/bin/env python3
"""Live smoke test for the bot layer.

Fetches real comments, feeds one through the dispatcher and prints the
reply. Nothing is posted — the send path is covered by the live comment
publish test instead.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.tiktok import TikTokAdapter  # noqa: E402
from app.bot import BotEngine, Dispatcher, InboundMessage  # noqa: E402
from app.db import init_db  # noqa: E402

VIDEO = os.environ.get("BOT_VIDEO_ID", "7684252586286927135")


async def main() -> int:
    await init_db()
    adapter = TikTokAdapter()
    await adapter.start()
    engine = BotEngine(adapter)
    dispatcher = Dispatcher(engine)

    try:
        result = await adapter.call("comments", {"video_id": VIDEO, "count": 10})
        comments = result.get("comments", [])
        print(f"live comments fetched: {len(comments)}")
        if not comments:
            print("no comments to work with")
            return 1

        sample = comments[0]
        print(f"sample comment: cid={sample['cid']} text={sample['text'][:60]!r}")

        for command in ("/whoami", "/help"):
            reply = await dispatcher.handle(
                InboundMessage(
                    id=f"smoke-{command}",
                    text=command,
                    author_id=str(sample.get("author_id") or ""),
                    author_name=str(sample.get("author") or ""),
                )
            )
            print(f"\n>>> {command}\n{reply}")

        # a real comment carrying the marker, stripped the way the channel
        # strips it, then run through the same path
        marked = InboundMessage(
            id=f"smoke-marked-{sample['cid']}",
            text="!whoami".lstrip("!"),
            author_id=str(sample.get("author_id") or ""),
            author_name=str(sample.get("author") or ""),
        )
        reply = await dispatcher.handle(marked)
        print(f"\n>>> marker command\n{reply}")
        print(f"\ndispatcher stats: {dispatcher.stats}")
        return 0
    finally:
        await adapter.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
