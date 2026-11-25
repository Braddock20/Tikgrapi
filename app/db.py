"""Async SQLAlchemy engine.

One code path for Postgres (production) and SQLite (local/dev).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings

if settings.is_sqlite:
    # A fresh checkout has no data/ directory and SQLite will not create one.
    _db_path = settings.database_url.split("///")[-1]
    if _db_path and _db_path != ":memory:":
        from pathlib import Path as _Path

        _Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        # A single shared connection keeps the in-memory/file DB consistent
        # across the API process and the in-process worker.
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
