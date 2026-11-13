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
from dataclasses import dataclass, field
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

    @property
    def size(self) -> float:
        """The longer side, which for a ball is its diameter however it blurs."""
        return max(self.x2 - self.x1, self.y2 - self.y1)

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
    landmarks: list[Box] = field(default_factory=list)


@dataclass
class Candidate:
    """A dead ball before the sport has named it: a DeadBall minus the
    period context, which depends on the other candidates."""

    obs: dict
    period: Optional[int]
    start_ms: int = 0           # when the ball came to rest


def frames_from_rows(rows: Iterable[dict], object_classes: Iterable[str],
                     landmark_classes: Iterable[str] = ()) -> list[Frame]:
    """Fragment rows (FRAGMENTS_SCHEMA) grouped into frames in time order:
    the object of play, the sport's fixed landmarks, and everyone else."""
    objs, lands = set(object_classes), set(landmark_classes)
    by: dict[int, Frame] = {}
    for r in rows:
        f = by.get(r["frame_index"])
        if f is None:
            f = by[r["frame_index"]] = Frame(r["frame_index"], r["t_ms"], [], [])
        b = Box(r["track_id"], r["cls"], r["x1"], r["y1"], r["x2"], r["y2"])
        (f.objects if r["cls"] in objs else f.landmarks if r["cls"] in lands else f.people).append(b)
    return [by[k] for k in sorted(by)]


def camera_shifts_from_people(frames: Sequence[Frame], min_common: int = 2) -> list[np.ndarray]:
    """The camera's motion from each frame to the next, estimated from what
    is seen in both. A landmark is fixed scenery, so one seen twice is the
    camera's motion exactly and is preferred; failing that, the median shift
    of the people, which is right for a pan and wrong for the second in
    which most of them jog the same way. Zero where too few were seen,
    which on a cut to an empty frame is wrong and harmless: nothing is
    still across a cut. This is the fallback for frames registration could
    not place; see `camera_shifts_from_registration`."""
    out = [np.zeros(2)]
    for prev, cur in zip(frames, frames[1:]):
        la = {b.track_id: b.centre for b in prev.landmarks}
        fixed = [(la[b.track_id], b.centre) for b in cur.landmarks if b.track_id in la]
        if fixed:
            out.append(np.median(np.array([c - p for p, c in fixed]), axis=0))
            continue
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


def people_moving(frames: Sequence[Frame], shifts: Sequence[np.ndarray], grammar: EventGrammar,
                  over_s: float = 2.0) -> list[int]:
    """How many people in each frame are moving: faster than `moving_h` of
    their own heights a second over the last `over_s`, the camera's motion
    removed. Whoever was not in frame that long ago is not counted."""
    cum = np.cumsum(np.array(shifts), axis=0) if len(frames) else np.zeros((0, 2))
    out: list[int] = []
    j = 0
    for k, f in enumerate(frames):
        while frames[j].t_ms < f.t_ms - over_s * 1000:
            j += 1
        dt = (f.t_ms - frames[j].t_ms) / 1000.0
        if dt <= 0:
            out.append(0)
            continue
        was = {b.track_id: b.centre - cum[j] for b in frames[j].people}
        n = 0
        for b in f.people:
            p = was.get(b.track_id)
            if p is not None and b.height > 0 and np.linalg.norm(b.centre - cum[k] - p) / b.height / dt >= grammar.moving_h:
                n += 1
        out.append(n)
    return out


def find_breaks(frames: Sequence[Frame], grammar: EventGrammar, shifts: Optional[Sequence[np.ndarray]] = None,
                window_s: float = 30.0) -> list[tuple[int, int]]:
    """Stretches in which play had stopped for everyone, as (start, end) in
    video ms: at least `break_min_s` long, and in every `window_s` of it
    fewer people were moving than `break_moving_frac` of what is usual in
    this footage. Half-time is one; so is a long injury, and the wait
    after the last whistle if the film runs on.

    It is read from the people because nothing else is always there: a
    follow-cam idles on the centre circle through the break and the grass
    never leaves the frame, which is why S0's hint found nothing on the
    second reviewed match, and a fixed camera sees the same pitch all
    day. A ball lying still in such a stretch is not a restart waiting to
    be taken, and the first one struck after half-time is the kick-off."""
    if not frames:
        return []
    if shifts is None:
        shifts = camera_shifts_from_people(frames)
    moving = people_moving(frames, shifts, grammar)
    step = window_s * 1000.0
    t0 = frames[0].t_ms
    per: dict[int, list[int]] = defaultdict(list)
    for f, n in zip(frames, moving):
        per[int((f.t_ms - t0) // step)].append(n)
    last = max(per)
    # A window nobody was seen in is as quiet as play gets.
    level = [float(np.median(per[w])) if w in per else 0.0 for w in range(last + 1)]
    usual = float(np.median([level[w] for w in per]))
    if usual <= 0:
        return []
    out: list[tuple[int, int]] = []
    start: Optional[int] = None
    for w in range(last + 2):
        quiet = w <= last and level[w] < grammar.break_moving_frac * usual
        if quiet and start is None:
            start = w
        elif not quiet and start is not None:
            if (w - start) * window_s >= grammar.break_min_s:
                out.append((int(t0 + start * step), int(t0 + w * step)))
            start = None
    return out


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


def control_gap_h(person: Box, at: np.ndarray, object_size: Optional[float], grammar: EventGrammar,
                  hands: bool = True) -> Optional[float]:
    """How far the object is from being at this person, in her heights: 0
    in her hands, else the distance from her feet. None where it cannot be
    hers at all.

    The box gap the cues use is 0 anywhere inside the box, so everyone
    standing behind the ball in the image is as near as the player with her
    foot on it. The feet are where a grounded ball is played, and a player
    further from the camera has hers higher in the image than the ball.
    That is still an image distance: a low camera compresses depth, and two
    players a few metres apart along the line of sight are close here.
    Registration is what removes that; this is the measure for frames it
    has not placed, and the nearest player wins rather than everyone within
    reach."""
    if person.height <= 0:
        return None
    if object_size is not None and object_size < grammar.object_min_h * person.height:
        return None                      # well behind her, only lined up with her in the image
    rel_y = (at[1] - person.y1) / person.height
    if hands and person.x1 <= at[0] <= person.x2 and -grammar.held_above_h <= rel_y <= grammar.held_rel_y:
        return 0.0
    dx = max(person.x1 - at[0], 0.0, at[0] - person.x2)
    return float(np.hypot(dx, at[1] - person.y2)) / person.height


def last_at_the_ball(frames: Sequence[Frame], idx: list[int], i: int, last: int, j: int, n: int, stab: np.ndarray,
                     cum: np.ndarray, grammar: EventGrammar, tid: int, lookback: int = 4) -> Optional[Box]:
    """The last person to have her feet at a grounded dead ball: over the
    final still samples and on for `strike_window_s` after the ball was
    last seen there, or until it is seen somewhere else.

    Whoever stood nearest while the ball sat is often the one who fetched
    it or put it down. The one who takes it runs up at the end, and the
    detector has usually lost the ball by then, so she is looked for at the
    spot the ball lay on, carried into later frames by the camera's motion.
    None when nobody came within a touch, and the nearest-while-still vote
    stands."""
    spot = np.mean(stab[i:last + 1], axis=0)
    end = frames[idx[last]]
    size = next(b for b in end.objects if b.track_id == tid).size
    until = idx[j] if j < n else len(frames) - 1
    found: Optional[Box] = None
    for fi in range(idx[max(i, last - lookback)], min(until, len(frames) - 1) + 1):
        f = frames[fi]
        if f.t_ms - end.t_ms > grammar.strike_window_s * 1000:
            break
        seen = next((b for b in f.objects if b.track_id == tid), None) if fi <= idx[last] else None
        at = seen.centre if seen is not None else spot + cum[fi]
        best: Optional[tuple[float, Box]] = None
        for p in f.people:
            g = control_gap_h(p, at, seen.size if seen is not None else size, grammar, hands=False)
            if g is not None and (best is None or g < best[0]):
                best = (g, p)
        if best is not None and best[0] <= grammar.touch_h:
            found = best[1]
    return found


def find_dead_balls(frames: Sequence[Frame], grammar: EventGrammar, frame_size: tuple[int, int],
                    period_starts_ms: Sequence[int] = (), max_gap_samples: int = 3,
                    merge_s: float = 2.0, shifts: Optional[Sequence[np.ndarray]] = None,
                    same_taker_s: float = 2.0, early_s: float = 5.0,
                    breaks_ms: Sequence[tuple[int, int]] = ()) -> list[DeadBall]:
    """Every moment the ball sat still long enough and was then put back into
    play, with what was visible around it. One per restart after merging.
    `shifts` is the camera's motion between consecutive frames; without it
    the people estimate is used.

    A ball that is back at rest with the same taker within `same_taker_s`
    of a release was never released: she shifted her grip, or stepped along
    the line with it. Those windows are one dead ball, timed at the last
    release and still since the first rest. Two seconds is too short for a
    ball to be played, returned and settled, which is the case that must
    stay two events.

    With period starts known, a ball dead more than `early_s` before the
    first of them is the warm-up, not the match, and is not reported. The
    margin is for a whistle time typed by hand, and every period is
    entered that much early for the same reason.

    Nothing put back into play inside one of `breaks_ms` (`find_breaks`) is
    reported either: play had stopped for everyone, and a ball that leaves
    its spot then was picked up or knocked about, not taken."""
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
            while j < n:
                if (idx[j] - idx[j - 1] > max_gap_samples
                        and (frames[idx[j - 1]].t_ms - frames[idx[i]].t_ms) / 1000.0 < grammar.dead_ball_min_s):
                    # Unseen for longer than a miss or two, before it had
                    # sat long enough to be dead: two sightings a second
                    # apart are not a ball at rest. Once it has sat that
                    # long, finding it in the same place after any gap is
                    # the best evidence there is that it never left.
                    break
                mean = np.mean(pts, axis=0)
                if np.linalg.norm(stab[j] - mean) > grammar.still_radius_h * heights[j]:
                    break
                pts.append(stab[j])
                j += 1
            last = j - 1
            still_s = (frames[idx[last]].t_ms - frames[idx[i]].t_ms) / 1000.0
            if still_s >= grammar.dead_ball_min_s:
                cand = self_contained(frames, idx, i, last, j, n, stab, heights, grammar, tid, (W, H), period_starts_ms,
                                      max_gap_samples, cum, early_s)
                if cand is not None:
                    raw.append(cand)
            i = j if j > i + 1 else i + 1

    raw.sort(key=lambda c: c.obs["t_ms"])
    kept: list[Candidate] = []
    first_start = min(period_starts_ms) if len(period_starts_ms) else None
    for c in raw:
        if first_start is not None and c.obs["t_ms"] < first_start - early_s * 1000:
            continue
        if any(a <= c.obs["t_ms"] < b for a, b in breaks_ms):
            continue
        prev = kept[-1] if kept else None
        if prev is not None and c.obs["t_ms"] - prev.obs["t_ms"] < merge_s * 1000:
            if c.obs["still_s"] > prev.obs["still_s"]:
                kept[-1] = c
            continue
        if (prev is not None and prev.obs["taker_track"] is not None
                and c.obs["taker_track"] == prev.obs["taker_track"]
                and c.start_ms - prev.obs["t_ms"] < same_taker_s * 1000):
            # Back at rest with the same taker: the earlier window did not
            # end in a release. The later one says when the ball left; the
            # earlier one says since when it had been dead and in whose hands.
            c.obs["still_s"] = float(c.obs["still_s"] + (c.start_ms - prev.start_ms) / 1000.0)
            c.obs["held"] = bool(c.obs["held"] or prev.obs["held"])
            c.start_ms = prev.start_ms
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
                   period_starts_ms: Sequence[int], max_gap_samples: int = 3,
                   cum: Optional[np.ndarray] = None, early_s: float = 0.0) -> Optional[Candidate]:
    """Measure one still window: how it ended, who took it, what was around.
    Returns None when the window ended without a release the rules can use."""
    W, H = size
    end_frame = frames[idx[last]]
    speed: Optional[float] = None
    step = frames[idx[last]].t_ms - frames[idx[last - 1]].t_ms if last > i else 200
    t_ms = end_frame.t_ms + step
    if j < n:
        # The same fragment was seen again, so where the ball went is known.
        dt = (frames[idx[j]].t_ms - end_frame.t_ms) / 1000.0
        if dt <= 0:
            return None
        moved = float(np.linalg.norm(stab[j] - stab[last])) / heights[j]
        if idx[j] - idx[j - 1] <= max_gap_samples:
            # Seen again within the gap stillness is carried across. A miss
            # of a sample or two is the ordinary case, not the struck one:
            # the first full match saw the release on the very next sample
            # at 33 of 317 windows. The average speed over the gap is a
            # floor on the speed it left at, and the same test applies.
            speed = moved / dt
            sat_s = (end_frame.t_ms - frames[idx[i]].t_ms) / 1000.0
            if speed < grammar.release_speed_h and sat_s < grammar.settled_s:
                return None                  # it drifted off, nobody struck it
            # A ball that sat for `settled_s` was dead however it left: a
            # restart taken short, rolled a step to a team-mate, leaves no
            # faster than a dribble. The slow speed is reported as it was.
            t_ms = frames[idx[last] + 1].t_ms
        # Otherwise it was unseen for longer and turned up elsewhere: put
        # back into play at a speed nobody saw, like the vanished ball below.
    # With no speed the ball vanished after sitting still: struck too hard
    # to see, or carried out of frame. A restart with a lower confidence,
    # provided someone was there to take it.

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
            if ball.size < grammar.object_min_h * p.height:
                # Too small for her to be at it: the object is well behind
                # her and only lines up with her in the image.
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
    # Whether this was a dead ball is settled above, by who was near while
    # it sat. Who took a grounded one is whoever was last at it, which is
    # often not the same player. A ball in the hands stays with its holder.
    late = last_at_the_ball(frames, idx, i, last, j, n, stab, cum, grammar, tid) if cum is not None and not held else None
    if late is not None:
        taker_id = late.track_id

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
    landmarks_h: dict[str, float] = {}
    for lm in end_frame.landmarks:
        d = lm.gap_to(ball) / scale
        if lm.cls not in landmarks_h or d < landmarks_h[lm.cls]:
            landmarks_h[lm.cls] = float(d)

    period, since = None, None
    starts = sorted(period_starts_ms)
    for pi, s in enumerate(starts):
        # A period's start is a time somebody typed or tapped, and the dead
        # ball it begins with is timed at when the ball was last seen at
        # rest: the kick-off may sit a little before its own whistle.
        if t_ms >= s - early_s * 1000:
            period, since = pi, max(0.0, (t_ms - s) / 1000.0)
    obs = dict(
        t_ms=int(t_ms), still_s=float((end_frame.t_ms - frames[idx[i]].t_ms) / 1000.0),
        taker_track=taker_id, taker_cls=(late.cls if late is not None else taker.cls if taker is not None
                                         else boxes_by_frame[max(boxes_by_frame)].cls if boxes_by_frame else None),
        taker_team=None, held=bool(held), ball_rel_y=float(np.median(rel_ys)) if rel_ys else None,
        ball_u=float(ball[0] / W), ball_v=float(ball[1] / H), n_visible=len(others), nearest_h=nearest_h,
        n_near=n_near, extreme_h=extreme_h, landmarks_h=landmarks_h, release_speed_h=speed,
        since_period_start_s=since,
    )
    return Candidate(obs=obs, period=period, start_ms=int(frames[idx[i]].t_ms))
