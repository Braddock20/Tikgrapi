"""Job queue abstraction.

Production runs ARQ on Redis. Local runs an in-process queue with the same
surface, so the API code does not care which one it has.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


class InlineQueue:
    """A tiny in-process job queue.

    Used when no Redis is configured. It has the same ``enqueue_job`` shape
    as ARQ's pool so the API layer stays identical in both modes.
    """

    def __init__(self, concurrency: int = 2, handler: Callable[..., Any] | None = None) -> None:
        self.concurrency = max(1, concurrency)
        self.handler = handler
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._ctx: dict[str, Any] = {}
        self._started = False

    def set_context(self, ctx: dict[str, Any]) -> None:
        self._ctx = ctx

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self.concurrency):
            self._tasks.append(asyncio.create_task(self._worker(index)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        self._started = False

    async def enqueue_job(self, function_name: str, *args: Any, **kwargs: Any) -> str:
        """Queue a job.

        ARQ reserves the underscore-prefixed keyword arguments (``_job_id``,
        ``_queue_name``, ...) for its own bookkeeping and never passes them to
        the job function — mirror that here.
        """
        job_id = kwargs.pop("_job_id", None)
        for reserved in ("_queue_name", "_job_try", "_expires", "_pipeline"):
            kwargs.pop(reserved, None)
        await self._queue.put((function_name, args, kwargs))
        return job_id or f"{function_name}:{args}:{kwargs}"

    async def _worker(self, index: int) -> None:
        while True:
            function_name, args, kwargs = await self._queue.get()
            try:
                if self.handler is None:
                    log.warning("inline queue has no handler for %s", function_name)
                    continue
                await self.handler(self._ctx, *args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("inline job %s failed", function_name)
            finally:
                self._queue.task_done()


class RedisQueue:
    """Thin wrapper over an ARQ pool so both queues expose the same API."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue_job(self, function_name: str, *args: Any, **kwargs: Any) -> str:
        return await self._pool.enqueue_job(function_name, *args, **kwargs)

    async def stop(self) -> None:
        await self._pool.close()
