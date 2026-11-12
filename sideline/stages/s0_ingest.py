"""S0 — Ingest.

Bring the video to a known codec, pull keyframes for the scrubber, look for
the break in play, write match.json with duration, fps and resolution. The
video that every later stage decodes is this stage's `video.mp4`, never the
upload, so a re-encode is a new S0 version and nothing downstream has to
know what Veo exported.

The known codec is H.264 in an MP4, and a Veo export already is one. Such a
file is remuxed, not re-encoded: the stream is copied into a fresh MP4 with
its index at the front, which is bound by disk speed and takes a minute,
where re-encoding a match on two cores took an hour. Anything else is
transcoded.
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


# What cv2 reports for an H.264 stream in an MP4 or MOV, depending on the muxer.
H264_FOURCCS = {"avc1", "h264", "H264", "AVC1", "x264"}


def is_h264_mp4(path: Path, info: dict) -> bool:
    return (info.get("codec") or "") in H264_FOURCCS and path.suffix.lower() in (".mp4", ".mov", ".m4v")


def transcode(src: Path, dst: Path, duration_ms: int = 0, progress=lambda f, d: None, copy: bool = False) -> bool:
    """H.264 in an MP4 with the index up front, if ffmpeg is on the path.
    Without it the source is used as is and match.json says so. With
    `copy` the stream is remuxed rather than re-encoded, for a source that
    is H.264 already. ffmpeg's own progress lines, against the source's
    duration, feed the callback: a re-encode takes an hour on two cores and
    a coach should see it move."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    codec = ["-c:v", "copy"] if copy else ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-i", str(src), *codec,
           "-an", "-movflags", "+faststart", str(dst)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_ms=") and duration_ms > 0:
                try:
                    done_ms = int(line.split("=", 1)[1]) / 1000.0
                except ValueError:
                    continue
                frac = max(0.0, min(0.99, done_ms / duration_ms))
                progress(frac, f"{'copying the video stream' if copy else 'transcoding'} {int(frac * 100)}%")
    finally:
        rc = proc.wait()
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
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
        ctx.progress(0.0, "probing the upload")
        source_info = probe(src)
        # Re-encoding is the hour; a Veo export is H.264 already and is only
        # remuxed, which is minutes. The rest is quick either way.
        remux = is_h264_mp4(src, source_info) and bool(ctx.config.get("remux_h264", True))
        converted = bool(ctx.config.get("transcode", True)) and transcode(
            src, out, source_info["duration_ms"], lambda f, d: ctx.progress(0.85 * f, d), copy=remux)
        transcoded, remuxed = (converted and not remux), (converted and remux)
        if not converted:
            out = src
        info = probe(out)
        if info["frame_count"] <= 0:
            raise ValueError("video reports no frames after ingest")
        video_key = ref.key("video.mp4")
        ctx.progress(0.86, "storing the transcoded video")
        ctx.blobs.put_file(video_key, out, "video/mp4")
        ctx.progress(0.9, "looking for the break in play")
        halftime = detect_halftime(out, info) if ctx.config.get("detect_halftime", True) else None
        ctx.progress(0.95, "writing keyframes")
        keyframes = write_keyframes(ctx, ref, out, info, every_s=float(ctx.config.get("keyframe_every_s", 60.0)))
        doc = MatchJson(
            match_id=m.id, sm_match_id=m.sm_match_id, capture_mode=CaptureMode(m.capture_mode),
            source_key=m.source_key, video_key=video_key, duration_ms=info["duration_ms"], fps=info["fps"],
            width=info["width"], height=info["height"], frame_count=info["frame_count"], codec=info["codec"],
            transcoded=transcoded, remuxed=remuxed, halftime=halftime, kickoff_offset_ms=m.kickoff_offset_ms,
            pitch=PitchDimensions(length_m=m.pitch_length, width_m=m.pitch_width),
        )
        ctx.artifacts.write_json(ref, "match.json", doc.model_dump(mode="json"))
        if out is not src:
            out.unlink(missing_ok=True)          # the blob store has it; the work copy is a second 2 GB
        ctx.artifacts.write_manifest(
            ref, config={"transcode": bool(ctx.config.get("transcode", True)), "remux_h264": bool(ctx.config.get("remux_h264", True)),
                         "ffmpeg": shutil.which("ffmpeg") is not None, "source_codec": source_info["codec"]},
            inputs=[], capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version, outputs=["match.json", "video.mp4"] + [k[len(ref.prefix):] for k in keyframes],
        )
        ctx.progress(1.0, "ingested")           # after the manifest: done means committed
        return ref
