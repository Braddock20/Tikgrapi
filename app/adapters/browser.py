"""Browser backend.

TikTok's web API now expects an ``X-Dynosaur`` signature that is produced by
the site's own JavaScript. Rather than re-implement it, this backend drives a
real Chromium: the page's request interceptor signs every ``fetch`` it makes,
so the requests are indistinguishable from the site's own.

The page is kept alive between calls, so the per-request cost is one network
round trip — not a page load.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config import settings
from app.security import parse_session_payload, read_session

# The site's interceptor signs requests it issues. Fetching from inside the
# page keeps us on that path.
_PAGE_FETCH = """
async ([url, method, body, csrf]) => {
    const opts = {method, credentials: 'include', headers: {'Accept': '*/*'}};
    if (body) { opts.body = body; opts.headers['Content-Type'] = 'application/x-www-form-urlencoded'; }
    if (csrf && method === 'POST') { opts.headers['tt-csrf-token'] = csrf; }
    const r = await fetch(url, opts);
    const t = await r.text();
    return {status: r.status, text: t};
}
"""

_PAGE_BLOB = """
() => {
    const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
}
"""


class BrowserBackend:
    """Signed TikTok requests driven through a real browser session."""

    name = "browser"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright is not installed — install it or set BACKEND=http"
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--lang=en-US",
            ],
        )
        await self._open_context()

    async def _open_context(self) -> None:
        ua = settings.browser_user_agent
        self._context = await self._browser.new_context(
            viewport={"width": 1680, "height": 1050},
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=ua,
        )
        cookies = parse_session_payload(read_session())
        await self._context.add_cookies(
            [
                {"name": name, "value": value, "domain": ".tiktok.com", "path": "/"}
                for name, value in cookies.items()
            ]
        )
        self._page = await self._context.new_page()

        # A plain headless Chromium advertises "HeadlessChrome" in sec-ch-ua,
        # which TikTok treats as automation. Override the client hints so they
        # match the user agent we present.
        try:
            cdp = await self._context.new_cdp_session(self._page)
            await cdp.send(
                "Network.setUserAgentOverride",
                {
                    "userAgent": ua,
                    "acceptLanguage": "en-US,en;q=0.9",
                    "platform": "Linux x86_64",
                    "userAgentMetadata": {
                        "brands": [
                            {"brand": "Not_A Brand", "version": "8"},
                            {"brand": "Chromium", "version": "159"},
                            {"brand": "Google Chrome", "version": "159"},
                        ],
                        "fullVersion": "159.0.0.0",
                        "platform": "Linux",
                        "platformVersion": "6.1.0",
                        "architecture": "x86",
                        "model": "",
                        "mobile": False,
                        "bitness": "64",
                        "wow64": False,
                    },
                },
            )
        except Exception:  # noqa: BLE001 - client hints are best-effort
            pass

        await self._page.goto(
            "https://www.tiktok.com/",
            wait_until="domcontentloaded",
            timeout=settings.browser_timeout_ms,
        )
        await self._page.wait_for_timeout(2500)

    async def stop(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001
                    pass
        self._context = self._browser = self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
        self._playwright = None

    async def reload_session(self) -> None:
        """Re-read the encrypted session file and rebuild the browser context."""
        async with self._lock:
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
            self._context = self._page = None
            await self._open_context()

    # -- requests --------------------------------------------------------
    def _ensure_page(self):
        if self._page is None:
            raise RuntimeError("Browser backend is not started")
        return self._page

    async def fetch_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._fetch("GET", path, params)

    async def post_json(
        self, path: str, params: dict[str, Any], form: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._fetch("POST", path, params, form=form)

    async def _fetch(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        form: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from urllib.parse import quote, urlencode

        # Only the endpoint's own parameters are sent. The page's interceptor
        # adds device_id, verifyFp, msToken and the request signature itself —
        # sending them here too would duplicate them and break the signature.
        clean = {"aid": "1988"}
        clean.update({k: v for k, v in params.items() if v is not None})
        url = f"https://www.tiktok.com{path}?" + urlencode(clean, doseq=True, quote_via=quote)
        body = None
        if form:
            body = urlencode({k: v for k, v in form.items() if v is not None}, doseq=True)

        # TikTok requires the csrf token header on every state-changing call.
        csrf = ""
        if method == "POST":
            try:
                csrf = parse_session_payload(read_session()).get("tt_csrf_token", "")
            except Exception:  # noqa: BLE001
                csrf = ""

        async with self._lock:
            page = self._ensure_page()
            try:
                out = await page.evaluate(_PAGE_FETCH, [url, method, body, csrf])
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Browser request failed: {type(exc).__name__}: {exc}") from exc

        status = out.get("status", 0)
        text = out.get("text") or ""
        if status >= 400:
            raise RuntimeError(f"TikTok returned HTTP {status} for {path}: {text[:200]}")
        if not text:
            raise RuntimeError(
                f"TikTok returned an empty body for {path} — the signed request "
                "was rejected (rate limit or blocked endpoint)"
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"TikTok returned a non-JSON body for {path}: {text[:160]}") from exc
        if isinstance(data, dict):
            code = data.get("status_code")
            if code not in (None, 0):
                raise RuntimeError(
                    f"TikTok error {code}: {data.get('status_msg', '')} ({path})"
                )
        return data

    async def fetch_page(self, path: str) -> dict[str, Any]:
        """Load a page and return its server-rendered data blob."""
        async with self._lock:
            page = self._ensure_page()
            url = path if path.startswith("http") else f"https://www.tiktok.com{path}"
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms
                )
                await page.wait_for_timeout(2000)
                blob = await page.evaluate(_PAGE_BLOB)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not load {url}: {type(exc).__name__}: {exc}") from exc
        if not blob:
            raise RuntimeError(f"No server-rendered data found on {url}")
        return blob

    async def harvest_cookies(self) -> dict[str, str]:
        """The cookies the site currently holds.

        The page mints a fresh msToken on every visit — harvesting the jar
        after a visit is how the volatile half of a session gets renewed.
        """
        async with self._lock:
            if self._context is None:
                return {}
            jar = await self._context.cookies()
        return {c["name"]: c["value"] for c in jar if c.get("value")}

    async def renew_tokens(self) -> dict[str, str]:
        """Visit a real page so the site mints a full set of device tokens.

        The light homepage only hands out the server-side tokens; a full page
        (the feed) is what makes the site mint the msToken, the odin_tt and
        the s_v_web_id as well. Those are what the upload pipeline signs
        with, so the feed is worth the extra seconds.
        """
        async with self._lock:
            if self._page is None or self._context is None:
                return {}
            try:
                await self._page.goto(
                    "https://www.tiktok.com/foryou",
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )
                await self._page.wait_for_timeout(4000)
                # The site writes the web id on the *second* load of the
                # feed — one load is not enough to get it.
                await self._page.reload(wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001 - best effort
                pass
            jar = await self._context.cookies()
        return {c["name"]: c["value"] for c in jar if c.get("value")}

    async def account_context(self) -> dict[str, Any]:
        """The signed-in account as described by the app context."""
        blob = await self.fetch_page("/foryou")
        scope = blob.get("__DEFAULT_SCOPE__", {})
        return scope.get("webapp.app-context", {}).get("user", {}) or {}

    async def user_detail(self, username: str) -> dict[str, Any]:
        """Profile + stats for a username, read from the rendered profile page."""
        blob = await self.fetch_page(f"/@{username}")
        scope = blob.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.user-detail", {}) or {}
        info = detail.get("userInfo")
        if not info:
            raise RuntimeError(f"No profile data found for @{username}")
        return info
