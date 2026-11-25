"""Credential storage and cookie parsing.

Session material is a credential. It is encrypted at rest with Fernet and
never returned by the API — only a masked hint and a hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# Cookies that describe the CDN edge which issued them rather than the
# account itself.
#
# `tt-target-idc` and its `tt-target-idc-sign` must stay in sync: sending a
# stale pair makes TikTok's *unsigned* endpoints answer with an empty body
# (verified live, 2026-11-25). The browser backend re-requests this pair from
# the site anyway, so only the plain-HTTP backend filters it out.
#
# The `store-*` cookies are different: they carry the account's country and
# dropping them loses the logged-in state. They are kept.
IDC_PAIR_COOKIES = frozenset({"tt-target-idc", "tt-target-idc-sign"})

# Cookies that must survive normalisation for the session to stay usable.
ESSENTIAL_COOKIES = ("sessionid", "ttwid", "msToken", "s_v_web_id")


class SessionError(RuntimeError):
    """Raised when session material is missing, malformed or unusable."""


def _fernet() -> Fernet:
    if not settings.session_encryption_key:
        raise SessionError("SESSION_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.session_encryption_key.encode())
    except Exception as exc:  # noqa: BLE001
        raise SessionError("SESSION_ENCRYPTION_KEY must be a valid Fernet key") from exc


# --------------------------------------------------------------------------
# cookie parsing
# --------------------------------------------------------------------------
def parse_cookie_header(raw: str) -> dict[str, str]:
    """Parse a browser ``Cookie:`` header into a name -> value mapping."""
    cookies: dict[str, str] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if not name:
            continue
        cookies[name] = unquote(value.strip())
    return cookies


def parse_cookie_json(raw: bytes | str) -> dict[str, str]:
    """Parse a Cookie-Editor style JSON export into a name -> value mapping.

    Accepts a list of cookie objects, ``{"cookies": [...]}`` or a plain
    ``{name: value}`` mapping.
    """
    data = json.loads(raw)
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    if isinstance(data, list):
        out: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                out[str(name)] = str(item.get("value", ""))
        return out
    raise SessionError("Expected a cookie list or object")


def parse_session_payload(raw: bytes | str) -> dict[str, str]:
    """Accept either a raw cookie header or a JSON export."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return parse_cookie_json(stripped)
        except json.JSONDecodeError:
            pass
    if "=" not in stripped:
        raise SessionError("Could not parse the session: expected cookies or JSON")
    return parse_cookie_header(stripped)


def extract_sessionid(cookies: dict[str, str]) -> str:
    """Return the account's sessionid, or raise if it is missing."""
    for name in ("sessionid", "sessionid_ss"):
        value = cookies.get(name)
        if value:
            return value
    raise SessionError(
        "No sessionid found in the supplied cookies — export them from a "
        "logged-in TikTok browser session."
    )


def mask_sessionid(sessionid: str) -> str:
    if len(sessionid) <= 12:
        return "***"
    return f"{sessionid[:6]}…{sessionid[-4:]}"


def hash_sessionid(sessionid: str) -> str:
    return hashlib.sha256(sessionid.encode()).hexdigest()


def normalize_cookies(cookies: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return the cookies to store plus any that were rejected.

    Nothing is dropped by default: the full set is what keeps the logged-in
    state intact. Use :func:`filter_for_http` for the plain-HTTP backend.
    """
    kept = {k: v for k, v in cookies.items() if v not in (None, "")}
    dropped = sorted(set(cookies) - set(kept))
    return kept, dropped


def filter_for_http(cookies: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Drop the CDN idc pair for unsigned HTTP calls."""
    kept = {k: v for k, v in cookies.items() if k not in IDC_PAIR_COOKIES}
    dropped = sorted(set(cookies) - set(kept))
    return kept, dropped


def to_session_payload(cookies: dict[str, str]) -> list[dict]:
    """Shape cookies the way the tiktokrapi session loader expects."""
    return [
        {"name": name, "value": value, "domain": ".tiktok.com", "path": "/"}
        for name, value in cookies.items()
    ]


# --------------------------------------------------------------------------
# encrypted storage
# --------------------------------------------------------------------------
def store_session(raw: bytes) -> None:
    path = Path(settings.session_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _fernet().encrypt(raw)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encrypted)
    tmp.replace(path)
    path.chmod(0o600)


def read_session() -> bytes:
    path = Path(settings.session_path)
    if not path.exists():
        raise SessionError("No session uploaded")
    try:
        return _fernet().decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise SessionError("Session cannot be decrypted; check the encryption key") from exc


def session_exists() -> bool:
    return Path(settings.session_path).exists()


def delete_session() -> None:
    path = Path(settings.session_path)
    if path.exists():
        path.unlink()
