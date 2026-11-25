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
    """ARQ startup — build the adapter once for the worker process."""
    from app.adapters.tiktok import TikTokAdapter

    adapter = TikTokAdapter()
    await adapter.start()
    ctx["adapter"] = adapter


class WorkerSettings:
    functions = [run_job]
    on_startup = startup
    redis_settings = None  # set at import time below
    max_jobs = settings.worker_concurrency
    job_timeout = 300
    # No blind retries: writes are not idempotent.
    max_tries = 1


if settings.uses_redis:
    from arq.connections import RedisSettings

    WorkerSettings.redis_settings = RedisSettings.from_dsn(settings.redis_url)
