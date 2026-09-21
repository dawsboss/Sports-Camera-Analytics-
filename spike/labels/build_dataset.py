"""Turn tags from the Sideline Tagger page into a training set.

`web/label.html` records two kinds of tag per frame:

- **ball** — where the ball is, normalised to the frame, or that it is
  not visible;
- **pitch** — which of the 32 named pitch vertices are visible and where.

This cuts the matching frames out of the video and writes them in the
layout Ultralytics trains from: a detection set for the ball, a pose set
for the pitch.

    python spike/labels/build_dataset.py --tags tags.json \
        --video 20260919-flight=/path/to/flight.mp4 \
        --video 20260920-future=/path/to/future.mp4 \
        --out data --holdout 20260920-future

Two rules the layout enforces rather than trusts:

Frames where the ball is not visible get an **empty** label file, which
is how a detector is taught what is *not* a ball. Those are the tags that
stop it firing on a corner flag or a white sock, so they are kept.

The held-out match is never trained on. One match's fix is not a fix
until it holds on footage it never saw, and `--holdout` is what keeps
that honest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

# A ball tag is a point, not a box, because pointing at the ball on a
# phone is quick and dragging a box is not. The box comes from this
# half-width, in pixels at 1080p, scaled if the footage is another size.
# The ball measured 11 px across on both exports (median over 240
# samples), so this is measured rather than guessed.
BOX_HALF_PX_AT_1080 = 11.0

N_VERTICES = 32

# The bounding box a pose model needs around the pitch. Built from the
# tagged points and padded, because the points seen in a follow-cam frame
# are only part of the pitch and a box drawn tight around them would teach
# the model that the pitch ends where the tagging did.
KP_BOX_PAD = 0.04


def load_tags(path: Path) -> dict:
    doc = json.loads(path.read_text())
    if "matches" not in doc:
        raise SystemExit("this does not look like a Sideline Tagger export (no 'matches')")
    return doc["matches"]


def frames_of(entry: dict, kind: str) -> dict:
    """Tags for one match and one kind, tolerating the older export shape
    where a match was a flat map of ball tags with no 'ball' key."""
    if not isinstance(entry, dict):
        return {}
    if kind in entry and isinstance(entry[kind], dict):
        return entry[kind]
    if kind == "ball" and "kp" not in entry:
        looks_flat = all(isinstance(v, dict) and ("vis" in v) for v in entry.values()) if entry else False
        if looks_flat:
            return entry
    return {}


def ball_line(rec: dict, w: int, h: int) -> str | None:
    if not rec.get("vis"):
        return None                      # visible-but-absent: an empty file
    half = BOX_HALF_PX_AT_1080 * (h / 1080.0)
    x = min(1.0, max(0.0, float(rec["x"])))
    y = min(1.0, max(0.0, float(rec["y"])))
    return f"0 {x:.6f} {y:.6f} {(2 * half) / w:.6f} {(2 * half) / h:.6f}"


def pose_line(points: dict) -> str | None:
    """One YOLO pose row: a box around the tagged points, then all 32
    keypoints with a visibility flag. Untagged vertices are written as
    `0 0 0`, which is the format's way of saying "not labelled here" and
    is not the same as "at the origin"."""
    idx = {int(k): v for k, v in points.items()}
    if len(idx) < 4:
        return None
    xs = [p[0] for p in idx.values()]
    ys = [p[1] for p in idx.values()]
    x0, x1 = max(0.0, min(xs) - KP_BOX_PAD), min(1.0, max(xs) + KP_BOX_PAD)
    y0, y1 = max(0.0, min(ys) - KP_BOX_PAD), min(1.0, max(ys) + KP_BOX_PAD)
    cx, cy, bw, bh = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    parts = [f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"]
    for i in range(N_VERTICES):
        if i in idx:
            parts.append(f"{min(1.0, max(0.0, idx[i][0])):.6f} {min(1.0, max(0.0, idx[i][1])):.6f} 2")
        else:
            parts.append("0 0 0")
    return " ".join(parts)


def cut(video: Path, tags: dict, kind: str, out: Path, split: str, prefix: str, quality: int) -> tuple[int, int]:
    """Write frames and labels for one match and one kind. Returns
    (frames written, frames carrying a positive label)."""
    if not tags:
        return 0, 0
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    img_dir = out / kind / "images" / split
    lbl_dir = out / kind / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    written = positive = 0
    for key in sorted(tags, key=lambda k: int(k)):
        idx = int(key)
        line = ball_line(tags[key], w, h) if kind == "ball" else pose_line(tags[key])
        if kind == "pitch" and line is None:
            continue                     # fewer than four points is not a usable frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        stem = f"{prefix}_{idx:07d}"
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        (lbl_dir / f"{stem}.txt").write_text(line or "")
        written += 1
        positive += line is not None
    cap.release()
    return written, positive


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=Path, required=True, help="the JSON copied out of the tagger")
    ap.add_argument("--video", action="append", default=[], metavar="MATCHID=PATH",
                    help="where each match's video file is; repeatable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holdout", action="append", default=[], metavar="MATCHID",
                    help="match ids to put in val/ and never train on; repeatable")
    ap.add_argument("--kinds", default="ball,pitch")
    ap.add_argument("--quality", type=int, default=92)
    args = ap.parse_args()

    videos = {}
    for pair in args.video:
        if "=" not in pair:
            raise SystemExit(f"--video wants MATCHID=PATH, got {pair!r}")
        k, v = pair.split("=", 1)
        videos[k] = Path(v)

    matches = load_tags(args.tags)
    missing = [m for m in matches if m not in videos]
    if missing:
        print(f"no video given for {missing}, skipping those")

    kinds = [k for k in args.kinds.split(",") if k]
    totals = {k: {"train": [0, 0], "val": [0, 0]} for k in kinds}
    for match_id, entry in matches.items():
        if match_id not in videos:
            continue
        split = "val" if match_id in args.holdout else "train"
        for kind in kinds:
            tags = frames_of(entry, kind if kind == "ball" else "kp")
            n, p = cut(videos[match_id], tags, kind, args.out, split, match_id, args.quality)
            if n:
                totals[kind][split][0] += n
                totals[kind][split][1] += p
                print(f"{match_id} {kind} -> {split}: {n} frames, {p} with a label")

    print()
    for kind in kinds:
        t = totals[kind]
        if not (t["train"][0] or t["val"][0]):
            print(f"{kind}: nothing tagged yet")
            continue
        root = (args.out / kind).resolve()
        if kind == "ball":
            (args.out / kind / "data.yaml").write_text(
                f"path: {root}\ntrain: images/train\nval: images/val\nnames:\n  0: ball\n"
            )
        else:
            # No horizontal-flip augmentation for the pitch. A pitch is
            # symmetric under a half turn, so a mirrored image is a valid
            # pitch with every left/right name swapped, and the same
            # ambiguity is why FollowCamConfig.camera_side exists as a
            # stated convention rather than something detected.
            (args.out / kind / "data.yaml").write_text(
                f"path: {root}\ntrain: images/train\nval: images/val\n"
                f"kpt_shape: [{N_VERTICES}, 3]\nnames:\n  0: pitch\n"
            )
        print(f"{kind}: train {t['train'][0]} frames ({t['train'][1]} labelled), "
              f"val {t['val'][0]} ({t['val'][1]} labelled) -> {args.out / kind / 'data.yaml'}")
        if t["val"][0] == 0:
            print(f"  nothing held out for {kind}; pass --holdout or the score will flatter itself")

    print("\nTrain on the homelab GPU:")
    if "ball" in kinds:
        print(f"  yolo detect train model=yolo11s.pt data={args.out / 'ball' / 'data.yaml'} imgsz=1920 epochs=100")
    if "pitch" in kinds:
        print(f"  yolo pose train model=yolo11s-pose.pt data={args.out / 'pitch' / 'data.yaml'} "
              f"imgsz=1280 epochs=200 fliplr=0.0")
    print("\nThen measure on the held-out match, never the trained one:")
    print("  python spike/evals/ball_recall.py --video <held-out> --weights runs/detect/train/weights/best.pt")
    print("  python spike/evals/pitch_keypoints.py --weights runs/pose/train/weights/best.pt --video <held-out> --out out/")


if __name__ == "__main__":
    main()
