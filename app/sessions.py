"""Session lifecycle: upload, normalise, track and probe the TikTok session."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import SessionMeta
from app.security import (
    ESSENTIAL_COOKIES,
    SessionError,
    extract_sessionid,
    hash_sessionid,
    mask_sessionid,
    normalize_cookies,
    parse_session_payload,
    store_session,
    to_session_payload,
)

log = logging.getLogger(__name__)


async def save_session_material(raw: bytes | str) -> dict:
    """Parse, normalise and encrypt a session; return its metadata.

    Accepts a browser cookie header or a JSON cookie export.
    """
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
