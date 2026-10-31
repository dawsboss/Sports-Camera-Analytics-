"""Joining detections into fragments.

A tracker with no dependencies beyond numpy, built on ByteTrack's idea: match
the confident detections to the live tracks first, then let the weak ones
rescue tracks that would otherwise die, and never start a track from a weak
detection. People are matched by box overlap after a constant-velocity
nudge, or by centre distance in their own heights where there is no overlap,
because at 5 fps a running player moves her own height between samples. The object of play is matched by where it went, because a 11 px ball
that moves 30 px between samples has no overlap with itself, and it is
allowed to vanish for far longer: the measured blackout tail on the Veo
footage is 8 to 15 samples (`docs/NEXT.md`), and a ball lost for two seconds
is the same ball when it comes back near where it was heading.

Nothing here knows the sport. Which classes are the object comes from the
sport plugin's entity classes; everything else is a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from sideline.detection import Detection


@dataclass
class TrackerConfig:
    iou_high: float = 0.3          # first pass: confident detections against live tracks
    iou_low: float = 0.2           # second pass: the rest, against what is still unmatched
    centre_gate_h: float = 1.5     # a person may also be matched by centre, within this many of her heights
    high_conf: float = 0.5         # the split between the two passes
    max_age: int = 5               # samples a person track survives unmatched (1 s at 5 fps)
    min_hits: int = 2              # shorter person tracks are flicker and are dropped at the end
    object_max_age: int = 15       # the measured blackout tail, 3 s at 5 fps
    object_max_jump: float = 0.12  # of the frame diagonal per sample, growing with the gap
    object_min_hits: int = 1
    velocity_blend: float = 0.5    # how much of the old velocity survives a new measurement


@dataclass
class Track:
    id: int
    cls: str
    box: np.ndarray                 # x1, y1, x2, y2 at the last match
    is_object: bool
    conf: float = 0.0
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))   # centre shift per sample
    hits: int = 1
    age: int = 0                    # samples since the last match
    last_frame: int = 0

    @property
    def centre(self) -> np.ndarray:
        return np.array([(self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2])

    def predicted_box(self, ahead: int = 1) -> np.ndarray:
        # Carry the velocity, but not forever: a ball lost in flight is not
        # still flying when it reappears.
        steps = min(ahead, 3)
        shift = np.concatenate([self.vel, self.vel]) * steps
        return self.box + shift

    def update(self, det: Detection, frame_index: int, blend: float) -> None:
        new = np.array([det.x1, det.y1, det.x2, det.y2], dtype=np.float64)
        gap = max(1, frame_index - self.last_frame)
        measured = (np.array([(new[0] + new[2]) / 2, (new[1] + new[3]) / 2]) - self.centre) / gap
        self.vel = blend * self.vel + (1 - blend) * measured
        self.box, self.conf, self.cls = new, det.conf, det.cls
        self.hits += 1
        self.age = 0
        self.last_frame = frame_index


def similarity(pred: np.ndarray, dets: np.ndarray, gate_h: float) -> np.ndarray:
    """How alike a predicted box and a detection are: their overlap, or how
    close their centres are in heights of the track when they do not overlap.
    At 5 fps a sprinting player moves about her own height between samples,
    so overlap alone loses her every time she runs."""
    iou = iou_matrix(pred, dets)
    if iou.size == 0:
        return iou
    pc = np.stack([(pred[:, 0] + pred[:, 2]) / 2, (pred[:, 1] + pred[:, 3]) / 2], axis=1)
    dc = np.stack([(dets[:, 0] + dets[:, 2]) / 2, (dets[:, 1] + dets[:, 3]) / 2], axis=1)
    h = np.maximum(pred[:, 3] - pred[:, 1], 1e-6)
    d = np.linalg.norm(pc[:, None, :] - dc[None, :, :], axis=2) / (gate_h * h[:, None])
    return np.maximum(iou, np.clip(1.0 - d, 0.0, 1.0))


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of two (n,4) and (m,4) arrays of x1,y1,x2,y2 boxes."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def greedy_match(score: np.ndarray, floor: float, larger_is_better: bool = True) -> list[tuple[int, int]]:
    """Pairs (row, col) taken best first, each row and column at most once,
    stopping at `floor`. Greedy rather than Hungarian: at these sizes the
    difference is nothing and it keeps scipy out of the dependencies."""
    if score.size == 0:
        return []
    order = np.argsort(score, axis=None)
    if larger_is_better:
        order = order[::-1]
    used_r, used_c, out = set(), set(), []
    for flat in order:
        r, c = divmod(int(flat), score.shape[1])
        v = score[r, c]
        if (v < floor) if larger_is_better else (v > floor):
            break
        if r in used_r or c in used_c:
            continue
        used_r.add(r)
        used_c.add(c)
        out.append((r, c))
    return out


class Tracker:
    def __init__(self, cfg: TrackerConfig, object_classes: Iterable[str], frame_size: tuple[int, int]) -> None:
        self.cfg = cfg
        self.object_classes = set(object_classes)
        self.diag = float(np.hypot(*frame_size))
        self.people: list[Track] = []
        self.objects: list[Track] = []
        self._next_id = 1
        self.finished: list[Track] = []

    def _new(self, det: Detection, frame_index: int, is_object: bool) -> Track:
        t = Track(self._next_id, det.cls, np.array([det.x1, det.y1, det.x2, det.y2], dtype=np.float64),
                  is_object, det.conf, last_frame=frame_index)
        self._next_id += 1
        return t

    def step(self, frame_index: int, dets: Sequence[Detection]) -> list[tuple[Track, Detection]]:
        """Advance one sampled frame. Returns the (track, detection) pairs
        matched or started in it, which is what the stage writes."""
        people = [d for d in dets if d.cls not in self.object_classes]
        objects = [d for d in dets if d.cls in self.object_classes]
        out: list[tuple[Track, Detection]] = []
        out += self._step_people(frame_index, people)
        out += self._step_objects(frame_index, objects)
        return out

    def _step_people(self, frame_index: int, dets: list[Detection]) -> list[tuple[Track, Detection]]:
        cfg = self.cfg
        out: list[tuple[Track, Detection]] = []
        live = self.people
        for t in live:
            t.age = frame_index - t.last_frame
        high = [d for d in dets if d.conf >= cfg.high_conf]
        low = [d for d in dets if d.conf < cfg.high_conf]
        unmatched = list(range(len(live)))
        pred = np.array([t.predicted_box(t.age) for t in live]) if live else np.zeros((0, 4))

        def match(cands: list[Detection], floor: float) -> list[Detection]:
            nonlocal unmatched
            if not cands or not unmatched:
                return cands
            boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in cands])
            m = similarity(pred[unmatched], boxes, cfg.centre_gate_h)
            pairs = greedy_match(m, floor)
            taken_d = set()
            for r, c in pairs:
                t = live[unmatched[r]]
                t.update(cands[c], frame_index, cfg.velocity_blend)
                out.append((t, cands[c]))
                taken_d.add(c)
            matched_rows = {r for r, _ in pairs}
            unmatched = [u for i, u in enumerate(unmatched) if i not in matched_rows]
            return [d for i, d in enumerate(cands) if i not in taken_d]

        left_high = match(high, cfg.iou_high)
        match(low, cfg.iou_low)
        for d in left_high:
            t = self._new(d, frame_index, False)
            live.append(t)
            out.append((t, d))
        keep = []
        for t in live:
            if frame_index - t.last_frame > cfg.max_age:
                self.finished.append(t)
            else:
                keep.append(t)
        self.people = keep
        return out

    def _step_objects(self, frame_index: int, dets: list[Detection]) -> list[tuple[Track, Detection]]:
        cfg = self.cfg
        out: list[tuple[Track, Detection]] = []
        live = self.objects
        for t in live:
            t.age = frame_index - t.last_frame
        if dets and live:
            pred = np.array([t.predicted_box(t.age) for t in live])
            pc = np.stack([(pred[:, 0] + pred[:, 2]) / 2, (pred[:, 1] + pred[:, 3]) / 2], axis=1)
            dc = np.array([d.centre for d in dets])
            dist = np.linalg.norm(pc[:, None, :] - dc[None, :, :], axis=2) / self.diag
            allow = np.array([min(1.0, cfg.object_max_jump * (1 + t.age)) for t in live])
            # A pair is admissible when within its own track's allowance;
            # scale distances so one floor serves every row.
            scaled = dist / allow[:, None]
            pairs = greedy_match(scaled, 1.0, larger_is_better=False)
            taken = set()
            for r, c in pairs:
                live[r].update(dets[c], frame_index, cfg.velocity_blend)
                out.append((live[r], dets[c]))
                taken.add(c)
            dets = [d for i, d in enumerate(dets) if i not in taken]
        for d in dets:
            t = self._new(d, frame_index, True)
            live.append(t)
            out.append((t, d))
        keep = []
        for t in live:
            if frame_index - t.last_frame > cfg.object_max_age:
                self.finished.append(t)
            else:
                keep.append(t)
        self.objects = keep
        return out

    def close(self) -> list[Track]:
        """Everything that was ever tracked, once the frames run out."""
        self.finished.extend(self.people)
        self.finished.extend(self.objects)
        self.people, self.objects = [], []
        return self.finished


def track_frames(frames: Iterable[tuple[int, int, Sequence[Detection]]], cfg: TrackerConfig,
                 object_classes: Iterable[str], frame_size: tuple[int, int]) -> list[dict]:
    """Run the tracker over (frame_index, t_ms, detections) in order and
    return fragment rows, with tracks shorter than the minimum dropped."""
    tracker = Tracker(cfg, object_classes, frame_size)
    rows: list[dict] = []
    for frame_index, t_ms, dets in frames:
        for t, d in tracker.step(frame_index, dets):
            rows.append({"frame_index": frame_index, "t_ms": t_ms, "track_id": t.id, "cls": d.cls,
                         "x1": d.x1, "y1": d.y1, "x2": d.x2, "y2": d.y2, "conf": d.conf})
    hits = {t.id: (t.hits, t.is_object) for t in tracker.close()}
    return [r for r in rows
            if hits[r["track_id"]][0] >= (cfg.object_min_hits if hits[r["track_id"]][1] else cfg.min_hits)]
