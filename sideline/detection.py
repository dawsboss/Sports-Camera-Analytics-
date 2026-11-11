"""Finding people and the object of play in a frame.

Nothing here knows the sport. A detector returns boxes labelled with the
entity class names the sport plugin declares, and S2 writes them. The one
real detector is an Ultralytics checkpoint (a COCO model out of the box; the
fine-tuned ball model when it is good enough) behind a protocol thin enough
that the tests drive the stage with a scripted detector and no model at all.
The measurements behind the defaults are in `docs/NEXT.md`: players are found
off the shelf at 1280 px, the ball needs the largest model at 1920 px and
still misses 27-57% of samples, so the ball's threshold is low and the
tracker, not the detector, is expected to bridge the gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

import numpy as np

from sideline.registration.lines import LineDetectionConfig, grass_mask


@dataclass(frozen=True)
class Detection:
    cls: str
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def foot(self) -> tuple[float, float]:
        """Where the thing touches the ground, as far as a box can say."""
        return ((self.x1 + self.x2) / 2, self.y2)


class Detector(Protocol):
    def detect(self, frame_bgr: np.ndarray) -> list[Detection]: ...
    def describe(self) -> dict: ...


# COCO's names for what the sport plugin calls a player and a ball. A COCO
# model cannot tell a keeper from a player or an official; those classes
# arrive with team assignment or a fine-tuned detector.
COCO_CLASSES: dict[str, str] = {"person": "player", "sports ball": "ball"}
DEFAULT_MIN_CONF: dict[str, float] = {"player": 0.3, "ball": 0.1}


class UltralyticsDetector:
    """One YOLO checkpoint. `classes` maps the model's class names to entity
    class names and drops everything else; `min_conf` is per entity class,
    because a ball at 0.15 is worth keeping and a person at 0.15 is a
    spectator's chair."""

    def __init__(self, weights: str = "yolo11x.pt", imgsz: int = 1920, conf: float = 0.05,
                 classes: Optional[dict[str, str]] = None, min_conf: Optional[dict[str, float]] = None,
                 device: Optional[str] = None, half: bool = False) -> None:
        from ultralytics import YOLO  # heavy, and only on the worker

        self.weights, self.imgsz, self.conf, self.device, self.half = weights, imgsz, conf, device, half
        self.classes = dict(classes or COCO_CLASSES)
        self.min_conf = dict(min_conf or DEFAULT_MIN_CONF)
        self.model = YOLO(weights)
        names = self.model.names
        self._keep = {int(i): self.classes[n] for i, n in names.items() if n in self.classes}
        if not self._keep:
            raise ValueError(f"{weights} has none of the classes {sorted(self.classes)}; its names are {list(names.values())[:10]}")

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        r = self.model.predict(frame_bgr, imgsz=self.imgsz, conf=self.conf, classes=sorted(self._keep),
                               verbose=False, device=self.device, half=self.half)[0]
        if r.boxes is None or len(r.boxes) == 0:
            return []
        xyxy = r.boxes.xyxy.cpu().numpy()
        cls = r.boxes.cls.cpu().numpy().astype(int)
        conf = r.boxes.conf.cpu().numpy()
        out = []
        for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf):
            name = self._keep.get(int(c))
            if name is None or p < self.min_conf.get(name, 0.0):
                continue
            out.append(Detection(name, float(x1), float(y1), float(x2), float(y2), float(p)))
        return out

    def describe(self) -> dict:
        return {"kind": "ultralytics", "weights": self.weights, "imgsz": self.imgsz, "conf": self.conf,
                "classes": self.classes, "min_conf": self.min_conf, "device": self.device}


class CompositeDetector:
    """Several detectors on the same frame: a people model and a ball model
    run at their own sizes, and the frame gets the union."""

    def __init__(self, parts: Sequence[Detector]) -> None:
        self.parts = list(parts)

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        out: list[Detection] = []
        for p in self.parts:
            out.extend(p.detect(frame_bgr))
        return out

    def describe(self) -> dict:
        return {"kind": "composite", "parts": [p.describe() for p in self.parts]}


class ScriptedDetector:
    """Boxes decided in advance, keyed by how many frames have been seen.
    This is how the stages are tested on a CPU with no model: the test
    writes the scene and the pipeline has to find what the scene says."""

    def __init__(self, script: Callable[[int], Sequence[Detection]] | dict[int, Sequence[Detection]]) -> None:
        self.script = script
        self.calls = 0

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        k = self.calls
        self.calls += 1
        dets = self.script(k) if callable(self.script) else self.script.get(k, ())
        return list(dets)

    def describe(self) -> dict:
        return {"kind": "scripted"}


def build_detector(spec: dict | list[dict] | None) -> Detector:
    """A detector from a stage config. One dict is one checkpoint; a list is
    a composite. No spec is the COCO default."""
    if spec is None:
        return UltralyticsDetector()
    if isinstance(spec, list):
        return CompositeDetector([build_detector(s) for s in spec])
    kind = spec.get("kind", "ultralytics")
    if kind != "ultralytics":
        raise ValueError(f"unknown detector kind {kind!r}")
    return UltralyticsDetector(**{k: v for k, v in spec.items() if k != "kind"})


# -- keeping to the pitch --------------------------------------------------

@dataclass
class GrassFilter:
    """Drop people whose feet are not on grass. The off-the-shelf detector
    finds 19 to 32 people a frame on the Veo footage and some are spectators
    and coaches on the touchline; on a school field the run-off is grass too,
    so a thrower standing behind the line keeps her feet on it and stays. A
    foot clipped by the bottom of the frame cannot be judged and stays."""

    min_frac: float = 0.3
    scale_px: int = 320            # the mask is computed on a frame this wide
    patch_frac: float = 0.08       # half-size of the patch under the foot, as a share of box height
    cfg: LineDetectionConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cfg is None:
            self.cfg = LineDetectionConfig()

    def mask(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        import cv2

        h, w = frame_bgr.shape[:2]
        s = self.scale_px / w
        small = cv2.resize(frame_bgr, (self.scale_px, max(1, int(round(h * s)))), interpolation=cv2.INTER_AREA)
        return (grass_mask(small, self.cfg) > 0), s

    def keep(self, det: Detection, mask: np.ndarray, scale: float, frame_h: int) -> bool:
        fx, fy = det.foot
        if fy >= frame_h - 2:
            return True
        r = max(2, int(round(det.height * self.patch_frac * scale)))
        cx, cy = int(round(fx * scale)), int(round(fy * scale))
        y0, y1 = max(0, cy - r), min(mask.shape[0], cy + r + 1)
        x0, x1 = max(0, cx - r), min(mask.shape[1], cx + r + 1)
        patch = mask[y0:y1, x0:x1]
        return patch.size > 0 and float(patch.mean()) >= self.min_frac
