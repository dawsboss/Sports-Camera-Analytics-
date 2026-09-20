"""S0 — Ingest.

Transcode to a known codec, pull keyframes for the scrubber, look for the
half-time break, write match.json with duration, fps and resolution. The
video that every later stage decodes is this stage's `video.mp4`, never the
upload, so a re-encode is a new S0 version and nothing downstream has to
know what Veo exported.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from sideline.contracts import CaptureMode, Halftime, MatchJson, PitchDimensions
from sideline.registration.lines import LineDetectionConfig, grass_mask
from sideline.stages.base import StageContext
from sideline.storage.artifacts import ArtifactRef


def probe(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video {path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = fourcc.to_bytes(4, "little").decode("ascii", "replace").strip("\x00") if fourcc else None
    finally:
        cap.release()
    return {"fps": fps, "frame_count": frames, "width": w, "height": h, "codec": codec,
            "duration_ms": int(round(1000 * frames / fps)) if frames > 0 else 0}


def transcode(src: Path, dst: Path) -> bool:
    """H.264 in an MP4 with the index up front, if ffmpeg is on the path.
    Without it the source is used as is and match.json says so."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-preset", "fast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)
    return True


def detect_halftime(path: Path, info: dict, every_s: float = 10.0, min_break_s: float = 180.0,
                    grass_floor: float = 0.3) -> Optional[Halftime]:
    """The break is the longest stretch in the middle half of the file with
    little grass in frame: the camera idles on the stands, the export cuts
    to a title card, or the crop sits on a huddle. Sampled every ten
    seconds on a small frame; it is a hint for the scrubber and for lining
    up the minutes app's periods, not the clock."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps, total = info["fps"], info["frame_count"]
    step = max(1, int(round(every_s * fps)))
    cfg = LineDetectionConfig()
    samples = []
    try:
        for f in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            samples.append((int(round(1000 * f / fps)), float((grass_mask(small, cfg) > 0).mean())))
    finally:
        cap.release()
    if len(samples) < 6:
        return None
    lo, hi = info["duration_ms"] * 0.25, info["duration_ms"] * 0.75
    best: Optional[tuple[int, int]] = None
    start = None
    for t, g in samples + [(info["duration_ms"], 1.0)]:
        if g < grass_floor and lo <= t <= hi:
            start = t if start is None else start
        else:
            if start is not None and (best is None or t - start > best[1] - best[0]):
                best = (start, t)
            start = None
    if best and best[1] - best[0] >= min_break_s * 1000:
        return Halftime(start_ms=best[0], end_ms=best[1], method="grass-gap")
    return None


def write_keyframes(ctx: StageContext, ref: ArtifactRef, path: Path, info: dict, every_s: float = 60.0) -> list[str]:
    cap = cv2.VideoCapture(str(path))
    keys: list[str] = []
    if not cap.isOpened():
        return keys
    step = max(1, int(round(every_s * info["fps"])))
    try:
        for f in range(0, info["frame_count"], step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, frame = cap.read()
            if not ok:
                break
            t_ms = int(round(1000 * f / info["fps"]))
            h = 180
            w = max(16, int(frame.shape[1] * h / frame.shape[0]))
            thumb = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                keys.append(ctx.artifacts.write_bytes(ref, f"keyframes/{t_ms:09d}.jpg", buf.tobytes(), "image/jpeg"))
    finally:
        cap.release()
    return keys


class IngestStage:
    name = "s0_ingest"
    version = "0.1.0"

    def run(self, ctx: StageContext) -> ArtifactRef:
        m = ctx.match
        ref = ctx.artifacts.new_version(m.id, self.name)
        src = ctx.fetch(m.source_key, "source" + Path(m.source_key).suffix)
        work = ctx.workdir / f"{m.id}_{self.name}_v{ref.version:03d}"
        work.mkdir(parents=True, exist_ok=True)
        out = work / "video.mp4"
        transcoded = bool(ctx.config.get("transcode", True)) and transcode(src, out)
        if not transcoded:
            out = src
        info = probe(out)
        if info["frame_count"] <= 0:
            raise ValueError("video reports no frames after ingest")
        video_key = ref.key("video.mp4")
        ctx.blobs.put_file(video_key, out, "video/mp4")
        halftime = detect_halftime(out, info) if ctx.config.get("detect_halftime", True) else None
        keyframes = write_keyframes(ctx, ref, out, info, every_s=float(ctx.config.get("keyframe_every_s", 60.0)))
        doc = MatchJson(
            match_id=m.id, sm_match_id=m.sm_match_id, capture_mode=CaptureMode(m.capture_mode),
            source_key=m.source_key, video_key=video_key, duration_ms=info["duration_ms"], fps=info["fps"],
            width=info["width"], height=info["height"], frame_count=info["frame_count"], codec=info["codec"],
            transcoded=transcoded, halftime=halftime, kickoff_offset_ms=m.kickoff_offset_ms,
            pitch=PitchDimensions(length_m=m.pitch_length, width_m=m.pitch_width),
        )
        ctx.artifacts.write_json(ref, "match.json", doc.model_dump(mode="json"))
        ctx.artifacts.write_manifest(
            ref, config={"transcode": bool(ctx.config.get("transcode", True)), "ffmpeg": shutil.which("ffmpeg") is not None},
            inputs=[], capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version, outputs=["match.json", "video.mp4"] + [k[len(ref.prefix):] for k in keyframes],
        )
        return ref
