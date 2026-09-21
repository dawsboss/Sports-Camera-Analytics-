"""One number per dataset for one registrar configuration.

Any change to registration has to be judged on all of these at once,
because every one of them has caught a change that helped the others:
the synthetic views pin the geometry, SoccerNet's broadcast frames carry
ground truth, and the two Veo exports are the actual target. A variant
that wins on one and loses on another is a trade-off to write down, not a
fix to ship.

    python spike/evals/bench.py --mode hybrid --soccernet <dir> --veo a.mp4 b.mp4

Veo frames have no ground truth. What is reported for them is the share
registered and how far the frame centre jumps between consecutive
registered frames 200 ms apart: a follow-cam pans smoothly, so a large
jump is a frame that registered to the wrong place.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sideline.registration.followcam import FollowCamConfig, FollowCamRegistrar  # noqa: E402
from sideline.registration.geometry import apply_h, surface_error_m  # noqa: E402
from sideline.registration.synthetic import render_pitch_frame, sideline_camera  # noqa: E402
from sideline.sports import get_sport  # noqa: E402
from spike.evals.soccernet import score_frame  # noqa: E402

PITCH = get_sport("soccer").surface(105.0, 68.0)


def _err(H_true, r, size=(1280, 720)):
    W, H = size
    grid = np.stack(np.meshgrid(np.linspace(0, W, 13), np.linspace(0, H, 8)), -1).reshape(-1, 2)
    t = apply_h(np.linalg.inv(H_true), grid)
    on = np.isfinite(t).all(1) & (np.abs(t[:, 0]) <= PITCH.length / 2) & (np.abs(t[:, 1]) <= PITCH.width / 2)
    return float(np.nanmean(surface_error_m(np.linalg.inv(H_true), r.H, grid[on])))


def synthetic(reg: FollowCamRegistrar) -> dict:
    box = [(-38, 0, 1.0), (-42, -5, 1.6), (40, 5, 1.2), (-45, 8, 1.5), (45, -10, 1.2)]
    errs = []
    for aim in box:
        H_true = sideline_camera(PITCH, *aim)
        r = reg.register(render_pitch_frame(PITCH, H_true, players=8, seed=1))
        errs.append(_err(H_true, r) if r.registered else float("nan"))
    mid = reg.register(render_pitch_frame(PITCH, sideline_camera(PITCH, 0, 0, 1.0), players=4, seed=2))
    views = [(ax, ay, z) for ax in (-45, -35, -25, 25, 35, 45) for ay, z in ((0, 1.0), (-10, 1.2))]
    acc = wrong = 0
    for ax, ay, z in views:
        H_true = sideline_camera(PITCH, ax, ay, z)
        r = reg.register(render_pitch_frame(PITCH, H_true, players=6, seed=int(ax + 100 * z)))
        if r.registered:
            acc += 1
            wrong += _err(H_true, r) > 3.0
    return {"box_err_max": float(np.nanmax(errs)), "box_registered": int(np.isfinite(errs).sum()),
            "midfield_refused": not mid.registered, "sweep_registered": acc, "sweep_wrong": wrong, "sweep_n": len(views)}


def soccernet(reg: FollowCamRegistrar, test_dir: Path, n: int, seed: int) -> dict:
    segs = {l.name: np.array([*l.p0, *l.p1]) for l in PITCH.lines()}
    images = sorted(test_dir.glob("*.jpg"))
    sample = np.random.default_rng(seed).choice(images, size=min(n, len(images)), replace=False)
    reg_n = good = bad = 0
    for img in sample:
        js = img.with_suffix(".json")
        if not js.exists():
            continue
        s = score_frame(reg, segs, img, js)
        if s.registered:
            reg_n += 1
            if s.mean_err_m is not None:
                good += s.mean_err_m < 2.0
                bad += s.mean_err_m >= 2.0
    return {"registered": reg_n, "correct": good, "wrong": bad, "n": len(sample)}


def veo(reg: FollowCamRegistrar, video: Path, n: int, burst: int) -> dict:
    from sideline.spike import sample_frames

    reg_n = 0
    centres: list[tuple[int, np.ndarray]] = []
    total = 0
    for idx, t_ms, frame in sample_frames(video, n, burst=burst):
        total += 1
        r = reg.register(frame, idx, t_ms)
        if r.registered:
            reg_n += 1
            c = r.to_pitch(np.array([[frame.shape[1] / 2, frame.shape[0] / 2]]))[0]
            centres.append((t_ms, c))
    jumps = []
    for (ta, ca), (tb, cb) in zip(centres, centres[1:]):
        if 0 < tb - ta <= 400 and np.isfinite(ca).all() and np.isfinite(cb).all():
            jumps.append(float(np.linalg.norm(cb - ca)) / ((tb - ta) / 1000.0))
    return {"registered": reg_n, "n": total,
            "jump_p50": float(np.percentile(jumps, 50)) if jumps else None,
            "jump_p90": float(np.percentile(jumps, 90)) if jumps else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="hybrid", choices=("mask", "frame", "hybrid"))
    ap.add_argument("--min-confidence", type=float, default=None)
    ap.add_argument("--soccernet", type=Path, default=None)
    ap.add_argument("--n-soccernet", type=int, default=100)
    ap.add_argument("--veo", type=Path, nargs="*", default=[])
    ap.add_argument("--n-veo", type=int, default=60)
    ap.add_argument("--burst", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    kw = {"plausibility": args.mode}
    if args.min_confidence is not None:
        kw["min_confidence"] = args.min_confidence
    reg = FollowCamRegistrar(PITCH, FollowCamConfig(**kw))

    t0 = time.time()
    out = [f"mode={args.mode} floor={reg.cfg.min_confidence}"]
    s = synthetic(reg)
    out.append(f"synthetic: box max err {s['box_err_max']:.2f}m ({s['box_registered']}/5 registered), midfield refused={s['midfield_refused']}, sweep {s['sweep_registered']}/{s['sweep_n']} registered {s['sweep_wrong']} wrong")
    if args.soccernet:
        sn = soccernet(reg, args.soccernet, args.n_soccernet, args.seed)
        out.append(f"soccernet: {sn['registered']}/{sn['n']} registered, {sn['correct']} correct (<2m), {sn['wrong']} wrong")
    for v in args.veo:
        r = veo(reg, v, args.n_veo, args.burst)
        j = f"jump p50 {r['jump_p50']:.1f} p90 {r['jump_p90']:.1f} m/s" if r["jump_p50"] is not None else "no consecutive pairs"
        out.append(f"{v.parent.name}/{v.name}: {r['registered']}/{r['n']} registered, {j}")
    out.append(f"({time.time() - t0:.0f}s)")
    print("\n".join(out))


if __name__ == "__main__":
    main()
