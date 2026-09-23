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
that honest. While only one match is tagged, `--holdout-window
MATCHID=START:END` (minutes of video, either end open) holds out a
stretch of it instead: different passages of play, light and end of the
pitch, which is weaker than a separate match and far better than nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
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

# A horizontal image flip mirrors the world about a vertical plane through
# the camera: the camera stays on the same touchline, so `camera_side` is
# untouched, and every pitch point goes to its mirror about the halfway
# line, x -> LENGTH - x. On `pitch_keypoints.py:VERTICES` that is this
# permutation (0-based, as Ultralytics wants it). Writing it into the pose
# data.yaml is what lets Ultralytics swap the names when it mirrors a
# frame; without it `fliplr` would teach that a left corner is a right one.
# The four vertices on the halfway line map to themselves, so mirroring
# evens out left against right but adds nothing for 13-16.
FLIP_IDX = [24, 25, 26, 27, 28, 29, 22, 23, 21, 17, 18, 19, 20, 13, 14, 15,
            16, 9, 10, 11, 12, 8, 6, 7, 0, 1, 2, 3, 4, 5, 31, 30]
# A mirror applied twice is the identity, so a typo here almost certainly
# breaks this; tests/test_build_dataset.py checks it against VERTICES too.
assert sorted(FLIP_IDX) == list(range(N_VERTICES))
assert all(FLIP_IDX[FLIP_IDX[i]] == i for i in range(N_VERTICES))

# Frames this close outside a held-out window are dropped rather than
# trained on. Tags come in bursts at 5 fps, and a burst cut in two by the
# window edge would put near-identical frames on both sides of the split.
WINDOW_GUARD_S = 10.0


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


def tagged_frame(key: str, rec: dict, fps: float) -> int:
    """The frame that was on screen when the tag was placed.

    Tags from before the tagger's fix are keyed Math.round(currentTime *
    29.97), but a paused video shows the frame whose interval contains
    currentTime: the floor, at the true rate. Past mid-frame the key names
    the frame after the one tapped, and on a fast zoomed pan that moves the
    ball 10-18 px out of its 22 px box (measured on 20260919-flight: the
    floor was the best-matching frame for 29 of 32 tags where it could be
    told apart). Ball tags carry the time, so the floor is recoverable;
    pitch tags do not, and keep their key, at most one frame late."""
    idx = int(key)
    t = rec.get("t") if isinstance(rec, dict) else None
    if t is None or not fps:
        return idx
    shown = math.floor(float(t) / 1000.0 * fps + 1e-6)
    # Anything further than one frame away is not this rounding; trust the key.
    return shown if abs(shown - idx) <= 1 else idx


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


def parse_window(text: str) -> tuple[str, float, float]:
    """`MATCHID=START:END` in minutes of video, either end may be left
    open. Returns the match id and the window in seconds."""
    match_id, _, span = text.partition("=")
    if not match_id or ":" not in span:
        raise SystemExit(f"--holdout-window wants MATCHID=START:END in minutes, got {text!r}")
    a, b = span.split(":", 1)
    start = float(a) * 60.0 if a.strip() else 0.0
    end = float(b) * 60.0 if b.strip() else float("inf")
    if end <= start:
        raise SystemExit(f"--holdout-window {text!r} holds out nothing")
    return match_id, start, end


def split_by_windows(tags: dict, fps: float, windows: list[tuple[float, float]],
                     guard_s: float = WINDOW_GUARD_S) -> tuple[dict, dict]:
    """(train, val) for one match's tags. Inside a window is val; within
    `guard_s` of one is neither; everything else is train."""
    train, val = {}, {}
    for key, rec in tags.items():
        t = int(key) / fps
        if any(a <= t < b for a, b in windows):
            val[key] = rec
        elif not any(a - guard_s <= t < b + guard_s for a, b in windows):
            train[key] = rec
    return train, val


def video_fps(video: Path) -> float:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if not fps or fps <= 0:
        raise SystemExit(f"{video} does not report a frame rate; cannot place a time window in it")
    return fps


def cut(video: Path, tags: dict, kind: str, out: Path, split: str, prefix: str,
        quality: int) -> tuple[int, int, int]:
    """Write frames and labels for one match and one kind. Returns
    (frames written, frames carrying a positive label, frames moved off
    their key by `tagged_frame`)."""
    if not tags:
        return 0, 0, 0
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    img_dir = out / kind / "images" / split
    lbl_dir = out / kind / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    written = positive = moved = 0
    for key in sorted(tags, key=lambda k: int(k)):
        idx = tagged_frame(key, tags[key], fps) if kind == "ball" else int(key)
        moved += idx != int(key)
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
    return written, positive, moved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=Path, required=True, help="the JSON copied out of the tagger")
    ap.add_argument("--video", action="append", default=[], metavar="MATCHID=PATH",
                    help="where each match's video file is; repeatable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holdout", action="append", default=[], metavar="MATCHID",
                    help="match ids to put in val/ and never train on; repeatable")
    ap.add_argument("--holdout-window", action="append", default=[], metavar="MATCHID=START:END",
                    help="minutes of one match to put in val/, either end open (50: is 50 min "
                         "to the end); for when only one match is tagged; repeatable")
    ap.add_argument("--kinds", default="ball,pitch")
    ap.add_argument("--quality", type=int, default=92)
    args = ap.parse_args()

    videos = {}
    for pair in args.video:
        if "=" not in pair:
            raise SystemExit(f"--video wants MATCHID=PATH, got {pair!r}")
        k, v = pair.split("=", 1)
        videos[k] = Path(v)

    windows: dict[str, list[tuple[float, float]]] = {}
    for text in args.holdout_window:
        match_id, start, end = parse_window(text)
        windows.setdefault(match_id, []).append((start, end))
    both = sorted(set(windows) & set(args.holdout))
    if both:
        raise SystemExit(f"{both} given to both --holdout and --holdout-window; pick one")

    matches = load_tags(args.tags)
    missing = [m for m in matches if m not in videos]
    if missing:
        print(f"no video given for {missing}, skipping those")
    unknown = sorted(set(windows) - set(matches))
    if unknown:
        raise SystemExit(f"--holdout-window names {unknown}, which the tags do not contain")

    kinds = [k for k in args.kinds.split(",") if k]
    # Frames left over from a previous build would keep their old split, so
    # a re-run that moves a frame into val would leave its twin in train.
    # These two trees are this script's output and nothing else's.
    for kind in kinds:
        for sub in ("images", "labels"):
            shutil.rmtree(args.out / kind / sub, ignore_errors=True)

    totals = {k: {"train": [0, 0], "val": [0, 0]} for k in kinds}
    for match_id, entry in matches.items():
        if match_id not in videos:
            continue
        fps = video_fps(videos[match_id]) if match_id in windows else 0.0
        for kind in kinds:
            tags = frames_of(entry, kind if kind == "ball" else "kp")
            if match_id in args.holdout:
                parts = {"val": tags}
            elif match_id in windows:
                train, val = split_by_windows(tags, fps, windows[match_id], WINDOW_GUARD_S)
                parts = {"train": train, "val": val}
                dropped = len(tags) - len(train) - len(val)
                if dropped:
                    print(f"{match_id} {kind}: {dropped} tags within {WINDOW_GUARD_S:g}s of the "
                          f"held-out window dropped from both sides")
            else:
                parts = {"train": tags}
            if "val" in parts:
                # Val is scored against what a person tapped, never against
                # what propagate.py filled in from those taps.
                parts["val"] = {k: v for k, v in parts["val"].items()
                                if not (isinstance(v, dict) and v.get("src") == "prop")}
            for split, part in parts.items():
                n, p, moved = cut(videos[match_id], part, kind, args.out, split, match_id, args.quality)
                if n:
                    totals[kind][split][0] += n
                    totals[kind][split][1] += p
                    print(f"{match_id} {kind} -> {split}: {n} frames, {p} with a label"
                          + (f", {moved} cut one frame off their key (the frame on screen)" if moved else ""))

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
            # flip_idx is what makes horizontal flips sound here: Ultralytics
            # swaps each keypoint for its mirror when it flips the frame.
            (args.out / kind / "data.yaml").write_text(
                f"path: {root}\ntrain: images/train\nval: images/val\n"
                f"kpt_shape: [{N_VERTICES}, 3]\nflip_idx: {FLIP_IDX}\nnames:\n  0: pitch\n"
            )
        print(f"{kind}: train {t['train'][0]} frames ({t['train'][1]} labelled), "
              f"val {t['val'][0]} ({t['val'][1]} labelled) -> {args.out / kind / 'data.yaml'}")
        if t["val"][0] == 0:
            print(f"  nothing held out for {kind}; pass --holdout or --holdout-window, "
                  f"or the score will flatter itself")

    # Stage two of docs/TRAINING.md: start from the public-data pretrain,
    # low lr0 so it is not erased. Ball: no mosaic and a narrow scale range,
    # because both shrink an 11 px ball to 5 px.
    print("\nFine-tune (stage two of docs/TRAINING.md) on the GPU:")
    if "ball" in kinds:
        print(f"  yolo detect train model=runs/detect/ball_pre/weights/best.pt data={args.out / 'ball' / 'data.yaml'} "
              f"imgsz=1920 epochs=80 lr0=0.001 patience=20 mosaic=0.0 scale=0.2 name=ball_ft")
    if "pitch" in kinds:
        print(f"  yolo pose train model=runs/pose/pitch_pre/weights/best.pt data={args.out / 'pitch' / 'data.yaml'} "
              f"imgsz=1280 epochs=200 lr0=0.001 name=pitch_ft")
    print("\nThen measure on footage it never trained on:")
    print("  python spike/evals/ball_recall.py --video <held-out> --weights runs/detect/ball_ft/weights/best.pt")
    print("  python spike/evals/pitch_keypoints.py --weights runs/pose/pitch_ft/weights/best.pt --video <held-out> --out out/")


if __name__ == "__main__":
    main()
