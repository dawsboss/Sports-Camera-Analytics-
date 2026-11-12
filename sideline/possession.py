"""Possession: who has the ball, sample by sample, with no pitch coordinates.

A player has the ball while it is at her feet or in her hands and nobody
else has been at it since. That is read off the fragments alone: in each
sample the ball was seen, the nearest player within a touch of it
(`control_gap_h`, in her own heights) is at the ball; a run of those by one
player is a spell. Between touches a dribbled ball runs free for a stride,
and the detector misses a sample or two at a time, so a spell carries
across a short gap provided the same player is the next one at the ball.
One sighting at another player in the middle of that is a deflection or two
boxes overlapping, not a change of possession; the sport says how long is
long enough to count.

What this cannot see is what the detector did not: on the two real matches
the ball is found in 43% and 73% of samples, and a spell ends at the last
sample it was seen at her, not when she lost it. Every spell says how many
of its samples were sightings, and the summary says how much of the match
had a ball in it at all. Nothing here knows the sport, or which camera it
is: distances are between a ball and a person in the same frame, which no
pan changes.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from sideline.events import Box, Frame, control_gap_h
from sideline.sports.base import EventGrammar


@dataclass(frozen=True)
class Possession:
    """One spell of the ball with one fragment."""

    start_ms: int               # first sample the ball was seen at her
    end_ms: int                 # last sample it was seen at her
    track_id: int
    cls: str
    sightings: int              # samples in the spell with the ball seen at her
    samples: int                # samples the spell spans, sightings and gaps alike
    held: bool                  # in her hands for most of the sightings
    # Where the ball was the next time it was seen. "taken": at another
    # player, as after a tackle or a pass to feet a stride away. "loose":
    # at nobody, as after a pass, a shot or a clearance. "unseen": not
    # found again within the carry time, which says nothing about what she
    # did with it.
    ended: str


@dataclass(frozen=True)
class _Touch:
    pos: int                    # index into frames
    t_ms: int
    box: Box
    held: bool


def at_the_ball(frame: Frame, grammar: EventGrammar) -> Optional[tuple[Box, bool]]:
    """The player the ball is at in this sample, and whether it is in her
    hands; None when no ball was seen or nobody is within a touch of it.

    Feet come first. A ball on the ground a few metres beyond a player who
    stands nearer the camera falls inside her box at the height of her
    chest, exactly where a ball in her hands would be, and the player
    actually on it has her feet beside it. Only when no feet are within a
    touch is a ball high in a box taken to be in those hands. The cost is a
    thrower with someone standing right behind the ball in the image, which
    is the rarer picture. With more than one object in frame (a spare ball
    by the bench) the one nearest a player is taken."""
    for hands in (False, True):
        best: Optional[tuple[float, Box]] = None
        for ball in frame.objects:
            for p in frame.people:
                g = control_gap_h(p, ball.centre, ball.size, grammar, hands=hands)
                if g is None or (hands and g != 0.0):
                    continue
                if g <= grammar.touch_h and (best is None or g < best[0]):
                    best = (g, p)
        if best is not None:
            return best[1], hands
    return None


def find_possessions(frames: Sequence[Frame], grammar: EventGrammar) -> list[Possession]:
    """Every spell of possession in time order."""
    touches: list[_Touch] = []
    for pos, f in enumerate(frames):
        hit = at_the_ball(f, grammar)
        if hit is not None:
            touches.append(_Touch(pos, f.t_ms, hit[0], hit[1]))
    carry_ms = grammar.possession_carry_s * 1000

    def runs(ts: list[_Touch]) -> list[list[_Touch]]:
        out: list[list[_Touch]] = []
        for t in ts:
            if out and out[-1][-1].box.track_id == t.box.track_id and t.t_ms - out[-1][-1].t_ms <= carry_ms:
                out[-1].append(t)
            else:
                out.append([t])
        return out

    # A run too short to be possession did not end anyone else's: drop it,
    # then let the runs either side of it join if they are one player's.
    long_enough = [r for r in runs(touches) if r[-1].t_ms - r[0].t_ms >= grammar.possession_min_s * 1000]
    spells = runs([t for r in long_enough for t in r])

    touched = {t.pos for t in touches}
    out: list[Possession] = []
    for r in spells:
        first, last = r[0], r[-1]
        ended = "unseen"
        for pos in range(last.pos + 1, len(frames)):
            if frames[pos].t_ms - last.t_ms > carry_ms:
                break
            if frames[pos].objects:
                ended = "taken" if pos in touched else "loose"
                break
        out.append(Possession(
            start_ms=int(first.t_ms), end_ms=int(last.t_ms), track_id=int(last.box.track_id), cls=last.box.cls,
            sightings=len(r), samples=last.pos - first.pos + 1,
            held=sum(1 for t in r if t.held) * 2 > len(r), ended=ended))
    return out


def possession_doc(frames: Sequence[Frame], spells: Sequence[Possession], capture_mode: str) -> dict:
    """`possession.json`: the spells, with what they rest on. `partial` is
    true whenever some sample had no ball in it, which on any footage so
    far is always: a share of possession computed from this is a share of
    the time the ball was seen, and has to say so."""
    with_ball = sum(1 for f in frames if f.objects)
    return {
        "capture_mode": capture_mode,
        "sample_count": len(frames),
        "samples_with_ball": with_ball,
        "samples_in_possession": sum(s.samples for s in spells),
        "partial": with_ball < len(frames),
        "spells": [asdict(s) for s in spells],
    }


def holders(spells: Sequence[dict], times_ms: Sequence[int]) -> list[Optional[int]]:
    """For each time, the track of the spell it falls inside, or None."""
    starts = [s["start_ms"] for s in spells]
    out: list[Optional[int]] = []
    for t in times_ms:
        k = bisect_right(starts, t) - 1
        out.append(spells[k]["track_id"] if k >= 0 and t <= spells[k]["end_ms"] else None)
    return out
