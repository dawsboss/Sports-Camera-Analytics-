"""S1 — Sampling.

Decode at 5 fps rather than 25. Tracking interpolates between samples; this
cuts compute by five and costs almost nothing at youth-match speeds. The
output is an index of the sampled frames (`frames.parquet`) and, by
default, the frames themselves as JPEGs, so no later stage has to seek
through the video again.
"""

from __future__ import annotations

import cv2
import numpy as np
import pyarrow as pa

from sideline.contracts import FRAMES_SCHEMA
from sideline.stages.base import StageContext
from sideline.storage.artifacts import ArtifactRef


class SamplingStage:
    name = "s1_sampling"
    version = "0.1.0"

    def run(self, ctx: StageContext) -> ArtifactRef:
        m = ctx.match
        s0 = ctx.artifacts.latest(m.id, "s0_ingest")
        if s0 is None or not ctx.artifacts.complete(s0):
            raise RuntimeError("s1_sampling needs a completed s0_ingest version")
        match = ctx.artifacts.read_json(s0, "match.json")
        sample_fps = float(ctx.config.get("sample_fps", 5.0))
        cache = bool(ctx.config.get("cache_frames", True))
        quality = int(ctx.config.get("jpeg_quality", 90))
        max_frames = ctx.config.get("max_frames")
        ref = ctx.artifacts.new_version(m.id, self.name)

        path = ctx.fetch(match["video_key"], f"{m.id}_s0_v{s0.version:03d}.mp4")
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ValueError(f"cannot open {path}")
        fps = float(match["fps"])
        rows = []
        try:
            # Walk the file once; grab() is cheap, retrieve() only for the
            # frames we keep. Target k lands on source frame round(k*fps/sample_fps).
            k = 0
            source_index = 0
            next_target = 0
            while True:
                if max_frames is not None and k >= int(max_frames):
                    break
                if not cap.grab():
                    break
                if source_index == next_target:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    t_ms = int(round(1000 * source_index / fps))
                    key = None
                    if cache:
                        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
                        if ok:
                            key = ctx.artifacts.write_bytes(ref, f"frames/{k:07d}.jpg", buf.tobytes(), "image/jpeg")
                    rows.append({"frame_index": k, "t_ms": t_ms, "source_frame": source_index, "key": key})
                    k += 1
                    next_target = int(round(k * fps / sample_fps))
                source_index += 1
        finally:
            cap.release()
        if not rows:
            raise ValueError("no frames could be decoded")
        table = pa.Table.from_pylist(rows, schema=FRAMES_SCHEMA)
        ctx.artifacts.write_table(ref, "frames.parquet", table)
        ctx.artifacts.write_manifest(
            ref, config={"sample_fps": sample_fps, "cache_frames": cache, "jpeg_quality": quality},
            inputs=[s0], capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version,
            outputs=["frames.parquet"] + ([f"frames/{r['frame_index']:07d}.jpg" for r in rows] if cache else []),
            extra={"frames": len(rows), "video_key": match["video_key"]},
        )
        return ref
