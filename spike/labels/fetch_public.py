"""Fetch the public pitch-keypoint and ball datasets to pretrain on.

Nobody else's data looks like a youth match on a school field, so these
cannot replace the tagger. What they buy is the part that is *not*
specific to this footage — what a penalty box corner is, what a ball is —
so that the frames tagged here are spent on the domain gap instead of on
the basics. Pretrain here, fine-tune on `tags.json`, measure on a match
neither stage saw.

    python spike/labels/fetch_public.py --out data/public

Both are CC-BY-4.0 mirrors of Roboflow Universe projects on Hugging Face,
and neither needs an account. `PITCH_KEYPOINTS.md` says the Universe links
return 401 unauthenticated; these mirrors are the way round that.

The pitch set is checked, not trusted. Its keypoint indices are the
*names* the model learns, so if upstream ever reorders them, pretraining
would quietly teach index 24 to mean "corner L-top" and every homography
built on it would be wrong while reporting a small reprojection error --
the exact failure PITCH_KEYPOINTS.md measured in the community weights.
The check below is cheap and that failure is not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOS = {
    "pitch": "martinjolif/football-pitch-detection",
    "ball": "martinjolif/football-ball-detection",
}

# Under a mirror about the halfway line the camera does not move, y is
# preserved and x goes to LENGTH - x, so this permutation is a property of
# the vertex order alone. Comparing it against ours is therefore a real
# test that their index i and our index i name the same point.
EXPECTED_FLIP = [24, 25, 26, 27, 28, 29, 22, 23, 21, 17, 18, 19, 20, 13, 14, 15, 16,
                 9, 10, 11, 12, 8, 6, 7, 0, 1, 2, 3, 4, 5, 31, 30]


def our_vertices() -> list[tuple[float, float]]:
    """The 32-vertex layout this repository already carries."""
    import importlib.util
    import types

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))   # only needed for drawing
    path = Path(__file__).resolve().parents[1] / "evals" / "pitch_keypoints.py"
    spec = importlib.util.spec_from_file_location("_pk", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass                              # VERTICES is defined before anything that needs cv2
    return mod.VERTICES


def check_pitch_order(root: Path) -> None:
    text = (root / "data" / "data.yaml").read_text()
    line = next((ln for ln in text.splitlines() if ln.startswith("flip_idx:")), None)
    if line is None:
        raise SystemExit("the pitch dataset no longer declares flip_idx; check its keypoint "
                         "order against spike/evals/pitch_keypoints.py by hand before training")
    theirs = [int(n) for n in line.split(":", 1)[1].strip().strip("[]").split(",")]
    if theirs != EXPECTED_FLIP:
        raise SystemExit(f"the pitch dataset's keypoint order has changed.\n"
                         f"  expected flip_idx {EXPECTED_FLIP}\n"
                         f"  found            {theirs}\n"
                         "Pretraining on it would teach the wrong point names. Stop here.")
    V = our_vertices()
    length = max(x for x, _ in V)
    bad = [i for i in range(32)
           if abs((length - V[i][0]) - V[theirs[i]][0]) > 1 or abs(V[i][1] - V[theirs[i]][1]) > 1]
    if bad:
        raise SystemExit(f"their flip_idx disagrees with our VERTICES at indices {bad}")
    print("  keypoint order matches spike/evals/pitch_keypoints.py and web/label.html")


def write_yaml(kind: str, root: Path) -> Path:
    """Ultralytics resolves splits against `path`, so write it absolute."""
    data = (root / "data").resolve()
    head = f"path: {data}\ntrain: train/images\nval: valid/images\ntest: test/images\n"
    if kind == "ball":
        body = "names:\n  0: ball\n"
    else:
        body = f"kpt_shape: [32, 3]\nflip_idx: {EXPECTED_FLIP}\nnames:\n  0: pitch\n"
    out = data / "sideline.yaml"
    out.write_text(head + body)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", choices=sorted(REPOS), action="append", default=[])
    args = ap.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("pip install huggingface_hub")

    for kind in (args.only or sorted(REPOS)):
        print(f"{kind}: downloading {REPOS[kind]}")
        root = Path(snapshot_download(REPOS[kind], repo_type="dataset",
                                      local_dir=args.out / kind))
        if kind == "pitch":
            check_pitch_order(root)
        n = len(list((root / "data" / "train" / "labels").glob("*.txt")))
        v = len(list((root / "data" / "valid" / "labels").glob("*.txt")))
        print(f"  {n} train, {v} val -> {write_yaml(kind, root)}")


if __name__ == "__main__":
    main()
