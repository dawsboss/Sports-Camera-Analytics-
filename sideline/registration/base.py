"""The registration interface: one contract, two implementations.

S5 is the only stage that differs between a static camera and a follow-cam.
Both produce the same thing for every sampled frame: a mapping from image
pixels to surface metres, and a confidence score. Everything downstream
consumes `Registration` and never asks which implementation made it. That is
what makes the mode swap a configuration change rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

import numpy as np

from sideline.contracts import CaptureMode
from sideline.registration.geometry import apply_h


class PointMapping(Protocol):
    def to_pitch(self, pts: np.ndarray) -> np.ndarray: ...
    def to_image(self, pts: np.ndarray) -> np.ndarray: ...


@dataclass
class HomographyMapping:
    """A planar homography. `H_i2p` maps image pixels to surface metres."""

    H_i2p: np.ndarray

    def __post_init__(self) -> None:
        self.H_i2p = np.asarray(self.H_i2p, dtype=np.float64).reshape(3, 3)
        self._H_p2i = np.linalg.inv(self.H_i2p)

    @property
    def H_p2i(self) -> np.ndarray:
        return self._H_p2i

    def to_pitch(self, pts: np.ndarray) -> np.ndarray:
        return apply_h(self.H_i2p, pts)

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        return apply_h(self._H_p2i, pts)


@dataclass
class Registration:
    frame_index: int
    t_ms: int
    method: str
    confidence: float
    n_lines: int
    mapping: Optional[PointMapping]
    # A mapping can exist and still be rejected: the spec says frames below a
    # confidence floor are dropped, and the spike wants to draw them anyway to
    # see what went wrong. `registered` is what downstream stages check.
    accepted: bool = False
    reason: str = ""
    debug: dict = field(default_factory=dict)

    @property
    def registered(self) -> bool:
        return self.accepted and self.mapping is not None

    @property
    def H(self) -> Optional[np.ndarray]:
        """The image->pitch homography, or None when the mapping is not one."""
        if isinstance(self.mapping, HomographyMapping):
            return self.mapping.H_i2p
        return None

    def to_pitch(self, pts: np.ndarray) -> np.ndarray:
        if self.mapping is None:
            raise ValueError("frame is not registered")
        return self.mapping.to_pitch(np.asarray(pts, dtype=np.float64))

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        if self.mapping is None:
            raise ValueError("frame is not registered")
        return self.mapping.to_image(np.asarray(pts, dtype=np.float64))

    def as_row(self) -> dict:
        """One row of REGISTRATION_SCHEMA."""
        H = self.H
        return {
            "frame_index": int(self.frame_index),
            "t_ms": int(self.t_ms),
            "method": self.method,
            "confidence": float(self.confidence),
            "n_lines": int(self.n_lines),
            "h": [float(v) for v in H.ravel()] if (self.registered and H is not None) else None,
        }


class Registrar(Protocol):
    mode: CaptureMode
    method: str

    def register(self, frame: np.ndarray, frame_index: int = 0, t_ms: int = 0) -> Registration: ...
