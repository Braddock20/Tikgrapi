"""Cookie loaders + small helpers."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any


def load_session(path: str | Path) -> dict[str, str]:
    """Load Playwright/browser-exported cookies → flat name=value dict."""
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    return {c["name"]: c["value"] for c in raw}


def dump_session(cookies: dict[str, str], path: str | Path) -> None:
    arr = [{"name": k, "value": v, "domain": ".tiktok.com", "path": "/"} for k, v in cookies.items()]
    Path(path).write_text(json.dumps(arr, indent=2))


def random_device_id() -> str:
    """19-digit numeric device_id matching TikTok's web format."""
    return str(secrets.randbits(63))[:19]


def now_ms() -> int:
    return int(time.time() * 1000)


def merge_url(base: str, params: dict[str, Any]) -> str:
    """Build URL with %20 for spaces (not +) — TikTok validates this in signatures."""
    from urllib.parse import quote, urlencode
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params, doseq=True, quote_via=quote)}"


def split_query(url: str) -> tuple[str, str]:
    """Return (path-with-host, query-string-without-leading-?)."""
    if "?" not in url:
        return url, ""
    base, q = url.split("?", 1)
    return base, q
