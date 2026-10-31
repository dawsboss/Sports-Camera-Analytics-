"""Dead balls: where the ball stopped, who put it back into play, and what
that looked like.

This is the half of restart detection that needs no pitch coordinates. A
restart is a ball that sits still and is then struck or thrown, and the
camera sees that whether or not registration could place the frame. What
separates one restart from another is also visible: the ball in the hands
above the head, the keeper taking it, the first one of a period, the taker
alone at one extreme of everyone else or among them. The stage measures
those cues in heights of the taker, hands them to the sport plugin as a
`DeadBall`, and the plugin names the restart. Nothing here knows what a
throw-in is.

Two things make this work on a follow-cam. Stillness is judged after
subtracting the camera's motion: a panning crop moves everything together
and the ball at rest moves with it. Registration is the stage that knows the
camera, so where it has placed consecutive frames the motion is read from
its homographies, which on a fixed camera is exactly zero; where it has not,
the motion is estimated as the median shift of the people in frame, which is
right for a pan and wrong for a second whenever most players jog the same
way, so it is the fallback and not the rule. And every distance is in
heights of the people nearest the ball, so the same thresholds hold on a
frame zoomed to the corner and at the far end of a static camera's view.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from sideline.sports.base import DeadBall, EventGrammar


@dataclass(frozen=True)
class Box:
    track_id: int
    cls: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def centre(self) -> np.ndarray:
        return np.array([(self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2])

    def gap_to(self, p: np.ndarray) -> float:
        """Pixel distance from a point to the box; 0 inside it."""
        dx = max(self.x1 - p[0], 0.0, p[0] - self.x2)
        dy = max(self.y1 - p[1], 0.0, p[1] - self.y2)
        return float(np.hypot(dx, dy))


@dataclass
class Frame:
    frame_index: int
    t_ms: int
    people: list[Box]
    objects: list[Box]


@dataclass
class Candidate:
    """A dead ball before the sport has named it: a DeadBall minus the
    period context, which depends on the other candidates."""

    obs: dict
    period: Optional[int]


def frames_from_rows(rows: Iterable[dict], object_classes: Iterable[str]) -> list[Frame]:
    """Fragment rows (FRAGMENTS_SCHEMA) grouped into frames in time order."""
    objs = set(object_classes)
    by: dict[int, Frame] = {}
    for r in rows:
        f = by.get(r["frame_index"])
        if f is None:
            f = by[r["frame_index"]] = Frame(r["frame_index"], r["t_ms"], [], [])
        b = Box(r["track_id"], r["cls"], r["x1"], r["y1"], r["x2"], r["y2"])
        (f.objects if r["cls"] in objs else f.people).append(b)
    return [by[k] for k in sorted(by)]


def camera_shifts_from_people(frames: Sequence[Frame], min_common: int = 2) -> list[np.ndarray]:
    """The camera's motion from each frame to the next, estimated as the
    median shift of the people seen in both. Zero where too few were, which
    on a cut to an empty frame is wrong and harmless: nothing is still
    across a cut. This is the fallback for frames registration could not
    place; see `camera_shifts_from_registration`."""
    out = [np.zeros(2)]
    for prev, cur in zip(frames, frames[1:]):
        a = {b.track_id: b.centre for b in prev.people}
        common = [(a[b.track_id], b.centre) for b in cur.people if b.track_id in a]
        if len(common) >= min_common:
            d = np.array([c - p for p, c in common])
            out.append(np.median(d, axis=0))
        else:
            out.append(np.zeros(2))
    return out


def camera_shifts_from_registration(frames: Sequence[Frame], h_by_frame: dict[int, np.ndarray],
                                    frame_size: tuple[int, int]) -> list[Optional[np.ndarray]]:
    """The camera's motion from each frame to the next where registration
    placed both: a ground point at the frame centre, taken to the pitch
    through the first homography and back through the second. On a fixed
    camera the two are the same mapping and the shift is zero. None where
    either frame is unregistered."""
    W, H = frame_size
    c = np.array([W / 2, H / 2, 1.0])
    out: list[Optional[np.ndarray]] = [np.zeros(2)]
    for prev, cur in zip(frames, frames[1:]):
        hp, hc = h_by_frame.get(prev.frame_index), h_by_frame.get(cur.frame_index)
        if hp is None or hc is None:
            out.append(None)
            continue
        q = np.linalg.inv(hc) @ (hp @ c)
        if abs(q[2]) < 1e-12:
            out.append(None)
            continue
        out.append(q[:2] / q[2] - c[:2])
    return out


def merge_shifts(primary: Sequence[Optional[np.ndarray]], fallback: Sequence[np.ndarray]) -> tuple[list[np.ndarray], int]:
    """Registration's shifts where it has them, the people estimate elsewhere.
    Returns the shifts and how many came from the primary source."""
    out, n = [], 0
    for i, (p, f) in enumerate(zip(primary, fallback)):
        if p is not None:
            out.append(np.asarray(p, dtype=float))
            n += 1 if i > 0 else 0          # the first entry is frame 0 against nothing
        else:
            out.append(np.asarray(f, dtype=float))
    return out, n


def height_scale(frames: Sequence[Frame], fallback: float) -> list[float]:
    """Median person height per frame, carried forward across frames with
    nobody in them, carried back before the first person, and `fallback`
    when the whole match has nobody."""
    measured: list[Optional[float]] = []
    for f in frames:
        hs = [b.height for b in f.people if b.height > 0]
        measured.append(float(np.median(hs)) if hs else None)
    known = [h for h in measured if h is not None]
    if not known:
        return [fallback] * len(frames)
    out, last = [], known[0]
    for h in measured:
        if h is not None:
            last = h
        out.append(last)
    return out


def local_height(frame: Frame, at: np.ndarray, default: float) -> float:
    """The height of the person nearest a point, which is the scale at that
    depth: on a static camera the far touchline is half the height of the
    near one, and a frame-wide median would be wrong at both."""
    best = None
    for p in frame.people:
        if p.height <= 0:
            continue
        d = p.gap_to(at)
        if best is None or d < best[0]:
            best = (d, p.height)
    return best[1] if best is not None else default


def find_dead_balls(frames: Sequence[Frame], grammar: EventGrammar, frame_size: tuple[int, int],
                    period_starts_ms: Sequence[int] = (), max_gap_samples: int = 3,
                    merge_s: float = 2.0, shifts: Optional[Sequence[np.ndarray]] = None) -> list[DeadBall]:
    """Every moment the ball sat still long enough and was then put back into
    play, with what was visible around it. One per restart after merging.
    `shifts` is the camera's motion between consecutive frames; without it
    the people estimate is used."""
    if not frames:
        return []
    W, H = frame_size
    if shifts is None:
        shifts = camera_shifts_from_people(frames)
    cum = np.cumsum(np.array(shifts), axis=0)                # camera position relative to frame 0
    medians = height_scale(frames, fallback=H / 10.0)
    index_of = {f.frame_index: i for i, f in enumerate(frames)}
    roles_known = len({b.cls for f in frames for b in f.people}) > 1

    # One trajectory per object track, in stabilised coordinates.
    tracks: dict[int, list[tuple[int, Box]]] = defaultdict(list)
    for i, f in enumerate(frames):
        for b in f.objects:
            tracks[b.track_id].append((i, b))

    raw: list[Candidate] = []
    for tid, samples in tracks.items():
        if len(samples) < 2:
            continue
        idx = [i for i, _ in samples]
        pos = np.array([b.centre for _, b in samples])
        stab = pos - cum[idx]
        heights = [local_height(frames[i], b.centre, medians[i]) for i, b in samples]   # per sample
        n = len(samples)
        i = 0
        while i < n - 1:
            pts = [stab[i]]
            j = i + 1
            while j < n and idx[j] - idx[j - 1] <= max_gap_samples:
                mean = np.mean(pts, axis=0)
                if np.linalg.norm(stab[j] - mean) > grammar.still_radius_h * heights[j]:
                    break
                pts.append(stab[j])
                j += 1
            last = j - 1
            still_s = (frames[idx[last]].t_ms - frames[idx[i]].t_ms) / 1000.0
            if still_s >= grammar.dead_ball_min_s:
                cand = self_contained(frames, idx, i, last, j, n, stab, heights, grammar, tid, (W, H), period_starts_ms)
                if cand is not None:
                    raw.append(cand)
            i = j if j > i + 1 else i + 1

    raw.sort(key=lambda c: c.obs["t_ms"])
    kept: list[Candidate] = []
    for c in raw:
        if kept and c.obs["t_ms"] - kept[-1].obs["t_ms"] < merge_s * 1000:
            if c.obs["still_s"] > kept[-1].obs["still_s"]:
                kept[-1] = c
            continue
        kept.append(c)
    seen_periods: set[int] = set()
    out: list[DeadBall] = []
    for c in kept:
        first = c.period is not None and c.period not in seen_periods
        if c.period is not None:
            seen_periods.add(c.period)
        out.append(DeadBall(first_in_period=first, roles_known=roles_known, **c.obs))
    return out


def self_contained(frames: Sequence[Frame], idx: list[int], i: int, last: int, j: int, n: int, stab: np.ndarray,
                   heights: list[float], grammar: EventGrammar, tid: int, size: tuple[int, int],
                   period_starts_ms: Sequence[int]) -> Optional[Candidate]:
    """Measure one still window: how it ended, who took it, what was around.
    Returns None when the window ended without a release the rules can use."""
    W, H = size
    end_frame = frames[idx[last]]
    speed: Optional[float]
    if j < n and idx[j] - idx[j - 1] <= 1:
        dt = (frames[idx[j]].t_ms - end_frame.t_ms) / 1000.0
        if dt <= 0:
            return None
        speed = float(np.linalg.norm(stab[j] - stab[last])) / heights[j] / dt
        if speed < grammar.release_speed_h:
            return None                      # it drifted off, nobody struck it
        t_ms = frames[idx[j]].t_ms
    else:
        # The ball vanished straight after sitting still: struck too hard to
        # see, or carried out of frame. A restart with a lower confidence,
        # provided someone was there to take it.
        speed = None
        step = frames[idx[last]].t_ms - frames[idx[last - 1]].t_ms if last > i else 200
        t_ms = end_frame.t_ms + step

    # The taker: whoever was nearest the ball over the last samples of the
    # window, and whether the ball sat high in her box (in hand) meanwhile.
    window = list(range(max(i, last - 4), last + 1))
    nearest_votes: Counter = Counter()
    boxes_by_frame: dict[int, Box] = {}
    for k in window:
        f = frames[idx[k]]
        ball = next(b for b in f.objects if b.track_id == tid)
        best = None
        for p in f.people:
            if p.height <= 0:
                continue
            d = p.gap_to(ball.centre) / p.height
            if best is None or d < best[0]:
                best = (d, p)
        if best is not None and best[0] <= grammar.reach_h:
            nearest_votes[best[1].track_id] += 1
            boxes_by_frame[k] = best[1]
    taker_id = nearest_votes.most_common(1)[0][0] if nearest_votes else None
    held_votes, rel_ys = 0, []
    if taker_id is not None:
        for k in window:
            f = frames[idx[k]]
            p = next((b for b in f.people if b.track_id == taker_id), None)
            if p is None or p.height <= 0:
                continue
            ball = next(b for b in f.objects if b.track_id == tid).centre
            rel_y = (ball[1] - p.y1) / p.height
            rel_ys.append(rel_y)
            if p.x1 <= ball[0] <= p.x2 and -grammar.held_above_h <= rel_y <= grammar.held_rel_y:
                held_votes += 1
    held = taker_id is not None and held_votes >= max(1, len(window) / 2)
    if speed is None and taker_id is None:
        return None

    # Context at the last still sample.
    ball = next(b for b in end_frame.objects if b.track_id == tid).centre
    taker = next((b for b in end_frame.people if b.track_id == taker_id), None)
    scale = taker.height if taker is not None and taker.height > 0 else heights[last]
    others = [p for p in end_frame.people if p.track_id != taker_id]
    gaps = [p.gap_to(ball) / scale for p in others]
    nearest_h = min(gaps) if gaps else None
    n_near = sum(1 for g in gaps if g <= grammar.near_h)
    extreme_h: Optional[float] = None
    if others:
        c = np.array([p.centre for p in others])
        margins = [max(c[:, a].min() - ball[a], ball[a] - c[:, a].max()) for a in (0, 1)]
        extreme_h = float(max(margins)) / scale

    period, since = None, None
    starts = sorted(period_starts_ms)
    for pi, s in enumerate(starts):
        if t_ms >= s:
            period, since = pi, (t_ms - s) / 1000.0
    obs = dict(
        t_ms=int(t_ms), still_s=float((end_frame.t_ms - frames[idx[i]].t_ms) / 1000.0),
        taker_track=taker_id, taker_cls=taker.cls if taker is not None else (boxes_by_frame[max(boxes_by_frame)].cls if boxes_by_frame else None),
        taker_team=None, held=bool(held), ball_rel_y=float(np.median(rel_ys)) if rel_ys else None,
        ball_u=float(ball[0] / W), ball_v=float(ball[1] / H), n_visible=len(others), nearest_h=nearest_h,
        n_near=n_near, extreme_h=extreme_h, release_speed_h=speed, since_period_start_s=since,
    )
    return Candidate(obs=obs, period=period)
