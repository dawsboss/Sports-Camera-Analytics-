"""S2 — Detection.

Run a detector over S1's sampled frames and write `detections.parquet`:
one row per box, in image pixels, labelled with the sport plugin's entity
class names. The stage knows nothing about the sport and nothing about the
camera: the same rows come out of a follow-cam frame and a static one, and
whether the boxes can be placed on the pitch is registration's business.

What is decided here and why:

- Which frames: S1's, so that a re-run of detection with a better model
  never re-decodes the video and lines up sample for sample with the old.
- People off the surface are dropped (`GrassFilter`) when the sport plugin
  says its surface is grass, because the Veo footage puts coaches and
  spectators along the edge of every frame and a tracker fed them spends
  its identities there. A court sport declares otherwise and keeps everyone.
- The detector is configuration (`detectors` in the stage config, or a
  `Detector` instance for tests), so the fine-tuned ball model replaces the
  COCO one by changing a path, and the manifest records which ran.
"""

from __future__ import annotations

from collections import Counter

import cv2
import numpy as np
import pyarrow as pa

from sideline.contracts import DETECTIONS_SCHEMA
from sideline.detection import Detector, GrassFilter, build_detector
from sideline.sports import get_sport
from sideline.stages.base import StageContext
from sideline.storage.artifacts import ArtifactRef


class DetectionStage:
    name = "s2_detection"
    version = "0.1.0"

    def run(self, ctx: StageContext) -> ArtifactRef:
        m = ctx.match
        s1 = ctx.artifacts.latest(m.id, "s1_sampling")
        if s1 is None or not ctx.artifacts.complete(s1):
            raise RuntimeError("s2_detection needs a completed s1_sampling version")
        s0 = ctx.artifacts.latest(m.id, "s0_ingest")
        frames = ctx.artifacts.read_table(s1, "frames.parquet").to_pylist()
        max_frames = ctx.config.get("max_frames")
        if max_frames is not None:
            frames = frames[: int(max_frames)]

        detector: Detector = ctx.config.get("detector") or build_detector(ctx.config.get("detectors"))
        sport = get_sport(m.sport)
        # The surface filter judges feet; the ball and the sport's fixed
        # landmarks have none and pass through.
        object_classes = {c.name for c in sport.entity_classes() if c.object or c.landmark}
        use_grass = ctx.config.get("grass_filter")
        if use_grass is None:
            use_grass = bool(getattr(sport, "surface_is_grass", False))
        grass = GrassFilter(min_frac=float(ctx.config.get("grass_min_frac", 0.3))) if use_grass else None

        ref = ctx.artifacts.new_version(m.id, self.name)
        video_key = ctx.artifacts.read_manifest(s1).get("video_key")
        rows: list[dict] = []
        kept, dropped = Counter(), 0
        for n, fr in enumerate(frames):
            frame = self._load(ctx, fr, video_key)
            if frame is None:
                continue
            ctx.progress(min(0.99, n / max(1, len(frames))), f"detecting in frame {n + 1} of {len(frames)}")
            dets = detector.detect(frame)
            if grass is not None and any(d.cls not in object_classes for d in dets):
                mask, scale = grass.mask(frame)
                before = len(dets)
                dets = [d for d in dets if d.cls in object_classes or grass.keep(d, mask, scale, frame.shape[0])]
                dropped += before - len(dets)
            for d in dets:
                kept[d.cls] += 1
                rows.append({"frame_index": fr["frame_index"], "t_ms": fr["t_ms"], "cls": d.cls,
                             "x1": d.x1, "y1": d.y1, "x2": d.x2, "y2": d.y2, "conf": d.conf})
        table = pa.Table.from_pylist(rows, schema=DETECTIONS_SCHEMA)
        ctx.artifacts.write_table(ref, "detections.parquet", table)
        ctx.progress(1.0, f"{len(rows)} detections in {len(frames)} frames")
        ctx.artifacts.write_manifest(
            ref, config={"detector": detector.describe(), "grass_filter": bool(use_grass),
                         "grass_min_frac": grass.min_frac if grass else None, "max_frames": max_frames},
            inputs=[i for i in (s1, s0) if i is not None], capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version, outputs=["detections.parquet"],
            extra={"frames": len(frames), "detections": dict(kept), "dropped_off_grass": dropped},
        )
        return ref

    def _load(self, ctx: StageContext, fr: dict, video_key: str | None) -> np.ndarray | None:
        """The cached JPEG when S1 kept one, else the source frame from the
        ingested video. Seeking per frame is slow and only for uncached runs."""
        if fr.get("key"):
            buf = np.frombuffer(ctx.blobs.get(fr["key"]), np.uint8)
            frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if frame is not None:
                return frame
        if not video_key:
            return None
        path = ctx.fetch(video_key, f"{ctx.match.id}_s0.mp4")
        cap = cv2.VideoCapture(str(path))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fr["source_frame"]))
            ok, frame = cap.read()
        finally:
            cap.release()
        return frame if ok else None
