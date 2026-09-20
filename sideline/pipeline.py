"""Running stages against the stores: the one place a job, the API and the
CLI all go through, so a stage runs the same way inline, under RQ, or from
a terminal with no services."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sideline.config import Settings, build_blobs, build_engine, build_queue
from sideline.db.models import Job, Match, StageRun
from sideline.db.session import session_scope
from sideline.stages import ORDER, STAGES, MatchRecord, StageContext
from sideline.storage.artifacts import ArtifactRef, ArtifactStore


def record_of(m: Match) -> MatchRecord:
    return MatchRecord(
        id=m.id, capture_mode=m.capture_mode, source_key=m.source_key, sport=m.sport,
        sm_match_id=m.sm_match_id, pitch_length=m.pitch_length, pitch_width=m.pitch_width,
        kickoff_offset_ms=m.kickoff_offset_ms,
    )


def run_stage(settings: Settings, match: MatchRecord, stage: str, config: Optional[dict] = None) -> ArtifactRef:
    """Run one stage for one match and record the version it wrote."""
    if stage not in STAGES:
        raise KeyError(f"unknown stage {stage!r}; known: {ORDER}")
    blobs = build_blobs(settings)
    artifacts = ArtifactStore(blobs)
    cfg = {"sample_fps": settings.sample_fps, "cache_frames": settings.cache_frames, **(config or {})}
    ctx = StageContext(match=match, artifacts=artifacts, blobs=blobs, workdir=settings.workdir,
                       pipeline_version=settings.pipeline_version, config=cfg)
    ref = STAGES[stage].run(ctx)
    engine = build_engine(settings)
    with session_scope(engine) as s:
        manifest = artifacts.read_manifest(ref)
        s.add(StageRun(match_id=match.id, stage=stage, version=ref.version, stage_version=manifest["stage_version"],
                       pipeline_version=manifest["pipeline_version"], config=manifest["config"], inputs=manifest["inputs"]))
    return ref


def run_job(settings: Settings, job_id: str) -> Optional[ArtifactRef]:
    """What the queue calls: mark the job, run its stage, mark the outcome."""
    engine = build_engine(settings)
    with session_scope(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status == "done":
            return None
        job.status, job.started_at = "running", datetime.now(timezone.utc)
        match = record_of(s.get(Match, job.match_id))
        stage = job.stage
    try:
        ref = run_stage(settings, match, stage)
    except Exception as e:  # the failure is the record; re-raise for the queue
        with session_scope(engine) as s:
            job = s.get(Job, job_id)
            job.status, job.error, job.finished_at = "failed", f"{type(e).__name__}: {e}", datetime.now(timezone.utc)
        raise
    with session_scope(engine) as s:
        job = s.get(Job, job_id)
        job.status, job.artifact_version, job.finished_at = "done", ref.version, datetime.now(timezone.utc)
    return ref


def enqueue_stages(settings: Settings, match_id: str, stages: list[str]) -> list[str]:
    """Create one job per stage, each depending on the one before, and hand
    them to the queue. Returns the job ids in order."""
    engine = build_engine(settings)
    queue = build_queue(settings)
    ids: list[str] = []
    with session_scope(engine) as s:
        for stage in stages:
            job = Job(match_id=match_id, stage=stage, status="queued")
            s.add(job)
            s.flush()
            ids.append(job.id)
    previous: Optional[str] = None
    for job_id in ids:
        queue_id = queue.enqueue(job_id, depends_on=previous)
        with session_scope(engine) as s:
            s.get(Job, job_id).queue_id = queue_id
        previous = queue_id
    return ids


def ingest_local(settings: Settings, video: Path, *, capture_mode: str = "followcam", sm_match_id: Optional[str] = None,
                 pitch: tuple[float, float] = (105.0, 68.0), kickoff_offset_ms: Optional[int] = None,
                 stages: Optional[list[str]] = None) -> str:
    """Register a video file as a match and run the stages inline."""
    blobs = build_blobs(settings)
    engine = build_engine(settings)
    with session_scope(engine) as s:
        m = Match(sm_match_id=sm_match_id, capture_mode=capture_mode, source_key="", original_filename=video.name,
                  pitch_length=pitch[0], pitch_width=pitch[1], kickoff_offset_ms=kickoff_offset_ms)
        s.add(m)
        s.flush()
        m.source_key = f"matches/{m.id}/source/{video.name}"
        blobs.put_file(m.source_key, video, "video/mp4")
        match_id = m.id
        rec = record_of(m)
    for stage in stages or ORDER:
        run_stage(settings, rec, stage)
    return match_id
