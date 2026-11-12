"""Verdicts carried from one events version to the next.

A reviewer's verdict is a row keyed by the events version it was given on
and the event's time. A re-run of S8 writes a new version whose events sit
at other times: better rules drop some, merge others, and move the moment
of release. The verdicts are not rewritten for it. They are read again
through the new version: the old verdict speaks for the new event whose
dead ball covers the moment it was given on, and it is shown as carried,
so the reviewer can see which answers are hers on this version and which
were inherited. A verdict given on the new version itself always wins.

Nothing here knows the sport, and nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Seen:
    """An event as one version wrote it: when the ball was put back into
    play, since when it had been dead, what it was called and who took it."""

    t_ms: int
    start_ms: int
    type: str
    track_id: Optional[int]


def seen(rows: Sequence[dict], log: Sequence[dict]) -> list[Seen]:
    """The events of one version, from its `events.parquet` rows and the
    cue log beside them (`dead_balls.json`), which holds how long each ball
    had been still."""
    return [Seen(int(r["t_ms"]), int(r["t_ms"] - float(l.get("still_s") or 0.0) * 1000), r["type"], r["track_id"])
            for r, l in zip(rows, log)]


def _said(verdict: dict, old: Seen, new: Seen, same_tracks: bool) -> dict:
    """An old verdict restated about the new event. What the reviewer said
    is a fact about the moment (nothing happened; it was a throw-in; she
    took it), and the verdict that states it depends on what the new
    version calls the event."""
    if verdict["verdict"] == "not_event":
        return {"verdict": "not_event", "corrected_type": None, "corrected_track": None}
    kind = verdict.get("corrected_type") if verdict["verdict"] == "wrong_type" else old.type
    if kind != new.type:
        return {"verdict": "wrong_type", "corrected_type": kind, "corrected_track": None}
    track = verdict.get("corrected_track") if verdict["verdict"] == "wrong_taker" else None
    # A track id means something only within the tracking version it was tapped in.
    if track is not None and same_tracks and track != new.track_id:
        return {"verdict": "wrong_taker", "corrected_type": None, "corrected_track": track}
    return {"verdict": "right", "corrected_type": None, "corrected_track": None}


def carry(old: Sequence[tuple[Seen, dict]], new: Sequence[Seen], same_tracks: bool,
          slack_ms: int = 1200, near_ms: int = 2000) -> dict[int, dict]:
    """Old verdicts onto new events, by time: `{new t_ms: verdict}`.

    `old` pairs each reviewed event of an earlier version with its newest
    verdict. A verdict speaks for a new event when the moment it was given
    on falls inside that event's dead ball (from the ball coming to rest to
    its release, with `slack_ms` either side for a sample or two of
    difference), or within `near_ms` of its release. Where several do, the
    one nearest the release speaks: a long dead ball that swallowed an
    earlier false alarm is judged by what was said about its end. Each
    carried verdict records the time it was given on, `from_t_ms`."""
    out: dict[int, dict] = {}
    for e in new:
        best: Optional[tuple[int, Seen, dict]] = None
        for o, v in old:
            inside = e.start_ms - slack_ms <= o.t_ms <= e.t_ms + slack_ms
            d = abs(e.t_ms - o.t_ms)
            if (inside or d <= near_ms) and (best is None or d < best[0]):
                best = (d, o, v)
        if best is not None:
            _, o, v = best
            out[e.t_ms] = {**_said(v, o, e, same_tracks), "from_t_ms": o.t_ms}
    return out
