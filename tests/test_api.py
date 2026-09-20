import json

import pytest
from fastapi.testclient import TestClient

from sideline.api.app import create_app
from sideline.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", inline_jobs=True)
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["stages"] == ["s0_ingest", "s1_sampling"]


def test_upload_runs_s0_and_s1_and_answers_what_exists(client, clip):
    with clip.open("rb") as f:
        r = client.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={
            "capture_mode": "followcam", "sm_match_id": "sm-42", "pitch_length": "100", "pitch_width": "64",
            "kickoff_offset_ms": "500",
            "roster": json.dumps({"p1": {"name": "A", "number": 7, "team": "home"}}),
            "stints": json.dumps({"s1": {"pid": "p1", "on": 0}}),
            "periods": json.dumps({"0": {"half": 1, "start": 1700000000000}}),
        })
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["sm_match_id"] == "sm-42" and m["pitch_length"] == 100.0
    assert [j["stage"] for j in m["jobs"]] == ["s0_ingest", "s1_sampling"]
    assert all(j["status"] == "done" and j["artifact_version"] == 1 for j in m["jobs"])
    assert m["artifacts"] == {"s0_ingest": [1], "s1_sampling": [1]}

    r = client.get(f"/matches/{m['id']}")
    assert r.status_code == 200 and r.json()["id"] == m["id"]
    assert client.get("/matches").json()[0]["id"] == m["id"]

    r = client.get(f"/matches/{m['id']}/artifacts/s1_sampling/1")
    assert r.status_code == 200
    body = r.json()
    assert body["manifest"]["frames"] == 15 and "frames.parquet" in body["files"]

    doc = client.get(f"/matches/{m['id']}/artifacts/s0_ingest/1/match.json").json()
    assert doc["sm_match_id"] == "sm-42" and doc["kickoff_offset_ms"] == 500

    job = client.get(f"/jobs/{m['jobs'][0]['id']}").json()
    assert job["status"] == "done"
    assert client.get(f"/matches/{m['id']}/artifacts/s1_sampling/9").status_code == 404
    assert client.get(f"/matches/{m['id']}/artifacts/s1_sampling/1/frames.parquet").status_code == 415


def test_bad_inputs_are_refused(client, clip):
    with clip.open("rb") as f:
        r = client.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"capture_mode": "drone"})
    assert r.status_code == 422
    with clip.open("rb") as f:
        r = client.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"roster": "{not json"})
    assert r.status_code == 422
    with clip.open("rb") as f:
        r = client.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"stages": "s9_publish"})
    assert r.status_code == 422
    assert client.get("/matches/nope").status_code == 404


def test_upload_without_stages_only_registers(client, clip):
    with clip.open("rb") as f:
        r = client.post("/matches", files={"file": ("clip.mp4", f, "video/mp4")}, data={"run": "false"})
    assert r.status_code == 201 and r.json()["jobs"] == [] and r.json()["artifacts"] == {"s0_ingest": [], "s1_sampling": []}
