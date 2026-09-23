"""Score a ball detector against tags, not against itself.

`ball_recall.py` counts a sample as found when *anything* clears the
confidence floor. On footage nobody tagged that is the only question
available, and it is the wrong one for a detector fine-tuned on ninety
boxes: a model that fires on a white sock scores as well as one that
finds the ball. On tagged frames the question can be asked properly, so
point this at a split `build_dataset.py` wrote — the held-out one — and it
measures the distance from the most confident detection to the tap.

    python spike/evals/ball_on_tags.py --data data/ours/ball --split val \
        --weights runs/detect/ball_ft/weights/best.pt

Distance, not IoU. The box round a tag is a fixed 22 px drawn about a
tap, so IoU would be measuring the tagger. Within `--radius` of the tap is
found; a confident detection further away is *wrong*, which is worse than
a miss because a tracker will follow it.

Tagged samples come in bursts 0.2 s apart, so the misses are also
reported as gaps, the number `docs/TRAINING.md` asks for: a tracker
bridges two samples and not fifteen.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# Samples further apart than this, in frames, are different bursts. The
# tagger steps 0.2 s, six frames at 29.97.
SAME_BURST = 8


def score(model, data: Path, split: str, imgsz: int, conf: float, accept: float,
          radius_1080: float, cls) -> list[dict]:
    rows = []
    for lf in sorted((data / "labels" / split).glob("*.txt")):
        img_path = next((data / "images" / split).glob(lf.stem + ".*"), None)
        if img_path is None:
            continue
        frame = cv2.imread(str(img_path))
        h, w = frame.shape[:2]
        radius = radius_1080 * h / 1080.0
        tag = lf.read_text().split()
        kw = {"imgsz": imgsz, "conf": conf, "verbose": False}
        if cls is not None:
            kw["classes"] = [cls]
        r = model.predict(frame, **kw)[0]
        c = r.boxes.conf.cpu().numpy()
        b = r.boxes.xyxy.cpu().numpy()
        keep = c >= accept
        c, b = c[keep], b[keep]
        centres = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1) if len(c) else np.zeros((0, 2))
        match, frame_no = lf.stem.rsplit("_", 1)
        row = {"stem": lf.stem, "match": match, "frame": int(frame_no), "visible": bool(tag), "n": int(len(c))}
        if tag:
            tx, ty = float(tag[1]) * w, float(tag[2]) * h
            if len(c):
                d = np.hypot(centres[:, 0] - tx, centres[:, 1] - ty)
                top = int(np.argmax(c))
                row["dist"] = float(d[top])
                row["conf"] = float(c[top])
                row["width"] = float(b[top, 2] - b[top, 0])
                if d[top] <= radius:
                    row["result"] = "found"
                elif d.min() <= radius:
                    row["result"] = "found, not top"
                else:
                    row["result"] = "wrong"
            else:
                row["result"] = "missed"
        else:
            row["result"] = "false alarm" if len(c) else "correctly empty"
        rows.append(row)
    return rows


def gaps(rows: list[dict]) -> list[int]:
    """Runs of visible-ball samples not found, within each burst."""
    out: list[int] = []
    rows = sorted((r for r in rows if r["visible"]), key=lambda r: (r["match"], r["frame"]))
    run, prev = 0, None
    for r in rows:
        if prev is not None and (r["match"] != prev["match"] or r["frame"] - prev["frame"] > SAME_BURST):
            if run:
                out.append(run)
            run = 0
        if r["result"] == "found":
            if run:
                out.append(run)
            run = 0
        else:
            run += 1
        prev = r
    if run:
        out.append(run)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True, help="a build_dataset.py output, e.g. data/ours/ball")
    ap.add_argument("--split", default="val")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--coco-ball", action="store_true", help="the weights are a COCO model; look for class 32")
    ap.add_argument("--imgsz", type=int, default=1920)
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--accept", type=float, default=0.25, help="as in ball_recall.py")
    ap.add_argument("--radius", type=float, default=20.0, help="px at 1080p from the tap that still counts")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    from ultralytics import YOLO

    rows = score(YOLO(args.weights), args.data, args.split, args.imgsz, args.conf, args.accept,
                 args.radius, 32 if args.coco_ball else None)
    if args.verbose:
        for r in rows:
            extra = f" {r['dist']:5.1f}px conf {r['conf']:.2f}" if "dist" in r else ""
            print(f"  {r['stem']}: {r['result']}{extra}")

    vis = [r for r in rows if r["visible"]]
    empty = [r for r in rows if not r["visible"]]
    count = lambda rs, k: sum(1 for r in rs if r["result"] == k)
    print(f"\n{args.data}/{args.split} with {args.weights}, accept {args.accept}, radius {args.radius:g}px")
    print(f"ball visible in {len(vis)} samples:")
    for k in ("found", "found, not top", "wrong", "missed"):
        n = count(vis, k)
        print(f"  {k:15s} {n:3d}  ({100 * n / max(len(vis), 1):.0f}%)")
    found = [r["dist"] for r in vis if r["result"] == "found"]
    if found:
        print(f"  distance to tap when found: median {np.median(found):.1f}px")
    if empty:
        print(f"ball not visible in {len(empty)}: {count(empty, 'false alarm')} false alarms")
    g = gaps(rows)
    if g:
        print(f"gaps between finds within bursts: {sorted(g, reverse=True)} (worst {max(g)})")
    else:
        print("no gaps: found in every visible sample")


if __name__ == "__main__":
    main()
