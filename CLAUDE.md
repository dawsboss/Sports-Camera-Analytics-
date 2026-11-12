# CLAUDE.md

Static-camera sports analytics pipeline; Veo follow-cam exports are the
proof-of-concept input. Python package `sideline/`, FastAPI + Postgres +
MinIO + Redis/RQ in `docker-compose.yml`, everything testable on a CPU with
no services. Publishes aggregates to Firebase under a `soccer-manager`
fixture; that app stays a static site that consumes JSON.

Read `docs/SPEC.md` before changing anything structural. It is a copy of
the living design doc, and its milestone order is the work order, except
that NEXT.md pulls M9's camera test and M10's click calibration forward
(23 September). Then read `docs/NEXT.md`: it says what two real Veo
exports, a labelled broadcast set and the synthetic views measured, what
each requested output needs, and the order of the next work. In short:
M1 (classical registration) fails the gate on real footage and the
measurements say why; players are detected off the shelf; the ball is
detected 43% of samples on worn turf and 73% on green, with a bridgeable
median gap and an unbridgeable tail, and it is the gate for everything
possession-shaped. M2 (the skeleton) is built. M3 onward is not started.
**Since 23 September the work order is the camera first** (NEXT.md, "The
camera first"). Registering a panning follow-cam from the paint on every
frame is a problem a fixed camera does not have, so a cheap fixed-camera
test comes before more registration or ball training; M3 starts now on
the Veo footage; and the next follow-cam registrar is clicked keyframes
plus tracking of the fixed background, not a fine-tuned line model.
**Since 31 October restarts are found without registration** (NEXT.md,
"Restarts without registration"): S2, S3 and the events half of S8 exist,
and a restart is a ball that sat still and was struck or thrown, named by
what surrounded it in heights of the taker, never in metres or pixels.

## Required after every change

- `pytest` — under two minutes, no GPU, no Redis, no MinIO, no Postgres.
  Registration is checked on rendered frames with known truth, storage
  against a directory, the database against SQLite, the queue inline.
  `.github/workflows/test.yml` runs it on every push.
- The synthetic registration tests pin the *machinery*, not the M1 gate.
  A change that makes `tests/test_followcam_synthetic.py` worse is a
  regression even if it seems to help real footage; add the real-footage
  case as a fixture rather than loosening the synthetic one.

## Invariants — do not violate these

- **Static wins.** The product is a fixed camera seeing the whole surface.
  If a shortcut helps follow-cam but constrains the static path, do not take it.
- **Registration is the only stage that knows which camera it is.**
  `FollowCamRegistrar` and `StaticRegistrar` both return `Registration`
  and both write `REGISTRATION_SCHEMA`; nothing downstream asks which. If
  a later stage needs to branch on capture mode, the S5 interface was
  drawn in the wrong place.
- **Every artifact records its capture mode**, and every aggregate carries
  `sample_count`, `capture_mode` and `partial`. `PlayerStats` refuses the
  spec's impossible-in-Mode-B stats with a value on follow-cam footage and
  refuses an unflagged distance. The schema is identical in both modes;
  only how much of it is populated changes.
- **Artifacts are immutable and versioned.** `matches/{id}/{stage}/v{n}/`,
  a manifest written last, and `BlobStore.put` refuses to overwrite. A
  re-run is a new version. Re-running S8 must never require re-running S2.
- **Review decisions are rows in Postgres, never edits to Parquet.**
  `ReviewDecision` carries the tracks version it refers to so a better S6
  can replay corrections on top.
- **S0 through S7 contain no soccer.** Pitch geometry, entity classes,
  event grammar and stat definitions come from `sideline/sports/` through
  `get_sport()`. The test of the boundary is that basketball is a court
  model and a rules file, touching zero detection, tracking or review code.
  The event stage measures a dead ball (`DeadBall`) and the plugin names it
  (`classify_restart`); no code outside `sports/` knows the word throw-in,
  only the docstrings that say so.
- **Event thresholds are in heights of the people, not pixels or metres,
  and each sport states its own** in its `EventGrammar`; `sports/base.py`
  carries no defaults to inherit. That is what lets the same rule hold on
  a frame registration could not place, on a zoomed corner and at the far
  end of a static view. A rule in pixels would be a follow-cam shortcut
  that constrains the static path.
- **The event stage reads the camera's motion from registration when it
  exists** and estimates it from the people only where it does not. It
  never asks which camera; it asks whether S5 placed the frame. The
  people estimate mistakes a coherent jog for a pan, which on a fixed
  camera is the case S5's one-off calibration removes.
- **Stints are the truth for who was on the pitch**, read from
  soccer-manager and bridged through `timeline.py` (`match_seconds`,
  `on_pitch`). Roster, stints and periods are copied at upload, never
  linked, so a roster edit a month later does not change an analysis.
- **Nothing derived from the video leaves the network.** The API serves
  JSON artifacts and, for the review page, S1's sampled frames and S3's
  boxes to browsers on the LAN or a Tailscale network; at 5 fps that is
  most of the footage, so the API is never exposed to the internet and
  never put behind a public tunnel. Video and Parquet are never served.
  Only `player_stats.json` goes to Firebase. (Amended 31 October when the
  owner asked for the review page to be hosted on his own network.)
- **The camera side is a convention, not a detection.** The pitch is
  symmetric under a half turn, so `FollowCamConfig.camera_side` says which
  touchline the camera stands on (default `y < 0`), and pitch x is
  oriented as seen from there. Without it the right box seen from here is
  the left box seen from the far side. The upload must eventually carry it.

## Registration: what is known

- Lines only. A frame needs two painted lines in each direction. Midfield
  frames with two touchlines, the halfway line and the centre circle come
  back unregistered with that reason; the spike report counts it.
- Hypotheses are every naming of two pairs of detected lines as surface
  lines, fit in closed form (`square_to_quad`) and batched; plausibility
  checks (`_sane`) reject what no camera above the pitch could produce,
  including "the pitch must land on grass"; scoring blends paint-under-model
  and model-under-paint; the best few are refined by point-to-line ICP and
  refinement may not lower the score or leave the plausible set.
- On rendered frames, confident wrong answers cluster where the halfway
  line and a penalty-box front are 36 m apart either way; they score
  0.86–0.90 against 0.91+ for right ones, which is what the 0.8 floor is
  set against. Real footage will move that, and the histogram exists to
  set it. The next lever is the centre circle as a landmark, then
  interpolating across short gaps between registered frames.
- `FollowCamRegistrar.keep_candidates = True` keeps every scored
  hypothesis of the last frame in `last_candidates`; compare against
  truth before guessing why a frame failed.
- **Grass colour is estimated per frame** (`estimate_grass()` in
  `lines.py`), and the paint thresholds are ratios of it. A worn pitch is
  olive at hue 23; the old fixed range started at 30. The ratios are the
  ones the original absolute thresholds had on rendered grass, on
  purpose: a lower top-hat gained broadcast recall and lost a metre on
  one rendered view, and the rendered view wins. Do not retune these
  against one dataset; `spike/evals/bench.py` scores all four at once.
- **Plausibility checks are reported one by one** (`_sane_checks()`), in
  three modes (`FollowCamConfig.plausibility`, numbers in its comment).
  Any rule that consults the grass mask assumes grass is the pitch, which
  is true in a stadium and false on an open field; rules that do not
  consult it let confident wrong fits through. Nothing in between was
  found. The default is the precise mode.
- **On real footage the classical detector's limit is not any one rule.**
  Every scene brings something thin, bright and on grass that is not a
  line: boards, a road, cars, a crowd, the goal net, the Veo watermark,
  white kits. Two attempts to reject them with colour geometry were
  measured and did not hold (`spike/evals/README.md`, both rounds). Do not
  attempt a third; the way past this is a detector that labels pixels as
  a named line, which learns what a line is not.
- **SoccerNet's pretrained line network runs here** (`sn_baseline.py`) and
  already finds on the worn field what the classical code cannot. It
  needs its BatchNorm epsilon set to 1e-3 before loading; without that it
  loads without complaint and predicts background everywhere. Its
  mistakes on Veo footage are domain shift, which fine-tuning on labelled
  frames of this footage would address. That route is parked behind
  clicked keyframes plus background tracking, which cost less per field
  (NEXT.md, "The camera first"). If it comes back, it goes behind the
  same `Registrar` interface, feeding the same hypothesis search.

## Known gaps

- M1 with the classical detector fails the gate on both real Veo exports
  (1 and 0 of 60 frames) after every measured improvement. The next
  attempt is clicked keyframes with the mapping carried between them by
  tracking the fixed background, since the Veo rig itself never moves.
  It needs the real pitch dimensions of each field, which are not
  105 x 68 and have not been measured. One match's fix is not to be
  trusted until it holds on the others; the spec's third, held-out match
  does not exist yet.
- **No fixed-camera footage exists.** Ball size, blur and coverage on a
  static camera are arithmetic from the spec, not measurements. They are
  the first thing to measure, and the one risk a static camera keeps.
- **The ball is detected more often than an early measurement here
  claimed, and unevenly.** Over 240 consecutive samples per match with a
  COCO model: 43% on the worn olive field, 73% on the green one, median
  gap one to two samples (bridgeable), worst 15 (not). Measure with
  `spike/evals/ball_recall.py` over *bursts*; scattered frames cannot
  answer whether a tracker bridges the misses, and the first attempt here
  made exactly that mistake. Fine-tuning's job is the blackout tail on
  worn turf, not the average. The ball is 11 px across.
- **Pitch dimensions are assumed, not known.** The search runs with
  105 x 68 and both real pitches are smaller. The upload carries them;
  nobody has measured either field.
- **S2, S3 and the events half of S8 have met one real match. They find
  dead balls passably and cannot name a grounded one.** Detection is an
  Ultralytics checkpoint behind `Detector`; tracking is a dependency-free
  two-pass tracker; `events.py` finds dead balls and the soccer plugin
  names them. On the first full follow-cam match (8 November, 24,658
  samples) S8 0.1.0 wrote 317 events, 295 of them `free_kick`. The owner
  reviewed the first 18 minutes: 64 verdicts, 36 not an event, 25 wrong
  kind, 3 right. The 28 real ones were 9 throw-ins, 7 goal kicks, 5
  kick-offs, 4 free kicks, 2 unnamed restarts and a corner.
  - S8 0.2.0 writes 249 events on the same fragments: 239 `restart`, 9
    `throw_in`, 1 `kick_off`. Read through his verdicts it keeps 26 of
    the 28 real marks (the other two were second sightings of restarts it
    keeps) and 14 of the 36 false ones. Twelve of the 22 it drops were
    the warm-up before the whistle.
  - **No cue a `DeadBall` carries separates a throw-in waiting to be
    picked up from a free kick from a goal kick** (the account is in
    `SoccerPlugin.classify_restart`). The name is where on the pitch the
    ball lay, so it waits for the goal and the touchline: the landmark
    detector or registration. Until then a grounded dead ball is
    `restart`, with a confidence set by how long it sat (10 of 12 real at
    three seconds, 9 of 19 at one).
  - The false ones left are open play in which the ball keeps pace with
    the players, which the people estimate of the camera's motion
    cancels. Measured against the background instead (phase correlation
    of consecutive S1 frames, people blanked, half a height), 21 of his
    28 real marks stay still and 7 of the 24 false ones after the
    whistle do. Worth building, in registration; not the clean cut a
    first sample checked by eye suggested (12 of 14 against 2 of 14).
  - The detector sees a ball at rest and then loses it far more often
    than it sees it struck (33 of 317 windows saw the next sample), so
    an event's time is when the ball was last seen still, and the kick
    can be seconds later. The review clip runs on eight seconds for that.

  Scripted scenes are still what the tests pin.
  The confidences in `SoccerPlugin.classify_restart` are priors until
  `spike/evals/restarts_on_taps.py` has run on both Veo exports against
  the minutes app's taps. Only detection is a trained model (a COCO
  checkpoint nobody here trained); tracking and the restart rules are
  rules, and corrections set their thresholds rather than train them.
- **Corner against goal kick is told apart by the corner flag and the goal**,
  which the soccer plugin declares as landmark entity classes, or by the
  keeper once S4 labels her. No detector emits `goal` or `corner_flag`
  yet: goal posts and crossbars are annotated in SoccerNet calibration
  (25k frames) and can be pretrained from there; corner flags need our own
  box tags. Until a detector does, the plugin says `restart` there, not a
  guess. A landmark seen in two frames is also the exact camera motion,
  which the people estimate only approximates.
- **Possession and the taker rule are built and have not met real
  footage.** S8 0.3.0 writes `possession.json` beside the events: spells
  of the ball with one fragment (`sideline/possession.py`), a touch being
  the ball within `touch_h` of a player's feet or, failing any feet, in
  her hands; a spell carries across `possession_carry_s` of free or unseen
  ball and is not ended by a run shorter than `possession_min_s`. The
  review page draws the holder in green on every frame, beside the yellow
  taker, which is one player for the whole clip.
  - The taker of a grounded dead ball is now the last player with her feet
    at the spot, looked for up to `strike_window_s` after the ball was last
    seen there (`last_at_the_ball`), because the owner found the yellow box
    on players who touched the ball and did not take it (12 November; no
    `wrong_taker` verdict had been recorded, so there is no count). Whether
    the ball was dead is still decided by who was near while it sat.
  - All three constants are priors. The measure is an image distance: a
    low camera compresses depth, so a player a few metres nearer the camera
    than the ball can be as close in the frame as the one on it. Registration
    is what removes that. No verdict exists for a spell yet, and no team,
    so there is no possession share.
- **No open-play events yet** (passes, crosses, turnovers). A cross without
  pitch coordinates is a long aerial ball ending near the goal landmark;
  it needs the ball held through flight, which is the blackout tail, and
  the goal detector above. `sideline overlay` renders what the stages saw
  so a run can be watched.
- **The review page is for events only, and the verdicts are read back
  only by the page itself.** `web/review.html`, served by the API at `/review` with a
  same-origin content policy, writes `EventDecision` rows (right / wrong
  kind / wrong taker / not an event, each checked against a real events
  version, an event in it, the sport's event types and a track of the
  tracking version it was built on) and `ReviewDecision` rows for a
  track's name, scoped to that tracking version. A new events version has
  no rows of its own; the page reads the earlier versions' verdicts
  through it by time and shows them as carried (`sideline/verdicts.py`),
  and the rows stay with the version they were given on. Nothing else
  consumes them: the scorer still reads only the minutes app's taps and
  S6 does not exist. Scoring a run against the verdicts was done by hand
  for S8 0.2.0 and belongs in `spike/evals` beside `restarts_on_taps.py`.
  There is no login; `decided_by` is whatever the reviewer typed.
- No S4 team assignment, S6 identity, S8 stats or S9 publish; S7 review
  exists for events only. `contracts.py` already defines what they write.
  Events carry the taker as a `track_id`; the name arrives as a review
  decision (the page's "Name the taker") or from S6, and the minutes
  app's attributed taps are a free labelled set for S6.
- Where `player_stats.json` sits under the fixture in soccer-manager's
  schema is undecided; that is a schema change on the other repo and is
  high-stakes there. Do not pick a path unilaterally.
- The compose worker image is CPU-only; the CUDA/Ultralytics image is M3.
- `RQQueue`, `MinioBlobStore` and `UltralyticsDetector` are exercised only
  on the compose stack or the GPU box, not by the tests.

## Conventions

- Commit messages: short summary line, blank line, body explaining *why*,
  with the numbers. The commit body is the record; `CHANGELOG.md` stopped
  at 0.1.14 (31 October) and is not maintained. `git log` is the history.
- Comments explain reasoning, not mechanics; the invariants above set the tone.
- No AI model is called from this pipeline's runtime, and none from
  soccer-manager ever.

## Agents and skills (`.claude/`)

Routine work goes to project agents, which keeps the main context for
design. Using them is standing permission from the user.

- **git-shepherd** does every commit, pull, push, PR and branch deletion
  (`/sync`, `/ship`, `/pr`).
  - Give it the *why*. It writes the commit message to
    `.claude/skills/house-style`.
  - It merges both the branch's remote and its base.
  - It stops on a conflict or a failing test, and never force-pushes.
  - After a PR it offers the branch for deletion: only the local branch
    while the PR is open, because deleting the remote closes it.
  - Its `OFFER:` lines go to the user. Never act on one unasked.
- **test-runner** runs `pytest` in the background (`/test`) and says
  whether a failure is new or already on the base. This is how the
  "after every change" rule above is met.
- **invariant-guard** checks a diff against the invariants above
  (`/guard`). Run it before structural changes ship.
- **docs-oracle** answers questions from SPEC, NEXT, TRAINING and the
  eval notes with citations (`/spec`). It saves reading them whole for a
  question. Before structural changes, still read SPEC and NEXT as the
  top of this file says.
- **eval-runner** runs `spike/evals` and `spike/labels` measurements on
  the GPU (`/eval`) and reports them as the sections above ask: gaps,
  not rates; held-out matches named.
