"""Video time to match time.

soccer-manager keeps the clock as periods of epoch milliseconds and records
substitutions in seconds of elapsed match time, so the pipeline needs one
bridge: the video timestamp at which the first period started. Everything
else — which half a frame belongs to, who was on the pitch when it was taken —
follows from that offset and the periods the minutes app already has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Period:
    half: int
    start_ms: int             # epoch ms, from soccer-manager's periods/{n}/start
    end_ms: Optional[int]     # epoch ms; None while the period is open


@dataclass(frozen=True)
class Stint:
    player_id: str
    on_s: float               # seconds of elapsed match time
    off_s: Optional[float]    # None while the player is still on


def wall_ms(t_ms: int, kickoff_offset_ms: int, first_period_start_ms: int) -> int:
    """Map a video timestamp to wall-clock epoch ms."""
    return first_period_start_ms + (t_ms - kickoff_offset_ms)


def elapsed_s(wall: int, periods: Iterable[Period]) -> float:
    """Seconds of match time elapsed at a wall-clock instant.

    This is the same `s.end || now` rule soccer-manager uses: a closed period
    contributes its full length, the open one contributes up to `wall`, and
    time between periods (half-time) counts for nothing.
    """
    total = 0.0
    for p in sorted(periods, key=lambda p: p.start_ms):
        if wall < p.start_ms:
            break
        end = p.end_ms if p.end_ms is not None else wall
        total += max(0, min(wall, end) - p.start_ms) / 1000.0
    return total


def match_seconds(
    t_ms: int, kickoff_offset_ms: int, periods: list[Period]
) -> Optional[float]:
    """Elapsed match seconds for a video timestamp, or None before kickoff."""
    if not periods:
        return None
    first = min(periods, key=lambda p: p.start_ms)
    w = wall_ms(t_ms, kickoff_offset_ms, first.start_ms)
    if w < first.start_ms:
        return None
    return elapsed_s(w, periods)


def in_play(wall: int, periods: Iterable[Period]) -> bool:
    """True when the clock is running at that instant (not half-time, not full-time)."""
    for p in periods:
        end = p.end_ms
        if p.start_ms <= wall and (end is None or wall < end):
            return True
    return False


def on_pitch(elapsed: float, stints: Iterable[Stint]) -> set[str]:
    """Who was on the pitch at a given elapsed match second.

    Stints are the only truth for that in soccer-manager, and they are the
    strongest constraint identity resolution has: eleven candidates per team
    at any instant rather than the whole roster.
    """
    out: set[str] = set()
    for s in stints:
        if s.on_s <= elapsed and (s.off_s is None or elapsed < s.off_s):
            out.add(s.player_id)
    return out


def periods_from_sm(node: dict) -> list[Period]:
    """Parse soccer-manager's matches/{id}/periods node."""
    out = []
    for key, p in (node or {}).items():
        if not isinstance(p, dict) or "start" not in p:
            continue
        out.append(Period(half=int(p.get("half", 1)), start_ms=int(p["start"]),
                          end_ms=int(p["end"]) if p.get("end") else None))
    return sorted(out, key=lambda p: p.start_ms)


def stints_from_sm(node: dict) -> list[Stint]:
    """Parse soccer-manager's matches/{id}/stints node."""
    out = []
    for key, s in (node or {}).items():
        if not isinstance(s, dict) or "pid" not in s:
            continue
        off = s.get("off")
        out.append(Stint(player_id=str(s["pid"]), on_s=float(s.get("on", 0)),
                         off_s=float(off) if off is not None else None))
    return sorted(out, key=lambda s: s.on_s)
