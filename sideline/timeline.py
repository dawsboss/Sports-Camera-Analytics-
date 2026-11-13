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

import math


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


# What a reviewer can mark on the film: a period starts, play breaks off
# between periods, the match ends. What a sport calls them is its own
# (`EventGrammar.period_words`).
MARK_KINDS: tuple[str, ...] = ("start", "break", "end")
# A start is marked on the frame the kick was seen in; the dead ball it
# belongs to is timed at when the ball was last seen at rest, seconds
# earlier. The same margin the first whistle gets for being typed by hand.
MARK_SLACK_MS = 5000


def periods_from_marks(marks: Iterable[dict], kickoff_offset_ms: Optional[int] = None) -> Optional[tuple[int, list[Period]]]:
    """The periods a reviewer marked on the film, as (kickoff offset,
    periods) with the periods on the video's own clock, so the offset is
    simply where the first one starts and everything that takes an offset
    and periods reads them unchanged. None when the marks open no period.

    A start opens a period, closing one still open at the same instant; a
    break or the end closes the open one; nothing counts after the end.
    The kickoff typed at upload opens the first period when the earliest
    mark is not itself a start, so marking half-time, the second kick-off
    and the last whistle is enough."""
    ordered = sorted(({"kind": m["kind"], "t_ms": int(m["t_ms"])} for m in marks), key=lambda m: m["t_ms"])
    if not ordered:
        return None
    if ordered[0]["kind"] != "start" and kickoff_offset_ms is not None and kickoff_offset_ms < ordered[0]["t_ms"]:
        ordered.insert(0, {"kind": "start", "t_ms": int(kickoff_offset_ms)})
    periods: list[Period] = []
    open_at: Optional[int] = None
    for m in ordered:
        if m["kind"] == "start":
            if open_at is not None:
                periods.append(Period(half=len(periods) + 1, start_ms=open_at, end_ms=m["t_ms"]))
            open_at = m["t_ms"]
        elif open_at is not None:
            periods.append(Period(half=len(periods) + 1, start_ms=open_at, end_ms=m["t_ms"]))
            open_at = None
        if m["kind"] == "end":
            break
    else:
        if open_at is not None:
            periods.append(Period(half=len(periods) + 1, start_ms=open_at, end_ms=None))
    return (periods[0].start_ms, periods) if periods else None


def breaks_between(periods: Iterable[Period], until_ms: int, slack_ms: int = MARK_SLACK_MS) -> list[tuple[int, int]]:
    """The stretches between marked periods, and from the end of the last
    one to `until_ms`, as (start, end) on the periods' own clock. Each
    stops `slack_ms` short of the period that follows it."""
    ordered = sorted(periods, key=lambda p: p.start_ms)
    out: list[tuple[int, int]] = []
    for p, nxt in zip(ordered, ordered[1:] + [None]):
        if p.end_ms is None:
            continue
        end = nxt.start_ms - slack_ms if nxt is not None else until_ms
        if end > p.end_ms:
            out.append((p.end_ms, end))
    return out


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


def period_starts_video_ms(kickoff_offset_ms: int, periods: Iterable[Period]) -> list[int]:
    """The video timestamp at which each period began. The first is the
    kickoff offset by definition; the rest follow from the wall-clock gaps
    the minutes app recorded."""
    ordered = sorted(periods, key=lambda p: p.start_ms)
    if not ordered:
        return []
    first = ordered[0].start_ms
    return [kickoff_offset_ms + (p.start_ms - first) for p in ordered]


def video_ms_for_elapsed(elapsed_s: float, kickoff_offset_ms: int, periods: list[Period]) -> Optional[int]:
    """The inverse of `match_seconds`: the video timestamp at which the match
    clock read `elapsed_s`. None with no periods or past the end of the
    closed ones. Half-time has no match seconds, so a tap can never land in
    the break."""
    if not periods:
        return None
    ordered = sorted(periods, key=lambda p: p.start_ms)
    first = ordered[0].start_ms
    remaining = elapsed_s * 1000.0
    for p in ordered:
        length = math.inf if p.end_ms is None else p.end_ms - p.start_ms
        if remaining <= length:
            wall = p.start_ms + remaining
            return int(round(wall - first + kickoff_offset_ms))
        remaining -= length
    return None


@dataclass(frozen=True)
class Tap:
    """One thing the coach logged on the minutes app's Track tab while it
    happened: `matches/{id}/events/{eid}` is {t, at, side, kind, pid?}, with
    goals, shots and turnovers in sibling nodes of the same shape. `t` is
    the match clock in whole seconds at the tap, so it trails the event by
    the coach's reaction; `at` is the tap's wall-clock millisecond."""

    kind: str                    # corner | throw | goalkick | foul | keeper | goal | shot | poss
    side: str                    # 'us' | 'them'
    elapsed_s: float
    at_ms: Optional[int] = None
    player_id: Optional[str] = None
    id: Optional[str] = None


def taps_from_sm(match: dict, collections: Iterable[str] = ("events", "goals", "shots", "poss")) -> list[Tap]:
    """Everything tapped on a soccer-manager match node, oldest first.
    `events` carry their kind; the other nodes are their own kind, and a
    turnover's side is the side that won it."""
    out: list[Tap] = []
    for coll in collections:
        for eid, x in (match.get(coll) or {}).items():
            if not isinstance(x, dict) or "t" not in x:
                continue
            kind = x.get("kind", coll[:-1] if coll != "poss" else "poss") if coll == "events" else {
                "goals": "goal", "shots": "shot", "poss": "poss"}[coll]
            side = x.get("to") if coll == "poss" else x.get("side")
            pid = x.get("pid")
            out.append(Tap(kind=str(kind), side=str(side) if side is not None else "", elapsed_s=float(x["t"]),
                           at_ms=int(x["at"]) if x.get("at") is not None else None,
                           player_id=str(pid) if pid is not None else None, id=str(eid)))
    return sorted(out, key=lambda t: (t.elapsed_s, t.at_ms or 0))
