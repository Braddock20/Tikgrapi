"""Programmatic login via Playwright + auto solvers.

Pipeline (in order):
  1. Launch Chromium
  2. Navigate /login/phone-or-email/email
  3. Click "Allow all" cookie banner
  4. Fill credentials, click Log in
  5. Loop watching for: error toast, captcha modal, OTP screen, success URL
  6. On error toast → raise typed LoginError subclass
  7. On captcha → solve via solver, click submit
  8. On OTP → poll IMAP, paste 6-digit code
  9. On /foryou or /following redirect → harvest cookies → out_path

Optional dependency: playwright (pip install 'tiktokrapi[browser]')
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional, Union

from ..exceptions import (
    AccountLocked,
    CaptchaSolverError,
    LoginError,
    LoginRateLimited,
    OTPError,
    WrongCredentials,
)
from .captcha import CaptchaSolver, NoOpSolver
from .imap_otp import IMAPOTPPoller


# Map TikTok's surface messages → typed exceptions
ERROR_MAP = [
    ("maximum number of attempts", LoginRateLimited),
    ("too many attempts", LoginRateLimited),
    ("internal server error", LoginRateLimited),  # bot detection masquerading as 500
    ("incorrect password", WrongCredentials),
    ("password is incorrect", WrongCredentials),
    ("user does not exist", WrongCredentials),
    ("account doesn't exist", WrongCredentials),
    ("account suspended", AccountLocked),
    ("under review", AccountLocked),
    ("temporarily unavailable", AccountLocked),
    ("try again later", LoginRateLimited),
]


async def _scrape_error(page) -> Optional[str]:
    """Return any visible error/toast text on the login page."""
    selectors = [
        "[class*='error']",
        "[class*='Error']",
        "[class*='toast']",
        "[class*='Toast']",
        "[role='alert']",
        ".tux-toast",
        "p.error-text",
        "div.tiktok-input-error",
    ]
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) > 3 and len(txt) < 500:
                    return txt
        except Exception:
            continue
    return None


def _classify_error(text: str) -> Optional[type]:
    lower = text.lower()
    for needle, exc_cls in ERROR_MAP:
        if needle in lower:
            return exc_cls
    return None


async def _async_login(
    *,
    username: str,
    password: str,
    out_path: Path,
    captcha_solver: CaptchaSolver,
    otp_poller: Optional[IMAPOTPPoller],
    headless: bool,
    timeout_ms: int,
    verbose: bool,
    proxy: Optional[dict] = None,
) -> dict[str, str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise ImportError(
            "playwright not installed. pip install 'tiktokrapi[browser]'"
        ) from e

    def log(msg: str) -> None:
        if verbose:
            print(f"[login] {msg}", flush=True)

    async with async_playwright() as pw:
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if proxy:
            launch_kwargs["proxy"] = proxy
            log(f"using proxy: {proxy.get('server')}")
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
        )
        page = await context.new_page()

        # Pipe console errors to terminal so user sees what TikTok JS complains about
        page.on("console", lambda msg: log(f"[browser console] {msg.type}: {msg.text[:200]}") if msg.type in ("error", "warning") else None)
        page.on("pageerror", lambda exc: log(f"[browser pageerror] {exc}"))
        page.on("response", lambda resp: log(f"[browser xhr] {resp.status} {resp.url[:120]}") if "/api/" in resp.url or "/passport/" in resp.url else None)

        login_started_ts = time.time()
        log(f"navigating to login page...")
        await page.goto(
            "https://www.tiktok.com/login/phone-or-email/email",
            wait_until="networkidle",
            timeout=60000,
        )
        log("page reached networkidle")

        # Wait for bundle to finish initializing (msToken + ttwid cookies set + signer loaded)
        log("waiting 8s for webmssdk init...")
        await asyncio.sleep(8)

        # Cookie banner
        try:
            await page.click("button:has-text('Allow all')", timeout=5000)
            log("clicked 'Allow all' cookies")
            await asyncio.sleep(2)
        except Exception:
            log("no cookie banner")

        # Wait until inputs actually exist + are interactive
        log("waiting for username input...")
        await page.wait_for_selector("input[name='username']", state="visible", timeout=30000)
        await asyncio.sleep(2)

        log(f"filling username={username}")
        # Type slowly to mimic human entry (defeats some bot detectors)
        await page.click("input[name='username']")
        await asyncio.sleep(0.5)
        await page.type("input[name='username']", username, delay=80)
        await asyncio.sleep(1)
        await page.click("input[type='password']")
        await asyncio.sleep(0.5)
        await page.type("input[type='password']", password, delay=80)
        await asyncio.sleep(2)

        # Wait for submit button to be enabled (TikTok disables it until inputs validated)
        log("waiting for Log in button to be enabled...")
        try:
            await page.wait_for_selector("button[type='submit']:not([disabled])", timeout=10000)
        except Exception:
            log("submit button never enabled — may still try")
        await asyncio.sleep(1)
        await page.click("button[type='submit']")
        log("clicked Log in button")

        deadline = time.time() + timeout_ms / 1000
        last_err = None
        while time.time() < deadline:
            await asyncio.sleep(1)
            try:
                url = page.url
            except Exception:
                break

            # Success?
            if "/foryou" in url or "/following" in url or url.endswith("tiktok.com/"):
                log(f"success! redirected to {url}")
                break

            # Error message visible?
            try:
                err_text = await _scrape_error(page)
            except Exception:
                break
            if err_text and err_text != last_err:
                log(f"detected error: {err_text!r}")
                exc_cls = _classify_error(err_text)
                if exc_cls:
                    await browser.close()
                    raise exc_cls(
                        f"login error: {err_text}",
                        code=exc_cls.__name__,
                        surface_message=err_text,
                    )
                last_err = err_text

            # Captcha modal
            captcha = await page.query_selector(".captcha-verify-container, [class*='captcha-verify']")
            if captcha:
                visible = await captcha.is_visible()
                if visible:
                    log("captcha modal detected — solving...")
                    try:
                        await _solve_captcha(page, captcha, captcha_solver, log)
                    except Exception as e:
                        await browser.close()
                        raise CaptchaSolverError(
                            f"captcha solver failed: {e}",
                            code="CaptchaSolverFailed",
                            surface_message=str(e),
                        )
                    continue

            # OTP screen
            otp_input = await page.query_selector("input[placeholder*='6-digit']")
            if otp_input:
                visible = await otp_input.is_visible()
                if visible:
                    if not otp_poller:
                        await browser.close()
                        raise OTPError(
                            "email OTP screen shown but no IMAP poller configured",
                            code="OTPNotConfigured",
                        )
                    log("OTP screen — polling inbox...")
                    try:
                        code = otp_poller.wait_for_code(
                            timeout=120, since_ts=login_started_ts
                        )
                    except Exception as e:
                        await browser.close()
                        raise OTPError(
                            f"OTP poll failed: {e}",
                            code="OTPPollFailed",
                            surface_message=str(e),
                        )
                    log(f"got OTP code, entering...")
                    await otp_input.fill(code)
                    await page.click("button:has-text('Next')")
                    continue

        if "/foryou" not in page.url and "/following" not in page.url and page.url != "https://www.tiktok.com/":
            err_text = await _scrape_error(page) or "unknown — no error toast found"
            await browser.close()
            raise LoginError(
                f"login did not reach FYP within {timeout_ms}ms. ended at {page.url}. "
                f"surface message: {err_text}",
                surface_message=err_text,
            )

        cookies_raw = await context.cookies()
        out = {c["name"]: c["value"] for c in cookies_raw}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(cookies_raw, indent=2))
        log(f"saved {len(cookies_raw)} cookies to {out_path}")

        await browser.close()
        return out


async def _solve_captcha(page, captcha_el, solver: CaptchaSolver, log) -> None:
    """Detect captcha type, solve, replay clicks/drag in browser."""
    drag = await captcha_el.query_selector(
        ".secsdk-captcha-drag-icon, [class*='drag']"
    )
    if drag:
        log("captcha type: slider/puzzle")
        bg_el = await captcha_el.query_selector("img[class*='captcha_verify_img_slide']")
        piece_el = await captcha_el.query_selector(
            "img[class*='captcha_verify_img_slide_piece']"
        )
        if not (bg_el and piece_el):
            raise RuntimeError("slider images not found")
        bg_bytes = await bg_el.screenshot()
        piece_bytes = await piece_el.screenshot()
        from .slider import solve_slider_local
        x_offset = solve_slider_local(bg_bytes, piece_bytes)
        log(f"computed slider offset: {x_offset}px")
        box = await drag.bounding_box()
        if not box:
            raise RuntimeError("drag handle not visible")
        sx = box["x"] + box["width"] / 2
        sy = box["y"] + box["height"] / 2
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        steps = 30
        for i in range(1, steps + 1):
            await page.mouse.move(sx + (x_offset * i / steps), sy, steps=2)
        await page.mouse.up()
        await asyncio.sleep(2)
        return

    grid_img = await captcha_el.query_selector("img[class*='verify_img']")
    if grid_img:
        log("captcha type: shape/icon grid")
        img_bytes = await grid_img.screenshot()
        coords = solver.solve_image_to_coords(
            img_bytes, prompt="click the matching shapes in order"
        )
        log(f"got {len(coords)} click coordinates from solver")
        box = await grid_img.bounding_box()
        if not box:
            raise RuntimeError("grid not visible")
        for cx, cy in coords:
            await page.mouse.click(box["x"] + cx, box["y"] + cy)
            await asyncio.sleep(0.5)
        await page.click(
            ".verify-captcha-submit-button, button:has-text('Submit')"
        )
        await asyncio.sleep(2)
        return

    raise RuntimeError("unknown captcha layout — neither slider nor image grid")


def login(
    *,
    username: str,
    password: str,
    out_path: str | Path = "session.json",
    captcha_solver: Optional[CaptchaSolver] = None,
    otp_poller: Optional[IMAPOTPPoller] = None,
    headless: bool = False,
    timeout_ms: int = 180_000,
    verbose: bool = True,
    proxy: Optional[Union[str, dict]] = None,
) -> dict[str, str]:
    """Programmatic login. Returns cookie dict, raises typed LoginError on failure.

    Args:
        username: email / phone / handle
        password: account password
        out_path: where to save session.json
        captcha_solver: solver for shape/icon captcha (None → fail on captcha)
        otp_poller: IMAPOTPPoller for email OTP (None → fail on OTP screen)
        headless: True for invisible browser. Set False to debug.
        timeout_ms: total budget for whole flow
        verbose: print step-by-step progress to stdout

    Raises:
        LoginRateLimited: TikTok said "Maximum number of attempts"
        WrongCredentials: bad username/password
        AccountLocked: suspended/under review
        CaptchaSolverError: captcha hit but no solver / solver failed
        OTPError: OTP screen but no poller / poll failed
        LoginError: anything else
    """
    # Normalize proxy: accept "user:pass@host:port" string OR Playwright dict
    proxy_dict: Optional[dict] = None
    if proxy:
        if isinstance(proxy, dict):
            proxy_dict = proxy
        else:
            # parse "host:port" or "user:pass@host:port" or "http://..."
            s = proxy
            if "://" in s:
                scheme, _, rest = s.partition("://")
            else:
                scheme, rest = "http", s
            user_pass, sep, host_port = rest.rpartition("@")
            proxy_dict = {"server": f"{scheme}://{host_port}"}
            if sep:
                u, _, p = user_pass.partition(":")
                proxy_dict["username"] = u
                proxy_dict["password"] = p

    return asyncio.run(
        _async_login(
            username=username,
            password=password,
            out_path=Path(out_path),
            captcha_solver=captcha_solver or NoOpSolver(),
            otp_poller=otp_poller,
            headless=headless,
            timeout_ms=timeout_ms,
            verbose=verbose,
            proxy=proxy_dict,
        )
    )
