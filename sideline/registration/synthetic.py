"""Frames rendered from the surface model through a known camera.

This is how registration is tested without footage: draw the pitch through a
homography we chose, run the registrar on the picture, and measure in metres
how far its answer is from the truth. It is also what `sideline spike
--selftest` shows before a real export is in hand.
"""

from __future__ import annotations

import numpy as np
import cv2

from sideline.registration.geometry import apply_h_w, sample_circle, sample_segment
from sideline.sports.base import SurfaceModel


def camera_homography(
    cam_xyz, look_at_xyz, focal_px: float, size: tuple[int, int], roll_deg: float = 0.0
) -> np.ndarray:
    """Surface->image homography of a pinhole camera at `cam_xyz` (metres, z
    up) looking at `look_at_xyz`. The surface is the plane z = 0."""
    cam = np.asarray(cam_xyz, dtype=np.float64)
    target = np.asarray(look_at_xyz, dtype=np.float64)
    fwd = target - cam
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    if roll_deg:
        r = np.deg2rad(roll_deg)
        right, up = np.cos(r) * right + np.sin(r) * up, -np.sin(r) * right + np.cos(r) * up
    R = np.stack([right, -up, fwd])          # image x right, image y down, z forward
    t = -R @ cam
    W, H = size
    K = np.array([[focal_px, 0, W / 2], [0, focal_px, H / 2], [0, 0, 1]], dtype=np.float64)
    P = K @ np.concatenate([R, t[:, None]], axis=1)
    Hm = P[:, [0, 1, 3]]
    return Hm / Hm[2, 2]


def render_pitch_frame(
    surface: SurfaceModel,
    H_p2i: np.ndarray,
    size: tuple[int, int] = (1280, 720),
    line_px: int = 4,
    noise_sigma: float = 5.0,
    blur: int = 3,
    players: int = 0,
    seed: int = 0,
    margin_m: float = 6.0,
) -> np.ndarray:
    """A BGR frame: mown grass with painted lines, surroundings beyond the
    run-off, sky above the horizon, and optionally a few players."""
    rng = np.random.default_rng(seed)
    W, Hh = size
    H_i2p = np.linalg.inv(H_p2i)
    ys, xs = np.mgrid[0:Hh, 0:W]
    px = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float64)
    xy, w = apply_h_w(H_i2p, px)
    in_front = w > 0
    on_surface = in_front & (np.abs(xy[:, 0]) <= surface.length / 2 + margin_m) & (np.abs(xy[:, 1]) <= surface.width / 2 + margin_m)
    img = np.empty((Hh * W, 3), dtype=np.float64)
    img[:] = (120, 110, 100)                                   # stands, dust, whatever is past the run-off
    img[~in_front] = (200, 170, 140)                           # sky
    stripe = np.where(np.sin(xy[:, 0] / 5.0 * np.pi) > 0, 1.0, 0.9)
    grass = np.array([70, 150, 70], dtype=np.float64)
    img[on_surface] = grass[None, :] * stripe[on_surface, None]
    img = img.reshape(Hh, W, 3)

    def draw(pts_m: np.ndarray, closed: bool = False) -> None:
        p, wv = apply_h_w(H_p2i, pts_m)
        ok = (wv > 1e-9) & np.isfinite(p).all(axis=1)
        ok &= (p[:, 0] > -W) & (p[:, 0] < 2 * W) & (p[:, 1] > -Hh) & (p[:, 1] < 2 * Hh)
        # Draw only unbroken runs of visible samples, so a line that crosses
        # the horizon is not joined across it.
        idx = np.flatnonzero(ok)
        if len(idx) < 2:
            return
        breaks = np.flatnonzero(np.diff(idx) > 1)
        for run in np.split(idx, breaks + 1):
            if len(run) >= 2:
                cv2.polylines(img, [np.round(p[run]).astype(np.int32)], False, (235, 235, 235), line_px, cv2.LINE_AA)

    for line in surface.lines():
        draw(sample_segment(line.p0, line.p1, 0.2))
    for c in surface.circles():
        pts = sample_circle(c.centre, c.radius, 0.2)
        draw(np.concatenate([pts, pts[:1]], axis=0))

    if players:
        kits = [(240, 240, 240), (40, 40, 200), (200, 60, 40)]
        for _ in range(players):
            x = rng.uniform(-surface.length / 2, surface.length / 2)
            y = rng.uniform(-surface.width / 2, surface.width / 2)
            foot, wv = apply_h_w(H_p2i, np.array([[x, y]]))
            head, _ = apply_h_w(H_p2i, np.array([[x, y]]))
            if wv[0] <= 0 or not (0 <= foot[0, 0] < W and 0 <= foot[0, 1] < Hh):
                continue
            # A player is roughly 1.8 m tall; approximate the local scale from a
            # 1 m step along y on the surface.
            step, _ = apply_h_w(H_p2i, np.array([[x, y + 1.0]]))
            ppm = float(np.linalg.norm(step[0] - foot[0]))
            h = max(6, int(1.8 * ppm))
            cx, cy = int(foot[0, 0]), int(foot[0, 1]) - h // 2
            cv2.ellipse(img, (cx, cy), (max(2, h // 4), h // 2), 0, 0, 360, kits[rng.integers(len(kits))], -1)

    img += rng.normal(0, noise_sigma, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if blur and blur > 1:
        img = cv2.GaussianBlur(img, (blur | 1, blur | 1), 0)
    return img


def sideline_camera(surface: SurfaceModel, aim_x: float, aim_y: float = 0.0, zoom: float = 1.0,
                    size: tuple[int, int] = (1280, 720), height_m: float = 8.0, setback_m: float = 12.0) -> np.ndarray:
    """A camera on the halfway line at the near touchline, raised on a mast,
    aimed at a point on the surface: the geometry of a Veo on its tripod, with
    the follow-cam's pan and zoom folded into `aim_x` and `zoom`."""
    W, _ = size
    cam = (0.0, -surface.width / 2 - setback_m, height_m)
    return camera_homography(cam, (aim_x, aim_y, 0.0), focal_px=0.9 * W * zoom, size=size)
