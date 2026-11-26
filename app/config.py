"""Application settings.

The stack runs in two shapes:
  * production  — Postgres + Redis + ARQ worker (docker compose / Render)
  * local/dev   — SQLite + in-process worker (no external services)

Both are selected purely from environment variables, so the same image runs
either way.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Relative paths resolve against the project root, so the service behaves the
# same no matter which directory it is started from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _absolute(path: str) -> str:
    p = Path(path)
    return str(p if p.is_absolute() else (PROJECT_ROOT / p))


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

    # The whole TikTok session in one variable: the raw cookie header from
    # the browser ("name=value; name=value"). This is the only TikTok
    # credential the deployment needs — the engine renews the rest.
    #
    # A bare sessionid is accepted too: it is expanded into the minimal
    # binding cookies (see security.build_session_from_sessionid).
    tiktok_session: str = ""

    # The edge cookie a bare sessionid is paired with. TikTok honours a
    # sessionid only when the request names the edge it was issued on.
    tiktok_target_idc: str = "alisg"

    # How often the volatile half of the session is re-minted, in hours.
    session_renew_hours: float = 6.0
    # Verify at boot that the account really is signed in (costs one page
    # load) and report it on /health.
    session_verify_on_boot: bool = True
    # Re-mint the session's device tokens as soon as the service starts.
    session_renew_on_boot: bool = True
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

# Pin the session, media and SQLite paths to the project root.
settings.session_path = _absolute(settings.session_path)
settings.media_path = _absolute(settings.media_path)
if settings.database_url.startswith("sqlite+aiosqlite:///"):
    _db = settings.database_url.split("///", 1)[1]
    if _db and _db != ":memory:":
        settings.database_url = "sqlite+aiosqlite:///" + _absolute(_db)
