"""WASB's released small-ball weights, cold, on our tags and on its own clip.

`docs/TRAINING.md` asked for this before the next tagging session: the
small-ball literature predicts a heatmap over a stack of consecutive
frames instead of regressing a box, and if a released model of that kind
already found our ball, the tagging target would shrink. WASB (MIT, BMVC
2023) publishes soccer weights trained on ISSIA: static 1080p cameras on a
Serie A pitch, frames shrunk to 512x288 so the ball is a few pixels, which
is the scale ours lands at too.

The model code is theirs and is not vendored; clone it and point at it:

    git clone https://github.com/nttcom/WASB-SBDT
    gdown 1pg0MpMtKZ6ziYEr4oyfKYPOO3hjLw94l -O wasb_soccer_best.pth.tar
    python spike/evals/wasb_ball.py --wasb-src WASB-SBDT/src \\
        --weights wasb_soccer_best.pth.tar \\
        tags --data data/ours/ball --split val --video 20260919-flight=flight.mp4

`issia --xml ID-5.xml --video ID-5.avi` runs the same wiring on WASB's own
test clip, which is what says a zero on ours is the model and not this
script. `--still` feeds the middle frame three times, taking motion away:
on a follow-cam everything moves, so that separates "the appearance does
not transfer" from "the camera pans".

Scored like `ball_on_tags.py`: the heatmap's peak, above WASB's own 0.5,
within 20 px of the tap (at 1080p). Also reported is the heatmap value at
the tap itself, which says whether the model saw anything there at all.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

IN_W, IN_H = 512, 288
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


class _Attr(dict):
    """Their HRNet reads its config with attribute access (OmegaConf)."""
    def __getattr__(self, k):
        return self[k]


def _attr(o):
    return _Attr({k: _attr(v) for k, v in o.items()}) if isinstance(o, dict) else o


class Wasb:
    def __init__(self, src: Path, weights: Path, device: str):
        import torch
        import yaml

        sys.path.insert(0, str(src))
        from models.hrnet import HRNet   # theirs

        self.torch = torch
        self.device = device
        self.model = HRNet(_attr(yaml.safe_load((src / "configs" / "model" / "wasb.yaml").read_text())))
        state = torch.load(weights, map_location="cpu", weights_only=False)["model_state_dict"]
        self.model.load_state_dict(state)
        self.model.eval().to(device)

    def heatmap(self, frames: list) -> np.ndarray:
        """Heatmap for the middle of three consecutive BGR frames, at 512x288."""
        x = np.concatenate([((cv2.cvtColor(cv2.resize(f, (IN_W, IN_H)), cv2.COLOR_BGR2RGB)
                              .astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1) for f in frames], 0)
        with self.torch.no_grad():
            out = self.model(self.torch.from_numpy(x)[None].to(self.device))[0]
        return self.torch.sigmoid(out)[0, 1].cpu().numpy()


def judge(hm: np.ndarray, x: float, y: float, w: int, h: int, radius_1080: float) -> tuple[bool, float]:
    """(peak above 0.5 and within the radius of (x, y), heatmap value at (x, y))."""
    sx, sy = IN_W / w, IN_H / h
    tx, ty = x * sx, y * sy
    iy, ix = np.unravel_index(int(np.argmax(hm)), hm.shape)
    r = radius_1080 * (h / 1080.0) * sx
    found = bool(hm[iy, ix] > 0.5 and np.hypot(ix - tx, iy - ty) <= r)
    at = float(hm[max(0, int(ty) - 3):int(ty) + 4, max(0, int(tx) - 3):int(tx) + 4].max())
    return found, at


def triple(cap, n: int, still: bool) -> list | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, n - 1)
    fs = [cap.read()[1] for _ in range(3)]
    if any(f is None for f in fs):
        return None
    return [fs[1]] * 3 if still else fs


def run_tags(wasb: Wasb, args) -> list[tuple[bool, float]]:
    videos = dict(v.split("=", 1) for v in args.video)
    caps = {m: cv2.VideoCapture(p) for m, p in videos.items()}
    out = []
    for lf in sorted((args.data / "labels" / args.split).glob("*.txt")):
        row = lf.read_text().split()
        match, n = lf.stem.rsplit("_", 1)
        if not row or match not in caps:
            continue
        fs = triple(caps[match], int(n), args.still)
        if fs is None:
            continue
        h, w = fs[0].shape[:2]
        out.append(judge(wasb.heatmap(fs), float(row[1]) * w, float(row[2]) * h, w, h, args.radius))
    return out


def run_issia(wasb: Wasb, args) -> list[tuple[bool, float]]:
    gt = {}
    for p in ET.parse(args.xml).getroot().iter("points"):
        used = [a.text for a in p if a.attrib.get("name") == "used_in_game"]
        if p.attrib["outside"] == "0" and p.attrib["occluded"] == "0" and used == ["1"]:
            gt[int(p.attrib["frame"])] = tuple(map(float, p.attrib["points"].split(",")))
    cap = cv2.VideoCapture(args.video)
    out = []
    for n in sorted(gt)[::args.every]:
        fs = triple(cap, n, args.still)
        if fs is None:
            continue
        h, w = fs[0].shape[:2]
        out.append(judge(wasb.heatmap(fs), *gt[n], w, h, args.radius))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wasb-src", type=Path, required=True, help="the src/ directory of a WASB-SBDT clone")
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "cpu")
    ap.add_argument("--still", action="store_true", help="the middle frame three times: no motion")
    ap.add_argument("--radius", type=float, default=20.0, help="px at 1080p from the truth that still counts")
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("tags", help="a build_dataset.py split, frames re-read from the video")
    t.add_argument("--data", type=Path, required=True)
    t.add_argument("--split", default="val")
    t.add_argument("--video", action="append", required=True, metavar="MATCHID=PATH")
    i = sub.add_parser("issia", help="WASB's own test clip, to check the wiring")
    i.add_argument("--xml", type=Path, required=True)
    i.add_argument("--video", required=True)
    i.add_argument("--every", type=int, default=25)
    args = ap.parse_args()

    wasb = Wasb(args.wasb_src, args.weights, args.device)
    rows = run_tags(wasb, args) if args.mode == "tags" else run_issia(wasb, args)
    found = sum(f for f, _ in rows)
    at = np.median([a for _, a in rows]) if rows else float("nan")
    print(f"{args.mode}{' (still)' if args.still else ''}: ball found in {found}/{len(rows)}; "
          f"heatmap at the ball, median {at:.3f} (0.5 is WASB's own threshold)")


if __name__ == "__main__":
    main()
