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
