# Changelog

## 0.1.0 — first commit

The spec ([`docs/SPEC.md`](docs/SPEC.md)) turned into a repository: the M1
registration machinery, the M2 skeleton, and the contracts everything later
will write.

- **Registration behind one interface.** `FollowCamRegistrar` fits a
  homography to every frame from painted lines: closed-form four-point
  fits over every naming of two line pairs, batched; plausibility checks
  that reject what no camera above the pitch could produce; a score that
  asks both whether the projected pitch lands on paint and whether the
  paint lands on the pitch; refinement of the best few by point-to-line
  ICP. `StaticRegistrar` fits clicked landmarks once per placement, as a
  homography or, for wide lenses, a homography with a thin-plate spline
  over its pixel residuals. Both return the same `Registration` and both
  write the same Parquet row, so the mode swap is configuration.
- **A synthetic camera** renders the pitch through a known homography, which
  is how registration is tested here and what `sideline spike --selftest`
  runs. Box views land within a metre; a midfield view with one crossing
  line is refused with a reason rather than guessed.
- **The M1 spike**: `sideline spike export.mp4` samples frames, registers
  each, writes an overlay per frame and a report with the registered
  fraction, confidence histogram, failure reasons and frame-centre jumps.
- **Contracts.** `tracks.parquet`, `events.parquet`, `registration.parquet`,
  `frames.parquet` and `detections.parquet` schemas; `match.json` and
  `player_stats.json` models where every aggregate carries its sample
  count, capture mode and partial flag, and follow-cam output refuses the
  stats the spec says are impossible on it.
- **The sport plugin boundary** with soccer as the first plugin: a pitch
  model parametrised for youth sizes, its lines by family, landmarks for
  clicking, entity classes, event grammar constants and stat definitions
  with their per-mode availability.
- **Video time to match time** through soccer-manager's periods and
  stints, so a frame knows its match minute and who was on the pitch.
- **M2 skeleton.** Blob store (directory or MinIO) with the immutable
  `matches/{id}/{stage}/v{n}/` layout and a manifest per version; Postgres
  models for matches, jobs, stage runs and review decisions; S0 ingest
  (probe, transcode when ffmpeg is present, keyframes, a half-time hint
  from the grass in frame, `match.json`) and S1 sampling at 5 fps to
  `frames.parquet` plus cached JPEGs; a FastAPI upload that takes the
  accompanying inputs and queues the stages; an RQ worker; an inline queue
  so all of it runs and is tested with no services; `docker-compose.yml`.

## 0.1.2 — two real matches, the detector's limits, and the plan

Both Veo exports run through the pipeline; what the footage says is in
`docs/NEXT.md`, and the measurements behind it are in `spike/evals/`.

- **Grass colour is estimated per frame** (`estimate_grass()`). The worn
  19 Sept field is olive, not green, and a fixed hue range passed a tenth
  of its pitch pixels. Broadcast registration went from 8 to 31 of 100
  on that change alone, and the worn field's lines became findable.
- **Line thresholds follow the frame's grass** as ratios, chosen to
  reproduce the original absolute thresholds on rendered grass so the
  synthetic views keep their precision (0.59 m worst case).
- **Hole-filling in the grass mask is capped** so a road with cars, or a
  crowd, enclosed by grass is no longer filled in as pitch.
- **Plausibility checks are reported one by one** (`_sane_checks()`) and
  come in three modes (`FollowCamConfig.plausibility`), measured on all
  four datasets by `spike/evals/bench.py`. The default stays the precise
  one; the looser ones register more frames and are wrong more.
- **SoccerNet's pretrained line network runs here** (`sn_baseline.py`)
  and finds on the worn field what the classical detector cannot. It is
  the next registrar, after fine-tuning on this footage.
- **Ball detection is the open problem for everything possession-shaped.**
  Players are found off the shelf; the ball is found by no model tried,
  COCO or fine-tuned. `docs/NEXT.md` puts a ball detector trained on this
  footage first.
- The midfield synthetic test now asserts the contract (refused, with a
  reason, no homography in the artifact row) rather than an internal
  detail; the view is still refused.

## 0.1.3 — the tagger reachable at the plain Pages root

`web/` had no `index.html`, only `label.html`, so the bare GitHub Pages
address (`dawsboss.github.io/Sports-Camera-Analytics-/`) 404ed while the
documented `/label.html` link worked. Deployment itself was fine — the
Pages workflow had already run and published successfully. Added
`web/index.html`, a one-line meta-refresh to `label.html`, so the root
link works too instead of relying on everyone knowing the exact file.

## 0.1.4 — the tagger was behind its own modal, at a tenth of its size

The page on Pages came up dim and inert: everything roughly a quarter
brightness, and no button, link or match did anything when tapped.

Two independent faults, both in the markup around the app rather than in
the app:

- **The sheet was never hidden.** `.sheet` sets `display: flex`, and a
  class rule outranks the browser's `[hidden] { display: none }`. So the
  modal's full-screen `rgba(11,10,7,.78)` scrim was painted over the page
  from the first frame, dimming everything behind it and taking every tap
  before it reached a button — `elementFromPoint` at the middle of the
  start screen returned the scrim, not the match list. `closeSheet()` set
  `.hidden = true` and nothing happened, which is why the page could not
  be recovered by tapping it. `.diagram` and `.row` had the same fault,
  so the pitch diagram and the pitch buttons showed in ball mode too.
  Hidden now wins outright.
- **No doctype and no viewport.** The file opened straight into `<title>`,
  so a phone rendered it in quirks mode at the default 980px layout width
  and then scaled the result down to fit — measured, a 412px phone laid
  the page out at 980. That is the "small, and cut off on the right".
  Added the doctype, a real `<head>`, `charset`, and
  `width=device-width` with `viewport-fit=cover`, which is also what makes
  the existing `env(safe-area-inset-*)` padding mean anything.

`web/index.html` was missing the same doctype and viewport and got them.

Checked in a mobile Chromium at Pixel 7 size, before and after: standards
mode, layout width equal to the device width, no horizontal overflow, the
scrim absent until a sheet is opened and gone again when it closes, and
both screens plus the mode toggle driven by click.

## 0.1.5 — the tagger's gestures, and what happens to a point you cannot see

Tagging on a phone was fighting the person doing it. Everything here is in
`web/label.html`; nothing in the pipeline changed.

- **Looking closer no longer moves the mark.** The frame had one zoom — a
  fixed 4x about wherever the last tap landed — and every `pointerdown`
  on the video placed a pin, so pinching to check the ball placed a pin
  instead, and there was no way to pan to a ball that was not under the
  point you zoomed at. The stage now reads pointers itself under
  `touch-action: none`: a tap places, a drag on a mark moves that mark, a
  drag anywhere else moves the frame, two fingers pinch, and a **&minus; /
  + / fit** control does the same one-handed. A mark is only placed by a
  press that does not travel.
- **Zoom in pitch mode at all.** Zoom was wired to ball mode only, which
  made a distant corner flag a guess. It is the same gesture layer in both
  modes now, to 10x, and marks counter-scale so a pin stays the size of a
  pin however far in you are.
- **Undo is a stack.** It deleted the last key of the pins object, and
  JavaScript returns integer-like keys in numeric order however they went
  in — so it removed the highest-numbered vertex, not the one just placed.
  Placement order is kept separately now, and undoing re-arms the point it
  removed so putting it back is one tap.
- **A point you cannot see has an answer.** Vertices hidden by a player,
  off the edge, or never painted on a school field now get **can't see
  it**: the point greys out on the diagram and stops being offered for
  that frame. Leaving it out is correct rather than a compromise — the
  pose model learns which points are present as much as where they are, so
  a guessed corner is a wrong label and a missing one costs nothing.
- **Marks that can be seen.** An unplaced diagram dot was panel-grey on a
  panel; predicted rings were a thin dashed outline that vanished over
  white kits and bright turf. Both now differ in fill and outline in every
  state, the dots are larger, and the diagram picks the vertex nearest the
  tap instead of whichever invisible hit circle was drawn last — goal-line
  points sit a few pixels apart there and the overlap meant the point you
  got was the one with the higher number.
- **Accepting a suggested ring says which point it took**, because a ring
  accepted by accident is otherwise a silent wrong label. With a point
  armed, a ring has to be tapped inside to be taken, so a ring near the
  point being placed cannot swallow the tap meant for it.
- **Taps outside the frame are ignored.** The stage is wider than the
  video; a tap in the black beside it used to clamp onto the nearest edge
  and record a point nobody meant.

`web/tagger.smoke.js` is new: Playwright driving the page in a real
Chromium, asserting the above. It is not part of `pytest` — none of it is
reachable without a browser — and `pytest` stays what CLAUDE.md says it is.

## 0.1.6 — which public datasets replace tagging, and which do not

`spike/evals/DATASETS.md` is new, and is a survey rather than a
measurement: it is the first file in that directory whose claims come
from other people's papers instead of our footage, and it says so.

- **Pitch keypoints can be pretrained for free.** SoccerNet's
  calibration set is ~25.5k frames with every line and circle named, and
  a named vertex is the intersection of two named segments, so the
  conversion to the 32-point layout is arithmetic. That is eighty times
  roboflow's 317 images for no tagging, and it changes what step 2 of
  `docs/NEXT.md` asks for: only enough worn-field frames to shift a
  domain, not several hundred from nothing.
- **No public dataset has a ball on worn turf.** SoccerNet-GSR removed
  the ball deliberately; SoccerTrack and TeamTrack annotate players;
  ball action spotting is timestamps, not boxes; ISSIA is a static-camera
  ball set on a Serie A pitch. All of it is pristine professional turf,
  which is the 73% case we already pass. The 43% case stays ours to tag.
- **Auto-annotating our frames with community models was already tried
  and is the wrong shape anyway.** Both keypoint models put points in the
  sky, and the fine-tuned ball model drew 33-59 px boxes around an 11 px
  ball. The general reason: auto-labelling yields labels where the model
  already works, and the frames worth labelling are the ones where it
  does not.
- **Four things that do cut the tagging** are written down instead:
  pretraining as above, tracker propagation across a burst (the median
  gap is one to two samples), hard negatives mined for free from the
  frames already tagged "not visible", and copy-paste of balls cropped
  from the green match onto worn-field frames at the measured 6-39 px.
- **An architecture worth one evaluation before tagging for YOLO:**
  heatmap-over-consecutive-frames small-ball detectors (WASB, FootAndBall,
  DeepBall) are built for a ball this size, and use the temporal
  structure that our burst measurement says is there. Scored with
  `ball_recall.py` over the same bursts or the number means nothing.

`docs/TRAINING.md` — the GPU training guide drafted separately, on
`claude/ragging-data-training-hep16d` — gains what follows from the
survey, and answers the question it left open:

- **Pretrain the pitch model on SoccerNet's calibration set**, not only
  on the 317-image mirror. Twenty-eight of our 32 vertices are
  intersections of its named segments; the two penalty spots are not
  lines and the two circle extremes need an ellipse fit, so those four
  are written `0 0 0` and left to our own tags.
- **Check the public ball set's box sizes before using it as stage one.**
  Ours is 11 px. A stage one full of 40 px balls teaches a scale prior
  that stage two has to unlearn; match the pixel size by choosing
  `imgsz`, not the resolution.
- **`mosaic=0.0` and `scale=0.2` for the ball.** Mosaic tiles four images
  into one frame and halves every object's linear size, and the default
  `scale` can halve it again — harmless for a 60 px person, fatal for an
  11 px ball. And not `freeze`: the shift here is grass colour and faint
  paint, which lives in the early layers that freezing would pin.
- **Hold out by time, not only by match, while there are two matches.**
  Holding out the green field measures the case we already pass; holding
  out the worn field leaves no worn-turf training data at all. Split the
  worn match into tagged and held-out halves until a third match exists.
- **Quote the gap distribution, not mAP.** A box six pixels off centre
  scores near-zero IoU and is a perfectly good ball for possession and
  restarts.
- **`flip_idx` resolved.** An image mirror moves the camera to a mirrored
  point on the same touchline, so `camera_side` is untouched and the
  labels permute by `x -> LENGTH - x`. Derived from `VERTICES` rather
  than copied, it is an involution, and the four halfway-line vertices
  map to themselves — so mirroring fixes the left/right imbalance and
  cannot help those four at all.

## 0.1.7 — suggested rings only where the taps put them

In pitch mode the suggested rings were often on the wrong grass, even after
five or six points. A simulated camera on 60 x 42 to 110 x 68 m fields with
a few pixels of tap error had 83% of rings more than 3% of the frame width
off. Two causes, both in `web/label.html`:

- **Three points on one line fix nothing off it.** Four points determine a
  homography only if no three are collinear. The next-point walk went
  nearest-first, and from a corner the nearest points are all on the goal
  line, so it led straight into that case. The solver then returned an
  arbitrary member of the family of exact fits, and every ring off that
  line was drawn from it. The walk now skips a point on a line two placed
  points already fix while there is another choice, and no rings are drawn
  until the placed points actually determine the fit.
- **The layout assumed a 120 x 70 m pitch.** Only the boxes, goal areas,
  spot and circle are fixed by the laws; both real fields are smaller and
  unmeasured. Box points predicted from box points were fine, but anything
  past the box sat where a 120 x 70 pitch would have it. The fit is now
  repeated for every length from 60 to 125 m and width from 45 to 80 m that
  the taps do not rule out, and again with the taps shaken by a finger's
  width. A ring is drawn only where all of those agree to within 3% of the
  frame width. A ring the taps cannot place is not drawn at all: that costs
  a tap, whereas a ring in the wrong place is a wrong label.

Rings are no longer clamped onto the frame's edge when they fall just
outside it. They are also dropped when the point is behind the camera,
where the projection folds it back into the frame upside down.

On the same simulation, 1–5% of rings are now more than 3% off, down from
83%. Fewer are shown after four points (about a fifth of the visible ones),
and more appear as the placed points spread past the box. The smoke test
now places its taps where a camera would see them on a 100 x 60 m field.
It checks that every ring lands on its point, and that three points on a
line plus one draw no rings. The old page fails both checks.

## 0.1.8 — the first training run, on a gaming PC, and what it found first

The GPU guide run end to end for the first time, on a Windows desktop with
an RTX 3090 Ti. Most of what it found was not about the models: a timing
bug in the tagger, three training defaults that silently undo a small
fine-tune, and a card that pages to system RAM instead of failing. The
measurements are in `spike/evals/training_2026-09-23.md`.

Brought over from `claude/ragging-data-training-hep16d`, which drafted
them alongside the guide that 0.1.6 superseded:

- **`spike/labels/fetch_public.py`** pulls the two CC-BY-4.0 Hugging Face
  mirrors of Roboflow's pitch-keypoint (317 images) and ball (1,237)
  datasets, which need no account, and refuses to continue if the pitch
  set's keypoint order ever stops matching `VERTICES`.
- **`spike/labels/tags.json`**, the first Sideline Tagger export: 94 ball
  samples in ten bursts and 12 pitch-keypoint frames, all from
  `20260919-flight`. Coordinates only.

New:

- **The tagger keyed about half of all ball tags to the frame after the
  one tapped.** It stored `Math.round(currentTime × 29.97)`; a paused
  video shows the frame whose interval contains `currentTime`, which is
  the floor, at 30000/1001. Invisible on a slow passage; on a fast zoomed
  pan the ball sat 10–18 px outside its 22 px box. Measured, not
  reasoned: `spike/labels/check_tag_timing.py` finds the best-matching
  frame around each tag with a detector, and `floor(t × fps)` was it for
  29 of the 32 tags where frames could be told apart. The tagger now
  keys by the floor at the exact rate; `build_dataset.py` recovers the
  right frame for existing ball tags from their stored time (52 of 94
  moved). Pitch tags store no time and stay at most a frame late.
- **`build_dataset.py`** writes the pose `flip_idx` (checked against
  `VERTICES` and against `fetch_public.py` in `tests/test_build_dataset.py`,
  so `fliplr` is sound for the pitch), takes `--holdout-window
  MATCHID=START:END` so a single tagged match can still be scored on a
  stretch it never trained on, drops tags within ten seconds of that
  window from both sides, clears its previous output so no frame keeps an
  old split, and keeps propagated tags out of val.
- **`spike/labels/propagate.py`** fills the five frames between two
  neighbouring ball tags by template matching forward from one tap and
  backward from the other, keeping only frames where the two agree
  within 3 px. On `20260919-flight`: 107 of 400 frames filled, two of
  those visibly wrong on the contact sheet (both taps off the same way,
  so both directions agreed on the same wrong spot) and deleted by hand.
  Agreement does not catch shared tap error; the sheet does.
- **`spike/evals/ball_on_tags.py`** scores a detector against held-out
  tags: is its most confident detection within 20 px of the tap? That is
  the question `ball_recall.py` cannot ask, and the one a detector
  fine-tuned on ninety boxes most needs asked. `ball_recall.py --sheet`
  writes a crop of every detection it counted as found, so that number
  can be checked by eye.
- **`spike/evals/wasb_ball.py`** runs WASB's released small-ball weights
  cold. On its own static-camera ISSIA test clip it finds the ball in 11
  of 13 frames; on our 40 held-out tags, in 0, with or without motion.
  The heatmap at our ball is ~0.007 where WASB's threshold is 0.5. The
  tagging plan stands.
- **`docs/TRAINING.md`** now says what actually ran: native Windows
  instead of WSL; `batch=6` for the ball at 1920 on 24 GB (at `batch=8`
  the driver paged 7.5 GB into system RAM and training ran at a quarter
  speed); `optimizer=auto` ignores `lr0`, `nbs=64` makes 53 images about
  one update an epoch, and warmup runs 100 iterations at a bias rate of
  0.1, so stage two names its optimizer, sets `nbs` to `batch` and turns
  warmup off; weights land in `runs/detect/<name>/`, not where the old
  commands said. The public ball set's balls measure 11.7 px median
  against our 11, so stage one needs no scale correction. And no
  `cache=ram` on Windows: stage one was stopped at epoch 41 of 60 for
  running the 64 GB machine out of memory with it on, most likely a copy
  of the 5.8 GB cache per spawned dataloader worker, for no speed gain on
  a GPU-bound run.
- **The green 20 Sept match is played with an orange ball**, the worn
  19 Sept one with a white ball, so `docs/NEXT.md`'s 73%-against-43% is
  ball colour as well as field condition; a caveat now says so there.
  The green match stays the never-trained-on test, but until a match
  with an orange ball is tagged it tests colour transfer as well.
- **The ball, trained and scored.** Stage one (public data) reached
  mAP50 0.95 on the public val split. On the worn match's 40 held-out
  tags it puts its top detection on the ball in 19 (COCO: 5), but it
  never draws a box outside 7–15 px, so on random bursts of the
  zoomed-in follow-cam it finds 16% where COCO finds 63%. The fine-tune
  on 53 tags from that match is the best of the four on that match and
  the worst on the green one, where it fires on white kit shirts: 88%
  "found", one on the orange match ball. A time split within one match
  could not show that; the untouched second match did. `propagate.py`'s
  frames stopped the fine-tune overfitting (21 against 10 of 40 at the
  last epoch) and made the shirt habit more confident. Nothing trained
  here beats COCO `yolo11x` on a match nothing was tuned on; the next
  lever is tags from more matches, with sizes.
- **The pitch, trained and scored.** `spike/evals/pitch_on_tags.py`
  scores a keypoint model against tagged vertices, because
  `pitch_keypoints.py`'s homography fit is self-consistency and was
  again confidently wrong: it fitted 11 of 12 worn-match frames with
  penalty boxes drawn beside the centre circle. Against the tags, the
  public-data pretrain (keypoint mAP50 0.81 on its own val, still
  rising at 200 epochs) finds 2 of 133 tagged vertices on our footage
  and puts its guesses on open grass; the fine-tune on ten frames finds
  none of the 25 on the two held-out frames and only 28% on its own
  training frames. It learned the halfway line, the most-tagged
  vertices, and invents the rest. Twelve frames and 317 broadcast images
  are not enough; the SoccerNet conversion and many more tagged frames,
  at both ends, are.
- **`PITCH_KEYPOINTS.md` no longer says the public sets need a Roboflow
  account.** They come anonymously from the Hugging Face mirrors through
  `fetch_public.py`. This note was the last part of
  `claude/ragging-data-training-hep16d` not already on this branch.

## 0.1.9 — agents and skills for the routine work

Claude Code sessions here had been spending their context on work that
follows the same steps every time: pulling, committing, pushing, running
the suite, re-reading long docs for a single number. That work now goes
to project agents in `.claude/agents/`. Slash commands in
`.claude/skills/` start them. The main session keeps its context for
the design.

- **`git-shepherd`** (`/sync`, `/ship`, `/pr`) is the only thing that
  writes to git or GitHub. It merges both the branch's own remote and
  its base (whatever the branch was cut from, recorded in
  `branch.<name>.sidelinebase`, else `main`). On a conflict it aborts
  the merge and reports both sides; it never resolves one. It runs the
  suite before pushing and never force-pushes. It refuses to stage
  video, weights, Parquet or anything under `data/`, `runs/` or `out/`.
  After a PR it offers to delete only the local branch, because
  deleting the remote branch of an open PR closes it; the remote branch
  is offered once the PR has merged. `gh` is not installed on the
  training PC, so without it the agent returns a compare link and a
  ready PR body.
- **`test-runner`** (`/test`) runs `pytest` in the background and
  returns a few lines. When a test fails, it reruns that test on the
  base branch in a throwaway worktree to say whether the failure is new.
  The package is installed editable, so the worktree is imported through
  `PYTHONPATH`; this was checked to import the worktree's `sideline`,
  not the checkout's.
- **`invariant-guard`** (`/guard`) reviews a diff against the invariants
  in `CLAUDE.md`, with the file each one lives in.
- **`docs-oracle`** (`/spec`) answers from SPEC, NEXT, TRAINING and the
  eval notes with citations.
- **`eval-runner`** (`/eval`) runs the `spike/evals` and `spike/labels`
  scripts on the GPU and reports them by the rules those notes learned:
  gap distributions, not rates; whether the model trained on the match;
  self-consistency called what it is.
- **`house-style`** is where the commit, CHANGELOG and PR conventions
  are written down, so they no longer have to be inferred by reading
  this file.

## 0.1.10 — the camera first

After the first training run, the question was whether what remains is
just more tagging, training and tuning. It is not: most of the effort so
far went into a problem the product does not have. Registering a
panning, zooming follow-cam from the paint on every frame means every
new field brings new confusers and new paint, so every new field has
needed its own labels. A fixed camera is calibrated once per placement
by clicking landmarks (M10), so a new field costs minutes, not an
evening of tagging and a training run — and the spec itself says to stop
and build the camera when the follow-cam pipeline is the wrong thing to
optimise. `docs/NEXT.md` gains a section, "The camera first (23
September)", with that reasoning, a follow-cam-versus-fixed-camera
table, a short survey of how commercial systems handle this (labelled as
a survey, not a measurement), and the observation that Veo's own rig is
a fixed camera, so follow-cam frames could instead be registered by
clicking a few keyframes per match and tracking the fixed background
(road, backstop, trees) between them.

- **Players are still solved off the shelf**, and the ball is still the
  one risk a fixed camera keeps: at the spec's 35–50 px per metre it is
  about 7–11 px, which is arithmetic, not a measurement, so it has to be
  measured on fixed footage before more ball tagging trains for the
  wrong scale.
- **"Next steps, in order" is rewritten**: (1) a fixed-camera test with a
  phone or action camera at 4K before building any rig, measuring player
  and ball pixel size, blur and coverage, and running the existing
  detectors and WASB on it; (2) M3 detection and tracking on the Veo
  footage now, which needs neither registration nor the ball; (3)
  identity and a first review UI; (4) click calibration, then keyframe
  registration for the follow-cam; (5) the ball, on the footage the
  product will actually record; (6) restarts and possession. The pitch
  keypoint/line model and one-match ball fine-tunes are parked, with
  what would bring each back.
- **`CLAUDE.md`'s summary, its SoccerNet line-network note and its first
  known gap** now say the same, and a new known gap records that no
  fixed-camera footage exists yet.
- **`docs/SPEC.md` is untouched.** It is a copy of the living design doc,
  which the user updates separately; this branch only reorders the work
  in `NEXT.md` ahead of it.

## 0.1.11 — a buying list, a printed head and the coverage arithmetic

Turns the camera-first decision into hardware: a fixed sideline rig for
daytime youth games (U10-U17), on a mast, the static camera the spec's
Mode A is built for (M9, pulled forward by `docs/NEXT.md`). Everything
here is sized by arithmetic, not a measurement, and the field test it is
built for is still ahead.

- **The halfway line, not behind a goal.** It is the one spot that
  minimises the farthest distance to any point on the pitch: 86-90 m on
  11v11, against 110 m behind a goal and 126 m from a corner.
- **An uneven lens split beats an even one.** The far corners are two to
  three times farther than the near side, so two 6 mm cameras cover the
  far half and two 4 mm cameras the near half. That keeps the ball at
  8.6 px or more anywhere on 11v11 (9.8 px at the far corner, inside the
  8-17 px range that `spike/evals/training_2026-09-23.md` found), where
  an even split would give about 5-6 px. Players stay at 62 px or more,
  and worst-case ground resolution is 0.25 m per pixel, from an 8 m mast
  10 m back.
- **Aim.** Far cameras 25.5 degrees either side of straight across and
  4 degrees down; near cameras 40 degrees either side and 26 degrees
  down. Nothing inside the lines drops out with up to 2 degrees of
  aiming error, on masts from 6 m to 8 m.
- **`hardware/README.md`** is the buying list, with sources: two
  Milesight MS-C8164-PD (450 g, 4K30, 16 Mbps, manual shutter, IP67/IK10,
  NDAA-compliant) per focal length keep the head near 2.9 kg, under the
  4.5 kg rating of the 8 m carbon mast it ships on; a Ubiquiti USW-Flex
  on the head means one PoE++ cable up the mast instead of four. It also
  has the mechanical and electrical connections, print and assembly
  steps, camera settings, the field routine, and what is not verified.
- **`hardware/head/sideline_head.scad`** is the parametric OpenSCAD
  head: four angled pads, a captive 3/8"-16 nut, a clamp collar, a
  switch hood, a guy ring, and two small test prints for the parts that
  need measuring against real hardware first. Binary STLs and preview
  renders are checked in alongside it.
- **`hardware/rig_geometry.py`** is the coverage and resolution
  arithmetic itself: it draws a per-camera aim card and a coverage map
  from the pipeline's own pitch model, so a card can be checked against
  a camera's live view at setup. Cards for 11v11, 9v9 and 7v7 are in
  `hardware/aim/`.
- **`hardware/recorder/`** stream-copies the four cameras into
  ten-minute clock-aligned segments, with a chrony config so all four
  cameras and the recorder share one clock at the field.
- **Nothing under `sideline/` changes.** Two gaps are recorded rather
  than fixed: the pipeline takes one camera per match, and multi-camera
  input is a SPEC non-goal, so four views need a stitch at ingest or
  per-view registration merged on the pitch, which is a spec decision;
  and `StaticRegistrar.register()` ignores the frame, so a bumped or
  swaying mast would silently shift every coordinate after it.

## 0.1.12 — a hidden pod, and what cheaper cameras give up

Answers whether cheaper Reolink cameras would do, and hides them: a second
build beside the open head, four RLC-833A zoom turrets behind flush ports
in one printed pod, so the rig reads as one sports camera rather than four
security cameras on a stick. What decides it is pixels per degree, not
megapixels, and it favours the zoom turret; what it gives up is bitrate,
frame rate, an unpublished shutter limit and an unproven zoom motor, so
the pod is a second build to test, not a replacement yet.

- **Pixels per degree, not megapixels.** From the same mast and lens
  model, the smallest ball on 11v11 is 6.3 px on four fixed RLC-810A (87
  degrees), 7.1 px on four 12 MP P340 (93 degrees), 8.6 px on the open
  head's Milesights, and 9.3 px on four RLC-833A zoom turrets (far pair
  zoomed to 54 degrees, near pair at 84) — at about a quarter of the
  camera cost, $310-420 against about $1,580. The price: half the bitrate
  (8 vs 16 Mbps), 25 fps, an unpublished fastest shutter, and a motorised
  zoom that must come back after every power-up. Record one match on one
  RLC-833A (`docs/NEXT.md`'s fixed-camera test, ball recall over bursts)
  before buying four.
- **`hardware/pod/sideline_pod.scad`** is the convex hull of a window disc
  per camera, the camera bases, a roof, a back and a floor ring. Each
  window is a face of that hull, so nothing of the housing stands in front
  of its plane: it cannot enter a view, and each camera can be trimmed 5
  degrees either way.
- **The shell is a rain screen, not a seal** (the cameras are IP66). It
  prints as a cap and four quarters, so the roof has no seam; every seam
  is a butt joint with a tongue behind it, and on level seams the tongue
  rises from the piece below, so water that creeps in runs back out. A
  printed frame (core, seat rings on struts, floor ring, top plate)
  carries the load, with each seat whole to one frame half so no seat is
  cut between prints. Print orientations keep every support inside the
  shell; the lower quarters print upside down, standing on their own
  tongue, because floor-down would scar the underside — the face the
  touchline looks at.
- **`hardware/pod/check_pod.py`** fixes two problems the first export had,
  floating lap strips and a top plate poking 3 mm through the back wall,
  and now checks, from the `.scad`'s own parameters: windows flush; no
  shell or frame in any view with 5 degrees of trim; clearances between
  turrets, shell, frame and the frame halves; and every piece one
  watertight body that fits a 250 mm bed. All pass: turret to turret 7.3
  mm, turret to shell 4.3 mm, frame to shell 4.5 mm.
- **Aim.** The first aims (near pair ±40/28, far zoom 53) left gaps inside
  the lines in 5 of 300 trials with every camera 2 degrees off; the
  shipped aims (near ±39/27, far zoom 54, far ±26/4) leave none in 600.
  `hardware/rig_geometry.py` gains `--head reolink-833a` (a `HEADS`
  table), so the pod's numbers and its aim cards (`hardware/pod/aim/`)
  come from the same model as the open head's.
- **`hardware/recorder/record.sh`** now copies video only (`-map 0:v`): S0
  already drops audio (`-an` in `sideline/stages/s0_ingest.py`), the
  Reolinks have microphones, and a sideline microphone records the parents
  standing under it. It also documents the Reolink RTSP path
  (`PATH_MAIN=h265Preview_01_main`).
