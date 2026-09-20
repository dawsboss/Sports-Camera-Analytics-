"""The output contract: what every stage writes and what soccer-manager reads.

Three artifacts leave the pipeline — tracks.parquet, events.parquet and
player_stats.json — and everything downstream reads only these. The schemas
here are the spec's tables, verbatim. Two rules the types enforce rather than
trust a reviewer to notice:

- Every aggregate carries its sample count, its capture mode and a partial
  flag. A coach must never see a distance number without knowing it covers
  40% of the match, so an aggregate without those fields cannot be built.
- The stats the spec lists as impossible on follow-cam footage cannot be
  populated when the capture mode is followcam. The contract does not change
  between modes; only how much of it is populated does.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

import pyarrow as pa
from pydantic import BaseModel, Field, model_validator


class CaptureMode(str, Enum):
    """Which camera produced the footage. Recorded on every artifact because
    the mode decides which outputs are trustworthy."""

    STATIC = "static"        # Mode A: fixed camera, whole surface, the product
    FOLLOWCAM = "followcam"  # Mode B: Veo auto-cropped follow view, the PoC input


class Team(str, Enum):
    HOME = "home"
    AWAY = "away"
    GK_HOME = "gk_home"
    GK_AWAY = "gk_away"
    OFFICIAL = "official"


EVENT_TYPES: tuple[str, ...] = ("touch", "duel", "shot", "third_entry", "out_of_play")

# One row per player per sampled frame. Coordinates are metres with the origin
# at the centre spot, x along the length of the pitch, y across it.
TRACKS_SCHEMA = pa.schema(
    [
        pa.field("t_ms", pa.int64(), nullable=False),
        pa.field("match_minute", pa.float32(), nullable=True),
        pa.field("track_id", pa.int32(), nullable=False),
        pa.field("player_id", pa.string(), nullable=True),
        pa.field("team", pa.dictionary(pa.int8(), pa.string()), nullable=True),
        pa.field("x_pitch", pa.float32(), nullable=True),
        pa.field("y_pitch", pa.float32(), nullable=True),
        pa.field("conf_det", pa.float32(), nullable=False),
        pa.field("conf_reg", pa.float32(), nullable=False),
        pa.field("visible", pa.bool_(), nullable=False),
    ]
)

# Ball-anchored occurrences, one row each.
EVENTS_SCHEMA = pa.schema(
    [
        pa.field("t_ms", pa.int64(), nullable=False),
        pa.field("type", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("x_pitch", pa.float32(), nullable=True),
        pa.field("y_pitch", pa.float32(), nullable=True),
        pa.field("player_id", pa.string(), nullable=True),
        pa.field("team", pa.dictionary(pa.int8(), pa.string()), nullable=True),
        pa.field("confidence", pa.float32(), nullable=False),
    ]
)

# S1's index of sampled frames. `key` is where the decoded frame was cached in
# the blob store, or null when frames are decoded on demand.
FRAMES_SCHEMA = pa.schema(
    [
        pa.field("frame_index", pa.int32(), nullable=False),
        pa.field("t_ms", pa.int64(), nullable=False),
        pa.field("source_frame", pa.int64(), nullable=False),
        pa.field("key", pa.string(), nullable=True),
    ]
)

# S5's artifact: a per-frame mapping plus a confidence score. Both registration
# implementations write exactly this. `h` is the 3x3 image->pitch homography
# in row-major order; null when the frame could not be registered or when the
# mapping is a thin-plate spline, which lives in the stage's calibration.json.
REGISTRATION_SCHEMA = pa.schema(
    [
        pa.field("frame_index", pa.int32(), nullable=False),
        pa.field("t_ms", pa.int64(), nullable=False),
        pa.field("method", pa.string(), nullable=False),
        pa.field("confidence", pa.float32(), nullable=False),
        pa.field("n_lines", pa.int16(), nullable=False),
        pa.field("h", pa.list_(pa.float64(), 9), nullable=True),
    ]
)

# S2's artifact. Boxes are image pixels; class names come from the sport
# plugin's entity classes.
DETECTIONS_SCHEMA = pa.schema(
    [
        pa.field("frame_index", pa.int32(), nullable=False),
        pa.field("t_ms", pa.int64(), nullable=False),
        pa.field("cls", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("x1", pa.float32(), nullable=False),
        pa.field("y1", pa.float32(), nullable=False),
        pa.field("x2", pa.float32(), nullable=False),
        pa.field("y2", pa.float32(), nullable=False),
        pa.field("conf", pa.float32(), nullable=False),
    ]
)


class PitchDimensions(BaseModel):
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)


class Halftime(BaseModel):
    start_ms: int
    end_ms: int
    method: str


class MatchJson(BaseModel):
    """S0's artifact. Everything a later stage needs to know about the file
    without opening it, plus the accompanying inputs the coach supplied."""

    match_id: str
    sm_match_id: Optional[str] = None
    capture_mode: CaptureMode
    source_key: str
    video_key: str
    duration_ms: int
    fps: float
    width: int
    height: int
    frame_count: int
    codec: Optional[str] = None
    transcoded: bool
    halftime: Optional[Halftime] = None
    kickoff_offset_ms: Optional[int] = None
    pitch: PitchDimensions


class Aggregate(BaseModel):
    """One number a coach will see, and the three facts that make it honest."""

    value: Optional[float]
    sample_count: int = Field(ge=0)
    capture_mode: CaptureMode
    partial: bool


class ZoneHistogram(BaseModel):
    """Counts over the 18-cell grid (6 along the length x 3 across the width)."""

    cells: list[int] = Field(min_length=18, max_length=18)
    sample_count: int = Field(ge=0)
    capture_mode: CaptureMode
    partial: bool


# The spec's "impossible in Mode B" list. Each requires continuous observation
# of the player or of the whole team, which a follow-cam never gives.
MODE_A_ONLY_STATS: tuple[str, ...] = (
    "total_distance",
    "sprints",
    "top_speed",
    "defensive_line_height",
)


class PlayerStats(BaseModel):
    player_id: str
    team: Team
    capture_mode: CaptureMode
    visible_seconds: Aggregate
    visible_pct: Aggregate
    touches: Aggregate
    duels_contested: Aggregate
    position_samples: int = Field(ge=0)
    avg_x: Aggregate
    avg_y: Aggregate
    zone_histogram: ZoneHistogram
    distance_covered_visible: Aggregate
    confidence_grade: Literal["A", "B", "C"]
    # Mode A only. Present in the schema so the contract is the same in both
    # modes; refused with a value on follow-cam footage.
    total_distance: Optional[Aggregate] = None
    sprints: Optional[Aggregate] = None
    top_speed: Optional[Aggregate] = None
    defensive_line_height: Optional[Aggregate] = None

    @model_validator(mode="after")
    def _honest_in_followcam(self) -> "PlayerStats":
        if self.capture_mode is CaptureMode.FOLLOWCAM:
            for name in MODE_A_ONLY_STATS:
                agg = getattr(self, name)
                if agg is not None and agg.value is not None:
                    raise ValueError(
                        f"{name} cannot be shipped from follow-cam footage; "
                        "it needs continuous observation the crop never gives"
                    )
            if not self.distance_covered_visible.partial:
                raise ValueError(
                    "distance_covered_visible must be flagged partial on follow-cam footage"
                )
        for name in (
            "visible_seconds", "visible_pct", "touches", "duels_contested",
            "avg_x", "avg_y", "distance_covered_visible",
        ):
            agg: Aggregate = getattr(self, name)
            if agg.capture_mode is not self.capture_mode:
                raise ValueError(f"{name} records a different capture mode than the player")
        return self


class PlayerStatsDoc(BaseModel):
    """player_stats.json: the only thing that reaches Firebase."""

    match_id: str
    sm_match_id: Optional[str] = None
    capture_mode: CaptureMode
    produced_at: datetime
    pipeline_version: str
    stage_versions: dict[str, str] = Field(default_factory=dict)
    players: dict[str, PlayerStats]

    @model_validator(mode="after")
    def _one_mode(self) -> "PlayerStatsDoc":
        for pid, p in self.players.items():
            if p.capture_mode is not self.capture_mode:
                raise ValueError(f"player {pid} was produced in a different capture mode")
        return self
