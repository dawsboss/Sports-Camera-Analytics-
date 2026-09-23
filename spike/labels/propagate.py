"""Fill the frames between two ball tags, where two independent guesses agree.

The tagger samples a burst every 0.2 s, six frames at 29.97, so between
two tapped samples there are five frames nobody labelled. The ball moves
smoothly across 0.2 s, so those five are cheap to fill: match a patch of
the ball from the earlier tap forward, and one from the later tap
backward, each searched near the straight line between the two taps. A
frame is kept only where the two land within `--agree` px of each other.

That agreement is the point. `docs/TRAINING.md` warns that a detector's
confidence is not evidence (every wrong fit measured here was confident),
and that a machine label is only worth keeping where an independent
signal agrees. Two template matches started from different human taps,
in opposite directions, fail differently: a player running past drags
one of them off and not the other.

    python spike/labels/propagate.py --tags spike/labels/tags.json \\
        --video 20260919-flight=data/videos/20260919-flight.mp4 \\
        --out spike/labels/tags_prop.json --sheet out/prop.jpg

The output is the input with the new tags added, each marked `"src":
"prop"`; human tags are untouched. `build_dataset.py` keeps propagated
tags out of val, so the held-out score is still against taps. Look at
the sheet before training on it — a tracker that loses the ball wanders
off it visibly — and delete any key that drifted.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

# Tags further apart than this, in frames, are not neighbours in a burst.
MAX_GAP = 8
PATCH = 10          # template half-size, px at 1080p: the ball is 11 px across
MIN_NCC = 0.6


def _tagged_frame():
    spec = importlib.util.spec_from_file_location("_bd", Path(__file__).with_name("build_dataset.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.tagged_frame


def match(frame: np.ndarray, templ: np.ndarray, centre: np.ndarray, radius: float) -> tuple[np.ndarray, float] | None:
    """Best normalised-correlation match of `templ` whose centre lies within
    `radius` of `centre`. Returns (centre, score) or None off the frame."""
    h, w = frame.shape[:2]
    th, tw = templ.shape[:2]
    x0 = int(round(centre[0] - radius - tw / 2)); y0 = int(round(centre[1] - radius - th / 2))
    x1 = int(round(centre[0] + radius + tw / 2)); y1 = int(round(centre[1] + radius + th / 2))
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    res = cv2.matchTemplate(frame[y0:y1, x0:x1], templ, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return np.array([x0 + loc[0] + tw / 2, y0 + loc[1] + th / 2]), float(score)


def patch(frame: np.ndarray, p: np.ndarray, half: int) -> np.ndarray | None:
    x, y = int(round(p[0])), int(round(p[1]))
    h, w = frame.shape[:2]
    if x - half < 0 or y - half < 0 or x + half + 1 > w or y + half + 1 > h:
        return None
    return frame[y - half:y + half + 1, x - half:x + half + 1]


def fill_pair(frames: dict, a: int, pa: np.ndarray, b: int, pb: np.ndarray, half: int,
              agree: float) -> dict[int, np.ndarray]:
    """Positions for the frames strictly between a and b that both
    directions agree on."""
    ta, tb = patch(frames[a], pa, half), patch(frames[b], pb, half)
    if ta is None or tb is None:
        return {}
    radius = 6 + 0.35 * float(np.linalg.norm(pb - pa))
    out = {}
    for f in range(a + 1, b):
        guess = pa + (pb - pa) * (f - a) / (b - a)
        fw, bw = match(frames[f], ta, guess, radius), match(frames[f], tb, guess, radius)
        if fw is None or bw is None or min(fw[1], bw[1]) < MIN_NCC:
            continue
        if np.linalg.norm(fw[0] - bw[0]) <= agree:
            out[f] = (fw[0] + bw[0]) / 2
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=Path, required=True)
    ap.add_argument("--video", action="append", required=True, metavar="MATCHID=PATH")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agree", type=float, default=3.0, help="px at 1080p the two directions must agree to")
    ap.add_argument("--sheet", type=Path, help="write a crop round every propagated point here")
    args = ap.parse_args()

    tagged_frame = _tagged_frame()
    doc = json.loads(args.tags.read_text())
    tiles = []
    for pair in args.video:
        match_id, path = pair.split("=", 1)
        ball = doc["matches"].get(match_id, {}).get("ball", {})
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        half = max(4, int(round(PATCH * h / 1080)))
        taps = sorted((tagged_frame(k, r, fps), np.array([r["x"] * w, r["y"] * h]))
                      for k, r in ball.items() if r.get("vis") and r.get("src") != "prop")
        added = tried = 0
        for (a, pa), (b, pb) in zip(taps, taps[1:]):
            if not 1 < b - a <= MAX_GAP:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, a)
            frames = {a + i: cap.read()[1] for i in range(b - a + 1)}
            if any(f is None for f in frames.values()):
                continue
            tried += b - a - 1
            for f, p in fill_pair(frames, a, pa, b, pb, half, args.agree * h / 1080).items():
                # Tags from before the tagger's fix are keyed one frame past
                # the frame they show, so a filled frame can share a tap's
                # key. The tap wins; this frame goes unfilled.
                if str(f) in ball:
                    continue
                # Mid-frame time, so tagged_frame() recovers exactly f.
                ball[str(f)] = {"x": round(p[0] / w, 4), "y": round(p[1] / h, 4), "vis": 1,
                                "t": round((f + 0.5) / fps * 1000), "src": "prop"}
                added += 1
                if args.sheet:
                    t = patch(cv2.copyMakeBorder(frames[f], 40, 40, 40, 40, cv2.BORDER_CONSTANT),
                              p + 40, 40).copy()
                    cv2.circle(t, (40, 40), 7, (0, 0, 255), 1)
                    t = cv2.resize(t, (160, 160), interpolation=cv2.INTER_NEAREST)
                    cv2.putText(t, str(f), (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
                    tiles.append(t)
        cap.release()
        print(f"{match_id}: {len(taps)} visible taps; {added} of {tried} frames between neighbours "
              f"filled where both directions agree")

    args.out.write_text(json.dumps(doc, indent=1))
    print(f"-> {args.out}")
    if tiles and args.sheet:
        cols = 15
        tiles += [np.zeros_like(tiles[0])] * (-len(tiles) % cols)
        args.sheet.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.sheet), np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(f"look at every one: {args.sheet}")


if __name__ == "__main__":
    main()
