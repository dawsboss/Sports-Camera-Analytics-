"""Turn tags from the Ball Tagger page into a training set.

`web/label.html` records one tag per sampled frame: the frame index, and
either where the ball is (normalised to the frame) or that it is not
visible. This cuts the matching frames out of the video and writes them
in the layout Ultralytics trains from.

    python spike/labels/build_dataset.py --tags tags.json \
        --video 20260919-flight=/path/to/flight.mp4 \
        --video 20260920-future=/path/to/future.mp4 \
        --out data/ball --holdout 20260920-future

Frames where the ball is not visible are written with an empty label
file, which is how a detector is taught what is *not* a ball. Those are
the tags that stop it firing on a corner flag or a white sock, so they
are kept, not dropped.

The held-out match is never trained on. One match's fix is not a fix
until it holds on footage it never saw, and `--holdout` is what keeps
that honest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

# A tag is a point, not a box, because pointing at the ball on a phone is
# quick and dragging a box is not. The box comes from this half-width, in
# pixels at 1080p, scaled if the footage is another size. The ball measured
# 5 to 35 px across in these exports depending on how far away it is; a
# fixed box is an approximation and the first thing to revisit if the
# trained detector's boxes are the wrong size. Recall matters more than box
# fit for everything downstream, which only needs where the ball is.
BOX_HALF_PX_AT_1080 = 11.0


def load_tags(path: Path) -> dict:
    doc = json.loads(path.read_text())
    if "matches" not in doc:
        raise SystemExit("this does not look like a Ball Tagger export (no 'matches')")
    return doc["matches"]


def cut(video: Path, frames: dict, out_img: Path, out_lbl: Path, prefix: str, quality: int) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    half = BOX_HALF_PX_AT_1080 * (h / 1080.0)
    bw, bh = (2 * half) / w, (2 * half) / h
    out_img.mkdir(parents=True, exist_ok=True)
    out_lbl.mkdir(parents=True, exist_ok=True)

    written = positives = 0
    for key in sorted(frames, key=lambda k: int(k)):
        idx = int(key)
        rec = frames[key]
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        stem = f"{prefix}_{idx:07d}"
        cv2.imwrite(str(out_img / f"{stem}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        lines = []
        if rec.get("vis"):
            x = min(1.0, max(0.0, float(rec["x"])))
            y = min(1.0, max(0.0, float(rec["y"])))
            lines.append(f"0 {x:.6f} {y:.6f} {bw:.6f} {bh:.6f}")
            positives += 1
        # No lines at all is a valid label file: this frame holds no ball.
        (out_lbl / f"{stem}.txt").write_text("\n".join(lines))
        written += 1
    cap.release()
    return written, positives


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=Path, required=True, help="the JSON copied out of the tagger")
    ap.add_argument("--video", action="append", default=[], metavar="MATCHID=PATH",
                    help="where each match's video file is; repeatable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holdout", action="append", default=[], metavar="MATCHID",
                    help="match ids to put in val/ and never train on; repeatable")
    ap.add_argument("--quality", type=int, default=92)
    args = ap.parse_args()

    videos = {}
    for pair in args.video:
        if "=" not in pair:
            raise SystemExit(f"--video wants MATCHID=PATH, got {pair!r}")
        k, v = pair.split("=", 1)
        videos[k] = Path(v)

    matches = load_tags(args.tags)
    unknown = [m for m in matches if m not in videos]
    if unknown:
        print(f"no video given for {unknown}, skipping those")

    totals = {"train": [0, 0], "val": [0, 0]}
    for match_id, frames in matches.items():
        if match_id not in videos:
            continue
        split = "val" if match_id in args.holdout else "train"
        n, p = cut(videos[match_id], frames, args.out / "images" / split, args.out / "labels" / split,
                   match_id, args.quality)
        totals[split][0] += n
        totals[split][1] += p
        print(f"{match_id} -> {split}: {n} frames, {p} with a ball")

    (args.out / "data.yaml").write_text(
        f"path: {args.out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: ball\n"
    )
    print(f"\ntrain {totals['train'][0]} frames ({totals['train'][1]} with a ball), "
          f"val {totals['val'][0]} ({totals['val'][1]} with a ball)")
    print(f"wrote {args.out / 'data.yaml'}")
    if totals["val"][0] == 0:
        print("\nNothing is held out. Pass --holdout with a match id, or the score will flatter itself.")
    print("\nTrain (on the homelab GPU):")
    print(f"  yolo detect train model=yolo11s.pt data={args.out / 'data.yaml'} imgsz=1920 epochs=100")
    print("Then measure it the same way as the baseline:")
    print("  python spike/evals/ball_recall.py --video <held-out match> --weights runs/detect/train/weights/best.pt")


if __name__ == "__main__":
    main()
