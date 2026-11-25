"""Application settings.

The stack runs in two shapes:
  * production  — Postgres + Redis + ARQ worker (docker compose / Render)
  * local/dev   — SQLite + in-process worker (no external services)

Both are selected purely from environment variables, so the same image runs
either way.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_db_url(url: str) -> str:
    """Render supplies postgres:// — SQLAlchemy needs the asyncpg driver."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


class Settings(BaseSettings):
    app_name: str = "TikTok Automation Engine"
    environment: str = "development"
    api_key: str = "change-me"

    database_url: str = "sqlite+aiosqlite:///./data/engine.db"
    # Empty REDIS_URL selects the in-process worker instead of ARQ.
    redis_url: str = ""

    session_encryption_key: str = ""
    session_path: str = "./data/session.enc"
    media_path: str = "./data/media"
    worker_concurrency: int = 2

    # --- TikTok client -------------------------------------------------
    # "browser" drives a real Chromium so requests carry the site's current
    # X-Dynosaur signature; "http" uses plain signed/unsigned HTTP.
    backend: str = "browser"
    browser_headless: bool = True
    browser_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
    )
    browser_timeout_ms: int = 45000
    http_impersonate: str = "chrome131"
    http_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # --- Safety rails --------------------------------------------------
    # Account-changing operations stay off until explicitly enabled.
    allow_write_ops: bool = False
    # Minimum seconds between two requests to TikTok (per process).
    request_min_interval: float = 2.0
    # Hard ceiling of TikTok requests per hour, per process.
    request_max_per_hour: int = 240

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def uses_redis(self) -> bool:
        return bool(self.redis_url.strip())

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
settings.database_url = _normalize_db_url(settings.database_url)
