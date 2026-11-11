"""The scorer that measures restart detection against the minutes app's
taps: a tap trails its event, a misnamed dead ball is not a miss, and a
free kick nobody tapped is not a false alarm."""

import importlib.util
import sys
from pathlib import Path

import pytest

from sideline.timeline import Tap

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def scorer():
    spec = importlib.util.spec_from_file_location("_restarts_on_taps", ROOT / "spike/evals/restarts_on_taps.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # its dataclasses resolve their annotations through here
    spec.loader.exec_module(mod)
    return mod


def det(t_s, type_, conf=0.8):
    return {"t_ms": int(t_s * 1000), "type": type_, "confidence": conf, "track_id": 1}


def tap(kind, elapsed_s, pid=None, side="us"):
    return Tap(kind=kind, side=side, elapsed_s=elapsed_s, player_id=pid)


def test_finds_misnames_misses_and_false_alarms(scorer):
    # Video time = elapsed time here (kickoff at 0).
    taps = [
        (tap("throw", 100, pid="p7"), 100_000),      # thrown at 94 s, tapped at 100: found
        (tap("corner", 300), 300_000),               # a dead ball at 292 s called a free kick: misnamed
        (tap("goalkick", 500), 500_000),             # nothing detected: missed
        (tap("foul", 700), 700_000),                 # the free kick comes 20 s after the foul: found
        (tap("throw", 900), None),                   # the clock ran past the file
    ]
    detected = [det(94, "throw_in"), det(292, "free_kick"), det(720, "free_kick"),
                det(1200, "throw_in"), det(1300, "free_kick"), det(1400, "restart")]
    r = scorer.score(detected, taps, before_s=15, after_s=3, kinds=["throw", "corner", "goalkick", "foul"])
    assert r.kinds["throw"].taps == 2 and r.kinds["throw"].found == 1 and r.kinds["throw"].missed == 1
    assert r.kinds["throw"].offsets_s == [6.0] and r.kinds["throw"].attributed == 1
    assert r.kinds["corner"].found == 0 and r.kinds["corner"].misnamed == {"free_kick": 1} and r.kinds["corner"].missed == 0
    assert r.kinds["goalkick"].missed == 1
    assert r.kinds["foul"].found == 1 and r.kinds["foul"].offsets_s == [-20.0]
    assert r.taps_off_video == 1
    # The late throw-in and free kick answer no tap; the unnamed restart is not a scored type.
    assert r.false_alarms == {"throw_in": 1, "free_kick": 1} and r.unscored == {"restart": 1}
    text = scorer.format_report(r)
    assert "throw" in text and "found as free_kick: 1" in text and "throw_in 1" in text


def test_each_detection_answers_one_tap_and_the_nearest_wins(scorer):
    taps = [(tap("throw", 100), 100_000), (tap("throw", 104), 104_000)]
    detected = [det(97, "throw_in")]
    r = scorer.score(detected, taps, 15, 3, ["throw"])
    assert r.kinds["throw"].found == 1 and r.kinds["throw"].missed == 1 and r.kinds["throw"].offsets_s == [3.0]


def test_untracked_kinds_are_not_scored(scorer):
    taps = [(tap("corner", 50), 50_000), (tap("throw", 60), 60_000)]
    r = scorer.score([det(48, "corner"), det(58, "throw_in")], taps, 15, 3, ["corner"])
    assert list(r.kinds) == ["corner"] and r.kinds["corner"].found == 1
    assert r.false_alarms == {} and r.unscored == {"throw_in": 1}


def test_main_runs_on_files(scorer, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from sideline.contracts import EVENTS_SCHEMA

    rows = [{"t_ms": 180_000 + 94_000, "type": "throw_in", "x_pitch": None, "y_pitch": None, "player_id": None,
             "team": None, "confidence": 0.9, "track_id": 3}]
    pq.write_table(pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA), tmp_path / "events.parquet")
    T0 = 1_700_000_000_000
    sm = {"periods": {"0": {"half": 1, "start": T0, "end": T0 + 1_800_000}},
          "events": {"e": {"t": 100, "at": T0 + 100_000, "side": "us", "kind": "throw", "pid": "p1"}}}
    (tmp_path / "sm.json").write_text(__import__("json").dumps(sm))
    out = tmp_path / "report.json"
    rc = scorer.main(["--events", str(tmp_path / "events.parquet"), "--sm", str(tmp_path / "sm.json"),
                      "--kickoff-offset-ms", "180000", "--kinds", "throw", "--out", str(out)])
    assert rc == 0
    rep = __import__("json").loads(out.read_text())
    assert rep["kinds"]["throw"] == {"taps": 1, "found": 1, "misnamed": {}, "missed": 0, "median_offset_s": 6.0,
                                     "attributed_finds": 1}
