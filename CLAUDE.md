# CLAUDE.md

Static-camera sports analytics pipeline; Veo follow-cam exports are the
proof-of-concept input. Python package `sideline/`, FastAPI + Postgres +
MinIO + Redis/RQ in `docker-compose.yml`, everything testable on a CPU with
no services. Publishes aggregates to Firebase under a `soccer-manager`
fixture; that app stays a static site that consumes JSON.

Read `docs/SPEC.md` before changing anything structural. It is a copy of
the living design doc, and its milestone order is the work order: M1 (does
per-frame registration work on real Veo footage?) is a gate that has not
been run yet, M2 (the skeleton) is built, M3 onward is not started.

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

## Known gaps

- M1 has not been run on a real Veo export. Everything about registration
  quality is from rendered frames.
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
