"""Functions RQ imports by name in the worker process."""

from __future__ import annotations

from sideline.config import Settings


def run_job_by_id(job_id: str) -> str:
    from sideline.pipeline import run_job

    ref = run_job(Settings.from_env(), job_id)
    return ref.prefix if ref else ""
