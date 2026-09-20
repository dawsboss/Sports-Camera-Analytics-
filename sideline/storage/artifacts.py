"""Stage artifacts: `matches/{match_id}/{stage}/{version}/`.

Every stage reads named artifacts, writes named artifacts, and records the
config it ran with in the version's manifest. A re-run is a new version
directory; nothing is overwritten, so re-running S8 never disturbs what S2
produced and a review decision can point at an artifact that will still be
there next month.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from sideline.storage.blobs import BlobStore

_VERSION = re.compile(r"^v(\d{3,})$")


@dataclass(frozen=True)
class ArtifactRef:
    match_id: str
    stage: str
    version: int

    @property
    def prefix(self) -> str:
        return f"matches/{self.match_id}/{self.stage}/v{self.version:03d}/"

    def key(self, name: str) -> str:
        return self.prefix + name

    def as_dict(self) -> dict:
        return {"match_id": self.match_id, "stage": self.stage, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "ArtifactRef":
        return cls(d["match_id"], d["stage"], int(d["version"]))


class ArtifactStore:
    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    def versions(self, match_id: str, stage: str) -> list[int]:
        prefix = f"matches/{match_id}/{stage}/"
        seen = set()
        for key in self.blobs.list(prefix):
            rest = key[len(prefix):].split("/", 1)[0]
            m = _VERSION.match(rest)
            if m:
                seen.add(int(m.group(1)))
        return sorted(seen)

    def latest(self, match_id: str, stage: str) -> Optional[ArtifactRef]:
        v = self.versions(match_id, stage)
        return ArtifactRef(match_id, stage, v[-1]) if v else None

    def new_version(self, match_id: str, stage: str) -> ArtifactRef:
        v = self.versions(match_id, stage)
        return ArtifactRef(match_id, stage, (v[-1] + 1) if v else 1)

    def complete(self, ref: ArtifactRef) -> bool:
        """A version is complete once its manifest is written, which happens last."""
        return self.blobs.exists(ref.key("manifest.json"))

    # -- writing ------------------------------------------------------------

    def write_manifest(
        self, ref: ArtifactRef, *, config: dict, inputs: list[ArtifactRef], capture_mode: str,
        stage_version: str, pipeline_version: str, outputs: list[str], extra: Optional[dict] = None,
    ) -> dict:
        manifest = {
            **ref.as_dict(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "capture_mode": capture_mode,
            "stage_version": stage_version,
            "pipeline_version": pipeline_version,
            "config": config,
            "inputs": [i.as_dict() for i in inputs],
            "outputs": sorted(outputs),
            **(extra or {}),
        }
        self.write_json(ref, "manifest.json", manifest)
        return manifest

    def write_json(self, ref: ArtifactRef, name: str, obj: Any) -> str:
        key = ref.key(name)
        self.blobs.put(key, json.dumps(obj, indent=2, default=str).encode(), "application/json")
        return key

    def write_table(self, ref: ArtifactRef, name: str, table: pa.Table) -> str:
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        key = ref.key(name)
        self.blobs.put(key, buf.getvalue(), "application/vnd.apache.parquet")
        return key

    def write_bytes(self, ref: ArtifactRef, name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        key = ref.key(name)
        self.blobs.put(key, data, content_type)
        return key

    # -- reading ------------------------------------------------------------

    def read_manifest(self, ref: ArtifactRef) -> dict:
        return self.read_json(ref, "manifest.json")

    def read_json(self, ref: ArtifactRef, name: str) -> Any:
        return json.loads(self.blobs.get(ref.key(name)))

    def read_table(self, ref: ArtifactRef, name: str) -> pa.Table:
        return pq.read_table(io.BytesIO(self.blobs.get(ref.key(name))))

    def list(self, ref: ArtifactRef) -> list[str]:
        return [k[len(ref.prefix):] for k in self.blobs.list(ref.prefix)]
