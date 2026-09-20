"""Finding the painted lines in a frame.

Nothing here knows what sport it is looking at. It finds the grass, finds the
thin bright things on the grass, turns them into straight lines, and splits
those lines into two directions. Which direction is the touchline is the
registrar's problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np


@dataclass
class LineDetectionConfig:
    grass_hue: tuple[int, int] = (30, 95)     # OpenCV hue is 0..180; green sits near 60
    grass_min_sat: int = 35
    grass_min_val: int = 40
    grass_close_px: int = 25
    tophat_px: int = 15                        # a little wider than the widest line
    tophat_thresh: int = 25
    line_min_val: int = 90
    line_max_sat: int = 90
    min_component_len: int = 25
    min_elongation: float = 3.0                # bbox diagonal^2 / area; blobs sit near 2.5
    hough_threshold: int = 50
    hough_min_len: int = 40
    hough_max_gap: int = 15
    merge_angle_deg: float = 3.0
    merge_dist_px: float = 8.0
    min_line_len: int = 60
    max_lines: int = 12


@dataclass
class DetectedLine:
    p0: np.ndarray
    p1: np.ndarray
    support: float          # summed length of the segments that voted for it

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.p1 - self.p0))

    @property
    def direction(self) -> np.ndarray:
        d = self.p1 - self.p0
        return d / max(np.linalg.norm(d), 1e-12)

    @property
    def angle(self) -> float:
        """Direction angle in [0, pi)."""
        d = self.direction
        return float(np.arctan2(d[1], d[0]) % np.pi)

    @property
    def abc(self) -> np.ndarray:
        d = self.direction
        a, b = -d[1], d[0]
        return np.array([a, b, -(a * self.p0[0] + b * self.p0[1])])

    @property
    def midpoint(self) -> np.ndarray:
        return (self.p0 + self.p1) / 2

    def samples(self, step: float = 10.0) -> np.ndarray:
        n = max(2, int(self.length / step) + 1)
        t = np.linspace(0, 1, n)
        return self.p0[None, :] + t[:, None] * (self.p1 - self.p0)[None, :]


@dataclass
class LineDetection:
    grass: np.ndarray            # uint8 mask of the playing surface
    line_pixels: np.ndarray      # uint8 mask of pixels that look like paint
    segments: np.ndarray         # (n, 4) raw Hough segments
    lines: list[DetectedLine] = field(default_factory=list)


def grass_mask(bgr: np.ndarray, cfg: LineDetectionConfig) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    lo, hi = cfg.grass_hue
    m = ((h >= lo) & (h <= hi) & (s >= cfg.grass_min_sat) & (v >= cfg.grass_min_val)).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.grass_close_px, cfg.grass_close_px))
    # Closing swallows the lines and the players, which are holes in the
    # green; opening drops small green things that are not the pitch.
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, cfg.grass_close_px // 2),) * 2)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k2)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return np.zeros_like(m)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    m = (labels == largest).astype(np.uint8) * 255
    # Fill holes so a white kit or a goal net in the middle of the pitch does
    # not punch a hole in the surface.
    padded = np.pad(m, 1)
    ff = padded.copy()
    cv2.floodFill(ff, np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(ff)
    return cv2.bitwise_or(padded, holes)[1:-1, 1:-1]


def line_pixel_mask(bgr: np.ndarray, grass: np.ndarray, cfg: LineDetectionConfig) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (cfg.tophat_px, cfg.tophat_px))
    tophat = cv2.subtract(v, cv2.morphologyEx(v, cv2.MORPH_OPEN, k))
    cand = (tophat >= cfg.tophat_thresh) & (s <= cfg.line_max_sat) & (v >= cfg.line_min_val) & (grass > 0)
    cand = cand.astype(np.uint8) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    if n <= 1:
        return np.zeros_like(cand)
    w = stats[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    h = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    area = np.maximum(stats[:, cv2.CC_STAT_AREA].astype(np.float64), 1)
    diag = np.hypot(w, h)
    # A painted line is long and thin: its bounding diagonal squared is many
    # times its area. A player in a white kit is a blob and is not.
    keep = (diag >= cfg.min_component_len) & (diag * diag / area >= cfg.min_elongation)
    keep[0] = False
    return np.where(keep[labels], 255, 0).astype(np.uint8)


def detect_segments(mask: np.ndarray, cfg: LineDetectionConfig) -> np.ndarray:
    segs = cv2.HoughLinesP(
        mask, 1, np.pi / 180, threshold=cfg.hough_threshold,
        minLineLength=cfg.hough_min_len, maxLineGap=cfg.hough_max_gap,
    )
    if segs is None:
        return np.zeros((0, 4), dtype=np.float64)
    return segs.reshape(-1, 4).astype(np.float64)


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % np.pi
    return min(d, np.pi - d)


class _Cluster:
    def __init__(self, seg: np.ndarray) -> None:
        self.pts: list[np.ndarray] = []
        self.weights: list[float] = []
        self.add(seg)

    def add(self, seg: np.ndarray) -> None:
        p0, p1 = seg[:2], seg[2:]
        w = float(np.linalg.norm(p1 - p0))
        self.pts += [p0, p1]
        self.weights += [w, w]
        self._fit()

    def _fit(self) -> None:
        P = np.array(self.pts)
        w = np.array(self.weights)
        c = (P * w[:, None]).sum(0) / w.sum()
        Q = (P - c) * np.sqrt(w)[:, None]
        _, _, vt = np.linalg.svd(Q, full_matrices=False)
        d = vt[0]
        t = (P - c) @ d
        self.centre, self.direction = c, d
        self.p0, self.p1 = c + t.min() * d, c + t.max() * d
        self.support = float(w.sum()) / 2
        self.angle = float(np.arctan2(d[1], d[0]) % np.pi)
        a, b = -d[1], d[0]
        self.abc = np.array([a, b, -(a * c[0] + b * c[1])])

    def accepts(self, p0: np.ndarray, p1: np.ndarray, angle: float, cfg: LineDetectionConfig) -> bool:
        if _angle_diff(angle, self.angle) > np.deg2rad(cfg.merge_angle_deg):
            return False
        d0 = abs(self.abc[:2] @ p0 + self.abc[2])
        d1 = abs(self.abc[:2] @ p1 + self.abc[2])
        return max(d0, d1) <= cfg.merge_dist_px

    def to_line(self) -> DetectedLine:
        return DetectedLine(self.p0.copy(), self.p1.copy(), self.support)


def merge_segments(segs: np.ndarray, cfg: LineDetectionConfig) -> list[DetectedLine]:
    """Hough returns many overlapping fragments of each painted line, and a
    line broken by a player is several fragments far apart. Collinear
    fragments are pooled and refit as one line, whatever the gaps."""
    if len(segs) == 0:
        return []
    lengths = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    order = np.argsort(-lengths)
    clusters: list[_Cluster] = []
    for i in order:
        seg = segs[i]
        p0, p1 = seg[:2], seg[2:]
        d = p1 - p0
        angle = float(np.arctan2(d[1], d[0]) % np.pi)
        for c in clusters:
            if c.accepts(p0, p1, angle, cfg):
                c.add(seg)
                break
        else:
            clusters.append(_Cluster(seg))
    # Second pass: fragments assigned early to different clusters can turn out
    # collinear once each cluster has been refit on all its members.
    merged = True
    while merged:
        merged = False
        clusters.sort(key=lambda c: -c.support)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                cj = clusters[j]
                if clusters[i].accepts(cj.p0, cj.p1, cj.angle, cfg):
                    for k in range(0, len(cj.pts), 2):
                        clusters[i].add(np.concatenate([cj.pts[k], cj.pts[k + 1]]))
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break
    lines = [c.to_line() for c in clusters if np.linalg.norm(c.p1 - c.p0) >= cfg.min_line_len]
    lines.sort(key=lambda l: -l.support)
    return lines[: cfg.max_lines]


def split_families(lines: list[DetectedLine], min_separation_deg: float = 20.0) -> tuple[list[DetectedLine], list[DetectedLine]]:
    """Two clusters of near-parallel image lines, longest first in each.

    Parallel surface lines converge in the image, so a plain angle threshold
    would split one family in two on a wide view. Two-means on the doubled
    angle (so 0 and 180 degrees coincide) gets the grouping right for the
    views a sideline camera produces; the registrar tries both assignments of
    cluster to surface direction anyway.
    """
    if len(lines) < 2:
        return list(lines), []
    ang = np.array([l.angle for l in lines])
    w = np.array([l.support for l in lines])
    v = np.stack([np.cos(2 * ang), np.sin(2 * ang)], axis=1)
    c0 = v[int(np.argmax(w))]
    c1 = v[int(np.argmin(v @ c0))]
    assign = np.zeros(len(lines), dtype=bool)
    for _ in range(10):
        assign = (v @ c0) >= (v @ c1)
        if assign.all() or (~assign).all():
            break
        n0 = (v[assign] * w[assign, None]).sum(0)
        n1 = (v[~assign] * w[~assign, None]).sum(0)
        c0, c1 = n0 / max(np.linalg.norm(n0), 1e-12), n1 / max(np.linalg.norm(n1), 1e-12)
    sep = np.degrees(np.arccos(np.clip(c0 @ c1, -1, 1)) / 2)
    if sep < min_separation_deg or assign.all() or (~assign).all():
        return sorted(lines, key=lambda l: -l.support), []
    A = sorted([l for l, a in zip(lines, assign) if a], key=lambda l: -l.support)
    B = sorted([l for l, a in zip(lines, assign) if not a], key=lambda l: -l.support)
    return A, B


def detect_lines(bgr: np.ndarray, cfg: Optional[LineDetectionConfig] = None) -> LineDetection:
    cfg = cfg or LineDetectionConfig()
    grass = grass_mask(bgr, cfg)
    pixels = line_pixel_mask(bgr, grass, cfg)
    segs = detect_segments(pixels, cfg)
    return LineDetection(grass, pixels, segs, merge_segments(segs, cfg))
