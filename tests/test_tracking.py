"""The tracker on scripted trajectories: people by overlap, the ball by
where it went, with the gaps the footage measured."""

import numpy as np

from sideline.detection import Detection
from sideline.tracking import Tracker, TrackerConfig, greedy_match, iou_matrix, track_frames

SIZE = (1920, 1080)


def person(x, y, conf=0.9, cls="player", h=60.0):
    return Detection(cls, x - 12, y - h, x + 12, y, conf)


def ball(x, y, conf=0.5):
    return Detection("ball", x - 5, y - 5, x + 5, y + 5, conf)


def test_iou_and_greedy_match():
    a = np.array([[0, 0, 10, 10], [20, 20, 30, 30]], float)
    b = np.array([[1, 1, 11, 11], [100, 100, 110, 110]], float)
    m = iou_matrix(a, b)
    assert 0.6 < m[0, 0] < 0.7 and m[1, 1] == 0 and m[0, 1] == 0
    assert greedy_match(m, 0.3) == [(0, 0)]
    # Smaller-is-better scores stop at the floor too.
    assert greedy_match(np.array([[0.5, 2.0], [3.0, 0.2]]), 1.0, larger_is_better=False) == [(1, 1), (0, 0)]


def test_two_players_crossing_keep_their_ids():
    # One walks right, one walks left; they pass at frame 5.
    frames = []
    for k in range(11):
        frames.append((k, 200 * k, [person(400 + 20 * k, 500), person(600 - 20 * k, 500)]))
    rows = track_frames(frames, TrackerConfig(), ["ball"], SIZE)
    by_id = {}
    for r in rows:
        by_id.setdefault(r["track_id"], []).append(r)
    assert len(by_id) == 2
    for tid, rs in by_id.items():
        xs = [(r["x1"] + r["x2"]) / 2 for r in rs]
        steps = np.diff(xs)
        assert len(rs) == 11 and (np.all(steps > 0) or np.all(steps < 0)), "a track changed direction at the crossing"


def test_weak_detections_rescue_a_track_but_never_start_one():
    frames = [(0, 0, [person(500, 500, 0.9)]), (1, 200, [person(505, 500, 0.3)]), (2, 400, [person(510, 500, 0.9)]),
              (3, 600, [person(510, 500, 0.9), person(900, 500, 0.3)])]
    rows = track_frames(frames, TrackerConfig(), ["ball"], SIZE)
    ids = {r["track_id"] for r in rows}
    assert len(ids) == 1 and len(rows) == 4


def test_a_track_dies_after_max_age_and_a_flicker_is_dropped():
    frames = [(0, 0, [person(500, 500)]), (1, 200, [person(502, 500)])]
    frames += [(k, 200 * k, []) for k in range(2, 9)]
    frames += [(9, 1800, [person(520, 500)]), (10, 2000, [person(522, 500)])]
    frames += [(11, 2200, [person(1500, 300)])]       # seen once: flicker
    rows = track_frames(frames, TrackerConfig(max_age=5), ["ball"], SIZE)
    ids = {r["track_id"] for r in rows}
    assert len(ids) == 2 and len(rows) == 4


def test_ball_is_followed_through_a_blackout_and_by_its_motion():
    # Kicked ball: 60 px a sample, missing for samples 3..9 (the measured
    # worst case is 15), then found again where its motion says.
    frames = []
    for k in range(16):
        dets = [person(300, 700), person(1500, 700)]       # two standing players for scale
        if k < 3 or k >= 10:
            dets.append(ball(200 + 60 * k, 600, conf=0.2))
        frames.append((k, 200 * k, dets))
    rows = track_frames(frames, TrackerConfig(), ["ball"], SIZE)
    ball_rows = [r for r in rows if r["cls"] == "ball"]
    assert len({r["track_id"] for r in ball_rows}) == 1 and len(ball_rows) == 9


def test_a_spare_ball_is_its_own_track():
    frames = []
    for k in range(8):
        frames.append((k, 200 * k, [person(900, 600), ball(100 + 50 * k, 500), ball(1800, 1000)]))
    rows = track_frames(frames, TrackerConfig(), ["ball"], SIZE)
    ball_tracks = {}
    for r in rows:
        if r["cls"] == "ball":
            ball_tracks.setdefault(r["track_id"], []).append(r)
    assert len(ball_tracks) == 2 and all(len(v) == 8 for v in ball_tracks.values())


def test_tracker_step_returns_what_it_matched():
    t = Tracker(TrackerConfig(), ["ball"], SIZE)
    out = t.step(0, [person(100, 100), ball(500, 500)])
    assert {tr.cls for tr, _ in out} == {"player", "ball"} and {tr.id for tr, _ in out} == {1, 2}
    out = t.step(1, [person(102, 100)])
    assert len(out) == 1 and out[0][0].id == 1 and out[0][0].hits == 2
    assert len(t.close()) == 2
