"""The API surface: accept an upload with its accompanying inputs, queue the
stages, answer what exists, and serve the review page. Raw video comes in
here and never leaves the homelab; only published aggregates go anywhere
else. The review routes hand sampled frames and boxes to a browser on the
same network, which is inside that boundary: this API is for the LAN or a
Tailscale network, never the open internet."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.compute as pc
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from sideline import __version__
from sideline.config import Settings, build_blobs, build_engine
from sideline.db.models import EventDecision, Job, Match, ReviewDecision, StageRun
from sideline.db.session import session_scope
from sideline.pipeline import enqueue_stages
from sideline.sports import get_sport
from sideline.stages import DEFAULT_STAGES, ORDER
from sideline.stages.s8_events import clock
from sideline.storage.artifacts import ArtifactRef, ArtifactStore
from sideline.timeline import periods_from_sm

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
VERDICTS = ("right", "wrong_type", "wrong_taker", "not_event")


class JobOut(BaseModel):
    id: str
    stage: str
    status: str
    error: Optional[str] = None
    artifact_version: Optional[int] = None


class DecisionIn(BaseModel):
    """A verdict on one event, keyed by the events version and the time."""

    events_version: int
    t_ms: int
    verdict: str
    corrected_type: Optional[str] = None
    corrected_track: Optional[int] = None
    decided_by: str = "coach"
    note: Optional[str] = None


class TrackNameIn(BaseModel):
    """Who a track is: the identity review decision, one per linked track."""

    tracks_version: int
    track_id: int
    player_id: Optional[str]          # null means "not a player"
    decided_by: str = "coach"
    note: Optional[str] = None


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

    def sport_of(match_id: str) -> str:
        with session_scope(app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            return m.sport

    app.state.sport_of = sport_of

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
        # No `stages` means the default ones (ingest and sampling; detection
        # is asked for by name because it wants a model and a GPU); `run=false`
        # registers the match and queues nothing, for a re-run later or a
        # stage started by hand.
        wanted = [s for s in (stages or ",".join(DEFAULT_STAGES)).split(",") if s] if run else []
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
    def get_artifact_json(match_id: str, stage: str, version: int, name: str, request: Request) -> Any:
        """JSON artifacts only (match.json, manifest.json, dead_balls.json).
        Video, frames and Parquet are read from the blob store by the stages
        and the review UI."""
        if not name.endswith(".json"):
            raise HTTPException(415, "only JSON artifacts are served here")
        ref = ArtifactRef(match_id, stage, version)
        store: ArtifactStore = request.app.state.artifacts
        if not store.blobs.exists(ref.key(name)):
            raise HTTPException(404, "no such artifact")
        return store.read_json(ref, name)

    # -- review: the page, its data, and the rows it writes -----------------

    @app.get("/review")
    def review_page() -> FileResponse:
        """The review page, served from the repository's web/ directory so
        it reaches this API on the same origin and nothing else."""
        page = WEB_DIR / "review.html"
        if not page.is_file():
            raise HTTPException(404, "web/review.html is not beside this install")
        return FileResponse(page, media_type="text/html")

    def _latest_complete(store: ArtifactStore, match_id: str, stage: str) -> Optional[ArtifactRef]:
        ref = store.latest(match_id, stage)
        return ref if ref is not None and store.complete(ref) else None

    @app.get("/matches/{match_id}/review")
    def review_doc(match_id: str, request: Request) -> dict:
        """Everything the page needs to start: the match, the sport's event
        vocabulary, the roster, every event of the latest events version
        with its verdict so far, and the versions the verdicts key on."""
        store: ArtifactStore = request.app.state.artifacts
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            sport, roster, periods_node, kickoff = m.sport, m.roster, m.periods, m.kickoff_offset_ms
            s8 = _latest_complete(store, match_id, "s8_events")
            decisions = (s.query(EventDecision).filter_by(match_id=match_id, events_version=s8.version)
                         .order_by(EventDecision.decided_at).all()) if s8 else []
            names = {d.track_id: d.player_id for d in s.query(ReviewDecision).filter_by(match_id=match_id)
                     .order_by(ReviewDecision.decided_at).all()}
        plugin = get_sport(sport)
        grammar = plugin.event_grammar(plugin.surface())
        s0 = _latest_complete(store, match_id, "s0_ingest")
        match_json = store.read_json(s0, "match.json") if s0 else {}
        events: list[dict] = []
        tracks_version = None
        if s8 is not None:
            manifest = store.read_manifest(s8)
            tracks_version = next((i["version"] for i in manifest.get("inputs", []) if i["stage"] == "s3_tracking"), None)
            rows = store.read_table(s8, "events.parquet").to_pylist()
            log = store.read_json(s8, "dead_balls.json")
            periods = periods_from_sm(periods_node or {})
            latest: dict[int, EventDecision] = {}
            for d in decisions:
                latest[d.t_ms] = d                    # the newest verdict per event wins
            for r, l in zip(rows, log):
                label, basis = clock(r["t_ms"], kickoff, periods)
                d = latest.get(r["t_ms"])
                events.append({
                    "t_ms": r["t_ms"], "clock": label, "clock_basis": basis, "type": r["type"],
                    "confidence": round(r["confidence"], 2), "reason": l["call"]["reason"],
                    "taker_track": r["track_id"], "player_id": r["player_id"] or names.get(r["track_id"]),
                    "team": r["team"], "cues": {k: l[k] for k in ("held", "still_s", "nearest_h", "n_near", "extreme_h", "landmarks_h", "release_speed_h")},
                    "decision": None if d is None else {
                        "verdict": d.verdict, "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                        "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat(), "note": d.note},
                })
        return {
            "match": {"id": match_id, "sport": sport, "capture_mode": match_json.get("capture_mode"),
                      "width": match_json.get("width"), "height": match_json.get("height"), "fps": match_json.get("fps"),
                      "duration_ms": match_json.get("duration_ms"), "kickoff_offset_ms": kickoff},
            "events_version": s8.version if s8 else None, "tracks_version": tracks_version,
            "event_types": list(grammar.restart_types), "roster": roster or {}, "track_names": names,
            "events": events,
        }

    @app.get("/matches/{match_id}/review/frames")
    def review_frames(match_id: str, request: Request, t_ms: int, span_s: float = 4.0) -> dict:
        """The sampled frames within `span_s` of a moment, each with the
        fragments seen in it, so the page can draw what the stages saw."""
        store: ArtifactStore = request.app.state.artifacts
        s1 = _latest_complete(store, match_id, "s1_sampling")
        s3 = _latest_complete(store, match_id, "s3_tracking")
        if s1 is None:
            raise HTTPException(404, "no sampled frames for this match")
        frames = store.read_table(s1, "frames.parquet")
        lo, hi = t_ms - span_s * 1000, t_ms + span_s * 1000
        window = frames.filter(pc.and_(pc.greater_equal(frames["t_ms"], int(lo)), pc.less_equal(frames["t_ms"], int(hi)))).to_pylist()
        indices = [f["frame_index"] for f in window]
        boxes: dict[int, list[dict]] = {i: [] for i in indices}
        if s3 is not None and indices:
            frags = store.read_table(s3, "fragments.parquet")
            hit = frags.filter(pc.is_in(frags["frame_index"], value_set=pa.array(indices, pa.int32()))).to_pylist()
            for r in hit:
                boxes[r["frame_index"]].append({k: r[k] for k in ("track_id", "cls", "x1", "y1", "x2", "y2", "conf")})
        plugin = get_sport(request.app.state.sport_of(match_id))
        kinds = {c.name: ("object" if c.object else "landmark" if c.landmark else "person") for c in plugin.entity_classes()}
        return {
            "frames": [{"frame_index": f["frame_index"], "t_ms": f["t_ms"], "cached": f["key"] is not None,
                        "url": f"/matches/{match_id}/frames/{f['frame_index']}.jpg", "boxes": boxes[f["frame_index"]]}
                       for f in window],
            "kinds": kinds,
        }

    @app.get("/matches/{match_id}/frames/{frame_index}.jpg")
    def frame_jpeg(match_id: str, frame_index: int, request: Request) -> Response:
        """One of S1's cached frames. Frames stay on this network; this route
        is for the review page on it."""
        store: ArtifactStore = request.app.state.artifacts
        s1 = _latest_complete(store, match_id, "s1_sampling")
        if s1 is None:
            raise HTTPException(404, "no sampled frames for this match")
        key = s1.key(f"frames/{frame_index:07d}.jpg")
        if not store.blobs.exists(key):
            raise HTTPException(404, "that frame was not cached")
        return Response(store.blobs.get(key), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/matches/{match_id}/review/decisions", status_code=201)
    def add_decision(match_id: str, body: DecisionIn, request: Request) -> dict:
        """A verdict becomes a row. The event itself is never edited."""
        if body.verdict not in VERDICTS:
            raise HTTPException(422, f"verdict must be one of {VERDICTS}")
        if body.verdict == "wrong_type" and not body.corrected_type:
            raise HTTPException(422, "wrong_type needs corrected_type")
        if body.verdict == "wrong_taker" and body.corrected_track is None:
            raise HTTPException(422, "wrong_taker needs corrected_track")
        with session_scope(request.app.state.engine) as s:
            if s.get(Match, match_id) is None:
                raise HTTPException(404, "no such match")
            d = EventDecision(match_id=match_id, events_version=body.events_version, t_ms=body.t_ms, verdict=body.verdict,
                              corrected_type=body.corrected_type, corrected_track=body.corrected_track,
                              decided_by=body.decided_by, note=body.note)
            s.add(d)
            s.flush()
            return {"id": d.id, "verdict": d.verdict, "t_ms": d.t_ms, "events_version": d.events_version,
                    "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                    "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat()}

    @app.get("/matches/{match_id}/review/decisions")
    def list_decisions(match_id: str, request: Request) -> list[dict]:
        """Every verdict ever given on this match, oldest first: the export
        that scores the rules and, one day, trains a classifier."""
        with session_scope(request.app.state.engine) as s:
            rows = s.query(EventDecision).filter_by(match_id=match_id).order_by(EventDecision.decided_at).all()
            return [{"id": d.id, "events_version": d.events_version, "t_ms": d.t_ms, "verdict": d.verdict,
                     "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                     "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat(), "note": d.note} for d in rows]

    @app.post("/matches/{match_id}/review/tracks", status_code=201)
    def name_track(match_id: str, body: TrackNameIn, request: Request) -> dict:
        """Who a track is: the identity decision the spec's review stage
        makes, one click per linked track, replayed by a better S6 later."""
        with session_scope(request.app.state.engine) as s:
            if s.get(Match, match_id) is None:
                raise HTTPException(404, "no such match")
            d = ReviewDecision(match_id=match_id, tracks_version=body.tracks_version, track_id=body.track_id,
                               player_id=body.player_id, decided_by=body.decided_by, note=body.note)
            s.add(d)
            s.flush()
            return {"id": d.id, "track_id": d.track_id, "player_id": d.player_id, "tracks_version": d.tracks_version}

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, request: Request) -> JobOut:
        with session_scope(request.app.state.engine) as s:
            j = s.get(Job, job_id)
            if j is None:
                raise HTTPException(404, "no such job")
            return JobOut(id=j.id, stage=j.stage, status=j.status, error=j.error, artifact_version=j.artifact_version)

    return app


app = create_app()
