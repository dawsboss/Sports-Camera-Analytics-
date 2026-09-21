"""How often is the ball found, over frames that are next to each other?

The first ball measurement here sampled frames minutes apart and reported
"1 of 8". That number cannot answer the question that matters, which is
whether tracking can carry the ball across the frames where detection
misses. Only consecutive samples answer that, so this runs bursts at the
pipeline's own 5 fps and reports the gaps between hits.

Read the gaps, not the rate. A ball missing for one or two samples is
bridged by any tracker. A ball missing for fifteen samples is three
seconds, in which it can cross half a pitch, and nothing may be drawn
across that.

    python spike/evals/ball_recall.py --video match.mp4 --weights yolo11x.pt \
        --bursts 4 --burst 40

`--weights` takes any Ultralytics model. With a COCO model pass
`--coco-ball` so it looks for class 32 ("sports ball"); a model fine-tuned
on this footage has ball as its only class and needs no flag.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def burst(model, cap, start_frame: int, n: int, step: int, imgsz: int, conf: float, cls):
    """Returns one (confidence, x, y, width) per sample, zeros where nothing."""
    out = []
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame + k * step)
        ok, frame = cap.read()
        if not ok:
            break
        kw = {"imgsz": imgsz, "conf": conf, "verbose": False}
        if cls is not None:
            kw["classes"] = [cls]
        r = model.predict(frame, **kw)[0]
        c = r.boxes.conf.cpu().numpy()
        b = r.boxes.xyxy.cpu().numpy()
        if len(c):
            i = int(np.argmax(c))
            out.append((float(c[i]), float((b[i, 0] + b[i, 2]) / 2), float((b[i, 1] + b[i, 3]) / 2), float(b[i, 2] - b[i, 0])))
        else:
            out.append((0.0, 0.0, 0.0, 0.0))
    return out


def gaps_of(samples, floor: float) -> list[int]:
    """Lengths of the runs with no detection at or above `floor`."""
    out, run = [], 0
    for s in samples:
        if s[0] >= floor:
            if run:
                out.append(run)
            run = 0
        else:
            run += 1
    if run:
        out.append(run)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--weights", default="yolo11x.pt")
    ap.add_argument("--coco-ball", action="store_true", help="the weights are a COCO model; look for class 32")
    ap.add_argument("--bursts", type=int, default=4)
    ap.add_argument("--burst", type=int, default=40)
    ap.add_argument("--sample-fps", type=float, default=5.0)
    ap.add_argument("--imgsz", type=int, default=1920)
    ap.add_argument("--conf", type=float, default=0.05, help="detector floor; --accept is the one that counts")
    ap.add_argument("--accept", type=float, default=0.25, help="confidence at which a detection is believed")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(round(fps / args.sample_fps)))
    span = step * args.burst
    rng = np.random.default_rng(args.seed)
    starts = rng.integers(int(fps * 60), max(int(fps * 60) + 1, total - span - int(fps * 60)), size=args.bursts)

    all_gaps: list[int] = []
    hits = seen = 0
    widths: list[float] = []
    for s in starts:
        samples = burst(model, cap, int(s), args.burst, step, args.imgsz, args.conf, 32 if args.coco_ball else None)
        g = gaps_of(samples, args.accept)
        h = sum(1 for x in samples if x[0] >= args.accept)
        widths += [x[3] for x in samples if x[0] >= args.accept]
        all_gaps += g
        hits += h
        seen += len(samples)
        # Flushed: a full run is twenty minutes on a CPU, and a progress line
        # that only appears at the end is not a progress line.
        print(f"  burst at {int(s)/fps/60:5.1f} min: {h:2d}/{len(samples)} found, gaps {g}", flush=True)
    cap.release()

    print(f"\n{args.video.name} with {args.weights}")
    print(f"found at conf>={args.accept}: {hits}/{seen} samples ({100*hits/max(seen,1):.0f}%)")
    if all_gaps:
        print(f"gap between finds: median {np.median(all_gaps):.0f}, worst {max(all_gaps)} samples "
              f"({max(all_gaps)/args.sample_fps:.1f}s at {args.sample_fps:g} fps)")
    if widths:
        print(f"detected ball width: median {np.median(widths):.0f}px, range {min(widths):.0f}-{max(widths):.0f}px")
    print("\nA gap of 1-2 samples is bridged by a tracker. A gap past about 5 is not.")


if __name__ == "__main__":
    main()
