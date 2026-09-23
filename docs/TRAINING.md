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

**A third source is eighty times larger, and worth the afternoon it
costs.** [SoccerNet calibration-2023](https://github.com/SoccerNet/sn-calibration)
is ~25.5k broadcast images in which every line and circle segment is
annotated as a *named* polyline, and `spike/evals/soccernet.py` already
downloads it. It is not in YOLO pose format and there is no converter
here yet, but most of the conversion is arithmetic rather than
annotation: a named vertex is the intersection of two named segments.
"corner L-top" is "Side line left" meeting "Side line top"; "box L-top"
is "Big rect. left top" meeting "Big rect. left main"; "circle top" is
"Middle line" meeting "Circle central".

Four of the 32 do not come out that way. The two penalty spots (8, 21)
are not lines, so SoccerNet does not annotate them at all, and the two
circle extremes (30, 31) are not on any line either and need an ellipse
fit to the centre circle. Write those four as `0 0 0` — the pose format
already means "not labelled here" by that, `build_dataset.py` relies on
it, and our own tags can teach them. Twenty-eight named points on 25.5k
frames is a far better stage one than 32 points on 317.

**What this does not fix.** All of these datasets are broadcast footage: stadium
cameras near the halfway line, full-size pitches, crisp paint, mown
stripes. Community weights trained on precisely this data were run on both
Veo exports and put keypoints in the sky. So pretraining buys the backbone
an understanding of what a penalty-box corner *is*; it cannot teach it
what faint paint on worn olive turf looks like from a follow-cam. That
part is yours and there is no dataset of it anywhere, because nobody else
films this pitch.

**And the ball half is worse than the pitch half.** No public dataset
anywhere has a small white ball on worn olive turf. SoccerNet's game
state set removed the ball on purpose; SoccerTrack and TeamTrack — the
two amateur, full-pitch, fixed-camera datasets, which are otherwise the
closest public footage to what this product is actually for — annotate
players only; ball action spotting is timestamps, not boxes; ISSIA is a
static-camera ball set on a Serie A pitch. Every one of them is
professional turf, which is the 73% case we already pass. The 43% case
is ours alone. `spike/evals/DATASETS.md` is the full survey.

One thing to check on `football-ball-detection` before trusting it as
stage one: **what size are its balls?** Ours is 11 px across at 1080p.
If its median box is 40 px, stage one teaches a scale prior that stage
two then has to unlearn, which is worse than no stage one.

```bash
python - <<'EOF'
from pathlib import Path
w = [float(l.split()[3]) for f in Path("data/public/ball/data").rglob("labels/*/*.txt")
     for l in f.read_text().splitlines() if l.strip()]
w.sort(); print(len(w), "boxes; median width", round(w[len(w)//2]*1920, 1), "px at 1920")
EOF
```

If that comes back much above ~15 px, train stage one at a smaller
`imgsz` so its balls land near ours in pixels, rather than at the same
1920 as stage two. Matching the pixel size matters more than matching
the resolution.

What public data changes in practice is the number of frames you have to
tag — plausibly the low hundreds instead of the low thousands — not
whether you tag at all.

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

## 5. Multiply the labels you already have

Tagging is the expensive input, so before booking another evening of it,
spend an hour on the three things that turn tags you already placed into
more training rows. All three are scripts that do not exist yet; they are
worth writing in this order.

1. **Propagate each tag across its burst.** The ball moves smoothly and
   the median detection gap is one to two samples, so a single tagged
   frame plus an off-the-shelf tracker run forward and backward covers
   the ten frames around it. Then eyeball the strip and delete what
   drifted. This is the one multiplier that works on the *worn* match,
   which is the footage that matters, and unlike model auto-labelling it
   fails visibly: a tracker that loses the ball wanders off it in a way
   you can see at a glance, where a wrong detector box looks exactly like
   a right one.
2. **Mine hard negatives for free.** `build_dataset.py` already writes an
   empty label file for every frame tagged "ball not visible", and an
   empty file is the strongest teaching signal there is for what is *not*
   a ball. So tag "not visible" generously — it costs one tap and no
   precision — and lean on frames containing corner flags, white socks, a
   linesman's shirt and the Veo watermark.
3. **Paste the ball onto the turf that hides it.** The COCO model finds
   the ball 73% of the time on the green field and 43% on the olive one.
   Crop the instances it finds on green, paste them into worn-field
   frames at the measured 6-39 px range with matching motion blur, and
   you have manufactured the exact case the detector fails, out of
   footage that is already labelled. Keep these out of the val split.

What *not* to do, because it was already measured and failed: run a
community model over the frames and hand-correct its output. Both
32-keypoint models put keypoints in the sky, and the fine-tuned ball
model from Hugging Face drew 33-59 px boxes around an 11 px ball
(`spike/evals/PITCH_KEYPOINTS.md`, `docs/NEXT.md`). The general reason is
worth keeping in mind whenever this comes up again: **auto-labelling
yields labels where the model already works, and the frames worth
labelling are the ones where it does not.** Its hit rate is
anti-correlated with the value of the label.

For the same reason, if you ever do pseudo-label, do it only where an
*independent* signal agrees — a detection that a tracker's prediction
from both neighbouring frames also lands on. Confidence alone is not that
signal; every wrong fit measured here was confident.

## 6. Train

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
    imgsz=1920 epochs=80 batch=-1 lr0=0.001 patience=20 \
    mosaic=0.0 scale=0.2 project=runs name=ball_ft
```

`imgsz=1920` is the one setting not to economise on. The ball measured
11 px across at 1080p; at `imgsz=640` it is under four pixels and there is
nothing left to detect. If you run out of memory, drop `batch`, never
`imgsz`.

**`mosaic=0.0` and `scale=0.2` are there for the same reason, and both
defaults work against an 11 px object.** Mosaic tiles four images into
one frame of `imgsz`, so every object it produces is about half its true
linear size: an 11 px ball trains as a 5 px ball for the majority of
epochs. `scale` defaults to 0.5, meaning a random resize anywhere in
[0.5, 1.5], which shrinks it to 5 px again on the low side. Neither is
a problem for a 60 px person and both are fatal for this class. Put
`mosaic=0.0` on stage one too, for the same reason.

**Do not reach for `freeze`.** The reflex with ninety boxes is to freeze
the backbone so a tiny fine-tuning set cannot wreck it. That protects the
wrong thing here: our domain shift is low-level — olive grass instead of
green, faint paint, compression — and low-level is exactly what the early
layers hold. Freezing them freezes the part that most needs to move. The
low `lr0` plus `patience` is the right guard against overfitting on this
much data.

These are reasoned settings, not measured ones. Nothing in this section
has been run against the footage yet; the first person to train should
record what actually happened in `spike/evals/`.

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
Keep `fliplr=0.0` until that line is actually written, because the failure
is silent — the last section of this file has the line to paste and the
argument for pasting it.

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

## 7. Measure, on a match nothing was trained on

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

### What to hold out, when you only have two matches

`--holdout` takes whole matches, which is the right unit — frames from
one match share a field, a light and a camera placement, so a random
frame split would leak all three. But with two matches it forces a choice
that is worse than it looks:

- **Hold out the green field** (what the command above does) and you
  train on worn turf and measure on easy turf. The number will look good
  and will say nothing about the blackout tail, which only exists on the
  field you just trained on.
- **Hold out the worn field** and you have no worn-turf training data at
  all, which is the entire reason for fine-tuning.

Until a third match exists, split the *worn* match by time instead: tag
bursts from the first half, hold out bursts from the second. Different
passages of play, different sun angle, different end of the pitch —
weaker than a separate match, and far more informative than either choice
above. That needs a `--holdout-window MATCHID=START:END` in
`build_dataset.py`, which is not written yet. Keep the green match whole
and untouched meanwhile, as the closest thing we have to the spec's
third, never-trained-on match.

### Quote the gap distribution, not mAP

mAP50 on an 11 px object is mostly a measure of the box. A detection six
pixels off centre scores an IoU near zero and is a perfectly good ball
for everything downstream — possession is "whose nearest player", a
restart is "the ball was still, then it was not". Neither needs the box
to be tight.

So the number to report is the one `ball_recall.py` reports: the fraction
of samples in which the ball was found, and the **distribution of gap
lengths** across consecutive samples, worst case included. The target is
not a higher average, it is no gap longer than a tracker can bridge. The
baseline to beat is 43% / 73% with a worst gap of 15 samples.

## An experiment worth an evening before the next tagging session

Everything above assumes a YOLO detector, and that assumption is worth
one cheap test first, because it decides how many frames you need to tag.

Our ball is 11 px. The small-ball literature does not use box regressors
for that: [WASB](https://github.com/nttcom/WASB-SBDT) (one baseline
across five sports, weights released),
[FootAndBall](https://github.com/jac99/FootAndBall) and DeepBall all
predict a **heatmap over a short stack of consecutive frames** and then
enforce temporal consistency — FootAndBall and DeepBall were built for
static cameras and a ball a few pixels across, which is this problem
exactly. And a stack of frames uses the structure we measured and a
per-frame detector throws away: the misses come in short runs surrounded
by hits.

The test is not a training run. Take WASB's released soccer weights, run
them through `ball_recall.py` over the same bursts, and compare against
43% / 73% cold. An evening, no labels. If a released small-ball model
beats a COCO detector on the worn field before any fine-tuning, the ball
plan changes shape and the tagging target shrinks; if it does not, you
have lost one evening and gained a number worth writing down.

If you stay with YOLO, the equivalent lever is an extra high-resolution
detection head (a P2 model config). It costs memory and there is no
pretrained checkpoint for it, so it competes with `imgsz=1920` for the
same VRAM — worth trying only if 1920 is already at the limit of the
card.

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

## The open question, answered: write the `flip_idx`

Yes, write it, and get the value from our own vertex table rather than by
copying upstream's — then upstream agreeing with it is a check rather
than an assumption.

A horizontal image flip is a reflection of the world about a vertical
plane through the camera. It moves the camera to a mirrored position on
the *same* touchline and leaves near and far touchlines where they were,
so `camera_side` is untouched and the instinct in `build_dataset.py`'s
comment is right: the only thing that changes is that every pitch
coordinate is mirrored about the halfway line, `x -> LENGTH - x`. Applied
to `pitch_keypoints.py:VERTICES` that is a permutation of the 32 indices,
and it computes to:

```python
# 0-based, which is what Ultralytics wants in data.yaml.
FLIP_IDX = [24, 25, 26, 27, 28, 29, 22, 23, 21, 17, 18, 19, 20, 13, 14, 15,
            16, 9, 10, 11, 12, 8, 6, 7, 0, 1, 2, 3, 4, 5, 31, 30]
```

It is its own inverse, which is worth asserting next to it in
`build_dataset.py`: a mirror applied twice is the identity, so a typo in
the list almost certainly breaks that and gets caught for free.

**One limit before you count on it.** Four vertices map to themselves —
13 "halfway top", 14 "circle top", 15 "circle bot", 16 "halfway bot",
which are exactly the ones standing on the halfway line. Mirroring
doubles the coverage of every left/right pair, so it does fix the
nine-frames-left against five-frames-right imbalance, but it cannot
create a single new sample for those four. If the vertex that is
"untagged entirely" is index 16, no augmentation will reach it and only
tagging will.

So: write `flip_idx` into the pose `data.yaml` that `build_dataset.py`
generates, drop `fliplr=0.0` from stage two and from the command it
prints, and leave the ball alone — one symmetric class needs no
permutation, so `fliplr` was always safe there.
