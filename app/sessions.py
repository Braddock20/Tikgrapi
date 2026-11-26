"""Session lifecycle: upload, normalise, track and probe the TikTok session."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import SessionMeta
from app.security import (
    ESSENTIAL_COOKIES,
    SessionError,
    extract_sessionid,
    hash_sessionid,
    mask_sessionid,
    normalize_cookies,
    parse_session_material,
    parse_session_payload,
    read_session,
    store_session,
    to_session_payload,
)

log = logging.getLogger(__name__)


async def save_session_material(
    raw: bytes | str, cookies: dict[str, str] | None = None
) -> dict:
    """Parse, normalise and encrypt a session; return its metadata.

    Accepts a browser cookie header or a JSON cookie export.
    """
    if cookies is None:
        cookies = parse_session_payload(raw)
    if not cookies:
        raise SessionError("No cookies found in the upload")

    sessionid = extract_sessionid(cookies)
    kept, dropped = normalize_cookies(cookies)

    missing = [name for name in ESSENTIAL_COOKIES if name not in kept]
    if "sessionid" in missing:
        raise SessionError("The session has no sessionid")

    # Store in the shape the tiktokrapi loader expects.
    payload = to_session_payload(kept)
    store_session(_json_bytes(payload))

    await init_db()
    async with SessionLocal() as db:
        record = await db.scalar(
            select(SessionMeta)
            .where(SessionMeta.sessionid_hash == hash_sessionid(sessionid))
            .order_by(SessionMeta.updated_at.desc())
            .limit(1)
        )
        if record is None:
            record = SessionMeta(sessionid_hash=hash_sessionid(sessionid))
            db.add(record)
        record.sessionid_hint = mask_sessionid(sessionid)
        record.cookie_names = sorted(kept)
        record.status = "unverified"
        record.detail = None
        record.is_active = True
        await db.commit()
        await db.refresh(record)

    return {
        "stored": True,
        "encrypted": True,
        "sessionid": record.sessionid_hint,
        "cookies": len(kept),
        "dropped_cookies": dropped,
        "missing_optional_cookies": missing,
        "status": record.status,
    }


def _json_bytes(payload) -> bytes:
    import json

    return json.dumps(payload, indent=2).encode()


# --------------------------------------------------------------------------
# the environment as the permanent attachment
# --------------------------------------------------------------------------
def env_session_cookies() -> dict[str, str]:
    """The cookies described by ``TIKTOK_SESSION``, if it is set."""
    raw = (settings.tiktok_session or "").strip()
    if not raw:
        return {}
    return parse_session_material(raw)


def stored_sessionid() -> str | None:
    """The sessionid of the stored session, if there is one."""
    try:
        return extract_sessionid(parse_session_payload(read_session()))
    except SessionError:
        return None


async def install_session_from_env(force: bool = False) -> dict:
    """Install the session described by ``TIKTOK_SESSION``.

    This is the deployment's permanent attachment to the account: the
    environment variable outlives restarts, redeploys and expired tokens.
    The stored session file is the *working copy* — it carries the volatile
    half that the site re-mints.

    A stored session whose sessionid differs from the environment one is
    kept: it is usually a sessionid the site rotated and the keeper already
    proved works. Pass ``force`` to overwrite it anyway.
    """
    cookies = env_session_cookies()
    if not cookies:
        return {"installed": False, "reason": "TIKTOK_SESSION is not set"}

    try:
        sessionid = extract_sessionid(cookies)
    except SessionError as exc:
        raise SessionError(str(exc)) from exc

    current = stored_sessionid()
    if current and current != sessionid and not force:
        log.warning(
            "TIKTOK_SESSION holds a different sessionid (%s) than the stored "
            "session (%s) — keeping the stored one, which the keeper has "
            "already renewed. Update TIKTOK_SESSION to the current value, "
            "or POST /admin/session to install a new session now.",
            mask_sessionid(sessionid),
            mask_sessionid(current),
        )
        return {
            "installed": False,
            "reason": "kept the stored session (different sessionid)",
            "sessionid": mask_sessionid(current),
        }

    info = await save_session_material(
        settings.tiktok_session, cookies=cookies
    )
    info["installed"] = True
    log.info(
        "session installed from TIKTOK_SESSION (%s, %s cookies)",
        info.get("sessionid"),
        info.get("cookies"),
    )
    return info


async def record_verification(
    status: str, detail: str | None = None, username: str | None = None,
    account_id: str | None = None,
) -> dict:
    """Store the outcome of a live session check."""
    async with SessionLocal() as db:
        row = await db.scalar(
            select(SessionMeta)
            .where(SessionMeta.is_active.is_(True))
            .order_by(SessionMeta.updated_at.desc())
            .limit(1)
        )
        if row is None:
            return {}
        row.status = status
        row.detail = (detail or "")[:500] or None
        if username:
            row.username = username
        if account_id:
            row.account_id = account_id
        row.verified_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        return _meta_dict(row)


async def current_session_meta() -> dict | None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(SessionMeta)
            .where(SessionMeta.is_active.is_(True))
            .order_by(SessionMeta.updated_at.desc())
            .limit(1)
        )
        return _meta_dict(row) if row else None


def _meta_dict(row: SessionMeta) -> dict:
    return {
        "sessionid": row.sessionid_hint,
        "status": row.status,
        "detail": row.detail,
        "username": row.username,
        "account_id": row.account_id,
        "cookies": len(row.cookie_names or []),
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
