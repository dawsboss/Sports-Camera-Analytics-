"""The worker: `sideline worker`.

With Redis configured it is an RQ worker, as on the compose stack. Without
one it polls the database for queued jobs and runs them in order in this
process, which is how the GPU worker sits beside the API on one machine:
the API queues (`SIDELINE_JOB_RUNNER=db`), this process holds the detector
and the GPU, and nothing between them but the database file.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Optional

from sqlalchemy import update

log = logging.getLogger(__name__)


def claim_next_job(settings) -> Optional[str]:
    """The oldest queued job, marked running by this process. The update is
    conditional on the row still being queued, so two workers on one
    database never run the same job."""
    from sideline.config import build_engine
    from sideline.db.models import Job
    from sideline.db.session import session_scope

    engine = build_engine(settings)
    with session_scope(engine) as s:
        job = s.query(Job).filter_by(status="queued").order_by(Job.created_at).first()
        if job is None:
            return None
        res = s.execute(update(Job).where(Job.id == job.id, Job.status == "queued").values(status="claimed"))
        return job.id if res.rowcount == 1 else None


def run_queued_jobs_once(settings) -> Optional[str]:
    """Claim and run one job; None when nothing is queued. A failure is on
    the job row, not raised, so the loop goes on to the next."""
    from sideline.pipeline import run_job

    job_id = claim_next_job(settings)
    if job_id is None:
        return None
    try:
        run_job(settings, job_id)
    except Exception:
        log.exception("job %s failed", job_id)
    return job_id


def poll_forever(settings, every_s: float = 2.0) -> int:
    """Run jobs as they appear until told to stop. SIGTERM finishes the job
    in hand and then exits, so a restart of the unit never half-writes a
    stage: the version in progress has no manifest and is ignored."""
    stop = {"now": False}

    def _stop(signum, frame) -> None:
        stop["now"] = True
        log.info("stopping after the current job")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker polling %s every %.0f s", settings.resolved_db_url, every_s)
    while not stop["now"]:
        if run_queued_jobs_once(settings) is None:
            time.sleep(every_s)
    return 0


def main(settings=None, once: bool = False, every_s: float = 2.0) -> int:
    from sideline.config import Settings

    settings = settings or Settings.from_env()
    if settings.redis_url and (settings.job_runner in ("", "rq")):
        from redis import Redis
        from rq import Queue, Worker

        from sideline.queue import RQQueue

        conn = Redis.from_url(settings.redis_url)
        Worker([Queue(RQQueue.name, connection=conn)], connection=conn).work()
        return 0
    if once:
        ran = 0
        while run_queued_jobs_once(settings) is not None:
            ran += 1
        print(f"ran {ran} job(s)")
        return 0
    return poll_forever(settings, every_s)
