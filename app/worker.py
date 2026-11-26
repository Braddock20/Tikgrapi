"""Job runner.

The same function backs both the ARQ worker and the in-process queue.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.adapters.base import UnsupportedOperation
from app.config import settings
from app.db import SessionLocal
from app.models import AppState, Job
from app.operations import WRITE_OPERATIONS

log = logging.getLogger(__name__)


async def writes_paused() -> bool:
    async with SessionLocal() as db:
        state = await db.get(AppState, "writes_paused")
        return bool(state and state.value.get("paused"))


async def run_job(ctx, job_id: str) -> None:
    """Execute one job and record the outcome."""
    adapter = ctx["adapter"]
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job:
            return
        job.status = "running"
        job.attempts += 1
        await db.commit()
        operation, params = job.operation, dict(job.params or {})

    try:
        if operation in WRITE_OPERATIONS:
            if not settings.allow_write_ops:
                raise PermissionError(
                    f"'{operation}' changes account state and ALLOW_WRITE_OPS is off"
                )
            if await writes_paused():
                raise PermissionError("Writes are paused by the operator")

        result = await adapter.call(operation, params)

        async with SessionLocal() as db:
            job = await db.get(Job, job_id)
            job.status = "completed"
            job.result = result if isinstance(result, dict) else {"data": result}
            job.error = None
            await db.commit()
    except UnsupportedOperation as exc:
        async with SessionLocal() as db:
            job = await db.get(Job, job_id)
            job.status = "unsupported"
            job.error = str(exc)
            await db.commit()
    except PermissionError as exc:
        async with SessionLocal() as db:
            job = await db.get(Job, job_id)
            job.status = "rejected"
            job.error = str(exc)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        async with SessionLocal() as db:
            job = await db.get(Job, job_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            await db.commit()
        log.exception("job %s (%s) failed", job_id, operation)
        raise


async def startup(ctx) -> None:
    """ARQ startup — build the adapter once for the worker process.

    The worker is its own process, so it installs the session from
    TIKTOK_SESSION itself and keeps it alive on its own.
    """
    import asyncio

    from app.adapters.tiktok import TikTokAdapter
    from app.keeper import SessionKeeper, renewal_loop
    from app.sessions import install_session_from_env

    try:
        await install_session_from_env()
    except Exception as exc:  # noqa: BLE001
        log.error("could not install the session from TIKTOK_SESSION: %s", exc)

    adapter = TikTokAdapter()
    await adapter.start()
    keeper = SessionKeeper(adapter)
    adapter.on_auth_failure = keeper.heal
    ctx["adapter"] = adapter
    ctx["keeper"] = keeper
    try:
        await keeper.renew()
        ctx["renewal_task"] = asyncio.create_task(renewal_loop(keeper))
    except Exception as exc:  # noqa: BLE001
        log.warning("the first session renewal did not work: %s: %s", type(exc).__name__, exc)


async def shutdown(ctx) -> None:
    """ARQ shutdown — stop renewing the session."""
    import asyncio

    task = ctx.get("renewal_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    adapter = ctx.get("adapter")
    if adapter is not None:
        await adapter.stop()


class WorkerSettings:
    functions = [run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None  # set at import time below
    max_jobs = settings.worker_concurrency
    job_timeout = 300
    # No blind retries: writes are not idempotent.
    max_tries = 1


if settings.uses_redis:
    from arq.connections import RedisSettings

    WorkerSettings.redis_settings = RedisSettings.from_dsn(settings.redis_url)
