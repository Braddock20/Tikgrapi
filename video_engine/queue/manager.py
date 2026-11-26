"""The render queue.

Jobs live in the history database, so a restart picks up where it left off.
Only one render runs at a time: a render is CPU- and memory-heavy and a
small instance cannot afford two of them.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from video_engine.storage.database import HistoryStore

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.PENDING
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobQueue:
    """A durable, single-worker queue."""

    def __init__(self, store: HistoryStore, *, max_attempts: int = 3) -> None:
        self.store = store
        self.max_attempts = max_attempts
        self._pending: list[Job] = []
        self._running: list[Job] = []
        self._done: list[Job] = []
        self._failed: list[Job] = []
        self._handler: Callable[[Job], Any] | None = None

    # -- wiring ----------------------------------------------------------
    def set_handler(self, handler: Callable[[Job], Any]) -> None:
        self._handler = handler

    # -- state -----------------------------------------------------------
    def recover(self) -> int:
        """Reload unfinished jobs after a restart."""
        recovered = 0
        for row in self.store.load_pending_jobs():
            job = Job(
                id=row["job_id"],
                status=JobStatus.PENDING,
                params=row.get("params") or {},
                attempts=int(row.get("attempts") or 0),
            )
            self._pending.append(job)
            recovered += 1
        if recovered:
            log.info("recovered %s pending job(s)", recovered)
        return recovered

    # -- lifecycle -------------------------------------------------------
    def enqueue(self, params: dict[str, Any]) -> Job:
        job = Job(id=uuid.uuid4().hex, params=params)
        self._pending.append(job)
        self.store.save_job(job.id, JobStatus.PENDING.value, params)
        return job

    def _finish(self, job: Job, status: JobStatus, result: Any = None, error: str | None = None) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.updated_at = time.time()
        self.store.save_job(job.id, status.value, job.params, result, error)

    def run_once(self) -> Job | None:
        """Run the next pending job. Returns the job, or None when idle."""
        if not self._pending:
            return None
        if self._handler is None:
            raise RuntimeError("the queue has no handler")

        job = self._pending.pop(0)
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.updated_at = time.time()
        self._running.append(job)
        self.store.update_job(job.id, JobStatus.RUNNING.value)

        try:
            result = self._handler(job)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            log.exception("job %s failed", job.id)
            if job.attempts < self.max_attempts:
                job.status = JobStatus.PENDING
                job.error = error
                self._pending.insert(0, job)
                self.store.update_job(job.id, JobStatus.PENDING.value, error=error)
            else:
                self._finish(job, JobStatus.FAILED, error=error)
                self._failed.append(job)
            self._running.remove(job)
            return job
        else:
            self._finish(job, JobStatus.COMPLETED, result=result)
            self._done.append(job)
            self._running.remove(job)
            return job

    def run_all(self) -> list[Job]:
        results: list[Job] = []
        while self._pending:
            job = self.run_once()
            if job is None:
                break
            results.append(job)
            if job.status == JobStatus.FAILED:
                # A failed job is retried in place, so stop when nothing moved.
                if job.attempts >= self.max_attempts:
                    break
        return results

    # -- inspection ------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "pending": len(self._pending),
            "running": len(self._running),
            "completed": len(self._done),
            "failed": len(self._failed),
            "jobs": [job.as_dict() for job in (self._running + self._pending + self._failed[-10:])],
        }

    @property
    def pending(self) -> list[Job]:
        return list(self._pending)

    @property
    def running(self) -> list[Job]:
        return list(self._running)

    @property
    def failed(self) -> list[Job]:
        return list(self._failed)

    @property
    def completed(self) -> list[Job]:
        return list(self._done)
