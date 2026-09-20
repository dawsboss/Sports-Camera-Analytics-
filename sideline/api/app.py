"""The API surface: accept an upload with its accompanying inputs, queue the
stages, and answer what exists. Raw video comes in here and never leaves
the homelab; only published aggregates go anywhere else."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from sideline import __version__
from sideline.config import Settings, build_blobs, build_engine
from sideline.db.models import Job, Match, StageRun
from sideline.db.session import session_scope
from sideline.pipeline import enqueue_stages
from sideline.stages import ORDER
from sideline.storage.artifacts import ArtifactRef, ArtifactStore


class JobOut(BaseModel):
    id: str
    stage: str
    status: str
    error: Optional[str] = None
    artifact_version: Optional[int] = None


class MatchOut(BaseModel):
    id: str
    sm_match_id: Optional[str]
    capture_mode: str
    sport: str
    original_filename: Optional[str]
    pitch_length: float
    pitch_width: float
    kickoff_offset_ms: Optional[int]
    jobs: list[JobOut]
    artifacts: dict[str, list[int]]


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Sideline", version=__version__)
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.blobs = build_blobs(settings)
    app.state.artifacts = ArtifactStore(app.state.blobs)

    def artifacts(request: Request) -> ArtifactStore:
        return request.app.state.artifacts

    def _match_out(m: Match, store: ArtifactStore) -> MatchOut:
        return MatchOut(
            id=m.id, sm_match_id=m.sm_match_id, capture_mode=m.capture_mode, sport=m.sport,
            original_filename=m.original_filename, pitch_length=m.pitch_length, pitch_width=m.pitch_width,
            kickoff_offset_ms=m.kickoff_offset_ms,
            jobs=[JobOut(id=j.id, stage=j.stage, status=j.status, error=j.error, artifact_version=j.artifact_version)
                  for j in sorted(m.jobs, key=lambda j: j.created_at)],
            artifacts={stage: store.versions(m.id, stage) for stage in ORDER},
        )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "stages": ORDER}

    @app.post("/matches", response_model=MatchOut, status_code=201)
    async def create_match(
        request: Request,
        file: UploadFile = File(...),
        capture_mode: str = Form("followcam"),
        sm_match_id: Optional[str] = Form(None),
        pitch_length: float = Form(105.0),
        pitch_width: float = Form(68.0),
        kickoff_offset_ms: Optional[int] = Form(None),
        roster: Optional[str] = Form(None),
        stints: Optional[str] = Form(None),
        periods: Optional[str] = Form(None),
        stages: Optional[str] = Form(None),
        run: bool = Form(True),
    ) -> MatchOut:
        if capture_mode not in ("static", "followcam"):
            raise HTTPException(422, "capture_mode must be static or followcam")
        # No `stages` means every stage; `run=false` registers the match and
        # queues nothing, for a re-run later or a stage started by hand.
        wanted = [s for s in (stages or ",".join(ORDER)).split(",") if s] if run else []
        unknown = [s for s in wanted if s not in ORDER]
        if unknown:
            raise HTTPException(422, f"unknown stages {unknown}; known: {ORDER}")

        def _json(name: str, text: Optional[str]) -> Optional[dict]:
            if text is None or text == "":
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"{name} is not JSON: {e}") from None

        st: Settings = request.app.state.settings
        store: ArtifactStore = request.app.state.artifacts
        filename = Path(file.filename or "upload.mp4").name
        # Spool to disk first: a match is gigabytes, and the blob store wants a
        # file it can stream, not a body held in memory.
        tmp = Path(tempfile.mkdtemp(dir=st.workdir)) / filename
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=8 * 1024 * 1024)
        with session_scope(request.app.state.engine) as s:
            m = Match(sm_match_id=sm_match_id, capture_mode=capture_mode, source_key="", original_filename=filename,
                      pitch_length=pitch_length, pitch_width=pitch_width, kickoff_offset_ms=kickoff_offset_ms,
                      roster=_json("roster", roster), stints=_json("stints", stints), periods=_json("periods", periods))
            s.add(m)
            s.flush()
            m.source_key = f"matches/{m.id}/source/{filename}"
            request.app.state.blobs.put_file(m.source_key, tmp, file.content_type or "video/mp4")
            match_id = m.id
        shutil.rmtree(tmp.parent, ignore_errors=True)
        if wanted:
            enqueue_stages(st, match_id, wanted)
        with session_scope(request.app.state.engine) as s:
            return _match_out(s.get(Match, match_id), store)

    @app.get("/matches", response_model=list[MatchOut])
    def list_matches(request: Request) -> list[MatchOut]:
        store = request.app.state.artifacts
        with session_scope(request.app.state.engine) as s:
            return [_match_out(m, store) for m in s.query(Match).order_by(Match.created_at.desc()).all()]

    @app.get("/matches/{match_id}", response_model=MatchOut)
    def get_match(match_id: str, request: Request) -> MatchOut:
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            return _match_out(m, request.app.state.artifacts)

    @app.get("/matches/{match_id}/artifacts/{stage}/{version}")
    def get_manifest(match_id: str, stage: str, version: int, request: Request) -> dict:
        ref = ArtifactRef(match_id, stage, version)
        store: ArtifactStore = request.app.state.artifacts
        if not store.complete(ref):
            raise HTTPException(404, "no such artifact version")
        return {"manifest": store.read_manifest(ref), "files": store.list(ref)}

    @app.get("/matches/{match_id}/artifacts/{stage}/{version}/{name:path}")
    def get_artifact_json(match_id: str, stage: str, version: int, name: str, request: Request) -> dict:
        """JSON artifacts only (match.json, manifest.json). Video, frames and
        Parquet are read from the blob store by the stages and the review UI."""
        if not name.endswith(".json"):
            raise HTTPException(415, "only JSON artifacts are served here")
        ref = ArtifactRef(match_id, stage, version)
        store: ArtifactStore = request.app.state.artifacts
        if not store.blobs.exists(ref.key(name)):
            raise HTTPException(404, "no such artifact")
        return store.read_json(ref, name)

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, request: Request) -> JobOut:
        with session_scope(request.app.state.engine) as s:
            j = s.get(Job, job_id)
            if j is None:
                raise HTTPException(404, "no such job")
            return JobOut(id=j.id, stage=j.stage, status=j.status, error=j.error, artifact_version=j.artifact_version)

    return app


app = create_app()
