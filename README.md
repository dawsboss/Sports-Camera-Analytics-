# Sideline

A pipeline that turns match footage into per-player data a coach can use.
It is built for a fixed camera that sees the whole pitch; Veo follow-cam
exports are the proof-of-concept input, used because the footage already
exists and because a hostile input keeps the pipeline honest. The two
differ in exactly one stage, registration, and everything else is shared.
The design is in [`docs/SPEC.md`](docs/SPEC.md); read it first.

Published aggregates land in Firebase next to the fixture that
[`soccer-manager`](https://github.com/dawsboss/soccer-manager-) already
tracks. Raw video never leaves the homelab.

## Where this is

| Milestone | State |
| --- | --- |
| M1 registration spike | **Run on two real Veo exports. The classical detector fails the gate on both** after every improvement that could be measured (per-frame grass colour, line thresholds that follow it, plausibility checks compared three ways on four datasets). A pretrained line network, unmodified, already finds on the worn field what the classical code cannot; fine-tuning it on this footage is the next registrar. The measurements are in [`spike/evals/`](spike/evals/) and the plan in [`docs/NEXT.md`](docs/NEXT.md). |
| M2 skeleton | API, Postgres, MinIO, RQ worker, S0 ingest and S1 sampling. Runs with no services on a laptop, or under `docker compose`. |
| M3 detection | Feasibility measured: players are found off the shelf, the ball is not, by any model tried. A ball detector trained on this footage is the gate for possession, passes and restarts. Not built. |
| M4 onward | Not started. |

## Run the M1 spike

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
sideline spike path/to/veo-export.mp4 --frames 200 --out spike/out
```

Then open `spike/out/overlays/` and read the summary. [`spike/README.md`](spike/README.md)
says what the numbers mean, what the overlays show, and what the machinery
cannot do yet. `sideline spike --selftest` runs the same thing on rendered
frames with known truth, which checks the code rather than the footage.

## Run the skeleton

With no services at all, against a directory and SQLite:

```
sideline ingest path/to/match.mp4 --pitch-length 100 --pitch-width 64
ls data/blobs/matches/<id>/
```

With the real stack:

```
docker compose up --build
curl -F file=@match.mp4 -F capture_mode=followcam -F pitch_length=100 -F pitch_width=64 \
     -F kickoff_offset_ms=95000 localhost:8000/matches
```

The API docs are at `localhost:8000/docs`, MinIO's console at `:9001`. The
upload takes the accompanying inputs the spec lists (`sm_match_id`, roster,
stints and periods as JSON, kickoff offset, pitch dimensions), stores the
file under `matches/{id}/source/`, and queues S0 then S1 unless `stages`
names more. Every stage writes `matches/{id}/{stage}/v{n}/` with a
`manifest.json` recording its config and inputs; a re-run is a new version.

Players, the ball and restarts, on a machine with a GPU and the `eval`
extra installed, with no services:

```
sideline ingest match.mp4 --kickoff-offset-ms 83000 \
    --stages s0_ingest,s1_sampling,s2_detection,s3_tracking,s8_events
python spike/evals/restarts_on_taps.py --match-dir data/blobs/matches/<id> --sm matches-node.json
```

The first writes `detections.parquet`, `fragments.parquet` and
`events.parquet` (throw-ins, corners, goal kicks, free kicks, kick-offs,
keeper claims, each with the taker's track), plus `events.csv`, the same
rows as a coach would write them down: match clock, event, who, how sure.
`sideline log <id>` prints that list and `sideline overlay <id>` renders
it onto the frames. The eval script scores the events against the
corners, throw-ins and goal kicks the coach tapped in the minutes app.
`docs/NEXT.md`, "Restarts without registration", says why this needs no
pitch coordinates and what it still needs.

## Review, on your own network

```
sideline serve --data ./data          # the same directory `sideline ingest` wrote
```

Then open `http://<that machine>:8000/review?match=<id>` from any browser
on the same network, phone included. The page plays the match at each
event the pipeline wrote, draws the taker, the ball and every tracked
box, and takes one of four taps: right; wrong kind; wrong taker, by
tapping the right player; not an event. With a roster uploaded it also
names the taker. Each tap is a row in the database (`event_decisions`,
`review_decisions`), never an edit to an artifact, and
`GET /matches/<id>/review/decisions` exports them. The frames the page
shows are served by this API to browsers on this network and nowhere
else: keep the API on the LAN or a Tailscale network, never on the open
internet. The compose stack serves the same page at `:8000/review`.

## Layout

```
sideline/
  contracts.py        tracks/events/registration Parquet schemas, match.json, player_stats.json
  timeline.py         video time <-> match seconds, and the minutes app's taps, via soccer-manager's nodes
  sports/             the plugin boundary; soccer.py is the pitch model, entities, stat definitions, restart rules
  detection.py        S2's detector behind a protocol: an Ultralytics checkpoint, or a script for tests
  tracking.py         S3's tracker: people by overlap or centre, the ball by where it went, no dependencies
  events.py           dead balls from fragments: still, then struck or thrown, and who took it, in player heights
  registration/       S5: one interface, two implementations
    followcam.py        per-frame fit from painted lines (the PoC)
    static.py           clicked landmarks once per placement, homography or spline (the product)
    lines.py            grass, paint, line segments
    synthetic.py        frames rendered through a known camera, for tests and the selftest
  storage/            blob store (local or MinIO) and the versioned artifact layout
  db/                 matches, jobs, stage runs, review decisions
  stages/             s0_ingest, s1_sampling, s2_detection, s3_tracking, s8_events
  overlay.py          the sampled frames with tracks, the ball's path and each event's call, as an MP4
  api/                FastAPI: uploads, artifacts, and the review page's routes
  worker/             RQ worker
  spike.py, cli.py    the M1 spike and the `sideline` command
docs/SPEC.md          the design, copied from the living doc
web/                  the tagger (GitHub Pages) and review.html (served by the API on your network)
hardware/             the static rig (M9): what to buy, the printed four-camera head or hidden pod, aim cards
tests/                pytest; everything runs on a CPU with no services
```

## Tests

```
pytest
```

Registration is checked against frames rendered through a known camera:
box views must land within a metre of the truth, a midfield view with one
crossing line must be refused with a reason, and a sweep of views must
register at least 70% with few confident wrong answers. The stages, the
API and the local pipeline run on a three-second synthetic clip. None of
that is the M1 gate; the gate is real footage.
