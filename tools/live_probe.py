#!/usr/bin/env python3
"""Run operations against the live TikTok site and print the results.

This is the "test it for real" harness. Read operations run by default;
write operations need --write and are still gated by ALLOW_WRITE_OPS.

    python tools/live_probe.py --op profile --params '{"username": "auren_and_i"}'
    python tools/live_probe.py --op comments --params '{"video_id": "7684252586286927135"}'
    python tools/live_probe.py --all
    python tools/live_probe.py --all --account auren_and_i
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.tiktok import TikTokAdapter  # noqa: E402
from app.operations import OPERATIONS  # noqa: E402

# A video with comments, used when the caller does not give one.
DEFAULT_VIDEO_ID = "7684252586286927135"


def trim(value, limit=1500):
    text = json.dumps(value, indent=2, default=str)
    return text if len(text) <= limit else text[:limit] + "\n  …"


async def resolve_account(adapter) -> dict:
    """Ask the live site who the session belongs to."""
    result = await adapter.call("me", {})
    return (result or {}).get("account", {})


def default_params(operation: str, account: dict) -> dict:
    unique_id = account.get("unique_id") or ""
    sec_uid = account.get("sec_uid") or ""
    user_id = str(account.get("id") or "")
    return {
        "me": {},
        "profile": {"username": unique_id},
        "user_posts": {"sec_uid": sec_uid, "count": 10},
        "comments": {"video_id": DEFAULT_VIDEO_ID, "count": 5},
        "followers": {"sec_uid": sec_uid, "user_id": user_id, "count": 10},
        "following": {"sec_uid": sec_uid, "user_id": user_id, "count": 10},
        "explore_feed": {"count": 5},
    }.get(operation, {})


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--op", help="operation name")
    ap.add_argument("--params", default=None, help="JSON parameters")
    ap.add_argument("--all", action="store_true", help="run every verified read operation")
    ap.add_argument("--write", action="store_true", help="also run write operations")
    ap.add_argument("--account", help="username for operations that need one")
    args = ap.parse_args()

    if not args.op and not args.all:
        ap.error("pass --op NAME or --all")
    if args.op and args.all:
        ap.error("pass --op or --all, not both")

    adapter = TikTokAdapter()
    try:
        await adapter.start()
    except Exception as exc:  # noqa: BLE001
        print(f"backend failed to start: {type(exc).__name__}: {exc}")
        return 2

    failures = 0
    try:
        account = {}
        if args.all:
            account = await resolve_account(adapter)
            print(f"session account: {account.get('unique_id')} (id={account.get('id')})")

        if args.op:
            params = json.loads(args.params) if args.params else default_params(args.op, account)
            try:
                result = await adapter.call(args.op, params)
            except Exception as exc:  # noqa: BLE001
                print(f"[{args.op}] FAILED {type(exc).__name__}: {exc}")
                return 1
            print(f"[{args.op}] OK")
            print(trim(result))
            return 0

        for name, spec in OPERATIONS.items():
            if not spec.verified:
                continue
            if spec.write and not args.write:
                continue
            params = default_params(name, account)
            if args.account and "username" in params:
                params["username"] = args.account
            print(f"\n=== {name} (backend={spec.backend}, write={spec.write}) ===")
            try:
                result = await adapter.call(name, params)
                print(f"[{name}] OK")
                print(trim(result, 900))
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] FAILED {type(exc).__name__}: {exc}")
                failures += 1
    finally:
        await adapter.stop()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
