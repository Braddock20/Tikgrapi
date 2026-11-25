#!/usr/bin/env python3
"""Install a TikTok session from a cookie header or a JSON export.

Extracts the sessionid, normalises the cookie set and stores everything
encrypted. The sessionid is only ever printed in masked form.

    python tools/session_from_cookies.py --file cookies.txt
    python tools/session_from_cookies.py --file cookies.txt --probe
    cat cookies.txt | python tools/session_from_cookies.py --stdin
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sessions import save_session_material  # noqa: E402
from app.security import (  # noqa: E402
    SessionError,
    extract_sessionid,
    mask_sessionid,
    parse_session_payload,
)


def read_input(args) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        return Path(args.file).read_text()
    raise SystemExit("Pass --file PATH or --stdin")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="file holding the cookie header or JSON export")
    ap.add_argument("--stdin", action="store_true", help="read the session from stdin")
    ap.add_argument("--probe", action="store_true", help="check the session against the live site")
    args = ap.parse_args()

    raw = read_input(args)

    # Report the sessionid before storing it, so a failed write is still visible.
    cookies = parse_session_payload(raw)
    try:
        sessionid = extract_sessionid(cookies)
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"sessionid : {mask_sessionid(sessionid)}")
    print(f"cookies   : {len(cookies)}")

    info = asyncio.run(save_session_material(raw))
    print(f"stored    : {info['cookies']} cookies (encrypted at {info['status']})")
    if info["dropped_cookies"]:
        print(f"dropped   : {', '.join(info['dropped_cookies'])} (CDN edge cookies)")
    if info["missing_optional_cookies"]:
        print(f"note      : missing {', '.join(info['missing_optional_cookies'])}")

    if args.probe:
        from app.adapters.tiktok import TikTokAdapter

        async def run_probe():
            adapter = TikTokAdapter()
            await adapter.start()
            try:
                return await adapter.call("me", {})
            finally:
                await adapter.stop()

        try:
            result = asyncio.run(run_probe())
        except Exception as exc:  # noqa: BLE001
            print(f"probe     : FAILED {type(exc).__name__}: {exc}")
            return 1
        account = result.get("account", {})
        print(
            f"probe     : OK account={account.get('unique_id')} "
            f"id={account.get('id')} nickname={account.get('nickname')!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
