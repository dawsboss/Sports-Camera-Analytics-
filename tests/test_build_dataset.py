"""The training-set builder under spike/labels. It is a script rather than
part of the package, but a wrong split or a wrong flip_idx trains a model
that reports good numbers and is wrong, so both are pinned here."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bd():
    return load("spike/labels/build_dataset.py", "_build_dataset")


def test_flip_idx_is_the_halfway_mirror_of_our_vertices(bd):
    pk = load("spike/evals/pitch_keypoints.py", "_pitch_keypoints")
    V = pk.VERTICES
    assert len(V) == len(bd.FLIP_IDX) == bd.N_VERTICES
    for i, j in enumerate(bd.FLIP_IDX):
        assert V[j] == pytest.approx((pk.LENGTH - V[i][0], V[i][1])), (i, pk.NAMES[i], pk.NAMES[j])
    # And the fetcher's upstream check compares against the same list.
    fp = load("spike/labels/fetch_public.py", "_fetch_public")
    assert fp.EXPECTED_FLIP == bd.FLIP_IDX


def test_window_parsing(bd):
    assert bd.parse_window("m=50:") == ("m", 3000.0, float("inf"))
    assert bd.parse_window("m=:10") == ("m", 0.0, 600.0)
    assert bd.parse_window("m=1.5:2") == ("m", 90.0, 120.0)
    for bad in ("m", "m=50", "=1:2", "m=5:5", "m=6:5"):
        with pytest.raises(SystemExit):
            bd.parse_window(bad)


def test_window_split_keeps_a_guard_band_out_of_both_sides(bd):
    tags = {str(f): {"vis": 1} for f in range(0, 300, 10)}          # 0..29 s at 10 fps
    train, val = bd.split_by_windows(tags, 10.0, [(15.0, 20.0)], guard_s=2.0)
    t = lambda d: sorted(int(k) / 10 for k in d)
    assert t(val) == [15, 16, 17, 18, 19]
    assert t(train) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 22, 23, 24, 25, 26, 27, 28, 29]


def test_ball_tags_are_cut_at_the_frame_that_was_on_screen(bd):
    fps = 30000 / 1001
    # 20260919-flight, key 47650: t=1589.910 s is frame 47649.6, so the
    # screen showed 47649 and Math.round keyed it 47650.
    assert bd.tagged_frame("47650", {"t": 1589910, "vis": 1}, fps) == 47649
    # Before mid-frame (76302.44) the round and the floor agree.
    assert bd.tagged_frame("76302", {"t": 2545958, "vis": 1}, fps) == 76302
    # No time recorded (older exports, pitch tags): the key stands.
    assert bd.tagged_frame("100", {"vis": 0}, fps) == 100
    # A time that disagrees by more than rounding is not this bug.
    assert bd.tagged_frame("100", {"t": 60000, "vis": 1}, fps) == 100


def run_main(bd, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["build_dataset.py", *argv])
    bd.main()


def stems(root, kind, split):
    return sorted(p.stem for p in (root / kind / "labels" / split).glob("*.txt"))


def test_build_splits_by_window_and_a_rerun_leaves_nothing_stale(bd, clip, tmp_path, monkeypatch):
    # The clip is 75 frames at 25 fps. Ball tagged every fifth frame, the
    # last one "not visible"; one pitch frame with four points.
    ball = {str(f): {"x": 0.5, "y": 0.5, "vis": 1} for f in range(0, 70, 5)}
    ball["70"] = {"vis": 0}
    kp = {"60": {"0": [0.1, 0.1], "1": [0.2, 0.1], "13": [0.5, 0.2], "24": [0.9, 0.1]}}
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"version": 2, "matches": {"m": {"ball": ball, "kp": kp}}}))
    out = tmp_path / "ds"

    monkeypatch.setattr(bd, "WINDOW_GUARD_S", 0.3)
    # 0.03 min = 1.8 s = frame 45; the guard drops frame 40 (1.6 s).
    run_main(bd, monkeypatch, "--tags", str(tags), "--video", f"m={clip}", "--out", str(out),
             "--holdout-window", "m=0.03:")
    train, val = stems(out, "ball", "train"), stems(out, "ball", "val")
    assert [int(s.split("_")[1]) for s in train] == [0, 5, 10, 15, 20, 25, 30, 35]
    assert [int(s.split("_")[1]) for s in val] == [45, 50, 55, 60, 65, 70]
    assert (out / "ball" / "labels" / "val" / "m_0000070.txt").read_text() == ""
    assert stems(out, "pitch", "val") == ["m_0000060"]
    y = (out / "pitch" / "data.yaml").read_text()
    assert f"flip_idx: {bd.FLIP_IDX}" in y and "kpt_shape: [32, 3]" in y
    row = (out / "pitch" / "labels" / "val" / "m_0000060.txt").read_text().split()
    assert len(row) == 5 + 3 * 32

    # Re-run without the window: every frame moves to train, and nothing
    # from the old val split survives next to it.
    run_main(bd, monkeypatch, "--tags", str(tags), "--video", f"m={clip}", "--out", str(out))
    assert len(stems(out, "ball", "train")) == 15
    assert not (out / "ball" / "labels" / "val").exists()
    assert not (out / "ball" / "images" / "val").exists()


def test_propagated_tags_train_but_never_score(bd, clip, tmp_path, monkeypatch):
    ball = {"5": {"x": 0.5, "y": 0.5, "vis": 1}, "6": {"x": 0.5, "y": 0.5, "vis": 1, "src": "prop"},
            "50": {"x": 0.5, "y": 0.5, "vis": 1}, "51": {"x": 0.5, "y": 0.5, "vis": 1, "src": "prop"}}
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"matches": {"m": {"ball": ball}}}))
    out = tmp_path / "ds"
    monkeypatch.setattr(bd, "WINDOW_GUARD_S", 0.0)
    run_main(bd, monkeypatch, "--tags", str(tags), "--video", f"m={clip}", "--out", str(out),
             "--kinds", "ball", "--holdout-window", "m=0.03:")
    assert stems(out, "ball", "train") == ["m_0000005", "m_0000006"]
    assert stems(out, "ball", "val") == ["m_0000050"]


def test_a_match_cannot_be_held_out_twice(bd, clip, tmp_path, monkeypatch):
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"matches": {"m": {"ball": {"0": {"vis": 0}}}}}))
    with pytest.raises(SystemExit):
        run_main(bd, monkeypatch, "--tags", str(tags), "--video", f"m={clip}", "--out", str(tmp_path / "o"),
                 "--holdout", "m", "--holdout-window", "m=0:1")
