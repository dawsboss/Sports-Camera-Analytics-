import numpy as np

from sideline.contracts import FRAMES_SCHEMA
from sideline.stages import STAGES, MatchRecord, StageContext
from sideline.stages.s0_ingest import detect_halftime, probe
from sideline.storage import ArtifactStore, LocalBlobStore
from tests.conftest import write_synthetic_video


def _ctx(tmp_path, clip, **config):
    blobs = LocalBlobStore(tmp_path / "blobs")
    key = "matches/m1/source/clip.mp4"
    blobs.put_file(key, clip, "video/mp4")
    m = MatchRecord(id="m1", capture_mode="followcam", source_key=key, pitch_length=100, pitch_width=64, kickoff_offset_ms=500)
    return StageContext(match=m, artifacts=ArtifactStore(blobs), blobs=blobs, workdir=tmp_path / "work",
                        config={"keyframe_every_s": 1.0, "detect_halftime": False, **config})


def test_probe_reads_the_clip(clip):
    info = probe(clip)
    assert info["frame_count"] == 75 and info["fps"] == 25.0 and (info["width"], info["height"]) == (320, 180)
    assert info["duration_ms"] == 3000


def test_s0_writes_match_json_video_and_keyframes(tmp_path, clip):
    ctx = _ctx(tmp_path, clip)
    ref = STAGES["s0_ingest"].run(ctx)
    assert ref.version == 1 and ctx.artifacts.complete(ref)
    doc = ctx.artifacts.read_json(ref, "match.json")
    assert doc["match_id"] == "m1" and doc["capture_mode"] == "followcam" and doc["frame_count"] == 75
    assert doc["pitch"] == {"length_m": 100.0, "width_m": 64.0} and doc["kickoff_offset_ms"] == 500
    assert doc["video_key"] == ref.key("video.mp4") and ctx.blobs.exists(doc["video_key"])
    files = ctx.artifacts.list(ref)
    assert "match.json" in files and "manifest.json" in files and len([f for f in files if f.startswith("keyframes/")]) == 3
    # Whether ffmpeg was there is recorded, not assumed.
    assert isinstance(doc["transcoded"], bool) and "ffmpeg" in ctx.artifacts.read_manifest(ref)["config"]


def test_s1_samples_at_five_fps_and_caches_frames(tmp_path, clip):
    ctx = _ctx(tmp_path, clip)
    s0 = STAGES["s0_ingest"].run(ctx)
    ref = STAGES["s1_sampling"].run(ctx)
    t = ctx.artifacts.read_table(ref, "frames.parquet")
    assert t.schema.equals(FRAMES_SCHEMA)
    rows = t.to_pylist()
    # 3 s at 5 fps: frames at 0, 200, 400 ... 2800 ms from source frames 0, 5, 10 ...
    assert [r["t_ms"] for r in rows] == list(range(0, 3000, 200))
    assert [r["source_frame"] for r in rows] == list(range(0, 75, 5))
    assert all(ctx.blobs.exists(r["key"]) for r in rows)
    man = ctx.artifacts.read_manifest(ref)
    assert man["inputs"] == [s0.as_dict()] and man["frames"] == 15 and man["config"]["sample_fps"] == 5.0


def test_s1_can_skip_the_cache_and_cap_frames(tmp_path, clip):
    ctx = _ctx(tmp_path, clip, cache_frames=False, max_frames=4, sample_fps=2.5)
    STAGES["s0_ingest"].run(ctx)
    ref = STAGES["s1_sampling"].run(ctx)
    rows = ctx.artifacts.read_table(ref, "frames.parquet").to_pylist()
    assert len(rows) == 4 and all(r["key"] is None for r in rows) and rows[1]["t_ms"] == 400


def test_s1_needs_s0_first(tmp_path, clip):
    ctx = _ctx(tmp_path, clip)
    try:
        STAGES["s1_sampling"].run(ctx)
    except RuntimeError as e:
        assert "s0_ingest" in str(e)
    else:
        raise AssertionError("ran without an ingest")


def test_reruns_make_new_versions(tmp_path, clip):
    ctx = _ctx(tmp_path, clip)
    a = STAGES["s0_ingest"].run(ctx)
    b = STAGES["s0_ingest"].run(ctx)
    assert (a.version, b.version) == (1, 2) and ctx.artifacts.versions("m1", "s0_ingest") == [1, 2]


def test_halftime_is_the_long_grassless_gap_in_the_middle(tmp_path, pitch):
    # 40 one-second frames; frames 15..25 show no grass. Sampled every 2 s
    # with a 6 s minimum break, that is the half-time.
    clip = write_synthetic_video(tmp_path / "ht.mp4", pitch, frames=40, fps=1.0, blank=set(range(15, 26)))
    info = probe(clip)
    ht = detect_halftime(clip, info, every_s=2.0, min_break_s=6.0)
    assert ht is not None and ht.method == "grass-gap"
    assert 13_000 <= ht.start_ms <= 16_000 and 25_000 <= ht.end_ms <= 28_000
    assert detect_halftime(clip, info, every_s=2.0, min_break_s=20.0) is None
