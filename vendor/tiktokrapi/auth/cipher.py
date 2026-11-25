"""TikTok login body cipher.

`mix_mode=1` means username/password are XOR'd byte-by-byte with key 0x05,
then hex-encoded. Discovered by diffing real Brave login cURL.
"""
from __future__ import annotations

XOR_KEY = 0x05


def encode_credential(value: str) -> str:
    """XOR ⊕ 0x05 + hex-encode. Used for username/password in mix_mode=1."""
    return bytes(b ^ XOR_KEY for b in value.encode()).hex()


def decode_credential(hex_str: str) -> str:
    """Inverse of encode_credential. Useful for verification."""
    return bytes(b ^ XOR_KEY for b in bytes.fromhex(hex_str)).decode()


def build_login_body(
    username: str,
    password: str,
    *,
    aid: int = 1459,
    region: str = "NG",
    language: str = "en",
    locale: str = "en-GB",
    device_id: str = "",
    is_sso: bool = False,
    account_sdk_source: str = "web",
) -> str:
    """Build URL-encoded body for POST /passport/web/user/login/.

    Returns x-www-form-urlencoded string ready for POST body.
    """
    from urllib.parse import urlencode

    return urlencode({
        "mix_mode": "1",
        "username": encode_credential(username),
        "password": encode_credential(password),
        "aid": str(aid),
        "is_sso": str(is_sso).lower(),
        "account_sdk_source": account_sdk_source,
        "region": region,
        "language": language,
        "locale": locale,
        "did": device_id,
        "fixed_mix_mode": "1",
    })
