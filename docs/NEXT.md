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
| Ball, largest COCO model at 1920 px, consecutive samples at 5 fps | **varies hugely by passage of play**: one burst 12 of 12 with no gap, another 7 of 12 with gaps of 2 and 3 samples, another 8 of 40 with gaps of 13 and 15 |
| Ball, a fine-tuned soccer-ball model from Hugging Face | 3 frames of 12, at 0.2 to 0.3 confidence, with boxes 33 to 59 px wide where the ball is 15 to 35 px: not the ball |

Players are solved off the shelf.

**The ball needs measuring over consecutive frames, not scattered ones,
and the first attempt here got that wrong.** Frames sampled minutes apart
said "1 of 8" and meant nothing: what matters is whether a tracker can
carry the ball across the samples where detection misses, and only
neighbouring samples answer that. Measured properly with
`spike/evals/ball_recall.py`, the off-the-shelf model is far better than
that first number suggested and wildly uneven — some passages every
sample, others three-second blackouts. A gap of one or two samples is
bridged by any tracker; a gap of fifteen is three seconds, in which the
ball crosses half a pitch, and nothing may be drawn across it.

So the question is not "does the ball get detected" but "how much of the
match is in the good regime", and closing the bad stretches is what
fine-tuning is for.

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

**Goal kick, corner, throw-in, drop ball.** Restarts: the ball stops,
the players reorganise, the ball is put back in play from a particular
place. Telling them apart needs where the ball is on the pitch, so
registration, or a cheaper proxy that is good enough on follow-cam
footage: what the camera is pointing at (a corner flag in frame, the
goal frame in frame, a player standing on the touchline holding the
ball overhead). The minutes app already records these by hand with the
player attributed. Those taps are the ground truth to measure a
detector against, and the tapping should continue for that reason.

**Fouls.** Not detectable from video alone. Play stops for many reasons
and only the referee's whistle and signal say which. Keep the Track tab
for fouls; the camera can timestamp the stoppage around a tapped foul,
which is a useful thing to show, and no more.

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

## Next steps, in order

1. **A ball detector fine-tuned on this footage.** Everything
   possession-shaped waits on closing the blackout stretches above. The
   labels come from `web/label.html`, a phone page that plays the Veo
   footage and records where you tap, and
   `spike/labels/build_dataset.py`, which cuts the tagged frames and
   writes them for training. Tag the frames where the ball is *not*
   visible too: those are what stop a detector firing on a corner flag.
   Train on the homelab GPU, and measure with `ball_recall.py` on the
   held-out match, never the one trained on.
2. **The line network fine-tuned on this footage**, from the baseline's
   published weights. Label pitch lines as polylines on 200 to 300 frames
   across both matches. Plug its masks into the existing hypothesis
   search behind the `Registrar` interface: the search needs no change,
   it just receives clean lines, and because the network names them the
   naming search collapses to a lookup. Pitch dimensions must be measured
   for each field first; the search assumes 105 x 68 and neither of these
   pitches is.
3. **Player detection and tracking (M3)** can start now. Off-the-shelf
   detection plus ByteTrack, filtered to the pitch by the grass mask so
   spectators drop out. CPU is fine for evaluation; a full match needs
   the GPU.
4. **Identity (S4, S6):** kit-colour team split, then fragment linking
   using stints from the minutes app. This is where the two systems meet.
5. **Restart events**, once 1 and 2 hold.

Item 1 is the gate now, in the same sense M1 was: if the ball cannot be
found reliably on this footage, the possession half of the wish list is
not available from a follow-cam and the honest move is to say so.

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
