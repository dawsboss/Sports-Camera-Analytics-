"""Static-camera registration: solved once per camera placement.

The operator clicks known surface landmarks on one frame. With a normal lens
that gives a planar homography; with a panoramic or wide lens, 15 to 25
correspondences fit a thin-plate spline instead, which absorbs lens
distortion without modelling it. Either way the mapping is reused for every
frame of the match, and `register()` is the same call the follow-cam
registrar answers.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from sideline.contracts import CaptureMode
from sideline.registration.base import HomographyMapping, Registration
from sideline.registration.geometry import apply_h, homography_dlt
from sideline.sports.base import SurfaceModel


class ThinPlateSpline:
    """2D -> 2D thin-plate spline, fit exactly (lam = 0) or smoothed."""

    def __init__(self, src: np.ndarray, dst: np.ndarray, lam: float = 0.0) -> None:
        src = np.asarray(src, dtype=np.float64)
        dst = np.asarray(dst, dtype=np.float64)
        n = len(src)
        if n < 4 or src.shape != dst.shape:
            raise ValueError("need at least four matching point pairs")
        # Normalise the source coordinates: a spline in raw pixels is badly
        # conditioned when the kernel argument reaches 10^6.
        self.offset = src.mean(axis=0)
        self.scale = float(np.sqrt(((src - self.offset) ** 2).sum(1)).mean()) or 1.0
        s = (src - self.offset) / self.scale
        K = self._kernel(s, s)
        P = np.concatenate([np.ones((n, 1)), s], axis=1)
        L = np.zeros((n + 3, n + 3))
        L[:n, :n] = K + lam * np.eye(n)
        L[:n, n:] = P
        L[n:, :n] = P.T
        Y = np.concatenate([dst, np.zeros((3, 2))], axis=0)
        sol = np.linalg.solve(L, Y)
        self.src_n = s
        self.W = sol[:n]
        self.A = sol[n:]

    @staticmethod
    def _kernel(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        r2 = ((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)
        with np.errstate(divide="ignore", invalid="ignore"):
            u = r2 * np.log(r2)
        u[r2 <= 0] = 0.0
        return u

    def __call__(self, pts: np.ndarray) -> np.ndarray:
        p = (np.asarray(pts, dtype=np.float64) - self.offset) / self.scale
        K = self._kernel(p, self.src_n)
        P = np.concatenate([np.ones((len(p), 1)), p], axis=1)
        return K @ self.W + P @ self.A


class SplineMapping:
    """A homography with a thin-plate spline correction in pixel space.

    The spline is not asked to model perspective, which it does badly and
    extrapolates worse; the homography carries that. What a lens adds on top
    is a smooth displacement of pixels, so that is what the spline learns:
    where each clicked pixel would have been with a perfect lens, given the
    homography, minus where it actually is."""

    def __init__(self, H_i2p: np.ndarray, image_pts: np.ndarray, pitch_pts: np.ndarray, lam: float) -> None:
        self.H_i2p = H_i2p
        self.H_p2i = np.linalg.inv(H_i2p)
        ideal = apply_h(self.H_p2i, pitch_pts)          # where a perfect lens would put each landmark
        self._undistort = ThinPlateSpline(image_pts, ideal - image_pts, lam)
        self._distort = ThinPlateSpline(ideal, image_pts - ideal, lam)

    def to_pitch(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        return apply_h(self.H_i2p, pts + self._undistort(pts))

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        ideal = apply_h(self.H_p2i, pts)
        return ideal + self._distort(ideal)


class StaticRegistrar:
    mode = CaptureMode.STATIC

    def __init__(
        self,
        surface: SurfaceModel,
        image_pts: np.ndarray,
        pitch_pts: np.ndarray,
        method: str = "auto",
        tps_lambda: float = 1e-3,
    ) -> None:
        image_pts = np.asarray(image_pts, dtype=np.float64)
        pitch_pts = np.asarray(pitch_pts, dtype=np.float64)
        if len(image_pts) < 4:
            raise ValueError("static registration needs at least four clicked landmarks")
        self.surface = surface
        self.image_pts, self.pitch_pts = image_pts, pitch_pts
        if method not in ("auto", "homography", "tps"):
            raise ValueError(f"unknown method {method!r}")
        use_tps = method == "tps" or (method == "auto" and len(image_pts) >= 15)
        self.H_i2p = homography_dlt(image_pts, pitch_pts)
        if use_tps:
            self.method = "static-tps"
            self.mapping = SplineMapping(self.H_i2p, image_pts, pitch_pts, tps_lambda)
            self.residual_m = self._loo_residual(tps_lambda)
        else:
            self.method = "static-homography"
            self.mapping = HomographyMapping(self.H_i2p)
            err = apply_h(self.H_i2p, image_pts) - pitch_pts
            self.residual_m = float(np.sqrt((err ** 2).sum(1).mean()))

    def _loo_residual(self, lam: float) -> float:
        """An exact spline has zero residual on its own points, which says
        nothing. Leave-one-out gives an honest number in metres."""
        errs = []
        n = len(self.image_pts)
        for i in range(n):
            keep = np.arange(n) != i
            if keep.sum() < 4:
                continue
            m = SplineMapping(homography_dlt(self.image_pts[keep], self.pitch_pts[keep]),
                              self.image_pts[keep], self.pitch_pts[keep], lam)
            errs.append(np.linalg.norm(m.to_pitch(self.image_pts[i : i + 1])[0] - self.pitch_pts[i]))
        return float(np.sqrt(np.mean(np.square(errs)))) if errs else float("nan")

    @property
    def confidence(self) -> float:
        # A metre of leave-one-out error is already poor for a fixed camera;
        # confidence falls off on that scale.
        return float(np.exp(-self.residual_m / 1.0))

    def register(self, frame: Optional[np.ndarray] = None, frame_index: int = 0, t_ms: int = 0) -> Registration:
        return Registration(
            frame_index=frame_index,
            t_ms=t_ms,
            method=self.method,
            confidence=self.confidence,
            n_lines=len(self.image_pts),
            mapping=self.mapping,
            accepted=True,
            debug={"residual_m": self.residual_m},
        )

    def to_dict(self) -> dict:
        """calibration.json: enough to rebuild the mapping without the frame."""
        return {
            "method": self.method,
            "image_pts": self.image_pts.tolist(),
            "pitch_pts": self.pitch_pts.tolist(),
            "h_i2p": self.H_i2p.ravel().tolist(),
            "residual_m": self.residual_m,
        }

    @classmethod
    def from_dict(cls, surface: SurfaceModel, d: dict) -> "StaticRegistrar":
        method = "tps" if d["method"] == "static-tps" else "homography"
        return cls(surface, np.array(d["image_pts"]), np.array(d["pitch_pts"]), method=method)
