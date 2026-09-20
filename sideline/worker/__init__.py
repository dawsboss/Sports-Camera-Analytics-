"""The RQ worker: `python -m sideline.worker` or `sideline worker`."""

from __future__ import annotations


def main() -> int:
    from redis import Redis
    from rq import Queue, Worker

    from sideline.config import Settings
    from sideline.queue import RQQueue

    settings = Settings.from_env()
    if not settings.redis_url:
        raise SystemExit("SIDELINE_REDIS_URL is not set; nothing to work on")
    conn = Redis.from_url(settings.redis_url)
    Worker([Queue(RQQueue.name, connection=conn)], connection=conn).work()
    return 0
