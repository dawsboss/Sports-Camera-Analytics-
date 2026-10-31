"""S2, S3 and S8 end to end on the synthetic clip, with the detector
scripted: the scene is a throw-in drawn in boxes, and the stages have to
find it, attribute it to the thrower's track, and say so in the artifacts.
No model, no GPU, no pitch coordinates."""

import json

from sideline.contracts import DETECTIONS_SCHEMA, EVENTS_SCHEMA, FRAGMENTS_SCHEMA
from sideline.detection import Detection, ScriptedDetector
from sideline.stages import STAGES, MatchRecord, StageContext
from sideline.storage import ArtifactStore, LocalBlobStore

# The clip is 320 x 180 and the grass starts around y = 80; people stand
# 20 px tall with their feet in the lower half.
H = 20.0


def person(x, foot_y, conf=0.9):
    return Detection("player", x - 4, foot_y - H, x + 4, foot_y, conf)


def ball(x, y, conf=0.4):
    return Detection("ball", x - 2, y - 2, x + 2, y + 2, conf)


def throw_in_scene(k: int) -> list[Detection]:
    # Sample k of 15. Three players stand; a fourth holds the ball overhead
    # for 1.6 s (samples 0..7) then throws it. One "person" in the sky is
    # a spectator-shaped mistake the grass filter should drop.
    dets = [person(120, 140), person(200, 150), person(260, 130), person(60, 30)]
    thrower = (40, 160)
    dets.append(person(*thrower))
    if k < 8:
        dets.append(ball(thrower[0], thrower[1] - H - 2))
    else:
        dets.append(ball(thrower[0] + 25 * (k - 7), thrower[1] - H - 2 - 6 * (k - 7)))
    return dets


def _ctx(tmp_path, clip, **config):
    blobs = LocalBlobStore(tmp_path / "blobs")
    key = "matches/m1/source/clip.mp4"
    blobs.put_file(key, clip, "video/mp4")
    m = MatchRecord(id="m1", capture_mode="followcam", source_key=key, kickoff_offset_ms=0)
    return StageContext(match=m, artifacts=ArtifactStore(blobs), blobs=blobs, workdir=tmp_path / "work",
                        config={"keyframe_every_s": 1.0, "detect_halftime": False, **config})


def test_detect_track_and_find_the_throw_in(tmp_path, clip):
    detector = ScriptedDetector(throw_in_scene)
    ctx = _ctx(tmp_path, clip, detector=detector)
    STAGES["s0_ingest"].run(ctx)
    STAGES["s1_sampling"].run(ctx)

    s2 = STAGES["s2_detection"].run(ctx)
    dets = ctx.artifacts.read_table(s2, "detections.parquet")
    assert dets.schema.equals(DETECTIONS_SCHEMA) and detector.calls == 15
    rows = dets.to_pylist()
    assert {r["cls"] for r in rows} == {"player", "ball"}
    # Four people on grass per frame and one ball; the one in the sky is gone.
    assert sum(1 for r in rows if r["cls"] == "player") == 15 * 4
    man = ctx.artifacts.read_manifest(s2)
    assert man["dropped_off_grass"] == 15 and man["config"]["detector"] == {"kind": "scripted"}
    assert man["detections"] == {"player": 60, "ball": 15} and man["capture_mode"] == "followcam"

    s3 = STAGES["s3_tracking"].run(ctx)
    frags = ctx.artifacts.read_table(s3, "fragments.parquet")
    assert frags.schema.equals(FRAGMENTS_SCHEMA)
    frows = frags.to_pylist()
    tracks = {}
    for r in frows:
        tracks.setdefault(r["track_id"], []).append(r)
    people = [t for t, rs in tracks.items() if rs[0]["cls"] == "player"]
    balls = [t for t, rs in tracks.items() if rs[0]["cls"] == "ball"]
    assert len(people) == 4 and len(balls) == 1 and all(len(tracks[t]) == 15 for t in people)
    thrower = next(t for t in people if abs((tracks[t][0]["x1"] + tracks[t][0]["x2"]) / 2 - 40) < 1)
    assert ctx.artifacts.read_manifest(s3)["fragments_by_class"] == {"player": 4, "ball": 1}

    s8 = STAGES["s8_events"].run(ctx)
    events = ctx.artifacts.read_table(s8, "events.parquet")
    assert events.schema.equals(EVENTS_SCHEMA)
    ev = events.to_pylist()
    assert len(ev) == 1
    e = ev[0]
    assert e["type"] == "throw_in" and e["track_id"] == thrower and e["t_ms"] == 8 * 200
    assert e["x_pitch"] is None and e["y_pitch"] is None and e["player_id"] is None
    assert e["confidence"] >= 0.8
    log = json.loads(ctx.blobs.get(s8.key("dead_balls.json")))
    assert len(log) == 1 and log[0]["held"] and log[0]["call"]["type"] == "throw_in"
    man = ctx.artifacts.read_manifest(s8)
    assert man["events_by_type"] == {"throw_in": 1} and man["inputs"][0] == s3.as_dict()
    assert man["capture_mode"] == "followcam"


def test_detection_needs_sampling_and_tracking_needs_detection(tmp_path, clip):
    ctx = _ctx(tmp_path, clip, detector=ScriptedDetector(lambda k: []))
    STAGES["s0_ingest"].run(ctx)
    for stage, needs in (("s2_detection", "s1_sampling"), ("s3_tracking", "s2_detection"), ("s8_events", "s3_tracking")):
        try:
            STAGES[stage].run(ctx)
        except RuntimeError as e:
            assert needs in str(e)
        else:
            raise AssertionError(f"{stage} ran without {needs}")


def test_empty_scene_writes_empty_artifacts_not_errors(tmp_path, clip):
    ctx = _ctx(tmp_path, clip, detector=ScriptedDetector(lambda k: []), grass_filter=False)
    STAGES["s0_ingest"].run(ctx)
    STAGES["s1_sampling"].run(ctx)
    s2 = STAGES["s2_detection"].run(ctx)
    s3 = STAGES["s3_tracking"].run(ctx)
    s8 = STAGES["s8_events"].run(ctx)
    assert ctx.artifacts.read_table(s2, "detections.parquet").num_rows == 0
    assert ctx.artifacts.read_table(s3, "fragments.parquet").num_rows == 0
    assert ctx.artifacts.read_table(s8, "events.parquet").num_rows == 0
    assert ctx.artifacts.read_manifest(s2)["config"]["grass_filter"] is False


def test_events_use_the_copied_periods_and_a_static_registration_when_present(tmp_path, clip):
    """The second period's start comes from the minutes app's periods copied
    at upload, through the timeline, not from S0's half-time hint; and a
    registration artifact, when one exists, is where the camera's motion is
    read from."""
    import numpy as np
    import pyarrow as pa

    from sideline.contracts import REGISTRATION_SCHEMA

    T0 = 1_700_000_000_000
    periods = {"0": {"half": 1, "start": T0, "end": T0 + 1_000}, "1": {"half": 2, "start": T0 + 1_600}}
    detector = ScriptedDetector(throw_in_scene)
    blobs = LocalBlobStore(tmp_path / "blobs")
    key = "matches/m1/source/clip.mp4"
    blobs.put_file(key, clip, "video/mp4")
    m = MatchRecord(id="m1", capture_mode="static", source_key=key, kickoff_offset_ms=0, periods=periods)
    ctx = StageContext(match=m, artifacts=ArtifactStore(blobs), blobs=blobs, workdir=tmp_path / "work",
                       config={"keyframe_every_s": 1.0, "detect_halftime": False, "detector": detector})
    STAGES["s0_ingest"].run(ctx)
    s1 = STAGES["s1_sampling"].run(ctx)
    STAGES["s2_detection"].run(ctx)
    STAGES["s3_tracking"].run(ctx)
    # A static registration: the same homography on every sampled frame.
    frames = ctx.artifacts.read_table(s1, "frames.parquet").to_pylist()
    H = [0.3, 0.0, -48.0, 0.0, 0.3, -27.0, 0.0, 0.0, 1.0]
    s5 = ctx.artifacts.new_version("m1", "s5_registration")
    ctx.artifacts.write_table(s5, "registration.parquet", pa.Table.from_pylist(
        [{"frame_index": f["frame_index"], "t_ms": f["t_ms"], "method": "static", "confidence": 1.0, "n_lines": 0, "h": H}
         for f in frames], schema=REGISTRATION_SCHEMA))
    ctx.artifacts.write_manifest(s5, config={}, inputs=[s1], capture_mode="static", stage_version="0", pipeline_version="0",
                                 outputs=["registration.parquet"])
    s8 = STAGES["s8_events"].run(ctx)
    man = ctx.artifacts.read_manifest(s8)
    assert man["config"]["period_starts_ms"] == [0, 1_600] and man["config"]["period_source"] == "periods"
    assert man["camera_shifts_from_registration"] == 14 and man["camera_shifts_from_people"] == 0
    assert s5.as_dict() in man["inputs"] and man["capture_mode"] == "static"
    ev = ctx.artifacts.read_table(s8, "events.parquet").to_pylist()
    # The throw at 1.6 s is the first dead ball of the second period, and a
    # ball held overhead is a throw-in before it is anything else.
    assert [e["type"] for e in ev] == ["throw_in"]


def test_the_write_down_is_a_csv_with_the_match_clock_and_the_cli_prints_it(tmp_path, clip, capsys):
    """What the coach asked for: each event, who, and when, as a list."""
    import csv
    import io

    from sideline.cli import main
    from sideline.config import Settings
    from sideline.pipeline import ingest_local
    from sideline.storage import ArtifactStore
    from sideline.config import build_blobs

    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=400,
                            stages=["s0_ingest", "s1_sampling", "s2_detection", "s3_tracking", "s8_events"],
                            config={"detector": ScriptedDetector(throw_in_scene), "detect_halftime": False})
    store = ArtifactStore(build_blobs(settings))
    ref = store.latest(match_id, "s8_events")
    rows = list(csv.DictReader(io.StringIO(store.blobs.get(ref.key("events.csv")).decode())))
    assert len(rows) == 1
    r = rows[0]
    # Thrown at 1.6 s of video, 1.2 s after the kickoff at 0.4 s: 00:01 on the match clock.
    assert r["event"] == "throw_in" and r["clock"] == "00:01" and r["clock_basis"] == "match" and r["t_ms"] == "1600"
    assert r["taker_track"] and r["player_id"] == "" and float(r["confidence"]) >= 0.8 and "held" in r["reason"]

    assert main(["log", match_id, "--data", str(tmp_path / "data")]) == 0
    out = capsys.readouterr().out
    assert "throw_in" in out and f"track {r['taker_track']}" in out and "1 events" in out
    assert main(["log", match_id, "--data", str(tmp_path / "data"), "--csv"]) == 0
    assert capsys.readouterr().out.startswith("clock,clock_basis,t_ms,event")
    assert main(["log", "nope", "--data", str(tmp_path / "data")]) == 1


def test_overlay_renders_the_run_with_the_event_captioned(tmp_path, clip):
    import cv2

    from sideline.cli import main
    from sideline.config import Settings, build_blobs
    from sideline.overlay import render_overlay
    from sideline.pipeline import ingest_local
    from sideline.storage import ArtifactStore

    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=0,
                            stages=["s0_ingest", "s1_sampling", "s2_detection", "s3_tracking", "s8_events"],
                            config={"detector": ScriptedDetector(throw_in_scene), "detect_halftime": False})
    store = ArtifactStore(build_blobs(settings))
    out = tmp_path / "overlay.mp4"
    info = render_overlay(store, match_id, out, object_classes={"ball"})
    assert info["frames"] == 15 and info["events"] == 1 and out.exists()
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 15 and cap.get(cv2.CAP_PROP_FPS) == 5.0
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    # The caption bar is drawn only while the event is live: dark top rows
    # from the release (1.6 s) on, and not at the start.
    assert frames[0][:10].mean() > 60 and frames[9][:10].mean() < 40
    # Only the seconds around the event, at a chosen rate.
    short = tmp_path / "short.mp4"
    # Samples at 1.2, 1.4, 1.6, 1.8 and 2.0 s lie within half a second of the release at 1.6 s.
    assert render_overlay(store, match_id, short, around_s=0.5, fps=10, object_classes={"ball"})["frames"] == 5
    assert main(["overlay", match_id, "--data", str(tmp_path / "data"), "--out", str(tmp_path / "cli.mp4")]) == 0
    assert (tmp_path / "cli.mp4").exists()


def corner_scene(k: int) -> list[Detection]:
    # A lone taker by a corner flag with a grounded ball, everyone else off
    # toward the goal; the ball is struck at sample 9. The flag is scenery:
    # a COCO detector calls everyone a player, and it is the flag that names
    # the restart.
    dets = [person(200, 130), person(230, 150), person(260, 140), person(210, 160)]
    dets.append(person(50, 170))                                           # the taker
    dets.append(Detection("corner_flag", 28, 120, 34, 172, 0.9))           # a thin pole, feet off any grass test
    if k < 9:
        dets.append(ball(42, 172))
    else:
        dets.append(ball(42 + 28 * (k - 8), 172 - 9 * (k - 8)))
    return dets


def test_a_detected_flag_passes_detection_is_tracked_and_names_the_corner(tmp_path, clip):
    ctx = _ctx(tmp_path, clip, detector=ScriptedDetector(corner_scene))
    STAGES["s0_ingest"].run(ctx)
    STAGES["s1_sampling"].run(ctx)
    s2 = STAGES["s2_detection"].run(ctx)
    dets = ctx.artifacts.read_table(s2, "detections.parquet").to_pylist()
    assert sum(1 for d in dets if d["cls"] == "corner_flag") == 15, "the surface filter must not judge a landmark's feet"
    s3 = STAGES["s3_tracking"].run(ctx)
    frags = ctx.artifacts.read_table(s3, "fragments.parquet").to_pylist()
    flag_tracks = {r["track_id"] for r in frags if r["cls"] == "corner_flag"}
    assert len(flag_tracks) == 1 and sum(1 for r in frags if r["cls"] == "corner_flag") == 15
    s8 = STAGES["s8_events"].run(ctx)
    ev = ctx.artifacts.read_table(s8, "events.parquet").to_pylist()
    assert [e["type"] for e in ev] == ["corner"] and ev[0]["confidence"] >= 0.8
    log = json.loads(ctx.blobs.get(s8.key("dead_balls.json")))
    assert log[0]["landmarks_h"]["corner_flag"] < 1.5 and not log[0]["roles_known"]
    assert ctx.artifacts.read_manifest(s8)["config"]["landmark_classes"] == ["goal", "corner_flag"]
