#!/usr/bin/env python3
"""Post videos from a folder, on a schedule.

    python tools/autoposter.py --dir data/queue --interval 7200
    python tools/autoposter.py --dir data/queue --count 3 --interval 0
    python tools/autoposter.py --dir data/queue --captions captions.txt
    python tools/autoposter.py --dir data/queue --default-caption "hello"
    python tools/autoposter.py --dir data/queue --tag fyp tiktok --delete-after --loop

Caption resolution: a sidecar next to the video (``clip.mp4`` -> ``clip.txt``)
wins, then the next line of the caption list, then ``--default-caption``.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run(args) -> int:
    from app.adapters.tiktok import TikTokAdapter
    from app.bot.autoposter import AutoPoster
    from app.bot.engine import BotEngine

    adapter = TikTokAdapter()
    try:
        await adapter.start()
    except Exception as exc:  # noqa: BLE001
        print(f"could not start the client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    engine = BotEngine(adapter)
    poster = AutoPoster(
        engine,
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
                if result.get("error"):
                    print(f"failed: {result['error']}")
                else:
                    print(f"posted: {result.get('video_path')} -> {result.get('aweme_id')}")
            return 0
        await poster.run_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        await adapter.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Post videos on a schedule")
    parser.add_argument("--dir", default="./data/queue", help="the queue folder")
    parser.add_argument("--count", type=int, help="post this many and stop")
    parser.add_argument("--interval", type=float, default=3600.0, help="seconds between posts")
    parser.add_argument("--captions", help="a file with one caption per line")
    parser.add_argument("--default-caption", default="", help="used when nothing else matches")
    parser.add_argument("--tag", nargs="*", help="hashtags appended to every caption")
    parser.add_argument("--delete-after", action="store_true", help="remove the file after posting")
    parser.add_argument("--loop", action="store_true", help="keep going when the queue empties")
    parser.add_argument(
        "--visibility", type=int, default=0, choices=[0, 1, 2],
        help="0 public, 1 friends, 2 private",
    )
    args = parser.parse_args()

    from app.config import settings

    if not settings.allow_write_ops:
        print(
            "ALLOW_WRITE_OPS is false — posting changes the account, so the "
            "autoposter refuses to start. Set ALLOW_WRITE_OPS=true to allow it.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
