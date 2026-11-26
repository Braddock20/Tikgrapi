#!/usr/bin/env python3
"""Install a TikTok session from a cookie export.

    python tools/session_from_cookies.py --file cookies.txt --probe
    python tools/session_from_cookies.py --sessionid <the sessionid>
    cat cookies.txt | python tools/session_from_cookies.py

Accepts the raw ``Cookie:`` header a browser gives you, a Cookie-Editor
JSON export, or a bare sessionid.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a TikTok session")
    parser.add_argument("--file", help="a file with the cookies")
    parser.add_argument("--sessionid", help="just the sessionid")
    parser.add_argument("--probe", action="store_true", help="check it against the live site")
    args = parser.parse_args()

    raw = b""
    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 2
        raw = path.read_bytes()
    elif args.sessionid:
        raw = args.sessionid.strip().encode()
    else:
        raw = sys.stdin.buffer.read()

    if not raw.strip():
        print("nothing to install — pass --file, --sessionid or pipe the cookies in", file=sys.stderr)
        return 2

    async def run() -> int:
        from app.sessions import record_verification, save_session_material
        from app.security import (
            SessionError,
            extract_sessionid,
            mask_sessionid,
            parse_session_material,
        )

        try:
            cookies = parse_session_material(raw)
        except SessionError as exc:
            print(f"those cookies could not be read: {exc}", file=sys.stderr)
            return 2

        sessionid = extract_sessionid(cookies)
        if not sessionid:
            print("no sessionid in those cookies", file=sys.stderr)
            return 2

        info = await save_session_material(raw, cookies=cookies)
        print(f"sessionid : {mask_sessionid(sessionid)}")
        print(f"cookies   : {len(cookies)}")
        print(f"stored    : {info.get('cookies', len(cookies))} cookies (encrypted at {info.get('status', 'unverified')})")

        if not args.probe:
            return 0

        from app.adapters.tiktok import TikTokAdapter

        adapter = TikTokAdapter()
        try:
            await adapter.start()
            result = await adapter.call("me", {})
        except Exception as exc:  # noqa: BLE001
            await record_verification("invalid", f"{type(exc).__name__}: {str(exc)[:300]}")
            print(f"probe     : FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            await adapter.stop()

        account = result.get("account") or {}
        await record_verification(
            "valid",
            detail=f"probed via {adapter.backend_name}",
            username=account.get("unique_id"),
            account_id=str(account.get("id") or "") or None,
        )
        print(
            f"probe     : OK account={account.get('unique_id')} "
            f"id={account.get('id')} nickname={account.get('nickname')!r}"
        )
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
