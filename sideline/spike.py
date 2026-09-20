"""M1: the registration spike.

Before any infrastructure, take frames from a real Veo export and try to fit
homographies. If per-frame registration on this footage does not work,
nothing downstream matters. This runs the follow-cam registrar over evenly
spaced frames, writes an overlay for each so a person can eyeball the fit,
and a report with the numbers the spec's gate asks for.

What it cannot measure on real footage is the reprojection error, because
there is no ground truth; `--selftest` renders frames through a known camera
instead, which checks the machinery, not the footage.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from sideline.registration.followcam import FollowCamConfig, FollowCamRegistrar
from sideline.registration.geometry import apply_h, project_in_view, sample_circle, sample_segment, surface_error_m
from sideline.registration.lines import LineDetection
from sideline.sports import get_sport
from sideline.sports.base import SurfaceModel


@dataclass
class FrameReport:
    frame_index: int
    t_ms: int
    registered: bool
    confidence: float
    n_lines: int
    lines_detected: int
    reason: str
    seconds: float
    centre_xy: Optional[list[float]] = None      # where the frame's middle lands on the pitch
    error_m: Optional[float] = None              # selftest only


@dataclass
class SpikeReport:
    source: str
    frames: int
    registered: int
    registered_fraction: float
    mean_confidence: float
    confidence_histogram: dict[str, int]
    failure_reasons: dict[str, int]
    seconds_per_frame: float
    # Between consecutive registered frames, how far the frame centre moved
    # on the pitch per second of video. A follow-cam pans smoothly; a spike
    # here is a frame that registered to the wrong place.
    centre_jump_m_per_s_p50: Optional[float]
    centre_jump_m_per_s_p90: Optional[float]
    selftest_error_m_mean: Optional[float] = None
    selftest_error_m_p90: Optional[float] = None
    selftest_wrong_over_3m: Optional[int] = None
    per_frame: list[FrameReport] = field(default_factory=list)


def sample_frames(video: Path, n: int, burst: int = 20, sample_fps: float = 5.0) -> Iterator[tuple[int, int, np.ndarray]]:
    """`n` frames as bursts of `burst` consecutive samples at the pipeline's
    rate, the bursts spread evenly over the file. Bursts rather than a flat
    spread because the frame-centre jump between neighbours only says
    something when the neighbours are 200 ms apart, not half a minute."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open {video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if total <= 0:
        raise ValueError(f"{video} reports no frames; transcode it first")
    burst = max(1, min(burst, n))
    step = max(1, int(round(fps / sample_fps)))
    span = step * (burst - 1)
    starts = np.linspace(0, max(0, total - 1 - span), max(1, n // burst)).astype(int)
    try:
        for start in starts:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(start))
            for k in range(burst):
                idx = int(start) + k * step
                if idx >= total:
                    break
                # Walk to the next sample with grab(), which does not decode.
                ok = True
                for _ in range(step - 1 if k else 0):
                    ok = cap.grab()
                if not ok:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                yield idx, int(round(1000 * idx / fps)), frame
    finally:
        cap.release()


def synthetic_frames(surface: SurfaceModel, n: int, seed: int = 0) -> Iterator[tuple[int, int, np.ndarray, np.ndarray]]:
    """Frames from a mast on the near touchline panning along the pitch, the
    way a follow-cam does, with the true homography for each."""
    from sideline.registration.synthetic import render_pitch_frame, sideline_camera

    rng = np.random.default_rng(seed)
    for k in range(n):
        aim_x = float(-48 + 96 * (0.5 + 0.5 * np.sin(2 * np.pi * k / n)))
        aim_y = float(rng.uniform(-10, 10))
        zoom = float(rng.uniform(1.0, 1.6))
        H = sideline_camera(surface, aim_x, aim_y, zoom)
        yield k, k * 200, render_pitch_frame(surface, H, players=int(rng.integers(3, 10)), seed=k), H


def draw_overlay(frame: np.ndarray, surface: SurfaceModel, det: LineDetection, H_p2i: Optional[np.ndarray], text: str) -> np.ndarray:
    vis = frame.copy()
    size = (frame.shape[1], frame.shape[0])
    for l in det.lines:
        cv2.line(vis, tuple(np.round(l.p0).astype(int)), tuple(np.round(l.p1).astype(int)), (255, 128, 0), 2)
    if H_p2i is not None:
        for l in surface.lines():
            pts = project_in_view(H_p2i, sample_segment(l.p0, l.p1, 0.25), size)
            for p in pts:
                cv2.circle(vis, (int(p[0]), int(p[1])), 2, (0, 0, 255), -1)
        for c in surface.circles():
            for p in project_in_view(H_p2i, sample_circle(c.centre, c.radius, 0.25), size):
                cv2.circle(vis, (int(p[0]), int(p[1])), 2, (0, 0, 255), -1)
    cv2.putText(vis, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(vis, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def draw_birdseye(surface: SurfaceModel, hull_px: np.ndarray, H_i2p: np.ndarray, scale: float = 6.0) -> np.ndarray:
    """The pitch from above with the frame's grass footprint on it."""
    L, W = surface.length, surface.width
    m = 4.0
    img = np.full((int((W + 2 * m) * scale), int((L + 2 * m) * scale), 3), (70, 150, 70), np.uint8)

    def to_px(pts: np.ndarray) -> np.ndarray:
        return np.stack([(pts[:, 0] + L / 2 + m) * scale, (W / 2 + m - pts[:, 1]) * scale], axis=1)

    for l in surface.lines():
        p = to_px(np.array([l.p0, l.p1], float)).astype(int)
        cv2.line(img, tuple(p[0]), tuple(p[1]), (240, 240, 240), 2)
    for c in surface.circles():
        cv2.polylines(img, [to_px(sample_circle(c.centre, c.radius, 0.5)).astype(np.int32)], True, (240, 240, 240), 2)
    foot = apply_h(H_i2p, hull_px)
    foot = foot[np.isfinite(foot).all(axis=1)]
    foot = foot[(np.abs(foot[:, 0]) < L) & (np.abs(foot[:, 1]) < W)]
    if len(foot) >= 3:
        cv2.polylines(img, [to_px(foot).astype(np.int32)], True, (0, 0, 255), 2)
    return img


def run_spike(
    video: Optional[Path],
    out: Path,
    frames: int = 200,
    pitch: tuple[float, float] = (105.0, 68.0),
    config: Optional[FollowCamConfig] = None,
    selftest: bool = False,
    overlays: bool = True,
    burst: int = 20,
) -> SpikeReport:
    surface = get_sport("soccer").surface(*pitch)
    reg = FollowCamRegistrar(surface, config)
    out.mkdir(parents=True, exist_ok=True)
    if overlays:
        (out / "overlays").mkdir(exist_ok=True)
        (out / "birdseye").mkdir(exist_ok=True)

    per: list[FrameReport] = []
    if selftest:
        source = "selftest"
        stream = synthetic_frames(surface, frames)
    else:
        assert video is not None
        source = str(video)
        stream = ((i, t, f, None) for i, t, f in sample_frames(video, frames, burst=burst))

    for idx, t_ms, frame, H_true in stream:
        t0 = time.perf_counter()
        r = reg.register(frame, idx, t_ms)
        dt = time.perf_counter() - t0
        det = reg.detect(frame)
        size = (frame.shape[1], frame.shape[0])
        fr = FrameReport(idx, t_ms, r.registered, r.confidence, r.n_lines, len(det.lines), r.reason, dt)
        if r.mapping is not None:
            c = r.to_pitch(np.array([[size[0] / 2, size[1] / 2]]))[0]
            fr.centre_xy = [float(c[0]), float(c[1])] if np.isfinite(c).all() else None
            if H_true is not None:
                grid = np.stack(np.meshgrid(np.linspace(0, size[0], 13), np.linspace(0, size[1], 8)), -1).reshape(-1, 2)
                truth = apply_h(np.linalg.inv(H_true), grid)
                on = np.isfinite(truth).all(1) & surface_inside(surface, truth)
                fr.error_m = float(np.nanmean(surface_error_m(np.linalg.inv(H_true), r.H, grid[on]))) if on.any() else None
        per.append(fr)
        if overlays:
            label = f"#{idx} t={t_ms/1000:.1f}s conf={r.confidence:.2f} lines={r.n_lines}/{len(det.lines)}"
            label += "" if r.registered else f" REJECTED: {r.reason}"
            if fr.error_m is not None:
                label += f" err={fr.error_m:.1f}m"
            H_p2i = r.mapping.H_p2i if r.mapping is not None else None  # type: ignore[union-attr]
            cv2.imwrite(str(out / "overlays" / f"{idx:07d}.jpg"), draw_overlay(frame, surface, det, H_p2i, label), [cv2.IMWRITE_JPEG_QUALITY, 80])
            if r.registered:
                ys, xs = np.nonzero(det.grass)
                if len(xs) > 10:
                    pts = np.stack([xs, ys], 1).astype(np.int32)
                    hull = cv2.convexHull(pts).reshape(-1, 2).astype(np.float64)
                    cv2.imwrite(str(out / "birdseye" / f"{idx:07d}.png"), draw_birdseye(surface, hull, r.H))

    report = summarise(source, per)
    (out / "report.json").write_text(json.dumps(asdict(report), indent=2))
    return report


def surface_inside(surface: SurfaceModel, pts: np.ndarray) -> np.ndarray:
    return (np.abs(pts[:, 0]) <= surface.length / 2) & (np.abs(pts[:, 1]) <= surface.width / 2)


def summarise(source: str, per: list[FrameReport]) -> SpikeReport:
    n = len(per)
    reg = [f for f in per if f.registered]
    hist = Counter()
    for f in per:
        b = min(9, int(f.confidence * 10))
        hist[f"{b/10:.1f}-{(b+1)/10:.1f}"] += 1
    reasons = Counter(f.reason.split(" ")[0] + " " + " ".join(f.reason.split(" ")[1:4]) for f in per if not f.registered)
    jumps = []
    for a, b in zip(reg, reg[1:]):
        if a.centre_xy and b.centre_xy and b.t_ms > a.t_ms:
            d = float(np.hypot(a.centre_xy[0] - b.centre_xy[0], a.centre_xy[1] - b.centre_xy[1]))
            jumps.append(d / ((b.t_ms - a.t_ms) / 1000.0))
    errs = [f.error_m for f in reg if f.error_m is not None]
    return SpikeReport(
        source=source,
        frames=n,
        registered=len(reg),
        registered_fraction=(len(reg) / n) if n else 0.0,
        mean_confidence=float(np.mean([f.confidence for f in per])) if per else 0.0,
        confidence_histogram=dict(sorted(hist.items())),
        failure_reasons=dict(reasons.most_common()),
        seconds_per_frame=float(np.mean([f.seconds for f in per])) if per else 0.0,
        centre_jump_m_per_s_p50=float(np.percentile(jumps, 50)) if jumps else None,
        centre_jump_m_per_s_p90=float(np.percentile(jumps, 90)) if jumps else None,
        selftest_error_m_mean=float(np.mean(errs)) if errs else None,
        selftest_error_m_p90=float(np.percentile(errs, 90)) if errs else None,
        selftest_wrong_over_3m=int(sum(e > 3.0 for e in errs)) if errs else None,
        per_frame=per,
    )


def format_report(r: SpikeReport) -> str:
    lines = [
        f"source: {r.source}",
        f"frames: {r.frames}   registered: {r.registered} ({100 * r.registered_fraction:.0f}%)   gate: >= 70%",
        f"mean confidence: {r.mean_confidence:.2f}   seconds/frame: {r.seconds_per_frame:.2f}",
        "confidence histogram: " + ", ".join(f"{k}: {v}" for k, v in r.confidence_histogram.items()),
        "failure reasons: " + (", ".join(f"{k}: {v}" for k, v in r.failure_reasons.items()) or "none"),
    ]
    if r.centre_jump_m_per_s_p50 is not None:
        lines.append(f"frame-centre jump between registered frames: p50 {r.centre_jump_m_per_s_p50:.1f} m/s, p90 {r.centre_jump_m_per_s_p90:.1f} m/s")
    if r.selftest_error_m_mean is not None:
        lines.append(f"selftest error vs truth: mean {r.selftest_error_m_mean:.2f} m, p90 {r.selftest_error_m_p90:.2f} m, wrong by >3 m: {r.selftest_wrong_over_3m}   gate: < 2 m")
    return "\n".join(lines)
