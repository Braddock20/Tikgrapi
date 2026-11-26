"""Async SQLAlchemy engine.

One code path for Postgres (production) and SQLite (local/dev).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool

from app.config import settings

if settings.is_sqlite:
    # A fresh checkout has no data/ directory and SQLite will not create one.
    _db_path = settings.database_url.split("///")[-1]
    if _db_path and _db_path != ":memory:":
        from pathlib import Path as _Path

        _Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine_kwargs: dict = {
        "echo": False,
        "future": True,
        "connect_args": {"check_same_thread": False},
    }
    if ":memory:" in settings.database_url:
        # An in-memory database only exists inside one connection.
        _engine_kwargs["poolclass"] = StaticPool
    else:
        # A file database needs a real pool: the API and the worker are
        # separate connections, and sharing one would let a read miss the
        # worker's commit.
        #
        # aiosqlite's default pool is a NullPool, which takes no size
        # arguments — passing them raises at engine creation.
        _engine_kwargs["poolclass"] = AsyncAdaptedQueuePool
        _engine_kwargs["pool_size"] = 5
        _engine_kwargs["max_overflow"] = 5
        _engine_kwargs["pool_recycle"] = 1800

    engine = create_async_engine(settings.database_url, **_engine_kwargs)
else:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with SessionLocal() as session:
        yield session


_initialised = False


async def init_db() -> None:
    """Create the schema. Idempotent, so any process can call it."""
    global _initialised
    if _initialised:
        return
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _initialised = True
