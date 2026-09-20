import pyarrow as pa
import pytest

from sideline.contracts import FRAMES_SCHEMA
from sideline.storage import ArtifactRef, ArtifactStore, ImmutableWriteError, LocalBlobStore


def test_layout_is_matches_match_stage_version():
    ref = ArtifactRef("abc", "s1_sampling", 3)
    assert ref.prefix == "matches/abc/s1_sampling/v003/"
    assert ref.key("frames.parquet") == "matches/abc/s1_sampling/v003/frames.parquet"


def test_versions_grow_and_never_overwrite(tmp_path):
    store = ArtifactStore(LocalBlobStore(tmp_path))
    assert store.versions("m", "s0_ingest") == [] and store.latest("m", "s0_ingest") is None
    v1 = store.new_version("m", "s0_ingest")
    assert v1.version == 1
    store.write_json(v1, "match.json", {"a": 1})
    with pytest.raises(ImmutableWriteError):
        store.write_json(v1, "match.json", {"a": 2})
    assert not store.complete(v1)
    store.write_manifest(v1, config={}, inputs=[], capture_mode="followcam", stage_version="0.1.0",
                         pipeline_version="0.1.0", outputs=["match.json"])
    assert store.complete(v1) and store.latest("m", "s0_ingest") == v1
    v2 = store.new_version("m", "s0_ingest")
    assert v2.version == 2 and store.versions("m", "s0_ingest") == [1]
    assert store.read_json(v1, "match.json") == {"a": 1}
    assert store.read_manifest(v1)["inputs"] == [] and store.read_manifest(v1)["outputs"] == ["match.json"]


def test_tables_round_trip_with_schema(tmp_path):
    store = ArtifactStore(LocalBlobStore(tmp_path))
    ref = store.new_version("m", "s1_sampling")
    t = pa.Table.from_pylist([{"frame_index": 0, "t_ms": 0, "source_frame": 0, "key": None}], schema=FRAMES_SCHEMA)
    store.write_table(ref, "frames.parquet", t)
    back = store.read_table(ref, "frames.parquet")
    assert back.schema.equals(FRAMES_SCHEMA) and back.num_rows == 1
    assert store.list(ref) == ["frames.parquet"]


def test_local_store_refuses_keys_outside_root(tmp_path):
    blobs = LocalBlobStore(tmp_path / "blobs")
    with pytest.raises(ValueError):
        blobs.put("../escape", b"x")
    blobs.put("a/b.txt", b"hi")
    assert blobs.get("a/b.txt") == b"hi" and blobs.exists("a/b.txt") and not blobs.exists("a/c.txt")
    assert blobs.list("a") == ["a/b.txt"] and blobs.local_path("a/b.txt") is not None
