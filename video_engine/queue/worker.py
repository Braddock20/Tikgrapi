"""The queue worker.

The worker runs exactly one job at a time. Rendering a vertical video is
CPU- and memory-heavy, and a small instance cannot afford two renders in
parallel — running them one after the other keeps the peak footprint
predictable.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from video_engine.queue.manager import Job, JobQueue, JobStatus

log = logging.getLogger(__name__)


class RenderWorker:
    """Runs render jobs from the queue, one at a time."""

    def __init__(self, queue: JobQueue, handler: Callable[[Job], Any], *, max_attempts: int = 3) -> None:
        self.queue = queue
        self.handler = handler
        self.max_attempts = max_attempts
        self.queue.set_handler(handler)

    def run_job(self, job: Job) -> Any:
        """Execute a single job (used as the queue's handler)."""
        log.info("rendering job %s", job.id)
        return self.handler(job)

    def run_pending(self) -> list[Job]:
        """Run everything that is queued."""
        results = self.queue.run_all()
        for job in results:
            if job.status == JobStatus.FAILED:
                log.error("job %s failed after %s attempt(s): %s", job.id, job.attempts, job.error)
            else:
                log.info("job %s finished", job.id)
        return results

    def recover(self) -> int:
        """Reload unfinished jobs after a restart."""
        return self.queue.recover()
