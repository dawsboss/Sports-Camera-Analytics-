"""Follow-cam registration: solved again on every sampled frame.

A Veo export is a virtual camera panning and zooming across a panorama, so
nothing about one frame's mapping carries to the next. Each frame is
registered from the painted lines it shows:

1. Find the lines (`lines.py`).
2. Every way of picking two pairs of them and naming each pair as two
   surface lines of one family is a hypothesis. Four line correspondences
   fix a homography exactly, the surface has few enough lines that trying
   every naming is cheap, and a closed-form four-point solve keeps tens of
   thousands of candidates under a second.
3. Throw out hypotheses no camera above the pitch could have produced,
   score the rest by how well the projected surface lands on paint and how
   well the paint lands on the surface, and refine the best few against
   every line they explain. The refined fit that scores highest wins.

Frames that do not show four usable lines (grass only, a lone touchline,
the centre circle with one line through it) come back unregistered with the
reason, so the spike can count how often that happens instead of guessing.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from sideline.contracts import CaptureMode
from sideline.registration.base import HomographyMapping, Registration
from sideline.registration.geometry import (
    apply_h,
    homogeneous,
    homography_dlt,
    intersect,
    is_convex_quad,
    point_segment_distance,
    sample_circle,
    sample_segment,
    square_to_quad,
)
from sideline.registration.lines import DetectedLine, LineDetection, LineDetectionConfig, detect_lines
from sideline.sports.base import SurfaceModel


@dataclass
class FollowCamConfig:
    # On synthetic frames wrong-but-plausible fits score 0.86-0.90 and right
    # ones 0.91+; the floor is a knob the spike report exists to set.
    min_confidence: float = 0.8
    max_lines: int = 6               # longest lines that may take part in a hypothesis
    refine_top: int = 8              # candidates refined before the winner is picked
    # Which touchline the camera stands on, as the sign of its y coordinate.
    # A Veo never moves, and the pitch is symmetric under a half turn, so
    # without this the right-hand box seen from here is indistinguishable
    # from the left-hand box seen from the far side.
    camera_side: int = -1
    tau_px: float = 8.0              # how far a projected line may sit from paint and still count
    tau_m: float = 1.5               # how far paint may sit from a surface line, in metres
    model_step_m: float = 1.0
    pitch_grid_m: float = 3.0
    detected_step_px: float = 12.0
    max_detected_samples: int = 300
    min_model_points: int = 30
    precision_weight: float = 0.5
    px_per_m: tuple[float, float] = (2.0, 400.0)
    pitch_margin_m: float = 12.0     # grass beyond the touchline is normal; a stand is not
    pitch_on_grass: float = 0.85     # share of the projected pitch, where in frame, that must be grass
    hull_erode_frac: float = 0.02    # of the frame height; the grass edge near the horizon is not to be trusted
    refine_iters: int = 5
    match_max_m: tuple[float, float] = (8.0, 2.0)   # first round, later rounds
    chunk: int = 256
    detection: LineDetectionConfig = field(default_factory=LineDetectionConfig)


@dataclass
class _FrameContext:
    size: tuple[int, int]
    grass: np.ndarray         # uint8 mask of the playing surface
    dt: np.ndarray            # distance from every pixel to the nearest painted pixel
    hull: np.ndarray          # (K, 2) convex hull of the eroded grass
    centroid: np.ndarray      # (2,)
    det_samples: np.ndarray   # (S, 2) image points along the detected lines


class FollowCamRegistrar:
    mode = CaptureMode.FOLLOWCAM
    method = "followcam-lines"

    def __init__(self, surface: SurfaceModel, config: Optional[FollowCamConfig] = None) -> None:
        self.surface = surface
        self.cfg = config or FollowCamConfig()
        # When set, `register()` keeps every scored hypothesis of the last
        # frame in `last_candidates` (H_p2i, score) so a failure can be
        # examined against ground truth instead of guessed at.
        self.keep_candidates = False
        self.last_candidates: Optional[tuple[np.ndarray, np.ndarray]] = None

        fam = [l for l in surface.lines() if l.family in ("x", "y")]
        # Several surface segments share one infinite line (both penalty-box
        # top edges sit on y = +20.16). Hypotheses are over the infinite lines;
        # scoring and refinement use the segments.
        self._coords = {f: np.array(sorted({l.coordinate for l in fam if l.family == f})) for f in ("x", "y")}
        self._segs = np.array([[l.p0[0], l.p0[1], l.p1[0], l.p1[1]] for l in fam], dtype=np.float64)
        self._circles = [(np.array(c.centre, dtype=np.float64), float(c.radius)) for c in surface.circles()]
        pts = [sample_segment(s[:2], s[2:], self.cfg.model_step_m) for s in self._segs]
        pts += [sample_circle(c, r, self.cfg.model_step_m) for c, r in self._circles]
        self._model_pts = np.concatenate(pts, axis=0)
        gx = np.arange(-surface.length / 2, surface.length / 2 + 1e-9, self.cfg.pitch_grid_m)
        gy = np.arange(-surface.width / 2, surface.width / 2 + 1e-9, self.cfg.pitch_grid_m)
        self._pitch_grid = np.stack(np.meshgrid(gx, gy), -1).reshape(-1, 2)
        # Every rectangle two lines of one family and two of the other can
        # bound, as the inverse of the unit-square homography onto it. A
        # hypothesis is then one matrix product: image quad times this.
        self._rect_inv: dict[str, np.ndarray] = {}
        for fam_a in ("x", "y"):
            fam_b = "y" if fam_a == "x" else "x"
            pa = np.array(list(itertools.permutations(self._coords[fam_a], 2)))
            pb = np.array(list(itertools.permutations(self._coords[fam_b], 2)))
            PA, PB = len(pa), len(pb)
            A1 = np.broadcast_to(pa[:, None, 0], (PA, PB)); A2 = np.broadcast_to(pa[:, None, 1], (PA, PB))
            B1 = np.broadcast_to(pb[None, :, 0], (PA, PB)); B2 = np.broadcast_to(pb[None, :, 1], (PA, PB))
            # Corners in the cyclic order (a1,b1), (a1,b2), (a2,b2), (a2,b1). An
            # "x" line has constant y, so where it meets a "y" line (constant
            # x) is (x of the y-line, y of the x-line).
            if fam_a == "x":
                rect = [np.stack([B1, A1], -1), np.stack([B2, A1], -1), np.stack([B2, A2], -1), np.stack([B1, A2], -1)]
            else:
                rect = [np.stack([A1, B1], -1), np.stack([A1, B2], -1), np.stack([A2, B2], -1), np.stack([A2, B1], -1)]
            rect = np.stack(rect, axis=2).reshape(-1, 4, 2)
            self._rect_inv[fam_a] = np.linalg.inv(square_to_quad(rect))

    # -- public ------------------------------------------------------------

    def register(self, frame: np.ndarray, frame_index: int = 0, t_ms: int = 0) -> Registration:
        cfg = self.cfg
        det = detect_lines(frame, cfg.detection)

        def fail(reason: str, **dbg) -> Registration:
            return Registration(frame_index, t_ms, self.method, 0.0, 0, None,
                                accepted=False, reason=reason, debug={"lines": len(det.lines), **dbg})

        if len(det.lines) < 4:
            return fail(f"only {len(det.lines)} lines found")
        ctx = self._context(frame.shape[1], frame.shape[0], det)
        if ctx is None:
            return fail("no grass found")

        candidates, n_hyp = self._search(det.lines[: cfg.max_lines], ctx)
        if not candidates:
            if n_hyp == 0:
                return fail("no four lines meet in a quadrilateral", hypotheses=0)
            return fail("no hypothesis survived the sanity checks", hypotheses=n_hyp)

        best_H, best_score, best_n = None, -1.0, 0
        for H0, s0 in candidates:
            H1, n1 = self._refine(H0, det.lines)
            s1 = float(self._score(H1[None], ctx)[0])
            # Refinement that leaves the plausible set or lowers the score is
            # a wrong match pulling the fit away; keep the unrefined one then.
            if not (np.isfinite(s1) and s1 >= s0 and self._sane(H1[None], ctx)[0]):
                H1, s1, n1 = H0, s0, 4
            if s1 > best_score:
                best_H, best_score, best_n = H1, s1, n1
        assert best_H is not None
        accepted = best_score >= cfg.min_confidence
        return Registration(
            frame_index, t_ms, self.method, best_score, best_n, HomographyMapping(np.linalg.inv(best_H)),
            accepted=accepted,
            reason="" if accepted else f"confidence {best_score:.2f} below floor {cfg.min_confidence}",
            debug={"lines": len(det.lines), "hypotheses": n_hyp, "initial_score": candidates[0][1]},
        )

    def detect(self, frame: np.ndarray) -> LineDetection:
        return detect_lines(frame, self.cfg.detection)

    # -- per-frame context ---------------------------------------------------

    def _context(self, W: int, Hh: int, det: LineDetection) -> Optional[_FrameContext]:
        cfg = self.cfg
        e = max(3, int(cfg.hull_erode_frac * Hh))
        eroded = cv2.erode(det.grass, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (e, e)))
        ys, xs = np.nonzero(eroded)
        if len(xs) < 100:
            return None
        pts = np.stack([xs, ys], axis=1).astype(np.int32)
        if len(pts) > 20000:
            pts = pts[np.random.default_rng(0).choice(len(pts), 20000, replace=False)]
        hull = cv2.convexHull(pts).reshape(-1, 2).astype(np.float64)
        centroid = pts.mean(axis=0).astype(np.float64)
        dt = cv2.distanceTransform((det.line_pixels == 0).astype(np.uint8), cv2.DIST_L2, 3)
        # A merged line is as long as its farthest fragments but only as
        # real as its painted support, so it gets samples in proportion to
        # the paint, not the span. Otherwise a few stray fragments that
        # happen to be collinear outvote a genuine line.
        samples = []
        for l in det.lines:
            n = max(2, int(l.support / cfg.detected_step_px))
            t = np.linspace(0, 1, n)
            samples.append(l.p0[None, :] + t[:, None] * (l.p1 - l.p0)[None, :])
        samples = np.concatenate(samples)
        if len(samples) > cfg.max_detected_samples:
            samples = samples[np.linspace(0, len(samples) - 1, cfg.max_detected_samples).astype(int)]
        return _FrameContext((W, Hh), det.grass, dt, hull, centroid, samples)

    # -- hypothesis search ---------------------------------------------------

    def _search(self, lines: list[DetectedLine], ctx: _FrameContext) -> tuple[list[tuple[np.ndarray, float]], int]:
        """Every way of picking two pairs of detected lines and naming each
        pair as two surface lines of one family. Returns the best few
        surface->image homographies with their scores, best first, and how
        many hypotheses were tried. Which detected lines are parallel on the
        surface is not decided in advance: under strong perspective the
        touchline and the goal line can look alike, so the score decides."""
        cfg = self.cfg
        far = 4.0 * max(ctx.size)
        quads = []
        idx = range(len(lines))
        for pa in itertools.combinations(idx, 2):
            rest = [i for i in idx if i not in pa]
            for pb in itertools.combinations(rest, 2):
                if pb < pa:
                    continue  # {pa, pb} and {pb, pa} are the same choice; the family loop covers both roles
                a1, a2 = lines[pa[0]].abc, lines[pa[1]].abc
                b1, b2 = lines[pb[0]].abc, lines[pb[1]].abc
                q = [intersect(a1, b1), intersect(a1, b2), intersect(a2, b2), intersect(a2, b1)]
                if any(p is None for p in q):
                    continue
                q = np.array(q)
                # The four corners of a rectangle on the surface. Two lines
                # from the same family meet only near their vanishing point,
                # which is far away and makes a quad that folds over itself.
                if np.abs(q - np.array(ctx.size) / 2).max() > far or not is_convex_quad(q):
                    continue
                quads.append(q)
        if not quads:
            return [], 0
        H_img = square_to_quad(np.array(quads))                      # (Q, 3, 3)
        blocks = []
        for fam_a in ("x", "y"):
            rect_inv = self._rect_inv[fam_a]                          # (R, 3, 3)
            blocks.append((H_img[:, None, :, :] @ rect_inv[None, :, :, :]).reshape(-1, 3, 3))
        H = np.concatenate(blocks)
        n = len(H)
        ok = self._sane(H, ctx)
        if not ok.any():
            return [], n
        H = H[ok]
        scores = self._score(H, ctx)
        if self.keep_candidates:
            self.last_candidates = (H, scores)
        order = [int(i) for i in np.argsort(-np.nan_to_num(scores, nan=-1.0))[: cfg.refine_top]]
        return [(H[i], float(scores[i])) for i in order if np.isfinite(scores[i])], n

    def _sane(self, H: np.ndarray, ctx: _FrameContext) -> np.ndarray:
        """Which surface->image homographies a camera above the pitch could
        have produced, judged on the grass the frame actually shows."""
        cfg = self.cfg
        L, W = self.surface.length / 2 + cfg.pitch_margin_m, self.surface.width / 2 + cfg.pitch_margin_m
        ok = np.isfinite(H).all(axis=(1, 2))
        safe = np.where(ok[:, None, None], H, np.eye(3))
        det = np.linalg.det(safe)
        ok &= np.abs(det) > 1e-12
        safe = np.where(ok[:, None, None], safe, np.eye(3))
        Hi = np.linalg.inv(safe)
        with np.errstate(divide="ignore", invalid="ignore"):
            # The visible grass must all be in front of the camera: the
            # horizon sits above it, so w keeps one sign across the hull.
            ph = np.einsum("nij,kj->nki", Hi, homogeneous(ctx.hull))
            w = ph[..., 2]
            ok &= (w > 0).all(axis=1) | (w < 0).all(axis=1)
            # At the middle of the grass: on the pitch, mirrored the way a
            # camera above a right-handed surface mirrors into y-down pixels,
            # and at a plausible scale.
            c = ctx.centroid
            probe = homogeneous(np.array([c, c + [1.0, 0.0], c + [0.0, 1.0]]))
            pp = np.einsum("nij,kj->nki", Hi, probe)
            wp = pp[..., 2]
            ok &= (np.abs(wp) > 1e-12).all(axis=1)
            q = pp[..., :2] / wp[..., None]
        cen = q[:, 0]
        ok &= (np.abs(cen[:, 0]) <= L) & (np.abs(cen[:, 1]) <= W)
        jx, jy = q[:, 1] - q[:, 0], q[:, 2] - q[:, 0]
        detj = jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]
        ok &= detj < 0
        # Down the image is toward the camera, so toward its own touchline.
        ok &= jy[:, 1] * cfg.camera_side > 0
        ppm = 1.0 / np.sqrt(np.abs(detj) + 1e-18)
        ok &= (ppm >= cfg.px_per_m[0]) & (ppm <= cfg.px_per_m[1])
        # Wherever the pitch itself lands in the frame, there must be grass.
        # This is what rejects a naming that fits the lines it used but puts
        # the far half of the pitch in the stands.
        idx = np.flatnonzero(ok)
        for s in range(0, len(idx), cfg.chunk):
            sel = idx[s : s + cfg.chunk]
            ok[sel] &= self._pitch_on_grass(H[sel], ctx) >= cfg.pitch_on_grass
        return ok

    def _pitch_on_grass(self, H: np.ndarray, ctx: _FrameContext) -> np.ndarray:
        W, Hh = ctx.size
        p = np.einsum("nij,kj->nki", H, homogeneous(self._pitch_grid))
        w = p[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            xy = p[..., :2] / w[..., None]
        inview = (w > 1e-9) & np.isfinite(xy).all(axis=-1)
        inview &= (xy[..., 0] >= 0) & (xy[..., 0] < W) & (xy[..., 1] >= 0) & (xy[..., 1] < Hh)
        xi = np.clip(np.round(np.nan_to_num(xy[..., 0])).astype(int), 0, W - 1)
        yi = np.clip(np.round(np.nan_to_num(xy[..., 1])).astype(int), 0, Hh - 1)
        on = (ctx.grass[yi, xi] > 0) & inview
        n = inview.sum(axis=1)
        return np.where(n > 0, on.sum(axis=1) / np.maximum(n, 1), 0.0)

    # -- scoring -------------------------------------------------------------

    def _score(self, H: np.ndarray, ctx: _FrameContext) -> np.ndarray:
        cfg = self.cfg
        out = np.full(len(H), np.nan)
        ok = np.isfinite(H).all(axis=(1, 2))
        ok &= np.abs(np.linalg.det(np.where(ok[:, None, None], H, np.eye(3)))) > 1e-12
        idx = np.flatnonzero(ok)
        for s in range(0, len(idx), cfg.chunk):
            sel = idx[s : s + cfg.chunk]
            p = self._precision(H[sel], ctx)
            r = self._recall(H[sel], ctx)
            out[sel] = cfg.precision_weight * p + (1 - cfg.precision_weight) * r
        return out

    def _precision(self, H: np.ndarray, ctx: _FrameContext) -> np.ndarray:
        """Does the projected surface land on paint? Surface points that fall
        in the image are looked up in the distance transform of the painted
        pixels; a line predicted where nothing is painted costs."""
        cfg = self.cfg
        W, Hh = ctx.size
        p = np.einsum("nij,kj->nki", H, homogeneous(self._model_pts))
        w = p[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            xy = p[..., :2] / w[..., None]
        ok = (w > 1e-9) & np.isfinite(xy).all(axis=-1)
        ok &= (xy[..., 0] >= 0) & (xy[..., 0] < W) & (xy[..., 1] >= 0) & (xy[..., 1] < Hh)
        xi = np.clip(np.round(np.nan_to_num(xy[..., 0])).astype(int), 0, W - 1)
        yi = np.clip(np.round(np.nan_to_num(xy[..., 1])).astype(int), 0, Hh - 1)
        g = np.exp(-((ctx.dt[yi, xi] / cfg.tau_px) ** 2)) * ok
        return g.sum(axis=1) / np.maximum(ok.sum(axis=1), cfg.min_model_points)

    def _recall(self, H: np.ndarray, ctx: _FrameContext) -> np.ndarray:
        """Does the paint land on the surface? Points along the detected lines
        are mapped to metres and measured against the nearest surface segment
        or circle. Segments, not infinite lines: a midfield line must not be
        explained by the extension of a penalty-box edge."""
        cfg = self.cfg
        Hi = np.linalg.inv(H)
        p = np.einsum("nij,kj->nki", Hi, homogeneous(ctx.det_samples))
        w = p[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            xy = p[..., :2] / w[..., None]
        p0 = self._segs[:, :2]
        d = self._segs[:, 2:] - p0
        len2 = (d ** 2).sum(axis=1)
        rel = xy[:, :, None, :] - p0[None, None, :, :]
        t = np.clip((rel * d).sum(-1) / len2, 0.0, 1.0)
        proj = p0 + t[..., None] * d
        dmin = np.linalg.norm(xy[:, :, None, :] - proj, axis=-1).min(axis=-1)
        for c, r in self._circles:
            dmin = np.minimum(dmin, np.abs(np.linalg.norm(xy - c, axis=-1) - r))
        dmin = np.where(np.isfinite(dmin), dmin, 1e6)
        return np.exp(-((dmin / cfg.tau_m) ** 2)).mean(axis=1)

    # -- refinement ----------------------------------------------------------

    def _refine(self, H_p2i: np.ndarray, lines: list[DetectedLine]) -> tuple[np.ndarray, int]:
        """Pull the fit onto every detected line it explains. Each line's
        samples are mapped to metres, matched to the nearest surface segment,
        and their feet on that segment's line become the targets of a fresh
        fit; a few rounds of this is a point-to-line ICP. The match radius
        starts wide, because an exact fit through four lines near the
        horizon can be metres off elsewhere, and tightens once it has moved."""
        cfg = self.cfg
        n_matched = 0
        for it in range(cfg.refine_iters):
            radius = cfg.match_max_m[0] if it == 0 else cfg.match_max_m[1]
            if not np.isfinite(H_p2i).all() or abs(np.linalg.det(H_p2i)) < 1e-12:
                break
            Hi = np.linalg.inv(H_p2i)
            img_pts, pitch_pts, matched = [], [], 0
            for line in lines:
                s = line.samples(8.0)
                p = apply_h(Hi, s)
                good = np.isfinite(p).all(axis=1)
                if good.sum() < 2:
                    continue
                s, p = s[good], p[good]
                best: Optional[tuple[float, np.ndarray]] = None
                for seg in self._segs:
                    dist, foot = point_segment_distance(p, seg[:2], seg[2:])
                    m = float(dist.mean())
                    if best is None or m < best[0]:
                        best = (m, foot)
                if best is not None and best[0] <= radius:
                    img_pts.append(s)
                    pitch_pts.append(best[1])
                    matched += 1
            if matched < 4:
                break
            n_matched = matched
            try:
                H_new = homography_dlt(np.concatenate(pitch_pts), np.concatenate(img_pts))
            except (ValueError, np.linalg.LinAlgError):
                break
            if not np.isfinite(H_new).all() or abs(np.linalg.det(H_new)) < 1e-12:
                break
            H_p2i = H_new
        return H_p2i, n_matched
