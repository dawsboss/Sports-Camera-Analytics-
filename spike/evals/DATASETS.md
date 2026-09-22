# Public datasets: what replaces tagging, and what does not

Prompted by a straight question — is there an existing labelled dataset
we can train on instead of tagging our own frames? — and by a suggestion
to auto-annotate our frames with community models and hand-correct only
the failures.

**This document is a survey, not a measurement.** Every other file in
this directory reports numbers produced by code in this repository run on
our footage. Nothing here was. The two times community weights *were* run
on our footage (`PITCH_KEYPOINTS.md`) they were confidently wrong, so
treat every row below as a hypothesis until `bench.py` or
`ball_recall.py` has scored it.

## The short answer

| | Public data enough? | What is still ours to tag |
| --- | --- | --- |
| Players | Already solved off the shelf | nothing |
| Pitch keypoints | **Mostly yes** — tens of thousands of labelled frames exist on a layout we can convert to | a hundred or two frames of worn olive turf, for domain shift |
| Ball | **No** | the worn-turf frames, and there is no way around it |

The asymmetry has a reason, and it is our own measurement: field
condition dominates ball detection (43% on the worn olive field, 73% on
the green one, same camera and same model). Every public ball dataset is
professional turf, which is the 73% case we already pass. Pretraining on
it raises the average and does nothing for the blackout tail, which is
the only thing that matters — see `docs/NEXT.md`.

## Pitch lines and keypoints: pretraining is free and large

| Dataset | What it is | Size | Licence | Use here |
| --- | --- | --- | --- | --- |
| [SoccerNet calibration-2023](https://github.com/SoccerNet/sn-calibration) | broadcast frames, every line and circle segment as a named polyline | ~25.5k images, ~226k polylines | research terms; the extracted frames need no NDA (see `README.md` here) | **the pretraining set.** Already downloadable by `soccernet.py` |
| [roboflow football-field-detection-f07vi](https://universe.roboflow.com/roboflow-jvuqo/football-field-detection-f07vi) | the 32 named vertices, TV frames | 317 images | CC BY 4.0 | the layout, which `pitch_keypoints.py` already carries verbatim. Not the volume |
| [SoccerTrack v2](https://atomscott.github.io/SoccerTrack-v2/) | 10 **university amateur** matches, full-pitch panoramic rigs, with calibration keypoints and remap arrays | ~900 min, 4K | CC BY 4.0 | the closest public footage to the *static* product this repo is actually for. No ball |
| [TeamTrack](https://atomscott.github.io/TeamTrack/) | amateur full-pitch, drone top view and fisheye side view | >4M player boxes | see repo | player tracking and pitch-coordinate work on fixed cameras |
| [SoccerNet-GSR](https://github.com/SoccerNet/sn-gamestate) | 200 clips, 9.37M line points for pitch localisation, 2.36M athlete positions with role/team/number | 200 × 30 s | research terms | identity and team assignment (S4/S6) more than registration |

**The conversion from SoccerNet's lines to the 32 vertices is
arithmetic, not annotation.** Their annotation names every segment ("Big
rect. left main", "Middle line", "Circle central"); a roboflow vertex is
the intersection of two named segments or a named circle-line tangency.
So ~25k labelled frames become ~25k keypoint frames deterministically,
which is roughly eighty times the roboflow set and removes the argument
for hunting Universe for more.

What that pretraining does **not** buy: every one of these except
SoccerTrack v2 is pristine professional turf under stadium light. That is
exactly the domain the unmodified SoccerNet baseline already handles on
our footage (`README.md`, second round) and exactly not our gap. The
prior it teaches is "what a painted pitch line is, and that the sky is
not one". The worn-paint-on-olive-grass prior is ours to supply, and it
is small: that is what `web/label.html` is for.

## The ball: nothing public covers it

What exists, and why each one is not it:

- **SoccerNet-GSR** removed the ball from the dataset on purpose (it
  spends too much of the match in the air to annotate cleanly).
- **SoccerTrack v1/v2, TeamTrack** — player boxes and GNSS/pitch
  coordinates. No ball boxes.
- **SoccerNet ball action spotting** (and Team BAS, 2025) — timestamps
  of on-ball events, 12 classes. Temporal labels, not boxes. Useful much
  later for the restart work in `docs/NEXT.md`, useless for a detector.
- **SoccerNet-v3** — ~27k bounding-box instances including ball, flag and
  cards, on broadcast replay frames.
- **SoccerDB** — player and ball boxes, broadcast.
- **[ISSIA-CNR](https://www.issia.cnr.it/)** — six synchronised static
  Full-HD cameras, ~20k annotated frames, ball as a centre *point* (others
  have since fitted boxes to it). This is the one static-camera ball set
  worth knowing about, and the pitch is Serie A.

So: professional turf, pristine paint, broadcast or stadium rigs. A ball
detector pretrained on all of it still meets a white ball on patchy brown
grass for the first time on our footage.

**The architecture is the more interesting borrow.** Our ball is 11 px
across. A box regressor is a strange tool for that, and the small-ball
literature says so:

- [WASB](https://github.com/nttcom/WASB-SBDT) (BMVC 2023) — one baseline
  across five sports, high-resolution heatmap over a short stack of
  consecutive frames plus a temporal-consistency step at inference.
  Weights released.
- [FootAndBall](https://github.com/jac99/FootAndBall) and DeepBall —
  players and a few-pixel ball from static cameras, trained on ISSIA.
  Built for exactly this pixel budget and exactly this camera.

A heatmap over consecutive frames also matches what we measured: the
misses come in runs of one or two samples surrounded by hits, which is
information a per-frame detector throws away. **Worth one evaluation with
`ball_recall.py`, over the same bursts, before an evening of tagging for
YOLO** — the number has to be comparable to 43% / 73% or it means
nothing.

## Why "auto-annotate, then fix the failures" does not work here

It is the standard advice and it is already measured against this
footage, in both places it would apply:

- **Pitch keypoints.** Two community 32-vertex models put keypoints in
  the sky on the worn field, and a penalty box on open grass on the green
  one, each reporting a 2-6 px reprojection error because the error is
  measured against their own points (`PITCH_KEYPOINTS.md`). A confidently
  wrong label costs more to find and delete than a fresh tap costs to
  place.
- **Ball.** A fine-tuned soccer-ball model from Hugging Face fired on 3
  frames of 12, with boxes 33-59 px wide where the ball is 11
  (`docs/NEXT.md`). Those are not corrections, they are a different
  object.

The structural reason, which is worth stating because it will come up
again: **auto-labelling works where the model already works, and the
frames worth labelling are the ones where it does not.** On the green
field the COCO model is right 73% of the time and those labels are nearly
free — and nearly worthless, because that match is not the problem. On
the worn field it is right 43%, and it contributes nothing at all to the
8-to-15-sample blackouts that are the entire target. The yield of
auto-annotation is anti-correlated with the value of the label.

One piece of that advice is actively wrong for this problem: sampling
frames at 1 fps to avoid near-duplicates. Near-duplicates are the
*point*. `ball_recall.py` exists because the first measurement here
sampled scattered frames and could not answer whether a tracker bridges
the misses. A training set drawn at 1 fps over-represents the easy frames
and never shows the model a blackout.

## What actually cuts the tagging

In descending order of frames saved per hour of work:

1. **Pretrain the keypoint model on SoccerNet calibration, converted to
   the 32 vertices.** Turns step 2 of `docs/NEXT.md` from "tag several
   hundred frames" into "tag enough worn-field frames to shift a domain",
   which is a much smaller number.
2. **Tracker-assisted ball tagging.** The median gap between detections
   is one to two samples, so a tag propagated forward and backward with
   an off-the-shelf tracker covers a burst from a handful of taps. Unlike
   model auto-labelling, propagation fails *visibly* — it drifts off the
   ball and you can see it — rather than confidently.
3. **Hard negatives for free.** Run the COCO detector over the frames
   already tagged "ball not visible" and keep every detection it makes as
   a negative. No tapping at all, and it is precisely the corner
   flag / white sock / linesman's shirt class that `build_dataset.py`'s
   empty label files exist to teach.
4. **Copy-paste augmentation.** The ball is found 73% of the time on the
   green field. Crop those instances, paste them onto worn-field frames
   at the measured 6-39 px scale with matching motion blur. It
   manufactures the hard case out of footage that is already labelled.
5. **More footage from YouTube is not the constraint.** We have ~180
   minutes of exactly the right footage and have tagged almost none of
   it; the shortage is labels, not video, and other people's matches come
   with somebody else's licence. The one exception is the spec's third,
   held-out match — and a match the club records itself, with stints, a
   first-whistle timestamp and measured pitch dimensions, is worth more
   than a stranger's video with none of those.

## Before using any of it

- **Licence.** roboflow's field-detection set and SoccerTrack v2 are
  CC BY 4.0, which is clean. SoccerNet's terms are research-oriented and
  its raw match videos carry an NDA that the extracted, annotated frames
  do not. If this ever stops being a hobby, re-check before shipping
  weights trained on them.
- **Do not redistribute any of it from this repository**, same rule as
  the calibration set in `README.md`.
- **Measure before believing.** Every claim above is somebody else's
  paper or dataset card. The bar in this directory is a number from our
  own footage, and none of these have one yet.
