"""Application settings.

The stack runs in two shapes:
  * production  — Postgres + Redis + ARQ worker (docker compose / Render)
  * local/dev   — SQLite + in-process worker (no external services)

Both are selected purely from environment variables, so the same image runs
either way.

Render's free tier is the design constraint that shaped this file: 512 MB of
RAM, 0.5 vCPU, an ephemeral disk and a service that is frozen between
requests. Every knob below has a default that fits inside that budget.
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
    #
    # "auto" is the Render-friendly default: start with the cheap HTTP
    # client and only pay for Chromium when an operation actually needs a
    # signed request. Chromium is released again after an idle window.
    backend: str = "auto"
    browser_headless: bool = True
    browser_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
    )
    browser_timeout_ms: int = 45000
    # The TLS fingerprint curl_cffi should present. curl_cffi 0.7.x knows up
    # to chrome124; a newer curl_cffi can go higher. If the target is not
    # supported the HTTP backend falls back to the generic "chrome".
    http_impersonate: str = "chrome124"
    http_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # A system Chromium/Chrome to drive instead of Playwright's own download.
    # Playwright's bundled browser is ~420 MB of disk and ~300 MB of RAM —
    # on a 512 MB Render instance that is the difference between running and
    # being OOM-killed. Point this at a system Chrome to avoid the download.
    browser_executable_path: str = ""
    # Extra Chromium flags, space separated, appended to the defaults.
    browser_extra_args: str = ""
    # Release Chromium this many seconds after the last signed request.
    # 0 keeps it alive forever (the old behaviour).
    browser_idle_shutdown_seconds: float = 180.0
    # Never start Chromium when less than this much RAM is free.
    browser_min_free_mb: int = 140
    # The memory ceiling Chromium is told to stay under.
    browser_max_old_space_mb: int = 128
    # Refuse the site's heavy React bundles and keep only the small security
    # SDK that signs the requests. This is the single biggest memory win:
    # the renderer falls from ~554 MB to ~260 MB and the signatures are
    # still accepted. Turn it off if TikTok ever moves the signer into the
    # application bundle.
    browser_block_app_scripts: bool = True

    # Posting a comment through the page's own composer needs TikTok's React
    # application to boot — the composer is drawn by it — and that costs
    # ~700 MB. On a 512 MB instance that is an OOM kill, so the fallback is
    # only used when there is genuinely room for it.
    ui_fallback_min_free_mb: int = 450

    # The memory the whole process may use. Render's free tier is 512 MB.
    memory_limit_mb: int = 512

    # --- Safety rails --------------------------------------------------
    # Account-changing operations stay off until explicitly enabled.
    allow_write_ops: bool = False
    # Minimum seconds between two requests to TikTok (per process).
    request_min_interval: float = 2.0
    # Hard ceiling of TikTok requests per hour, per process.
    request_max_per_hour: int = 240

    # --- Bot -----------------------------------------------------------
    # The marker that turns a chat message into a command.
    bot_marker: str = "/"
    # comment | dm | auto — auto prefers DM and falls back to a comment.
    bot_channel: str = "auto"
    # The video whose comment thread is the chat. Empty means "use the
    # account's own most recent video".
    bot_video_id: str = ""
    # Seconds between two polls of the chat channel.
    bot_poll_interval: float = 20.0
    # Comma separated user ids allowed to run commands. Empty = anyone.
    bot_allowed_authors: str = ""
    # Seconds between two commands from the same author.
    bot_cooldown: float = 2.0
    # Send the welcome message when the bot starts.
    welcome_on_boot: bool = True
    # Override the welcome text (empty = the built-in one).
    welcome_text: str = ""

    # --- Scheduler -----------------------------------------------------
    # Enable the cron-style automation runner.
    scheduler_enabled: bool = True

    # --- Render --------------------------------------------------------
    # Ping ourselves so Render does not freeze the free instance between
    # visits. Costs a request every N minutes; disable if you do not mind
    # the cold start.
    keepalive_enabled: bool = False
    keepalive_interval_seconds: float = 600.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def uses_redis(self) -> bool:
        return bool(self.redis_url.strip())

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def browser_enabled(self) -> bool:
        """Should a browser ever be launched?

        "auto" and "browser" both may; "http" never does.
        """
        return self.backend.lower() in ("browser", "auto")

    @property
    def http_first(self) -> bool:
        """Start with the cheap HTTP client and only escalate on demand."""
        return self.backend.lower() == "auto"

    @property
    def bot_allowed_author_set(self) -> set[str]:
        return {a.strip() for a in self.bot_allowed_authors.split(",") if a.strip()}


settings = Settings()
settings.database_url = _normalize_db_url(settings.database_url)

# Pin the session, media and SQLite paths to the project root.
settings.session_path = _absolute(settings.session_path)
settings.media_path = _absolute(settings.media_path)
if settings.database_url.startswith("sqlite+aiosqlite:///"):
    _db = settings.database_url.split("///", 1)[1]
    if _db and _db != ":memory:":
        settings.database_url = "sqlite+aiosqlite:///" + _absolute(_db)
