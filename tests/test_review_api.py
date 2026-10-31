"""The review routes: what the page reads, and the rows its taps write.
A verdict is a row keyed by the events version and the event's time; the
event itself is never edited, and a frame leaves the blob store only to a
browser on this network."""

import pytest
from fastapi.testclient import TestClient

from sideline.api.app import create_app
from sideline.config import Settings
from sideline.detection import ScriptedDetector
from sideline.pipeline import ingest_local
from tests.test_detect_track_events import throw_in_scene

STAGES = ["s0_ingest", "s1_sampling", "s2_detection", "s3_tracking", "s8_events"]


@pytest.fixture
def reviewed(tmp_path, clip):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=400, stages=STAGES,
                            config={"detector": ScriptedDetector(throw_in_scene), "detect_halftime": False})
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, match_id


def test_review_doc_lists_the_events_with_their_clock_and_cues(reviewed):
    c, match_id = reviewed
    r = c.get(f"/matches/{match_id}/review")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["events_version"] == 1 and doc["tracks_version"] == 1
    assert doc["match"]["id"] == match_id and doc["match"]["width"] == 320 and doc["match"]["kickoff_offset_ms"] == 400
    assert "throw_in" in doc["event_types"] and "corner" in doc["event_types"]
    assert len(doc["events"]) == 1
    e = doc["events"][0]
    assert e["type"] == "throw_in" and e["clock"] == "00:01" and e["clock_basis"] == "match" and e["t_ms"] == 1600
    assert e["taker_track"] and e["player_id"] is None and e["decision"] is None
    assert e["cues"]["held"] is True and e["cues"]["still_s"] > 1
    assert c.get("/matches/nope/review").status_code == 404


def test_frames_around_a_moment_carry_their_boxes_and_the_jpegs_are_served(reviewed):
    c, match_id = reviewed
    r = c.get(f"/matches/{match_id}/review/frames", params={"t_ms": 1600, "span_s": 0.5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["t_ms"] for f in body["frames"]] == [1200, 1400, 1600, 1800, 2000]
    f = body["frames"][2]
    assert f["cached"] and f["url"].endswith("/frames/8.jpg")
    kinds = {b["cls"] for b in f["boxes"]}
    assert kinds == {"player", "ball"} and len(f["boxes"]) == 5
    assert body["kinds"]["ball"] == "object" and body["kinds"]["player"] == "person" and body["kinds"]["goal"] == "landmark"
    img = c.get(f["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"
    assert c.get(f"/matches/{match_id}/frames/999.jpg").status_code == 404


def test_verdicts_are_rows_and_the_newest_shows_on_the_event(reviewed):
    c, match_id = reviewed
    doc = c.get(f"/matches/{match_id}/review").json()
    e = doc["events"][0]
    key = {"events_version": doc["events_version"], "t_ms": e["t_ms"]}
    assert c.post(f"/matches/{match_id}/review/decisions", json={**key, "verdict": "maybe"}).status_code == 422
    assert c.post(f"/matches/{match_id}/review/decisions", json={**key, "verdict": "wrong_type"}).status_code == 422
    assert c.post(f"/matches/{match_id}/review/decisions", json={**key, "verdict": "wrong_taker"}).status_code == 422
    r = c.post(f"/matches/{match_id}/review/decisions", json={**key, "verdict": "right", "decided_by": "g"})
    assert r.status_code == 201 and r.json()["verdict"] == "right"
    r = c.post(f"/matches/{match_id}/review/decisions",
               json={**key, "verdict": "wrong_type", "corrected_type": "free_kick", "decided_by": "g", "note": "quick one"})
    assert r.status_code == 201
    doc = c.get(f"/matches/{match_id}/review").json()
    d = doc["events"][0]["decision"]
    assert d["verdict"] == "wrong_type" and d["corrected_type"] == "free_kick" and d["note"] == "quick one"
    # Both rows are kept; nothing in the artifact changed.
    rows = c.get(f"/matches/{match_id}/review/decisions").json()
    assert [x["verdict"] for x in rows] == ["right", "wrong_type"]
    assert c.get(f"/matches/{match_id}/artifacts/s8_events/1/dead_balls.json").json()[0]["call"]["type"] == "throw_in"
    assert c.post("/matches/nope/review/decisions", json={**key, "verdict": "right"}).status_code == 404


def test_naming_the_taker_is_an_identity_decision_on_the_track(reviewed):
    c, match_id = reviewed
    doc = c.get(f"/matches/{match_id}/review").json()
    e = doc["events"][0]
    r = c.post(f"/matches/{match_id}/review/tracks",
               json={"tracks_version": doc["tracks_version"], "track_id": e["taker_track"], "player_id": "p7", "decided_by": "g"})
    assert r.status_code == 201 and r.json()["player_id"] == "p7"
    doc = c.get(f"/matches/{match_id}/review").json()
    assert doc["events"][0]["player_id"] == "p7" and doc["track_names"][str(e["taker_track"])] == "p7"


def test_the_page_is_served_from_web(reviewed):
    c, _ = reviewed
    r = c.get("/review")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Sideline Review" in r.text
