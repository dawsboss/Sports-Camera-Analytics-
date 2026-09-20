from sideline.config import Settings
from sideline.db import Job, Match, StageRun, session_scope
from sideline.config import build_engine
from sideline.pipeline import enqueue_stages, ingest_local, run_job
from sideline.storage import ArtifactStore
from sideline.config import build_blobs


def test_ingest_local_runs_stages_and_records_runs(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True, cache_frames=False)
    match_id = ingest_local(settings, clip, capture_mode="followcam", sm_match_id="x")
    store = ArtifactStore(build_blobs(settings))
    assert store.versions(match_id, "s0_ingest") == [1] and store.versions(match_id, "s1_sampling") == [1]
    with session_scope(build_engine(settings)) as s:
        runs = s.query(StageRun).filter_by(match_id=match_id).order_by(StageRun.created_at).all()
        assert [(r.stage, r.version) for r in runs] == [("s0_ingest", 1), ("s1_sampling", 1)]
        assert runs[1].inputs == [{"match_id": match_id, "stage": "s0_ingest", "version": 1}]


def test_failed_job_is_recorded_as_failed(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, stages=[])
    with session_scope(build_engine(settings)) as s:
        m = s.get(Match, match_id)
        m.source_key = "matches/nowhere.mp4"     # break the source so S0 fails
    try:
        enqueue_stages(settings, match_id, ["s0_ingest"])
    except Exception:
        pass
    with session_scope(build_engine(settings)) as s:
        job = s.query(Job).filter_by(match_id=match_id).one()
        assert job.status == "failed" and job.error
