"""Which frame was on screen when each ball tag was placed?

A tag is a tap on a paused phone video, keyed by a frame number the page
computed from `currentTime`. If that computation and the frame the phone
displayed disagree, the box lands beside the ball: invisible on a slow
passage, 10-18 px out of a 22 px box on a fast zoomed pan. That happened
(`build_dataset.py:tagged_frame`), and this is how it was measured, so it
can be re-run on every new export and on every phone.

For each visible ball tag it runs a COCO detector on a crop around the
tap in the frames from -6 to +3 around the tag's key, and reports the
offset whose detection lands closest to the tap, next to the offset that
`floor(t * fps)` predicts. Only tags where the ball moves enough between
frames to tell them apart count towards the verdict.

    python spike/labels/check_tag_timing.py --tags spike/labels/tags.json \\
        --video 20260919-flight=data/videos/20260919-flight.mp4

With tags keyed by the fixed tagger, the key and the prediction agree and
the best offset should be 0; a steady -1 or -2 means the phone shows an
earlier frame than it reports, and the fix needs revisiting.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

OFFSETS = range(-6, 4)
HALF = 160          # crop half-size, px at 1080p
DISTINCT_PX = 3.0   # the key's error must beat the best offset's by this to count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=Path, required=True)
    ap.add_argument("--video", action="append", required=True, metavar="MATCHID=PATH")
    ap.add_argument("--weights", default="yolo11x.pt", help="a COCO model; the ball is class 32")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    matches = json.loads(args.tags.read_text())["matches"]
    agree = disagree = 0
    best_hist: Counter = Counter()
    for pair in args.video:
        match_id, path = pair.split("=", 1)
        ball = (matches.get(match_id) or {}).get("ball", {})
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        for key in sorted(ball, key=int):
            rec = ball[key]
            if not rec.get("vis"):
                continue
            n = int(key)
            tx, ty = rec["x"] * cap.get(cv2.CAP_PROP_FRAME_WIDTH), rec["y"] * cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_POS_FRAMES, n + OFFSETS[0])
            err = {}
            for o in OFFSETS:
                ok, frame = cap.read()
                if not ok:
                    break
                pad = cv2.copyMakeBorder(frame, HALF, HALF, HALF, HALF, cv2.BORDER_CONSTANT)
                crop = pad[int(ty):int(ty) + 2 * HALF, int(tx):int(tx) + 2 * HALF]
                r = model.predict(crop, imgsz=2 * HALF, conf=0.05, classes=[32], verbose=False,
                                  device=args.device)[0]
                b = r.boxes.xyxy.cpu().numpy()
                if len(b):
                    cx = (b[:, 0] + b[:, 2]) / 2 - HALF + (int(tx) - tx)
                    cy = (b[:, 1] + b[:, 3]) / 2 - HALF + (int(ty) - ty)
                    err[o] = float(np.min(np.hypot(cx, cy)))
            if 0 not in err or len(err) < 3:
                continue
            best = min(err, key=err.get)
            if "t" in rec:
                pred = math.floor(rec["t"] / 1000.0 * fps + 1e-6) - n
                pred = pred if abs(pred) <= 1 else 0
            else:
                pred = 0
            if err[0] - err[best] < DISTINCT_PX and best != pred:
                continue                     # the ball barely moved; the frames cannot be told apart
            best_hist[best] += 1
            good = pred in err and err[pred] - err[best] < 2.0
            agree += good
            disagree += not good
            print(f"{match_id} {n}: best {best:+d} ({err[best]:.1f}px), predicted {pred:+d}, "
                  f"key {err[0]:.1f}px{'' if good else '   <- prediction not best'}")
        cap.release()

    print(f"\nbest offset over distinguishable tags: {dict(sorted(best_hist.items()))}")
    print(f"the predicted frame is within 2 px of the best in {agree} of {agree + disagree}")


if __name__ == "__main__":
    main()
