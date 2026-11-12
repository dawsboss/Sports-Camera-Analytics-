"""Adding film from the landing page: a linked video is fetched by a job
before S0, uploads return while the stages run on a background thread, a
match another machine ingested can have stages run on it later, and a
database made before a column existed gains the column on start."""

import http.server
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from sideline.api.app import create_app
from sideline.config import Settings, build_blobs, build_engine, build_queue
from sideline.db import EventDecision, Job, Match, session_scope
from sideline.db.session import add_missing_columns
from sideline.pipeline import fetch_source, run_stages
from sideline.queue import ThreadQueue
from sideline.storage import ArtifactStore


@pytest.fixture
def served_clip(clip):
    """The synthetic clip on a local HTTP server, the way Veo's CDN serves a match."""
    handler = type("H", (http.server.SimpleHTTPRequestHandler,), {
        "log_message": lambda *a: None,
        "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(self, *a, directory=str(clip.parent), **k),
    })
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/{clip.name}"
    srv.shutdown()


def test_a_linked_video_is_fetched_then_ingested_inline(tmp_path, served_clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    with TestClient(create_app(settings)) as c:
        r = c.post("/matches", data={"video_url": served_clip + "?token=secret", "kickoff_offset_ms": "500"})
        assert r.status_code == 201, r.text
        m = r.json()
        assert [j["stage"] for j in m["jobs"]] == ["fetch", "s0_ingest", "s1_sampling"]
        assert all(j["status"] == "done" for j in m["jobs"]) and m["jobs"][0]["artifact_version"] is None
        # The link is shown without its query: a signed token stays on the server.
        assert m["source_url"] == served_clip and m["original_filename"] == "clip.mp4" and m["artifacts"]["s1_sampling"] == [1]
        assert not list((tmp_path / "data" / "work").glob("tmp*")), "the fetch left a spool directory behind"
        # The source landed in the blob store under the match.
        with session_scope(build_engine(settings)) as s:
            key = s.get(Match, m["id"]).source_key
        assert key == f"matches/{m['id']}/source/clip.mp4" and build_blobs(settings).exists(key)


def test_bad_film_requests_are_refused(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    with TestClient(create_app(settings)) as c:
        # Neither a file nor a link makes a match that waits for its video.
        r = c.post("/matches", data={"capture_mode": "followcam"})
        assert r.status_code == 201 and r.json()["jobs"] == []
        assert c.post("/matches", data={"video_url": "ftp://x/y.mp4"}).status_code == 422
        with clip.open("rb") as f:
            r = c.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"video_url": "http://x/y.mp4"})
        assert r.status_code == 422


def test_fetch_refuses_a_web_page_in_place_of_a_video(tmp_path, clip, served_clip):
    # The server's directory is the clip's; a page beside it is what a Veo
    # match link returns, and it is not a video.
    (clip.parent / "index.html").write_text("<html>not a video</html>")
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    with session_scope(build_engine(settings)) as s:
        m = Match(capture_mode="followcam", source_key="", source_url=served_clip.replace("clip.mp4", "index.html"))
        s.add(m)
        s.flush()
        mid = m.id
    with pytest.raises(ValueError, match="not a video"):
        fetch_source(settings, mid)
    assert not list((tmp_path / "data" / "work").glob("tmp*")), "a failed fetch left its spool behind"


def test_fetch_follows_redirects_only_on_http(tmp_path, clip):
    class Redirecting(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            target = "ftp://127.0.0.1/clip.mp4" if self.path == "/away" else f"http://127.0.0.1:{self.server.server_address[1]}/clip.mp4"
            if self.path in ("/away", "/hop"):
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            data = clip.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirecting)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    try:
        with session_scope(build_engine(settings)) as s:
            a = Match(capture_mode="followcam", source_key="", source_url=f"{base}/hop")
            b = Match(capture_mode="followcam", source_key="", source_url=f"{base}/away")
            s.add_all([a, b])
            s.flush()
            ida, idb = a.id, b.id
        # The file is named from the link the coach typed, redirects or not.
        assert fetch_source(settings, ida) == f"matches/{ida}/source/hop.mp4"
        with pytest.raises(ValueError, match="off http"):
            fetch_source(settings, idb)
        # Fetching again after the blob landed only writes the record.
        assert fetch_source(settings, ida) == f"matches/{ida}/source/hop.mp4"
    finally:
        srv.shutdown()


def test_uploads_return_while_the_thread_runs_the_stages(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", job_runner="thread")
    q = build_queue(settings)
    assert isinstance(q, ThreadQueue) and build_queue(settings) is q, "one queue per process"
    with TestClient(create_app(settings)) as c:
        with clip.open("rb") as f:
            r = c.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"kickoff_offset_ms": "500"})
        assert r.status_code == 201, r.text
        m = r.json()
        # Returned before the work finished (or just as it did on a fast machine).
        assert {j["status"] for j in m["jobs"]} <= {"queued", "running", "done"}
        assert q.wait(timeout=120), "the background thread did not drain"
        m = c.get(f"/matches/{m['id']}").json()
        assert [j["status"] for j in m["jobs"]] == ["done", "done"] and m["artifacts"]["s1_sampling"] == [1]
        # A failure is recorded on the job, and the thread carries on.
        with session_scope(build_engine(settings)) as s:
            s.get(Match, m["id"]).source_key = "matches/nowhere.mp4"
        r = c.post(f"/matches", data={"video_url": "http://127.0.0.1:9/none.mp4", "kickoff_offset_ms": "0"})
        assert r.status_code == 201
        assert q.wait(timeout=120)
        jobs = c.get(f"/matches/{r.json()['id']}").json()["jobs"]
        assert jobs[0]["stage"] == "fetch" and jobs[0]["status"] == "failed" and jobs[0]["error"]
        assert all(j["status"] == "failed" for j in jobs[1:]), "stages after a failed fetch cannot succeed"


def test_stages_run_later_on_an_existing_match(tmp_path, clip):
    from sideline.detection import ScriptedDetector
    from sideline.pipeline import ingest_local
    from tests.test_detect_track_events import throw_in_scene

    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=400)          # the homelab's half
    refs = run_stages(settings, match_id, ["s2_detection", "s3_tracking", "s8_events"],
                      config={"detector": ScriptedDetector(throw_in_scene)})   # the GPU machine's half
    assert [(r.stage, r.version) for r in refs] == [("s2_detection", 1), ("s3_tracking", 1), ("s8_events", 1)]
    store = ArtifactStore(build_blobs(settings))
    assert store.read_table(refs[-1], "events.parquet").num_rows == 1
    with pytest.raises(KeyError):
        run_stages(settings, "nope", ["s2_detection"])


def test_a_database_from_before_a_column_gains_it_on_start(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE matches (id VARCHAR(32) PRIMARY KEY, capture_mode VARCHAR(16), source_key VARCHAR(512))"))
        conn.execute(text("CREATE TABLE event_decisions (id VARCHAR(32) PRIMARY KEY, match_id VARCHAR(32), events_version INTEGER, "
                          "t_ms INTEGER, verdict VARCHAR(16), decided_by VARCHAR(128))"))
        conn.execute(text("INSERT INTO matches (id, capture_mode, source_key) VALUES ('old1', 'followcam', '')"))
    eng.dispose()
    settings = Settings(data_dir=tmp_path, db_url=url)
    engine = build_engine(settings)
    cols = {c["name"] for c in inspect(engine).get_columns("matches")}
    assert {"source_url", "roster", "periods", "kickoff_offset_ms"} <= cols
    assert "tracks_version" in {c["name"] for c in inspect(engine).get_columns("event_decisions")}
    assert add_missing_columns(engine) == []          # a second start adds nothing
    with session_scope(engine) as s:
        old = s.get(Match, "old1")
        # Old rows take the model's plain defaults rather than reading None.
        assert old.pitch_length == 105.0 and old.pitch_width == 68.0 and old.sport == "soccer" and old.source_url is None
        s.add(Match(capture_mode="followcam", source_key="", source_url="http://x/y.mp4"))


def test_blobs_stream_without_a_spool_and_leave_nothing_on_failure(tmp_path):
    from sideline.storage import ImmutableWriteError, LocalBlobStore

    store = LocalBlobStore(tmp_path / "blobs")
    n = store.put_stream("a/b.bin", iter([b"ab", b"cd", b""]), "application/octet-stream")
    assert n == 4 and store.get("a/b.bin") == b"abcd"
    with pytest.raises(ImmutableWriteError):
        store.put_stream("a/b.bin", iter([b"x"]))

    def broken():
        yield b"part"
        raise OSError("the network went")

    with pytest.raises(OSError):
        store.put_stream("a/c.bin", broken())
    assert not store.exists("a/c.bin") and not list((tmp_path / "blobs" / "a").glob("*.part"))


def test_the_video_can_be_streamed_to_a_match_after_its_details(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    with TestClient(create_app(settings)) as c:
        r = c.post("/matches", data={"capture_mode": "followcam", "kickoff_offset_ms": "500"})
        assert r.status_code == 201, r.text
        m = r.json()
        assert m["jobs"] == [] and m["original_filename"] is None       # waiting for its video
        with clip.open("rb") as f:
            r = c.put(f"/matches/{m['id']}/source", params={"filename": "clip.mp4"}, content=f.read(),
                      headers={"Content-Type": "video/mp4"})
        assert r.status_code == 200, r.text
        m = r.json()
        assert [j["stage"] for j in m["jobs"]] == ["s0_ingest", "s1_sampling"] and all(j["status"] == "done" for j in m["jobs"])
        assert m["original_filename"] == "clip.mp4" and m["artifacts"]["s1_sampling"] == [1]
        # Every job reports how far it got and what it did.
        assert all(j["progress"] == 1.0 for j in m["jobs"])
        assert m["jobs"][1]["detail"].startswith("sampled 15 frames")
        # A second video for the same match is refused; an empty one too; an unknown match 404s.
        assert c.put(f"/matches/{m['id']}/source", content=b"xx").status_code == 409
        assert c.put("/matches/nope/source", content=b"xx").status_code == 404
        r = c.post("/matches", data={"capture_mode": "followcam"})
        assert c.put(f"/matches/{r.json()['id']}/source", content=b"").status_code == 422
        # The work directory holds no copy of the upload.
        assert not list((tmp_path / "data" / "work").glob("**/*.mp4"))


def test_s0_reports_transcode_progress_when_ffmpeg_is_there(tmp_path, clip):
    import shutil as _sh

    from sideline.pipeline import ingest_local, record_of, run_stage

    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=0, stages=[])
    seen = []
    with session_scope(build_engine(settings)) as s:
        rec = record_of(s.get(Match, match_id))
    run_stage(settings, rec, "s0_ingest", {"detect_halftime": False}, progress=lambda f, d: seen.append((f, d)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0 and seen[-1][1] == "ingested"
    if _sh.which("ffmpeg"):
        assert any(d.startswith("transcoding") for _, d in seen), seen
