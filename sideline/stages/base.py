from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from sideline.storage.artifacts import ArtifactRef, ArtifactStore
from sideline.storage.blobs import BlobStore


@dataclass(frozen=True)
class MatchRecord:
    """What a stage may know about the match. A plain copy of the database
    row, so stages never hold a session open across a long decode."""

    id: str
    capture_mode: str
    source_key: str
    sport: str = "soccer"
    sm_match_id: Optional[str] = None
    pitch_length: float = 105.0
    pitch_width: float = 68.0
    kickoff_offset_ms: Optional[int] = None


@dataclass
class StageContext:
    match: MatchRecord
    artifacts: ArtifactStore
    blobs: BlobStore
    workdir: Path
    pipeline_version: str = "0.1.0"
    config: dict = field(default_factory=dict)

    def fetch(self, key: str, name: Optional[str] = None) -> Path:
        """A local path for a blob: the store's own file when it has one,
        otherwise a copy in the work directory."""
        local = self.blobs.local_path(key)
        if local is not None:
            return local
        return self.blobs.get_to_file(key, self.workdir / (name or key.replace("/", "_")))


class Stage(Protocol):
    name: str
    version: str

    def run(self, ctx: StageContext) -> ArtifactRef: ...
