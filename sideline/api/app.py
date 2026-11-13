"""The API surface: accept an upload with its accompanying inputs, queue the
stages, answer what exists, and serve the review page. Raw video comes in
here and nothing derived from it leaves the network; only published
aggregates go anywhere else. The review routes hand S1's sampled frames
and S3's boxes to a browser on the same network, which is inside that
boundary and is most of the footage at 5 fps, so this API is for the LAN or
a Tailscale network only: never the open internet, and not behind a public
tunnel. Video and Parquet are never served."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.compute as pc
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from sideline import __version__
from sideline.config import Settings, build_blobs, build_engine
from sideline.db.models import EventDecision, Job, Match, PeriodMark, ReviewDecision, StageRun
from sideline.db.session import session_scope
from sideline.pipeline import FETCH, enqueue_stages
from sideline.possession import holders
from sideline.sports import get_sport
from sideline.stages import DEFAULT_STAGES, ORDER
from sideline.stages.s8_events import clock
from sideline.storage.artifacts import ArtifactRef, ArtifactStore
from sideline.storage.blobs import ImmutableWriteError
from sideline.timeline import MARK_KINDS, MARK_SLACK_MS, periods_from_marks, periods_from_sm
from sideline.verdicts import carry, seen

VERDICTS = ("right", "wrong_type", "wrong_taker", "not_event")
# The page must not reach anything but this API, and a script that slipped
# into it must not be able to send frames anywhere: images and requests are
# same-origin by policy, not by convention. Inline script and style are the
# page's own single file.
REVIEW_CSP = ("default-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; "
              "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")


class UploadIncomplete(ValueError):
    """The body ended before its declared length, or was empty."""


async def stream_body_to_blob(blobs, key: str, body, content_type: str, expected: Optional[int]) -> int:
    """Hand an incoming body to the blob store chunk by chunk from a writer
    thread, so the request loop is never blocked on disk and the writer
    never holds more than a few chunks. Any failure on either side, a
    dropped client included, reaches the other: the writer gets a poison
    pill that raises inside its iterator, so `put_stream` removes its
    `.part` and no blob is left; a writer that fails early is noticed by
    the producer within a second rather than hanging it. Returns the bytes
    written."""
    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[object]" = asyncio.Queue(maxsize=4)
    done: "asyncio.Future[int]" = loop.create_future()
    END = object()

    def consume() -> None:
        def gen():
            total = 0
            while True:
                item = asyncio.run_coroutine_threadsafe(q.get(), loop).result()
                if item is END:
                    if total == 0:
                        raise UploadIncomplete("the upload was empty")
                    if expected is not None and total != expected:
                        raise UploadIncomplete(f"the upload ended after {total} of {expected} bytes")
                    return
                if isinstance(item, BaseException):
                    raise item
                total += len(item)
                yield item
        try:
            n = blobs.put_stream(key, gen(), content_type)
            loop.call_soon_threadsafe(done.set_result, n)
        except BaseException as e:
            loop.call_soon_threadsafe(done.set_exception, e)

    threading.Thread(target=consume, name="sideline-upload", daemon=True).start()

    async def hand(item) -> None:
        # Offer to the writer, but notice if it has died and raise its reason.
        while True:
            try:
                await asyncio.wait_for(q.put(item), timeout=1.0)
                return
            except asyncio.TimeoutError:
                if done.done():
                    done.result()

    try:
        async for chunk in body:
            if chunk:
                await hand(chunk)
        await hand(END)
    except BaseException as e:
        if not done.done():
            try:
                await asyncio.wait_for(q.put(e if isinstance(e, Exception) else UploadIncomplete(str(e))), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(done), timeout=30.0)
            except BaseException:
                pass
        raise
    return await done


def web_dir() -> Path:
    """Where the pages live: beside the source tree in a checkout, or where
    `SIDELINE_WEB_DIR` says, which the Docker image sets because a plain
    install puts this module under site-packages and not beside web/."""
    import os

    env = os.environ.get("SIDELINE_WEB_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "web"


class JobOut(BaseModel):
    id: str
    stage: str
    status: str
    error: Optional[str] = None
    artifact_version: Optional[int] = None
    progress: Optional[float] = None
    detail: Optional[str] = None


class DecisionIn(BaseModel):
    """A verdict on one event, keyed by the events version and the time.
    The tracks version is the one that events version was built on; the
    API checks it rather than trusting it."""

    events_version: int
    t_ms: int
    verdict: str
    corrected_type: Optional[str] = None
    corrected_track: Optional[int] = None
    decided_by: str = "coach"
    note: Optional[str] = None


class MarkIn(BaseModel):
    """A moment marked on the film: a period starts, play breaks off
    between periods, or the match ends. Video time."""

    kind: str
    t_ms: int
    decided_by: str = "coach"


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
    source_url: Optional[str] = None
    created_at: Optional[str] = None
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
    app.state.uploading = set()          # matches whose video is arriving right now

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
            # A signed CDN link carries its token in the query; the browser
            # needs only where the film came from, not the right to fetch it.
            original_filename=m.original_filename, source_url=m.source_url.split("?")[0] if m.source_url else None,
            created_at=m.created_at.isoformat() if m.created_at else None,
            pitch_length=m.pitch_length, pitch_width=m.pitch_width,
            kickoff_offset_ms=m.kickoff_offset_ms,
            jobs=[JobOut(id=j.id, stage=j.stage, status=j.status, error=j.error, artifact_version=j.artifact_version,
                         progress=j.progress, detail=j.detail)
                  for j in sorted(m.jobs, key=lambda j: j.created_at)],
            artifacts={stage: store.versions(m.id, stage) for stage in ORDER},
        )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "stages": ORDER}

    @app.post("/matches", response_model=MatchOut, status_code=201)
    async def create_match(
        request: Request,
        file: Optional[UploadFile] = File(None),
        video_url: Optional[str] = Form(None),
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
        has_file = file is not None and bool(file.filename)
        link = (video_url or "").strip()
        if has_file and link:
            raise HTTPException(422, "send either a video file or a direct link to one, not both")
        if link and not (link.startswith("http://") or link.startswith("https://")):
            raise HTTPException(422, "video_url must be an http(s) link to the video file itself")
        # No `stages` means the default ones (ingest and sampling; detection
        # is asked for by name because it wants a model and a GPU); `run=false`
        # registers the match and queues nothing, for a re-run later or a
        # stage started by hand. A linked video is fetched by a job before S0.
        # With neither a file nor a link the match waits for its video to be
        # streamed to PUT /matches/{id}/source, and the jobs queue then.
        wanted = [s for s in (stages or ",".join(DEFAULT_STAGES)).split(",") if s] if run else []
        unknown = [s for s in wanted if s not in ORDER]
        if unknown:
            raise HTTPException(422, f"unknown stages {unknown}; known: {ORDER}")
        if link and wanted:
            wanted = [FETCH] + wanted
        if not has_file and not link:
            wanted = []

        def _json(name: str, text: Optional[str]) -> Optional[dict]:
            if text is None or text == "":
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"{name} is not JSON: {e}") from None

        st: Settings = request.app.state.settings
        store: ArtifactStore = request.app.state.artifacts
        if has_file:
            filename = Path(file.filename or "upload.mp4").name
        elif link:
            filename = Path(link.split("?")[0].rstrip("/")).name or "match.mp4"
        else:
            filename = None
        with session_scope(request.app.state.engine) as s:
            m = Match(sm_match_id=sm_match_id, capture_mode=capture_mode, source_key="", original_filename=filename,
                      source_url=link or None,
                      pitch_length=pitch_length, pitch_width=pitch_width, kickoff_offset_ms=kickoff_offset_ms,
                      roster=_json("roster", roster), stints=_json("stints", stints), periods=_json("periods", periods))
            s.add(m)
            s.flush()
            match_id = m.id
        if has_file:
            # Straight from the parsed upload into the blob store, chunk by
            # chunk; nothing else is copied.
            key = f"matches/{match_id}/source/{filename}"
            request.app.state.blobs.put_stream(key, iter(lambda: file.file.read(8 * 1024 * 1024), b""),
                                               file.content_type or "video/mp4")
            with session_scope(request.app.state.engine) as s:
                s.get(Match, match_id).source_key = key
        if wanted:
            enqueue_stages(st, match_id, wanted)
        with session_scope(request.app.state.engine) as s:
            return _match_out(s.get(Match, match_id), store)

    @app.put("/matches/{match_id}/source", response_model=MatchOut)
    async def put_source(match_id: str, request: Request, filename: str = "match.mp4",
                         stages: Optional[str] = None, run: bool = True) -> MatchOut:
        """The video for a match created without one, streamed as the raw
        request body into the blob store as it arrives: no multipart
        parsing, no spool, no copy, flat memory however large the match.
        The browser sends this after the details, and the stages queue when
        the last byte lands. A dropped connection, a short body or a second
        upload for the same match leaves no blob behind."""
        store: ArtifactStore = request.app.state.artifacts
        blobs = request.app.state.blobs
        name = Path(filename).name or "match.mp4"
        key = f"matches/{match_id}/source/{name}"
        wanted = [x for x in (stages or ",".join(DEFAULT_STAGES)).split(",") if x] if run else []
        unknown = [x for x in wanted if x not in ORDER]
        if unknown:
            raise HTTPException(422, f"unknown stages {unknown}; known: {ORDER}")
        length = request.headers.get("content-length")
        expected = int(length) if length and length.isdigit() else None
        if expected == 0:
            raise HTTPException(422, "the upload was empty")
        uploading: set = request.app.state.uploading
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            # Its video is whatever already sits under source/, finished or
            # still arriving; a match gets one.
            if blobs.list(f"matches/{match_id}/source/") or match_id in uploading:
                raise HTTPException(409, "this match already has its video")
        uploading.add(match_id)
        try:
            written = await stream_body_to_blob(blobs, key, request.stream(),
                                                request.headers.get("content-type") or "video/mp4", expected)
        except ImmutableWriteError:
            raise HTTPException(409, "this match already has its video")
        except UploadIncomplete as e:
            raise HTTPException(422, str(e))
        finally:
            uploading.discard(match_id)
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            m.source_key = key
            m.original_filename = m.original_filename or name
        if wanted:
            enqueue_stages(request.app.state.settings, match_id, wanted)
        with session_scope(request.app.state.engine) as s:
            return _match_out(s.get(Match, match_id), store)

    @app.post("/matches/{match_id}/jobs", response_model=MatchOut, status_code=201)
    def queue_stages(match_id: str, request: Request, stages: Optional[str] = None) -> MatchOut:
        """Queue stages for a match that exists: the landing page's "run
        detection here" button. The worker, or the API's own thread, picks
        them up; a match with a job already queued or running is left alone
        so a double tap cannot start the same work twice."""
        store: ArtifactStore = request.app.state.artifacts
        wanted = [x for x in (stages or "s2_detection,s3_tracking,s8_events").split(",") if x]
        unknown = [x for x in wanted if x not in ORDER]
        if unknown:
            raise HTTPException(422, f"unknown stages {unknown}; known: {ORDER}")
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            if any(j.status in ("queued", "claimed", "running") for j in m.jobs):
                raise HTTPException(409, "this match already has work queued or running")
            if not m.source_key:
                raise HTTPException(409, "this match has no video yet")
        enqueue_stages(request.app.state.settings, match_id, wanted)
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
        Video and Parquet are read from the blob store by the stages and are
        never served; S1's sampled frames have their own route, for the
        review page on this network."""
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
        """The review page, served with a policy that keeps it on this
        origin: it can fetch from and show images of this API and nothing
        else, whatever text a roster or a verdict carries."""
        page = web_dir() / "review.html"
        if not page.is_file():
            raise HTTPException(404, f"review.html not found under {web_dir()}; set SIDELINE_WEB_DIR")
        return FileResponse(page, media_type="text/html", headers={"Content-Security-Policy": REVIEW_CSP})

    def _latest_complete(store: ArtifactStore, match_id: str, stage: str) -> Optional[ArtifactRef]:
        ref = store.latest(match_id, stage)
        return ref if ref is not None and store.complete(ref) else None

    def _events_of(store: ArtifactStore, match_id: str, version: int) -> tuple[ArtifactRef, dict, list[dict], list[dict]]:
        """A complete events version with its manifest, rows and cue log,
        or a 404/422 that says what is wrong."""
        ref = ArtifactRef(match_id, "s8_events", version)
        if not store.complete(ref):
            raise HTTPException(422, f"no complete s8_events version {version} for this match")
        manifest = store.read_manifest(ref)
        return ref, manifest, store.read_table(ref, "events.parquet").to_pylist(), store.read_json(ref, "dead_balls.json")

    def _tracks_version_of(manifest: dict) -> Optional[int]:
        return next((i["version"] for i in manifest.get("inputs", []) if i["stage"] == "s3_tracking"), None)

    @app.get("/matches/{match_id}/review")
    def review_doc(match_id: str, request: Request) -> dict:
        """Everything the page needs to start: the match, the sport's event
        vocabulary, the roster, every event of the latest events version
        with its verdict so far, and the versions the verdicts key on."""
        store: ArtifactStore = request.app.state.artifacts
        s8 = _latest_complete(store, match_id, "s8_events")
        tracks_version = _tracks_version_of(store.read_manifest(s8)) if s8 else None
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            sport, roster, periods_node, kickoff = m.sport, m.roster, m.periods, m.kickoff_offset_ms
            marks = [{"id": k.id, "kind": k.kind, "t_ms": k.t_ms, "decided_by": k.decided_by} for k in m.marks]
            decisions = (s.query(EventDecision).filter_by(match_id=match_id, events_version=s8.version)
                         .order_by(EventDecision.decided_at).all()) if s8 else []
            # Verdicts given on earlier events versions, newest version
            # first, each as its newest verdict per event: {version: {t_ms: verdict}}.
            earlier: dict[int, dict[int, dict]] = {}
            for d in (s.query(EventDecision).filter(EventDecision.match_id == match_id,
                                                    EventDecision.events_version < s8.version)
                      .order_by(EventDecision.decided_at).all()) if s8 else []:
                earlier.setdefault(d.events_version, {})[d.t_ms] = {
                    "verdict": d.verdict, "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                    "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat(), "note": d.note}
            # A name belongs to a track of one tracking version; the same id
            # in a re-run is a different player. Only this version's names apply.
            names = {d.track_id: d.player_id for d in s.query(ReviewDecision)
                     .filter_by(match_id=match_id, tracks_version=tracks_version)
                     .order_by(ReviewDecision.decided_at).all()} if tracks_version is not None else {}
        plugin = get_sport(sport)
        grammar = plugin.event_grammar(plugin.surface())
        s0 = _latest_complete(store, match_id, "s0_ingest")
        match_json = store.read_json(s0, "match.json") if s0 else {}
        # What the reviewer marked on the film is the clock where it exists,
        # and says which events fall outside play. The events themselves are
        # as the stage wrote them until it runs again.
        periods = periods_from_sm(periods_node or {})
        marked = periods_from_marks(marks, kickoff)
        if marked is not None:
            kickoff, periods = marked

        def in_play(t_ms: int) -> bool:
            if marked is None:
                return True
            return any(p.start_ms - MARK_SLACK_MS <= t_ms and (p.end_ms is None or t_ms < p.end_ms) for p in periods)

        for k in marks:
            k["clock"] = clock(k["t_ms"], kickoff, periods)[0]
        events: list[dict] = []
        if s8 is not None:
            rows = store.read_table(s8, "events.parquet").to_pylist()
            log = store.read_json(s8, "dead_balls.json")
            latest: dict[int, EventDecision] = {}
            for d in decisions:
                latest[d.t_ms] = d                    # the newest verdict per event wins
            # A re-run starts a new version; what was said about the old
            # one is read through it by time and shown as carried. The
            # rows stay where they were given (`sideline/verdicts.py`).
            carried: dict[int, dict] = {}
            now = seen(rows, log)
            for version in sorted(earlier, reverse=True):
                old_ref = ArtifactRef(match_id, "s8_events", version)
                if not store.complete(old_ref):
                    continue
                was = {e.t_ms: e for e in seen(store.read_table(old_ref, "events.parquet").to_pylist(),
                                              store.read_json(old_ref, "dead_balls.json"))}
                pairs = [(was[t], v) for t, v in earlier[version].items() if t in was]
                same_tracks = _tracks_version_of(store.read_manifest(old_ref)) == tracks_version
                for t, c in carry(pairs, [e for e in now if e.t_ms not in latest and e.t_ms not in carried], same_tracks).items():
                    given = earlier[version][c["from_t_ms"]]
                    carried[t] = {"verdict": c["verdict"], "corrected_type": c["corrected_type"],
                                  "corrected_track": c["corrected_track"], "decided_by": given["decided_by"],
                                  "decided_at": given["decided_at"], "note": given["note"],
                                  "carried_from": {"events_version": version, "t_ms": c["from_t_ms"]}}
            for r, l in zip(rows, log):
                label, basis = clock(r["t_ms"], kickoff, periods)
                d = latest.get(r["t_ms"])
                events.append({
                    "t_ms": r["t_ms"], "clock": label, "clock_basis": basis, "type": r["type"],
                    "confidence": round(r["confidence"], 2), "reason": l["call"]["reason"],
                    "taker_track": r["track_id"], "player_id": r["player_id"] or names.get(r["track_id"]),
                    "in_play": in_play(r["t_ms"]),
                    "team": r["team"], "cues": {k: l[k] for k in ("held", "still_s", "nearest_h", "n_near", "extreme_h", "landmarks_h", "release_speed_h")},
                    "decision": carried.get(r["t_ms"]) if d is None else {
                        "verdict": d.verdict, "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                        "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat(), "note": d.note,
                        "carried_from": None},
                })
        return {
            "match": {"id": match_id, "sport": sport, "capture_mode": match_json.get("capture_mode"),
                      "width": match_json.get("width"), "height": match_json.get("height"), "fps": match_json.get("fps"),
                      "duration_ms": match_json.get("duration_ms"), "kickoff_offset_ms": kickoff},
            "events_version": s8.version if s8 else None, "tracks_version": tracks_version,
            "event_types": list(grammar.restart_types), "roster": roster or {}, "track_names": names,
            "marks": marks, "period_words": dict(grammar.period_words),
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
        # Who had the ball in each sample, from the latest events version
        # that wrote spells and was built on the fragments drawn here: a
        # track id means nothing against another tracking version.
        has_ball: list[Optional[int]] = [None] * len(window)
        s8 = _latest_complete(store, match_id, "s8_events")
        if (s8 is not None and s3 is not None and store.blobs.exists(s8.key("possession.json"))
                and _tracks_version_of(store.read_manifest(s8)) == s3.version):
            has_ball = holders(store.read_json(s8, "possession.json")["spells"], [f["t_ms"] for f in window])
        return {
            "frames": [{"frame_index": f["frame_index"], "t_ms": f["t_ms"], "cached": f["key"] is not None,
                        "url": f"/matches/{match_id}/frames/{f['frame_index']}.jpg", "boxes": boxes[f["frame_index"]],
                        "has_ball": h}
                       for f, h in zip(window, has_ball)],
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

    def _decision_out(d: EventDecision) -> dict:
        return {"id": d.id, "events_version": d.events_version, "tracks_version": d.tracks_version, "t_ms": d.t_ms,
                "verdict": d.verdict, "corrected_type": d.corrected_type, "corrected_track": d.corrected_track,
                "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat(), "note": d.note}

    @app.post("/matches/{match_id}/review/decisions", status_code=201)
    def add_decision(match_id: str, body: DecisionIn, request: Request) -> dict:
        """A verdict becomes a row. The event itself is never edited. These
        rows are the labelled set that will score the rules and train a
        classifier, so what goes in is checked against what exists: a real
        events version, an event in it, a kind the sport knows, a track of
        the tracking version that events version was built on."""
        store: ArtifactStore = request.app.state.artifacts
        sport = request.app.state.sport_of(match_id)          # 404 for a match this database does not know
        if body.verdict not in VERDICTS:
            raise HTTPException(422, f"verdict must be one of {VERDICTS}")
        _, manifest, rows, _ = _events_of(store, match_id, body.events_version)
        if not any(r["t_ms"] == body.t_ms for r in rows):
            raise HTTPException(422, f"no event at {body.t_ms} ms in s8_events v{body.events_version}")
        tracks_version = _tracks_version_of(manifest)
        plugin = get_sport(sport)
        kinds = plugin.event_grammar(plugin.surface()).event_types
        if body.verdict == "wrong_type":
            if not body.corrected_type:
                raise HTTPException(422, "wrong_type needs corrected_type")
            if body.corrected_type not in kinds:
                raise HTTPException(422, f"{body.corrected_type!r} is not an event type of {sport}: {list(kinds)}")
        if body.verdict == "wrong_taker":
            if body.corrected_track is None:
                raise HTTPException(422, "wrong_taker needs corrected_track")
            if body.corrected_type:
                raise HTTPException(422, "a corrected kind is wrong_type, which may carry corrected_track as well")
        # Kind and taker can both be wrong: wrong_type may name the taker too.
        if body.verdict in ("wrong_type", "wrong_taker") and body.corrected_track is not None:
            if tracks_version is None:
                raise HTTPException(422, "this events version records no tracking version to name a track in")
            frags = store.read_table(ArtifactRef(match_id, "s3_tracking", tracks_version), "fragments.parquet")
            if not pc.any(pc.equal(frags["track_id"], body.corrected_track)).as_py():
                raise HTTPException(422, f"track {body.corrected_track} is not in s3_tracking v{tracks_version}")
        with session_scope(request.app.state.engine) as s:
            d = EventDecision(match_id=match_id, events_version=body.events_version, tracks_version=tracks_version or 0,
                              t_ms=body.t_ms, verdict=body.verdict, corrected_type=body.corrected_type,
                              corrected_track=body.corrected_track, decided_by=body.decided_by, note=body.note)
            s.add(d)
            s.flush()
            return _decision_out(d)

    @app.get("/matches/{match_id}/review/decisions")
    def list_decisions(match_id: str, request: Request) -> list[dict]:
        """Every verdict ever given on this match, oldest first: the export
        that scores the rules and, one day, trains a classifier."""
        with session_scope(request.app.state.engine) as s:
            rows = s.query(EventDecision).filter_by(match_id=match_id).order_by(EventDecision.decided_at).all()
            return [_decision_out(d) for d in rows]

    @app.post("/matches/{match_id}/review/marks", status_code=201)
    def add_mark(match_id: str, body: MarkIn, request: Request) -> dict:
        """Mark a moment of the film as a period starting, play breaking
        off between periods, or the match ending. The page shows events
        outside the marked periods as out of play at once; the events
        stage reads the marks the next time it runs."""
        if body.kind not in MARK_KINDS:
            raise HTTPException(422, f"kind must be one of {MARK_KINDS}")
        if body.t_ms < 0:
            raise HTTPException(422, "t_ms is video time and cannot be negative")
        with session_scope(request.app.state.engine) as s:
            if s.get(Match, match_id) is None:
                raise HTTPException(404, "no such match")
            k = PeriodMark(match_id=match_id, kind=body.kind, t_ms=body.t_ms, decided_by=body.decided_by)
            s.add(k)
            s.flush()
            return {"id": k.id, "kind": k.kind, "t_ms": k.t_ms, "decided_by": k.decided_by}

    @app.delete("/matches/{match_id}/review/marks/{mark_id}")
    def remove_mark(match_id: str, mark_id: str, request: Request) -> dict:
        """Take back a mark set by mistake."""
        with session_scope(request.app.state.engine) as s:
            k = s.get(PeriodMark, mark_id)
            if k is None or k.match_id != match_id:
                raise HTTPException(404, "no such mark on this match")
            s.delete(k)
        return {"removed": mark_id}

    @app.post("/matches/{match_id}/review/tracks", status_code=201)
    def name_track(match_id: str, body: TrackNameIn, request: Request) -> dict:
        """Who a track is: the identity decision the spec's review stage
        makes, one click per linked track, replayed by a better S6 later.
        The track must exist in that tracking version, and a player must be
        on the copied roster when there is one."""
        store: ArtifactStore = request.app.state.artifacts
        ref = ArtifactRef(match_id, "s3_tracking", body.tracks_version)
        if not store.complete(ref):
            raise HTTPException(422, f"no complete s3_tracking version {body.tracks_version} for this match")
        frags = store.read_table(ref, "fragments.parquet")
        if not pc.any(pc.equal(frags["track_id"], body.track_id)).as_py():
            raise HTTPException(422, f"track {body.track_id} is not in s3_tracking v{body.tracks_version}")
        with session_scope(request.app.state.engine) as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404, "no such match")
            if body.player_id is not None and m.roster and body.player_id not in m.roster:
                raise HTTPException(422, f"{body.player_id!r} is not on this match's roster")
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
