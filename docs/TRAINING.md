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

**Measured: it does not.** Every image in the set is 1920×1080 with one
ball, and the 1,237 boxes have a median width of 11.7 px (10th percentile
8.3, 90th 17.4) against our 11. Stage one trains at `imgsz=1920` as is.

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

To run `pytest` on the training machine as well, add the `service` extra:
`pip install -e ".[dev,eval,service]"`. The API tests import FastAPI.

### Native Windows

Native Windows works, and is what the first training box ran: an RTX 3090
Ti (24 GB), driver 591.86, Python 3.13, no WSL. Install torch *before* the
project so pip keeps the CUDA build, and take the CUDA version from
`nvidia-smi`'s header (13.1 there, so `cu130`):

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.venv/Scripts/python -m pip install -e ".[dev,eval,service]" huggingface_hub
```

Every command below runs as written from Git Bash with `.venv/Scripts/`
in front of `python` and `yolo`. What differs from Linux:

- **A card that is also a desktop shares its VRAM with the desktop**, and
  when training asks for more than is free, the Windows driver pages it
  into system RAM instead of failing. Nothing errors; training just runs
  four times slower. Watch the first epoch: if the memory Ultralytics
  prints is within a gigabyte or two of the card's total, lower `batch`.
  The batch table in section 6 is what fitted here.
- **Do not pass `cache=ram` on Windows.** Dataloader workers there are
  spawned processes, not forks, and each can hold its own copy of the
  cache: 5.8 GB for the public ball set, times up to eight train workers
  and sixteen val workers. The first run here was stopped at epoch 41 of
  60 for running a 64 GB machine out of memory with it on. It bought
  nothing either: at `batch=6` and 1920 px the GPU is the bottleneck, and
  four workers decode JPEGs far faster than it consumes them. Use the
  default (no cache) and `workers=4`. Even so, budget about 24 GB of RAM
  for a run: every spawned worker imports torch, and one ball run is
  fifteen Python processes (four train workers, eight val, and their
  parents).
- **Stopping a run** means stopping its dataloader workers too; they are
  separate `python.exe` processes. Stop the `yolo.exe` process and its
  children.
- **Resuming** an interrupted run continues from its last epoch, and
  `cache` and `workers` can be overridden on the way:
  `yolo detect train resume model=runs/detect/ball_pre/weights/last.pt workers=4 cache=False`.

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
  them as `MATCHID=PATH`. `docs/NEXT.md` has the retention argument. The
  URLs of the two tagged matches are in `web/label.html`'s `MATCHES`; each
  is ~2.3 GB and arrives in under a minute:

  ```bash
  mkdir -p data/videos
  curl -L -o data/videos/20260919-flight.mp4 "<url from MATCHES>"
  ```

  `data/` is gitignored, which is what keeps the video out of git.

## 4. Build the training set

```bash
python spike/labels/build_dataset.py \
    --tags spike/labels/tags.json \
    --video 20260919-flight=data/videos/20260919-flight.mp4 \
    --out data/ours \
    --holdout-window 20260919-flight=50:
```

This cuts the tagged frames out of the videos and writes
`data/ours/ball/` and `data/ours/pitch/` in YOLO layout, each with a
`data.yaml`. Frames tagged "ball not visible" become empty label files on
purpose — those are what stop a detector firing on a corner flag. Each
run clears the `images/` and `labels/` it wrote last time, so a frame
cannot keep an old split when the holdout changes.

It prints a per-match count. If it says `nothing held out`, stop: a score
measured on footage you trained on tells you nothing. While only
`20260919-flight` is tagged, `--holdout-window` holds out a stretch of it
(minutes of video, `50:` is 50 minutes to the end); why that and not the
other match is under "What to hold out" below. Tags within ten seconds of
the window's edge are dropped from both sides, so a burst the edge cuts
in two cannot put near-identical frames in train and val. Once a second
worn-field match is tagged, use `--holdout MATCHID` for the whole match
instead.

**Ball tags from before the tagger's fix are cut one frame earlier than
their key, about half the time, and that is right.** The tagger keyed each
tag `Math.round(currentTime × 29.97)`, but a paused video shows the frame
whose interval contains `currentTime`: the floor, at the true 30000/1001.
Past mid-frame the key named the frame *after* the one tapped. On a slow
passage that is invisible; on a fast zoomed pan it put the ball 10–18 px
outside its 22 px box. Ball tags carry the time as `t`, so the builder
cuts `floor(t × fps)` instead, which was the best-matching frame for 29 of
the 32 tags where a detector could tell the frames apart. It reports how
many it moved. Pitch tags carry no time and keep their key, at most a
frame late on a mostly still pitch. The tagger now keys by the floor, so
new tags need no correction.

**Every ball tag becomes a 22 px box**, because a tag is a tap and has
no size. Right for the 11 px ball the static camera and a distant
follow-cam shot see; wrong for a zoomed follow-cam shot, where the ball
is 30–40 px and the label is its middle. Models fine-tuned on these
tags draw 22 px boxes and nothing else. A size gesture in the tagger — a
second tap on the ball's edge — is the fix, and it is not built yet.

## 5. Multiply the labels you already have

Tagging is the expensive input, so before booking another evening of it,
spend an hour on the three things that turn tags you already placed into
more training rows. The first exists as `spike/labels/propagate.py`; the
other two are not written yet. They are worth doing in this order.

1. **Propagate each tag across its burst.** The ball moves smoothly and
   the median detection gap is one to two samples, so a single tagged
   frame plus an off-the-shelf tracker run forward and backward covers
   the ten frames around it. Then eyeball the strip and delete what
   drifted. This is the one multiplier that works on the *worn* match,
   which is the footage that matters, and unlike model auto-labelling it
   fails visibly: a tracker that loses the ball wanders off it in a way
   you can see at a glance, where a wrong detector box looks exactly like
   a right one.

   `propagate.py` does the conservative version: it fills only the five
   frames *between* two tapped neighbours, matching the ball forward from
   one tap and backward from the other, and keeps a frame only where the
   two agree within 3 px. It writes a new tags file with the additions
   marked `"src": "prop"`, and `build_dataset.py` keeps those out of val.
   On the first export it filled 107 of 400 frames, mostly on slow
   passages; fast zoomed passes rarely agree, which is the point. Two of
   the 107 were wrong in a way agreement cannot catch — both taps off in
   the same direction, so both matches agreed on the same wrong spot —
   and only the `--sheet` showed it. Look at every crop.

   ```bash
   python spike/labels/propagate.py --tags spike/labels/tags.json \
       --video 20260919-flight=data/videos/20260919-flight.mp4 \
       --out data/ours/tags_prop.json --sheet out/prop.jpg
   # delete the keys that drifted from data/ours/tags_prop.json, then
   python spike/labels/build_dataset.py --tags data/ours/tags_prop.json ...
   ```
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
    imgsz=1920 epochs=60 batch=6 mosaic=0.0 scale=0.2 workers=4 name=ball_pre

# stage 2 — fine-tune on this footage, low LR so stage 1 is not erased
yolo detect train model=runs/detect/ball_pre/weights/best.pt \
    data=data/ours/ball/data.yaml \
    imgsz=1920 epochs=80 batch=6 nbs=6 optimizer=AdamW lr0=0.0005 \
    warmup_epochs=0 patience=20 mosaic=0.0 scale=0.2 name=ball_ft
```

Weights land in `runs/detect/<name>/` and `runs/pose/<name>/`. Do not pass
`project=runs`: Ultralytics 8.4 puts a relative project *under* its runs
directory, so it becomes `runs/detect/runs/<name>/`.

**Three defaults quietly undo a fine-tune on a hundred frames**, and none
of them warns:

- `optimizer=auto`, the default, **ignores `lr0`** and picks its own. On
  stage one it chose AdamW at 0.002. A stage-two `lr0` only takes effect
  with the optimizer named, so name it; 0.0005 is a quarter of what stage
  one ran at.
- `nbs=64` accumulates gradients until it has seen 64 images. With 53
  training images that is about one weight update per epoch, so eighty
  epochs would be roughly seventy steps. `nbs` equal to `batch` makes
  every batch a step.
- Warmup is at least 100 iterations whatever `warmup_epochs` says, with
  the bias learning rate starting at 0.1. On nine batches an epoch that is
  eleven epochs of large bias updates to a model that was already trained.
  `warmup_epochs=0` turns it off.

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

**Measured on the first run, that protects the small ball at the price
of the big one.** Stage one never drew a box outside 7–15 px, the public
set's range. That is right for a distant ball, and it won the dim
passage the COCO model could not see (19 of 20 against 5). But the
follow-cam zooms in on play: COCO's hits on the worn match have a median
of 19 px and reach 40, and there stage one found 16% of samples against
COCO's 63%. For follow-cam footage, widen `scale` or add zoomed frames;
for the static camera, where the ball's size varies only with distance,
this recipe is the right one. The fixed 22 px tag box has the same blind
spot — see section 4.

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
    imgsz=1280 epochs=200 batch=12 workers=4 name=pitch_pre

yolo pose train model=runs/pose/pitch_pre/weights/best.pt \
    data=data/ours/pitch/data.yaml \
    imgsz=1280 epochs=200 batch=10 nbs=10 optimizer=AdamW lr0=0.0005 \
    warmup_epochs=0 patience=50 name=pitch_ft
```

Horizontal flips are on in both stages, because both `data.yaml` files
carry a `flip_idx`: Ultralytics swaps each keypoint for its mirror when it
mirrors a frame. `build_dataset.py` writes ours; the last section of this
file has the argument. Without it, mirroring would keep the old names on
a mirrored pitch and teach the model that a left corner is a right one,
silently, so if a pose `data.yaml` ever lacks the line, pass `fliplr=0.0`.

Pin `batch` rather than passing `-1`. On a 24 GB card that is also
driving a desktop, measured on the first epoch:

| VRAM | ball @ 1920 | pitch @ 1280 |
| --- | --- | --- |
| 24 GB | `batch=6`: 17.9 GB, 40 s an epoch on 989 images. `batch=8` asked for 24 GB, paged into system RAM and ran at a quarter of the speed | `batch=12` |

For a smaller card scale `batch` with the VRAM and check the first epoch's
memory figure against the card's total, not against an error. Leave
`cache` off: training here is GPU-bound, and on Windows `cache=ram` costs
a copy of the dataset per worker (section 1).

## 7. Measure, on a match nothing was trained on

This is the only step that produces a number worth quoting.

```bash
# the held-out tags: is the top detection on the ball?
python spike/evals/ball_on_tags.py --data data/ours/ball --split val \
    --weights runs/detect/ball_ft/weights/best.pt

# a match nothing was tagged on: how long are the blackouts?
python spike/evals/ball_recall.py \
    --video /path/to/held-out.mp4 \
    --weights runs/detect/ball_ft/weights/best.pt \
    --bursts 6 --burst 40 --sheet out/ball_ft_sheet.jpg

python spike/evals/pitch_keypoints.py \
    --weights runs/pose/pitch_ft/weights/best.pt \
    --video /path/to/held-out.mp4 \
    --out out/pitch --pitch 100 64
```

`ball_recall.py` samples *bursts* of consecutive frames rather than
scattered ones, because the question is not "what fraction of frames" but
"how long are the blackouts" — a tracker bridges a two-sample gap and not
a fifteen-sample one. The baseline to beat, from a COCO model: 43% on the
worn olive field, 73% on the green one, worst gap 15 samples. Fine-tuning's
job is that tail.

**But "found" there means something fired, not that it was the ball.** No
tag says where the ball is, so a model that has learned to fire on white
socks scores as well as one that finds the ball, and a fine-tune on ninety
boxes is exactly the model that might. Two guards: `--sheet` writes a crop
of every detection it counted, so look at it; and `ball_on_tags.py` scores
the held-out *tags*, where the question can be asked properly: is the most
confident detection within 20 px of the tap, or is it confidently
somewhere else? The second is worse than a miss, because a tracker will
follow it. It reports gaps within the tagged bursts too.

The same COCO model that finds "43%" of the worn match with
`ball_recall.py` puts its top detection on the tapped ball in 5 of the 40
held-out tags (12%), worst gap 12. That is the honest baseline for the
worn field: the held-out stretch includes a throw-in with the ball held
overhead, a dim passage, and the ball among feet.

**Pick the weights before you look at the held-out score, or report both.**
`best.pt` is the epoch that scored best on the val split, and while the
val split *is* the held-out window, that choice has seen it. `last.pt`
has not. With forty held-out samples the difference is noise more often
than not, but quote `last.pt` alongside it so nobody has to wonder.

For the pitch, **look at the overlays in `out/pitch`**. Reprojection error
is measured against the model's own points, so a confidently wrong fit
reports 2-6 px and is still wrong. The overlay is the honest check, and
where frames are tagged there is a numeric one:

```bash
python spike/evals/pitch_on_tags.py --data data/ours/pitch --split val \
    --weights runs/pose/pitch_ft/weights/best.pt --out out/pitch_tags
```

It counts each tagged vertex as found (named, within 25 px), wrong
(named, more than 60 px off) or not named. On the first run
`pitch_keypoints.py` reported a fitted homography in 11 of 12 frames
while this found 0 of 25 held-out vertices; the overlays sided with the
tags. `--split all` is fair only for a model that trained on none of
the tagged frames, such as the public pretrain.

The public pitch pretrain was still improving at 200 epochs (keypoint
mAP50 0.69 at 160, 0.81 at 200) and takes ten minutes per hundred on
this card; give it 400.

Pass the real pitch dimensions with `--pitch LENGTH WIDTH`. Neither field
is 105 x 68 and neither has been measured, which is a known gap: until
someone walks the touchline with a tape, every metre figure downstream
carries that error.

### What to hold out, when you only have two matches

`--holdout` takes whole matches, which is the right unit — frames from
one match share a field, a light and a camera placement, so a random
frame split would leak all three. But with two matches it forces a choice
that is worse than it looks:

- **Hold out the green field** and you
  train on worn turf and measure on easy turf. The number will look good
  and will say nothing about the blackout tail, which only exists on the
  field you just trained on.
- **Hold out the worn field** and you have no worn-turf training data at
  all, which is the entire reason for fine-tuning.

Until a third match exists, split the *worn* match by time instead: tag
bursts from the first half, hold out bursts from the second. Different
passages of play, different sun angle, different end of the pitch —
weaker than a separate match, and far more informative than either choice
above. That is `--holdout-window MATCHID=START:END` in `build_dataset.py`.
The tags do not split evenly by half — the ball bursts run from 18 to 63
minutes — so the first run held out 50 minutes to the end: 41 of 94 ball
samples and 2 of 12 pitch frames, leaving 53 and 10 to train on. Keep the
green match whole and untouched meanwhile, as the closest thing we have
to the spec's third, never-trained-on match; `ball_recall.py` on it is
the cleanest number available.

**A time split measures generalisation within a match, not to the next
one, and the first run showed the difference.** The ball fine-tune
matched or beat stage one on the worn match's held-out window (21–22 of
40 against 19) and on that match's random bursts (71% found, real balls
on the sheet). On the green match it fired on 88% of samples and hit the
orange match ball in one: the rest were white kit shirts. The worn match
has yellow and black kits, so in its 53 training frames the only white
thing was the ball. Nothing within that match could reveal it. So tag
several matches, with different kits and ball colours, and keep one
whole; `spike/evals/training_2026-09-23.md` has the numbers.

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
- **No error, but an epoch takes four times as long as it should.** On
  Windows the driver pages VRAM into system RAM rather than failing. The
  GPU memory Ultralytics prints is near the card's total and the card
  draws a third of its rated power. Lower `batch`.
- **Stage two changed nothing, or wrecked stage one.** Check the
  `optimizer:` line in the log. If it says `optimizer=auto found, ignoring
  'lr0'`, the learning rate you passed was not used.
- **mAP near 1.0 on stage two.** With ~90 boxes from one match this means
  the val split is the train split. Check step 4 printed a non-zero val
  count.
- **Great numbers, useless model.** You measured on the trained match.
  `--holdout` exists for this.
- **Pitch keypoints land in the sky.** The signature of a model outside
  its training distribution, and exactly what the community weights did.
  Try the other `imgsz`; if it persists, stage two needs more frames.

## Why `build_dataset.py` writes a `flip_idx`

It does now; this is the argument, kept because the failure without it is
silent. The value comes from our own vertex table rather than from
upstream's, so upstream agreeing with it is a check rather than an
assumption, and `tests/test_build_dataset.py` checks it against
`VERTICES` and against `fetch_public.py`'s copy.

A horizontal image flip is a reflection of the world about a vertical
plane through the camera. It moves the camera to a mirrored position on
the *same* touchline and leaves near and far touchlines where they were,
so `camera_side` is untouched: the only thing that changes is that every
pitch coordinate is mirrored about the halfway line, `x -> LENGTH - x`. Applied
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

The ball needs none of this — one symmetric class has nothing to
permute, so `fliplr` was always safe there.
