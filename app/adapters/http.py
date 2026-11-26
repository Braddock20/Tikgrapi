"""Plain-HTTP backend.

Some TikTok endpoints answer without any request signature. Those work from a
lightweight HTTP client, which is far cheaper than driving a browser. This
backend serves them; everything else goes through the browser backend.

Uses curl_cffi so the TLS fingerprint matches a real Chrome.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from curl_cffi import requests as curl_requests

from app.config import settings
from app.security import filter_for_http, parse_session_payload, read_session

log = logging.getLogger(__name__)


def _impersonate() -> str:
    """The TLS fingerprint to present, with a safe fallback.

    curl_cffi only knows the browser versions it was built against. Asking
    for one it does not have would take the whole HTTP backend down, so an
    unknown target falls back to the generic Chrome fingerprint.
    """
    wanted = (settings.http_impersonate or "chrome").strip()
    try:
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        import typing

        known = set(typing.get_args(BrowserTypeLiteral))
    except Exception:  # noqa: BLE001
        known = set()
    if not known or wanted in known:
        return wanted
    log.warning(
        "curl_cffi does not know the TLS fingerprint %r — using the generic "
        "Chrome one instead", wanted,
    )
    return "chrome"


def base_params() -> dict[str, str]:
    """Query parameters TikTok expects on every web API call."""
    return {
        "aid": "1988",
        "app_language": "en",
        "app_name": "tiktok_web",
        "browser_language": "en",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "browser_platform": "MacIntel",
        "browser_version": "5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "channel": "tiktok_web",
        "cookie_enabled": "true",
        "data_collection_enabled": "true",
        "device_platform": "web_pc",
        "focus_state": "true",
        "history_len": "5",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "os": "mac",
        "priority_region": "US",
        "region": "US",
        "screen_height": "1050",
        "screen_width": "1680",
        "tz_name": "America/New_York",
        "webcast_language": "en",
        "from_page": "user",
    }


class HttpBackend:
    """Cookie-bearing HTTP client for unsigned TikTok endpoints."""

    name = "http"

    def __init__(self) -> None:
        self._session: curl_requests.Session | None = None
        self._lock = asyncio.Lock()
    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        await self._refresh_session()

    async def stop(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    async def _refresh_session(self) -> None:
        """Rebuild the HTTP session from the stored (encrypted) session file.

        The CDN idc cookie pair is removed: with it, TikTok's unsigned
        endpoints answer with an empty body.
        """
        cookies, _dropped = filter_for_http(parse_session_payload(read_session()))
        session = curl_requests.Session(impersonate=_impersonate())
        session.cookies.update(cookies)
        old = self._session
        self._session = session
        if old is not None:
            old.close()

    def _ensure(self) -> curl_requests.Session:
        if self._session is None:
            raise RuntimeError("HTTP backend is not started")
        return self._session

    # -- requests --------------------------------------------------------
    async def fetch_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**base_params(), **{k: v for k, v in params.items() if v is not None}}
        return await asyncio.to_thread(self._get, path, params)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        session = self._ensure()
        url = f"https://www.tiktok.com{path}"
        resp = session.get(
            url,
            params=params,
            headers={
                "User-Agent": settings.http_user_agent,
                "Referer": "https://www.tiktok.com/",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=25,
        )
        return self._decode(resp)

    async def fetch_page(self, path: str) -> dict[str, Any]:
        """Not supported by the HTTP backend — the page blob needs a browser."""
        raise RuntimeError("fetch_page requires the browser backend")

    # -- renewal ---------------------------------------------------------
    async def harvest_cookies(self) -> dict[str, str]:
        """The cookies the site currently holds."""
        if self._session is None:
            return {}
        # curl_cffi's cookie jar is a mapping: iterating it yields the names,
        # so the values are read through get_dict().
        return {str(k): str(v) for k, v in self._session.cookies.get_dict().items() if v}

    async def renew_session(self) -> dict[str, str]:
        """Visit the site so it re-mints the device tokens.

        The plain-HTTP backend cannot mint an msToken the way the site's
        JavaScript does, but the server-side tokens (ttwid, the csrf token,
        the chain token) are refreshed on any page visit — enough to keep a
        session usable.
        """
        session = self._ensure()

        def _visit() -> None:
            session.get(
                "https://www.tiktok.com/",
                headers={
                    "User-Agent": settings.http_user_agent,
                    "Referer": "https://www.tiktok.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=25,
            )

        await asyncio.to_thread(_visit)
        return await self.harvest_cookies()

    @staticmethod
    def _decode(resp) -> dict[str, Any]:
        text = resp.text or ""
        if not text:
            raise RuntimeError(
                f"TikTok returned an empty body for {resp.request.url[:120]} "
                "(the endpoint needs a signed request — use the browser backend)"
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"TikTok returned a non-JSON body: {text[:160]}") from exc
        if isinstance(data, dict) and data.get("status_code") not in (None, 0):
            raise RuntimeError(
                f"TikTok error {data.get('status_code')}: {data.get('status_msg', '')}"
            )
        return data
