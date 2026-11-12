"""The worker beside the API: the API queues a job as a row, a separate
process claims the oldest queued row and runs it, and two processes on one
SQLite file do not trip over each other."""

from fastapi.testclient import TestClient
from sqlalchemy import text

from sideline.api.app import create_app
from sideline.config import Settings, build_engine, build_queue
from sideline.db import Job, Match, session_scope
from sideline.queue import DbQueue
from sideline.worker import claim_next_job, main, run_queued_jobs_once


def test_the_api_queues_and_the_worker_runs_in_order(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", job_runner="db")
    assert isinstance(build_queue(settings), DbQueue)
    with TestClient(create_app(settings)) as c:
        with clip.open("rb") as f:
            r = c.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"kickoff_offset_ms": "500"})
        assert r.status_code == 201
        m = r.json()
        assert [j["status"] for j in m["jobs"]] == ["queued", "queued"], "the API only queues"
        # The worker takes the oldest first, so S0 runs before S1.
        first = run_queued_jobs_once(settings)
        with session_scope(build_engine(settings)) as s:
            assert s.get(Job, first).stage == "s0_ingest" and s.get(Job, first).status == "done"
        assert run_queued_jobs_once(settings) is not None
        assert run_queued_jobs_once(settings) is None
        m = c.get(f"/matches/{m['id']}").json()
        assert [j["status"] for j in m["jobs"]] == ["done", "done"] and m["artifacts"]["s1_sampling"] == [1]


def test_a_claim_is_exclusive_and_a_failure_does_not_stop_the_loop(tmp_path, clip, capsys):
    settings = Settings(data_dir=tmp_path / "data", job_runner="db")
    with TestClient(create_app(settings)) as c:
        r = c.post("/matches", data={"video_url": "http://127.0.0.1:9/none.mp4"})
        mid = r.json()["id"]
    with session_scope(build_engine(settings)) as s:
        ids = [j.id for j in s.query(Job).filter_by(match_id=mid).order_by(Job.created_at)]
    assert len(ids) == 3
    a = claim_next_job(settings)
    assert a == ids[0]
    with session_scope(build_engine(settings)) as s:
        assert s.get(Job, a).status == "claimed"
    # Claimed rows are not queued, so the next claim is the next row.
    assert claim_next_job(settings) == ids[1]
    with session_scope(build_engine(settings)) as s:
        for i in ids[:2]:
            s.get(Job, i).status = "queued"
    # --once drains the queue: the fetch fails (nothing listens on port 9) and
    # the stages after it fail for want of a source, and the loop still ends.
    assert main(settings, once=True) == 0
    assert "ran 3 job(s)" in capsys.readouterr().out
    with session_scope(build_engine(settings)) as s:
        statuses = [s.get(Job, i).status for i in ids]
        assert statuses == ["failed", "failed", "failed"] and s.get(Job, ids[0]).error


def test_detector_settings_reach_the_stage_config(tmp_path):
    plain = Settings(data_dir=tmp_path)
    assert "detectors" not in plain.stage_defaults()
    gpu = Settings(data_dir=tmp_path, detector_weights="/models/best.pt", detector_imgsz=1280, detector_device="0")
    assert gpu.stage_defaults()["detectors"] == [{"weights": "/models/best.pt", "imgsz": 1280, "device": "0"}]
    import os
    os.environ.update({"SIDELINE_DETECTOR_WEIGHTS": "w.pt", "SIDELINE_DETECTOR_IMGSZ": "960"})
    try:
        env = Settings.from_env(data_dir=tmp_path)
        assert env.stage_defaults()["detectors"] == [{"weights": "w.pt", "imgsz": 960}]
    finally:
        del os.environ["SIDELINE_DETECTOR_WEIGHTS"], os.environ["SIDELINE_DETECTOR_IMGSZ"]


def test_a_file_database_uses_write_ahead_logging(tmp_path):
    engine = build_engine(Settings(data_dir=tmp_path / "data"))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_the_run_button_queues_detection_for_an_existing_match(tmp_path, clip):
    from sideline.pipeline import ingest_local

    settings = Settings(data_dir=tmp_path / "data", job_runner="db")
    match_id = ingest_local(settings, clip, kickoff_offset_ms=400)          # ingested and sampled, inline
    with TestClient(create_app(settings)) as c:
        assert c.post("/matches/nope/jobs").status_code == 404
        assert c.post(f"/matches/{match_id}/jobs", params={"stages": "s9_publish"}).status_code == 422
        r = c.post(f"/matches/{match_id}/jobs")
        assert r.status_code == 201, r.text
        assert [(j["stage"], j["status"]) for j in r.json()["jobs"]] == [
            ("s2_detection", "queued"), ("s3_tracking", "queued"), ("s8_events", "queued")]
        # A second tap while work is queued is refused rather than doubled.
        assert c.post(f"/matches/{match_id}/jobs").status_code == 409
        # A match with no video yet cannot be run.
        empty = c.post("/matches", data={"capture_mode": "followcam"}).json()["id"]
        assert c.post(f"/matches/{empty}/jobs").status_code == 409
    # The worker then runs them; detection fails here for want of a model,
    # which is recorded on the job and shown on the page as a failure.
    assert main(settings, once=True) == 0
    with session_scope(build_engine(settings)) as s:
        jobs = s.query(Job).filter_by(match_id=match_id).order_by(Job.created_at).all()
        assert jobs[0].stage == "s2_detection" and jobs[0].status == "failed" and "ultralytics" in (jobs[0].error or "")
