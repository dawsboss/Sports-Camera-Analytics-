"""Metadata and orchestration. Small, relational, queryable, and never a
trajectory row: those live in Parquet.

Review decisions are rows here, never edits to an artifact. When a human
assigns fragment 847 to a player that is a `ReviewDecision`; re-running
identity resolution with a better model replays these on top, which is what
makes review time cumulative rather than wasted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    sm_match_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    sport: Mapped[str] = mapped_column(String(32), default="soccer")
    capture_mode: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(512))
    # Where the video came from when it was fetched rather than uploaded:
    # a direct link to the file. Empty source_key until the fetch job lands it.
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pitch_length: Mapped[float] = mapped_column(Float, default=105.0)
    pitch_width: Mapped[float] = mapped_column(Float, default=68.0)
    kickoff_offset_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Snapshots of what the minutes app knew at upload: roster, substitution
    # stints and periods. Copied, not linked, so the analysis of a match is
    # not changed by a roster edit a month later.
    roster: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    stints: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    periods: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    jobs: Mapped[list["Job"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    runs: Mapped[list["StageRun"]] = relationship(back_populates="match", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued | running | done | failed
    # How far a running job has got, 0 to 1, and what it is doing, so a
    # coach watching a two-hour transcode sees it move.
    progress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    queue_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    match: Mapped[Match] = relationship(back_populates="jobs")


class StageRun(Base):
    """One version directory of one stage: what it ran with, what it read."""

    __tablename__ = "stage_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    stage_version: Mapped[str] = mapped_column(String(32))
    pipeline_version: Mapped[str] = mapped_column(String(32))
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    inputs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    match: Mapped[Match] = relationship(back_populates="runs")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    tracks_version: Mapped[int] = mapped_column(Integer)   # which S3 output the track id belongs to
    track_id: Mapped[int] = mapped_column(Integer)
    player_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # null = "not a player"
    decided_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EventDecision(Base):
    """A human's verdict on one event the pipeline wrote: right, wrong kind,
    wrong taker, or not an event at all. Kind and taker can both be wrong:
    that row is `wrong_type` with `corrected_track` set beside
    `corrected_type`, so a reader of kinds alone reads it as before. An
    event has no id of its own, so
    it is keyed by the events version it sits in and its time, which is
    unique within a version because dead balls closer than two seconds are
    merged. A new events version starts with no rows of its own; these
    are read through it by time (`sideline/verdicts.py`), never copied
    or rewritten, and never an edit to the Parquet."""

    __tablename__ = "event_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    events_version: Mapped[int] = mapped_column(Integer)
    tracks_version: Mapped[int] = mapped_column(Integer)      # the S3 output the event's tracks belong to
    t_ms: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(16))          # right | wrong_type | wrong_taker | not_event; wrong_type may also correct the taker
    corrected_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    corrected_track: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # a track of tracks_version
    decided_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
