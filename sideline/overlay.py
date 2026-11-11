"""Watching what the stages saw.

An overlay video is the spec's M3 deliverable and the cheapest way to see a
run decide: every sampled frame with the fragments drawn on it, the ball's
recent path, and at each event a caption saying what was called, who took
it, how sure, and why. It renders from S1's cached frames, S3's fragments
and S8's events, all local, and writes an MP4 that scrubs. Nothing here
decides anything; it only shows.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from sideline.storage.artifacts import ArtifactRef, ArtifactStore


def _colour(track_id: int) -> tuple[int, int, int]:
    """A stable, distinct colour per track, so a fragment that breaks shows
    as a colour change and nothing else does."""
    rng = np.random.default_rng(track_id * 7919)
    h = int(rng.integers(0, 180))
    bgr = cv2.cvtColor(np.uint8([[[h, 200, 230]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def draw_frame(frame: np.ndarray, boxes: list[dict], trail: list[tuple[float, float]], caption: Optional[dict],
               object_classes: set[str], highlight: Optional[int]) -> np.ndarray:
    out = frame.copy()
    H, W = out.shape[:2]
    scale = max(0.4, W / 1920)
    for b in boxes:
        x1, y1, x2, y2 = (int(round(b[k])) for k in ("x1", "y1", "x2", "y2"))
        if b["cls"] in object_classes:
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            cv2.circle(out, (cx, cy), max(4, int(8 * scale)), (0, 220, 255), 2)
            continue
        col = (0, 255, 255) if b["track_id"] == highlight else _colour(b["track_id"])
        cv2.rectangle(out, (x1, y1), (x2, y2), col, 3 if b["track_id"] == highlight else 1)
        cv2.putText(out, str(b["track_id"]), (x1, max(10, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, col, 1, cv2.LINE_AA)
    for a, b in zip(trail, trail[1:]):
        cv2.line(out, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (0, 220, 255), 1)
    if caption:
        who = caption["player_id"] or (f"track {caption['taker_track']}" if caption["taker_track"] else "nobody near")
        text = f"{caption['clock']}  {caption['event'].upper()}  {who}  {caption['confidence']}"
        why = caption["reason"]
        bar = int(44 * scale) + 20
        cv2.rectangle(out, (0, 0), (W, bar), (20, 20, 20), -1)
        cv2.putText(out, text, (10, int(18 * scale) + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(out, why, (10, int(38 * scale) + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (200, 200, 200), 1, cv2.LINE_AA)
    return out


def render_overlay(store: ArtifactStore, match_id: str, out: Path, *, around_s: Optional[float] = None,
                   hold_s: float = 3.0, fps: Optional[float] = None, object_classes: set[str] = frozenset({"ball"}),
                   trail: int = 10) -> dict:
    """Write the overlay MP4 for the latest versions of S1, S3 and S8.
    `around_s` keeps only the seconds around each event; None renders every
    sampled frame. Returns what was rendered."""
    s1 = store.latest(match_id, "s1_sampling")
    s3 = store.latest(match_id, "s3_tracking")
    s8 = store.latest(match_id, "s8_events")
    if s1 is None or s3 is None:
        raise RuntimeError("the overlay needs s1_sampling and s3_tracking")
    frames = store.read_table(s1, "frames.parquet").to_pylist()
    sample_fps = float(store.read_manifest(s1)["config"].get("sample_fps", 5.0))
    frags = store.read_table(s3, "fragments.parquet").to_pylist()
    by_frame: dict[int, list[dict]] = defaultdict(list)
    for r in frags:
        by_frame[r["frame_index"]].append(r)
    events: list[dict] = []
    if s8 is not None and store.blobs.exists(s8.key("events.csv")):
        events = list(csv.DictReader(io.StringIO(store.blobs.get(s8.key("events.csv")).decode())))
        for e in events:
            e["t_ms"] = int(e["t_ms"])
            e["taker_track"] = int(e["taker_track"]) if e["taker_track"] else None

    def caption_at(t_ms: int) -> Optional[dict]:
        live = [e for e in events if e["t_ms"] - 1000 <= t_ms <= e["t_ms"] + hold_s * 1000]
        return min(live, key=lambda e: abs(e["t_ms"] - t_ms)) if live else None

    if around_s is not None:
        keep = [f for f in frames if any(abs(f["t_ms"] - e["t_ms"]) <= around_s * 1000 for e in events)]
    else:
        keep = frames
    first = next((f for f in keep if f.get("key")), None)
    if first is None:
        raise RuntimeError("the overlay needs S1's cached frames (cache_frames=True)")
    probe = cv2.imdecode(np.frombuffer(store.blobs.get(first["key"]), np.uint8), cv2.IMREAD_COLOR)
    h, w = probe.shape[:2]
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps or sample_fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"cannot write {out}")
    recent: list[tuple[float, float]] = []
    n = 0
    try:
        for f in keep:
            if not f.get("key"):
                continue
            frame = cv2.imdecode(np.frombuffer(store.blobs.get(f["key"]), np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            boxes = by_frame.get(f["frame_index"], [])
            balls = [b for b in boxes if b["cls"] in object_classes]
            if balls:
                b = max(balls, key=lambda b: b["conf"])
                recent.append(((b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2))
                recent = recent[-trail:]
            else:
                recent = recent[1:]
            cap = caption_at(f["t_ms"])
            writer.write(draw_frame(frame, boxes, recent, cap, object_classes, cap["taker_track"] if cap else None))
            n += 1
    finally:
        writer.release()
    return {"frames": n, "events": len(events), "out": str(out), "size": [w, h], "fps": fps or sample_fps}
