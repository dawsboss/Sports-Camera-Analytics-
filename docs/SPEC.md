# Sideline: Static-Camera Sports Analytics Spec

Source: the living document at
https://claude.ai/code/artifact/a6d3c16b-ca3f-4b97-a04f-dfe52501c486 (as of 2026-09-20, rev 16).
That doc is the one that gets edited; this copy is here so the repo carries its own design.

## Purpose

**The product is a pipeline for static camera footage.** A fixed camera covering the entire playing surface is the target input, and every architectural decision in this spec assumes it. Static footage is what makes complete per-player data possible at all.

**Veo follow-cam exports are the proof-of-concept input only.** Veo will only let you download the auto-cropped follow view, never the panorama, so it is strictly worse than what the finished system runs on. It is used now for two reasons: it is footage that already exists today, and building against a hostile input forces the pipeline to be structured correctly from the start rather than accidentally depending on properties of one camera.

**The two modes differ in exactly one stage: registration.** Static solves the camera-to-surface mapping once per camera placement. Follow-cam re-estimates it on every frame. Detection, tracking, team assignment, identity resolution, review, stats and publish are all shared. That is the whole reason the PoC is not throwaway work, and it is the invariant to protect when making any design decision in this document.

Where the two modes disagree, **static wins.** If a shortcut would make follow-cam easier but constrain the static path, do not take it.

## Input contract

The tool accepts two input modes. Every artifact records which mode produced it, because the mode determines which outputs are trustworthy.

**Mode A — static camera (primary).** A fixed camera, mounted high enough to see the full surface, recording the entire match from one unchanging viewpoint. Target is 4K at 30 fps, which gives roughly 35–50 px per metre and a player height of 60–90 px across a full pitch. 1080p is the floor and gets marginal. Shutter speed matters more than resolution for ball work. One file per match, or a set of segment files stitched at ingest. This is the input the system is built for.

**Mode B — Veo follow-cam (proof of concept).** A single MP4 per match, downloaded by hand from the Veo Editor. No API, no automation. The file is the follow-cam render: a virtual camera that crops and pans across Veo's stitched panorama to keep the ball roughly centred. Typically 1080p, 25 or 30 fps, heavily compressed. It is a *moving* camera even though the physical hardware never moved, and it shows only a fraction of the surface at any instant.

**Accompanying inputs**, supplied by the user at upload time:

- Match ID linking to an existing fixture in `soccer-manager`
- Roster for both teams: name, jersey number, team
- Substitution timeline (already captured by the minutes app)
- Kickoff wall-clock offset, so video time maps to match minute
- Pitch dimensions, since youth pitches vary by age group

**Explicit non-goals for the PoC.** No live processing, no multi-camera input, no audio, no Veo Analytics ingestion, no scraping of Veo's player.

## Output contract

The tool emits three artifacts. Everything downstream, including `soccer-manager`, reads only these.

**1. `tracks.parquet`** — the raw spatial record, one row per player per sampled frame:

| field | type | notes |
| --- | --- | --- |
| `t_ms` | int64 | video time |
| `match_minute` | float | derived from kickoff offset |
| `track_id` | int | pipeline-local, survives only within a segment |
| `player_id` | string? | null until identity resolution succeeds |
| `team` | enum | home / away / gk_home / gk_away / official |
| `x_pitch`, `y_pitch` | float | metres, origin at centre spot |
| `conf_det` | float | detection confidence |
| `conf_reg` | float | registration confidence for that frame |
| `visible` | bool | in frame at this timestamp |

**2. `events.parquet`** — ball-anchored occurrences, one row each: `t_ms`, `type`, `x_pitch`, `y_pitch`, `player_id`, `team`, `confidence`. PoC event types are deliberately few: `touch`, `duel`, `shot`, `third_entry`, `out_of_play`.

**3. `player_stats.json`** — the aggregate written to Firebase, per player:

- `visible_seconds` and `visible_pct` (honest denominator for everything else)
- `touches`, `duels_contested`
- `position_samples` count, plus `avg_x`, `avg_y` and a `zone_histogram` over an 18-cell grid
- `distance_covered_visible` in metres, flagged `partial: true`
- `confidence_grade` of A/B/C, driven by how much review the track needed

Every aggregate carries its sample count, its capture mode and a partial flag. A coach must never see a distance number without knowing it covers 40% of the match. In Mode A the partial flag goes false and the stats listed as impossible below become available through this same schema — the contract does not change between modes, only how much of it is populated.

## Hard limits of Mode B

Everything in this section applies to the follow-cam PoC input only. These are properties of that footage, not bugs to be fixed later, and collectively they are the reason static is the primary mode rather than a nice-to-have upgrade.

**Only a fraction of the pitch is visible at any moment.** The crop follows the ball, so a centre-back during a sustained attack is off-screen for minutes at a time. Expect any given outfield player to be in frame perhaps 30–60% of the match.

**Therefore these stats are impossible here, and must not be shipped:**

- Total distance covered, sprint counts, top speed — all require continuous observation
- Full-pitch heat maps for off-ball movement
- Defensive line height and team shape, which need all eleven simultaneously
- Pass networks, since the receiver is often out of frame at the moment of the pass

**Tracks fragment constantly.** Every time a player leaves and re-enters frame, tracking produces a new `track_id`. A single player might generate 40 fragments across a half. Re-linking them is the identity stage's main job, and it is why the review UI is not optional.

**Registration is per-frame.** The virtual camera pans and zooms continuously, so homography must be re-estimated from pitch lines on every sampled frame. Frames showing only grass with no lines are unregisterable and get dropped.

**What survives in Mode B.** Ball-anchored analysis is genuinely fine, because the crop is centred on the ball by construction. Touches, duels, shot locations, attacking-third entries, and where play spent its time are all real outputs from this footage.

**All of the above is lifted in Mode A.** A static camera sees every player for the full match, so tracks are continuous, fragments are rare, registration is solved once, and the impossible list becomes the headline feature set: distance covered, sprint counts, top speed, full-pitch heat maps, defensive line height and team shape. Nothing in the pipeline changes to get there except the registration implementation.

## Pipeline stages

Each stage is a separately queued job. It reads named artifacts, writes named artifacts, and records the model or config version it ran with. Re-running S8 must never require re-running S2.

**S0 — Ingest.** Transcode to a known codec, extract keyframes, detect the half-time break, write `match.json` with duration, fps and resolution.

**S1 — Sampling.** Decode at 5 fps rather than 25. Tracking interpolates between samples. This cuts compute by 5x and costs almost nothing in accuracy at youth-match speeds.

**S2 — Detection.** YOLO fine-tuned on SoccerNet plus a few hundred hand-labelled frames from actual matches. Classes: player, goalkeeper, official, ball. Output `detections.parquet`.

**S3 — Tracking.** ByteTrack over the detections. Produces short-lived `track_id` fragments. No attempt at long-range re-identification here.

**S4 — Team assignment.** Crop each detection's torso, cluster in colour space into two kits plus keepers plus officials. Kit colours are known per fixture, so this is constrained classification rather than blind clustering.

**S5 — Registration.** The one stage with two implementations behind a single interface. Both emit the same artifact: a per-frame homography plus a confidence score.

- *Static (primary).* Solve once per camera placement. The operator clicks known surface landmarks on a single frame; the resulting mapping is reused for every frame of the match. Robust, cheap, and accurate. With a panoramic or wide lens, fit a thin-plate spline over 15–25 correspondence points instead of a planar homography, which sidesteps modelling lens distortion entirely.
- *Follow-cam (PoC).* Detect surface lines and landmarks on every sampled frame and fit a homography per frame. Reject frames below a confidence floor. Frames showing only grass with no visible lines are unregisterable and get dropped.

Keeping both behind one interface is what makes the mode swap a configuration change rather than a rewrite.

**S6 — Identity resolution.** Link fragments to roster entries using jersey colour, substitution windows from the minutes app, spatial continuity across gaps, and number OCR where legible. Substitution data is the strongest constraint: it reduces candidates to eleven per team at any instant.

**S7 — Review.** Human pass in the UI. Scrub the timeline, see unassigned fragments ranked by duration, click to assign. Target under 15 minutes of human time per match.

**S8 — Stats.** Sport plugin computes the aggregates from tracks plus events.

**S9 — Publish.** Write `player_stats.json` to Firebase under the fixture. `soccer-manager` picks it up with no code change.

## Storage and data model

Three stores, each holding what it is good at.

**MinIO** holds blobs: source video, transcoded video, sampled frames if cached, and every stage artifact. Layout is `matches/{match_id}/{stage}/{version}/`. Artifacts are immutable; a re-run writes a new version directory rather than overwriting.

**Postgres** holds metadata and orchestration: matches, fixtures, rosters, job records, stage versions, artifact pointers, review decisions. Small, relational, queryable. Never holds trajectory rows.

**Parquet on MinIO, queried with DuckDB**, holds trajectories and events. A 90-minute match at 5 fps with 22 players is roughly 600,000 rows. That is nothing for DuckDB and miserable for Postgres.

**Review decisions are stored, never baked in.** When a human assigns fragment 847 to a player, that is a row in Postgres, not an edit to the Parquet file. Re-running S6 with a better model then replays human corrections on top. This is what makes the review time cumulative rather than wasted.

**Firebase** holds only the final published aggregates, so parent share links and the coach UI never touch the homelab.

## Stack and deployment

**Compute** runs on the Proxmox homelab. One VM with GPU passthrough runs the worker. If the Slurm environment is already up, S2 and S5 are the batch jobs worth pushing there, since they are the only stages that saturate a GPU and they parallelise cleanly across match segments.

**Services**, all containerised:

- FastAPI for the API surface
- Redis with RQ for the job queue — simpler than Celery and sufficient at this scale
- Postgres, MinIO
- Worker image with CUDA, PyTorch, Ultralytics, ByteTrack, OpenCV

**Web UI** is a separate SPA from `soccer-manager`, since its needs are completely different: video scrubbing, canvas overlays, bulk assignment. Deploy it as a static build, reach the homelab API through a Cloudflare Tunnel or Tailscale.

**The boundary that matters.** Raw match video never leaves the homelab. Only computed aggregates go to Firebase. That keeps the existing rule that `soccer-manager` stays a static site with no AI calls — it consumes JSON and knows nothing about how it was produced.

**Expected runtime.** A 90-minute match at 5 fps is ~27,000 frames. On a single consumer GPU, detection is the bottleneck at roughly 20–40 minutes. Full pipeline probably lands near real time for the first version. Overnight batch is entirely acceptable and should be the assumption.

## Success criteria

These are the Mode B gates — the bar the PoC must clear to prove the machinery works. Mode A targets are strictly higher across the board and get set once real static footage exists. Pick three matches as a fixed evaluation set and never tune against anything else.

| Measure | Threshold |
| --- | --- |
| Frames successfully registered | ≥ 70% of sampled frames |
| Reprojection error on registered frames | < 2 m at pitch centre |
| Player detection recall, in-frame | ≥ 90% |
| Team assignment accuracy | ≥ 95% |
| Identity accuracy after review | ≥ 95% of visible player-seconds |
| Human review time | ≤ 15 min per match |
| Wall-clock processing | ≤ 2x match duration |

The one that decides whether this is worth continuing is review time. If a match takes an hour of clicking, no coach will ever use it, and the honest response is to stop and build the camera first rather than optimise the wrong pipeline.

## Milestones

Ordered so the riskiest unknown is answered early and cheaply.

M1 through M8 run on Mode B footage and build the machinery. M9 onward is the static camera and the real product.

**M1 — Registration spike.** Before building any infrastructure, take 200 frames from a real Veo export and try to fit homographies. If per-frame registration on this footage does not work, nothing downstream matters. Two days, throwaway notebook, no services.

**M2 — Skeleton.** FastAPI, Postgres, MinIO, RQ, a worker that accepts an upload and runs S0 and S1. Nothing clever, just the artifact contract working.

**M3 — Detect and track.** S2 and S3 with an off-the-shelf model. Render an overlay video for eyeballing. Accept poor accuracy.

**M4 — Pitch coordinates.** Wire in S5 properly, produce a birds-eye overlay from a real match. This is the first moment the project looks impressive and is worth showing someone.

**M5 — Identity.** S4 and S6, pulling rosters and substitution windows from Firebase.

**M6 — Review UI.** The stage that determines viability. Build it before the stats.

**M7 — Stats and publish.** S8 and S9, then one full match end to end against the success criteria.

**M8 — Fine-tune.** Only now, with a labelling loop fed by review corrections, train a match-specific detector.

M1 is genuinely a gate. It is worth doing this week and it either kills or validates the whole approach.

### Mode A phase

**M9 — Capture rig.** Build and mount the static camera. Validate full-surface coverage, player pixel height and shutter speed against a real pitch before trusting a single match to it.

**M10 — Static registration.** Calibration UI: click surface landmarks once per camera placement, fit and store the mapping. Swap S5's implementation behind the existing interface.

**M11 — Tier-2 stats.** Distance covered, sprint counts, top speed, full-pitch heat maps, team shape. These are unblocked purely by the input change and should require no new CV work. If they do, the S5 interface was drawn in the wrong place.

M9 onward is the actual product. M1 through M8 exist to make sure that when the camera arrives, the only thing left to build is the camera.

## Multi-sport extension points

S0 through S7 are sport-agnostic and should contain no soccer-specific logic. Everything a new sport needs lives in a plugin supplying four things.

**Surface model.** Canonical dimensions plus the landmark set used for registration: lines, arcs, circles, corners, and which of them are reliable enough to fit against.

**Entity classes.** What is detected and how many of each are expected on the surface. Soccer expects 22 plus officials plus one ball; hockey expects 12 plus a puck; basketball expects 10 in a far smaller space.

**Event grammar.** The rules mapping trajectories to events: what counts as possession, what ends it, what a shot is, where the scoring zones are.

**Stat definitions.** The aggregate functions over tracks and events, plus how each is displayed and what its partial-data caveats are.

The test of whether this abstraction is real: adding basketball should mean writing a court model and a rules file, touching zero lines of detection, tracking or review code. If it does not, the boundary is in the wrong place and should be fixed before the second sport ships, not after.

One genuine caveat. Indoor court sports will probably need their own registration approach, since a court fills the frame differently than a pitch and reflections behave badly. Expect S5 to become pluggable too, with static-camera, follow-cam and court variants.
