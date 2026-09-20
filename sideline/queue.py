"""The job queue: Redis with RQ in the homelab, or the same jobs run inline
for tests and for a laptop with no Redis."""

from __future__ import annotations

from typing import Optional, Protocol


class JobQueue(Protocol):
    def enqueue(self, job_id: str, depends_on: Optional[str] = None) -> str: ...


class InlineQueue:
    """Runs the job before `enqueue` returns. A failure raises here, which
    is what a test wants and what a terminal wants."""

    def __init__(self, settings) -> None:
        self.settings = settings

    def enqueue(self, job_id: str, depends_on: Optional[str] = None) -> str:
        from sideline.pipeline import run_job

        run_job(self.settings, job_id)
        return job_id


class RQQueue:
    name = "sideline"

    def __init__(self, redis_url: str) -> None:
        from redis import Redis
        from rq import Queue

        self.queue = Queue(self.name, connection=Redis.from_url(redis_url), default_timeout=6 * 3600)

    def enqueue(self, job_id: str, depends_on: Optional[str] = None) -> str:
        from sideline.worker.jobs import run_job_by_id

        job = self.queue.enqueue(run_job_by_id, job_id, depends_on=depends_on, job_id=f"sideline:{job_id}")
        return job.id
