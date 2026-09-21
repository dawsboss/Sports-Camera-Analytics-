"""Run SoccerNet's pretrained line-segmentation network on real frames.

The classical line finder (colour mask, top-hat, Hough) has been pushed
against three real datasets and every scene brought a new confuser it
had no way to reject: advertising boards, a road with parked cars, a
crowd in folding chairs, the goal net, the Veo watermark, white kits. A
network trained to label pixels *as* "penalty box front" or "touchline"
learns what those are not. SoccerNet's calibration baseline is such a
network with published weights (DeepLabV3-ResNet50, 28 line classes at
640x360), so this is the cheapest possible test of whether a learned
detector sees the lines on a worn youth pitch that the classical one
cannot. It writes a coloured overlay per frame and prints which line
classes it found. Nothing here trains anything.

    python spike/evals/sn_baseline.py --weights baseline.pt --resources sn-calibration/resources \
        --video match.mp4 --frames 0 26486 53480 --out out/
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision.models.segmentation import deeplabv3_resnet50

# Class k+1 of the network is LINE_CLASSES[k]; 0 is background. Order from
# sn-calibration/src/soccerpitch.py.
LINE_CLASSES = [
    "Big rect. left bottom", "Big rect. left main", "Big rect. left top",
    "Big rect. right bottom", "Big rect. right main", "Big rect. right top",
    "Circle central", "Circle left", "Circle right",
    "Goal left crossbar", "Goal left post left ", "Goal left post right",
    "Goal right crossbar", "Goal right post left", "Goal right post right",
    "Goal unknown", "Line unknown", "Middle line",
    "Side line bottom", "Side line left", "Side line right", "Side line top",
    "Small rect. left bottom", "Small rect. left main", "Small rect. left top",
    "Small rect. right bottom", "Small rect. right main", "Small rect. right top",
]


def load(weights: Path, resources: Path, num_classes: int = 29):
    model = nn.DataParallel(deeplabv3_resnet50(weights=None, weights_backbone=None, num_classes=num_classes))
    # The baseline's own loader sets these before loading, and epsilon is
    # not part of the saved weights. With torchvision's default 1e-5 the
    # network loads without complaint and predicts background everywhere.
    for m in model.modules():
        if isinstance(m, nn.BatchNorm2d):
            m.eps, m.momentum = 1e-3, 0.1
    ck = torch.load(str(weights), map_location="cpu", weights_only=False)
    state = ck["model"] if isinstance(ck, dict) and "model" in ck else ck
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        print(f"load_state_dict: {len(missing)} missing, {len(unexpected)} unexpected keys (aux classifier is expected here)")
    model.eval()
    return model, np.load(str(resources / "mean.npy")), np.load(str(resources / "std.npy"))


def segment(model, mean, std, bgr: np.ndarray, width: int = 640, height: int = 360) -> np.ndarray:
    img = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_LINEAR)
    img = np.asarray(img, np.float32) / 255.0
    img = (img - mean) / std
    x = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0).float()
    with torch.no_grad():
        out = model(x)["out"][0].numpy()
    return np.argmax(out, axis=0).astype(np.uint8)


def palette(n: int = 29) -> np.ndarray:
    rng = np.random.default_rng(3)
    p = rng.integers(60, 255, size=(n, 3)).astype(np.uint8)
    p[0] = 0
    return p


def overlay(bgr: np.ndarray, mask: np.ndarray, pal: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    m = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    colour = pal[m]
    vis = bgr.copy()
    on = m > 0
    vis[on] = (0.35 * vis[on] + 0.65 * colour[on]).astype(np.uint8)
    return vis


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--resources", type=Path, required=True)
    ap.add_argument("--video", type=Path, action="append", default=[])
    ap.add_argument("--frames", type=int, nargs="*", default=[])
    ap.add_argument("--image", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    torch.set_num_threads(args.threads)
    args.out.mkdir(parents=True, exist_ok=True)
    model, mean, std = load(args.weights, args.resources)
    pal = palette()

    jobs: list[tuple[str, np.ndarray]] = []
    for video in args.video:
        cap = cv2.VideoCapture(str(video))
        for idx in args.frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                jobs.append((f"{video.parent.name}_{idx:07d}", frame))
        cap.release()
    for image in args.image:
        jobs.append((image.stem, cv2.imread(str(image))))

    for name, frame in jobs:
        t0 = time.perf_counter()
        mask = segment(model, mean, std, frame)
        dt = time.perf_counter() - t0
        counts = np.bincount(mask.ravel(), minlength=29)
        found = [(LINE_CLASSES[k - 1].strip(), int(counts[k])) for k in range(1, 29) if counts[k] >= 40]
        found.sort(key=lambda kv: -kv[1])
        print(f"{name}: {dt:.1f}s  " + (", ".join(f"{n} ({c}px)" for n, c in found) or "nothing"))
        cv2.imwrite(str(args.out / f"{name}.jpg"), overlay(frame, mask, pal), [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    main()
