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
    # Who has the ball in each sample: the thrower until she lets go at 1600.
    thrower = c.get(f"/matches/{match_id}/review").json()["events"][0]["taker_track"]
    assert [fr["has_ball"] for fr in body["frames"]] == [thrower, thrower, None, None, None]
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


def test_a_rerun_shows_the_old_verdicts_as_carried_and_a_new_one_wins(reviewed):
    """A re-run of S8 is a new events version with no rows of its own. The
    verdicts given on the old one are not lost and not rewritten: the page
    reads them through the new version, marked as carried."""
    c, match_id = reviewed
    doc = c.get(f"/matches/{match_id}/review").json()
    e = doc["events"][0]
    r = c.post(f"/matches/{match_id}/review/decisions",
               json={"events_version": 1, "t_ms": e["t_ms"], "verdict": "wrong_type", "corrected_type": "free_kick", "decided_by": "g"})
    assert r.status_code == 201
    assert c.post(f"/matches/{match_id}/jobs", params={"stages": "s8_events"}).status_code == 201
    doc = c.get(f"/matches/{match_id}/review").json()
    assert doc["events_version"] == 2 and len(doc["events"]) == 1
    d = doc["events"][0]["decision"]
    assert d["verdict"] == "wrong_type" and d["corrected_type"] == "free_kick" and d["decided_by"] == "g"
    assert d["carried_from"] == {"events_version": 1, "t_ms": e["t_ms"]}
    # The row itself still belongs to version 1.
    assert [x["events_version"] for x in c.get(f"/matches/{match_id}/review/decisions").json()] == [1]
    # A verdict given on the new version is hers on this version, and wins.
    r = c.post(f"/matches/{match_id}/review/decisions",
               json={"events_version": 2, "t_ms": doc["events"][0]["t_ms"], "verdict": "right", "decided_by": "g"})
    assert r.status_code == 201
    d = c.get(f"/matches/{match_id}/review").json()["events"][0]["decision"]
    assert d["verdict"] == "right" and d["carried_from"] is None


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


def test_verdicts_are_checked_against_what_exists(reviewed):
    c, match_id = reviewed
    doc = c.get(f"/matches/{match_id}/review").json()
    e = doc["events"][0]
    ok = {"events_version": doc["events_version"], "t_ms": e["t_ms"]}
    # A version that does not exist, an event that is not there, a kind the
    # sport does not know, a track that is not in the tracking version.
    assert c.post(f"/matches/{match_id}/review/decisions", json={**ok, "events_version": 9, "verdict": "right"}).status_code == 422
    assert c.post(f"/matches/{match_id}/review/decisions", json={**ok, "t_ms": 12345, "verdict": "right"}).status_code == 422
    r = c.post(f"/matches/{match_id}/review/decisions", json={**ok, "verdict": "wrong_type", "corrected_type": "slam_dunk"})
    assert r.status_code == 422 and "not an event type" in r.text
    assert c.post(f"/matches/{match_id}/review/decisions", json={**ok, "verdict": "wrong_taker", "corrected_track": 999}).status_code == 422
    other = next(b["track_id"] for b in c.get(f"/matches/{match_id}/review/frames", params={"t_ms": e["t_ms"], "span_s": 0}).json()["frames"][0]["boxes"]
                 if b["cls"] == "player" and b["track_id"] != e["taker_track"])
    r = c.post(f"/matches/{match_id}/review/decisions", json={**ok, "verdict": "wrong_taker", "corrected_track": other})
    assert r.status_code == 201 and r.json()["tracks_version"] == doc["tracks_version"] and r.json()["corrected_track"] == other
    # Kind and taker can both be wrong. That is one row, wrong_type with the
    # taker beside it, and its track is checked like any other.
    both = {**ok, "verdict": "wrong_type", "corrected_type": "free_kick"}
    assert c.post(f"/matches/{match_id}/review/decisions", json={**both, "corrected_track": 999}).status_code == 422
    assert c.post(f"/matches/{match_id}/review/decisions",
                  json={**ok, "verdict": "wrong_taker", "corrected_type": "free_kick", "corrected_track": other}).status_code == 422
    r = c.post(f"/matches/{match_id}/review/decisions", json={**both, "corrected_track": other})
    assert r.status_code == 201
    d = c.get(f"/matches/{match_id}/review").json()["events"][0]["decision"]
    assert (d["verdict"], d["corrected_type"], d["corrected_track"]) == ("wrong_type", "free_kick", other)


def test_names_belong_to_one_tracking_version(reviewed):
    c, match_id = reviewed
    doc = c.get(f"/matches/{match_id}/review").json()
    e = doc["events"][0]
    # A name on a tracking version that does not exist is refused; one on a
    # version that exists but is not the events' version is kept but not shown.
    body = {"tracks_version": 7, "track_id": e["taker_track"], "player_id": "p7"}
    assert c.post(f"/matches/{match_id}/review/tracks", json=body).status_code == 422
    assert c.post(f"/matches/{match_id}/review/tracks", json={**body, "tracks_version": doc["tracks_version"], "track_id": 999}).status_code == 422
    r = c.post(f"/matches/{match_id}/review/tracks", json={**body, "tracks_version": doc["tracks_version"]})
    assert r.status_code == 201
    assert c.get(f"/matches/{match_id}/review").json()["events"][0]["player_id"] == "p7"


def test_a_player_must_be_on_the_roster_when_there_is_one(tmp_path, clip):
    import json

    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    match_id = ingest_local(settings, clip, kickoff_offset_ms=400, stages=STAGES,
                            config={"detector": ScriptedDetector(throw_in_scene), "detect_halftime": False})
    from sideline.config import build_engine
    from sideline.db import Match, session_scope
    with session_scope(build_engine(settings)) as s:
        s.get(Match, match_id).roster = {"p7": {"name": "A", "number": 7}}
    with TestClient(create_app(settings)) as c:
        doc = c.get(f"/matches/{match_id}/review").json()
        body = {"tracks_version": doc["tracks_version"], "track_id": doc["events"][0]["taker_track"]}
        assert c.post(f"/matches/{match_id}/review/tracks", json={**body, "player_id": "p99"}).status_code == 422
        assert c.post(f"/matches/{match_id}/review/tracks", json={**body, "player_id": "p7"}).status_code == 201
        assert c.post(f"/matches/{match_id}/review/tracks", json={**body, "player_id": None}).status_code == 201
        assert doc["roster"]["p7"]["number"] == 7


def test_the_page_carries_a_same_origin_policy_and_can_live_where_the_image_puts_it(reviewed, tmp_path, monkeypatch):
    c, _ = reviewed
    r = c.get("/review")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp and "img-src 'self'" in csp
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "review.html").write_text("<!doctype html><title>Sideline Review</title>moved")
    monkeypatch.setenv("SIDELINE_WEB_DIR", str(other))
    assert "moved" in c.get("/review").text
    monkeypatch.setenv("SIDELINE_WEB_DIR", str(tmp_path / "nowhere"))
    assert c.get("/review").status_code == 404
