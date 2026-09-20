"""Homography arithmetic shared by both registration implementations.

Conventions: points are (n, 2) arrays; a homography H maps homogeneous points
x' ~ H x. `H_p2i` maps surface metres to image pixels and `H_i2p` the other
way; the artifact stores `H_i2p` because that is what every consumer wants.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def homogeneous(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64)
    return np.concatenate([pts, np.ones((*pts.shape[:-1], 1))], axis=-1)


def apply_h_w(H: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Apply H and also return the homogeneous scale w, whose sign says whether
    a point is in front of the camera (positive) or behind the horizon."""
    p = homogeneous(pts) @ np.asarray(H, dtype=np.float64).T
    w = p[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = p[..., :2] / w[..., None]
    return xy, w


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    xy, w = apply_h_w(H, pts)
    xy = np.array(xy, copy=True)
    xy[np.abs(w) < 1e-12] = np.nan
    return xy


def _normalization(pts: np.ndarray) -> np.ndarray:
    """Hartley normalisation: translate to the centroid, scale to mean
    distance sqrt(2). Without it the DLT is badly conditioned when image
    coordinates are in the hundreds and surface coordinates in the tens."""
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2) / d if d > 1e-12 else 1.0
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]], dtype=np.float64)


def homography_dlt(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """H with dst ~ H src, from n >= 4 point correspondences."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape[0] < 4 or src.shape != dst.shape:
        raise ValueError("need at least four matching point pairs")
    Ts, Td = _normalization(src), _normalization(dst)
    s = homogeneous(src) @ Ts.T
    d = homogeneous(dst) @ Td.T
    A = _dlt_rows(s, d)
    _, _, vt = np.linalg.svd(A)
    Hn = vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    if abs(H[2, 2]) > 1e-12:
        H = H / H[2, 2]
    return H


def _dlt_rows(s: np.ndarray, d: np.ndarray) -> np.ndarray:
    """The 2n x 9 system for batched or single inputs (…, n, 3)."""
    x, y = s[..., 0], s[..., 1]
    u, v = d[..., 0], d[..., 1]
    z = np.zeros_like(x)
    o = np.ones_like(x)
    r1 = np.stack([-x, -y, -o, z, z, z, u * x, u * y, u], axis=-1)
    r2 = np.stack([z, z, z, -x, -y, -o, v * x, v * y, v], axis=-1)
    return np.concatenate([r1, r2], axis=-2)


def homography_dlt_batched(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """(N, n, 2) -> (N, 3, 3). Hypothesis search fits tens of thousands of
    candidate homographies per frame; one batched SVD is what keeps that
    under a second."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    N, n, _ = src.shape
    cs, cd = src.mean(axis=1, keepdims=True), dst.mean(axis=1, keepdims=True)
    ds = np.sqrt(((src - cs) ** 2).sum(-1)).mean(axis=1)
    dd = np.sqrt(((dst - cd) ** 2).sum(-1)).mean(axis=1)
    ss = np.where(ds > 1e-12, np.sqrt(2) / np.maximum(ds, 1e-12), 1.0)
    sd = np.where(dd > 1e-12, np.sqrt(2) / np.maximum(dd, 1e-12), 1.0)
    s = homogeneous((src - cs) * ss[:, None, None])
    d = homogeneous((dst - cd) * sd[:, None, None])
    A = _dlt_rows(s, d)
    bad = ~np.isfinite(A).all(axis=(1, 2))
    A[bad] = 0.0
    _, _, vt = np.linalg.svd(A)
    Hn = vt[:, -1, :].reshape(N, 3, 3)
    Ts = np.zeros((N, 3, 3)); Ts[:, 0, 0] = ss; Ts[:, 1, 1] = ss; Ts[:, 2, 2] = 1
    Ts[:, 0, 2] = -ss * cs[:, 0, 0]; Ts[:, 1, 2] = -ss * cs[:, 0, 1]
    Tdi = np.zeros((N, 3, 3)); Tdi[:, 0, 0] = 1 / sd; Tdi[:, 1, 1] = 1 / sd; Tdi[:, 2, 2] = 1
    Tdi[:, 0, 2] = cd[:, 0, 0]; Tdi[:, 1, 2] = cd[:, 0, 1]
    H = Tdi @ Hn @ Ts
    H[bad] = np.nan
    return H


def line_through(p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
    """Homogeneous line (a, b, c) with a^2 + b^2 = 1 through two points."""
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    d = p1 - p0
    n = np.hypot(d[0], d[1])
    if n < 1e-12:
        raise ValueError("degenerate segment")
    a, b = -d[1] / n, d[0] / n
    return np.array([a, b, -(a * p0[0] + b * p0[1])])


def intersect(l1: np.ndarray, l2: np.ndarray) -> Optional[np.ndarray]:
    p = np.cross(l1, l2)
    if abs(p[2]) < 1e-9:
        return None
    return p[:2] / p[2]


def point_line_distance(pts: np.ndarray, line: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64)
    return np.abs(pts @ line[:2] + line[2]) / np.hypot(line[0], line[1])


def point_segment_distance(pts: np.ndarray, p0: np.ndarray, p1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance from points to a segment, and the foot of each point on the
    segment's infinite line (used as the target when refining a fit)."""
    pts = np.asarray(pts, dtype=np.float64)
    p0 = np.asarray(p0, dtype=np.float64)
    d = np.asarray(p1, dtype=np.float64) - p0
    len2 = float(d @ d)
    t = ((pts - p0) @ d) / max(len2, 1e-12)
    foot = p0 + t[:, None] * d
    proj = p0 + np.clip(t, 0, 1)[:, None] * d
    return np.linalg.norm(pts - proj, axis=1), foot


def sample_segment(p0, p1, step: float) -> np.ndarray:
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    n = max(2, int(np.linalg.norm(p1 - p0) / step) + 1)
    t = np.linspace(0, 1, n)
    return p0[None, :] + t[:, None] * (p1 - p0)[None, :]


def sample_circle(centre, radius: float, step: float) -> np.ndarray:
    n = max(8, int(2 * np.pi * radius / step))
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([centre[0] + radius * np.cos(a), centre[1] + radius * np.sin(a)], axis=1)


def project_in_view(H_p2i: np.ndarray, pts: np.ndarray, size: tuple[int, int], margin: float = 0.0) -> np.ndarray:
    """Surface points that land inside the image (with a margin) and in front
    of the camera. Returns (k, 2) image points."""
    xy, w = apply_h_w(H_p2i, pts)
    W, Hh = size
    ok = (w > 1e-9) & np.isfinite(xy).all(axis=1)
    ok &= (xy[:, 0] >= -margin) & (xy[:, 0] < W + margin) & (xy[:, 1] >= -margin) & (xy[:, 1] < Hh + margin)
    return xy[ok]


def surface_error_m(H_true_i2p: np.ndarray, H_est_i2p: np.ndarray, image_pts: np.ndarray) -> np.ndarray:
    """Per-point metres between where two mappings put the same pixels.
    This is the spec's reprojection error once ground truth exists."""
    a = apply_h(H_true_i2p, image_pts)
    b = apply_h(H_est_i2p, image_pts)
    return np.linalg.norm(a - b, axis=1)


def square_to_quad(q: np.ndarray) -> np.ndarray:
    """Homography taking the unit square (0,0),(1,0),(1,1),(0,1) to the quad
    q[..., 0..3, :], in closed form (Heckbert). Hypothesis search fits an
    exact four-point homography per candidate, and this is a few dozen
    elementwise operations per candidate where an SVD is a LAPACK call."""
    q = np.asarray(q, dtype=np.float64)
    x0, y0 = q[..., 0, 0], q[..., 0, 1]
    x1, y1 = q[..., 1, 0], q[..., 1, 1]
    x2, y2 = q[..., 2, 0], q[..., 2, 1]
    x3, y3 = q[..., 3, 0], q[..., 3, 1]
    sx = x0 - x1 + x2 - x3
    sy = y0 - y1 + y2 - y3
    dx1, dx2 = x1 - x2, x3 - x2
    dy1, dy2 = y1 - y2, y3 - y2
    det = dx1 * dy2 - dx2 * dy1
    affine = (np.abs(sx) < 1e-9) & (np.abs(sy) < 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(affine, 0.0, (sx * dy2 - dx2 * sy) / det)
        h = np.where(affine, 0.0, (dx1 * sy - sx * dy1) / det)
    a = x1 - x0 + g * x1
    b = x3 - x0 + h * x3
    c = x0
    d = y1 - y0 + g * y1
    e = y3 - y0 + h * y3
    f = y0
    one = np.ones_like(g)
    return np.stack(
        [np.stack([a, b, c], -1), np.stack([d, e, f], -1), np.stack([g, h, one], -1)], axis=-2
    )


def homography_from_quads(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Exact homography with dst ~ H src from four corresponding points, in
    the same cyclic order on both sides. Batched over leading dimensions."""
    Hs = square_to_quad(src)
    Hd = square_to_quad(dst)
    ok = np.isfinite(Hs).all(axis=(-1, -2)) & (np.abs(np.linalg.det(np.where(np.isfinite(Hs).all(axis=(-1, -2))[..., None, None], Hs, np.eye(3)))) > 1e-12)
    Hs = np.where(ok[..., None, None], Hs, np.eye(3))
    H = Hd @ np.linalg.inv(Hs)
    H = np.where(ok[..., None, None], H, np.nan)
    return H


def is_convex_quad(q: np.ndarray) -> bool:
    """True when the four points, in order, bound a convex quadrilateral."""
    q = np.asarray(q, dtype=np.float64)
    e = np.roll(q, -1, axis=0) - q
    cross = e[:, 0] * np.roll(e, -1, axis=0)[:, 1] - e[:, 1] * np.roll(e, -1, axis=0)[:, 0]
    return bool((cross > 0).all() or (cross < 0).all())
