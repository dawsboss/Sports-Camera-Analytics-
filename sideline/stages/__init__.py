"""The pipeline stages, looked up by name. Each is a separately queued job
that reads named artifacts and writes a new version of its own."""

from __future__ import annotations

from sideline.stages.base import MatchRecord, Stage, StageContext
from sideline.stages.s0_ingest import IngestStage
from sideline.stages.s1_sampling import SamplingStage

STAGES: dict[str, Stage] = {
    IngestStage.name: IngestStage(),
    SamplingStage.name: SamplingStage(),
}

ORDER = [IngestStage.name, SamplingStage.name]

__all__ = ["MatchRecord", "ORDER", "STAGES", "Stage", "StageContext"]
