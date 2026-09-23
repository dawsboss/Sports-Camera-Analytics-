# What the footage says, and what to build next

Written after running the pipeline against two real Veo exports (19 Sept
"Flight" on a worn olive field with a road and a baseball backstop behind
it; 20 Sept "Future" on a green multi-pitch school field), SoccerNet's
labelled broadcast frames, and the synthetic views. Every number below
comes from code in this repository run on that footage; nothing is
estimated. The evaluation scripts are in `spike/evals/`.

## What was measured

### Registration (M1): where the camera points, per frame

| Dataset | Classical detector, first version | Classical detector, now | Pretrained line network (SoccerNet baseline, unmodified) |
| --- | --- | --- | --- |
| Synthetic, five box views, worst error | 0.98 m | 0.59 m | not run |
| Broadcast, 100 labelled frames | 3 right, 5 wrong | 12 right, 9 wrong | finds the lines cleanly on the two controls checked |
| Veo 19 Sept, worn field, 60 frames | 0 registered | 1 registered | finds touchlines, halfway line, centre circle, penalty-box region; also labels the road and the clouds; some names wrong |
| Veo 20 Sept, green field, 60 frames | 0 registered | 0 registered | finds the goal frame and the centre circle; faint touchlines mostly missed |

The classical detector improved on every dataset that has ground truth
and still fails the 70% gate everywhere real. Two things were learned
that carry forward whatever detector is used:

- **Grass colour must be estimated per frame.** A worn September pitch is
  olive-brown (hue 23 on OpenCV's scale); a fixed "green" range passed a
  tenth of the pixels that were certainly pitch. `estimate_grass()` in
  `sideline/registration/lines.py` fixes that and is why line-finding
  works on the worn field at all now.
- **Every plausibility rule that leans on the grass mask assumes a
  stadium.** On an open school field the mask runs to the tree line, so
  "the grass must be in front of the camera" refused every correct fit.
  Rules that assume nothing about the mask let confident wrong fits
  through instead. `spike/evals/bench.py` measures the trade-off on all
  four datasets at once; the numbers are in `FollowCamConfig.plausibility`.

The reason the classical detector cannot get there: every real scene
brings a new thing that passes a "thin, bright, on grass" test and is not
a pitch line. Seen so far: advertising boards, a road, parked cars, a
crowd in folding chairs, the goal net, the Veo watermark, white kits. A
network trained to label pixels *as* "penalty box front" learns what
those are not. The unmodified broadcast-trained baseline already finds
on the worn field what the classical code could not, and its mistakes
(clouds, the road, a halfway line called a side line) are the ordinary
domain shift that fine-tuning on a few hundred frames of this footage
removes. `spike/evals/sn_baseline.py` runs it; note the BatchNorm
epsilon it needs, without which it predicts nothing.

### Detection (M3 feasibility): can a detector see players and the ball?

| | Result on real frames |
| --- | --- |
| Players, off-the-shelf detector (COCO, small model, 1280 px) | 19 to 32 per frame, 55 to 65 px tall, including spectators and coaches on the sideline |
| Ball, largest COCO model at 1920 px, 240 consecutive samples at 5 fps | 19 Sept (worn olive field) **43%**, median gap 2 samples, worst 15 (3.0 s). 20 Sept (green school field) **73%**, median gap 1, worst 8 (1.6 s). Ball measures 11 px across on both, range 6-39 |
| Ball, a fine-tuned soccer-ball model from Hugging Face | 3 frames of 12, boxes 33 to 59 px wide where the ball is 11: not the ball |

Players are solved off the shelf.

**The ball has to be measured over consecutive frames, and the first
attempt here got that wrong.** Frames sampled minutes apart said "1 of 8"
and meant nothing: the question is whether a tracker can carry the ball
across the samples where detection misses, and only neighbouring samples
answer it. Measured properly with `spike/evals/ball_recall.py`, over
sixteen bursts on the two matches:

- **The median gap is one to two samples on both matches.** That is
  bridged by any tracker. Most of the time, the ball is effectively
  tracked.
- **The tail is the problem.** Occasional runs of 8 to 15 samples, which
  is 1.6 to 3 seconds, in which the ball crosses half a pitch. Nothing
  may be drawn across those, and they are what fine-tuning has to close.
- **Field condition dominates, not camera or distance.** The green
  school field gives 73%; the worn olive field, same camera and same
  model, gives 43%. A white ball on green grass is a contrast the model
  already knows; a white ball on patchy brown turf is not. So the worn
  footage is the *valuable* footage to tag, and a model tuned only on
  easy matches will disappoint on exactly the ones that need help.

  *Caveat, found on the first training run:* the 20 Sept match is played
  with an **orange** ball (six of the eight confident detections across
  it; the other two are white spare balls, one lying on the sideline).
  So 73% against 43% is an orange ball on green against a white ball on
  olive, and ball colour is confounded with field condition. It also
  means the green match, as the never-trained-on test, asks a model tuned
  on white-ball tags to transfer colour as well as turf; and that
  `ball_recall.py`'s "found" counts spare balls on the touchline.
  `spike/evals/training_2026-09-23.md`.
- **The ball is 11 px across.** That is small enough to explain the
  misses without anything being wrong, and it sets the box size the
  training set uses (`build_dataset.py` writes 22 px, which the
  measurement confirms rather than guesses).

So the target is not "lift the average". It is "close the blackouts on
worn-turf footage", and that is a narrow, checkable goal.

## What each thing you asked for needs

**Each player as an individual, names assigned later.** Player detection
works today. Tracking through frames (ByteTrack, off the shelf) gives
short track fragments; the spec expects dozens per player per half on
follow-cam footage. Linking fragments into one player uses kit colour
(the two teams here are yellow and black, easy), the minutes app's
stints (eleven candidates per team at any instant, not the whole
roster), and appearance. Registration helps that linking but is not
required for the kit and appearance parts. Names are assigned in the
review UI, once, to a linked track; the pipeline's job is to keep the
fragments of one player together so that is one click, not forty.

**Possession, possession changes, connected passes.** All ball-anchored.
Possession is "which team's nearest player has the ball"; a change is
that flipping; a pass is the ball leaving one player and arriving at a
teammate. None of it needs pitch coordinates, and all of it needs the
ball found reliably. This is blocked on ball detection and on nothing
else.

**Restarts, and counting them.** This is the better target, and it
replaces what was written here before about detecting fouls. Do not try
to detect a foul: a foul is a referee's judgement, announced by a whistle
this footage has no audio for, and play stops for a dozen other reasons
that look identical. Detect the **restart that follows** instead. Every
restart is a visible, physical event, and the ones that matter are:

| Restart | What it looks like | What the count tells a coach |
| --- | --- | --- |
| Throw-in | ball stationary off the touchline, thrown two-handed overhead | how much play dies on the flanks, and who wins the second ball |
| Corner | ball placed in the corner arc | attacking pressure earned, and whether it converts |
| Goal kick | ball placed in the six-yard box | how often the other side turns it over deep |
| Free kick | ball placed still, a wall forms, a whistle precedes it | where fouls are being conceded, without ever detecting a foul |
| Kick-off | ball on the centre spot, both sides in their halves | segments the match and marks every goal |
| Drop ball | rare; referee stands over a stationary ball with one player each side | mostly a stoppage marker |

Three things make this the right shape. A restart is **observable** where
a foul is not. Every restart is **also a possession change**, so one
detector serves both of the things asked for. And the **count per type
per team** is the part a coach acts on — "we conceded fourteen free kicks
in our own half" is a training session, where "there were fourteen fouls"
is not.

What it needs: the ball found and tracked, then a stationary-ball test
(the ball still for a second or two, then struck hard), then *where* on
the pitch that happened, which is what separates a corner from a throw-in
from a goal kick. So it depends on both open items, ball and
registration, and on nothing else. The rough position may be enough:
distinguishing the corner arc from the six-yard box from the touchline
does not need the two-metre accuracy the spec's gate asks for.

The minutes app already records corners, throw-ins, goal kicks, fouls and
keeper claims by hand, with the player attributed. Those taps are the
ground truth this detector is measured against, so they are worth
continuing for that reason alone.

**Possession share in general.** Falls out of possession above.

## Build our own models, or fine-tune someone else's?

Fine-tune, every time. Training a detector from nothing needs tens of
thousands of labelled images and buys nothing here: a pretrained backbone
already knows edges, grass, texture and people, and the only thing it does
not know is what *this* footage looks like. That part is a few hundred to
a few thousand labelled frames, which is an evening with the tagger rather
than a research project.

So the models are borrowed and the data is ours, and the data is the part
that actually matters. Concretely:

| For | Start from | Teach it |
| --- | --- | --- |
| Ball | an Ultralytics YOLO checkpoint | what a 10-25 px ball looks like on this grass, and what is not one |
| Players | nothing, for now | off-the-shelf detection already finds 20-30 a frame |
| Pitch lines | SoccerNet's calibration baseline weights | faint paint on worn olive turf, and that the road is not a touchline |

The one thing worth building from scratch is the labelling loop, because
nobody else's data looks like a youth match on a school field. That is
`web/label.html` and `spike/labels/build_dataset.py`.

Which public datasets can carry the pretraining, and which tagging they
do not remove, is surveyed in `spike/evals/DATASETS.md`. In short:
pitch keypoints can be pretrained on tens of thousands of labelled
broadcast frames that already exist, so the tagging left there is only
domain shift; no public dataset has a ball on worn turf, so that tagging
is ours. That document is a survey and not a measurement, unlike the rest
of `spike/evals/`.

## The camera first (23 September)

After the first training run the question was whether what is left is
simply more tagging, training and tuning. It is not, and the reason is
where the effort has gone rather than how much of it there is.

**Most of it went into a problem the product does not have.**
Registering a panning, zooming virtual camera from the paint in each
frame is what M1 measured, with a classical detector and then with
learned ones. Every field brings its own paint and its own confusers —
boards, a road, cars, white kits — which is why each new field feels
like starting again, and why the line and keypoint models have so far
needed labelled frames of the footage they meet. A fixed camera removes
the problem rather than solving it: its mapping to the pitch is found
once per placement by clicking a few landmarks (M10), and a new field
costs minutes of clicking, not an evening of tags and a training run.
The spec anticipated this: if the follow-cam pipeline is the wrong thing
to optimise, stop and build the camera.

| | On the follow-cam | On a fixed camera |
| --- | --- | --- |
| Registration | per frame, per field; fails the gate | once per placement, by hand |
| Players | solved off the shelf | the same detector; tracks continuous, fragments rare |
| Ball | COCO finds 43–73% of burst samples by field and run; median gap 1–2 samples, a blackout tail of 1.6–3 s | not measured; the one risk the camera keeps |
| The stats Mode B cannot have | impossible | the headline set |

**The ball is the risk a fixed camera keeps, and nobody has measured it.**
At the spec's 35–50 px per metre a 20–22 cm ball is about 7–11 px across:
the follow-cam's median ball, and the range of the public broadcast set
stage one learned. That is arithmetic, not a measurement. Turf colour does
not change with the camera, so the worn-field problem may come along. Two
things point the other way, neither measured here. A fixed background
lets a moving ball stand out by its motion as well as its look, which is
the half of WASB that a panning camera destroyed. And stage one, the
small-ball specialist, was trained on exactly the size range a fixed
camera produces (`spike/evals/training_2026-09-23.md`). So the next ball
measurement belongs on fixed footage, and tagging more follow-cam frames
before that footage exists risks training for the wrong scale.

**How products that do this work.** This is a survey, not a measurement.
They own the camera. Veo and Pixellot sell the rig, so the lens, the
height and the stitching are known, and the mapping to the pitch is set
at install or in the app, not inferred per frame. Broadcast graphics such
as the first-down line read the camera's pan and zoom from sensors on its
head and calibrate once before the game. Their ball models are trained on
footage from very many installs. None of them infers the camera from the
paint on every frame of an arbitrary recording, from a few hundred
labels, which is the problem M1 set itself.

**Veo is itself a fixed camera.** Its hardware never moves; the follow-cam
is a virtual crop of a panorama taken from one position. Two consequences:

- To the extent the render is a pinhole view, which the registrar already
  assumes, consecutive follow-cam frames are related by one homography
  for the *whole* scene, not only the ground, because the view only turns
  and zooms about one point. The road, the backstop and the tree line that
  defeat the line detector are fixed features that anchor frame-to-frame
  tracking. So the next follow-cam registrar is: click landmarks on a few
  keyframes per match, carry the mapping between them by matching the
  background, and re-anchor where drift shows. It sits inside
  `FollowCamRegistrar`, returns `Registration`, and is scored by the same
  M1 gate and `bench.py`; nothing is loosened.
- If Veo offers the panoramic recording as a download, that recording is
  a fixed camera already, and the Mode A path can be tried on it before
  any rig exists. Not checked.

`docs/SPEC.md` still orders M9 after M8. It is a copy of the living design
doc, and changes when that does; until then, this section is the work
order.

## Next steps, in order

1. **A fixed-camera test, before any rig.** This is M9's validation pulled
   forward. A phone or action camera at 4K, as high as can be managed (a
   mast, a stand, a building), ten to fifteen minutes of a match or a
   training session, the whole pitch in frame, a fast shutter. Measure:
   player height and ball size in pixels at the near and far touchlines,
   blur on a struck ball, and whether one camera covers the surface. Run
   what already exists on it: COCO players, COCO ball, stage one, and WASB,
   which the first training run said was worth one more run on static
   footage. Click the four corners, fit a homography by hand, and measure
   its reprojection error. An afternoon, and it decides the lens, the
   height, and whether one camera is enough.
2. **Player detection and tracking (M3)** on the Veo footage, now.
   Off-the-shelf detection plus ByteTrack, filtered to the pitch by the
   grass mask so spectators drop out. CPU is fine for evaluation; a full
   match needs the GPU. Nothing here waits on registration or the ball.
3. **Identity (S4, S6), then a first review UI (M6).** Kit-colour team
   split, then fragment linking using stints from the minutes app; this is
   where the two systems meet. Review time is the criterion the spec says
   decides whether this is worth continuing, and neither stage needs
   registration.
4. **Click calibration, then keyframe registration for the follow-cam.**
   The click UI is M10's, and serves both modes: once per placement for a
   fixed camera, a few keyframes per match for the follow-cam, with the
   background tracking above in between. Measure each field's real
   dimensions first; the search assumes 105 x 68 and neither pitch is.
5. **The ball, on the footage the product will record.** If the
   fixed-camera test shows the ball at the size the spec implies, start
   from stage one and WASB there. Wherever follow-cam tagging continues,
   what 23 September taught still holds: tags from several matches with
   different kits and balls, not more of one; a size on every tag;
   not-visible tags on frames full of white shirts; throw-ins and the ball
   in hands. The first attempt is in `spike/evals/training_2026-09-23.md`.
6. **Restarts and possession**, once 4 and 5 hold. Possession first, since
   it is ball-and-players only; then the stationary-ball test that finds
   restarts; then the restart type from where on the pitch it happened.
   Counts per type per team are the output a coach reads.

**Parked, with what would bring each back:**

- **A pitch keypoint or line model** (the old second step). Each field
  has so far been a new domain for it, and keyframe clicks cost less
  than the labels. It returns if keyframe tracking cannot hold the gate
  across a whole match. The evidence and the pretraining plan stay in
  `spike/evals/PITCH_KEYPOINTS.md` and `spike/evals/DATASETS.md`.
- **Fine-tuning the ball on one follow-cam match at a time.** 53 tags from
  one match made a detector for that match, which fired on white shirts
  on the other. It returns, with the tagging rules in step 5, if the
  follow-cam stays the product's input for longer than planned.

The ball is still the gate for everything possession-shaped, as it was.
What changes is where it is measured: on the footage a fixed camera
records. If that camera cannot find the ball reliably, the possession half
of the wish list needs a different rig — higher, closer, or a second
camera — and the fixed-camera test is where that shows, for the price of
an afternoon.

## How to store the videos, and what matters more

**The Veo link is enough.** The `standard/machine` rendition, which is
the follow-cam cut, downloads from Veo's CDN with no login at roughly
100 MB/s; a match arrives here in under half a minute. Keep matches on
Veo and paste the match link.

**Also keep a copy you control.** Veo's retention is theirs, not yours.
The pipeline's own layout is `matches/{id}/source/` on the homelab's
MinIO, and that is where a match should live long term. A Google Drive
folder works as a fallback and this session can read it directly.

**Never in git.** GitHub will refuse the size, and raw match video is the
one thing the spec says leaves the homelab under no circumstances.

**What helps far more than where the file sits** is what is recorded
alongside each match, in the minutes app, at the time:

- The video timestamp of the first whistle, so video time maps to match
  time. Without it nothing from the camera can be lined up with a stint.
- Roster and stints, which the app already keeps. Stints are what make
  identity tractable.
- Track-tab events with the player attributed, which the app already
  keeps. These are the labels every event detector is judged against.
- The pitch's real dimensions, measured once per field with a tape or a
  phone. Youth pitches vary, and both of these are smaller than the
  full-size default the search assumes.
- Which touchline the camera stood on.

**Three matches, and one of them is never trained on.** The spec asks
for a fixed evaluation set of three matches that nothing is tuned
against. Two exist now. When the third is recorded, name it the held-out
one and keep it that way.
