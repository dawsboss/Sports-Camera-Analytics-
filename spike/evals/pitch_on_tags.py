"""Score a pitch keypoint model against tagged points, not against itself.

`pitch_keypoints.py` reports how many named points a model finds and how
well they agree with one homography. Its own docstring says what that
cannot tell: a confidently wrong set of points agrees with a homography
too — the community weights reported 2-6 px while drawing the pitch in
the sky. Where a person tagged the points, the question can be asked
directly: for each tagged vertex, did the model name that vertex, and
how far from the tag did it put it?

    python spike/evals/pitch_on_tags.py --data data/ours/pitch --split val \\
        --weights runs/pose/pitch_ft/weights/best.pt --out out/pitch_tags

`--split all` scores train and val together, which is fair only for a
model that trained on neither, such as the public-data pretrain.

Distances are in pixels at 1080p. A vertex the model named with
confidence but put more than `--wrong` px from its tag is *wrong*, which
is the failure that matters: a homography built on it is confident and
misplaced. Vertices the model named that nobody tagged are counted but
not judged, because the tagger does not tag every visible point.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

N = 32


def tagged_points(label: Path, w: int, h: int) -> dict[int, np.ndarray]:
    row = label.read_text().split()
    if not row:
        return {}
    kp = np.array(row[5:5 + 3 * N], dtype=float).reshape(N, 3)
    return {i: np.array([kp[i, 0] * w, kp[i, 1] * h]) for i in range(N) if kp[i, 2] > 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True, help="a build_dataset.py output, e.g. data/ours/pitch")
    ap.add_argument("--split", default="val", help="train, val, or all")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.5, help="keypoint confidence to believe")
    ap.add_argument("--near", type=float, default=25.0, help="px at 1080p from the tag that counts as found")
    ap.add_argument("--wrong", type=float, default=60.0, help="px at 1080p beyond which a named point is wrong")
    ap.add_argument("--out", type=Path, help="write an overlay per frame: green tags, red predictions, a line between")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    splits = ["train", "val"] if args.split == "all" else [args.split]
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    tot = {"tagged": 0, "found": 0, "wrong": 0, "missed": 0, "untagged": 0}
    errs: list[float] = []
    for split in splits:
        for lf in sorted((args.data / "labels" / split).glob("*.txt")):
            img_path = next((args.data / "images" / split).glob(lf.stem + ".*"), None)
            if img_path is None:
                continue
            frame = cv2.imread(str(img_path))
            h, w = frame.shape[:2]
            s = 1080.0 / h
            tags = tagged_points(lf, w, h)
            r = model.predict(frame, imgsz=args.imgsz, verbose=False)[0]
            if r.keypoints is not None and len(r.keypoints):
                top = int(np.argmax(r.boxes.conf.cpu().numpy())) if r.boxes is not None and len(r.boxes) else 0
                xy = r.keypoints.xy.cpu().numpy()[top]
                kc = r.keypoints.conf.cpu().numpy()[top] if r.keypoints.conf is not None else np.ones(N)
            else:
                xy, kc = np.zeros((N, 2)), np.zeros(N)
            named = {i for i in range(N) if kc[i] >= args.conf}
            f = {"found": 0, "wrong": 0, "missed": 0}
            vis = frame.copy() if args.out else None
            for i, t in tags.items():
                if i not in named:
                    f["missed"] += 1
                    continue
                d = float(np.linalg.norm(xy[i] - t)) * s
                errs.append(d)
                if d <= args.near:
                    f["found"] += 1
                elif d > args.wrong:
                    f["wrong"] += 1
                if vis is not None:
                    cv2.line(vis, tuple(map(int, t)), tuple(map(int, xy[i])), (0, 255, 255), 1)
            untagged = len(named - set(tags))
            if vis is not None:
                for i, t in tags.items():
                    cv2.circle(vis, tuple(map(int, t)), 7, (0, 200, 0), 2)
                    cv2.putText(vis, str(i), (int(t[0]) + 8, int(t[1]) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
                for i in named:
                    cv2.circle(vis, tuple(map(int, xy[i])), 4, (0, 0, 255), -1)
                    cv2.putText(vis, str(i), (int(xy[i][0]) + 6, int(xy[i][1]) + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
                cv2.imwrite(str(args.out / f"{lf.stem}.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
            print(f"  {split} {lf.stem}: {len(tags):2d} tagged, {f['found']:2d} found, {f['wrong']:2d} wrong, "
                  f"{f['missed']:2d} not named; {untagged} named that nobody tagged")
            tot["tagged"] += len(tags)
            tot["untagged"] += untagged
            for k in f:
                tot[k] += f[k]

    t = max(tot["tagged"], 1)
    print(f"\n{args.data} [{args.split}] with {args.weights}, keypoint conf >= {args.conf}")
    print(f"tagged vertices: {tot['tagged']}")
    print(f"  found within {args.near:g}px: {tot['found']} ({100 * tot['found'] / t:.0f}%)")
    print(f"  named but more than {args.wrong:g}px off: {tot['wrong']} ({100 * tot['wrong'] / t:.0f}%)")
    print(f"  not named: {tot['missed']} ({100 * tot['missed'] / t:.0f}%)")
    if errs:
        print(f"distance from tag where named: median {np.median(errs):.0f}px")
    print(f"named where nobody tagged (not judged): {tot['untagged']}")


if __name__ == "__main__":
    main()
