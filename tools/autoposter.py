#!/usr/bin/env python3
"""Post videos automatically, without being there.

Drop videos into a queue folder and this posts them on a schedule.

    # post every video in the folder, one every 2 hours
    python tools/autoposter.py --dir data/queue --interval 7200

    # post three now and exit (handy for cron)
    python tools/autoposter.py --dir data/queue --count 3 --interval 0

    # captions: one per line, cycled
    python tools/autoposter.py --dir data/queue --captions captions.txt

    # or a sidecar next to each video: clip.mp4 + clip.txt
    python tools/autoposter.py --dir data/queue --default-caption "hello"

Already-posted files are remembered in ``<dir>/.posted.json`` so a restart
never reposts. Write operations must be enabled: ALLOW_WRITE_OPS=true.
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
from app.bot.autoposter import AutoPoster  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402


async def run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await init_db()

    if not settings.allow_write_ops:
        print("ALLOW_WRITE_OPS is false — posting is disabled.")
        print("Set ALLOW_WRITE_OPS=true in .env to enable it.")
        return 2

    adapter = TikTokAdapter()
    await adapter.start()
    poster = AutoPoster(
        adapter,
        args.dir,
        captions=args.captions,
        default_caption=args.default_caption,
        interval=args.interval,
        limit=args.count,
        delete_after=args.delete_after,
        visibility=args.visibility,
        loop=args.loop,
        tags=args.tag or [],
    )

    try:
        if args.count:
            results = await poster.run_once(args.count)
            for result in results:
                print(result)
            print(f"posted {len(results)} video(s)")
            return 0 if results else 1
        print(f"autoposter watching {args.dir} — one post every {args.interval:.0f}s")
        await poster.run_forever()
        return 0
    finally:
        await adapter.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="data/queue", help="folder holding the videos")
    ap.add_argument("--captions", help="file with one caption per line")
    ap.add_argument("--default-caption", default="", help="caption when nothing else applies")
    ap.add_argument("--tag", nargs="*", help="hashtags appended to every caption")
    ap.add_argument("--interval", type=float, default=3600.0, help="seconds between posts")
    ap.add_argument("--count", type=int, help="post this many and exit")
    ap.add_argument("--delete-after", action="store_true", help="delete the video once posted")
    ap.add_argument("--visibility", type=int, default=0, choices=[0, 1, 2],
                    help="0 public, 1 friends, 2 private")
    ap.add_argument("--loop", action="store_true", help="keep polling when the queue empties")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
