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
