"""Does a pitch *keypoint* model work on this footage, where lines did not?

The classical registrar here finds line segments and then has to work out
which pitch line each one is. That naming search is most of the code and
all of the failures: on real footage something thin and bright that is
not a line always turns up, and the search has no way to know.

A keypoint model skips the whole problem. It is trained to emit named
points — "left penalty box, top corner" — so the correspondence is given,
not searched for, and the homography is one `findHomography` call. This
is what the Roboflow sports project does, and several people have
published 32-keypoint models trained on their layout.

This script answers the only question that matters before adopting it:
how many named keypoints does such a model find on *our* footage, which
is a worn youth pitch shot by a follow-cam, not a broadcast game.

    python spike/evals/pitch_keypoints.py --weights pitch.pt \\
        --video match.mp4 --frames 12 --out out/

Reported per frame: keypoints above threshold, and — where there are at
least four — the reprojection error of the fitted homography on those
same points. That error is self-consistency, not accuracy: it says the
points agree with one homography, not that they are in the right place.
The overlay is what says whether they are in the right place.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# Roboflow's 32-vertex soccer pitch, in centimetres with the origin at a
# corner, from sports/configs/soccer.py. Kept in their order because the
# model's keypoint index is what names each point.
WIDTH, LENGTH = 7000.0, 12000.0
PB_W, PB_L = 4100.0, 2015.0
GB_W, GB_L = 1832.0, 550.0
CC_R, PS_D = 915.0, 1100.0

VERTICES = [
    (0, 0), (0, (WIDTH - PB_W) / 2), (0, (WIDTH - GB_W) / 2), (0, (WIDTH + GB_W) / 2),
    (0, (WIDTH + PB_W) / 2), (0, WIDTH),
    (GB_L, (WIDTH - GB_W) / 2), (GB_L, (WIDTH + GB_W) / 2),
    (PS_D, WIDTH / 2),
    (PB_L, (WIDTH - PB_W) / 2), (PB_L, (WIDTH - GB_W) / 2), (PB_L, (WIDTH + GB_W) / 2), (PB_L, (WIDTH + PB_W) / 2),
    (LENGTH / 2, 0), (LENGTH / 2, WIDTH / 2 - CC_R), (LENGTH / 2, WIDTH / 2 + CC_R), (LENGTH / 2, WIDTH),
    (LENGTH - PB_L, (WIDTH - PB_W) / 2), (LENGTH - PB_L, (WIDTH - GB_W) / 2),
    (LENGTH - PB_L, (WIDTH + GB_W) / 2), (LENGTH - PB_L, (WIDTH + PB_W) / 2),
    (LENGTH - PS_D, WIDTH / 2),
    (LENGTH - GB_L, (WIDTH - GB_W) / 2), (LENGTH - GB_L, (WIDTH + GB_W) / 2),
    (LENGTH, 0), (LENGTH, (WIDTH - PB_W) / 2), (LENGTH, (WIDTH - GB_W) / 2),
    (LENGTH, (WIDTH + GB_W) / 2), (LENGTH, (WIDTH + PB_W) / 2), (LENGTH, WIDTH),
    (LENGTH / 2 - CC_R, WIDTH / 2), (LENGTH / 2 + CC_R, WIDTH / 2),
]

NAMES = [
    "corner L-top", "pen L-top out", "goal L-top out", "goal L-bot out", "pen L-bot out", "corner L-bot",
    "goal L-top in", "goal L-bot in", "pen spot L",
    "pen L-top in", "box L-top", "box L-bot", "pen L-bot in",
    "halfway top", "circle top", "circle bot", "halfway bot",
    "pen R-top in", "box R-top", "box R-bot", "pen R-bot in",
    "pen spot R", "goal R-top in", "goal R-bot in",
    "corner R-top", "pen R-top out", "goal R-top out", "goal R-bot out", "pen R-bot out", "corner R-bot",
    "circle left", "circle right",
]

# Edges between vertex indices (1-based in their config), for drawing.
EDGES = [(1,2),(2,3),(3,4),(4,5),(5,6),(2,10),(3,7),(4,8),(5,13),(7,8),(10,11),(11,12),(12,13),
         (14,17),(1,14),(6,17),(25,30),(14,25),(17,30),(26,27),(27,28),(28,29),(23,24),
         (18,19),(19,20),(20,21),(26,18),(27,23),(28,24),(29,21),(1,25),(6,30)]


def pitch_xy(real_length_m: float, real_width_m: float) -> np.ndarray:
    """The 32 vertices in metres, scaled to a real pitch, origin at the
    centre spot. Their layout is full size; a youth pitch is not, and the
    keypoints are *named*, so they rescale rather than needing a new model."""
    sx, sy = real_length_m / (LENGTH / 100.0), real_width_m / (WIDTH / 100.0)
    out = []
    for x, y in VERTICES:
        out.append([(x / 100.0) * sx - real_length_m / 2.0, (y / 100.0) * sy - real_width_m / 2.0])
    return np.array(out, dtype=np.float64)


def draw(frame, kpts, conf, thresh, H_p2i, pitch_m):
    vis = frame.copy()
    if H_p2i is not None:
        pts = np.concatenate([pitch_m, np.ones((len(pitch_m), 1))], axis=1) @ H_p2i.T
        w = pts[:, 2]
        ok = np.abs(w) > 1e-9
        proj = np.full((len(pitch_m), 2), np.nan)
        proj[ok] = pts[ok, :2] / w[ok, None]
        for a, b in EDGES:
            pa, pb = proj[a - 1], proj[b - 1]
            if np.isfinite(pa).all() and np.isfinite(pb).all():
                if max(abs(pa[0]), abs(pb[0])) < 1e4 and max(abs(pa[1]), abs(pb[1])) < 1e4:
                    cv2.line(vis, tuple(pa.astype(int)), tuple(pb.astype(int)), (0, 220, 255), 2, cv2.LINE_AA)
    for i, (p, c) in enumerate(zip(kpts, conf)):
        if c < thresh:
            continue
        cv2.circle(vis, (int(p[0]), int(p[1])), 7, (40, 40, 240), -1)
        cv2.circle(vis, (int(p[0]), int(p[1])), 7, (255, 255, 255), 1)
        cv2.putText(vis, NAMES[i], (int(p[0]) + 9, int(p[1]) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--at", type=int, nargs="*", default=None, help="specific frame indices instead of a spread")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--conf", type=float, default=0.5, help="keypoint confidence to believe")
    ap.add_argument("--pitch", type=float, nargs=2, default=[100.0, 64.0], metavar=("LENGTH_M", "WIDTH_M"))
    ap.add_argument("--imgsz", type=int, default=1280)
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    pitch_m = pitch_xy(*args.pitch)
    args.out.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(args.video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idxs = args.at if args.at else [int(total * f) for f in np.linspace(0.05, 0.95, args.frames)]

    found_counts, fitted = [], 0
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        r = model.predict(frame, imgsz=args.imgsz, verbose=False)[0]
        if r.keypoints is None or len(r.keypoints) == 0:
            print(f"frame {idx:7d} ({idx/fps/60:5.1f} min): no pitch detected")
            found_counts.append(0)
            continue
        kp = r.keypoints.xy.cpu().numpy()[0]
        kc = (r.keypoints.conf.cpu().numpy()[0] if r.keypoints.conf is not None else np.ones(len(kp)))
        good = kc >= args.conf
        n = int(good.sum())
        found_counts.append(n)

        H_p2i, err = None, None
        if n >= 4:
            src = pitch_m[good].astype(np.float32)
            dst = kp[good].astype(np.float32)
            H_p2i, mask = cv2.findHomography(src, dst, cv2.RANSAC, 8.0)
            if H_p2i is not None:
                fitted += 1
                p = np.concatenate([src, np.ones((len(src), 1), np.float32)], axis=1) @ H_p2i.T
                proj = p[:, :2] / p[:, 2:3]
                err = float(np.median(np.linalg.norm(proj - dst, axis=1)))
        names = ", ".join(NAMES[i] for i in np.flatnonzero(good)[:6])
        print(f"frame {idx:7d} ({idx/fps/60:5.1f} min): {n:2d} keypoints"
              + (f", homography fits to {err:.1f}px" if err is not None else ", no homography")
              + (f"  [{names}{'…' if n > 6 else ''}]" if n else ""))
        cv2.imwrite(str(args.out / f"{idx:07d}.jpg"), draw(frame, kp, kc, args.conf, H_p2i, pitch_m),
                    [cv2.IMWRITE_JPEG_QUALITY, 82])
    cap.release()

    fc = np.array(found_counts)
    print(f"\n{args.video.name} with {Path(args.weights).name}")
    print(f"frames with 4+ keypoints: {int((fc >= 4).sum())}/{len(fc)}   homography fitted: {fitted}/{len(fc)}")
    print(f"keypoints per frame: median {np.median(fc):.0f}, best {fc.max() if len(fc) else 0}")
    print(f"overlays in {args.out} — the yellow pitch is the fit, red dots are what the model named")


if __name__ == "__main__":
    main()
