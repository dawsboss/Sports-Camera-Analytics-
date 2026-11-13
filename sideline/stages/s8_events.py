"""S8 — Events (the first half of S8; the aggregates are the second).

The sport plugin's rules run over S3's fragments and write `events.parquet`.
Today that is restarts: every moment the ball sat still and was put back
into play, named by the plugin from what was visible (`sideline/events.py`
measures, the plugin names). Pitch coordinates stay null until registration
can place the frame; the event is real without them, which is the point.
Each event carries the taker as a `track_id`. The name arrives as a review
decision on that track, or from identity resolution, never by editing this
file; a re-run after S6 fills `player_id` in a new version.

Beside the restarts it writes `possession.json`: every spell of the ball
with one fragment (`sideline/possession.py`), with how many samples had a
ball in them at all. It is not in `events.parquet`: a spell has a start
and an end and no event type of the sport's yet names it. A pass is one
spell ending loose and a team-mate's beginning, which waits for teams.

Beside the Parquet the stage writes `events.csv`, the same rows as a coach
would write them down: the match clock, what happened, who, how sure. The
clock comes through the timeline from the kickoff offset and the copied
periods; without those it is video time and the column says so.

The stage knows nothing about the sport and nothing about the camera.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict

import numpy as np
import pyarrow as pa

from sideline.contracts import EVENTS_SCHEMA
from sideline.events import (
    camera_shifts_from_people,
    camera_shifts_from_registration,
    find_breaks,
    find_dead_balls,
    frames_from_rows,
    merge_shifts,
)
from sideline.possession import find_possessions, possession_doc
from sideline.sports import get_sport
from sideline.stages.base import StageContext
from sideline.storage.artifacts import ArtifactRef
from sideline.timeline import MARK_SLACK_MS, breaks_between, match_seconds, period_starts_video_ms, periods_from_marks, periods_from_sm


def clock(t_ms: int, kickoff_offset_ms, periods) -> tuple[str, str]:
    """A time a coach reads: match minute and second when the timeline is
    known, else seconds into the video. Returns (label, basis)."""
    if kickoff_offset_ms is not None:
        s = match_seconds(t_ms, int(kickoff_offset_ms), periods) if periods else max(0.0, (t_ms - int(kickoff_offset_ms)) / 1000.0)
        if s is not None:
            return f"{int(s // 60):02d}:{int(s % 60):02d}", "match"
    s = t_ms / 1000.0
    return f"{int(s // 60):02d}:{int(s % 60):02d}", "video"


def write_log(rows: list[dict], kickoff_offset_ms, periods) -> str:
    """events.csv: one line per event, oldest first."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["clock", "clock_basis", "t_ms", "event", "taker_track", "player_id", "team", "confidence", "reason"])
    for r in rows:
        label, basis = clock(r["t_ms"], kickoff_offset_ms, periods)
        w.writerow([label, basis, r["t_ms"], r["type"], "" if r["track_id"] is None else r["track_id"],
                    r["player_id"] or "", r["team"] or "", f"{r['confidence']:.2f}", r["reason"]])
    return buf.getvalue()


class EventsStage:
    name = "s8_events"
    version = "0.4.0"

    def run(self, ctx: StageContext) -> ArtifactRef:
        m = ctx.match
        s3 = ctx.artifacts.latest(m.id, "s3_tracking")
        if s3 is None or not ctx.artifacts.complete(s3):
            raise RuntimeError("s8_events needs a completed s3_tracking version")
        s0 = ctx.artifacts.latest(m.id, "s0_ingest")
        if s0 is None:
            raise RuntimeError("s8_events needs s0_ingest for the frame size and the timeline")
        match = ctx.artifacts.read_json(s0, "match.json")
        sport = get_sport(m.sport)
        surface = sport.surface(m.pitch_length, m.pitch_width)
        grammar = sport.event_grammar(surface)
        object_classes = [c.name for c in sport.entity_classes() if c.object]
        landmark_classes = [c.name for c in sport.entity_classes() if c.landmark]

        # Period starts in video time. What a reviewer marked on the film
        # comes first: she saw the kick-offs. Otherwise the timeline: the
        # kickoff the coach supplied and the periods the minutes app
        # recorded, copied at upload; without those, S0's half-time hint
        # stands in for the second period; without a kickoff offset there
        # is no period context and no kick-off can be named.
        starts: list[int] = []
        source = "none"
        kickoff, clock_periods = match.get("kickoff_offset_ms"), periods_from_sm(m.periods or {})
        marked = periods_from_marks(m.marks or [], kickoff)
        if marked is not None:
            kickoff, clock_periods = marked
            starts, source = [p.start_ms for p in clock_periods], "marks"
        elif kickoff is not None:
            k0 = int(kickoff)
            if clock_periods:
                starts, source = period_starts_video_ms(k0, clock_periods), "periods"
            else:
                starts, source = [k0], "kickoff"
                if match.get("halftime"):
                    starts.append(int(match["halftime"]["end_ms"]))
                    source = "kickoff+halftime-hint"

        rows = ctx.artifacts.read_table(s3, "fragments.parquet").to_pylist()
        frames = frames_from_rows(rows, object_classes, landmark_classes)
        size = (match["width"], match["height"])

        # The camera's motion between samples. Registration is the stage that
        # knows the camera: where it has placed consecutive frames the motion
        # is read from its homographies (zero on a fixed camera); elsewhere
        # it is estimated from the people. Nothing here asks which camera.
        people_shifts = camera_shifts_from_people(frames)
        from_registration = 0
        s5 = ctx.artifacts.latest(m.id, "s5_registration")
        if s5 is not None and ctx.artifacts.complete(s5) and ctx.blobs.exists(s5.key("registration.parquet")):
            reg = ctx.artifacts.read_table(s5, "registration.parquet").to_pylist()
            h_by_frame = {r["frame_index"]: np.array(r["h"], dtype=float).reshape(3, 3) for r in reg if r.get("h")}
            shifts, from_registration = merge_shifts(camera_shifts_from_registration(frames, h_by_frame, size), people_shifts)
        else:
            shifts = people_shifts

        # Stretches in which play had stopped, and no dead ball in one is a
        # restart: between the periods a reviewer marked and after the last
        # of them, and wherever the tracking shows nobody moving for minutes.
        # The second kind does not say where a period began. On the second
        # reviewed match the first dead ball after such a break was a false
        # alarm, forty-five seconds before the kick-off.
        breaks = find_breaks(frames, grammar, shifts)
        if marked is not None and frames:
            # A marked start is where play began again, whatever the people
            # were still doing: a break the tracking found stops short of it.
            for s in starts:
                breaks = [(a, min(b, s - MARK_SLACK_MS)) if a < s <= b + MARK_SLACK_MS else (a, b) for a, b in breaks]
            breaks = sorted([b for b in breaks if b[1] > b[0]] + breaks_between(clock_periods, frames[-1].t_ms + 1))
        if ctx.config.get("period_starts_ms"):
            starts, source = [int(x) for x in ctx.config["period_starts_ms"]], "config"
        dead = find_dead_balls(frames, grammar, size, period_starts_ms=starts,
                               max_gap_samples=int(ctx.config.get("max_gap_samples", 3)),
                               merge_s=float(ctx.config.get("merge_s", 2.0)), shifts=shifts, breaks_ms=breaks)

        spells = find_possessions(frames, grammar)
        possession = possession_doc(frames, spells, m.capture_mode)

        events, log = [], []
        for obs in dead:
            call = sport.classify_restart(obs)
            conf = call.confidence * (1.0 if obs.release_speed_h is not None else 0.7)
            events.append({"t_ms": obs.t_ms, "type": call.type, "x_pitch": None, "y_pitch": None, "player_id": None,
                           "team": obs.taker_team, "confidence": float(conf), "track_id": obs.taker_track})
            log.append({**asdict(obs), "call": asdict(call), "confidence": float(conf)})

        ref = ctx.artifacts.new_version(m.id, self.name)
        ctx.artifacts.write_table(ref, "events.parquet", pa.Table.from_pylist(events, schema=EVENTS_SCHEMA))
        ctx.artifacts.write_json(ref, "dead_balls.json", log)
        ctx.artifacts.write_json(ref, "possession.json", possession)
        csv_rows = [{**e, "reason": l["call"]["reason"]} for e, l in zip(events, log)]
        ctx.artifacts.write_bytes(ref, "events.csv",
                                  write_log(csv_rows, kickoff, clock_periods).encode(),
                                  "text/csv")
        ctx.artifacts.write_manifest(
            ref, config={"period_starts_ms": starts, "period_source": source,
                         "breaks_ms": [list(b) for b in breaks],
                         "max_gap_samples": int(ctx.config.get("max_gap_samples", 3)),
                         "merge_s": float(ctx.config.get("merge_s", 2.0)),
                         "grammar": {k: getattr(grammar, k) for k in (
                             "dead_ball_min_s", "still_radius_h", "release_speed_h", "settled_s", "reach_h", "held_rel_y",
                             "held_above_h", "near_h", "object_min_h", "touch_h", "strike_window_s",
                             "possession_min_s", "possession_carry_s", "moving_h", "break_min_s",
                             "break_moving_frac")},
                         "landmark_classes": landmark_classes},
            inputs=[i for i in (s3, s0, s5 if from_registration else None) if i is not None],
            capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version, outputs=["events.parquet", "events.csv", "dead_balls.json", "possession.json"],
            extra={"events": len(events), "events_by_type": dict(Counter(e["type"] for e in events)),
                   "frames": len(frames), "possession_spells": len(spells),
                   "possession": {k: possession[k] for k in ("sample_count", "samples_with_ball",
                                                             "samples_in_possession", "partial")},
                   "camera_shifts_from_registration": from_registration,
                   "camera_shifts_from_people": max(0, len(frames) - 1 - from_registration)},
        )
        return ref
