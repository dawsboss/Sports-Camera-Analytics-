"""Score the follow-cam registrar against SoccerNet's camera-calibration
ground truth: real broadcast frames with named pitch-line points.

No training happens here. `FollowCamRegistrar` is classical computer
vision (line detection + geometric search), not a learned model, so this
script just runs the code already in `sideline/registration/followcam.py`
against real images and measures how far off it is. That is what "run it
against real footage" means for this stage.

Caveat worth keeping in mind reading the numbers: these are stable, wide
broadcast shots, not a tight panning Veo crop. They are a fair test of
the line-finding and homography-fitting machinery, but likely easier than
Veo footage, which shows less of the pitch per frame and moves.

Usage:
    pip install SoccerNet
    python -m SoccerNet.Downloader ...  (see download() below, or the README)
    python spike/evals/soccernet.py /path/to/calibration-2023/test --n 100
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from sideline.registration.followcam import FollowCamRegistrar
from sideline.registration.geometry import apply_h
from sideline.sports import get_sport

# SoccerNet's label -> which of our SurfaceLine names it lies on. Circles
# are skipped: our recall scoring measures distance to segments, and a
# circle needs the different arc-distance metric `_recall` already uses
# internally but that this quick script does not reimplement.
LABEL_TO_LINE = {
    "Side line top": "touchline_top",
    "Side line bottom": "touchline_bottom",
    "Side line left": "goal_line_left",
    "Side line right": "goal_line_right",
    "Middle line": "halfway",
    "Big rect. left top": "penalty_left_top",
    "Big rect. left bottom": "penalty_left_bottom",
    "Big rect. left main": "penalty_left_front",
    "Big rect. right top": "penalty_right_top",
    "Big rect. right bottom": "penalty_right_bottom",
    "Big rect. right main": "penalty_right_front",
    "Small rect. left top": "goal_area_left_top",
    "Small rect. left bottom": "goal_area_left_bottom",
    "Small rect. left main": "goal_area_left_front",
    "Small rect. right top": "goal_area_right_top",
    "Small rect. right bottom": "goal_area_right_bottom",
    "Small rect. right main": "goal_area_right_front",
}


@dataclass
class FrameScore:
    name: str
    registered: bool
    confidence: float
    n_points: int
    mean_err_m: float | None
    p90_err_m: float | None


def load_points(json_path: Path, size: tuple[int, int]) -> dict[str, np.ndarray]:
    """Named line -> its annotated points, in pixels."""
    raw = json.loads(json_path.read_text())
    W, H = size
    out: dict[str, list[np.ndarray]] = {}
    for label, pts in raw.items():
        line = LABEL_TO_LINE.get(label.strip())
        if line is None:
            continue
        out.setdefault(line, []).extend(np.array([p["x"] * W, p["y"] * H]) for p in pts)
    return {k: np.array(v) for k, v in out.items()}


def score_frame(reg: FollowCamRegistrar, segs_by_name: dict, img_path: Path, json_path: Path) -> FrameScore:
    img = cv2.imread(str(img_path))
    H, W = img.shape[:2]
    points = load_points(json_path, (W, H))
    r = reg.register(img)
    if not r.registered:
        return FrameScore(img_path.stem, False, r.confidence, sum(len(v) for v in points.values()), None, None)

    errs = []
    for name, pts in points.items():
        seg = segs_by_name.get(name)
        if seg is None or len(pts) == 0:
            continue
        proj = apply_h(r.H, pts)              # image px -> pitch metres
        p0, p1 = seg[:2], seg[2:]
        d = p1 - p0
        t = np.clip(((proj - p0) @ d) / (d @ d), 0, 1)
        foot = p0 + t[:, None] * d
        errs.append(np.linalg.norm(proj - foot, axis=1))
    errs = np.concatenate(errs) if errs else np.array([])
    errs = errs[np.isfinite(errs)]
    return FrameScore(
        img_path.stem, True, r.confidence, sum(len(v) for v in points.values()),
        float(np.mean(errs)) if len(errs) else None,
        float(np.percentile(errs, 90)) if len(errs) else None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("test_dir", type=Path, help="the unzipped calibration-2023/test directory")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    pitch = get_sport("soccer").surface(105.0, 68.0)
    segs_by_name = {l.name: np.array([*l.p0, *l.p1]) for l in pitch.lines()}
    reg = FollowCamRegistrar(pitch)

    images = sorted(args.test_dir.glob("*.jpg"))
    rng = np.random.default_rng(args.seed)
    sample = rng.choice(images, size=min(args.n, len(images)), replace=False)

    scores = []
    for img_path in sample:
        json_path = img_path.with_suffix(".json")
        if not json_path.exists():
            continue
        scores.append(score_frame(reg, segs_by_name, img_path, json_path))

    n = len(scores)
    reg_frac = sum(s.registered for s in scores) / n
    errs = [s.mean_err_m for s in scores if s.mean_err_m is not None]
    print(f"frames scored: {n}")
    print(f"registered:    {sum(s.registered for s in scores)} ({100*reg_frac:.0f}%)   gate: >= 70%")
    if errs:
        print(f"mean error (of registered, mean per frame): {np.mean(errs):.2f} m   median: {np.median(errs):.2f} m   gate: < 2 m at centre")
        print(f"worst 10 frames by error:")
        worst = sorted([s for s in scores if s.mean_err_m is not None], key=lambda s: -s.mean_err_m)[:10]
        for s in worst:
            print(f"  {s.name}  err={s.mean_err_m:.1f}m  conf={s.confidence:.2f}  points={s.n_points}")
    failed = [s for s in scores if not s.registered]
    print(f"unregistered: {len(failed)} ({', '.join(s.name for s in failed[:15])})")


if __name__ == "__main__":
    main()
