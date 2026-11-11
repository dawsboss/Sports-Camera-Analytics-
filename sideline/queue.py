"""The job queue: Redis with RQ on the compose stack, a background thread
for one machine serving the API with no Redis, or the same jobs run inline
for tests and a terminal."""

from __future__ import annotations

import logging
import queue as _queue
import threading
from typing import Optional, Protocol

log = logging.getLogger(__name__)


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


class ThreadQueue:
    """Jobs run one after another on a daemon thread in the API's own
    process, so an upload returns at once and a full match transcodes in
    the background. This is what `sideline serve` uses on a machine with no
    Redis. One queue per process: jobs depend on the one before and must
    not overtake it. A failure is recorded on the job by `run_job` and the
    thread carries on with the next."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.pending: "_queue.Queue[str]" = _queue.Queue()
        self._lock = threading.Lock()
        self._outstanding = 0
        self.done = threading.Event()
        self.done.set()
        self.thread = threading.Thread(target=self._work, name="sideline-jobs", daemon=True)
        self.thread.start()

    def enqueue(self, job_id: str, depends_on: Optional[str] = None) -> str:
        # `depends_on` is implicit: one thread, first in first out, so a
        # stage never starts before the one queued ahead of it has finished.
        with self._lock:
            self._outstanding += 1
            self.done.clear()
        self.pending.put(job_id)
        return job_id

    def _work(self) -> None:
        from sideline.pipeline import run_job

        while True:
            job_id = self.pending.get()
            try:
                run_job(self.settings, job_id)
            except Exception:                        # the job row carries the failure
                log.exception("job %s failed", job_id)
            finally:
                self.pending.task_done()
                with self._lock:
                    self._outstanding -= 1
                    if self._outstanding == 0:
                        self.done.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the queue has drained; for tests and shutdown."""
        return self.done.wait(timeout)
