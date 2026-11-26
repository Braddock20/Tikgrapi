#!/usr/bin/env python3
"""Run the read operations against the live site and report what answers.

    python tools/live_probe.py --all
    python tools/live_probe.py --ops me,profile,comments
    python tools/live_probe.py --ops profile --params '{"username": "auren_and_i"}'

Read-only by default. Nothing here changes the account.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The read operations, with the arguments they need.
PROBES = {
    "me": {},
    "profile": {"username": "auren_and_i"},
    "account_settings": {},
    "my_posts": {"count": 5},
    "comments": {"video_id": "7712427397584260373", "count": 5},
    "notification_count": {},
    "notifications": {"count": 5},
    "dm_conversations": {},
    "followers": {"sec_uid": "", "user_id": "", "count": 5},
    "following": {"sec_uid": "", "user_id": "", "count": 5},
    "explore_feed": {"count": 5},
}


async def run(operations: list[str], params_override: dict) -> int:
    from app.adapters.tiktok import TikTokAdapter

    adapter = TikTokAdapter()
    try:
        await adapter.start()
    except Exception as exc:  # noqa: BLE001
        print(f"could not start the client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    failures = 0
    try:
        for name in operations:
            params = {**PROBES.get(name, {}), **params_override.get(name, {})}
            missing = [k for k, v in params.items() if not v]
            if missing:
                print(f"{name:24s} SKIP   needs {', '.join(missing)}")
                continue
            try:
                result = await adapter.call(name, params)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"{name:24s} FAIL   {type(exc).__name__}: {str(exc)[:120]}")
                continue
            size = len(json.dumps(result, default=str))
            print(f"{name:24s} OK     {size} bytes")
            if size < 900:
                print("         " + json.dumps(result, default=str)[:800])
    finally:
        await adapter.stop()
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the live site")
    parser.add_argument("--all", action="store_true", help="run every read operation")
    parser.add_argument("--ops", help="a comma separated list of operations")
    parser.add_argument("--params", default="{}", help="JSON: {\"op\": {\"key\": \"value\"}}")
    args = parser.parse_args()

    if args.ops:
        operations = [o.strip() for o in args.ops.split(",") if o.strip()]
    elif args.all:
        operations = list(PROBES)
    else:
        parser.print_help()
        return 2

    try:
        override = json.loads(args.params)
    except json.JSONDecodeError as exc:
        print(f"--params is not valid JSON: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(run(operations, override))


if __name__ == "__main__":
    raise SystemExit(main())
