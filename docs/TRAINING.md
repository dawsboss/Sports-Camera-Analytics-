# Training the ball and pitch models on your GPU

Everything here runs on one machine with one NVIDIA card. The pipeline
itself never calls a model at runtime on your laptop — this is the
offline step that produces the weights `spike/evals/` measures and, later,
S2 loads.

Read `docs/NEXT.md` first for *why* these two models and not others. This
file is only the how.

## What public data can and cannot do for you

Tagging is slow, so the first question is how much of it can be skipped.
The honest answer is: most of the *generic* part, none of the *specific*
part.

Two CC-BY-4.0 datasets mirror Roboflow Universe projects on Hugging Face
and need no account (the Universe links themselves return 401
unauthenticated, which is what `spike/evals/PITCH_KEYPOINTS.md` ran into):

| Dataset | Size | What it is |
| --- | --- | --- |
| [`martinjolif/football-pitch-detection`](https://huggingface.co/datasets/martinjolif/football-pitch-detection) | 317 images (255/34/28) | 32 named pitch keypoints, YOLO pose format |
| [`martinjolif/football-ball-detection`](https://huggingface.co/datasets/martinjolif/football-ball-detection) | 1,237 images (989/123/125) | ball boxes, one class |

**The pitch set uses exactly this repository's 32-vertex order.** That was
checked, not assumed: its `flip_idx` is the permutation a mirror about the
halfway line induces, and that permutation is a property of the vertex
order alone, so comparing it against `pitch_keypoints.py:VERTICES` tests
that their index *i* and our index *i* name the same point. They do, at
all 32. `fetch_public.py` re-runs that check on every download and refuses
to continue if upstream ever reorders, because pretraining on a silently
permuted layout would teach the wrong names while still reporting a small
reprojection error — the exact failure mode `PITCH_KEYPOINTS.md` measured.

**What this does not fix.** Both datasets are broadcast footage: stadium
cameras near the halfway line, full-size pitches, crisp paint, mown
stripes. Community weights trained on precisely this data were run on both
Veo exports and put keypoints in the sky. So pretraining buys the backbone
an understanding of what a penalty-box corner *is*; it cannot teach it
what faint paint on worn olive turf looks like from a follow-cam. That
part is yours and there is no dataset of it anywhere, because nobody else
films this pitch.

What it changes in practice is the number of frames you have to tag —
plausibly the low hundreds instead of the low thousands — not whether you
tag at all.

---

## 1. Machine setup

Once per machine.

```bash
nvidia-smi                       # driver is alive, and note the VRAM figure
git clone https://github.com/dawsboss/Sports-Camera-Analytics-.git
cd Sports-Camera-Analytics-
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,eval]"     # eval pulls torch, torchvision, ultralytics
pip install huggingface_hub
```

Then confirm the GPU is actually visible to torch, because a CPU-only
torch installs without complaint and trains roughly a hundred times
slower rather than failing:

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

If that prints `False`, your torch is the CPU build. Reinstall it with the
CUDA wheel your driver supports, picking the command from
<https://pytorch.org/get-started/locally/> — the right CUDA version
depends on your driver, so take it from the selector rather than from
here.

On Windows, do all of this inside WSL2 with the NVIDIA WSL driver. Native
Windows works but the paths in every command below assume a POSIX shell.

## 2. Fetch the public datasets

```bash
python spike/labels/fetch_public.py --out data/public
```

Roughly 400 MB. It writes a `sideline.yaml` next to each dataset with
absolute paths, which is what Ultralytics wants, and prints the keypoint
order check for the pitch set.

## 3. Get your own labels and footage onto the machine

The tags live in git; the video never does.

- **Tags**: `spike/labels/tags.json` is already in the repo. To add more,
  tag on your phone at `web/label.html`, hit **Copy tags out**, and
  replace the file. It is coordinates only, so it is safe to commit.
- **Video**: download each Veo export's `standard/machine` rendition, or
  copy it off the homelab's MinIO. Note the path of each; you will pass
  them as `MATCHID=PATH`. `docs/NEXT.md` has the retention argument.

## 4. Build the training set

```bash
python spike/labels/build_dataset.py \
    --tags spike/labels/tags.json \
    --video 20260919-flight=/path/to/flight.mp4 \
    --video 20260920-future=/path/to/future.mp4 \
    --out data/ours \
    --holdout 20260920-future
```

This cuts the tagged frames out of the videos and writes
`data/ours/ball/` and `data/ours/pitch/` in YOLO layout, each with a
`data.yaml`. Frames tagged "ball not visible" become empty label files on
purpose — those are what stop a detector firing on a corner flag.

It prints a per-match count. If it says `nothing held out`, stop: a score
measured on the match you trained on tells you nothing, and as of today
only `20260919-flight` is tagged, so there is nothing to hold out yet.
Tagging the second match is the prerequisite for every number below
meaning anything.

## 5. Train

Two stages. Stage one learns football; stage two learns *your* football.
Skipping stage one with a hundred-odd frames of your own will overfit.

### Ball

```bash
# stage 1 — public broadcast data
yolo detect train model=yolo11s.pt data=data/public/ball/data/sideline.yaml \
    imgsz=1920 epochs=60 batch=-1 project=runs name=ball_pre

# stage 2 — fine-tune on this footage, low LR so stage 1 is not erased
yolo detect train model=runs/ball_pre/weights/best.pt \
    data=data/ours/ball/data.yaml \
    imgsz=1920 epochs=80 batch=-1 lr0=0.001 project=runs name=ball_ft
```

`imgsz=1920` is the one setting not to economise on. The ball measured
11 px across at 1080p; at `imgsz=640` it is under four pixels and there is
nothing left to detect. If you run out of memory, drop `batch`, never
`imgsz`.

### Pitch keypoints

```bash
yolo pose train model=yolo11s-pose.pt data=data/public/pitch/data/sideline.yaml \
    imgsz=1280 epochs=200 batch=-1 project=runs name=pitch_pre

yolo pose train model=runs/pitch_pre/weights/best.pt \
    data=data/ours/pitch/data.yaml \
    imgsz=1280 epochs=200 batch=-1 lr0=0.001 fliplr=0.0 project=runs name=pitch_ft
```

`fliplr=0.0` on stage two, and only on stage two. The public `data.yaml`
carries a `flip_idx`, so Ultralytics permutes the keypoint names when it
mirrors an image and the augmentation is sound. `build_dataset.py` does
not write a `flip_idx`, so mirroring our frames would keep the old names
on a mirrored pitch and teach the model that a left corner is a right one.
See the note at the end of this file — this is worth changing.

`batch=-1` lets Ultralytics size the batch to about 60% of your VRAM. If
you would rather pin it:

| VRAM | ball @ 1920 | pitch @ 1280 |
| --- | --- | --- |
| 8 GB | `batch=2` | `batch=4` |
| 12 GB | `batch=4` | `batch=8` |
| 16 GB | `batch=6` | `batch=12` |
| 24 GB | `batch=10` | `batch=20` |

Starting points, not measurements — watch `nvidia-smi` on the first epoch
and adjust. `cache=ram` speeds things up markedly if the dataset fits.

## 6. Measure, on a match nothing was trained on

This is the only step that produces a number worth quoting.

```bash
python spike/evals/ball_recall.py \
    --video /path/to/held-out.mp4 \
    --weights runs/ball_ft/weights/best.pt \
    --bursts 6 --burst 40

python spike/evals/pitch_keypoints.py \
    --weights runs/pitch_ft/weights/best.pt \
    --video /path/to/held-out.mp4 \
    --out out/pitch --pitch 100 64
```

`ball_recall.py` samples *bursts* of consecutive frames rather than
scattered ones, because the question is not "what fraction of frames" but
"how long are the blackouts" — a tracker bridges a two-sample gap and not
a fifteen-sample one. The baseline to beat, from a COCO model: 43% on the
worn olive field, 73% on the green one, worst gap 15 samples. Fine-tuning's
job is that tail.

For the pitch, **look at the overlays in `out/pitch`**. Reprojection error
is measured against the model's own points, so a confidently wrong fit
reports 2-6 px and is still wrong. The overlay is the honest check.

Pass the real pitch dimensions with `--pitch LENGTH WIDTH`. Neither field
is 105 x 68 and neither has been measured, which is a known gap: until
someone walks the touchline with a tape, every metre figure downstream
carries that error.

## Things that will go wrong

- **`torch.cuda.is_available()` is False.** CPU-only torch. Section 1.
- **CUDA out of memory.** Lower `batch`. For the ball, never `imgsz`.
- **mAP near 1.0 on stage two.** With ~90 boxes from one match this means
  the val split is the train split. Check step 4 printed a non-zero val
  count.
- **Great numbers, useless model.** You measured on the trained match.
  `--holdout` exists for this.
- **Pitch keypoints land in the sky.** The signature of a model outside
  its training distribution, and exactly what the community weights did.
  Try the other `imgsz`; if it persists, stage two needs more frames.

## An open question worth resolving before a long run

`build_dataset.py` disables horizontal flips for the pitch, reasoning that
a mirrored pitch is a valid pitch with every left/right name swapped. That
is correct as the file stands, because it writes no `flip_idx`. But the
public dataset ships one, and it was verified above to match our vertex
order — so writing the same twelve-element line into our generated
`data.yaml` would make `fliplr` safe here too.

That is not a small detail: the current tags cover the left goal in nine
frames and the right in five, vertices 16 and 21 are untagged entirely,
and mirroring is the cheapest way to even that out without tagging
anything. The counter-argument is `camera_side` — but a mirror about the
halfway line leaves the camera where it is and preserves near and far
touchlines, so it does not reintroduce that ambiguity. Worth deciding
deliberately rather than inheriting.
