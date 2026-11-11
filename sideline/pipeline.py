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
from sideline.stages import DEFAULT_STAGES, ORDER, STAGES, MatchRecord, StageContext
from sideline.storage.artifacts import ArtifactRef, ArtifactStore


def record_of(m: Match) -> MatchRecord:
    return MatchRecord(
        id=m.id, capture_mode=m.capture_mode, source_key=m.source_key, sport=m.sport,
        sm_match_id=m.sm_match_id, pitch_length=m.pitch_length, pitch_width=m.pitch_width,
        kickoff_offset_ms=m.kickoff_offset_ms, periods=m.periods,
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


FETCH = "fetch"          # the job that lands a linked video in the blob store, before S0

MAX_FETCH_BYTES = 32 * 1024 ** 3


def fetch_source(settings: Settings, match_id: str) -> str:
    """Download the match's `source_url` into `matches/{id}/source/` and
    point `source_key` at it. Direct links to a video file over http(s)
    only, streamed to disk; a Veo match page is not a video and is refused
    by the content type. Returns the blob key."""
    import shutil
    import tempfile
    import urllib.parse
    import urllib.request

    engine = build_engine(settings)
    blobs = build_blobs(settings)
    with session_scope(engine) as s:
        m = s.get(Match, match_id)
        if m is None:
            raise KeyError(match_id)
        if m.source_key and blobs.exists(m.source_key):
            return m.source_key
        url = m.source_url
    if not url:
        raise ValueError("the match has no source_url to fetch")
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) links can be fetched, not {parts.scheme!r}")
    name = Path(urllib.parse.unquote(parts.path)).name or "match.mp4"
    if "." not in name:
        name += ".mp4"
    key = f"matches/{match_id}/source/{name}"
    if not blobs.exists(key):
        # A retry after the download landed but the record did not: the blob
        # is there, immutable, and only the record needs writing.
        tmpdir = Path(tempfile.mkdtemp(dir=settings.workdir))
        try:
            tmp = tmpdir / name
            req = urllib.request.Request(url, headers={"User-Agent": "sideline/0.1"})
            with _http_only_opener().open(req, timeout=60) as resp, tmp.open("wb") as out:
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype and not (ctype.startswith("video/") or ctype in ("application/octet-stream", "binary/octet-stream")):
                    raise ValueError(f"{url} serves {ctype}, not a video; the link must be to the file itself")
                total = 0
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FETCH_BYTES:
                        raise ValueError("the file is larger than the fetch limit")
                    out.write(chunk)
            if total == 0:
                raise ValueError(f"{url} returned an empty file")
            blobs.put_file(key, tmp, "video/mp4")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    with session_scope(engine) as s:
        m = s.get(Match, match_id)
        m.source_key = key
        m.original_filename = m.original_filename or name
    return key


def _http_only_opener():
    """urllib follows a redirect to any scheme its handlers know, ftp and
    file-like ones included; a fetch that was asked for an http(s) link
    stays on http(s) through every hop."""
    import urllib.request

    class _Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not (newurl.startswith("http://") or newurl.startswith("https://")):
                raise ValueError(f"refusing a redirect off http(s): {newurl}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_Redirects)


def run_job(settings: Settings, job_id: str) -> Optional[ArtifactRef]:
    """What the queue calls: mark the job, run its stage (or the fetch that
    precedes S0 for a linked video), mark the outcome."""
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
        if stage == FETCH:
            fetch_source(settings, match.id)
            with session_scope(engine) as s:
                match = record_of(s.get(Match, match.id))
            ref = None
        else:
            ref = run_stage(settings, match, stage)
    except Exception as e:  # the failure is the record; re-raise for the queue
        with session_scope(engine) as s:
            job = s.get(Job, job_id)
            job.status, job.error, job.finished_at = "failed", f"{type(e).__name__}: {e}", datetime.now(timezone.utc)
        raise
    with session_scope(engine) as s:
        job = s.get(Job, job_id)
        job.status, job.finished_at = "done", datetime.now(timezone.utc)
        job.artifact_version = ref.version if ref is not None else None
    return ref


def run_stages(settings: Settings, match_id: str, stages: list[str], config: Optional[dict] = None) -> list[ArtifactRef]:
    """Run stages on a match that already exists, inline; each run is a
    `StageRun` row as usual. How a GPU machine runs detection on a match
    another machine ingested."""
    engine = build_engine(settings)
    with session_scope(engine) as s:
        m = s.get(Match, match_id)
        if m is None:
            raise KeyError(f"no match {match_id}")
        rec = record_of(m)
    return [run_stage(settings, rec, stage, config) for stage in stages]


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
                 stages: Optional[list[str]] = None, config: Optional[dict] = None) -> str:
    """Register a video file as a match and run the stages inline. `config`
    reaches every stage; each takes the keys it knows."""
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
    for stage in DEFAULT_STAGES if stages is None else stages:
        run_stage(settings, rec, stage, config)
    return match_id
