"""Keeping the session alive.

A TikTok web session is not one credential, it is several:

* **Durable** — the account binding: ``sessionid`` and the edge cookie
  ``tt-target-idc``. These survive for months and only die when the account
  is logged out or the password changes.
* **Volatile** — the device tokens: ``msToken``, ``ttwid``,
  ``tt_csrf_token`` and friends. These expire in hours and are re-minted by
  the site on every visit.

So the durable core is the anchor and the volatile half can be refreshed
without the account. This module re-mints the volatile half on a schedule
and re-heals the session when a call comes back unauthenticated — which is
what keeps a bot attached to an account for as long as the user does not
log out of it themselves.

The permanent copy of the durable half lives in the ``TIKTOK_SESSION``
environment variable: it survives restarts and redeploys, and it is the
fallback the keeper falls back to if the live session ever dies.

Renewal can only ever *add or refresh* cookies; it never drops one, so a
renewal can never lose the account binding.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from app.config import settings
from app.security import (
    SessionError,
    extract_sessionid,
    hash_sessionid,
    mask_sessionid,
    parse_session_payload,
    read_session,
    store_session,
)

log = logging.getLogger(__name__)

# Cookies that must survive a renewal — dropping any of these loses the
# account binding.
DURABLE_COOKIES = frozenset(
    {
        "sessionid",
        "sessionid_ss",
        "odin_tt",
        "s_v_web_id",
        "uid_tt",
        "uid_tt_ss",
        "sid_tt",
        "multi_sids",
        "sid_guard",
        "tt-target-idc",
        "tt-target-idc-sign",
        "store-idc",
        "store-country-sign",
        "store-country-code",
        "store-country-code-src",
    }
)

# The tokens the site hands out per visit; these are what a renewal replaces.
VOLATILE_COOKIES = frozenset(
    {
        "msToken",
        "ttwid",
        "tt_csrf_token",
        "tt_chain_token",
        "cmpl_token",
        "passport_auth_status",
        "passport_auth_status_ss",
        "passport-sotl-auth-token-nonce_G1gX1cW1_6_FQc1CfwtzBmui6ac1Ix-DgGnGz2M5jmY",
        "passport-sotl-auth-token-nonce_KPEIvyjQmMFqJFMmYKTv-DMD6nDh8UwEH09LQG9Go3Q",
        "tt_session_tlb_tag",
        "sid_ucp_v1",
        "ssid_ucp_v1",
        "last_login_method",
        "perf_feed_cache",
    }
)


class SessionKeeper:
    """Renews the volatile half of a session and heals it when it dies."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.last_renewal: datetime | None = None
        self.renewals = 0
        self.heals = 0
        self.reseeds = 0
        # Whether the account was last seen signed in (None = unknown).
        self.attached: bool | None = None
        self.last_error: str | None = None
        self.sessionid_rotated = False

    # -- helpers ---------------------------------------------------------
    def current_cookies(self) -> dict[str, str]:
        return parse_session_payload(read_session())

    def durable(self) -> dict[str, str]:
        return {k: v for k, v in self.current_cookies().items() if k in DURABLE_COOKIES}

    def stale(self) -> bool:
        """True when the session is missing or the msToken looks old."""
        try:
            cookies = self.current_cookies()
        except SessionError:
            return True
        if "sessionid" not in cookies and "sessionid_ss" not in cookies:
            return True
        if self.last_renewal is None:
            return True
        age = (datetime.now(timezone.utc) - self.last_renewal).total_seconds()
        return age > settings.session_renew_hours * 3600

    # -- renewal ---------------------------------------------------------
    async def _harvest(self) -> dict[str, str]:
        """Ask the site for a fresh set of device tokens."""
        backend = getattr(self.engine, "_browser", None)
        if backend is not None:
            renew = getattr(backend, "renew_tokens", None)
            if renew is not None:
                return await renew()
            return await backend.harvest_cookies()

        http = getattr(self.engine, "_http", None)
        if http is not None:
            return await http.renew_session()

        raise RuntimeError(
            "renewal needs a running backend — the site mints its device "
            "tokens in JavaScript (BACKEND=browser) or on a page visit "
            "(BACKEND=http)"
        )

    async def renew(self, force: bool = False) -> dict:
        """Re-mint the volatile cookies by visiting the site.

        Uses the browser when there is one because the site's own JavaScript
        is what mints the device tokens; the plain-HTTP backend falls back to
        a page visit.
        """
        before = self.current_cookies()
        harvested = await self._harvest()

        merged = dict(before)
        changed = []
        for name, value in harvested.items():
            if merged.get(name) != value:
                changed.append(name)
                merged[name] = value

        rotated = False
        new_sid = harvested.get("sessionid") or harvested.get("sessionid_ss")
        old_sid = before.get("sessionid") or before.get("sessionid_ss")
        if new_sid and old_sid and new_sid != old_sid:
            # The site rotated the session id — keep the new one (it is the
            # one that works) and tell the operator to update the env var.
            rotated = True
            self.sessionid_rotated = True
            log.warning(
                "the site rotated the sessionid (%s -> %s) — update "
                "TIKTOK_SESSION to the new value",
                mask_sessionid(old_sid),
                mask_sessionid(new_sid),
            )

        if changed:
            store_session(
                json.dumps(
                    [
                        {"name": k, "value": v, "domain": ".tiktok.com", "path": "/"}
                        for k, v in merged.items()
                    ],
                    indent=2,
                ).encode()
            )

        # push the refreshed cookies back into the live clients
        try:
            await self.engine.reload_session()
        except Exception:  # noqa: BLE001
            log.exception("could not reload the clients after renewal")

        self.last_renewal = datetime.now(timezone.utc)
        self.renewals += 1
        log.info("session renewed: %s", ", ".join(changed) or "nothing changed")
        return {
            "renewed": True,
            "changed": changed,
            "sessionid": mask_sessionid(
                merged.get("sessionid") or merged.get("sessionid_ss") or ""
            ),
            "sessionid_rotated": rotated,
            "cookies": len(merged),
            "at": self.last_renewal.isoformat(),
        }

    async def ensure(self) -> dict:
        """Renew when needed."""
        if not force_renew_needed(self):
            return {"renewed": False, "reason": "still fresh"}
        return await self.renew()

    # -- healing ---------------------------------------------------------
    async def check_attached(self) -> bool:
        """Is the account actually signed in right now?

        Reads the rendered page: a logged-out session renders a guest.
        """
        backend = getattr(self.engine, "_browser", None)
        if backend is None:
            http = getattr(self.engine, "_http", None)
            if http is None:
                return self.attached if self.attached is not None else False
            try:
                await http.fetch_json("/api/inbox/notice_count/", {})
                self.attached = True
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.attached = False
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return bool(self.attached)

        try:
            user = await backend.account_context()
        except Exception as exc:  # noqa: BLE001
            self.attached = False
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return False
        self.attached = bool(user and (user.get("id") or user.get("uid")))
        self.last_error = None if self.attached else "the page rendered no account"
        return bool(self.attached)

    async def reseed_from_env(self) -> dict:
        """Re-install the durable half from ``TIKTOK_SESSION``.

        This is the permanent copy: as long as the user keeps the account
        logged in, the environment variable keeps working.
        """
        from app.sessions import install_session_from_env

        try:
            info = await install_session_from_env(force=True)
        except SessionError as exc:
            self.last_error = str(exc)
            log.error("could not re-seed the session from TIKTOK_SESSION: %s", exc)
            return {"reseeded": False, "reason": str(exc)}
        self.reseeds += 1
        log.warning("session re-seeded from TIKTOK_SESSION")
        return {"reseeded": True, **(info or {})}

    async def heal(self) -> dict:
        """Re-mint the session and retry once after an auth failure.

        First the volatile half is refreshed; if the account still does not
        answer, the durable half is restored from the environment copy.
        """
        log.warning("session looks dead — re-minting it")
        self.heals += 1
        result = await self.renew(force=True)

        if not await self.check_attached():
            reseeded = await self.reseed_from_env()
            if reseeded.get("reseeded"):
                result = await self.renew(force=True)
        return result

    # -- status ----------------------------------------------------------
    def status(self) -> dict:
        try:
            cookies = self.current_cookies()
        except SessionError:
            return {"present": False, "status": "missing"}
        durable = sorted(k for k in cookies if k in DURABLE_COOKIES)
        try:
            sessionid = mask_sessionid(extract_sessionid(cookies))
        except SessionError:
            sessionid = None
        return {
            "present": True,
            "status": "valid" if durable else "incomplete",
            "sessionid": sessionid,
            "durable_cookies": durable,
            "cookies": len(cookies),
            "attached": self.attached,
            "last_renewal": self.last_renewal.isoformat() if self.last_renewal else None,
            "renewals": self.renewals,
            "heals": self.heals,
            "reseeds": self.reseeds,
            "sessionid_rotated": self.sessionid_rotated,
            "last_error": self.last_error,
        }


def force_renew_needed(keeper: SessionKeeper) -> bool:
    return keeper.stale()


async def renewal_loop(keeper: SessionKeeper) -> None:
    """Keep the session alive for as long as the process runs.

    The volatile half of a session expires in hours; the site re-mints it on
    every visit. Renewing keeps the bot attached to the account without the
    user doing anything.
    """
    interval = max(300.0, settings.session_renew_hours * 3600)
    while True:
        await asyncio.sleep(interval)
        try:
            await keeper.renew()
            if not await keeper.check_attached():
                log.warning("the account is no longer answering as signed in")
                await keeper.heal()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("session renewal failed: %s: %s", type(exc).__name__, exc)
