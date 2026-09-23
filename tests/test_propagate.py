"""Tag propagation under spike/labels: it writes training labels nobody
tapped, so where it puts them and what it refuses to touch are pinned."""

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prop():
    spec = importlib.util.spec_from_file_location("_propagate", ROOT / "spike/labels/propagate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ball_at(i):
    """A ball 12 px across moving on a curve, 0.2 s of it over frames 0-6."""
    return np.array([200.0 + 9 * i, 150.0 + 4 * i + 0.4 * i * i])


def frame(i, size=(360, 640)):
    rng = np.random.default_rng(i)
    f = np.full((*size, 3), (40, 110, 70), np.uint8)
    f = cv2.add(f, rng.integers(0, 12, f.shape, dtype=np.uint8))
    x, y = ball_at(i)
    cv2.circle(f, (int(round(x)), int(round(y))), 6, (235, 235, 235), -1, lineType=cv2.LINE_AA)
    return f


def test_fills_between_taps_on_the_ball(prop):
    frames = {i: frame(i) for i in range(7)}
    got = prop.fill_pair(frames, 0, ball_at(0), 6, ball_at(6), half=10, agree=3.0)
    assert sorted(got) == [1, 2, 3, 4, 5]
    for i, p in got.items():
        assert np.linalg.norm(p - ball_at(i)) < 1.5, (i, p, ball_at(i))


def test_nothing_where_the_directions_disagree(prop):
    # The later tap is on empty grass: the backward search has no ball to
    # follow, so nothing may be filled.
    frames = {i: frame(i) for i in range(7)}
    got = prop.fill_pair(frames, 0, ball_at(0), 6, ball_at(6) + np.array([0.0, 60.0]), half=10, agree=3.0)
    assert got == {}


def test_never_replaces_a_tap_and_marks_what_it_adds(prop, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30000 / 1001, (640, 360))
    for i in range(7):
        w.write(frame(i))
    w.release()
    h, wd = 360, 640
    tap = lambda i: {"x": ball_at(i)[0] / wd, "y": ball_at(i)[1] / h, "vis": 1, "t": (i + 0.5) / (30000 / 1001) * 1000}
    # Key 3 is an older-style key sitting on a filled frame; it must survive.
    ball = {"0": tap(0), "6": tap(6), "3": {"vis": 0, "t": 90}}
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"matches": {"m": {"ball": ball}}}))
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["propagate.py", "--tags", str(tags), "--video", f"m={video}", "--out", str(out)])
    prop.main()
    got = json.loads(out.read_text())["matches"]["m"]["ball"]
    assert got["3"] == ball["3"] and got["0"] == ball["0"] and got["6"] == ball["6"]
    added = {k: v for k, v in got.items() if v.get("src") == "prop"}
    assert set(added) <= {"1", "2", "4", "5"} and len(added) >= 3
