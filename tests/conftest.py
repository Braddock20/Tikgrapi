"""Test environment.

Environment variables are set before the app is imported so the engine picks
up a throwaway database and session file instead of the developer's own.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp(prefix="tiktok-engine-tests-"))

os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_PATH", str(_TMP / "session.enc"))
os.environ.setdefault("MEDIA_PATH", str(_TMP / "media"))
os.environ.setdefault("BACKEND", "http")
os.environ.setdefault("BROWSER_HEADLESS", "true")
os.environ.setdefault("ALLOW_WRITE_OPS", "false")
os.environ.setdefault("REQUEST_MIN_INTERVAL", "0")
os.environ.setdefault("REQUEST_MAX_PER_HOUR", "1000")
