# CLAUDE.md

Static-camera sports analytics pipeline; Veo follow-cam exports are the
proof-of-concept input. Python package `sideline/`, FastAPI + Postgres +
MinIO + Redis/RQ in `docker-compose.yml`, everything testable on a CPU with
no services. Publishes aggregates to Firebase under a `soccer-manager`
fixture; that app stays a static site that consumes JSON.

Read `docs/SPEC.md` before changing anything structural. It is a copy of
the living design doc, and its milestone order is the work order. Then
read `docs/NEXT.md`: it says what two real Veo exports, a labelled
broadcast set and the synthetic views measured, what each requested
output needs, and the order of the next work. In short: M1 (classical
registration) fails the gate on real footage and the measurements say
why; a pretrained line network already does better and is the next
registrar after fine-tuning; players are detected off the shelf; the
ball is detected 43% of samples on worn turf and 73% on green, with a
bridgeable median gap and an unbridgeable tail, so a fine-tuned ball
detector is the gate for everything possession-shaped. M2 (the skeleton)
is built. M3 onward is not started.

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
- **Stints are the truth for who was on the pitch**, read from
  soccer-manager and bridged through `timeline.py` (`match_seconds`,
  `on_pitch`). Roster, stints and periods are copied at upload, never
  linked, so a roster edit a month later does not change an analysis.
- **Raw video never leaves the homelab.** The API serves JSON artifacts
  only; frames, video and Parquet are read by stages and the review UI
  through the blob store. Only `player_stats.json` goes to Firebase.
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
  mistakes on Veo footage are domain shift; fine-tuning on a few hundred
  labelled frames of this footage is the next registrar, behind the same
  `Registrar` interface, feeding the same hypothesis search.

## Known gaps

- M1 with the classical detector fails the gate on both real Veo exports
  (1 and 0 of 60 frames) after every measured improvement. The path past
  it is the learned detector above, fine-tuned; that needs labelled
  frames from this footage, which do not exist yet, and the real pitch
  dimensions of each field, which are not 105 x 68 and have not been
  measured. One match's fix is not to be trusted until it holds on the
  others; the spec's third, held-out match does not exist yet.
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
- No S2 onward: no detection, tracking, team assignment, identity, review,
  stats or publish. `contracts.py` already defines what they write.
- Where `player_stats.json` sits under the fixture in soccer-manager's
  schema is undecided; that is a schema change on the other repo and is
  high-stakes there. Do not pick a path unilaterally.
- The compose worker image is CPU-only; the CUDA/Ultralytics image is M3.
- `RQQueue` and `MinioBlobStore` are exercised only by the compose stack,
  not by the tests.

## Conventions

- Commit messages: short summary line, blank line, body explaining *why*.
  Every shipped change gets a `CHANGELOG.md` entry.
- Comments explain reasoning, not mechanics; the invariants above set the tone.
- No AI model is called from this pipeline's runtime, and none from
  soccer-manager ever.

## Agents and skills (`.claude/`)

Routine work goes to project agents, which keeps the main context for
design. Using them is standing permission from the user.

- **git-shepherd** does every commit, pull, push, PR and branch deletion
  (`/sync`, `/ship`, `/pr`).
  - Give it the *why*. It writes the commit message and CHANGELOG entry
    to `.claude/skills/house-style`.
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
