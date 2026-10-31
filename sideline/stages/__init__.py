"""The pipeline stages, looked up by name. Each is a separately queued job
that reads named artifacts and writes a new version of its own."""

from __future__ import annotations

from sideline.stages.base import MatchRecord, Stage, StageContext
from sideline.stages.s0_ingest import IngestStage
from sideline.stages.s1_sampling import SamplingStage
from sideline.stages.s2_detection import DetectionStage
from sideline.stages.s3_tracking import TrackingStage
from sideline.stages.s8_events import EventsStage

STAGES: dict[str, Stage] = {
    IngestStage.name: IngestStage(),
    SamplingStage.name: SamplingStage(),
    DetectionStage.name: DetectionStage(),
    TrackingStage.name: TrackingStage(),
    EventsStage.name: EventsStage(),
}

# Every stage, in the order its inputs are made.
ORDER = [IngestStage.name, SamplingStage.name, DetectionStage.name, TrackingStage.name, EventsStage.name]

# What an upload runs when it asks for nothing in particular. Detection needs
# a model and a GPU-sized budget, so it is asked for by name.
DEFAULT_STAGES = [IngestStage.name, SamplingStage.name]

__all__ = ["DEFAULT_STAGES", "MatchRecord", "ORDER", "STAGES", "Stage", "StageContext"]
