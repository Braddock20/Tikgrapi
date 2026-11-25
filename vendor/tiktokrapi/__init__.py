"""tiktokrapi — modern TikTok web API client."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from . import config, exceptions
from .http import HttpClient
from .mixins.auth import AuthMixin
from .mixins.comment import CommentMixin
from .mixins.feed import FeedMixin
from .mixins.hashtag import HashtagMixin
from .mixins.interact import InteractMixin
from .mixins.music import MusicMixin
from .mixins.search import SearchMixin
from .mixins.upload import UploadMixin
from .mixins.user import UserMixin
from .signer import Signer
from .utils import dump_session, load_session, random_device_id

__version__ = "0.1.0"

__all__ = ["Client", "exceptions", "config"]


def _atomic_write_session(path: Path, cookies: dict[str, str]) -> None:
    """Write session.json atomically: temp file + rename. Survives crashes."""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".session_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w") as f:
            import json
            arr = [
                {"name": k, "value": v, "domain": ".tiktok.com", "path": "/"}
                for k, v in cookies.items()
            ]
            json.dump(arr, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


class Client(
    AuthMixin,
    UserMixin,
    FeedMixin,
    InteractMixin,
    CommentMixin,
    SearchMixin,
    HashtagMixin,
    MusicMixin,
    UploadMixin,
):
    """TikTok web client.

    Usage:
        c = Client.from_session("session.json")
        videos, cursor, has_more = c.user_posts("MS4wLj...secUid")
        c.like(videos[0].id)

    Token rotation:
        Set-Cookie msToken from every accepted response auto-updates the in-memory jar.
        If `session_path` is set (via from_session), updates are persisted atomically.
        On token-shaped failure (200 + empty body), one refresh-and-retry is attempted
        under a singleflight lock before raising SignatureError.
    """

    def __init__(
        self,
        cookies: dict[str, str],
        *,
        device_id: Optional[str] = None,
        verify_fp: Optional[str] = None,
        proxy: Optional[str] = None,
        ua: str = config.UA,
        node_path: str = "node",
        session_path: Optional[Path] = None,
        warmup: bool = True,
    ):
        self.cookies = dict(cookies)
        self.device_id = device_id or random_device_id()
        self.verify_fp = verify_fp or cookies.get("s_v_web_id", "")
        self.ua = ua
        self.session_path = Path(session_path) if session_path else None
        self.signer = Signer(ua, node_path=node_path)

        # Cookie change callback — atomic persistence if session_path set
        on_change = None
        if self.session_path:
            def on_change(updated_cookies: dict[str, str]) -> None:
                self.cookies = updated_cookies
                _atomic_write_session(self.session_path, updated_cookies)

        self.http = HttpClient(
            cookies, self.signer, proxy=proxy, ua=ua, on_cookie_change=on_change
        )
        # Bind defaults so HttpClient can attach device_id+verifyFp+odinId per call
        self._bind_request_defaults()

        # Warmup: hit /foryou to mint a fresh msToken (signed API rejects stale tokens)
        if warmup:
            self._warmup()

    def _warmup(self) -> None:
        """Hit /foryou HTML page — server mints fresh msToken via Set-Cookie.
        Updates jar in place. Required after long idle or first run with stale session."""
        try:
            r = self.http.session.get(
                f"{config.API_BASE}/foryou",
                headers={"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml"},
                timeout=config.DEFAULT_TIMEOUT,
            )
            self.http._ingest_set_cookie(r)
        except Exception:
            pass  # warmup is best-effort

    def _bind_request_defaults(self) -> None:
        orig = self.http.request

        def wrapped(path: str, **kwargs):
            kwargs.setdefault("device_id", self.device_id)
            kwargs.setdefault("verify_fp", self.verify_fp)
            return orig(path, **kwargs)

        self.http.request = wrapped  # type: ignore[method-assign]

    @classmethod
    def from_session(
        cls,
        path: str | Path,
        *,
        device_id: Optional[str] = None,
        verify_fp: Optional[str] = None,
        proxy: Optional[str] = None,
        node_path: str = "node",
        persist: bool = True,
        ua: str = config.UA,
        warmup: bool = True,
    ) -> "Client":
        """Load session from JSON file. If `persist=True` (default), msToken updates
        are written back atomically so subsequent runs use the latest token."""
        cookies = load_session(path)
        return cls(
            cookies,
            device_id=device_id,
            verify_fp=verify_fp,
            proxy=proxy,
            node_path=node_path,
            session_path=Path(path) if persist else None,
            ua=ua,
            warmup=warmup,
        )

    def close(self) -> None:
        self.http.close()
        self.signer.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
