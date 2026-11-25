"""Session loading. Programmatic login NOT supported (TikTok demands captcha + email OTP)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..utils import dump_session, load_session

if TYPE_CHECKING:
    from .. import Client


class AuthMixin:
    self: "Client"

    def load_session(self, path: str | Path) -> None:
        """Load cookies from a Playwright/browser export."""
        cookies = load_session(path)
        self.cookies = cookies
        if hasattr(self, "http") and self.http is not None:
            self.http.cookies = cookies
            self.http.session.cookies.update(cookies)

    def dump_session(self, path: str | Path) -> None:
        dump_session(self.cookies, path)
