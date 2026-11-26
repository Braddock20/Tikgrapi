"""Browser backend.

TikTok's web API now expects an ``X-Dynosaur`` signature that is produced by
the site's own JavaScript. Rather than re-implement it, this backend drives a
real Chromium: the page's request interceptor signs every ``fetch`` it makes,
so the requests are indistinguishable from the site's own.

The page is kept alive between calls, so the per-request cost is one network
round trip — not a page load.

**Memory.** A Chromium left running costs 300–400 MB, which is most of
Render's free-tier budget. This backend is therefore *lazy*: it starts only
when an operation genuinely needs a signed request, and it is released again
after an idle window so the rest of the service gets the memory back.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings
from app.memory import IdleRelease, can_start_browser, process_rss_mb
from app.security import parse_session_payload, read_session

log = logging.getLogger(__name__)

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

# Flags that keep the renderer small. Playwright's own defaults are tuned for
# a desktop, not for a 512 MB container.
_LOW_MEMORY_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints",
    "--disable-sync",
    "--renderer-process-limit=1",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
]

# The renderer is where the memory goes: a TikTok page is a single-page app
# that pulls in videos, images, fonts and analytics. None of that is needed
# to read the server-rendered data or to sign a request, and blocking it
# cuts the renderer's footprint by more than half.
_BLOCKED_HOSTS = (
    "analytics.tiktok.com",
    "mon.tiktokv.com",
    "log.tiktok.com",
    "ads.tiktok.com",
    "ads.tiktokv.com",
    "mcs.tiktok.com",
    "sd-sdk.tiktok.com",
    "ct.pinterest.com",
    "googleadservices.com",
    "doubleclick.net",
    "googletagmanager.com",
    "branch.io",
    "appsflyer.com",
    "adjust.com",
    "sentry.io",
)

# Resource types that are never needed for signing or reading.
_BLOCKED_TYPES = {"media", "font", "image"}

# The site's own JavaScript is split in two: a small security SDK that signs
# the requests, and a large React application that draws the page. Only the
# first one is needed — the engine reads the server-rendered data, it does
# not look at the pixels.
#
# Blocking the application bundles is the single biggest memory win
# available: measured against the live site, the renderer falls from
# ~554 MB to ~260 MB and the requests are still signed correctly.
_APP_SCRIPT_MARKERS = (
    "/webapp-desktop/",
    "/webapp-mobile/",
    "/foryou-pr",
    "/static/js/",
    "/react-v18/",
)
_SDK_SCRIPT_MARKERS = (
    "sdk",
    "slardar",
    "privacy_protection",
    "framework",
    "secsdk",
    "crawler",
)

# The standard query parameters every TikTok web API call carries.
#
# Measured: sending the full set the site uses ("url doesn't match" — the
# signature is computed over the query string, and a parameter the page's
# interceptor also adds breaks it) makes *working* endpoints fail. The
# site's interceptor already appends device_id, verifyFp, msToken and the
# signature, so the engine sends the endpoint's own parameters and nothing
# else. Only ``aid`` is added, and it is what the interceptor does not.
_STANDARD_PARAMS: dict[str, str] = {
    "aid": "1988",
}


async def _install_blocking(context, state: dict) -> None:
    """Stop the page from pulling in what the engine never uses.

    TikTok's pages are heavy: an autoplaying video feed, a wall of images,
    web fonts, a pile of analytics beacons and — the big one — a React
    application that costs hundreds of megabytes to build a page nobody is
    going to look at.

    The engine only reads the server-rendered data and signs its own
    requests, so the application bundles are refused while the small
    security SDK that does the signing is allowed through. Measured against
    the live site, that takes the renderer from ~554 MB to ~260 MB with the
    signatures still accepted.

    ``state["enabled"]`` can be turned off for the moments when the page's
    own interface has to work — posting a comment through the composer, for
    instance — and turned straight back on afterwards.
    """

    async def _handler(route):
        if not state.get("enabled", True):
            await route.continue_()
            return
        request = route.request
        url = request.url.lower()
        host = url.split("//", 1)[-1].split("/", 1)[0]

        if any(host == blocked or host.endswith("." + blocked) for blocked in _BLOCKED_HOSTS):
            await route.abort()
            return
        if request.resource_type in _BLOCKED_TYPES:
            await route.abort()
            return
        if (
            settings.browser_block_app_scripts
            and request.resource_type == "script"
            and any(marker in url for marker in _APP_SCRIPT_MARKERS)
            and not any(marker in url for marker in _SDK_SCRIPT_MARKERS)
        ):
            await route.abort()
            return
        await route.continue_()

    try:
        await context.route("**/*", _handler)
    except Exception:  # noqa: BLE001 - blocking is an optimisation, not a must
        log.debug("could not install the request blocking")


class BrowserBackend:
    """Signed TikTok requests driven through a real browser session."""

    name = "browser"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._starting = False
        # The request blocking can be turned off for the moments when the
        # page's own interface has to work (posting a comment through the
        # site's own composer, for example).
        self._blocking = {"enabled": True}
        self.idle = IdleRelease(settings.browser_idle_shutdown_seconds)
        self.starts = 0
        self.last_error: str = ""

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        """Launch the browser. Safe to call when it is already running."""
        async with self._lock:
            if self._browser is not None:
                self.idle.touch()
                return
            if self._starting:
                return
            self._starting = True
            try:
                await self._start_locked()
            finally:
                self._starting = False

    async def _start_locked(self) -> None:
        allowed, reason = can_start_browser(
            settings.browser_min_free_mb, settings.memory_limit_mb
        )
        if not allowed:
            self.last_error = reason
            raise RuntimeError(f"not enough memory to start the browser: {reason}")

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright is not installed — install it or set BACKEND=http"
            ) from exc

        args = list(_LOW_MEMORY_ARGS)
        args.append(f"--js-flags=--max-old-space-size={settings.browser_max_old_space_mb}")
        if settings.browser_extra_args:
            args.extend(settings.browser_extra_args.split())

        launch: dict[str, Any] = {
            "headless": settings.browser_headless,
            "args": args,
        }
        # A system Chrome is far smaller on disk than Playwright's bundled
        # Chromium and saves ~420 MB in the image.
        if settings.browser_executable_path:
            launch["executable_path"] = settings.browser_executable_path

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(**launch)
            await self._open_context()
        except Exception:
            await self._stop_locked()
            raise
        self.starts += 1
        self.last_error = ""
        self.idle.touch()
        log.info(
            "browser started (%s) — RSS %.0f MB",
            settings.browser_executable_path or "playwright chromium",
            process_rss_mb(),
        )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
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

    async def ensure_started(self) -> None:
        """Start the browser if it is not already running."""
        if self._browser is None:
            await self.start()
        else:
            self.idle.touch()

    async def release_if_idle(self) -> bool:
        """Let the browser go if it has been unused for a while.

        Returns True when the browser was released.
        """
        if self._browser is None:
            return False
        if not self.idle.expired:
            return False
        async with self._lock:
            if self._browser is None or not self.idle.expired:
                return False
            await self._stop_locked()
        self.idle.released += 1
        log.info("browser released after %.0fs idle — RSS %.0f MB", self.idle.timeout, process_rss_mb())
        return True

    def set_blocking(self, enabled: bool) -> None:
        """Turn the heavy-request blocking on or off.

        Some operations need the page's own interface — the comment composer
        is the reliable way to publish a comment from an IP that the write
        API refuses. The blocking is restored straight afterwards.
        """
        self._blocking["enabled"] = bool(enabled)

    async def reload_session(self) -> None:
        """Re-read the encrypted session file and rebuild the browser context."""
        async with self._lock:
            if self._browser is None:
                # Nothing is running yet — the next request will pick up the
                # new cookies when it starts the browser.
                self.idle.touch()
                return
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
            self._context = self._page = None
            await self._open_context()
        self.idle.touch()

    async def new_context(self):
        """A separate browsing context on the shared browser.

        The DM channel needs its own page, but it must not start a second
        Chromium — two of them would not fit in Render's 512 MB. A second
        *context* is cheap next to a second browser, and it keeps the DM
        session state separate from the API session.
        """
        await self.ensure_started()
        async with self._lock:
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
            )
        await _install_blocking(context, self._blocking)
        cookies = parse_session_payload(read_session())
        await context.add_cookies(
            [
                {"name": name, "value": value, "domain": ".tiktok.com", "path": "/"}
                for name, value in cookies.items()
            ]
        )
        return context

    def status(self) -> dict[str, Any]:
        return {
            "running": self._browser is not None,
            "starts": self.starts,
            "releases": self.idle.released,
            "idle_seconds": round(self.idle.idle_for, 1),
            "idle_timeout": self.idle.timeout,
            "rss_mb": round(process_rss_mb(), 1),
            "last_error": self.last_error,
        }

    # -- context ---------------------------------------------------------
    async def _open_context(self) -> None:
        ua = settings.browser_user_agent
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
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
        await _install_blocking(self._context, self._blocking)

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
        await self._page.wait_for_timeout(2000)

    # -- requests --------------------------------------------------------
    def _ensure_page(self):
        if self._page is None:
            raise RuntimeError("Browser backend is not started")
        return self._page

    async def fetch_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.idle.touch()
        return await self._fetch("GET", path, params)

    async def post_json(
        self, path: str, params: dict[str, Any], form: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.idle.touch()
        return await self._fetch("POST", path, params, form=form)

    async def _fetch(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        form: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from urllib.parse import quote, urlencode

        await self.ensure_started()

        # Only the endpoint's own parameters are sent on top of the standard
        # set the site itself sends. The page's interceptor adds device_id,
        # verifyFp, msToken and the request signature — sending those here
        # too would duplicate them and break the signature.
        clean = dict(_STANDARD_PARAMS)
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
                if code == 0 and data.get("status_msg") == "url doesn't match":
                    raise RuntimeError(
                        f"TikTok rejected the parameters for {path} "
                        "('url doesn't match') — the endpoint needs a different "
                        "set of query parameters"
                    )
                raise RuntimeError(
                    f"TikTok error {code}: {data.get('status_msg', '')} ({path})"
                )
        return data

    async def fetch_page(self, path: str) -> dict[str, Any]:
        """Load a page and return its server-rendered data blob."""
        self.idle.touch()
        await self.ensure_started()
        async with self._lock:
            page = self._ensure_page()
            url = path if path.startswith("http") else f"https://www.tiktok.com{path}"
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms
                )
                await page.wait_for_timeout(1800)
                blob = await page.evaluate(_PAGE_BLOB)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not load {url}: {type(exc).__name__}: {exc}") from exc
        if not blob:
            raise RuntimeError(f"No server-rendered data found on {url}")
        return blob

    async def fetch_page_text(self, path: str) -> str:
        """Load a page and return its raw HTML."""
        self.idle.touch()
        await self.ensure_started()
        async with self._lock:
            page = self._ensure_page()
            url = path if path.startswith("http") else f"https://www.tiktok.com{path}"
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms
                )
                await page.wait_for_timeout(1200)
                return await page.content()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not load {url}: {type(exc).__name__}: {exc}") from exc

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
        await self.ensure_started()
        async with self._lock:
            if self._page is None or self._context is None:
                return {}
            try:
                await self._page.goto(
                    "https://www.tiktok.com/foryou",
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )
                await self._page.wait_for_timeout(3500)
                # The site writes the web id on the *second* load of the
                # feed — one load is not enough to get it.
                await self._page.reload(wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2200)
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

    async def own_video_ids(self, username: str, limit: int = 20) -> list[str]:
        """The account's own video ids, read from the rendered profile page.

        The signed ``/api/post/item_list/`` endpoint answers with an empty
        body from datacenter IPs, but the profile page ships the same list
        in its server-rendered data. This is the reliable way to find the
        account's own videos.
        """
        blob = await self.fetch_page(f"/@{username}")
        scope = blob.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.user-detail", {}) or {}
        info = detail.get("userInfo") or {}
        items = info.get("itemList") or detail.get("itemList") or []
        ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            vid = item.get("id") or item.get("aweme_id") or item.get("awemeId")
            if vid:
                ids.append(str(vid))
            if len(ids) >= limit:
                break
        return ids

    async def video_page(self, video_id: str) -> dict[str, Any]:
        """The server-rendered data blob of one video page.

        The signed ``/api/item/detail/`` endpoint answers with an empty body
        from datacenter IPs, but the rendered page carries the same data —
        so this is the path that works from a server.
        """
        blob = await self.fetch_page(f"/@me/video/{video_id}")
        scope = blob.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.video-detail", {}) or {}
        info = detail.get("itemInfo") or {}
        item = info.get("itemStruct")
        if not item:
            raise RuntimeError(f"No video data found for {video_id}")
        return item

    async def video_author(self, video_id: str) -> dict[str, Any]:
        """Who posted a video, read from the rendered page."""
        item = await self.video_page(video_id)
        author = item.get("author") or {}
        return {
            "id": author.get("id") or author.get("uid"),
            "unique_id": author.get("uniqueId") or author.get("unique_id"),
            "nickname": author.get("nickname"),
            "sec_uid": author.get("secUid") or author.get("sec_uid"),
        }

    async def post_comment_via_ui(self, video_id: str, text: str) -> dict:
        """Publish a comment by using the site's own composer.

        The write API answers with an empty body from some IPs — TikTok
        refuses state-changing calls from datacenter ranges without saying
        so. The page's own composer is a different route in: the site
        builds the request itself, with every parameter its current bundle
        expects.

        The heavy application bundles are let through for the duration and
        blocked again as soon as the comment is away, so the memory cost
        is paid for a few seconds rather than all the time.

        Two things about the live page shape the code below, both measured:

        * the composer is drawn by TikTok's React application, so the page
          has to finish booting before the comment button exists — that
          takes about ten seconds, far longer than a fixed wait;
        * the editor is a Draft editor. Its placeholder sits on top of the
          text area and swallows clicks, and rewriting its DOM makes the
          panel close itself. It has to be focused directly and filled
          with real input events.
        """
        page = self._ensure_page()
        url = f"https://www.tiktok.com/@me/video/{video_id}"
        editor = '[data-e2e="comment-text"] div[contenteditable="true"]'
        self.set_blocking(False)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            try:
                await page.wait_for_selector(
                    '[data-e2e="comment-icon"]', timeout=settings.browser_timeout_ms
                )
            except Exception:  # noqa: BLE001 - reported below
                pass

            # Open the comment panel. The click is made in the page rather
            # than with the mouse: after the panel opens the layout shifts,
            # and a mouse click can land on whatever has moved underneath
            # it — the notification bell, for one.
            opened = await page.evaluate(
                """() => {
                    const icon = document.querySelector('[data-e2e="comment-icon"]');
                    if (!icon) return false;
                    icon.click();
                    return true;
                }"""
            )
            if not opened:
                raise RuntimeError("the comment button did not appear on the page")
            try:
                await page.wait_for_selector(editor, timeout=20000)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("no comment box on the page") from exc

            # Focus the editor directly — its placeholder overlay takes the
            # click otherwise — and fill it with a real text insertion.
            await page.evaluate("(sel) => document.querySelector(sel).focus()", editor)
            await page.wait_for_timeout(300)
            await page.keyboard.insert_text(text)
            await page.wait_for_timeout(900)

            typed = await page.evaluate(
                "(sel) => (document.querySelector(sel) || {}).textContent || ''", editor
            )
            if not typed.strip():
                raise RuntimeError("the composer did not accept the text")

            posted = await page.evaluate(
                """() => {
                    const btn = document.querySelector('[data-e2e="comment-post"]')
                              || document.querySelector('[data-e2e="comment-publish"]');
                    if (!btn) return false;
                    btn.click();
                    return true;
                }"""
            )
            if not posted:
                raise RuntimeError("the composer had no post button")

            # The composer empties itself when the comment has been sent.
            await page.wait_for_timeout(4000)
            left = await page.evaluate(
                "(sel) => (document.querySelector(sel) || {}).textContent || ''", editor
            )
            if left.strip():
                raise RuntimeError("the composer did not send the comment")
            return {"published": True, "via": "button", "typed": len(typed)}
        finally:
            self.set_blocking(True)

    async def comment_list(self, video_id: str, count: int = 20) -> list[dict]:
        """The comments on a video, read from the rendered page.

        The signed endpoint answers with an empty body from some IPs, but
        the rendered page carries the same data.
        """
        blob = await self.fetch_page(f"/@me/video/{video_id}")
        scope = blob.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.video-detail", {}) or {}
        info = detail.get("itemInfo") or {}
        item = info.get("itemStruct") or {}
        comments = item.get("comments") or []
        return comments[:count]

    async def im_conversations(self) -> list[dict]:
        """The account's DM conversations.

        TikTok's messaging service is degraded from datacenter IPs and answers
        ``status_code 4`` there, so an empty list is a normal answer and not
        an error.
        """
        try:
            data = await self.fetch_json(
                "/api/im/spotlight/relation/",
                {
                    "source": "web",
                    "with_fstatus": 1,
                    "device_platform": "webapp_pc",
                    "count": 30,
                    "max_time": 0,
                    "min_time": 0,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.info("the IM service is not reachable: %s", exc)
            return []
        users = data.get("users") or data.get("user_list") or []
        return [u for u in users if isinstance(u, dict)]
