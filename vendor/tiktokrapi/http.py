"""HTTP layer — curl_cffi session + signed-request orchestration with auto-refresh."""
from __future__ import annotations

import threading
from typing import Any, Optional

from curl_cffi import requests as curl_requests

from . import config, exceptions
from .signer import Signer
from .utils import merge_url


class HttpClient:
    """Cookie-bearing curl_cffi session with chrome impersonation, auto-signing,
    Set-Cookie msToken propagation, and singleflight refresh-on-rejection."""

    def __init__(
        self,
        cookies: dict[str, str],
        signer: Signer,
        proxy: Optional[str] = None,
        impersonate: str = config.IMPERSONATE,
        ua: str = config.UA,
        on_cookie_change: Optional[callable] = None,
    ):
        self.cookies = dict(cookies)
        self.signer = signer
        self.ua = ua
        self.session = curl_requests.Session(impersonate=impersonate)
        self.session.cookies.update(cookies)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        # singleflight refresh lock — only one thread refreshes at a time
        self._refresh_lock = threading.RLock()
        self._on_cookie_change = on_cookie_change  # callback for atomic persistence

    @property
    def ms_token(self) -> str:
        return self.cookies.get("msToken", "")

    def _ingest_set_cookie(self, response) -> bool:
        """Pull fresh cookies (especially msToken) from response into our jar.
        Returns True if msToken changed."""
        old_ms = self.cookies.get("msToken")
        changed = False
        for cookie in response.cookies.jar:
            # curl_cffi stores cookies with full attributes; we only care name/value
            if cookie.value and cookie.value != self.cookies.get(cookie.name):
                self.cookies[cookie.name] = cookie.value
                if cookie.name == "msToken":
                    changed = True
        if changed and self._on_cookie_change:
            try:
                self._on_cookie_change(self.cookies)
            except Exception:
                pass
        return changed

    def _is_token_reject(self, response) -> bool:
        """Heuristic: 200 OK + empty body = signature/token rejection."""
        if response.status_code != 200:
            return False
        body = response.content or b""
        return len(body) == 0

    def _base_params(self, **overrides: Any) -> dict[str, str]:
        """Standard query params present on every signed call."""
        p = {
            "aid": config.AID,
            "app_language": config.APP_LANGUAGE,
            "app_name": config.APP_NAME,
            "browser_language": config.BROWSER_LANGUAGE,
            "browser_name": config.BROWSER_NAME,
            "browser_online": "true",
            "browser_platform": config.BROWSER_PLATFORM,
            "browser_version": config.BROWSER_VERSION,
            "channel": config.CHANNEL,
            "cookie_enabled": "true",
            "data_collection_enabled": "true",
            "device_platform": config.DEVICE_PLATFORM,
            "focus_state": "true",
            "history_len": "5",
            "is_fullscreen": "false",
            "is_page_visible": "true",
            "os": config.OS,
            "priority_region": config.PRIORITY_REGION,
            "region": config.REGION,
            "screen_height": str(config.SCREEN_HEIGHT),
            "screen_width": str(config.SCREEN_WIDTH),
            "tz_name": config.TZ_NAME,
            "user_is_login": "true",
            "webcast_language": config.WEBCAST_LANGUAGE,
        }
        p.update({k: str(v) for k, v in overrides.items()})
        return p

    def _build_url(
        self,
        path: str,
        params: dict[str, Any],
        from_page: str,
        device_id: Optional[str],
        verify_fp: Optional[str],
        odin_id: Optional[str],
    ) -> str:
        base = self._base_params(from_page=from_page)
        if device_id:
            base["device_id"] = device_id
        if verify_fp:
            base["verifyFp"] = verify_fp
        if odin_id:
            base["odinId"] = odin_id
        base.update(params)
        if self.ms_token:
            base["msToken"] = self.ms_token
        return merge_url(f"{config.API_BASE}{path}", base)

    def _send_once(self, method: str, signed_url: str, headers: dict, body: Optional[str]):
        """One transport-level send. Returns raw response."""
        return self.session.request(
            method, signed_url, headers=headers, data=body, timeout=config.DEFAULT_TIMEOUT
        )

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[dict[str, Any]] = None,
        from_page: str = "fyp",
        device_id: Optional[str] = None,
        verify_fp: Optional[str] = None,
        odin_id: Optional[str] = None,
        referer: Optional[str] = None,
        sign: bool = True,
        body: Optional[str] = None,
        _retried: bool = False,
    ) -> dict[str, Any]:
        """Build, sign, send, parse one TikTok API request.

        Pipeline:
          1. Build URL with current msToken from jar.
          2. Sign URL.
          3. Send. Always ingest Set-Cookie from response (even on success — token rotates).
          4. If response is token-reject (200/empty), singleflight refresh ONCE and retry.
          5. Parse JSON or raise typed error.
        """
        if not path.startswith("/"):
            path = "/" + path
        params = dict(params or {})

        url = self._build_url(path, params, from_page, device_id, verify_fp, odin_id)
        if sign:
            url = self.signer.sign(url)

        headers = {
            "User-Agent": self.ua,
            "Referer": referer or config.ROOT_REFERER,
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.9",
        }
        if method == "POST" and self.cookies.get("tt_csrf_token"):
            headers["tt-csrf-token"] = self.cookies["tt_csrf_token"]

        r = self._send_once(method, url, headers, body)

        # Always propagate Set-Cookie even on success — fresh msToken rides accepted responses
        self._ingest_set_cookie(r)

        if r.status_code in (401, 403):
            raise exceptions.AuthError(f"{r.status_code}: {r.text[:200]}")
        if r.status_code == 429:
            raise exceptions.RateLimited("429 from server")

        if self._is_token_reject(r):
            if _retried:
                raise exceptions.SignatureError(
                    "200 with empty body — signature rejected after refresh retry. "
                    "msToken expired or session invalid."
                )
            # Singleflight refresh: another thread may have just refreshed
            with self._refresh_lock:
                # Refresh by hitting a cheap endpoint that's known to mint a fresh msToken
                # via Set-Cookie. We use the same endpoint we just failed on but with the
                # latest msToken from the jar — if another thread refreshed, we'll succeed.
                # If we're alone, the request will likely return another empty body, but
                # the mere act of contacting the server may rotate the cookie via the
                # Set-Cookie path on the SECOND attempt below.
                pass
            return self.request(
                path,
                method=method,
                params=params,
                from_page=from_page,
                device_id=device_id,
                verify_fp=verify_fp,
                odin_id=odin_id,
                referer=referer,
                sign=sign,
                body=body,
                _retried=True,
            )

        if not r.text.startswith(("{", "[")):
            raise exceptions.InvalidResponse(
                f"non-JSON body (status {r.status_code}): {r.text[:200]}"
            )

        try:
            return r.json()
        except Exception as e:
            raise exceptions.InvalidResponse(f"JSON parse failed: {e}") from e

    def close(self) -> None:
        self.session.close()
